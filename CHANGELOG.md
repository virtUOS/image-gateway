# Changelog

## Unreleased

- First version: prompt check (Qwen3Guard-Gen), input and output image check (ShieldGemma 2),
  invisible TrustMark watermark, request defaults for FLUX.2 [klein] on vLLM-Omni, Prometheus
  counters. Only `/v1/images/generations`, `/v1/images/edits` and `/v1/models` reach the
  upstream; the service fails closed when a checker errors.
