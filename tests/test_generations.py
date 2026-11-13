import base64
import io

from PIL import Image

from image_gateway.policy import PromptResult

from fakes import MARK, RED, FakeUpstream

URL = "/v1/images/generations"


def image_from(response_json, index=0) -> Image.Image:
    raw = base64.b64decode(response_json["data"][index]["b64_json"])
    return Image.open(io.BytesIO(raw))


async def test_benign_prompt_gets_defaults_check_and_watermark(
    client, upstream, prompt_guard, image_guard, watermarker
):
    r = await client.post(URL, json={"prompt": "Ein Igel im Herbstlaub", "size": "auto"})
    assert r.status_code == 200, r.text
    sent = upstream.last_json()
    assert sent["size"] == "1024x1024"
    assert sent["num_inference_steps"] == 4
    assert sent["guidance_scale"] == 1.0
    assert sent["response_format"] == "b64_json"
    assert sent["output_format"] == "png"
    assert prompt_guard.calls == ["Ein Igel im Herbstlaub"]
    assert image_guard.calls == 1 and watermarker.calls == 1
    assert image_from(r.json()).convert("RGB").getpixel((1, 1)) == MARK


async def test_client_values_win_over_defaults(client, upstream):
    body = {"prompt": "a fox", "size": "1536x1024", "num_inference_steps": 8, "guidance_scale": 2}
    r = await client.post(URL, json=body)
    assert r.status_code == 200
    sent = upstream.last_json()
    assert (sent["size"], sent["num_inference_steps"], sent["guidance_scale"]) == (
        "1536x1024",
        8,
        2,
    )


async def test_missing_size_gets_the_default(client, upstream):
    await client.post(URL, json={"prompt": "a fox"})
    assert upstream.last_json()["size"] == "1024x1024"


async def test_oversized_or_malformed_size_is_rejected_before_generation(client, upstream):
    for size in ("4096x4096", "big"):
        r = await client.post(URL, json={"prompt": "a fox", "size": size})
        assert r.status_code == 400 and r.json()["error"]["param"] == "size"
    assert upstream.requests == []


async def test_missing_prompt_is_rejected(client, upstream):
    r = await client.post(URL, json={"size": "1024x1024"})
    assert r.status_code == 400 and r.json()["error"]["param"] == "prompt"
    assert upstream.requests == []


async def test_overlong_prompt_is_refused_not_truncated(client, upstream, prompt_guard):
    r = await client.post(URL, json={"prompt": "a" * 8001})
    assert r.status_code == 400 and r.json()["error"]["param"] == "prompt"
    assert prompt_guard.calls == [] and upstream.requests == []


async def test_invalid_json_is_rejected(client):
    r = await client.post(URL, content=b"{nope", headers={"content-type": "application/json"})
    assert r.status_code == 400


async def test_blocked_prompt_never_reaches_the_model(client, upstream, prompt_guard):
    prompt_guard.rules = {"nude": PromptResult("Unsafe", ["Sexual Content or Sexual Acts"])}
    r = await client.post(URL, json={"prompt": "A nude woman on a beach"})
    assert r.status_code == 400
    error = r.json()["error"]
    assert error["code"] == "content_policy_violation"
    assert "image description" in error["message"]
    assert upstream.requests == []


async def test_flagged_output_is_withheld(settings, prompt_guard, image_guard, watermarker):
    import httpx

    from image_gateway.app import create_app

    red = FakeUpstream(color=RED)
    app = create_app(
        settings,
        prompt_guard=prompt_guard,
        image_guard=image_guard,
        watermarker=watermarker,
        upstream_transport=red.transport,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://g") as c:
        r = await c.post(URL, json={"prompt": "a harmless looking request"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "content_policy_violation"
    assert "b64_json" not in r.text
    assert watermarker.calls == 0


async def test_upstream_errors_pass_through(settings, prompt_guard, image_guard, watermarker):
    import httpx

    from image_gateway.app import create_app

    failing = FakeUpstream(status=400, error_body={"error": {"message": "bad size"}})
    app = create_app(
        settings,
        prompt_guard=prompt_guard,
        image_guard=image_guard,
        watermarker=watermarker,
        upstream_transport=failing.transport,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://g") as c:
        r = await c.post(URL, json={"prompt": "a fox"})
    assert r.status_code == 400 and r.json() == {"error": {"message": "bad size"}}


async def test_requested_jpeg_is_returned_as_jpeg(client, upstream):
    r = await client.post(URL, json={"prompt": "a fox", "output_format": "jpeg"})
    assert r.status_code == 200
    assert upstream.last_json()["output_format"] == "png"
    assert image_from(r.json()).format == "JPEG"
    assert r.json()["output_format"] == "jpeg"


async def test_n_is_capped_and_every_image_checked_and_marked(client, upstream, image_guard):
    r = await client.post(URL, json={"prompt": "a fox", "n": 10})
    assert r.status_code == 200
    assert upstream.last_json()["n"] == 4
    assert len(r.json()["data"]) == 4 and image_guard.calls == 4


async def test_streaming_is_turned_off(client, upstream):
    await client.post(URL, json={"prompt": "a fox", "stream": True})
    assert upstream.last_json()["stream"] is False


async def test_a_broken_prompt_checker_fails_closed(client, upstream, prompt_guard):
    prompt_guard.fail = True
    r = await client.post(URL, json={"prompt": "a fox"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "safety_check_unavailable"
    assert upstream.requests == []


async def test_a_broken_image_checker_fails_closed(client, image_guard, watermarker):
    image_guard.fail = True
    r = await client.post(URL, json={"prompt": "a fox"})
    assert r.status_code == 503 and "b64_json" not in r.text
    assert watermarker.calls == 0
