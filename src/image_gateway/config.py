"""Settings, read from GATEWAY_* environment variables (lists and dicts as JSON)."""

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

    # Invisible watermark (TrustMark). The payload is ASCII, at most 8 characters with BCH_5.
    watermark: bool = True
    watermark_payload: str = "KI-UOS"
    watermark_model: str = "Q"
    watermark_strength: float = 1.0

    device: str = "cuda"
