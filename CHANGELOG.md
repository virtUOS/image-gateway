# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-13

### Added

- Gateway in front of vLLM-Omni's OpenAI image API. Only `/v1/images/generations`,
  `/v1/images/edits` and `/v1/models` reach the upstream; every other route answers 404.
- Prompt check with Qwen3Guard-Gen (blocks configurable per category and severity), input
  image check on edits and output check on every image with ShieldGemma 2 (thresholds per
  policy). Blocks return HTTP 400 `content_policy_violation`; a failing checker returns 503
  (fail closed).
- Invisible TrustMark watermark on every returned image, payload set with
  `GATEWAY_WATERMARK_PAYLOAD`. The gateway refuses to start with a payload TrustMark can't
  carry (more than 8 characters or not printable ASCII), because TrustMark would embed it
  garbled without an error.
- Request defaults for FLUX.2 [klein]: size `auto` or missing → 1024x1024, 4 steps,
  guidance 1.0; a client's own values win. Remote image URLs and overlong prompts are refused.
- Prometheus counters at `/gateway/metrics`; logs carry decisions and scores, never prompt text.

[Unreleased]: https://github.com/virtUOS/image-gateway/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/virtUOS/image-gateway/releases/tag/v0.1.0
