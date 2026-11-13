import httpx
import pytest

from image_gateway.app import create_app
from image_gateway.config import Settings

from fakes import FakeImageGuard, FakePromptGuard, FakeUpstream, FakeWatermarker


@pytest.fixture
def upstream():
    return FakeUpstream()


@pytest.fixture
def prompt_guard():
    return FakePromptGuard()


@pytest.fixture
def image_guard():
    return FakeImageGuard()


@pytest.fixture
def watermarker():
    return FakeWatermarker()


@pytest.fixture
def settings():
    return Settings(upstream_url="http://vllm:8000")


@pytest.fixture
async def client(settings, upstream, prompt_guard, image_guard, watermarker):
    app = create_app(
        settings,
        prompt_guard=prompt_guard,
        image_guard=image_guard,
        watermarker=watermarker,
        upstream_transport=upstream.transport,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as c:
        yield c
