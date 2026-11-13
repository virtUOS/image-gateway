# uv comes from its own image; a FROM line so Dependabot's docker ecosystem bumps it.
FROM ghcr.io/astral-sh/uv:0.11.14 AS uv

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/root/.cache/huggingface

WORKDIR /app
COPY --from=uv /uv /bin/uv
# Dependencies first (cached while uv.lock is unchanged), the project after its source.
# The models extra brings torch (with its CUDA libraries), transformers and trustmark.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-editable --no-install-project --extra models
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --extra models \
    && rm /bin/uv

# TrustMark downloads its weights (MIT) on first use; keep them in the image so a start
# needs no network for the watermark. The checker models come from the mounted HF cache.
RUN python -c "from trustmark import TrustMark; TrustMark(verbose=False, model_type='Q', device='cpu')"

EXPOSE 8000

# Loading both checkers takes a while on the first start (download) and ~1 min after.
HEALTHCHECK --interval=30s --timeout=5s --start-period=900s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "--factory", "image_gateway.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
