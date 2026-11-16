import base64
import io

import pytest
from PIL import Image, ImageChops

from image_gateway import images
from image_gateway.config import Settings

from fakes import GREY, MARK, png_bytes


@pytest.fixture
def settings(request):
    return Settings(upstream_url="http://vllm:8000", ai_label=getattr(request, "param", "off"))


async def generate(client, **extra):
    return await client.post("/v1/images/generations", json={"prompt": "a fox", **extra})


async def edit(client, **extra):
    fields = {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in extra.items()
    }
    files = [("image[]", ("in.png", png_bytes(), "image/png"))]
    return await client.post(
        "/v1/images/edits", data={"prompt": "warmer colours", **fields}, files=files
    )


def labelled(response_json, index=0) -> bool:
    raw = base64.b64decode(response_json["data"][index]["b64_json"])
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    unlabelled = Image.new("RGB", image.size, GREY)  # what the fakes return without a label
    unlabelled.putpixel((1, 1), MARK)
    return ImageChops.difference(image, unlabelled).getbbox() is not None


TABLE = [
    ("off", {}, False),
    ("off", {"ai_label": True}, False),
    ("off", {"ai_label": False}, False),
    ("optional", {}, False),
    ("optional", {"ai_label": True}, True),
    ("optional", {"ai_label": False}, False),
    ("mandatory", {}, True),
    ("mandatory", {"ai_label": True}, True),
    ("mandatory", {"ai_label": False}, True),
]


@pytest.mark.parametrize("send", [generate, edit])
@pytest.mark.parametrize(("settings", "extra", "expected"), TABLE, indirect=["settings"])
async def test_the_label_follows_the_mode_and_the_request(client, upstream, send, extra, expected):
    r = await send(client, **extra)
    assert r.status_code == 200, r.text
    assert labelled(r.json()) is expected
    assert b"ai_label" not in upstream.requests[-1].content  # removed in every mode


@pytest.mark.parametrize("send", [generate, edit])
@pytest.mark.parametrize("settings", ["off", "optional", "mandatory"], indirect=True)
async def test_an_unreadable_ai_label_is_refused_before_any_work(
    client, upstream, prompt_guard, send
):
    r = await send(client, ai_label="yes")
    assert r.status_code == 400
    error = r.json()["error"]
    assert (error["code"], error["param"]) == ("invalid_request", "ai_label")
    assert prompt_guard.calls == [] and upstream.requests == []


@pytest.mark.parametrize("settings", ["mandatory"], indirect=True)
async def test_every_image_of_a_request_is_labelled(client, watermarker):
    r = await generate(client, n=3)
    assert [labelled(r.json(), i) for i in range(3)] == [True, True, True]
    assert watermarker.calls == 3


@pytest.mark.parametrize("settings", ["mandatory"], indirect=True)
async def test_check_then_label_then_watermark(client, monkeypatch, image_guard, watermarker):
    order = []

    def recorded(name, fn):
        def wrapper(*args):
            order.append(name)
            return fn(*args)

        return wrapper

    monkeypatch.setattr(image_guard, "scores", recorded("check", image_guard.scores))
    monkeypatch.setattr(images, "add_label", recorded("label", images.add_label))
    monkeypatch.setattr(watermarker, "apply", recorded("watermark", watermarker.apply))
    r = await generate(client)
    assert r.status_code == 200, r.text
    assert order == ["check", "label", "watermark"]


@pytest.mark.parametrize("settings", ["mandatory"], indirect=True)
async def test_a_failing_label_fails_closed(client, monkeypatch, watermarker):
    def broken(*_args):
        raise RuntimeError("no font")

    monkeypatch.setattr(images, "add_label", broken)
    r = await generate(client)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "safety_check_unavailable"
    assert "b64_json" not in r.text and watermarker.calls == 0
