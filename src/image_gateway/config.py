"""Settings, read from GATEWAY_* environment variables (lists and dicts as JSON)."""

import os
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Qwen3Guard categories. "Unsafe" in any of these blocks a prompt. "Politically Sensitive
# Topics" is where it files Nazi symbols (a swastika poster came back Unsafe with only this
# category); benign political motifs come back Safe. PII and Copyright Violation stay open:
# names in prompts are common, and the agents handle copyrighted characters.
DEFAULT_BLOCK_UNSAFE = [
    "Sexual Content or Sexual Acts",
    "Violent",
    "Non-violent Illegal Acts",
    "Suicide & Self-Harm",
    "Unethical Acts",
    "Politically Sensitive Topics",
    "Jailbreak",
]
# "Controversial" blocks only here: nudity and suggestive content are not allowed at all.
DEFAULT_BLOCK_CONTROVERSIAL = ["Sexual Content or Sexual Acts"]

# ShieldGemma 2 policies and the "Yes" probability from which an image is blocked.
DEFAULT_IMAGE_THRESHOLDS = {"sexual": 0.5, "dangerous": 0.5, "violence": 0.5}

# TrustMark with BCH_5 error correction carries 61 bits; text mode spends 7 bits per ASCII
# character, so 8 characters fit. A longer or non-ASCII payload is not refused by TrustMark:
# it comes back garbled ("AI-UNIVERSITY" decodes as "AI-UNIVEP", "KI-Ü" as "KI-\").
WATERMARK_MAX_CHARS = 8

# The visible label is drawn with Pillow's built-in font. It has every printable ASCII character
# but no umlauts, typographic dashes or accents, and draws those as empty boxes without an
# error. 40 characters still fit in a corner.
AI_LABEL_MAX_CHARS = 40


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_")

    upstream_url: str = "http://vllm:8000"
    upstream_timeout: float = 600.0

    # Request defaults. vLLM-Omni rejects size "auto" for generations and runs 50 steps when a
    # request names none; FLUX.2 [klein] is distilled for 4 steps without guidance.
    default_size: str = "1024x1024"
    default_steps: int | None = 4
    default_guidance: float | None = 1.0
    max_side: int = 2048
    max_images: int = 4
    # Longer prompts are refused rather than truncated, so nothing slips past the check.
    max_prompt_chars: int = 8000

    # Checks. Turning one off is meant for tests and debugging only.
    check_prompts: bool = True
    check_images: bool = True
    prompt_guard_model: str = "Qwen/Qwen3Guard-Gen-4B"
    image_guard_model: str = "google/shieldgemma-2-4b-it"
    block_unsafe: list[str] = DEFAULT_BLOCK_UNSAFE
    block_controversial: list[str] = DEFAULT_BLOCK_CONTROVERSIAL
    image_thresholds: dict[str, float] = DEFAULT_IMAGE_THRESHOLDS

    # Invisible watermark (TrustMark). The payload says whose gateway marked the image:
    # 1 to 8 printable ASCII characters, e.g. "KI-UOS" or "AI-TUM".
    watermark: bool = True
    watermark_payload: str = "KI-UOS"
    watermark_model: str = "Q"
    watermark_strength: float = 1.0

    # Visible label ("KI-generiert") in the bottom-right corner. Off by default: the invisible
    # watermark is the machine-readable marking the AI Act asks for, and a visible label spoils
    # images meant for posters or slides. It is here so a host can switch it on without a new
    # release if its legal review asks for one. "optional": a request asks for it with
    # "ai_label": true. "mandatory": every image gets it, whatever the request says.
    ai_label: Literal["off", "optional", "mandatory"] = "off"
    ai_label_text: str = "KI-generiert"

    # Content Credentials (C2PA): a signed statement inside the image file that AI made it.
    # Off by default, because it needs a signing certificate. The EU Code of Practice on
    # marking AI-generated content asks for it next to the watermark on images.
    # cert_file: PEM chain, signing certificate first. key_file: its PEM private key.
    # tsa_url: an RFC 3161 timestamp server. The signature then carries a trusted time and
    # stays verifiable after the certificate expires. Every image costs one call to it.
    # software_agent: names the image model in the manifest, e.g. "FLUX.2 [klein] 4B".
    c2pa: bool = False
    c2pa_cert_file: Path | None = None
    c2pa_key_file: Path | None = None
    c2pa_alg: Literal["es256", "es384", "es512", "ps256", "ps384", "ps512", "ed25519"] = "es256"
    c2pa_tsa_url: str | None = None
    c2pa_software_agent: str | None = None

    device: str = "cuda"

    @field_validator("watermark_payload")
    @classmethod
    def payload_fits_the_watermark(cls, value: str) -> str:
        if not 1 <= len(value) <= WATERMARK_MAX_CHARS:
            raise ValueError(
                f"must be 1 to {WATERMARK_MAX_CHARS} characters (got {len(value)}): "
                "a longer text comes back garbled from the watermark"
            )
        if not all(32 <= ord(char) <= 126 for char in value):
            raise ValueError(
                "must be printable ASCII (letters A-Z/a-z, digits, - _ . and the like, "
                "no umlauts): other characters come back garbled from the watermark"
            )
        return value

    @field_validator("ai_label_text")
    @classmethod
    def label_text_is_readable(cls, value: str) -> str:
        if not value.strip() or len(value) > AI_LABEL_MAX_CHARS:
            raise ValueError(
                f"must be 1 to {AI_LABEL_MAX_CHARS} characters and not only spaces "
                f"(got {len(value)}): the label has to show something and fit in a corner"
            )
        if not all(32 <= ord(char) <= 126 for char in value):
            raise ValueError(
                "must be printable ASCII (no umlauts, accents or typographic dashes like –): "
                "the built-in font draws other characters as empty boxes"
            )
        return value

    @model_validator(mode="after")
    def c2pa_has_its_files(self) -> "Settings":
        if not self.c2pa:
            return self
        for name in ("c2pa_cert_file", "c2pa_key_file"):
            path = getattr(self, name)
            if path is None:
                raise ValueError(f"GATEWAY_{name.upper()} must be set when GATEWAY_C2PA is on")
            if not path.is_file() or not os.access(path, os.R_OK):
                raise ValueError(f"GATEWAY_{name.upper()}: {path} is not a readable file")
        return self
