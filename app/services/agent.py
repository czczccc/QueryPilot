"""资源搜索 agent：LLM 通过 tool-calling 决定下一步，代码负责执行与兜底。

分工：
- 确定性的事交给代码：搜索、验证、质量识别、记忆、目标判断、预算控制；
- LLM（DeepSeek，OpenAI 兼容 function calling）只决定下一步做什么：
  用哪些查询词、要不要换别名/英文名再搜、什么时候结束。

没有 DEEPSEEK_API_KEY 或 LLM 调用失败时，用 `RulePlanner` 按固定策略走完，
保证 agent 在任何情况下都能给出结果。
"""

import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Protocol

import httpx

from app.models import (
    AgentSearchResponse,
    AgentStep,
    DoubanMeta,
    ParsedResource,
    ProviderStatus,
    QuarkLink,
    SearchMetrics,
    SearchRequest,
    UserPrefs,
)
from app.services import llm
from app.services.conversation import (
    Conversation,
    ConversationStore,
    Refinement,
    interpret_llm,
    interpret_rules,
    refined_parsed,
)
from app.services.memory import resource_key
from app.services.quality import meets_requirement, required_resolution
from app.services.relevance import (
    RelevanceTarget,
    _key,
    add_aliases,
    build_target,
    clean_keyword,
    judge,
    llm_judge,
)
from app.services.search import (
    FRESH_HOURS,
    QuarkSearchService,
    SearchUnavailableError,
    is_fresh,
    sort_links,
)

logger = logging.getLogger(__name__)


TARGET_MATCHES = 5  # 满足要求的有效链接达到该数量即结束
MAX_STEPS = 8  # 工具调用步数上限
MAX_SECONDS = 90.0  # 总耗时预算（超出后不再开始新步骤）
MAX_VERIFY_PER_CALL = 30
MAX_VERIFY_TOTAL = 90
MAX_QUERIES_PER_SEARCH = 4

Emit = Callable[[AgentStep], Awaitable[None]]

# LLM 规划可能出现的错误：网络/HTTP、坏 JSON、缺字段、类型不对
PLANNER_ERRORS = (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError)


@dataclass
class Action:
    tool: str
    args: dict = field(default_factory=dict)
    thought: str | None = None


# ---------------- 工具定义（OpenAI function calling 格式） ----------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": "查询记忆库中该资源之前验证过的有效链接。通常第一步调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "用查询词全网搜索夸克网盘分享链接，结果加入候选池（未验证）。"
                "queries 给通用搜索引擎；keyword 给网盘垂直搜索站，应是简短的资源名"
                "（可换成别名或英文名）。不要重复已经用过的查询词。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": f"1~{MAX_QUERIES_PER_SEARCH} 个完整查询词",
                    },
                    "keyword": {"type": "string", "description": "简短资源名"},
                },
                "required": ["queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify",
            "description": (
                "验证候选池中尚未验证的链接是否有效，并识别分辨率等质量信息。"
                "搜索之后必须验证，结果才可用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": f"本次最多验证多少条（1~{MAX_VERIFY_PER_CALL}）",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "结束搜索。已有足够满足要求的有效链接，或继续搜索不太可能改善时调用。",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string", "description": "一句话原因"}},
                "required": ["reason"],
            },
        },
    },
]

PANSOU_TOOL = {
    "type": "function",
    "function": {
        "name": "pansou_search",
        "description": (
            "用 PanSou 网盘聚合搜索（几十个网盘搜索站一起搜，只要夸克链接），结果加入候选池（未验证）。"
            "search 已经会用它的 keyword 顺带查 PanSou；想只用 PanSou 换别名、英文名、"
            "指定季再深挖时用这个，不花通用搜索引擎的额度。同一个关键词不要重复搜。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "简短资源名（可用别名、英文名）"},
            },
            "required": ["keyword"],
        },
    },
}


