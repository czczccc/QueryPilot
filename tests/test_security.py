"""工程防护测试：限流、SSRF 防护、request_id 日志贯穿。"""

import logging
import socket

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import RawSearchResult
from app.security import RateLimiter, is_safe_fetch_url
from app.services.intent import ParseOutcome, rule_based_parsed
from app.services.search import QuarkSearchService


class FakeParser:
    async def parse(self, query: str) -> ParseOutcome:
        return ParseOutcome(parsed=rule_based_parsed(query), fallback_used=True)


class FakeProvider:
    name = "tavily"

    async def search(self, query: str, limit: int):
        return [RawSearchResult(title="资源页", url="https://example.com/x",
                                snippet="", provider=self.name)]


def _make_client(rate_limit: int | None = None) -> TestClient:
    # 注入 MockTransport：不访问真实网络。TestClient 每个请求用新的事件循环，
    # 真实 httpx 连接池跨请求复用会触发 "Event loop is closed"（CI 有网络时出现）
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = QuarkSearchService(parser=FakeParser(), tavily=FakeProvider(),
                             use_qkyunso=False, use_bing=False, client=client)
    return TestClient(create_app(service=svc, rate_limit_per_minute=rate_limit))


# ---------------- RateLimiter ----------------

def test_rate_limiter_allows_until_limit():
    limiter = RateLimiter(rate=2, window=60)
    assert limiter.allow("ip-1") is True
    assert limiter.allow("ip-1") is True
    assert limiter.allow("ip-1") is False
    # 不同 key 不受影响
    assert limiter.allow("ip-2") is True


def test_rate_limiter_recovers_after_window():
    limiter = RateLimiter(rate=1, window=0.05)
    assert limiter.allow("ip-1") is True
    assert limiter.allow("ip-1") is False
    import time

    time.sleep(0.06)
    assert limiter.allow("ip-1") is True


def test_api_returns_429_when_limited():
    client = _make_client(rate_limit=2)
    for _ in range(2):
        assert client.post("/api/search", json={"query": "测试资源"}).status_code == 200
    assert client.post("/api/search", json={"query": "测试资源"}).status_code == 429


# ---------------- SSRF 防护 ----------------

async def test_safe_url_rejects_private_ips():
    assert await is_safe_fetch_url("http://127.0.0.1:8000/x") is False
    assert await is_safe_fetch_url("http://10.0.0.1/x") is False
    assert await is_safe_fetch_url("http://192.168.1.1/x") is False
    assert await is_safe_fetch_url("http://169.254.169.254/latest/meta-data") is False
    assert await is_safe_fetch_url("http://[::1]/x") is False


async def test_safe_url_rejects_bad_scheme_and_hostless():
    assert await is_safe_fetch_url("ftp://example.com/x") is False
    assert await is_safe_fetch_url("file:///etc/passwd") is False
    assert await is_safe_fetch_url("http:///nohost") is False
    assert await is_safe_fetch_url("") is False


async def test_safe_url_accepts_public_host(monkeypatch):
    def fake_getaddrinfo(host, port):
        assert host == "example.com"
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert await is_safe_fetch_url("https://example.com/page") is True


async def test_safe_url_rejects_dns_pointing_private(monkeypatch):
    def fake_getaddrinfo(host, port):
        return [(2, 1, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert await is_safe_fetch_url("https://internal.example.com/x") is False


async def test_safe_url_rejects_dns_failure(monkeypatch):
    def fake_getaddrinfo(host, port):
        raise OSError("nxdomain")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert await is_safe_fetch_url("https://no-such-host.invalid/x") is False


# ---------------- request_id 日志贯穿 ----------------

def test_response_has_request_id_header():
    client = _make_client()
    resp = client.post("/api/search", json={"query": "测试资源"})
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID")


def test_http_log_contains_request_id(caplog):
    client = _make_client()
    with caplog.at_level(logging.INFO):
        resp = client.post("/api/search", json={"query": "测试资源"})
    assert resp.status_code == 200
    http_logs = [r for r in caplog.records if "http method=" in r.getMessage()]
    assert http_logs, "应存在请求日志"
    rid = resp.headers.get("X-Request-ID")
    assert rid
    assert all(getattr(r, "request_id", None) == rid for r in http_logs)
