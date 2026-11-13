"""Decisions from checker results. Pure functions, so the rules are easy to test."""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PromptResult:
    """What the prompt checker said: Safe, Controversial or Unsafe, plus categories."""

    label: str | None
    categories: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Verdict:
    blocked: bool
    stage: str  # "prompt", "input" or "output"
    category: str | None = None
    detail: dict = field(default_factory=dict)


def judge_prompt(
    result: PromptResult, block_unsafe: list[str], block_controversial: list[str]
) -> Verdict:
    detail = {"label": result.label, "categories": result.categories}
    if result.label is None:
        # The checker's answer could not be parsed: fail closed.
        return Verdict(True, "prompt", "unparsable", detail)
    blocklist = {"Unsafe": block_unsafe, "Controversial": block_controversial}.get(result.label, [])
    for category in result.categories:
        if category in blocklist:
            return Verdict(True, "prompt", category, detail)
    return Verdict(False, "prompt", None, detail)


def judge_image(scores: dict[str, float], thresholds: dict[str, float], stage: str) -> Verdict:
    detail = {"scores": {k: round(v, 4) for k, v in scores.items()}}
    missing = [policy for policy in thresholds if policy not in scores]
    if missing:
        return Verdict(True, stage, "unscored:" + ",".join(missing), detail)
    worst = max(thresholds, key=lambda policy: scores[policy] - thresholds[policy])
    if scores[worst] >= thresholds[worst]:
        return Verdict(True, stage, worst, detail)
    return Verdict(False, stage, None, detail)


LABEL_RE = re.compile(r"Safety: (Safe|Unsafe|Controversial)")
CATEGORY_RE = re.compile(
    r"(Violent|Non-violent Illegal Acts|Sexual Content or Sexual Acts|PII|Suicide & Self-Harm"
    r"|Unethical Acts|Politically Sensitive Topics|Copyright Violation|Jailbreak|None)"
)


def parse_guard_output(text: str) -> PromptResult:
    label = LABEL_RE.search(text)
    categories_line = text.split("Categories:", 1)[1] if "Categories:" in text else ""
    return PromptResult(label.group(1) if label else None, CATEGORY_RE.findall(categories_line))
