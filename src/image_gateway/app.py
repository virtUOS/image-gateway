"""The gateway: only the two image endpoints reach vLLM-Omni, and only through the checks.

generations:  check prompt -> fill defaults -> generate -> check each image -> watermark
edits:        check prompt -> check uploaded images -> fill defaults -> edit -> check -> watermark

A checker that fails blocks the request (503); nothing passes unchecked. Logs and metrics carry
decisions and scores, never prompt text or images.
"""

import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Protocol

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.concurrency import run_in_threadpool

from image_gateway import images
from image_gateway.config import Settings
from image_gateway.policy import PromptResult, Verdict, judge_image, judge_prompt

log = logging.getLogger("image_gateway")

IMAGE_FIELDS = ("image", "image[]", "reference_image")
URL_FIELDS = ("url", "url[]")
STAGE_TEXT = {
    "prompt": "the image description",
    "input": "an uploaded image",
    "output": "the generated image",
}


class PromptGuard(Protocol):
    def check(self, text: str) -> PromptResult: ...


class ImageGuard(Protocol):
    def scores(self, image: Image.Image) -> dict[str, float]: ...


class Watermarker(Protocol):
    def apply(self, image: Image.Image) -> Image.Image: ...


class GatewayError(Exception):
    def __init__(self, status: int, message: str, code: str, param: str | None = None):
        super().__init__(message)
        self.status, self.message, self.code, self.param = status, message, code, param

    def response(self) -> JSONResponse:
        kind = "invalid_request_error" if self.status < 500 else "server_error"
        body = {"message": self.message, "type": kind, "param": self.param, "code": self.code}
        return JSONResponse({"error": body}, status_code=self.status)


def invalid(message: str, param: str) -> GatewayError:
    return GatewayError(400, message, "invalid_request", param)


def blocked(verdict: Verdict) -> GatewayError:
    return GatewayError(
        400,
        f"Rejected by the safety system of this image service: {STAGE_TEXT[verdict.stage]} "
        "violates the content policy.",
        "content_policy_violation",
    )


