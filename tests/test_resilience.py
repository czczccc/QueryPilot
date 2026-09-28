import httpx
import pytest

from app.services import resilience
from app.services.resilience import CircuitBreaker, RetryTransport


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fast(_):
        return None
    monkeypatch.setattr(resilience.asyncio, "sleep", fast)


def _client(seq, retries=2):
    calls = []

    def handler(request):
        calls.append(request.method)
        item = seq[min(len(calls) - 1, len(seq) - 1)]
        if isinstance(item, Exception):
            raise item
        return httpx.Response(item)

    transport = RetryTransport(httpx.MockTransport(handler), retries=retries)
    return httpx.AsyncClient(transport=transport), calls


async def test_retries_429_then_ok():
    client, calls = _client([429, 503, 200])
    resp = await client.get("https://x.test/")
    assert resp.status_code == 200 and len(calls) == 3


async def test_gives_up_after_limit():
    client, calls = _client([503])
    resp = await client.get("https://x.test/")
    assert resp.status_code == 503 and len(calls) == 3


async def test_post_read_timeout_not_retried():
    client, calls = _client([httpx.ReadTimeout("t")])
    with pytest.raises(httpx.ReadTimeout):
        await client.post("https://x.test/")
    assert calls == ["POST"]


async def test_connect_error_retried_for_post():
    client, calls = _client([httpx.ConnectError("c"), 200])
    resp = await client.post("https://x.test/")
    assert resp.status_code == 200 and len(calls) == 2


async def test_404_not_retried():
    client, calls = _client([404])
    assert (await client.get("https://x.test/")).status_code == 404 and len(calls) == 1


def test_breaker_opens_and_recovers(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(resilience.time, "monotonic", lambda: now[0])
    b = CircuitBreaker(threshold=3, cooldown=60)
    assert not b.record("pansou", False)
    assert not b.record("pansou", False)
    assert b.record("pansou", False)
    assert not b.allow("pansou") and b.allow("bing")
    now[0] = 61
    assert b.allow("pansou")
    b.record("pansou", True)
    assert b.allow("pansou")
