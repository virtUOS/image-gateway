import pytest
from PIL import Image, ImageChops
from pydantic import ValidationError

from image_gateway.config import AI_LABEL_MAX_CHARS, Settings
from image_gateway.images import add_label, label_font

from fakes import GREY


def changed(before: Image.Image, after: Image.Image) -> tuple[int, int, int, int] | None:
    """The box (left, top, right, bottom; right and bottom exclusive) where RGB differs."""
    return ImageChops.difference(before.convert("RGB"), after.convert("RGB")).getbbox()


def test_the_label_sits_in_the_bottom_right_corner():
    image = Image.new("RGB", (1024, 1024), GREY)
    out = add_label(image, "KI-generiert")
    assert out.size == image.size and out.mode == "RGB"
    left, top, _right, _bottom = changed(image, out)
    assert left > 512 and top > 900
    assert image.getpixel((1000, 1000)) == GREY  # the input is untouched


@pytest.mark.parametrize("side", [512, 1024, 2048])
def test_the_label_is_readable_but_small(side):
    image = Image.new("RGB", (side, side), GREY)
    left, top, right, bottom = changed(image, add_label(image, "KI-generiert"))
    height = bottom - top
    area = (right - left) * height
    assert height >= 12
    assert area < 0.015 * side * side


@pytest.mark.parametrize("size", [(1, 1), (64, 48), (2048, 256), (256, 2048)])
def test_any_image_size_gets_a_label_without_error(size):
    image = Image.new("RGB", size, GREY)
    out = add_label(image, "KI-generiert")
    assert out.size == size
    if size != (1, 1):
        assert changed(image, out)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("RGB", "RGB"), ("L", "RGB"), ("P", "RGB"), ("RGBA", "RGBA"), ("LA", "RGBA")],
)
def test_transparency_is_kept_and_other_modes_work(mode, expected):
    out = add_label(Image.new(mode, (256, 256)), "KI-generiert")
    assert out.mode == expected
    if expected == "RGBA":
        assert out.getpixel((0, 0))[3] == 0  # away from the label, still transparent


@pytest.mark.parametrize(
    "text",
    [
        "".join(map(chr, range(32, 127))),  # every printable ASCII character
        "Mit KI erzeugt – Ü",
        "Künstlich erzeugt",
        "Généré par IA",
        "KI\u200b",
    ],
)
def test_the_settings_accept_only_text_the_label_font_can_draw(text):
    """The built-in font draws a missing character as an empty box, without an error."""
    try:
        Settings(ai_label_text=text[:AI_LABEL_MAX_CHARS])
    except ValidationError:
        return
    font = label_font(20)
    box = bytes(font.getmask("\uffff"))
    missing = [char for char in set(text) - {" "} if bytes(font.getmask(char)) == box]
    assert not missing, f"accepted but drawn as boxes: {missing}"
