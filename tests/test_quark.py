"""夸克链接提取、置信度与云搜解析的单元测试（纯函数 + MockTransport）。"""

import httpx

from app.services.quark import (
    QUARK_RE,
    confidence_from_months,
    extract_links_with_pwd,
    find_pwd,
    make_entry,
    parse_qkyunso_detail,
    parse_qkyunso_search,
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


# ---------------- 夸克云搜解析 ----------------

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

async def test_verify_quark_returns_status():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "pan.quark.cn"
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await verify_quark("abc1234567", client, timeout=5) == 200


async def test_verify_quark_network_error_returns_none():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await verify_quark("abc1234567", client, timeout=5) is None
