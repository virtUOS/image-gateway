"""Content Credentials (C2PA): real signing with a throwaway CA, read back with the C2PA SDK."""

import base64
import io
import json
from contextlib import asynccontextmanager

import c2pa
import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from image_gateway.app import create_app
from image_gateway.config import Settings
from image_gateway.credentials import (
    EDITED,
    GENERATED,
    ContentCredentials,
    CredentialsError,
    manifest,
)

from certs import make_certs
from fakes import MARK, FakeImageGuard, FakePromptGuard, FakeUpstream, FakeWatermarker, png_bytes

GENERATIONS, EDITS = "/v1/images/generations", "/v1/images/edits"
MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
AGENT = "FLUX.2 [klein] 4B"


@pytest.fixture(scope="module")
def certs():
    return make_certs()


@pytest.fixture
def cert_files(tmp_path, certs):
    cert, key = tmp_path / "chain.pem", tmp_path / "key.pem"
    cert.write_bytes(certs.chain_pem)
    key.write_bytes(certs.key_pem)
    return cert, key


@pytest.fixture
def settings(cert_files):
    cert, key = cert_files
    return Settings(
        upstream_url="http://vllm:8000",
        c2pa=True,
        c2pa_cert_file=cert,
        c2pa_key_file=key,
        c2pa_software_agent=AGENT,
    )


