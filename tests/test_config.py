import pytest
from pydantic import ValidationError

from image_gateway.config import Settings


@pytest.mark.parametrize("payload", ["KI-UOS", "AI-TUM", "AI-UNI-X", "X"])
def test_short_ascii_watermark_payloads_are_accepted(payload):
    assert Settings(watermark_payload=payload).watermark_payload == payload


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("AI-UNIVERSITY", "1 to 8 characters"),
        ("", "1 to 8 characters"),
        ("KI-Ü", "printable ASCII"),
        ("KI\tUOS", "printable ASCII"),
    ],
)
def test_payloads_the_watermark_cannot_carry_are_refused(payload, reason):
    with pytest.raises(ValidationError, match=reason):
        Settings(watermark_payload=payload)


def test_the_payload_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("GATEWAY_WATERMARK_PAYLOAD", "AI-TUM")
    assert Settings().watermark_payload == "AI-TUM"
    monkeypatch.setenv("GATEWAY_WATERMARK_PAYLOAD", "AI-UNIVERSITY")
    with pytest.raises(ValidationError):
        Settings()


def test_the_visible_label_is_off_by_default():
    settings = Settings()
    assert (settings.ai_label, settings.ai_label_text) == ("off", "KI-generiert")


def test_label_mode_and_text_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("GATEWAY_AI_LABEL", "mandatory")
    monkeypatch.setenv("GATEWAY_AI_LABEL_TEXT", "Mit KI erzeugt")
    settings = Settings()
    assert (settings.ai_label, settings.ai_label_text) == ("mandatory", "Mit KI erzeugt")


def test_an_unknown_label_mode_stops_startup(monkeypatch):
    monkeypatch.setenv("GATEWAY_AI_LABEL", "always")
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("text", ["AI-generated", "Mit KI erzeugt – Ü", "x" * 40])
def test_readable_label_texts_are_accepted(text):
    assert Settings(ai_label_text=text).ai_label_text == text


@pytest.mark.parametrize("text", ["", "   ", "x" * 41, "KI\ngeneriert"])
def test_label_texts_that_would_hide_or_break_the_label_are_refused(text):
    with pytest.raises(ValidationError):
        Settings(ai_label_text=text)