class Metrics:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "image_gateway_requests",
            "Image requests by endpoint and outcome",
            ["endpoint", "outcome"],
            registry=self.registry,
        )
        self.blocked = Counter(
            "image_gateway_blocked",
            "Blocked requests by stage and category",
            ["stage", "category"],
            registry=self.registry,
        )
        self.seconds = Histogram(
            "image_gateway_stage_seconds",
            "Time per stage",
            ["stage"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
            registry=self.registry,
        )


def create_app(
    settings: Settings | None = None,
    *,
    prompt_guard: PromptGuard | None = None,
    image_guard: ImageGuard | None = None,
    watermarker: Watermarker | None = None,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or Settings()
    if prompt_guard is None or image_guard is None or watermarker is None:
        from image_gateway import models  # torch, transformers, trustmark: container only

        prompt_guard = prompt_guard or models.QwenPromptGuard(
            settings.prompt_guard_model, settings.device
        )
        image_guard = image_guard or models.ShieldGemmaImageGuard(
            settings.image_guard_model, settings.device
        )
        watermarker = watermarker or models.TrustMarkWatermarker(
            settings.watermark_payload,
            settings.watermark_model,
            settings.watermark_strength,
            settings.device,
        )

    upstream = httpx.AsyncClient(
        base_url=settings.upstream_url,
        timeout=httpx.Timeout(settings.upstream_timeout, connect=10.0),
        transport=upstream_transport,
    )
    metrics = Metrics()
    gpu = threading.Lock()  # one GPU, three models: one call at a time

    async def on_gpu(stage: str, fn, *args):
        def run():
            with gpu:
                return fn(*args)

        start = time.perf_counter()
        try:
            return await run_in_threadpool(run)
        except Exception as exc:
            log.exception("checker failed at stage %s", stage)
            raise GatewayError(
                503,
                "The safety check of this image service is unavailable; try again later.",
                "safety_check_unavailable",
            ) from exc
        finally:
            metrics.seconds.labels(stage).observe(time.perf_counter() - start)

    def record_block(verdict: Verdict, endpoint: str, request_id: str) -> GatewayError:
        metrics.blocked.labels(verdict.stage, verdict.category or "").inc()
        log.warning(
            "blocked request=%s endpoint=%s stage=%s category=%s detail=%s",
            request_id,
            endpoint,
            verdict.stage,
            verdict.category,
            verdict.detail,
        )
        return blocked(verdict)

    async def check_prompt(text: str, endpoint: str, request_id: str) -> None:
        if not settings.check_prompts:
            return
        result = await on_gpu("prompt_check", prompt_guard.check, text)
        verdict = judge_prompt(result, settings.block_unsafe, settings.block_controversial)
        if verdict.blocked:
            raise record_block(verdict, endpoint, request_id)

    async def check_image(image: Image.Image, stage: str, endpoint: str, request_id: str) -> None:
        if not settings.check_images:
            return
        scores = await on_gpu(f"{stage}_check", image_guard.scores, image)
        verdict = judge_image(scores, settings.image_thresholds, stage)
        if verdict.blocked:
            raise record_block(verdict, endpoint, request_id)

    def require_prompt(prompt: Any) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise invalid("prompt is required.", "prompt")
        if len(prompt) > settings.max_prompt_chars:
            raise invalid(
                f"prompt is longer than {settings.max_prompt_chars} characters.", "prompt"
            )
        return prompt

    def check_size(size: Any, allow_auto: bool) -> str | None:
        if size in (None, ""):
            return None
        if size == "auto":
            return None if allow_auto else settings.default_size
        parsed = images.parse_size(str(size))
        if parsed is None or max(parsed) > settings.max_side:
            raise invalid(
                f"size must be WIDTHxHEIGHT with both sides at most {settings.max_side}", "size"
            )
        return str(size)

    async def finish(
        response: httpx.Response, fmt: str, compression: int, endpoint: str, request_id: str
    ) -> Response:
        if response.status_code != 200:
            return Response(
                response.content,
                status_code=response.status_code,
                media_type=response.headers.get("content-type", "application/json"),
            )
        payload = response.json()
        items = payload.get("data") or []
        if not items:
            raise GatewayError(502, "The image model returned no image.", "upstream_empty")
        for item in items:
            if not item.get("b64_json"):
                raise GatewayError(502, "The image model returned no image data.", "upstream_empty")
            try:
                image = images.decode_b64(item["b64_json"])
            except images.BadImage as exc:
                raise GatewayError(
                    502, "The image model returned a broken image.", "upstream_bad"
                ) from exc
            await check_image(image, "output", endpoint, request_id)
            if settings.watermark:
                image = await on_gpu("watermark", watermarker.apply, image)
            item["b64_json"] = images.encode_b64(image, fmt, compression)
        payload["output_format"] = fmt
        metrics.requests.labels(endpoint, "ok").inc()
        return JSONResponse(payload)

    def fill_defaults(params: dict) -> None:
        if params.get("num_inference_steps") in (None, "") and settings.default_steps is not None:
            params["num_inference_steps"] = settings.default_steps
        if params.get("guidance_scale") in (None, "") and settings.default_guidance is not None:
            params["guidance_scale"] = settings.default_guidance
        params["response_format"] = "b64_json"
        params["output_format"] = "png"  # lossless for the watermark; re-encoded afterwards
        params["stream"] = False

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await upstream.aclose()

    app = FastAPI(
        title="image-gateway", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )

    @app.exception_handler(GatewayError)
    async def gateway_error(_request: Request, exc: GatewayError):
        return exc.response()

    @app.middleware("http")
    async def count_failures(request: Request, call_next):
        response = await call_next(request)
        endpoint = {"/v1/images/generations": "generations", "/v1/images/edits": "edits"}.get(
            request.url.path
        )
        if endpoint and response.status_code != 200:
            outcome = "rejected" if response.status_code == 400 else str(response.status_code)
            metrics.requests.labels(endpoint, outcome).inc()
        return response

    @app.get("/health")
    async def health():
        try:
            r = await upstream.get("/health", timeout=5.0)
        except httpx.HTTPError:
            return JSONResponse({"status": "upstream unreachable"}, status_code=503)
        status = 200 if r.status_code == 200 else 503
        return JSONResponse({"status": "ok" if status == 200 else "upstream unhealthy"}, status)

    @app.get("/gateway/metrics")
    async def prometheus():
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/models")
    async def list_models():
        r = await upstream.get("/v1/models")
        return Response(r.content, r.status_code, media_type=r.headers.get("content-type"))

    @app.post("/v1/images/generations")
    async def generations(request: Request):
        request_id = uuid.uuid4().hex[:12]
        try:
            body = await request.json()
        except ValueError as exc:
            raise invalid("The request body must be JSON.", "body") from exc
        if not isinstance(body, dict):
            raise invalid("The request body must be a JSON object.", "body")
        prompt = require_prompt(body.get("prompt"))
        body["size"] = check_size(body.get("size"), allow_auto=False) or settings.default_size
        fmt = str(body.get("output_format") or "png").lower()
        compression = int(body.get("output_compression") or 100)
        try:
            body["n"] = max(1, min(int(body.get("n") or 1), settings.max_images))
        except (TypeError, ValueError) as exc:
            raise invalid("n must be a number.", "n") from exc

        await check_prompt(prompt, "generations", request_id)
        fill_defaults(body)
        start = time.perf_counter()
        r = await upstream.post("/v1/images/generations", json=body)
        metrics.seconds.labels("generate").observe(time.perf_counter() - start)
        return await finish(r, fmt, compression, "generations", request_id)

    @app.post("/v1/images/edits")
    async def edits(request: Request):
        request_id = uuid.uuid4().hex[:12]
        form = await request.form()
        fields: list[tuple[str, str]] = []
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        uploads: list[Image.Image] = []
        for key, value in form.multi_items():
            if key in URL_FIELDS:
                raise invalid("Remote image URLs are not accepted; upload the image.", "url")
            if isinstance(value, str):
                fields.append((key, value))
                continue
            data = await value.read()
            if key in IMAGE_FIELDS:
                try:
                    uploads.append(images.decode_bytes(data))
                except images.BadImage as exc:
                    raise invalid("An uploaded image could not be read.", key) from exc
            files.append((key, (value.filename or "image.png", data, value.content_type or "")))
        if not uploads:
            raise invalid("An image to edit is required (field image or image[]).", "image")

        params = dict(fields)
        prompt = require_prompt(params.get("prompt"))
        size = check_size(params.get("size"), allow_auto=True)
        params["size"] = size or "auto"
        fmt = str(params.get("output_format") or "png").lower()
        compression = int(params.get("output_compression") or 100)

        await check_prompt(prompt, "edits", request_id)
        for image in uploads:
            await check_image(image, "input", "edits", request_id)
        fill_defaults(params)
        data = {
            key: str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in params.items()
        }
        start = time.perf_counter()
        r = await upstream.post("/v1/images/edits", data=data, files=files)
        metrics.seconds.labels("generate").observe(time.perf_counter() - start)
        return await finish(r, fmt, compression, "edits", request_id)

    @app.api_route(
        "/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    )
    async def closed(path: str):
        body = {"message": f"/{path} is not available on this image service.", "code": 404}
        return JSONResponse({"error": body}, status_code=404)

    return app
