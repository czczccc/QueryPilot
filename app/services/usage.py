"""用量与额度：防止 LLM token 被刷。

- 每个「身份」每天的用量记在 `usage_daily` 表（搜索次数、用了 LLM 的搜索次数、LLM 调用次数、token）；
  身份：登录用户 `user:…`、匿名按 IP `ip:…`、全站合计 `*`、后台任务 `system`；
- 每次 agent 搜索前 `QuotaGuard.check()`：
  1. 同一 IP 当天搜索总数超过上限 → 直接拒绝（防脚本狂刷，规则模式也要占搜索源）；
  2. 全站当天 token 到预算上限 → 全站降级为规则模式；
  3. 这个身份当天的 AI 搜索次数用完 → 这个人降级为规则模式（不拒绝，照样能搜）；
- 搜索结束 `record()` 记账；命中缓存没调 LLM 的不算 AI 次数。

表结构只用标准 SQL（`ON CONFLICT … DO UPDATE` 在 SQLite 与 PostgreSQL 都能用），换库时只改连接。
"""

import asyncio
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.services.llm import Meter

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_daily (
    day          TEXT NOT NULL,
    subject      TEXT NOT NULL,
    searches     INTEGER NOT NULL DEFAULT 0,
    llm_searches INTEGER NOT NULL DEFAULT 0,
    llm_calls    INTEGER NOT NULL DEFAULT 0,
    tokens       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, subject)
);
"""

SITE = "*"
SYSTEM = "system"
FIELDS = ("searches", "llm_searches", "llm_calls", "tokens")


def today() -> str:
    return time.strftime("%Y-%m-%d")


class UsageStore:
    """用量表。`path=":memory:"` 用于测试；和链接库可以是同一个 SQLite 文件。"""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _add(self, day: str, rows: list[tuple[str, dict[str, int]]]) -> None:
        with self._lock:
            for subject, inc in rows:
                vals = [int(inc.get(f, 0)) for f in FIELDS]
                self._conn.execute(
                    "INSERT INTO usage_daily (day, subject, searches, llm_searches, llm_calls, tokens)"
                    " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (day, subject) DO UPDATE SET"
                    " searches = usage_daily.searches + excluded.searches,"
                    " llm_searches = usage_daily.llm_searches + excluded.llm_searches,"
                    " llm_calls = usage_daily.llm_calls + excluded.llm_calls,"
                    " tokens = usage_daily.tokens + excluded.tokens",
                    (day, subject, *vals),
                )
            self._conn.commit()

    def _get(self, day: str, subject: str) -> dict[str, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT searches, llm_searches, llm_calls, tokens FROM usage_daily"
                " WHERE day = ? AND subject = ?", (day, subject),
            ).fetchone()
        return {f: (row[f] if row else 0) for f in FIELDS}

    async def add(self, day: str, rows: list[tuple[str, dict[str, int]]]) -> None:
        await asyncio.to_thread(self._add, day, rows)

    async def get(self, day: str, subject: str) -> dict[str, int]:
        return await asyncio.to_thread(self._get, day, subject)


@dataclass(frozen=True)
class QuotaConfig:
    """0 表示不限制。"""

    anon_daily_ai: int = 3  # 未登录（按 IP）每天 AI 搜索次数
    user_daily_ai: int = 30  # 登录用户每天 AI 搜索次数
    site_daily_tokens: int = 1_000_000  # 全站每天 token 预算
    ip_daily_searches: int = 100  # 同一 IP 每天搜索总次数（含规则模式）


@dataclass
class Decision:
    ai: bool  # 本次是否允许用 LLM
    reason: str | None  # None / user_quota / site_budget
    used: int  # 这个身份今天已用的 AI 搜索次数
    limit: int  # 0 = 不限
    logged_in: bool
    blocked: bool = False  # IP 当天搜索次数超限：拒绝

    def message(self) -> str | None:
        if self.blocked:
            return "今天的搜索次数太多了，请明天再来"
        if self.reason == "site_budget":
            return "今日全站 AI 额度已用完，已自动切换为基础搜索"
        if self.reason == "user_quota":
            tip = "" if self.logged_in else "；扫码登录后每天次数更多"
            return f"今日 AI 搜索次数已用完（{self.limit} 次），已自动切换为基础搜索{tip}"
        return None

    def to_dict(self) -> dict:
        return {
            "ai": self.ai,
            "reason": self.reason,
            "message": self.message(),
            "used": self.used,
            "limit": self.limit,
            "remaining": max(0, self.limit - self.used) if self.limit else None,
            "logged_in": self.logged_in,
        }


class QuotaGuard:
    def __init__(self, store: UsageStore, config: QuotaConfig | None = None) -> None:
        self.store = store
        self.config = config or QuotaConfig()

    async def check(self, subject: str, ip_subject: str, logged_in: bool) -> Decision:
        cfg, day = self.config, today()
        me = await self.store.get(day, subject)
        limit = cfg.user_daily_ai if logged_in else cfg.anon_daily_ai
        decision = Decision(True, None, me["llm_searches"], limit, logged_in)
        ip = me if ip_subject == subject else await self.store.get(day, ip_subject)
        if cfg.ip_daily_searches and ip["searches"] >= cfg.ip_daily_searches:
            decision.ai, decision.blocked = False, True
            return decision
        site = await self.store.get(day, SITE)
        if cfg.site_daily_tokens and site["tokens"] >= cfg.site_daily_tokens:
            decision.ai, decision.reason = False, "site_budget"
        elif limit and me["llm_searches"] >= limit:
            decision.ai, decision.reason = False, "user_quota"
        return decision

    async def record(
        self, subject: str, ip_subject: str, meter: Meter, searched: bool = True,
        decision: Decision | None = None,
    ) -> None:
        """记账；传入 decision 时顺便把 used 更新成记账后的值（用于响应里的剩余次数）。"""
        used_ai = 1 if searched and meter.calls else 0
        mine = {"searches": int(searched), "llm_searches": used_ai,
                "llm_calls": meter.calls, "tokens": meter.tokens}
        rows = [(subject, mine), (SITE, mine)]
        if searched and ip_subject != subject:
            rows.append((ip_subject, {"searches": 1}))
        await self.store.add(today(), rows)
        if decision is not None:
            decision.used += used_ai
