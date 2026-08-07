"""DeepSeek 意图解析与规则降级。

模型输出先经 `_coerce` 归一化再通过 `SearchIntent.model_validate` 校验；
任何超时、HTTP 错误、无效 JSON、无 Key 或字段越界都进入规则降级，
保证无外部依赖时仍能生成有效的搜索计划。
"""

import json
import logging
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import ValidationError

from app.models import ResourceType, SearchIntent

logger = logging.getLogger(__name__)

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
RESOURCE_TYPES: set[str] = {"game", "movie", "music", "software", "other"}

SYSTEM_PROMPT = (
    "你是搜索意图解析助手。把用户的自然语言需求解析成 JSON，只输出 JSON 本身。"
    '格式：{"resource_type":"game|movie|music|software|other",'
    '"keywords":["核心关键词",...最多8个],'
    '"constraints":["约束条件",...最多8个],'
    '"query_variants":["完整搜索查询词1","查询词2","查询词3"]}'
    "query_variants 必须生成 3 个不同且完整的搜索查询词，组合使用关键词、"
    "同义词、中英文，确保覆盖用户可能的搜索习惯。"
)

# 资源类型词典：用于规则降级与类型猜测
_TYPE_HINTS: dict[ResourceType, tuple[str, ...]] = {
    "game": ("游戏", "steam", "联机", "手游", "pc", "主机", "playstation", "xbox", "switch"),
    "movie": ("电影", "电视剧", "剧集", "影", "动画", "纪录片", "片", "番"),
    "music": ("音乐", "歌曲", "专辑", "歌手", "乐队", "歌单", "歌词"),
    "software": ("软件", "工具", "app", "应用", "插件", "编辑器", "下载"),
    "other": (),
}


class IntentError(Exception):
    """意图解析失败，应使用规则降级。"""


@dataclass
class ParseOutcome:
    intent: SearchIntent
    fallback_used: bool


class IntentParser(Protocol):
    async def parse(self, query: str) -> ParseOutcome:
        """返回解析出的意图与是否使用了降级。"""


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
    """将 DeepSeek 的任意 JSON 归一化为合法意图；无法归一化时抛 IntentError。"""
    rt = str(data.get("resource_type", "")).lower()
    if rt not in RESOURCE_TYPES:
        rt = "other"
    keywords = _clean_list(data.get("keywords"), 8)
    variants = _clean_list(data.get("query_variants"), 3)
    constraints = _clean_list(data.get("constraints"), 8)
    if not keywords or not variants:
        raise IntentError("DeepSeek 输出缺少关键词或查询变体")
    return {
        "resource_type": rt,
        "keywords": keywords,
        "constraints": constraints,
        "query_variants": variants,
    }


def guess_resource_type(query: str) -> ResourceType:
    q = query.lower()
    for rt, hints in _TYPE_HINTS.items():
        if any(h in q for h in hints):
            return rt
    return "other"


def rule_based_intent(query: str) -> SearchIntent:
    """规则降级：保留原始查询，基于资源类型词典生成最多 3 个变体。"""
    q = query.strip()
    rt = guess_resource_type(q)
    suffixes = {
        "game": ("推荐", "联机"),
        "movie": ("影评", "正版观看"),
        "music": ("专辑", "歌词"),
        "software": ("官网", "教程"),
        "other": ("资料", "介绍"),
    }[rt]
    variants = [q]
    for suffix in suffixes:
        variant = f"{q} {suffix}".strip()
        if variant not in variants:
            variants.append(variant)
    return SearchIntent(
        resource_type=rt,
        keywords=[q[:100]],
        constraints=[],
        query_variants=variants[:3],
    )


class DeepSeekParser:
    """调用 DeepSeek Chat Completions 的意图解析器；失败时规则降级。"""

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
            return ParseOutcome(intent=rule_based_intent(query), fallback_used=True)
        payload = {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            if self._client is not None:
                resp = await self._client.post(DEEPSEEK_URL, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(DEEPSEEK_URL, json=payload, headers=headers)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            intent = SearchIntent.model_validate(_coerce(json.loads(content)))
            return ParseOutcome(intent=intent, fallback_used=False)
        except (httpx.HTTPError, ValidationError, IntentError, KeyError, IndexError,
                json.JSONDecodeError) as exc:
            logger.warning("DeepSeek 意图解析失败（%s），使用规则降级", type(exc).__name__)
            return ParseOutcome(intent=rule_based_intent(query), fallback_used=True)
