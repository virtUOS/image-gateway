import base64
import io

from PIL import Image

from image_gateway.policy import PromptResult

from fakes import MARK, RED, png_bytes

URL = "/v1/images/edits"


def upstream_form(request) -> bytes:
    return request.content


async def test_benign_edit_is_checked_forwarded_and_marked(
    client, upstream, prompt_guard, image_guard, watermarker
):
    files = [("image[]", ("in.png", png_bytes(), "image/png"))]
    data = {"prompt": "Mach den Himmel blau", "size": "auto", "quality": "auto", "model": "flux"}
    r = await client.post(URL, data=data, files=files)
    assert r.status_code == 200, r.text
    assert prompt_guard.calls == ["Mach den Himmel blau"]
    # one input check, one output check
    assert image_guard.calls == 2 and watermarker.calls == 1
    body = upstream_form(upstream.requests[-1])
    assert b'name="image[]"' in body
    assert b'name="num_inference_steps"' in body and b"\r\n\r\n4\r\n" in body
    assert b'name="response_format"' in body and b"b64_json" in body
    raw = base64.b64decode(r.json()["data"][0]["b64_json"])
    assert Image.open(io.BytesIO(raw)).convert("RGB").getpixel((1, 1)) == MARK


async def test_plain_image_field_works_too(client, upstream):
    files = [("image", ("in.png", png_bytes(), "image/png"))]
    r = await client.post(URL, data={"prompt": "warmer colours"}, files=files)
    assert r.status_code == 200
    assert b'name="image"' in upstream.requests[-1].content


async def test_explicit_input_image_is_rejected_before_editing(client, upstream, image_guard):
    files = [("image[]", ("in.png", png_bytes(RED), "image/png"))]
    r = await client.post(URL, data={"prompt": "make the background blue"}, files=files)
    assert r.status_code == 400
    error = r.json()["error"]
    assert error["code"] == "content_policy_violation" and "uploaded image" in error["message"]
    assert upstream.requests == []


async def test_blocked_edit_prompt_never_reaches_the_model(client, upstream, prompt_guard):
    prompt_guard.rules = {"undress": PromptResult("Unsafe", ["Sexual Content or Sexual Acts"])}
    files = [("image[]", ("in.png", png_bytes(), "image/png"))]
    r = await client.post(URL, data={"prompt": "undress the person"}, files=files)
    assert r.status_code == 400 and upstream.requests == []


async def test_remote_image_urls_are_refused(client, upstream):
    r = await client.post(URL, data={"prompt": "edit", "url": "http://169.254.169.254/x.png"})
    assert r.status_code == 400 and r.json()["error"]["param"] == "url"
    assert upstream.requests == []


async def test_an_edit_without_an_image_is_refused(client, upstream):
    r = await client.post(URL, data={"prompt": "edit"}, files=[("other", ("a.txt", b"x"))])
    assert r.status_code == 400 and r.json()["error"]["param"] == "image"
    assert upstream.requests == []


async def test_an_unreadable_upload_is_refused(client, upstream):
    files = [("image[]", ("in.png", b"not an image", "image/png"))]
    r = await client.post(URL, data={"prompt": "edit"}, files=files)
    assert r.status_code == 400 and upstream.requests == []
