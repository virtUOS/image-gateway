# image-gateway

A small service that sits in front of a self-hosted image model and makes it safe to offer to
users. It checks what people ask for and what the model produces, blocks harmful content, and
adds an invisible watermark that marks every image as AI-generated. With a signing certificate
it also signs every image with Content Credentials (C2PA).

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
6. **Adds the watermark.**
7. **Signs the image** with Content Credentials, if you switched them on, and sends it back.

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
[TrustMark](https://github.com/adobe/trustmark) (MIT licence, code and model weights). The EU AI
Act (Article 50(2)) requires machine-readable marking of AI-generated images. The watermark is
one of the two layers the EU Code of Practice asks for. [Content
Credentials](#content-credentials-c2pa) are the other.

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

## Content Credentials (C2PA)

The gateway can also sign every image with [Content Credentials](https://c2pa.org/), the C2PA
standard. They are a manifest inside the image file. It states that AI made the image, with
which software, and the signature shows any later change to the image or the manifest.

Why both? The EU Code of Practice on marking AI-generated content asks for two machine-readable
layers on images: digitally signed metadata and an invisible watermark. Each covers the other's
weakness. Anyone can read the metadata with free tools, but it is lost when an app re-encodes
the image. The watermark survives that, but only TrustMark can find it.

The manifest records:

- that AI created the image (IPTC source type `trainedAlgorithmicMedia`), or that it is an AI
  edit of an uploaded image (`compositeWithTrainedAlgorithmicMedia`)
- the image model, if you name it in `GATEWAY_C2PA_SOFTWARE_AGENT`
- that a TrustMark watermark was added, when the watermark is on (`c2pa.watermarked.unbound`:
  the watermark carries your payload, not a link to the manifest)
- the gateway and its version

It never contains the prompt, user data or the uploaded images. Nothing in it comes from the
request. It has no thumbnail either, so an image grows by only a few kilobytes. Signing takes a
few milliseconds.

Switch it on with a certificate and its key:

```yaml
image-gateway:
  environment:
    GATEWAY_C2PA: "true"
    GATEWAY_C2PA_CERT_FILE: /run/secrets/c2pa/chain.pem
    GATEWAY_C2PA_KEY_FILE: /run/secrets/c2pa/key.pem
    GATEWAY_C2PA_TSA_URL: "<an RFC 3161 timestamp server, for example your CA's>"
    GATEWAY_C2PA_SOFTWARE_AGENT: "FLUX.2 [klein] 4B"
  volumes:
    - ./c2pa:/run/secrets/c2pa:ro
```

**The certificate.** The certificate file holds the signing certificate first, then the CA
certificates above it. The key file holds the signing certificate's private key, so keep it as
secret as any other key. C2PA has rules for signing certificates. A CA must have issued the
certificate, it must allow digital signatures, and it needs an extended key usage such as email
protection or document signing. The gateway signs a test image at startup. If the certificate
or the key doesn't work, it refuses to start.

Where the certificate comes from decides what verifiers show:

- **Your own CA:** the signature is valid and every statement can be read. Public verifiers
  such as [contentcredentials.org/verify](https://contentcredentials.org/verify) don't know your
  CA, though, and show the signer as unknown.
- **A CA on the C2PA trust list:** verifiers show the signer as trusted. Such certificates usually
  cost money. See [c2pa.org](https://c2pa.org/) for the conformance programme and its trust list.

**The timestamp.** The Code of Practice asks for metadata that is signed and time-stamped. With
`GATEWAY_C2PA_TSA_URL` the gateway asks a timestamp server for every image. The signature then
proves when it was made, and it stays verifiable after the certificate expires. The server only
sees a hash, never the image. Each image costs one call, about 0.4 seconds in our tests.
Without a timestamp server the signature carries no time.

If signing fails, for example because the timestamp server is down, the gateway answers 503 and
returns no image. It never sends an image out unmarked.

**Where Content Credentials get lost.** An app that re-encodes the image drops the manifest.
LibreChat does this with every generated image today. A pull request adds an option to store
them unchanged ([LibreChat#18245](https://github.com/LibreChat-AI/LibreChat/pull/18245)). Copying
the manifest over would not help, because the signature covers the exact bytes of the image. Clients that store the image
as they receive it keep the manifest. The watermark survives in both cases.

To check an image, upload it to [contentcredentials.org/verify](https://contentcredentials.org/verify),
use the `c2patool` command line tool, or read it in Python:

```python
import c2pa

with open("image.png", "rb") as f, c2pa.Reader("image/png", f) as reader:
    print(reader.get_validation_state(), reader.json())
```

## The visible label

The gateway can also draw a small visible label in the bottom-right corner of each image. It
says "KI-generiert" unless you set your own text with `GATEWAY_AI_LABEL_TEXT`. Your text can be
**up to 40 plain characters**, for example `AI-generated` or `Mit KI erstellt`. The label uses
Pillow's built-in font, which has no umlauts, accents or typographic dashes. It would draw them
as empty boxes, so the gateway refuses to start with them.

The label is **off by default**. The invisible watermark already provides the machine-readable
marking. A visible label would spoil images that people want to use on posters or slides. The
setting exists so that you can switch the label on without waiting for a new release, for
example if your legal review asks for one.

`GATEWAY_AI_LABEL` has three modes:

- `off`: no image gets the label.
- `optional`: an image gets the label when the request asks for it. Send `"ai_label": true` in
  the JSON body of a generation, or the form field `ai_label=true` with an edit.
- `mandatory`: every image gets the label. A request that sends `false` still gets it.

Values other than `true` and `false` are refused with HTTP 400. The gateway removes the field
before the request reaches the image model.

Chat apps like LibreChat cannot send `ai_label`. For them, only `off` and `mandatory` make a
difference. If a proxy such as LiteLLM sits in front of the gateway, check that it passes the
field on.

The label is drawn before the watermark, so the watermark covers the label too.

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
| `GATEWAY_AI_LABEL` | `off` | Visible label: `off`, `optional` (each request decides) or `mandatory` |
| `GATEWAY_AI_LABEL_TEXT` | `KI-generiert` | Text of the visible label, up to 40 plain characters (no umlauts) |
| `GATEWAY_C2PA` | `false` | Sign every image with Content Credentials |
| `GATEWAY_C2PA_CERT_FILE` | none | PEM file: the signing certificate first, then its CA certificates |
| `GATEWAY_C2PA_KEY_FILE` | none | PEM file with the signing certificate's private key |
| `GATEWAY_C2PA_ALG` | `es256` | Signature algorithm that fits the key: `es256`, `es384`, `es512`, `ps256`, `ps384`, `ps512` or `ed25519` |
| `GATEWAY_C2PA_TSA_URL` | none | RFC 3161 timestamp server, recommended |
| `GATEWAY_C2PA_SOFTWARE_AGENT` | none | Name of the image model in the manifest |
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
- **Content Credentials need a client that keeps them.** LibreChat re-encodes every generated
  image and drops them, unless a pending option to keep the original is merged and switched on.
  The watermark still marks those images.

## Development

```bash
uv sync
uv run pytest -q          # stand-ins replace the safety models and vLLM
uv run ruff check . && uv run ruff format --check .
```

The tests run without a GPU. To try the real models, run `scripts/check_models.py` inside the
container on a GPU host. It sends a fixed set of harmless and harmful prompts and images
through the checks and confirms that the watermark survives re-encoding.
