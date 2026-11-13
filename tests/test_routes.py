async def test_models_are_passed_through(client):
    r = await client.get("/v1/models")
    assert r.status_code == 200 and r.json()["data"][0]["id"] == "flux"


async def test_other_upstream_routes_are_closed(client, upstream):
    # vLLM-Omni can also make images through chat completions; that would skip every check.
    for method, path in [
        ("POST", "/v1/chat/completions"),
        ("POST", "/v1/completions"),
        ("POST", "/v1/videos"),
        ("POST", "/v1/images/variations"),
        ("GET", "/v1/files"),
    ]:
        r = await client.request(method, path, json={})
        assert r.status_code == 404, path
    assert upstream.requests == []


async def test_health_reflects_upstream(client):
    r = await client.get("/health")
    assert r.status_code == 200


async def test_metrics_count_blocks(client, prompt_guard):
    from image_gateway.policy import PromptResult

    prompt_guard.rules = {"nude": PromptResult("Unsafe", ["Sexual Content or Sexual Acts"])}
    await client.post("/v1/images/generations", json={"prompt": "nude"})
    await client.post("/v1/images/generations", json={"prompt": "a fox"})
    text = (await client.get("/gateway/metrics")).text
    assert (
        'image_gateway_blocked_total{category="Sexual Content or Sexual Acts",stage="prompt"}'
        in (text)
    )
    assert 'image_gateway_requests_total{endpoint="generations",outcome="ok"} 1.0' in text
