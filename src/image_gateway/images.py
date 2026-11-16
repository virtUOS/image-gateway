"""Image decoding/encoding, request-size helpers and the visible AI label."""

import base64
import io
import re

from PIL import Image, ImageDraw, ImageFont

SIZE_RE = re.compile(r"^([1-9][0-9]*)x([1-9][0-9]*)$")
FORMATS = {"png": "PNG", "jpeg": "JPEG", "jpg": "JPEG", "webp": "WEBP"}

# Visible label: text height as a share of the shorter side, never below 12 px so it stays
# readable at 512 px. A label too wide for a small image shrinks, down to 6 px.
LABEL_HEIGHT_RATIO = 0.03
LABEL_MIN_PX = 12
LABEL_SHRINK_MIN_PX = 6
LABEL_BOX_FILL = (0, 0, 0, 140)
LABEL_TEXT_FILL = (255, 255, 255, 235)


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


def has_transparency(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info


def _measure(text: str, size: int):
    font = ImageFont.load_default(size=size)  # Pillow's built-in font, no font files needed
    bbox = font.getbbox(text)
    pad = max(2, size // 3)
    return font, bbox, pad, bbox[2] - bbox[0] + 2 * pad, bbox[3] - bbox[1] + 2 * pad


def add_label(image: Image.Image, text: str) -> Image.Image:
    """A copy of `image` with `text` on a dark box in the bottom-right corner.

    Transparent images stay transparent outside the label; everything else comes back as RGB.
    It does not raise for small images: what does not fit is clipped.
    """
    width, height = image.size
    size = max(LABEL_MIN_PX, round(min(width, height) * LABEL_HEIGHT_RATIO))
    margin = max(1, size // 2)
    font, bbox, pad, box_w, box_h = _measure(text, size)
    room = width - 2 * margin
    if box_w > room:
        size = max(LABEL_SHRINK_MIN_PX, size * room // box_w)
        margin = max(1, size // 2)
        font, bbox, pad, box_w, box_h = _measure(text, size)

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    right, bottom = width - margin, height - margin
    left, top = right - box_w, bottom - box_h
    draw.rectangle((left, top, right - 1, bottom - 1), fill=LABEL_BOX_FILL)
    draw.text((left + pad - bbox[0], top + pad - bbox[1]), text, font=font, fill=LABEL_TEXT_FILL)
    labelled = Image.alpha_composite(image.convert("RGBA"), overlay)
    return labelled if has_transparency(image) else labelled.convert("RGB")