LOOKUP_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_media",
        "description": (
            "查影视条目（TMDB，没有时用豆瓣）：返回中文名、原名、年份、类型、每季集数。"
            "搜不到或结果片名不对时，用它拿到原名 / 英文名换词再搜，并核对年份和季。"
            "查到的名字会自动加进片名判定。同一个名字不要重复查。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "片名（默认用解析出的资源名）"},
                "year": {"type": "string", "description": "年份，可省略"},
            },
        },
    },
}


def tools_for(pansou: bool, lookup: bool = False) -> list[dict]:
    """没配 PanSou / 条目查询时 LLM 看不到对应工具。"""
    tools = [*TOOLS[:2], PANSOU_TOOL, *TOOLS[2:]] if pansou else list(TOOLS)
    return [*tools[:-1], LOOKUP_TOOL, tools[-1]] if lookup else tools


SYSTEM_PROMPT = (
    "你是夸克网盘资源搜索 agent。目标：找到至少 {target} 条【有效】且【满足清晰度要求】"
    "的分享链接。你只能通过工具行动，每次调用一个工具，并在 content 里用一句话说明理由。\n"
    "策略：先 recall_memory；记忆不够就 search，然后 verify；"
    "满足要求的链接不够时，换别名、英文名、加「4K」「1080P」「全集」「夸克网盘」等词再搜，"
    "不要重复用过的查询词；搜不到或片名对不上时可以 lookup_media 查原名、年份和每季集数；"
    "valid_but_wrong_title 多时说明搜到了同名作品或别的季，"
    "查询里应加上年份或季数；达到目标或继续搜索收益很低时 finish。\n"
    "预算：最多 {steps} 步。"
)


# ---------------- 状态 ----------------


@dataclass
class AgentState:
    parsed: ParsedResource
    required: str | None
    refresh: bool
    target: RelevanceTarget
    prefs: UserPrefs = field(default_factory=UserPrefs)
    need_subtitle: bool = False
    need_hdr: bool = False
    candidates: dict[str, QuarkLink] = field(default_factory=dict)
    verified: set[str] = field(default_factory=set)
    skipped_invalid: set[str] = field(default_factory=set)
    used_queries: list[str] = field(default_factory=list)
    used_keywords: list[str] = field(default_factory=list)
    recalled: bool = False
    search_calls: int = 0
    verify_calls: int = 0
    pansou: bool = False  # 配了 PanSou（有 pansou_search 工具）
    pansou_keywords: list[str] = field(default_factory=list)
    lookup: bool = False  # 能查影视条目（有 lookup_media 工具）
    looked_up: list[str] = field(default_factory=list)  # 查过的名字
    raw_count: int = 0
    fresh_after: float = 0.0

    def confirmed(self, link: QuarkLink) -> bool:
        """本次验证过，或是新鲜期内的记忆链接（旧记忆需重新验证才算数）。"""
        return link.share in self.verified or is_fresh(link, self.fresh_after)

    def passes_filters(self, link: QuarkLink) -> bool:
        """追问里提出的硬性条件：要字幕 / 要 HDR。"""
        q = link.quality
        if self.need_subtitle and not (q and q.has_subtitle):
            return False
        return not (self.need_hdr and not (q and q.hdr))

    def matching(self) -> list[QuarkLink]:
        return [
            c for c in self.candidates.values()
            if self.confirmed(c)
            and c.state == "valid"
            and c.relevance == "match"  # 待核对的不算
            and meets_requirement(c.quality, self.required)
            and self.passes_filters(c)
        ]

    def filters(self) -> dict:
        return {
            "season": self.target.season,
            "resolution": self.required,
            "subtitle": self.need_subtitle,
            "hdr": self.need_hdr,
        }

    def unverified(self) -> list[QuarkLink]:
        return [
            c for c in self.candidates.values()
            if c.share not in self.verified
            and c.share not in self.skipped_invalid
            and not is_fresh(c, self.fresh_after)
        ]

    def satisfied(self) -> bool:
        return len(self.matching()) >= TARGET_MATCHES

    def summary(self) -> dict:
        """给 LLM 看的状态摘要（不含具体链接，控制 token）。"""
        valid = [c for c in self.candidates.values() if c.state == "valid"]
        resolutions: dict[str, int] = {}
        for c in valid:
            res = (c.quality.resolution if c.quality else None) or "未识别"
            resolutions[res] = resolutions.get(res, 0) + 1
        mismatched = sum(1 for c in valid if c.relevance == "mismatch")
        return {
            "valid_but_wrong_title": mismatched,
            "candidates": len(self.candidates),
            "unverified": len(self.unverified()),
            "valid": len(valid),
            "matching": len(self.matching()),
            "target": TARGET_MATCHES,
            "valid_by_resolution": resolutions,
            "used_queries": self.used_queries,
            "used_keywords": self.used_keywords,
            **({"pansou_keywords": self.pansou_keywords} if self.pansou else {}),
        }


