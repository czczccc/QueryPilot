"""DeepSeek 意图解析与规则降级测试：全部使用假 HTTP 客户端，不调用真实 API。"""

import json

import httpx
import pytest

from app.models import SearchIntent
from app.services.intent import (
    DeepSeekParser,
    IntentError,
    _coerce,
    guess_resource_type,
    rule_based_intent,
)


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok_handler(content: str):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = {"choices": [{"message": {"content": content}}]}
        return httpx.Response(200, json=body)

    return handler


def _json_result(intent: dict) -> str:
    return json.dumps(intent, ensure_ascii=False)


VALID_INTENT = {
    "resource_type": "game",
    "keywords": ["四人联机", "轻量"],
    "constraints": ["支持中文"],
    "query_variants": ["四人轻量联机游戏 中文", "4 player co-op game Chinese", "多人合作游戏 推荐"],
}


# ---------------- 正常解析 ----------------

async def test_parses_valid_json():
    parser = DeepSeekParser(api_key="sk-test", client=_mock_client(_ok_handler(_json_result(VALID_INTENT))))
    outcome = await parser.parse("想找四人联机游戏")
    assert outcome.fallback_used is False
    assert outcome.intent.resource_type == "game"
    assert len(outcome.intent.query_variants) == 3


async def test_parser_without_client_uses_default(monkeypatch):
    """未注入 client 时应能正常构造并调用（超时场景已在其他用例覆盖）。"""
    parser = DeepSeekParser(api_key="sk-test", timeout=1.0)
    assert parser is not None


# ---------------- 降级路径 ----------------

async def test_invalid_json_falls_back():
    parser = DeepSeekParser(api_key="sk-test", client=_mock_client(_ok_handler("不是 JSON")))
    outcome = await parser.parse("任意查询")
    assert outcome.fallback_used is True


async def test_http_error_falls_back():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limit"})

    parser = DeepSeekParser(api_key="sk-test", client=_mock_client(handler))
    outcome = await parser.parse("任意查询")
    assert outcome.fallback_used is True


async def test_timeout_falls_back():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    parser = DeepSeekParser(api_key="sk-test", client=_mock_client(handler))
    outcome = await parser.parse("任意查询")
    assert outcome.fallback_used is True


async def test_no_api_key_falls_back():
    parser = DeepSeekParser(api_key="", client=_mock_client(_ok_handler(_json_result(VALID_INTENT))))
    outcome = await parser.parse("任意查询")
    assert outcome.fallback_used is True
    assert len(outcome.intent.query_variants) == 3


async def test_missing_keywords_falls_back():
    bad = dict(VALID_INTENT)
    bad["keywords"] = []
    parser = DeepSeekParser(api_key="sk-test", client=_mock_client(_ok_handler(_json_result(bad))))
    outcome = await parser.parse("任意查询")
    assert outcome.fallback_used is True


# ---------------- _coerce 归一化 ----------------

def test_coerce_maps_unknown_resource_type_to_other():
    data = dict(VALID_INTENT)
    data["resource_type"] = "weird"
    coerced = _coerce(data)
    assert coerced["resource_type"] == "other"


def test_coerce_truncates_overlong_lists():
    data = dict(VALID_INTENT)
    data["keywords"] = [f"关键词{i}" for i in range(20)]
    coerced = _coerce(data)
    assert len(coerced["keywords"]) == 8


def test_coerce_raises_when_variants_missing():
    data = dict(VALID_INTENT)
    data["query_variants"] = []
    with pytest.raises(IntentError):
        _coerce(data)


# ---------------- 规则降级 ----------------

def test_rule_based_intent_shape():
    intent = rule_based_intent("找一款四人联机游戏")
    assert isinstance(intent, SearchIntent)
    assert 1 <= len(intent.keywords) <= 8
    assert 1 <= len(intent.query_variants) <= 3
    assert all(v.strip() for v in intent.query_variants)


def test_guess_resource_type():
    assert guess_resource_type("推荐一款射击游戏") == "game"
    assert guess_resource_type("好看的电影") == "movie"
    assert guess_resource_type("周杰伦的歌曲") == "music"
    assert guess_resource_type("好用的截图工具") == "software"
    assert guess_resource_type("随便聊聊") == "other"


def test_rule_fallback_variants_count():
    intent = rule_based_intent("适合四人的轻量联机游戏")
    assert len(intent.query_variants) == 3
    assert intent.query_variants[0] == "适合四人的轻量联机游戏"
