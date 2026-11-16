import pytest
from PIL import Image, ImageChops

from image_gateway.images import add_label

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


def test_umlauts_and_dashes_are_drawn():
    image = Image.new("RGB", (512, 512), GREY)
    assert changed(image, add_label(image, "Mit KI erzeugt – Ü"))