# ---------------- 规划器 ----------------


class Planner(Protocol):
    name: str

    async def decide(self, state: AgentState, last: tuple[Action, dict] | None) -> Action:
        """根据状态与上一步观察决定下一步动作。"""


class RulePlanner:
    """无 LLM 时的固定策略：记忆 → 主查询搜索 → 验证 → 不够则用别名/英文名补搜 → 结束。

    只看当前状态做决定（无内部状态），LLM 中途失败时可以无缝接管。
    """

    name = "rules"

    async def decide(self, state: AgentState, last: tuple[Action, dict] | None) -> Action:
        if not state.recalled:
            return Action("recall_memory")
        if state.satisfied() and not (state.refresh and state.search_calls == 0):
            return Action("finish", {"reason": "已找到足够满足要求的有效链接"})
        if state.search_calls == 0:
            return Action("search", {
                "queries": state.parsed.search_suggestions,
                "keyword": state.parsed.resource,
            })
        if state.unverified() and state.verify_calls < 4:
            return Action("verify", {"limit": MAX_VERIFY_PER_CALL})
        if not state.satisfied() and state.lookup and not state.looked_up:
            return Action("lookup_media", {"name": state.parsed.resource})
        if not state.satisfied() and state.search_calls == 1:
            alt = alternate_search(state)
            if alt:
                return Action("search", alt)
        return Action("finish", {
            "reason": "已找到足够满足要求的有效链接" if state.satisfied() else "可用的搜索手段已用完",
        })


def _key_in(name: str, names: list[str]) -> bool:
    """查到的名字已被 add_aliases 认可（归一化后在片名列表里）。"""
    return _key(name) in names


def alternate_search(state: AgentState) -> dict | None:
    """补搜：别名、英文名、带清晰度与网盘词的新查询（去掉用过的）。"""
    p = state.parsed
    names = [n for n in [*p.aliases, p.english_name] if n]
    quality = {"2160p": "4K", "1080p": "1080P", "720p": "720P"}.get(state.required or "", "")
    queries = [f"{n} 夸克网盘 {quality}".strip() for n in names]
    queries += [f"{p.resource} {quality or '4K'} 夸克网盘", f"{p.resource} 全集 夸克"]
    fresh = [q for q in dict.fromkeys(queries) if q not in state.used_queries]
    if not fresh:
        return None
    keyword = next((n for n in names if n not in state.used_keywords), p.resource)
    return {"queries": fresh[:MAX_QUERIES_PER_SEARCH], "keyword": keyword}


