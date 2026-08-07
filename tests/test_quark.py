"""夸克链接提取、置信度与云搜解析的单元测试（纯函数 + MockTransport）。"""

import base64

import httpx

from app.services.quark import (
    QUARK_RE,
    confidence_from_months,
    decode_bing_url,
    extract_links_with_pwd,
    find_pwd,
    make_entry,
    parse_qkyunso_detail,
    parse_qkyunso_search,
    search_bing,
    verify_quark,
)

# ---------------- 正则提取 ----------------

def test_quark_re_matches_domain_variants():
    for url in (
        "https://pan.quark.cn/s/abc1234567",
        "https://share.quark.cn/s/AbCd1234567890Xy",
        "https://drive.quark.cn/s/abc1234567",
        "https://quark.cn/s/abc1234567",
    ):
        assert QUARK_RE.search(url).group(1)


def test_extract_links_with_pwd():
    text = "资源：https://pan.quark.cn/s/abc1234567 提取码：1234"
    assert extract_links_with_pwd(text) == [("abc1234567", "1234")]


def test_extract_links_dedupes():
    text = "https://pan.quark.cn/s/abc1234567 https://pan.quark.cn/s/abc1234567"
    assert len(extract_links_with_pwd(text)) == 1


def test_find_pwd_variants():
    assert find_pwd("提取码：8888") == "8888"
    assert find_pwd("提取码 8888") == "8888"
    assert find_pwd("访问码: abcd") == "abcd"
    assert find_pwd("pwd=z9x8") == "z9x8"
    assert find_pwd("无提取码") is None


# ---------------- 置信度 ----------------

def test_make_entry_confidence():
    import datetime

    today = datetime.datetime.now(datetime.UTC).date()
    recent = (today - datetime.timedelta(days=10)).isoformat()
    old = (today - datetime.timedelta(days=400)).isoformat()
    assert make_entry("标题", "sid123456789", "src", recent).conf == "高"
    assert make_entry("标题", "sid123456789", "src", old).conf == "低"
    assert make_entry("标题", "sid123456789", "src", "未知").conf == "中"


def test_confidence_from_months():
    assert confidence_from_months(1) == "高"
    assert confidence_from_months(3) == "中"
    assert confidence_from_months(12) == "低"
    assert confidence_from_months(None) == "中"


# ---------------- Bing 中文引擎 ----------------

def test_decode_bing_url():
    # bing 跳转链接：u=a1 后的 base64url 编码的目标 URL
    target = "https://pan.quark.cn/s/abc1234567"
    encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    href = f"https://cn.bing.com/ck/a?u=a1{encoded}&ntb=1"
    assert decode_bing_url(href) == target


def test_decode_bing_url_returns_none_when_no_match():
    assert decode_bing_url("https://example.com/direct") is None


async def test_search_bing_parses_html():
    html = (
        '<li class="b_algo"><h2><a href="https://cn.bing.com/ck/a?u=a1">标题</a></h2>'
        'https://pan.quark.cn/s/xyz9876543 提取码 abcd 3天前</li>'
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert "cn.bing.com" in request.url.host
        return httpx.Response(200, text=html)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await search_bing("绝命律师", client, timeout=5)
    assert len(out) == 1
    assert out[0].share == "xyz9876543"
    assert out[0].pwd == "abcd"


async def test_search_bing_extracts_link_from_title_text():
    """新版 Bing 把夸克链接直接写在 h2 标题文本里，必须从 h2 起始处提取。"""
    html = (
        '<h2><a href="https://tieba.baidu.com/p/123">漫长的季节电视剧 夸克网盘链接：'
        "https://pan.quark.cn/s/1aac85bf446d</a></h2>"
        '<div class="b_caption">摘要</div>'
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await search_bing("漫长的季节", client, timeout=5)
    assert len(out) >= 1
    assert out[0].share == "1aac85bf446d"


async def test_search_bing_http_error_returns_empty():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await search_bing("绝命律师", client, timeout=5) == []


async def test_search_bing_dedupes_across_queries():
    html = (
        '<h2><a href="https://example.com/x">标题</a></h2>'
        "https://pan.quark.cn/s/same1234567 4天前"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await search_bing("绝命律师", client, timeout=5)
    # 4 个查询词但同一链接只保留一次
    assert len(out) == 1

def test_parse_qkyunso_search():
    html = (
        '<div data-resource-id="123" data-resource-name="测试资源A">'
        "<span>3个月前</span></div>"
        '<div data-resource-id="456" data-resource-name="测试资源B"></div>'
    )
    items = parse_qkyunso_search(html)
    assert len(items) == 2
    assert items[0]["name"] == "测试资源A"
    assert items[0]["time"] == "3个月前"
    assert items[1]["name"] == "测试资源B"


def test_parse_qkyunso_detail():
    html = "https://pan.quark.cn/s/abc1234567 提取码：abcd 5个月前"
    sid, months = parse_qkyunso_detail(html)
    assert sid == "abc1234567"
    assert months == 5


# ---------------- 验证（MockTransport） ----------------

def _verify_ok_handler():
    """token 返回 stoken，detail 返回带文件列表的分享。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sharepage/token"):
            assert request.url.host == "pan.quark.cn"
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "testtoken"}})
        if request.url.path.endswith("/sharepage/detail"):
            return httpx.Response(200, json={
                "code": 0,
                "data": {"share": {"status": 1}, "list": [{"file_name": "电影.mkv"}]},
            })
        return httpx.Response(404)

    return handler


async def test_verify_quark_valid():
    client = httpx.AsyncClient(transport=httpx.MockTransport(_verify_ok_handler()))
    code, state = await verify_quark("abc1234567", client, timeout=5)
    assert code == 200
    assert state == "valid"


async def test_verify_quark_missing_share_invalid():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"status": 404, "code": 41006, "message": "分享不存在"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    code, state = await verify_quark("abc1234567", client, timeout=5)
    assert code == 404
    assert state == "invalid"


async def test_verify_quark_empty_detail_invalid():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sharepage/token"):
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "t"}})
        return httpx.Response(200, json={"code": 0, "data": {"share": {"status": 3}, "list": []}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    _, state = await verify_quark("abc1234567", client, timeout=5)
    assert state == "invalid"


async def test_verify_quark_network_error_unknown():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    code, state = await verify_quark("abc1234567", client, timeout=5)
    assert code is None
    assert state == "unknown"
