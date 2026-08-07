"""豆瓣链接识别测试：URL 检测 + 移动版页面解析（MockTransport，不访问真实豆瓣）。"""

import httpx

from app.services.douban import (
    extract_douban_id,
    fetch_douban_meta,
    parse_douban_html,
)

DOUBAN_HTML = """
<html><head>
<meta property="og:title" content="漫长的季节 (2023) - 电视剧" />
</head><body></body></html>
"""


# ---------------- URL 检测 ----------------

def test_extract_douban_id_variants():
    assert extract_douban_id("https://movie.douban.com/subject/35320175/") == "35320175"
    assert extract_douban_id("https://m.douban.com/movie/subject/35320175/") == "35320175"
    assert extract_douban_id("https://www.douban.com/subject/35320175/") == "35320175"
    assert extract_douban_id("看看这个 https://movie.douban.com/subject/35320175/?from=search") == "35320175"


def test_extract_douban_id_rejects_non_douban():
    assert extract_douban_id("https://example.com/subject/35320175/") is None
    assert extract_douban_id("漫长的季节") is None
    assert extract_douban_id("") is None


# ---------------- HTML 解析 ----------------

def test_parse_douban_html_full():
    meta = parse_douban_html(DOUBAN_HTML)
    assert meta["title"] == "漫长的季节"
    assert meta["year"] == "2023"
    assert meta["kind"] == "电视剧"


def test_parse_douban_html_without_year():
    html = '<meta property="og:title" content="难以忽视的真相 - 电影" />'
    meta = parse_douban_html(html)
    assert meta["title"] == "难以忽视的真相"
    assert meta["year"] is None
    assert meta["kind"] == "电影"


def test_parse_douban_html_rejects_challenge_page():
    assert parse_douban_html("<html><p>载入中...</p></html>") == {}


# ---------------- 抓取（MockTransport） ----------------

async def test_fetch_douban_meta_success():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "m.douban.com"
        assert "iPhone" in request.headers.get("User-Agent", "")
        return httpx.Response(200, text=DOUBAN_HTML)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    meta = await fetch_douban_meta("35320175", client, timeout=5)
    assert meta is not None
    assert meta["title"] == "漫长的季节"
    assert meta["year"] == "2023"


async def test_fetch_douban_meta_404_returns_none():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await fetch_douban_meta("99999999", client, timeout=5) is None


async def test_fetch_douban_meta_network_error_returns_none():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await fetch_douban_meta("35320175", client, timeout=5) is None