class DeepSeekPlanner:
    """DeepSeek function calling 规划器。一次回复可能包含多个工具调用，逐个执行。"""

    name = "llm"

    def __init__(
        self, api_key: str, client: httpx.AsyncClient, timeout: float = 20.0,
        model: str | None = None, tools: list[dict] | None = None,
    ) -> None:
        self._tools = tools or TOOLS
        self._api_key = api_key
        self._client = client
        self._timeout = timeout
        self._model = model or llm.MODEL
        self._messages: list[dict] = []
        self._queue: list[tuple[str, Action]] = []  # (tool_call_id, action)
        self._pending_id: str | None = None

    def _start(self, state: AgentState) -> None:
        p = state.parsed
        prompt = SYSTEM_PROMPT.format(target=TARGET_MATCHES, steps=MAX_STEPS)
        if state.pansou:
            prompt += ("\nsearch 会顺带查 PanSou 网盘聚合搜索；另有 pansou_search 只查 PanSou，"
                       "适合换别名、英文名、加季数深挖，结果同样要 verify。")
        self._messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({
                "resource": p.resource,
                "aliases": p.aliases,
                "english_name": p.english_name,
                "required_resolution": state.required,
                "suggested_queries": p.search_suggestions,
                "force_refresh": state.refresh,
            }, ensure_ascii=False)},
        ]

    async def decide(self, state: AgentState, last: tuple[Action, dict] | None) -> Action:
        if not self._messages:
            self._start(state)
        if last is not None and self._pending_id is not None:
            self._messages.append({
                "role": "tool",
                "tool_call_id": self._pending_id,
                "content": json.dumps(
                    {"observation": last[1], "state": state.summary()}, ensure_ascii=False
                ),
            })
            self._pending_id = None
        if not self._queue:
            await self._ask()
        self._pending_id, action = self._queue.pop(0)
        return action

    async def _ask(self) -> None:
        message = await llm.chat(
            self._client,
            {
                "model": self._model,
                "messages": self._messages,
                "tools": self._tools,
                "tool_choice": "required",
                "temperature": 0.2,
            },
            self._api_key,
            self._timeout,
        )
        calls = message.get("tool_calls") or []
        if not calls:
            raise ValueError("LLM 没有返回工具调用")
        self._messages.append({
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": calls,
        })
        thought = (message.get("content") or "").strip()[:200] or None
        for call in calls:
            fn = call["function"]
            args = json.loads(fn.get("arguments") or "{}")
            if not isinstance(args, dict):
                raise TypeError("工具参数不是对象")
            self._queue.append((call["id"], Action(fn["name"], args, thought)))


# ---------------- agent ----------------


