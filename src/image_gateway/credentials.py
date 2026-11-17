"""Content Credentials (C2PA): a signed manifest in the image file that says AI made it.

The EU Code of Practice on marking AI-generated content asks for two layers on images:
digitally signed metadata and an invisible watermark. This module is the first layer; TrustMark
(models.py) is the second. The metadata is easy to read but disappears when a client re-encodes
the image. The watermark survives that.

The manifest records only what made the image: this gateway, the image model if the operator
names it, how the image came about (generated, or an edit of an uploaded image) and the
watermark. It never carries prompt text, user data or the uploaded images.
"""

import io
from pathlib import Path

import c2pa
from PIL import Image

from image_gateway import __version__

IPTC = "http://cv.iptc.org/newscodes/digitalsourcetype/"
GENERATED = IPTC + "trainedAlgorithmicMedia"
EDITED = IPTC + "compositeWithTrainedAlgorithmicMedia"
MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


class CredentialsError(RuntimeError):
    """The certificate or key can't sign. Raised at startup, so the gateway does not start."""


def manifest(edit: bool, software_agent: str | None, watermark: str | None) -> dict:
    """The manifest definition. Nothing in it comes from the request."""
    created: dict = {"action": "c2pa.created", "digitalSourceType": EDITED if edit else GENERATED}
    if software_agent:
        created["softwareAgent"] = {"name": software_agent}
    actions = [created]
    if watermark:
        # "unbound": the watermark carries the gateway's payload, not a pointer to this manifest.
        # The plain "c2pa.watermarked" action would require a soft-binding assertion.
        actions.append({"action": "c2pa.watermarked.unbound", "softwareAgent": {"name": watermark}})
    return {
        "claim_generator_info": [{"name": "image-gateway", "version": __version__}],
        "assertions": [{"label": "c2pa.actions.v2", "data": {"actions": actions}}],
    }


class ContentCredentials:
    def __init__(
        self,
        cert_pem: bytes,
        key_pem: bytes,
        alg: str = "es256",
        tsa_url: str | None = None,
        software_agent: str | None = None,
        watermark: str | None = None,
    ):
        self.software_agent = software_agent
        self.watermark = watermark
        # No thumbnail in the manifest: it would only repeat the image and make it larger.
        self.context = c2pa.Context(
            c2pa.Settings.from_dict({"builder": {"thumbnail": {"enabled": False}}})
        )
        try:
            # Sign once now, without the timestamp server, so a certificate the C2PA rules
            # reject or a key that doesn't belong to it stops the gateway at startup.
            probe = c2pa.Signer.from_info(c2pa.C2paSignerInfo(alg, cert_pem, key_pem, None))
            self._sign(probe, _tiny_png(), "PNG", edit=False)
            self.signer = (
                c2pa.Signer.from_info(c2pa.C2paSignerInfo(alg, cert_pem, key_pem, tsa_url))
                if tsa_url
                else probe
            )
        except (c2pa.C2paError, ValueError, TypeError) as exc:
            raise CredentialsError(
                f"C2PA signing does not work with this certificate: {exc}"
            ) from exc

    @classmethod
    def from_files(cls, cert_file: Path, key_file: Path, **kwargs) -> "ContentCredentials":
        try:
            cert_pem, key_pem = cert_file.read_bytes(), key_file.read_bytes()
        except OSError as exc:
            raise CredentialsError(f"C2PA certificate or key can't be read: {exc}") from exc
        return cls(cert_pem, key_pem, **kwargs)

    def sign(self, data: bytes, image_format: str, edit: bool) -> bytes:
        """`data` with a signed manifest embedded. `image_format` is PNG, JPEG or WEBP."""
        return self._sign(self.signer, data, image_format, edit)

    def _sign(self, signer, data: bytes, image_format: str, edit: bool) -> bytes:
        definition = manifest(edit, self.software_agent, self.watermark)
        out = io.BytesIO()
        # The builder holds native memory: close it right away, not whenever GC gets to it.
        with c2pa.Builder(definition, self.context) as builder:
            builder.sign(signer, MIME_TYPES[image_format], io.BytesIO(data), out)
        return out.getvalue()


def _tiny_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (128, 128, 128)).save(buffer, format="PNG")
    return buffer.getvalue()
