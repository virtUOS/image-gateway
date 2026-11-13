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
