from image_gateway.config import DEFAULT_BLOCK_CONTROVERSIAL, DEFAULT_BLOCK_UNSAFE
from image_gateway.policy import PromptResult, judge_image, judge_prompt

THRESHOLDS = {"sexual": 0.5, "dangerous": 0.5, "violence": 0.5}


def prompt(label, *categories):
    return judge_prompt(
        PromptResult(label, list(categories)), DEFAULT_BLOCK_UNSAFE, DEFAULT_BLOCK_CONTROVERSIAL
    )


def test_safe_prompt_passes():
    assert not prompt("Safe", "None").blocked


def test_unsafe_sexual_is_blocked():
    verdict = prompt("Unsafe", "Sexual Content or Sexual Acts")
    assert verdict.blocked and verdict.category == "Sexual Content or Sexual Acts"


def test_controversial_sexual_is_blocked_but_controversial_violence_is_not():
    assert prompt("Controversial", "Sexual Content or Sexual Acts").blocked
    assert not prompt("Controversial", "Violent").blocked


def test_unsafe_in_an_unlisted_category_passes():
    # Names in prompts are common; the agents handle copyrighted characters.
    assert not prompt("Unsafe", "PII").blocked
    assert not prompt("Unsafe", "Copyright Violation").blocked


def test_nazi_symbols_filed_as_politically_sensitive_are_blocked():
    assert prompt("Unsafe", "Politically Sensitive Topics").blocked
    assert not prompt("Controversial", "Politically Sensitive Topics").blocked


def test_unparsable_prompt_answer_fails_closed():
    verdict = prompt(None)
    assert verdict.blocked and verdict.category == "unparsable"


def test_image_below_thresholds_passes():
    verdict = judge_image({"sexual": 0.1, "dangerous": 0.2, "violence": 0.3}, THRESHOLDS, "output")
    assert not verdict.blocked and verdict.detail["scores"]["violence"] == 0.3


def test_image_at_threshold_is_blocked_with_the_worst_policy():
    verdict = judge_image({"sexual": 0.9, "dangerous": 0.5, "violence": 0.1}, THRESHOLDS, "input")
    assert verdict.blocked and verdict.category == "sexual" and verdict.stage == "input"


def test_missing_policy_score_fails_closed():
    verdict = judge_image({"sexual": 0.1}, THRESHOLDS, "output")
    assert verdict.blocked and verdict.category.startswith("unscored:")
