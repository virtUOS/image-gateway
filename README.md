# image-gateway

A small service that sits in front of a self-hosted image model and makes it safe to offer to
users. It checks what people ask for and what the model produces, blocks harmful content, and
adds an invisible watermark that marks every image as AI-generated.

We built it at the University of Osnabrück for [FLUX.2 [klein]](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)
served by [vLLM-Omni](https://github.com/vllm-project/vllm-omni). It speaks the OpenAI image API,
so clients such as LibreChat or LiteLLM use it exactly like the image API behind it.

## How it works

The gateway is the only way to reach the image model:

```
client → (LiteLLM) → reverse proxy → image-gateway → vLLM-Omni
```

When someone asks for an image, the gateway:

1. **Checks the description.** A text safety model reads the prompt. If it asks for something
   harmful, the request stops here and no image is made.
2. **Checks uploaded images** (only when editing). An image safety model looks at every image
   the user wants to change.
3. **Fills in defaults** that the model needs, such as size and number of steps.
4. **Lets the model generate or edit** the image.
5. **Checks the result** with the image safety model. A harmful result is never returned.
6. **Adds the watermark** and sends the image back.

Each check takes about 0.3 seconds on an NVIDIA L40S. A typical image takes around 3 seconds
from request to answer.

Only the image endpoints get through. Every other route returns 404. This matters because
vLLM-Omni can also create images through its chat endpoint, which would skip all the checks.

| Endpoint | What it does |
|---|---|
| `POST /v1/images/generations` | Creates an image, with all the checks above |
| `POST /v1/images/edits` | Edits uploaded images, with all the checks above |
| `GET /v1/models` | Lists the model, passed through unchanged |
| `GET /health` | Answers 200 when the gateway and the model are up |
| `GET /gateway/metrics` | Prometheus counters for requests, blocks and timings |

## What gets blocked

Two open models do the checking:

- **Prompts:** [Qwen3Guard-Gen-4B](https://huggingface.co/Qwen/Qwen3Guard-Gen-4B) (Apache 2.0).
  It understands German and English, among many other languages. It rates each prompt as safe,
  controversial or unsafe and names a category. By default the gateway blocks unsafe prompts
  about sexual content, violence, illegal acts, self-harm and hate. It also blocks Nazi symbols,
  fake pictures of real politicians and attempts to talk the model out of its rules. For sexual
  content it blocks prompts rated controversial too, so nudity is never allowed.
- **Images:** [ShieldGemma 2](https://huggingface.co/google/shieldgemma-2-4b-it) (Gemma terms,
  gated on Hugging Face). It gives each image a score for sexual content, dangerous content and
  violence. An image that scores 0.5 or higher in any of them is blocked.

A blocked request gets HTTP 400 with the error code `content_policy_violation`, the code that
OpenAI's own API uses. If a safety model fails, the gateway answers 503 and generates nothing.
It never lets a request through unchecked.

A few more rules keep the checks honest. Images for editing must be uploaded, because remote
image URLs could point anywhere. Very long prompts are refused instead of being cut short,
so nothing hides at the end.

The logs and metrics record what was decided and why, but never the prompt or the image.

## The watermark

Every image the gateway returns carries an invisible watermark made with
[TrustMark](https://github.com/adobe/trustmark) (MIT licence, code and model weights). This is
the machine-readable marking that the EU AI Act (Article 50(2)) requires for AI-generated
images.

The watermark survives resizing, cropping and JPEG compression. That matters in practice: chat
apps like LibreChat re-encode generated images and drop any metadata, but the watermark stays.
To check an image:

```python
from trustmark import TrustMark

text, present, _ = TrustMark(model_type="Q").decode(image, MODE="text")
```

`present` tells you whether the image is watermarked. `text` says who marked it. Set your own
text with `GATEWAY_WATERMARK_PAYLOAD`. It can be **up to 8 plain characters** (letters, digits,
hyphens), for example `AI-TUM` or `KI-UNI-X`. Longer text or special characters such as umlauts
come back garbled, so the gateway refuses to start with them.

## Running it

The image is published at `ghcr.io/virtuos/image-gateway`. It needs an NVIDIA GPU with about
16 GB of free memory for the two safety models, next to whatever the image model uses. FLUX.2
[klein] 4B plus the gateway fit on a single 48 GB card.

A minimal Docker Compose setup next to vLLM-Omni:

```yaml
services:
  vllm:
    image: vllm/vllm-omni:v0.32.0
    command: vllm serve black-forest-labs/FLUX.2-klein-4B --omni --host 0.0.0.0 --port 8000
    # ... GPU and cache settings as usual

  image-gateway:
    image: ghcr.io/virtuos/image-gateway:0.1.0
    environment:
      HF_TOKEN: "<a Hugging Face token with access to ShieldGemma 2>"
      GATEWAY_UPSTREAM_URL: "http://vllm:8000"
      GATEWAY_WATERMARK_PAYLOAD: "AI-MYUNI"
    volumes:
      - /path/to/huggingface/hub:/root/.cache/huggingface/hub
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

Point your reverse proxy at `image-gateway:8000` instead of at vLLM, and don't expose vLLM's
port anywhere else. On the first start the gateway downloads both safety models (about 17 GB)
into the Hugging Face cache. After that it is ready in under a minute.

ShieldGemma 2 is gated: accept Google's terms on its Hugging Face page with the account whose
token you use.

## Settings

All settings are environment variables. Lists and dictionaries are written as JSON.

| Variable | Default | What it does |
|---|---|---|
| `GATEWAY_UPSTREAM_URL` | `http://vllm:8000` | Where the image model runs |
| `GATEWAY_WATERMARK_PAYLOAD` | `KI-UOS` | Text in the watermark, up to 8 plain characters |
| `GATEWAY_BLOCK_UNSAFE` | see [`config.py`](src/image_gateway/config.py) | Categories blocked when a prompt is rated unsafe |
| `GATEWAY_BLOCK_CONTROVERSIAL` | `["Sexual Content or Sexual Acts"]` | Categories blocked even when a prompt is only rated controversial |
| `GATEWAY_IMAGE_THRESHOLDS` | `{"sexual": 0.5, "dangerous": 0.5, "violence": 0.5}` | Score from which an image is blocked |
| `GATEWAY_DEFAULT_SIZE` | `1024x1024` | Size used when a request asks for `auto` or none |
| `GATEWAY_DEFAULT_STEPS` | `4` | Steps used when a request names none (FLUX.2 [klein] is made for 4) |
| `GATEWAY_DEFAULT_GUIDANCE` | `1.0` | Guidance used when a request names none |
| `GATEWAY_MAX_SIDE` | `2048` | Largest allowed width or height |
| `GATEWAY_MAX_IMAGES` | `4` | Most images per request |
| `GATEWAY_MAX_PROMPT_CHARS` | `8000` | Longest allowed prompt |

A client's own values always win over the defaults. So when someone asks for 8 steps, they get
8 steps.

## Known limits

- **Some medical and educational images are blocked.** An illustration of breastfeeding for a
  midwifery course is rated "controversial, sexual" and refused. It gets the same rating as a
  request to undress a person in a photo, so loosening one would loosen both.
- **ShieldGemma 2 judges gore, not threats.** A picture of someone aiming a gun at people, with
  no injury shown, passes the image check. The prompt check stops such requests before an image
  is made.
- **No C2PA content credentials yet.** They would be stripped by most chat apps anyway, which
  is why the watermark does the marking.

## Development

```bash
uv sync
uv run pytest -q          # stand-ins replace the safety models and vLLM
uv run ruff check . && uv run ruff format --check .
```

The tests run without a GPU. To try the real models, run `scripts/check_models.py` inside the
container on a GPU host. It sends a fixed set of harmless and harmful prompts and images
through the checks and confirms that the watermark survives re-encoding.
