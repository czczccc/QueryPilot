"""对话式追问：在上一轮结果上继续筛选或补搜。

用户说「要第二季」「只要中字的」「4K 的呢」「再找找」时，不重新从零开始，
而是把追问解释成对上一轮条件的修改（季数 / 清晰度 / 字幕 / HDR / 要更多），
上一轮已验证的链接作为候选直接复用；够了就只做筛选，不够再补搜。

解释优先用 LLM（有 key 时），失败或无 key 用规则。会话只存在进程内存里，1 小时过期。
"""

import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

import httpx

from app.models import DoubanMeta, ParsedResource, QuarkLink
from app.services.quality import required_resolution
from app.services.relevance import RelevanceTarget, seasons_in

logger = logging.getLogger(__name__)

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
SESSION_TTL = 3600.0
MAX_SESSIONS = 500

SUB_RE = re.compile(r"中字|字幕|中文|简中|繁中|双语")
HDR_RE = re.compile(r"hdr|杜比|dolby|dv(?![a-z])", re.IGNORECASE)
MORE_RE = re.compile(r"更多|换一批|再找|再搜|其他的|还有吗|还有没有|不够")
RES_LABEL = {"2160p": "4K", "1080p": "1080P", "720p": "720P"}


@dataclass
class Conversation:
    id: str
    parsed: ParsedResource
    target: RelevanceTarget
    required: str | None
    need_subtitle: bool = False
    need_hdr: bool = False
    links: list[QuarkLink] = field(default_factory=list)
    verified: set[str] = field(default_factory=set)
    history: list[str] = field(default_factory=list)
    douban: DoubanMeta | None = None
    updated: float = field(default_factory=time.time)


@dataclass
class Refinement:
    """对追问的解释。mode=new 表示这是一个新的搜索，不沿用上一轮。"""

    mode: str = "refine"
    season: int | None = None
    resolution: str | None = None
    subtitle: bool = False
    hdr: bool = False
    more: bool = False
    by: str = "rules"

    def describe(self) -> dict:
        return {
            "mode": self.mode,
            "season": self.season,
            "resolution": self.resolution,
            "subtitle": self.subtitle,
            "hdr": self.hdr,
            "more": self.more,
            "by": self.by,
        }


class ConversationStore:
    """进程内会话表（LRU + 过期）。重启后会话丢失，前端会自然退回为新搜索。"""

    def __init__(self, ttl: float = SESSION_TTL, max_size: int = MAX_SESSIONS) -> None:
        self._items: OrderedDict[str, Conversation] = OrderedDict()
        self._ttl = ttl
        self._max = max_size

    def get(self, session_id: str | None) -> Conversation | None:
        if not session_id:
            return None
        conv = self._items.get(session_id)
        if conv is None or time.time() - conv.updated > self._ttl:
            self._items.pop(session_id, None)
            return None
        self._items.move_to_end(session_id)
        return conv

    def put(self, conv: Conversation) -> None:
        conv.updated = time.time()
        self._items[conv.id] = conv
        self._items.move_to_end(conv.id)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex


def interpret_rules(text: str) -> Refinement:
    seasons = seasons_in(text)
    ref = Refinement(
        season=min(seasons) if len(seasons) == 1 else None,
        resolution=required_resolution(text),
        subtitle=bool(SUB_RE.search(text)),
        hdr=bool(HDR_RE.search(text)),
        more=bool(MORE_RE.search(text)),
    )
    if not any([ref.season, ref.resolution, ref.subtitle, ref.hdr, ref.more]):
        ref.mode = "new"  # 看不出是在修改条件：当作新的搜索
    return ref


LLM_PROMPT = (
    "用户刚搜过一部影视资源，现在追问了一句。判断这句话是在修改上一轮的条件，还是要搜别的作品。"
    "只输出 JSON：{\"mode\":\"refine\"或\"new\",\"season\":季数或null,"
    "\"resolution\":\"2160p\"/\"1080p\"/\"720p\"或null,\"subtitle\":是否要求中文字幕,"
    "\"hdr\":是否要求HDR,\"more\":是否要更多结果}"
)


async def interpret_llm(
    text: str, conv: Conversation, api_key: str, client: httpx.AsyncClient
) -> Refinement | None:
    payload = {
        "previous": {
            "resource": conv.parsed.resource,
            "season": conv.target.season,
            "resolution": conv.required,
            "history": conv.history[-3:],
        },
        "followup": text,
    }
    try:
        resp = await client.post(
            DEEPSEEK_URL,
            json={
                "model": "deepseek-v4-flash",
                "messages": [
                    {"role": "system", "content": LLM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = json.loads(resp.json()["choices"][0]["message"]["content"])
        season = data.get("season")
        resolution = data.get("resolution")
        return Refinement(
            mode="new" if data.get("mode") == "new" else "refine",
            season=season if isinstance(season, int) and 0 < season < 50 else None,
            resolution=resolution if resolution in RES_LABEL else None,
            subtitle=data.get("subtitle") is True,
            hdr=data.get("hdr") is True,
            more=data.get("more") is True,
            by="llm",
        )
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        logger.warning("LLM 追问解释失败（%s），使用规则", type(exc).__name__)
        return None


def refined_parsed(conv: Conversation, ref: Refinement) -> ParsedResource:
    """按追问更新搜索条件，生成新的查询词（带季数 / 清晰度 / 中字）。"""
    p = conv.parsed
    season = ref.season or conv.target.season
    resolution = ref.resolution or conv.required
    tokens = []
    if season:
        tokens.append(f"第{season}季")
    if resolution:
        tokens.append(RES_LABEL.get(resolution, ""))
    if ref.subtitle or conv.need_subtitle:
        tokens.append("中字")
    suffix = " ".join(t for t in tokens if t)
    queries = [f"{p.resource} {suffix} 夸克网盘".strip(), f"{p.resource} {suffix}".strip()]
    if p.english_name:
        eng = f"{p.english_name} S{season:02d}" if season else p.english_name
        queries.append(f"{eng} {RES_LABEL.get(resolution or '', '')} 夸克".strip())
    queries.append(f"{p.resource} {suffix} 全集 夸克".strip())
    return p.model_copy(update={
        "quality": RES_LABEL.get(resolution or "") or p.quality,
        "search_suggestions": list(dict.fromkeys(queries))[:6],
    })
