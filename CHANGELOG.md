# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-11-17

### Added

- Optional C2PA Content Credentials (`GATEWAY_C2PA`, off by default): every returned image gets
  a signed manifest that says AI created it (IPTC `trainedAlgorithmicMedia`) or edited an
  uploaded image (`compositeWithTrainedAlgorithmicMedia`), that a TrustMark watermark was added,
  and optionally the image model (`GATEWAY_C2PA_SOFTWARE_AGENT`). With the watermark this gives
  the two layers the EU Code of Practice asks for on images. The manifest carries no prompt
  text, user data, uploaded images or thumbnail. Certificate and key come from PEM files
  (`GATEWAY_C2PA_CERT_FILE`, `GATEWAY_C2PA_KEY_FILE`). `GATEWAY_C2PA_TSA_URL` adds an RFC 3161
  timestamp. The gateway signs a test image at startup and refuses to start with a certificate
  or key that can't sign. A signing failure at request time returns 503 like a failed check.
  New dependency: c2pa-python 0.38.
- Optional visible label in the bottom-right corner of each image, "KI-generiert" by default
  (`GATEWAY_AI_LABEL_TEXT`, up to 40 printable ASCII characters, because the built-in font has
  no umlauts). `GATEWAY_AI_LABEL` switches it: `off` (the default), `optional`
  (a request asks for it with `ai_label: true`) or `mandatory` (every image, whatever the
  request says). Values of `ai_label` other than true and false get HTTP 400, and the field
  never reaches vLLM-Omni. The label is drawn before the watermark, so the watermark covers it.

### Changed

- The image runs Python 3.15 (was 3.12) and is built with uv 0.13.8. Checked on the GPU: every
  prompt and image decision and the watermark are the same as on 3.12.
- CI also runs the test suite on the Python version the image is built from (read from the
  `FROM python:` line of the Dockerfile), so a base-image bump is tested, not only built.

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

[Unreleased]: https://github.com/virtUOS/image-gateway/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/virtUOS/image-gateway/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/virtUOS/image-gateway/releases/tag/v0.1.0
