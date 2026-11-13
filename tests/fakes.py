"""Stand-ins for the GPU models and for vLLM-Omni.

An image counts as "explicit" for FakeImageGuard when its top-left pixel is pure red, so a test
picks the checker's answer by the colour the fake upstream paints. The fake watermark writes a
marker pixel at (1, 1).
"""

import base64
import io
import json

import httpx
from PIL import Image

from image_gateway.policy import PromptResult

RED = (255, 0, 0)
GREY = (128, 128, 128)
MARK = (1, 2, 3)


def png_bytes(color=GREY, size=(64, 48), mode="RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def png_b64(color=GREY, size=(64, 48)) -> str:
    return base64.b64encode(png_bytes(color, size)).decode()


class FakePromptGuard:
    """Returns the result of the first key that appears in the prompt (lower-cased)."""

    def __init__(self, rules: dict[str, PromptResult] | None = None, fail: bool = False):
        self.rules = rules or {}
        self.fail = fail
        self.calls: list[str] = []

    def check(self, text: str) -> PromptResult:
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("guard down")
        for key, result in self.rules.items():
            if key in text.lower():
                return result
        return PromptResult("Safe", ["None"])


class FakeImageGuard:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def scores(self, image: Image.Image) -> dict[str, float]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("guard down")
        explicit = image.convert("RGB").getpixel((0, 0)) == RED
        return {"sexual": 0.97 if explicit else 0.01, "dangerous": 0.01, "violence": 0.02}


class FakeWatermarker:
    def __init__(self):
        self.calls = 0

    def apply(self, image: Image.Image) -> Image.Image:
        self.calls += 1
        marked = image.convert("RGB").copy()
        marked.putpixel((1, 1), MARK)
        return marked


class FakeUpstream:
    """vLLM-Omni: answers image requests with images of `color`, records every request."""

    def __init__(self, color=GREY, status: int = 200, error_body: dict | None = None):
        self.color = color
        self.status = status
        self.error_body = error_body or {"error": {"message": "upstream says no", "code": 400}}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/health":
            return httpx.Response(200)
        if path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [{"id": "flux"}]})
        if path in ("/v1/images/generations", "/v1/images/edits"):
            if self.status != 200:
                return httpx.Response(self.status, json=self.error_body)
            n = 1
            if path.endswith("generations"):
                n = json.loads(request.content).get("n", 1)
            data = [{"b64_json": png_b64(self.color)} for _ in range(n)]
            return httpx.Response(200, json={"created": 1, "data": data, "output_format": "png"})
        return httpx.Response(404, json={"error": "not found upstream"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def last_json(self) -> dict:
        return json.loads(self.requests[-1].content)
