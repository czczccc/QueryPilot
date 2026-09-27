"""额外搜索源：Telegram 频道网页预览解析、资源站模板、接入编排。"""

import socket

import httpx
import pytest

from app.models import SearchRequest
from app.services.search import QuarkSearchService
from app.services.sources import (
    parse_telegram_page,
    render_site_url,
    search_sites,
    search_telegram,
)
from tests.test_agent import FakeParser, ScriptedTavily, quark_client


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )


TG_PAGE = """
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message_text js-message_text" dir="auto">
    名称：流浪地球2 4K 杜比视界<br/>描述：...<br/>
    链接：<a href="https://pan.quark.cn/s/abcd123456" target="_blank">https://pan.quark.cn/s/abcd123456</a>
    提取码：x7y8
  </div>
  <time datetime="2026-09-01T10:00:00+00:00" class="time">10:00</time>
</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message_text js-message_text" dir="auto">没有链接的闲聊</div>
</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message_text js-message_text" dir="auto">
    流浪地球2 &amp; 花絮<br/>https://pan.quark.cn/s/abcd123456 重复链接
    https://pan.quark.cn/s/efgh987654
  </div>
</div>
"""


def test_parse_telegram_page():
    links = parse_telegram_page(TG_PAGE, "quarkshare")
    assert [lk.share for lk in links] == ["abcd123456", "efgh987654"]
    first = links[0]
    assert first.name == "流浪地球2 4K 杜比视界"
    assert first.pwd == "x7y8"
    assert first.time == "2026-09-01"
    assert first.source == "Telegram @quarkshare"
    assert links[1].name == "流浪地球2 & 花絮"


async def test_search_telegram_skips_bad_channels_and_errors():
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path.endswith("/broken"):
            return httpx.Response(500)
        assert request.url.params["q"] == "流浪地球2"
        return httpx.Response(200, text=TG_PAGE)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    links = await search_telegram("流浪地球2", ["quarkshare", "broken", "../evil"], client)
    assert sorted(requested) == ["/s/broken", "/s/quarkshare"]
    assert len(links) == 2


def test_render_site_url():
    assert render_site_url("https://x.com/s?q={q}", "流浪 地球") == (
        "https://x.com/s?q=%E6%B5%81%E6%B5%AA%20%E5%9C%B0%E7%90%83"
    )
    assert render_site_url("https://x.com/s", "a") is None


async def test_search_sites_extracts_links():
    page = "<li><b>流浪地球2 4K</b> <a href='https://pan.quark.cn/s/site123456'>下载</a></li>"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    links = await search_sites("流浪地球2", ["https://res.example.com/search?kw={q}", "bad"], client)
    assert [lk.share for lk in links] == ["site123456"]
    assert links[0].name == "流浪地球2 4K"
    assert links[0].source == "资源站 res.example.com"


async def test_search_sites_blocks_private_hosts(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("10.0.0.5", 0))]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("不应请求内网地址")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await search_sites("x", ["http://intranet.local/s?q={q}"], client) == []


async def test_service_includes_new_providers():
    async def tg_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=TG_PAGE)

    svc = QuarkSearchService(
        parser=FakeParser(), tavily=ScriptedTavily({}), use_qkyunso=False, use_bing=False,
        client=quark_client(), tg_channels=["quarkshare"],
        tg_client=httpx.AsyncClient(transport=httpx.MockTransport(tg_handler)),
    )
    resp = await svc.search(SearchRequest(query="流浪地球2"))
    names = {p.name: p for p in resp.providers}
    assert names["telegram"].result_count == 2
    assert {lk.share for lk in resp.links} == {"abcd123456", "efgh987654"}
    assert all(lk.state == "valid" for lk in resp.links)
