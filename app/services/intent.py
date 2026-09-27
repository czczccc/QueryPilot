"""DeepSeek 影视资源解析与规则降级。

模型输出先经 `_coerce` 归一化再通过 `ParsedResource.model_validate` 校验；
任何超时、HTTP 错误、无效 JSON、无 Key 或字段越界都进入规则降级，
保证无外部依赖时仍能生成有效的搜索计划。
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import ValidationError

from app.models import ParsedResource
from app.services import llm

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "你是影视资源网盘搜索助手。把用户输入解析成 JSON，只输出 JSON 本身。"
    "格式：{\"resource\":\"资源名（必填）\","
    "\"quality\":\"清晰度要求如 4K HDR / 1080p，没有则 null\","
    "\"preference\":\"网盘偏好如 夸克网盘，没有则 null\","
    "\"aliases\":[\"常见别名（如 绝命律师/风骚律师）\",...],"
    "\"english_name\":\"英文名如 The Long Season，没有则 null\","
    "\"search_suggestions\":[\"完整搜索查询词1\",\"查询词2\",\"查询词3\",\"查询词4\",\"查询词5\",\"查询词6\"]}"
    "search_suggestions 必须生成 5~6 个不同的完整查询词，"
    "组合使用资源名、别名、英文名、清晰度、'夸克网盘/网盘/云盘/资源/全集' 等词，"
    "确保覆盖用户可能的搜索习惯。"
)

# 免费模式关键词清洗（迁移自桌面原型）
STOP_WORDS = [
    "夸克网盘", "百度网盘", "阿里云盘", "夸克", "网盘", "百度", "阿里",
    "迅雷", "4k", "hdr", "1080p", "1080", "2160p", "2160", "高清",
    "超清", "无删减", "全集", "全季", "资源", "分享", "下载",
    "链接", "字幕", "中英", "完整版", "合集", "美剧", "韩剧", "日剧",
    "国剧", "电视剧", "电影",
]


class IntentError(Exception):
    """意图解析失败，应使用规则降级。"""


@dataclass
class ParseOutcome:
    parsed: ParsedResource
    fallback_used: bool


class IntentParser(Protocol):
    async def parse(self, query: str) -> ParseOutcome:
        """返回解析出的资源信息与是否使用了降级。"""


def _clean_list(items: object, limit: int) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _coerce(data: dict) -> dict:
    """将 DeepSeek 的任意 JSON 归一化为合法解析结果；无法归一化时抛 IntentError。"""
    resource = str(data.get("resource", "")).strip()
    suggestions = _clean_list(data.get("search_suggestions"), 6)
    if not resource or not suggestions:
        raise IntentError("DeepSeek 输出缺少资源名或搜索建议")
    return {
        "resource": resource,
        "quality": data.get("quality") or None,
        "preference": data.get("preference") or None,
        "aliases": _clean_list(data.get("aliases"), 8),
        "english_name": data.get("english_name") or None,
        "search_suggestions": suggestions,
    }


def clean_keyword(raw: str) -> str:
    kw = raw.strip()
    for w in STOP_WORDS:
        kw = re.sub(re.escape(w), " ", kw, flags=re.IGNORECASE)
    kw = re.sub(r"\s+", " ", kw).strip()
    return kw or raw.strip()


def rule_based_parsed(query: str) -> ParsedResource:
    """规则降级：清洗关键词并生成基础查询词。"""
    resource = clean_keyword(query)
    qs = [resource + " 夸克网盘", resource + " 夸克 分享", resource + " 网盘 全集",
          resource + " 云盘 资源"]
    if len(qs) > 6:
        qs = qs[:6]
    return ParsedResource(
        resource=resource[:100] or query.strip(),
        quality=None,
        preference=None,
        aliases=[],
        english_name=None,
        search_suggestions=qs[:6],
    )


class DeepSeekParser:
    """调用 DeepSeek Chat Completions 的资源解析器；失败时规则降级。"""

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 8.0,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout = timeout

    async def parse(self, query: str) -> ParseOutcome:
        if not self._api_key:
            logger.info("未配置 DEEPSEEK_API_KEY，使用规则降级")
            return ParseOutcome(parsed=rule_based_parsed(query), fallback_used=True)
        payload = {
            "model": llm.MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        try:
            if self._client is not None:
                message = await llm.chat(self._client, payload, self._api_key, self._timeout)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    message = await llm.chat(client, payload, self._api_key, self._timeout)
            content = message["content"]
            parsed = ParsedResource.model_validate(_coerce(json.loads(content)))
            return ParseOutcome(parsed=parsed, fallback_used=False)
        except (httpx.HTTPError, ValidationError, IntentError, KeyError, IndexError,
                json.JSONDecodeError) as exc:
            logger.warning("LLM 资源解析失败（%s），使用规则降级", type(exc).__name__)
            return ParseOutcome(parsed=rule_based_parsed(query), fallback_used=True)
