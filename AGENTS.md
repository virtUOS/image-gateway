# Working in this repository

image-gateway sits between clients and a self-hosted image model (vLLM-Omni serving
FLUX.2 [klein]). It checks prompts and images with two safety models, adds an invisible
TrustMark watermark and fills in request defaults. README.md explains it for people who run
it. This file covers what you need to know to change it safely.

## Where things are

| Path | Holds |
| --- | --- |
| `src/image_gateway/app.py` | the FastAPI app: routes, the request flow, errors, metrics |
| `src/image_gateway/policy.py` | pure decision logic: when a prompt or an image is blocked, parsing the prompt checker's answer |
| `src/image_gateway/config.py` | every setting (`GATEWAY_*`), the default block lists and thresholds, the watermark payload check |
| `src/image_gateway/models.py` | the real safety models and TrustMark (torch, transformers). Only the container imports it |
| `src/image_gateway/images.py` | decoding, encoding and size parsing |
| `tests/fakes.py` | stand-ins for both safety models, the watermark and vLLM |
| `scripts/check_models.py` | calibration run with the real models on a GPU host |
| `CHANGELOG.md` | the changelog in Keep a Changelog format. New entries go under `[Unreleased]` |

## Commands

```bash
uv sync
uv run pytest -q
uv run ruff check . && uv run ruff format --check .   # ruff also formats code blocks in *.md
```

CI also runs the tests on the Python version from the Dockerfile's `FROM python:` line. To do
the same locally, pass the version explicitly (`uv run --python 3.15 pytest -q`). Without
`--python`, uv follows `.python-version` and quietly tests the older version. On macOS some
dependencies don't build for brand-new Python versions. Run the tests in a `python:<version>-slim`
container then, with `UV_PYTHON_DOWNLOADS=never` and `UV_PYTHON=/usr/local/bin/python3`.

## Rules

1. **Fail closed.** If a safety model or the watermark raises, the request gets 503
   `safety_check_unavailable`. Never forward a request or return an image that wasn't checked.
2. **Allowlist the routes.** Only `/v1/images/generations`, `/v1/images/edits`, `/v1/models`,
   `/health` and `/gateway/metrics` exist. Do not add a catch-all proxy: vLLM-Omni can make
   images through its chat endpoint, which would skip every check.
3. **No prompt text or images in logs or metrics.** Log decisions, categories and scores only.
   Metric labels stay limited to endpoint, outcome, stage and category.
4. **Keep the error format.** Blocks are HTTP 400 with `code: content_policy_violation` and a
   message starting "Rejected by the safety system of this image service". Clients rely on it,
   for example the agent instructions in kiwi (librechat-setup, `scripts/bilder-flux/`).
5. **Keep torch out of the core.** Only `models.py` imports torch, transformers or trustmark.
   Put new logic in `policy.py` or `app.py`, where the tests reach it without a GPU.
6. **Don't change the watermark variant or encoding casually.** TrustMark can only decode an
   image with the variant it was marked with (Q here, BCH_5). Switching breaks detection of
   every image marked before. The payload is limited to 8 printable ASCII characters, and
   `config.py` enforces that because TrustMark garbles longer text without an error.

## When the tests are not enough

The tests use stand-ins, so they cannot tell whether the real models still behave. Run
`scripts/check_models.py` on a GPU host after changing any of these:

- the safety models, block lists or thresholds
- the Python base image
- runtime dependencies (torch, transformers, trustmark, accelerate)

Compare its output with the previous run. Decisions, scores and the watermark lines should
match. The script needs about 16 GB of free GPU memory. On a host where the gateway already
runs next to the image model, stop the live gateway for the run (about 90 seconds). It prints
scores only and never stores images.

Dependabot bumps of the base image or runtime dependencies need such a run before merging.
Bumps of build tools, GitHub Actions and dev dependencies only need green CI. Dependabot rebases
open PRs whenever another one merges, so merge with `--match-head-commit <sha>` to be sure you
merge what was tested.

## Releases

1. Set the version in `pyproject.toml` and move `[Unreleased]` in CHANGELOG.md to
   `[x.y.z] - YYYY-MM-DD`.
2. Merge to main.
3. Tag the merge commit: `git tag -a vX.Y.Z -m "vX.Y.Z: <one line>"` and push the tag.

The release workflow publishes `ghcr.io/virtuos/image-gateway:X.Y.Z` and `:X.Y`. It fails if the
tag and `pyproject.toml` disagree. Hosts pin the full version, so a release changes nothing
until a host's deployment is updated.

## Writing

README.md is read by people at other institutions who want to run this. Write short, plain
sentences and explain why a rule exists. Avoid chains of clauses joined by semicolons.