class SearchAgent:
    """执行循环：规划器出动作 → 代码执行工具 → 观察 → 再规划，直到结束或预算用完。"""

    def __init__(
        self,
        service: QuarkSearchService,
        api_key: str = "",
        client: httpx.AsyncClient | None = None,
        max_seconds: float = MAX_SECONDS,
        conversations: ConversationStore | None = None,
        cache_minutes: float = 0,
        lookup=None,
    ) -> None:
        """`cache_minutes`：同一句搜索（同样的偏好）在这段时间内直接复用上次结果，不再调 LLM。
        `lookup`：MetadataLookup，用 TMDB / 豆瓣的标准名、原名补充相关性判定的片名。"""
        self._service = service
        self.lookup = lookup
        self._cache_seconds = cache_minutes * 60
        self._cache: OrderedDict[str, tuple[float, AgentSearchResponse, Conversation]] = (
            OrderedDict()
        )
        self._conversations = conversations or ConversationStore()
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._max_seconds = max_seconds

    def _llm_on(self) -> bool:
        """有 key，且本次请求没被降级（额度 / 全站预算）。"""
        return bool(self._api_key) and llm.enabled()

    def _planner(self) -> Planner:
        if self._llm_on():
            return DeepSeekPlanner(self._api_key, self._client,
                                   tools=tools_for(self._service.pansou_enabled,
                                                   self.lookup is not None))
        return RulePlanner()

    async def run(
        self, req: SearchRequest, emit: Emit | None = None, fresh_hours: float = FRESH_HOURS
    ) -> AgentSearchResponse:
        """`fresh_hours`：记忆里多久内验证过的链接可免复验（订阅检查传 0，全部重验）。"""
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        prefs = await self._service.prefs_for(req.client_id)
        steps: list[AgentStep] = []

        # 追问：沿用上一轮会话，把这句话解释成对条件的修改
        conv = self._conversations.get(req.session_id)
        cache_key = None
        if conv is None and not req.refresh and fresh_hours > 0 and self._cache_seconds > 0:
            cache_key = f"{resource_key(req.query)}|{prefs.model_dump_json()}"
            hit = await self._from_cache(cache_key, emit)
            if hit is not None:
                return hit
        ref: Refinement | None = None
        if conv is not None:
            t0 = time.monotonic()
            ref = None
            if self._llm_on():
                ref = await interpret_llm(req.query, conv, self._api_key, self._client)
            ref = ref or interpret_rules(req.query)
            step = AgentStep(
                step=1, tool="interpret_followup", args={"text": req.query},
                observation=ref.describe(), planner="llm" if ref.by == "llm" else "rules",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            steps.append(step)
            if emit:
                await emit(step)
            if ref.mode == "new":
                conv = None

        if conv is not None and ref is not None:
            parsed = refined_parsed(conv, ref)
            fallback_used, douban = False, conv.douban
            state = AgentState(
                parsed=parsed,
                required=ref.resolution or conv.required,
                refresh=ref.more,  # 「再找找」：即使已满足也补搜一轮
                target=replace(conv.target, season=ref.season or conv.target.season),
                prefs=prefs,
                need_subtitle=conv.need_subtitle or ref.subtitle,
                need_hdr=conv.need_hdr or ref.hdr,
                recalled=True,
                fresh_after=time.time() - fresh_hours * 3600,
            )
            # 上一轮验证过的链接直接作为候选（条件变了要重新判定相关性）
            for link in conv.links:
                judge(link, state.target)
                state.candidates[link.share] = link
            state.verified = {s for s in conv.verified if s in state.candidates}
            session_id, history = conv.id, [*conv.history, req.query]
        else:
            parsed, fallback_used, douban = await self._service.prepare(req.query)
            state = AgentState(
                parsed=parsed,
                # 本次明确要求的清晰度优先，其次是用户偏好里的默认最低清晰度
                required=required_resolution(parsed.quality)
                or required_resolution(req.query)
                or prefs.min_resolution,
                refresh=req.refresh,
                target=build_target(parsed, req.query, douban.year if douban else None),
                prefs=prefs,
                fresh_after=time.time() - fresh_hours * 3600,
            )
            session_id, history = ConversationStore.new_id(), [req.query]
            if self.lookup is not None:
                year = str(state.target.year) if state.target.year else None
                add_aliases(state.target, await self.lookup(parsed.resource, year))
        state.pansou = self._service.pansou_enabled
        state.lookup = self.lookup is not None
        providers = self._service.new_providers()
        key = resource_key(parsed.resource)

        planner: Planner = self._planner()
        last: tuple[Action, dict] | None = None
        stop_reason = "达到步数上限"
        # 追问后上一轮结果已够用：只筛选，不再搜索
        filter_only = conv is not None and state.satisfied() and not state.refresh
        if filter_only:
            stop_reason = "在上一轮结果里筛选即可满足"

        while not filter_only and len(steps) < MAX_STEPS:
            if time.monotonic() - started > self._max_seconds:
                stop_reason = "达到时间预算"
                break
            try:
                action = await planner.decide(state, last)
            except PLANNER_ERRORS as exc:  # LLM 不可用/输出异常：规则规划器接管剩余步骤
                logger.warning("LLM 规划失败（%s），切换规则规划", type(exc).__name__)
                planner = RulePlanner()
                action = await planner.decide(state, last)

            t0 = time.monotonic()
            observation = await self._execute(action, state, providers, key)
            step = AgentStep(
                step=len(steps) + 1,
                tool=action.tool,
                args=action.args,
                observation=observation,
                thought=action.thought,
                planner=planner.name,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            steps.append(step)
            if emit:
                await emit(step)
            last = (action, observation)

            if action.tool == "finish":
                stop_reason = str(action.args.get("reason") or "agent 结束")
                break
            # 代码兜底的停止条件：验证或查记忆后目标已达成（强制刷新时记忆不算数）
            if state.satisfied() and (
                action.tool == "verify"
                or (action.tool == "recall_memory" and not state.refresh)
            ):
                stop_reason = "已找到足够满足要求的有效链接"
                break

        # 规则判不了的标题，有 LLM 时批量交给 LLM 判断是否是同一部作品
        if self._llm_on() and planner.name == "llm":
            uncertain = [
                c for c in state.candidates.values()
                if state.confirmed(c) and c.state == "valid" and c.relevance == "uncertain"
            ]
            if uncertain:
                t0 = time.monotonic()
                judged = await llm_judge(
                    uncertain, parsed, state.target, self._api_key, self._client
                )
                step = AgentStep(
                    step=len(steps) + 1,
                    tool="judge_relevance",
                    args={"count": len(uncertain)},
                    observation={
                        "judged": judged,
                        "match": sum(1 for c in uncertain if c.relevance == "match"),
                        "mismatch": sum(1 for c in uncertain if c.relevance == "mismatch"),
                        "matching_total": len(state.matching()),
                    },
                    planner="llm",
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
                steps.append(step)
                if emit:
                    await emit(step)

        resp = await self._finalize(
            req, request_id, started, state, providers, key, steps, planner.name,
            stop_reason, fallback_used, douban, session_id, history,
            ref.describe() if ref else None,
        )
        if cache_key and resp.matching_count > 0:
            self._cache[cache_key] = (
                time.time(), resp.model_copy(deep=True),
                deepcopy(self._conversations.get(session_id)),
            )
            while len(self._cache) > 500:
                self._cache.popitem(last=False)
        return resp

    async def _from_cache(self, key: str, emit: Emit | None) -> AgentSearchResponse | None:
        """命中缓存：复制一份结果（新的会话 id，追问照常可用），不调用 LLM、不搜索。"""
        item = self._cache.get(key)
        if item is None:
            return None
        ts, cached, conv = item
        age = time.time() - ts
        if age > self._cache_seconds or conv is None:
            self._cache.pop(key, None)
            return None
        session_id = ConversationStore.new_id()
        conv = deepcopy(conv)
        conv.id = session_id
        self._conversations.put(conv)
        step = AgentStep(
            step=1, tool="cache", args={"minutes_ago": int(age // 60)},
            observation={"links": len(cached.links), "matching": cached.matching_count},
        )
        if emit:
            await emit(step)
        return cached.model_copy(deep=True, update={
            "request_id": uuid.uuid4().hex, "session_id": session_id, "steps": [step],
            "planner": "rules",
            "stop_reason": f"{max(1, int(age // 60))} 分钟内搜过同样的内容，直接复用结果",
        })

    async def _execute(
        self, action: Action, state: AgentState, providers: dict[str, ProviderStatus], key: str
    ) -> dict:
        try:
            if action.tool == "recall_memory":
                return await self._recall(state, key)
            if action.tool == "search":
                return await self._search(action.args, state, providers)
            if action.tool == "pansou_search" and state.pansou:
                return await self._pansou(action.args, state, providers)
            if action.tool == "verify":
                return await self._verify(action.args, state)
            if action.tool == "lookup_media" and state.lookup:
                return await self._lookup_media(action.args, state)
            if action.tool == "finish":
                return {"matching": len(state.matching())}
            return {"error": f"未知工具 {action.tool}"}
        except Exception as exc:
            logger.exception("工具执行异常: %s", action.tool)
            return {"error": type(exc).__name__}

    async def _lookup_media(self, args: dict, state: AgentState) -> dict:
        """查条目：把查到的中文名 / 原名加进片名判定和补搜用的别名，已判过的候选重判一遍。"""
        name = str(args.get("name") or state.parsed.resource).strip()[:60]
        year = str(args.get("year") or "").strip()[:4] or (
            str(state.target.year) if state.target.year else None)
        if not name or name in state.looked_up:
            return {"error": "这个名字已经查过"}
        state.looked_up.append(name)
        infos = await self.lookup(name, year)
        before = list(state.target.names)
        add_aliases(state.target, infos)
        known = {state.parsed.resource, *state.parsed.aliases, state.parsed.english_name or ""}
        for info in infos:
            for n in (info.title, info.original_title):
                if n and n not in known and _key_in(n, state.target.names):
                    state.parsed.aliases.append(n)
                    known.add(n)
        if state.target.names != before:  # 多了名字：没认出片名的候选重判
            for c in state.candidates.values():
                if c.relevance == "uncertain" and c.state == "valid":
                    judge(c, state.target)
        return {
            "results": [
                {k: v for k, v in {
                    "title": i.title, "original_title": i.original_title, "year": i.year,
                    "media": i.media, "source": i.source,
                    "episodes": {str(k): v for k, v in i.episodes.items()} or None,
                }.items() if v}
                for i in infos[:5]
            ],
            "new_names": [n for n in state.target.names if n not in before],
            "matching": len(state.matching()),
        }

    async def _recall(self, state: AgentState, key: str) -> dict:
        state.recalled = True
        remembered = await self._service.recall(key)
        for link in remembered:
            judge(link, state.target)
            state.candidates.setdefault(link.share, link)
        fresh = [m for m in remembered if is_fresh(m, state.fresh_after)]
        return {
            "remembered_valid": len(remembered),
            "fresh": len(fresh),
            "matching": len(state.matching()),
        }

    async def _search(
        self, args: dict, state: AgentState, providers: dict[str, ProviderStatus]
    ) -> dict:
        queries = [
            str(q).strip()[:100] for q in args.get("queries") or [] if str(q).strip()
        ][:MAX_QUERIES_PER_SEARCH] or [state.parsed.resource]
        # 网盘搜索站 / PanSou 按整串匹配：只发片名，季和清晰度等结果回来再筛
        keyword = clean_keyword(str(args.get("keyword") or state.parsed.resource))[:50]
        state.search_calls += 1
        state.used_queries += [q for q in queries if q not in state.used_queries]
        new_keyword = keyword not in state.used_keywords
        if new_keyword:
            state.used_keywords.append(keyword)

        # 关键词没变时，按关键词搜的引擎（云搜/Bing/Telegram/资源站）不再重复请求
        # 每次 search 都同时查 PanSou（AI 模式也不靠 LLM 想起来）；这个关键词查过就不重复
        pansou = state.pansou and keyword not in state.pansou_keywords
        if pansou:
            state.pansou_keywords.append(keyword)
        found = await self._service.collect(
            queries, keyword, providers, keyword_engines=new_keyword, pansou=pansou
        )
        return await self._add_found(found, state, providers)

    async def _pansou(
        self, args: dict, state: AgentState, providers: dict[str, ProviderStatus]
    ) -> dict:
        keyword = clean_keyword(str(args.get("keyword") or state.parsed.resource))[:50]
        if keyword in state.pansou_keywords:
            return {"error": "这个关键词已经用 PanSou 搜过了"}
        state.pansou_keywords.append(keyword)
        found = await self._service.collect(
            [], keyword, providers, keyword_engines=False, pansou=True, tavily=False,
        )
        return await self._add_found(found, state, providers)

    async def _add_found(
        self, found: list[QuarkLink], state: AgentState, providers: dict[str, ProviderStatus]
    ) -> dict:
        """搜到的链接进候选池（同一分享码保留先到的），跳过记忆里已知失效的。"""
        state.raw_count += len(found)
        before = len(state.candidates)
        for link in found:
            state.candidates.setdefault(link.share, link)
        new = list(state.candidates.values())[before:]
        state.skipped_invalid |= await self._service.known_invalid(new)
        return {
            "found": len(found),
            "new_candidates": len(new),
            "known_invalid_skipped": sum(1 for c in new if c.share in state.skipped_invalid),
            "engine_errors": [n for n, p in providers.items() if p.status == "error"],
        }

    async def _verify(self, args: dict, state: AgentState) -> dict:
        try:
            limit = int(args.get("limit") or MAX_VERIFY_PER_CALL)
        except (TypeError, ValueError):
            limit = MAX_VERIFY_PER_CALL
        budget = MAX_VERIFY_TOTAL - len(state.verified)
        batch = state.unverified()[: max(0, min(limit, MAX_VERIFY_PER_CALL, budget))]
        state.verify_calls += 1
        await self._service.verify(batch)
        for b in batch:
            judge(b, state.target)
        state.verified |= {b.share for b in batch}
        counts = {"valid": 0, "invalid": 0, "unknown": 0}
        for b in batch:
            counts[b.state] = counts.get(b.state, 0) + 1
        return {
            "verified": len(batch),
            **counts,
            "wrong_title": sum(1 for b in batch if b.relevance == "mismatch"),
            "matching_total": len(state.matching()),
            "remaining_unverified": len(state.unverified()),
            "verify_budget_left": MAX_VERIFY_TOTAL - len(state.verified),
        }

    async def _finalize(
        self,
        req: SearchRequest,
        request_id: str,
        started: float,
        state: AgentState,
        providers: dict[str, ProviderStatus],
        key: str,
        steps: list[AgentStep],
        planner: str,
        stop_reason: str,
        fallback_used: bool,
        douban: DoubanMeta | None,
        session_id: str,
        history: list[str],
        followup: dict | None,
    ) -> AgentSearchResponse:
        # 只返回验证过（或新鲜记忆）的链接；未验证的不展示；追问的硬性条件不满足的不展示
        final = [
            c for c in state.candidates.values()
            if state.confirmed(c) and (c.state != "valid" or state.passes_filters(c))
        ]
        searched = state.search_calls > 0
        if (
            searched
            and not final
            and all(p.status == "error" for p in providers.values())
        ):
            raise SearchUnavailableError()
        if not searched:
            for p in providers.values():
                p.status = "skipped"
        sort_links(final, state.prefs)
        # 满足要求（清晰度 + 不是片名不符）的排在最前
        final.sort(
            key=lambda c: (
                c.state != "valid",
                c.relevance == "mismatch" or not meets_requirement(c.quality, state.required),
            )
        )
        await self._service.remember(key, req.query, final, from_memory=not searched)
        # 保存会话：下一句追问在这轮结果上继续（包括被筛掉的，条件放宽时还能用）
        self._conversations.put(Conversation(
            id=session_id,
            parsed=state.parsed,
            target=state.target,
            required=state.required,
            need_subtitle=state.need_subtitle,
            need_hdr=state.need_hdr,
            links=[c for c in state.candidates.values() if state.confirmed(c)],
            verified={c.share for c in state.candidates.values() if state.confirmed(c)},
            history=history,
            douban=douban,
        ))

        metrics = SearchMetrics(
            duration_ms=int((time.monotonic() - started) * 1000),
            raw_result_count=state.raw_count,
            deduplicated_result_count=len(final),
            fallback_used=fallback_used,
            memory_hits=sum(1 for f in final if f.from_memory),
            skipped_invalid=len(state.skipped_invalid),
            served_from_memory=not searched,
        )
        return AgentSearchResponse(
            request_id=request_id,
            query=req.query,
            parsed=state.parsed,
            links=final,
            providers=list(providers.values()),
            metrics=metrics,
            douban=douban,
            steps=steps,
            planner=planner,  # type: ignore[arg-type]
            stop_reason=stop_reason,
            required_resolution=state.required,
            matching_count=len(state.matching()),
            session_id=session_id,
            history=history,
            followup=followup,
            filters=state.filters(),
        )

