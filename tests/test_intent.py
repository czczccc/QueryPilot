"""DeepSeek 影视资源解析与规则降级测试：全部使用假 HTTP 客户端，不调用真实 API。"""

import json

import httpx
import pytest

from app.models import ParsedResource
from app.services.intent import (
    DeepSeekParser,
    IntentError,
    _coerce,
    clean_keyword,
    rule_based_parsed,
)


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok_handler(content: str):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return handler


def _json_result(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


VALID = {
    "resource": "漫长的季节",
    "quality": "4K HDR",
    "preference": "夸克网盘",
    "aliases": [],
    "english_name": "The Long Season",
    "search_suggestions": [
        "漫长的季节 4K 夸克网盘",
        "漫长的季节 夸克 分享",
        "The Long Season 夸克网盘",
        "漫长的季节 全集 网盘",
        "漫长的季节 4K HDR 夸克",
        "漫长的季节 云盘 资源",
    ],
}


# ---------------- 正常解析 ----------------

async def test_parses_valid_json():
    parser = DeepSeekParser(api_key="sk-test", client=_mock_client(_ok_handler(_json_result(VALID))))
    outcome = await parser.parse("漫长的季节 4K")
    assert outcome.fallback_used is False
    assert outcome.parsed.resource == "漫长的季节"
    assert len(outcome.parsed.search_suggestions) == 6


# ---------------- 降级路径 ----------------

async def test_invalid_json_falls_back():
    parser = DeepSeekParser(api_key="sk-test", client=_mock_client(_ok_handler("不是 JSON")))
    outcome = await parser.parse("任意查询")
    assert outcome.fallback_used is True
    assert outcome.parsed.resource


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
    parser = DeepSeekParser(api_key="", client=_mock_client(_ok_handler(_json_result(VALID))))
    outcome = await parser.parse("任意查询")
    assert outcome.fallback_used is True
    assert 1 <= len(outcome.parsed.search_suggestions) <= 6


async def test_missing_resource_falls_back():
    bad = dict(VALID)
    bad["resource"] = ""
    parser = DeepSeekParser(api_key="sk-test", client=_mock_client(_ok_handler(_json_result(bad))))
    outcome = await parser.parse("任意查询")
    assert outcome.fallback_used is True


# ---------------- _coerce 归一化 ----------------

def test_coerce_truncates_suggestions():
    data = dict(VALID)
    data["search_suggestions"] = [f"查询{i}" for i in range(20)]
    coerced = _coerce(data)
    assert len(coerced["search_suggestions"]) == 6


def test_coerce_raises_when_suggestions_missing():
    data = dict(VALID)
    data["search_suggestions"] = []
    with pytest.raises(IntentError):
        _coerce(data)


# ---------------- 规则降级 ----------------

def test_clean_keyword_removes_stop_words():
    assert "漫长的季节" in clean_keyword("漫长的季节 4K 夸克网盘 全集")


def test_rule_based_parsed_shape():
    parsed = rule_based_parsed("绝命律师 4K 夸克网盘")
    assert isinstance(parsed, ParsedResource)
    assert parsed.resource
    assert 1 <= len(parsed.search_suggestions) <= 6
    assert all(s.strip() for s in parsed.search_suggestions)
