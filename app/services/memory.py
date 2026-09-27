"""资源记忆：SQLite 链接库。

记住每条验证过的分享链接（状态、质量、所属资源），实现：
- 同一资源再次搜索时直接复用近期验证有效的链接（足够多时跳过全网搜索）；
- 近期确认失效的链接直接跳过，不再占用验证名额；
- 后台定期复验旧的有效链接，失效后自动下线。

sqlite3 是同步库，所有公开方法都通过 `asyncio.to_thread` 调用，避免阻塞事件循环。
"""

import asyncio
import re
import sqlite3
import threading
import time
from pathlib import Path

from app.models import QualityInfo, QuarkLink

_SCHEMA = """
CREATE TABLE IF NOT EXISTS links (
    share        TEXT PRIMARY KEY,
    pwd          TEXT,
    name         TEXT NOT NULL,
    source       TEXT NOT NULL,
    time         TEXT NOT NULL,
    conf         TEXT NOT NULL,
    state        TEXT NOT NULL,
    http         INTEGER,
    quality      TEXT,
    first_seen   REAL NOT NULL,
    last_checked REAL NOT NULL,
    fail_count   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS link_resources (
    resource_key TEXT NOT NULL,
    share        TEXT NOT NULL,
    PRIMARY KEY (resource_key, share)
);
CREATE TABLE IF NOT EXISTS searches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_key TEXT NOT NULL,
    query        TEXT NOT NULL,
    valid_count  INTEGER NOT NULL,
    from_memory  INTEGER NOT NULL,
    ts           REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_state_checked ON links(state, last_checked);
"""

_PUNCT_RE = re.compile(r"[\s\-_·:：,，.。!！?？'\"“”‘’()（）\[\]【】《》<>]+")

HOUR = 3600.0


def resource_key(name: str) -> str:
    """资源名归一化：小写、去空白与标点，作为记忆的主键。"""
    return _PUNCT_RE.sub("", name).lower()


class LinkStore:
    """SQLite 链接库。`path=":memory:"` 用于测试。"""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------- 同步实现 ----------------

    @staticmethod
    def _row_to_link(row: sqlite3.Row) -> QuarkLink:
        quality = QualityInfo.model_validate_json(row["quality"]) if row["quality"] else None
        return QuarkLink(
            name=row["name"],
            share=row["share"],
            pwd=row["pwd"],
            source=row["source"],
            time=row["time"],
            conf=row["conf"],
            http=row["http"],
            state=row["state"],
            quality=quality,
            from_memory=True,
            last_checked=row["last_checked"],
        )

    def _recall(self, key: str) -> list[QuarkLink]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT l.* FROM links l JOIN link_resources r ON r.share = l.share "
                "WHERE r.resource_key = ? AND l.state = 'valid' "
                "ORDER BY l.last_checked DESC",
                (key,),
            ).fetchall()
        return [self._row_to_link(r) for r in rows]

    def _known_invalid(self, shares: list[str], since: float) -> set[str]:
        if not shares:
            return set()
        out: set[str] = set()
        with self._lock:
            for i in range(0, len(shares), 500):
                chunk = shares[i : i + 500]
                marks = ",".join("?" * len(chunk))
                rows = self._conn.execute(
                    f"SELECT share FROM links WHERE state = 'invalid' "
                    f"AND last_checked >= ? AND share IN ({marks})",
                    (since, *chunk),
                ).fetchall()
                out.update(r["share"] for r in rows)
        return out

    def _save(self, key: str, links: list[QuarkLink], now: float) -> None:
        with self._lock:
            for link in links:
                if link.state == "unknown":
                    continue  # 无法判定的不入库，避免污染
                quality = link.quality.model_dump_json() if link.quality else None
                self._conn.execute(
                    """
                    INSERT INTO links (share, pwd, name, source, time, conf, state, http,
                                       quality, first_seen, last_checked, fail_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(share) DO UPDATE SET
                        pwd = COALESCE(excluded.pwd, links.pwd),
                        state = excluded.state,
                        http = excluded.http,
                        quality = COALESCE(excluded.quality, links.quality),
                        last_checked = excluded.last_checked,
                        fail_count = CASE WHEN excluded.state = 'invalid'
                                          THEN links.fail_count + 1 ELSE 0 END
                    """,
                    (
                        link.share, link.pwd, link.name, link.source, link.time, link.conf,
                        link.state, link.http, quality, now, now,
                        1 if link.state == "invalid" else 0,
                    ),
                )
                self._conn.execute(
                    "INSERT OR IGNORE INTO link_resources (resource_key, share) VALUES (?, ?)",
                    (key, link.share),
                )
            self._conn.commit()

    def _log_search(self, key: str, query: str, valid: int, from_memory: bool, now: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO searches (resource_key, query, valid_count, from_memory, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, query, valid, int(from_memory), now),
            )
            self._conn.commit()

    def _stale_valid(self, before: float, limit: int) -> list[QuarkLink]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM links WHERE state = 'valid' AND last_checked < ? "
                "ORDER BY last_checked ASC LIMIT ?",
                (before, limit),
            ).fetchall()
        return [self._row_to_link(r) for r in rows]

    def _update_state(self, link: QuarkLink, now: float) -> None:
        quality = link.quality.model_dump_json() if link.quality else None
        with self._lock:
            self._conn.execute(
                "UPDATE links SET state = ?, http = ?, last_checked = ?, "
                "quality = COALESCE(?, quality), "
                "fail_count = CASE WHEN ? = 'invalid' THEN fail_count + 1 ELSE 0 END "
                "WHERE share = ?",
                (link.state, link.http, now, quality, link.state, link.share),
            )
            self._conn.commit()

    def _stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT state, COUNT(*) AS n FROM links GROUP BY state"
            ).fetchall()
            searches = self._conn.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
        stats = {r["state"]: r["n"] for r in rows}
        stats["searches"] = searches
        return stats

    # ---------------- 异步接口 ----------------

    async def recall(self, key: str) -> list[QuarkLink]:
        """某资源下记住的有效链接（最近验证的在前）。"""
        return await asyncio.to_thread(self._recall, key)

    async def known_invalid(self, shares: list[str], within_hours: float = 72) -> set[str]:
        """其中近期已确认失效的分享码。"""
        since = time.time() - within_hours * HOUR
        return await asyncio.to_thread(self._known_invalid, shares, since)

    async def save(self, key: str, links: list[QuarkLink]) -> None:
        """写入/更新验证结果并关联到资源。`unknown` 状态不入库。"""
        await asyncio.to_thread(self._save, key, links, time.time())

    async def log_search(self, key: str, query: str, valid: int, from_memory: bool) -> None:
        await asyncio.to_thread(self._log_search, key, query, valid, from_memory, time.time())

    async def stale_valid(self, older_than_hours: float, limit: int) -> list[QuarkLink]:
        """最久未复验的有效链接。"""
        before = time.time() - older_than_hours * HOUR
        return await asyncio.to_thread(self._stale_valid, before, limit)

    async def update_state(self, link: QuarkLink) -> None:
        await asyncio.to_thread(self._update_state, link, time.time())

    async def stats(self) -> dict[str, int]:
        return await asyncio.to_thread(self._stats)
