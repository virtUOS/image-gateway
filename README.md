# image-gateway

A safety and marking gateway in front of an OpenAI-compatible image API, built for
[vLLM-Omni](https://github.com/vllm-project/vllm-omni) serving FLUX.2 [klein]. Clients talk to the
gateway exactly as they would to the image API; the gateway is the only way in.

```
client → (LiteLLM) → Caddy → image-gateway → vLLM-Omni
```

| Request | What happens |
|---|---|
| `POST /v1/images/generations` | prompt check → defaults → generate → image check → watermark |
| `POST /v1/images/edits` | prompt check → check uploaded images → defaults → edit → image check → watermark |
| `GET /v1/models` | passed through |
| `GET /health` | 200 when the gateway is up and vLLM answers |
| `GET /gateway/metrics` | Prometheus counters: requests, blocks per stage and category, timings |
| anything else | 404, so e.g. vLLM-Omni's chat completions can't be used to bypass the checks |

## Checks

- **Prompt:** [Qwen3Guard-Gen-4B](https://huggingface.co/Qwen/Qwen3Guard-Gen-4B) (Apache-2.0,
  multilingual) labels the request *Safe*, *Controversial* or *Unsafe* with categories. *Unsafe* in
  `GATEWAY_BLOCK_UNSAFE` blocks; *Controversial* blocks only in `GATEWAY_BLOCK_CONTROVERSIAL`
  (default: sexual content).
- **Images:** [ShieldGemma 2](https://huggingface.co/google/shieldgemma-2-4b-it) (Gemma terms,
  gated) gives the probability that an image violates *sexual*, *dangerous* and *violence*.
  `GATEWAY_IMAGE_THRESHOLDS` sets the block line per policy. Uploaded images on edits and every
  generated image are checked.
- A blocked request gets HTTP 400 with `code: content_policy_violation`; nothing is generated
  for a blocked prompt, and a blocked result is never returned.
- **Fail closed:** if a checker errors, the request gets 503 `safety_check_unavailable`.
- Remote image URLs (`url`, `url[]`) on edits are refused; images must be uploaded. Prompts
  longer than `GATEWAY_MAX_PROMPT_CHARS` are refused, not truncated.
- Logs and metrics carry decisions and scores, never prompt text or images.

## Watermark

Every returned image carries an invisible [TrustMark](https://github.com/adobe/trustmark)
watermark (MIT, code and weights; variant Q, BCH_5, payload `GATEWAY_WATERMARK_PAYLOAD`). It is
the machine-readable marking that EU AI Act Art. 50(2) asks of AI-generated images, and it
survives resizing and JPEG re-encoding, which matters because chat front ends such as LibreChat
re-encode generated images and drop embedded metadata. Detect it with:

```python
from trustmark import TrustMark

secret, present, schema = TrustMark(model_type="Q").decode(image, MODE="text")
```

## Defaults

vLLM-Omni rejects `size: "auto"` for generations and runs 50 steps when a request names none;
FLUX.2 [klein] is distilled for 4 steps without guidance. The gateway fills in
`GATEWAY_DEFAULT_SIZE` (1024x1024), `GATEWAY_DEFAULT_STEPS` (4) and `GATEWAY_DEFAULT_GUIDANCE`
(1.0) when a request has none; a client's own values win. It caps `n` at `GATEWAY_MAX_IMAGES`
and either side at `GATEWAY_MAX_SIDE`, and always asks vLLM for lossless PNG so the watermark
goes in before the requested `output_format` is encoded.

## Configuration

All settings are `GATEWAY_*` environment variables (lists and dicts as JSON); see
[`config.py`](src/image_gateway/config.py). The container needs a GPU, `HF_TOKEN` with access to
ShieldGemma 2, and the Hugging Face cache mounted at `/root/.cache/huggingface/hub`. Both
checkers together take roughly 17 GB of GPU memory.

## Development

```bash
uv sync
uv run pytest -q          # fakes stand in for the models and vLLM
uv run ruff check . && uv run ruff format --check .
```

`scripts/check_models.py` runs the real models in the container against a fixed set of prompts
and generated images and checks that the watermark survives re-encoding.
