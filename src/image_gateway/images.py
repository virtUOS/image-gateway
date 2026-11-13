"""Image decoding/encoding and request-size helpers."""

import base64
import io
import re

from PIL import Image

SIZE_RE = re.compile(r"^([1-9][0-9]*)x([1-9][0-9]*)$")
FORMATS = {"png": "PNG", "jpeg": "JPEG", "jpg": "JPEG", "webp": "WEBP"}


class BadImage(ValueError):
    pass


def parse_size(size: str) -> tuple[int, int] | None:
    match = SIZE_RE.match(size)
    return (int(match.group(1)), int(match.group(2))) if match else None


def decode_bytes(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # PIL raises many types for broken input
        raise BadImage(str(exc)) from exc
    return image


def decode_b64(data: str) -> Image.Image:
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception as exc:
        raise BadImage(str(exc)) from exc
    return decode_bytes(raw)


def encode_b64(image: Image.Image, output_format: str, compression: int = 100) -> str:
    fmt = FORMATS.get(output_format.lower(), "PNG")
    buffer = io.BytesIO()
    if fmt == "PNG":
        image.save(buffer, format="PNG", compress_level=4)
    else:
        if fmt == "JPEG" and image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(buffer, format=fmt, quality=max(1, min(int(compression), 100)))
    return base64.b64encode(buffer.getvalue()).decode("ascii")