@asynccontextmanager
async def gateway(settings, **parts):
    app = create_app(
        settings,
        prompt_guard=FakePromptGuard(),
        image_guard=FakeImageGuard(),
        watermarker=FakeWatermarker(),
        upstream_transport=FakeUpstream().transport,
        **parts,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        yield client


def read(response_json: dict, ca_pem: bytes | None, index: int = 0):
    """The returned bytes, their image format and a C2PA reader (None: no manifest).

    The reader checks trust like a public verifier. `ca_pem` puts the test CA on its list.
    """
    raw = base64.b64decode(response_json["data"][index]["b64_json"])
    image_format = Image.open(io.BytesIO(raw)).format
    config: dict = {"verify": {"verify_trust": True}}
    if ca_pem:
        config["trust"] = {"user_anchors": ca_pem.decode()}
    context = c2pa.Context(c2pa.Settings.from_dict(config))
    reader = c2pa.Reader.try_create(MIME[image_format], io.BytesIO(raw), context=context)
    return raw, image_format, reader


def actions(reader: c2pa.Reader) -> list[dict]:
    manifest = reader.get_active_manifest()
    return next(
        a["data"]["actions"] for a in manifest["assertions"] if a["label"] == "c2pa.actions.v2"
    )


@pytest.mark.parametrize(
    ("output_format", "image_format"), [("png", "PNG"), ("jpeg", "JPEG"), ("webp", "WEBP")]
)
async def test_generated_images_carry_signed_credentials(
    client, certs, output_format, image_format
):
    r = await client.post(GENERATIONS, json={"prompt": "a fox", "output_format": output_format})
    assert r.status_code == 200, r.text
    raw, fmt, reader = read(r.json(), certs.ca_pem)
    assert fmt == image_format and reader is not None
    assert reader.get_validation_state() == "Trusted"
    manifest = reader.get_active_manifest()
    assert manifest["claim_generator_info"][0]["name"] == "image-gateway"
    assert actions(reader) == [
        {
            "action": "c2pa.created",
            "digitalSourceType": GENERATED,
            "softwareAgent": {"name": AGENT},
        },
        {"action": "c2pa.watermarked.unbound", "softwareAgent": {"name": "TrustMark"}},
    ]
    if fmt == "PNG":  # lossless: the watermarked pixels are untouched by signing
        assert Image.open(io.BytesIO(raw)).convert("RGB").getpixel((1, 1)) == MARK


async def test_edits_are_recorded_as_composites_without_the_upload(client, certs):
    files = [("image[]", ("in.png", png_bytes(), "image/png"))]
    r = await client.post(EDITS, data={"prompt": "Mach den Himmel blau"}, files=files)
    assert r.status_code == 200, r.text
    _raw, _fmt, reader = read(r.json(), certs.ca_pem)
    assert reader.get_validation_state() == "Trusted"
    assert actions(reader)[0]["digitalSourceType"] == EDITED
    assert not reader.get_active_manifest().get("ingredients")


async def test_the_manifest_carries_no_prompt_text(client, certs):
    r = await client.post(GENERATIONS, json={"prompt": "Ein Igel namens Ferdinand", "n": 2})
    for index in (0, 1):
        raw, _fmt, reader = read(r.json(), certs.ca_pem, index)
        assert reader.get_validation_state() == "Trusted"
        assert b"Ferdinand" not in raw and "Ferdinand" not in reader.json()


async def test_without_a_ca_on_the_trust_list_the_signature_is_still_valid(client):
    r = await client.post(GENERATIONS, json={"prompt": "a fox"})
    _raw, _fmt, reader = read(r.json(), ca_pem=None)
    assert reader.get_validation_state() == "Valid"


async def test_without_the_watermark_the_manifest_does_not_claim_one(settings, certs):
    settings.watermark = False
    async with gateway(settings) as client:
        r = await client.post(GENERATIONS, json={"prompt": "a fox"})
    _raw, _fmt, reader = read(r.json(), certs.ca_pem)
    assert [a["action"] for a in actions(reader)] == ["c2pa.created"]


async def test_credentials_are_off_by_default(certs):
    async with gateway(Settings(upstream_url="http://vllm:8000")) as client:
        r = await client.post(GENERATIONS, json={"prompt": "a fox"})
    assert r.status_code == 200
    assert read(r.json(), certs.ca_pem)[2] is None


async def test_a_failing_signer_fails_closed(settings):
    class BrokenSigner:
        def sign(self, data, image_format, edit):
            raise RuntimeError("timestamp server unreachable")

    async with gateway(settings, signer=BrokenSigner()) as client:
        r = await client.post(GENERATIONS, json={"prompt": "a fox"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "safety_check_unavailable"
    assert "b64_json" not in r.text


def test_c2pa_needs_both_files(cert_files):
    cert, _key = cert_files
    with pytest.raises(ValidationError, match="GATEWAY_C2PA_KEY_FILE must be set"):
        Settings(c2pa=True, c2pa_cert_file=cert)
    with pytest.raises(ValidationError, match="not a readable file"):
        Settings(c2pa=True, c2pa_cert_file=cert, c2pa_key_file=cert.parent / "missing.pem")


def test_c2pa_settings_come_from_the_environment(monkeypatch, cert_files):
    cert, key = cert_files
    monkeypatch.setenv("GATEWAY_C2PA", "true")
    monkeypatch.setenv("GATEWAY_C2PA_CERT_FILE", str(cert))
    monkeypatch.setenv("GATEWAY_C2PA_KEY_FILE", str(key))
    monkeypatch.setenv("GATEWAY_C2PA_TSA_URL", "http://timestamp.example")
    settings = Settings()
    assert (settings.c2pa, settings.c2pa_cert_file, settings.c2pa_alg) == (True, cert, "es256")
    assert settings.c2pa_tsa_url == "http://timestamp.example"


def test_a_key_that_does_not_match_the_certificate_stops_startup(settings, certs):
    settings.c2pa_key_file.write_bytes(make_certs().key_pem)
    with pytest.raises(CredentialsError):
        create_app(
            settings,
            prompt_guard=FakePromptGuard(),
            image_guard=FakeImageGuard(),
            watermarker=FakeWatermarker(),
        )


def test_a_broken_certificate_stops_startup(settings):
    settings.c2pa_cert_file.write_text("not a certificate")
    with pytest.raises(CredentialsError):
        create_app(
            settings,
            prompt_guard=FakePromptGuard(),
            image_guard=FakeImageGuard(),
            watermarker=FakeWatermarker(),
        )


def test_a_certificate_the_c2pa_rules_refuse_stops_startup():
    refused = make_certs(eku=False)
    with pytest.raises(CredentialsError):
        ContentCredentials(refused.chain_pem, refused.key_pem)


def test_the_manifest_names_only_what_the_operator_configured():
    assert manifest(edit=False, software_agent=None, watermark=None)["assertions"] == [
        {
            "label": "c2pa.actions.v2",
            "data": {"actions": [{"action": "c2pa.created", "digitalSourceType": GENERATED}]},
        }
    ]
    edited = manifest(edit=True, software_agent=AGENT, watermark="TrustMark")
    assert json.dumps(edited).count(AGENT) == 1
    assert edited["assertions"][0]["data"]["actions"][0]["digitalSourceType"] == EDITED
