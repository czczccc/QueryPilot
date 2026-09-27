"""资源记忆：SQLite 链接库。

记住每条验证过的分享链接（状态、质量、所属资源），实现：
- 同一资源再次搜索时直接复用近期验证有效的链接（足够多时跳过全网搜索）；
- 近期确认失效的链接直接跳过，不再占用验证名额；
- 后台定期复验旧的有效链接，失效后自动下线。

sqlite3 是同步库，所有公开方法都通过 `asyncio.to_thread` 调用，避免阻塞事件循环。
"""

import asyncio
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from app.models import (
    Notification, QualityInfo, QuarkLink, Subscription, SubscriptionHistory, UserPrefs,
)

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
CREATE TABLE IF NOT EXISTS subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       TEXT NOT NULL,
    query           TEXT NOT NULL,
    resource        TEXT NOT NULL,
    resource_key    TEXT NOT NULL,
    created         REAL NOT NULL,
    last_checked    REAL,
    best_episodes   INTEGER NOT NULL DEFAULT 0,
    best_score      INTEGER NOT NULL DEFAULT 0,
    best_resolution TEXT,
    UNIQUE (client_id, resource_key)
);
CREATE TABLE IF NOT EXISTS notifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL,
    client_id       TEXT NOT NULL,
    resource        TEXT NOT NULL,
    kind            TEXT NOT NULL,
    message         TEXT NOT NULL,
    share           TEXT,
    ts              REAL NOT NULL,
    read            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notifications_client ON notifications(client_id, ts);
CREATE TABLE IF NOT EXISTS quark_accounts (
    session_hash TEXT PRIMARY KEY,
    cookie_enc   BLOB NOT NULL,
    nickname     TEXT,
    created      REAL NOT NULL,
    last_used    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS auto_saves (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL,
    share           TEXT NOT NULL,
    ok              INTEGER NOT NULL,
    file_count      INTEGER NOT NULL DEFAULT 0,
    folder          TEXT,
    message         TEXT,
    ts              REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auto_saves_sub ON auto_saves(subscription_id, ts);
CREATE TABLE IF NOT EXISTS subscription_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id      TEXT NOT NULL,
    query          TEXT NOT NULL,
    resource       TEXT NOT NULL,
    data           TEXT NOT NULL,
    created        REAL NOT NULL,
    completed      REAL NOT NULL,
    reason         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sub_history_client ON subscription_history(client_id, completed);
CREATE TABLE IF NOT EXISTS prefs (
    client_id TEXT PRIMARY KEY,
    data      TEXT NOT NULL,
    updated   REAL NOT NULL
);
"""

# 旧库升级：后续版本新增的列（ALTER TABLE 只加不删）
_MIGRATIONS = [
    ("links", "share_title", "TEXT"),
    ("links", "files_preview", "TEXT"),
    ("links", "copy_count", "INTEGER NOT NULL DEFAULT 0"),
    ("quark_accounts", "user_id", "TEXT"),
    ("subscriptions", "auto_save", "INTEGER NOT NULL DEFAULT 0"),
    ("subscriptions", "auto_save_status", "TEXT"),
    # 订阅 v2：以影视条目为订阅对象
    ("subscriptions", "state", "TEXT NOT NULL DEFAULT 'active'"),
    ("subscriptions", "media", "TEXT"),
    ("subscriptions", "season", "INTEGER"),
    ("subscriptions", "year", "TEXT"),
    ("subscriptions", "tmdb_id", "TEXT"),
    ("subscriptions", "douban_id", "TEXT"),
    ("subscriptions", "poster", "TEXT"),
    ("subscriptions", "total_episodes", "INTEGER"),
    ("subscriptions", "start_episode", "INTEGER NOT NULL DEFAULT 1"),
    ("subscriptions", "manual_total", "INTEGER NOT NULL DEFAULT 0"),
    ("subscriptions", "resolution", "TEXT"),
    ("subscriptions", "include_words", "TEXT"),
    ("subscriptions", "exclude_words", "TEXT"),
    ("subscriptions", "saved_episodes", "TEXT"),
    ("subscriptions", "meta_checked", "REAL"),
]

# 订阅 v2 的可编辑字段：Subscription 属性名 → 列名
SUB_FIELDS = {
    "media": "media", "season": "season", "year": "year", "tmdb_id": "tmdb_id",
    "douban_id": "douban_id", "poster": "poster", "total_episodes": "total_episodes",
    "start_episode": "start_episode", "manual_total": "manual_total",
    "resolution": "resolution", "include": "include_words", "exclude": "exclude_words",
    "auto_save": "auto_save",
}

_PUNCT_RE = re.compile(r"[\s\-_·:：,，.。!！?？'\"“”‘’()（）\[\]【】《》<>]+")

HOUR = 3600.0


def resource_key(name: str) -> str:
    """资源名归一化：小写、去空白与标点，作为记忆的主键。"""
    return _PUNCT_RE.sub("", name).lower()


def _to_col(v: object) -> object:
    return int(v) if isinstance(v, bool) else v


def _state(row: sqlite3.Row) -> str:
    """库里只存 new / active / paused；能搜但没法判断「完成」的订阅显示为 pending（待定）。"""
    state = row["state"] or "active"
    if state == "active" and (
        row["media"] is None or (row["media"] == "tv" and not row["total_episodes"])
    ):
        return "pending"
    return state


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
            for table, column, ddl in _MIGRATIONS:
                cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
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
            share_title=row["share_title"],
            files_preview=json.loads(row["files_preview"]) if row["files_preview"] else [],
            copy_count=row["copy_count"],
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
                                       quality, first_seen, last_checked, fail_count,
                                       share_title, files_preview)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(share) DO UPDATE SET
                        pwd = COALESCE(excluded.pwd, links.pwd),
                        state = excluded.state,
                        http = excluded.http,
                        quality = COALESCE(excluded.quality, links.quality),
                        share_title = COALESCE(excluded.share_title, links.share_title),
                        files_preview = COALESCE(excluded.files_preview, links.files_preview),
                        last_checked = excluded.last_checked,
                        fail_count = CASE WHEN excluded.state = 'invalid'
                                          THEN links.fail_count + 1 ELSE 0 END
                    """,
                    (
                        link.share, link.pwd, link.name, link.source, link.time, link.conf,
                        link.state, link.http, quality, now, now,
                        1 if link.state == "invalid" else 0,
                        link.share_title,
                        json.dumps(link.files_preview, ensure_ascii=False)
                        if link.files_preview else None,
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

    def _record_copy(self, share: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE links SET copy_count = copy_count + 1 WHERE share = ?", (share,)
            )
            self._conn.commit()
        return cur.rowcount > 0

    def _get_prefs(self, client_id: str) -> UserPrefs:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM prefs WHERE client_id = ?", (client_id,)
            ).fetchone()
        return UserPrefs.model_validate_json(row["data"]) if row else UserPrefs()

    def _set_prefs(self, client_id: str, prefs: UserPrefs, now: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO prefs (client_id, data, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(client_id) DO UPDATE SET data = excluded.data, "
                "updated = excluded.updated",
                (client_id, prefs.model_dump_json(), now),
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

    def _add_subscription(
        self, client_id: str, query: str, resource: str, baseline: tuple[int, int, str | None],
        now: float, limit: int, fields: dict,
    ) -> Subscription | None:
        key = resource_key(resource)
        with self._lock:
            count = self._conn.execute(
                "SELECT COUNT(*) FROM subscriptions WHERE client_id = ?", (client_id,)
            ).fetchone()[0]
            exists = self._conn.execute(
                "SELECT id FROM subscriptions WHERE client_id = ? AND resource_key = ?",
                (client_id, key),
            ).fetchone()
            if exists is None and count >= limit:
                return None
            self._conn.execute(
                "INSERT INTO subscriptions (client_id, query, resource, resource_key, created, "
                "best_episodes, best_score, best_resolution) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(client_id, resource_key) DO UPDATE SET query = excluded.query",
                (client_id, query, resource, key, now, *baseline),
            )
            if fields:
                cols = ", ".join(f"{SUB_FIELDS[k]} = ?" for k in fields)
                self._conn.execute(
                    f"UPDATE subscriptions SET {cols} WHERE client_id = ? AND resource_key = ?",
                    (*(_to_col(v) for v in fields.values()), client_id, key),
                )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM subscriptions WHERE client_id = ? AND resource_key = ?",
                (client_id, key),
            ).fetchone()
        return self._row_to_sub(row)

    @staticmethod
    def _row_to_sub(row: sqlite3.Row) -> Subscription:
        return Subscription(
            id=row["id"], query=row["query"], resource=row["resource"], created=row["created"],
            last_checked=row["last_checked"], best_episodes=row["best_episodes"],
            best_score=row["best_score"], best_resolution=row["best_resolution"],
            auto_save=bool(row["auto_save"]), auto_save_status=row["auto_save_status"],
            state=_state(row), media=row["media"], season=row["season"], year=row["year"],
            tmdb_id=row["tmdb_id"], douban_id=row["douban_id"], poster=row["poster"],
            total_episodes=row["total_episodes"], start_episode=row["start_episode"] or 1,
            manual_total=bool(row["manual_total"]), resolution=row["resolution"],
            include=row["include_words"], exclude=row["exclude_words"],
            saved_episodes=json.loads(row["saved_episodes"] or "[]"),
        )

    def _list_subscriptions(self, client_id: str | None) -> list[tuple[str, Subscription]]:
        sql = "SELECT * FROM subscriptions"
        args: tuple = ()
        if client_id is not None:
            sql += " WHERE client_id = ?"
            args = (client_id,)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY COALESCE(last_checked, 0), id", args)
            return [(r["client_id"], self._row_to_sub(r)) for r in rows.fetchall()]

    def _delete_subscription(self, client_id: str, sub_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM subscriptions WHERE id = ? AND client_id = ?", (sub_id, client_id)
            )
            self._conn.execute(
                "DELETE FROM notifications WHERE subscription_id = ? AND client_id = ?",
                (sub_id, client_id),
            )
            if cur.rowcount:
                self._conn.execute("DELETE FROM auto_saves WHERE subscription_id = ?", (sub_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def _update_subscription(
        self, sub: Subscription, now: float, notes: list[tuple[str, str, str | None]],
        client_id: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE subscriptions SET last_checked = ?, best_episodes = ?, best_score = ?, "
                "best_resolution = ?, total_episodes = ?, saved_episodes = ?, "
                "state = CASE WHEN state = 'paused' THEN state ELSE 'active' END WHERE id = ?",
                (now, sub.best_episodes, sub.best_score, sub.best_resolution,
                 sub.total_episodes, json.dumps(sorted(set(sub.saved_episodes))), sub.id),
            )
            for kind, message, share in notes:
                self._conn.execute(
                    "INSERT INTO notifications (subscription_id, client_id, resource, kind, "
                    "message, share, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sub.id, client_id, sub.resource, kind, message, share, now),
                )
            self._conn.commit()

    def _notifications(self, client_id: str, limit: int) -> list[Notification]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM notifications WHERE client_id = ? ORDER BY ts DESC, id DESC "
                "LIMIT ?",
                (client_id, limit),
            ).fetchall()
        return [
            Notification(
                id=r["id"], subscription_id=r["subscription_id"], resource=r["resource"],
                kind=r["kind"], message=r["message"], share=r["share"], ts=r["ts"],
                read=bool(r["read"]),
            )
            for r in rows
        ]

    def _mark_read(self, client_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE notifications SET read = 1 WHERE client_id = ?", (client_id,)
            )
            self._conn.commit()

    def _put_account(
        self, sh: str, cookie_enc: bytes, nickname: str | None, user_id: str | None, now: float
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO quark_accounts (session_hash, cookie_enc, nickname, user_id, created, "
                "last_used) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(session_hash) DO UPDATE SET "
                "cookie_enc = excluded.cookie_enc, nickname = excluded.nickname, "
                "user_id = excluded.user_id, last_used = excluded.last_used",
                (sh, cookie_enc, nickname, user_id, now, now),
            )
            self._conn.commit()

    def _get_account(self, sh: str, now: float) -> tuple[bytes, str | None, str | None] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT cookie_enc, nickname, user_id FROM quark_accounts WHERE session_hash = ?",
                (sh,),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE quark_accounts SET last_used = ? WHERE session_hash = ?", (now, sh)
            )
            self._conn.commit()
        return bytes(row["cookie_enc"]), row["nickname"], row["user_id"]

    def _latest_account(self, user_id: str) -> tuple[str, bytes] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_hash, cookie_enc FROM quark_accounts WHERE user_id = ?"
                " ORDER BY last_used DESC LIMIT 1", (user_id,),
            ).fetchone()
        return (row["session_hash"], bytes(row["cookie_enc"])) if row else None

    def _exec(self, sql: str, args: tuple) -> int:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
        return cur.rowcount

    def _get_subscription(self, client_id: str, sub_id: int) -> Subscription | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM subscriptions WHERE id = ? AND client_id = ?", (sub_id, client_id)
            ).fetchone()
        return self._row_to_sub(row) if row else None

    def _delete_account(self, sh: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM quark_accounts WHERE session_hash = ?", (sh,))
            self._conn.commit()

    def _edit_subscription(
        self, client_id: str, sub_id: int, fields: dict, state: str | None
    ) -> Subscription | None:
        sets = [f"{SUB_FIELDS[k]} = ?" for k in fields]
        args = [_to_col(v) for v in fields.values()]
        if state is not None:
            sets.append("state = ?")
            args.append(state)
        if "auto_save" in fields:
            sets.append("auto_save_status = NULL")
        if sets:
            self._exec(
                f"UPDATE subscriptions SET {', '.join(sets)} WHERE id = ? AND client_id = ?",
                (*args, sub_id, client_id),
            )
        return self._get_subscription(client_id, sub_id)

    def _archive(self, client_id: str, sub: Subscription, reason: str, now: float) -> int:
        data = sub.model_dump(include={
            "media", "season", "year", "tmdb_id", "douban_id", "poster", "total_episodes",
            "start_episode", "resolution", "include", "exclude", "auto_save",
        })
        data["saved_count"] = len(set(sub.saved_episodes))
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO subscription_history (client_id, query, resource, data, created, "
                "completed, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (client_id, sub.query, sub.resource, json.dumps(data, ensure_ascii=False),
                 sub.created, now, reason),
            )
            # 通知和转存记录留着（历史里还能看到），只删订阅本身
            self._conn.execute(
                "DELETE FROM subscriptions WHERE id = ? AND client_id = ?", (sub.id, client_id)
            )
            self._conn.commit()
        return int(cur.lastrowid or 0)

    @staticmethod
    def _row_to_history(row: sqlite3.Row) -> tuple[SubscriptionHistory, dict]:
        data = json.loads(row["data"] or "{}")
        hist = SubscriptionHistory(
            id=row["id"], query=row["query"], resource=row["resource"], created=row["created"],
            completed=row["completed"], reason=row["reason"],
            **{k: data.get(k) for k in ("media", "season", "year", "tmdb_id", "douban_id",
                                        "poster", "total_episodes")},
            saved_count=data.get("saved_count") or 0,
        )
        return hist, data

    def _history(self, client_id: str, limit: int) -> list[SubscriptionHistory]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM subscription_history WHERE client_id = ? "
                "ORDER BY completed DESC, id DESC LIMIT ?", (client_id, limit),
            ).fetchall()
        return [self._row_to_history(r)[0] for r in rows]

    def _get_history(self, client_id: str, hid: int) -> tuple[SubscriptionHistory, dict] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM subscription_history WHERE id = ? AND client_id = ?",
                (hid, client_id),
            ).fetchone()
        return self._row_to_history(row) if row else None

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

    async def record_copy(self, share: str) -> bool:
        """用户复制了某条链接：计数 +1（排序时作为「被认可」的信号）。"""
        return await asyncio.to_thread(self._record_copy, share)

    async def get_prefs(self, client_id: str) -> UserPrefs:
        return await asyncio.to_thread(self._get_prefs, client_id)

    async def set_prefs(self, client_id: str, prefs: UserPrefs) -> None:
        await asyncio.to_thread(self._set_prefs, client_id, prefs, time.time())

    async def stats(self) -> dict[str, int]:
        return await asyncio.to_thread(self._stats)

    async def add_subscription(
        self, client_id: str, query: str, resource: str,
        baseline: tuple[int, int, str | None] = (0, 0, None), limit: int = 20,
        **fields: object,
    ) -> Subscription | None:
        """新增订阅（同一资源重复订阅只更新搜索词）；超过每人上限返回 None。

        baseline = (集数, 质量分, 清晰度)：订阅时已有的最好情况，之后只有超过它才通知。
        """
        return await asyncio.to_thread(
            self._add_subscription, client_id, query, resource, baseline, time.time(), limit,
            {k: v for k, v in fields.items() if k in SUB_FIELDS},
        )

    async def get_subscription(self, client_id: str, sub_id: int) -> Subscription | None:
        return await asyncio.to_thread(self._get_subscription, client_id, sub_id)

    async def edit_subscription(
        self, client_id: str, sub_id: int, state: str | None = None, **fields: object
    ) -> Subscription | None:
        """改订阅的设置字段（见 SUB_FIELDS）和状态（new/active/paused）；不存在返回 None。"""
        bad = set(fields) - set(SUB_FIELDS)
        if bad:
            raise ValueError(f"不能修改的字段：{bad}")
        return await asyncio.to_thread(self._edit_subscription, client_id, sub_id, fields, state)

    async def meta_due(self, sub_id: int, hours: float) -> bool:
        """距上次刷新元数据超过 `hours` 就返回 True 并记下这次刷新时间。"""
        now = time.time()
        n = await asyncio.to_thread(
            self._exec,
            "UPDATE subscriptions SET meta_checked = ? WHERE id = ? "
            "AND (meta_checked IS NULL OR meta_checked < ?)", (now, sub_id, now - hours * HOUR),
        )
        return n > 0

    async def archive_subscription(self, client_id: str, sub: Subscription, reason: str) -> int:
        """订阅完成：移入订阅历史（可重新订阅），返回历史记录 id。"""
        return await asyncio.to_thread(self._archive, client_id, sub, reason, time.time())

    async def subscription_history(
        self, client_id: str, limit: int = 50
    ) -> list[SubscriptionHistory]:
        return await asyncio.to_thread(self._history, client_id, limit)

    async def get_history(self, client_id: str, hid: int) -> tuple[SubscriptionHistory, dict] | None:
        """(历史记录, 当时的订阅设置)。"""
        return await asyncio.to_thread(self._get_history, client_id, hid)

    async def delete_history(self, client_id: str, hid: int) -> bool:
        return await asyncio.to_thread(
            self._exec, "DELETE FROM subscription_history WHERE id = ? AND client_id = ?",
            (hid, client_id),
        ) > 0

    async def list_subscriptions(
        self, client_id: str | None = None
    ) -> list[tuple[str, Subscription]]:
        """(client_id, 订阅)；不传 client_id 时返回全部，最久没检查的在前。"""
        return await asyncio.to_thread(self._list_subscriptions, client_id)

    async def delete_subscription(self, client_id: str, sub_id: int) -> bool:
        return await asyncio.to_thread(self._delete_subscription, client_id, sub_id)

    async def update_subscription(
        self, client_id: str, sub: Subscription, notes: list[tuple[str, str, str | None]]
    ) -> None:
        """写回检查结果，并记下通知 (kind, message, share)。"""
        await asyncio.to_thread(self._update_subscription, sub, time.time(), notes, client_id)

    async def notifications(self, client_id: str, limit: int = 30) -> list[Notification]:
        return await asyncio.to_thread(self._notifications, client_id, limit)

    async def mark_read(self, client_id: str) -> None:
        await asyncio.to_thread(self._mark_read, client_id)

    async def put_account(
        self, session_hash: str, cookie_enc: bytes, nickname: str | None, user_id: str | None = None
    ) -> None:
        """保存某个浏览器会话对应的夸克登录凭证（只存密文与会话哈希）及所属账号。"""
        await asyncio.to_thread(
            self._put_account, session_hash, cookie_enc, nickname, user_id, time.time()
        )

    async def get_account(self, session_hash: str) -> tuple[bytes, str | None, str | None] | None:
        """(加密凭证, 昵称, 账号 id)。"""
        return await asyncio.to_thread(self._get_account, session_hash, time.time())

    async def latest_account(self, user_id: str) -> tuple[str, bytes] | None:
        """某个账号最近使用的登录凭证：(会话哈希, 密文)；解密时会话哈希是 AAD。"""
        return await asyncio.to_thread(self._latest_account, user_id)

    async def set_auto_save(self, client_id: str, sub_id: int, on: bool) -> Subscription | None:
        """打开 / 关闭某个订阅的自动转存（打开时清掉之前的失败状态）。"""
        n = await asyncio.to_thread(
            self._exec,
            "UPDATE subscriptions SET auto_save = ?, auto_save_status = NULL"
            " WHERE id = ? AND client_id = ?", (int(on), sub_id, client_id),
        )
        return await asyncio.to_thread(self._get_subscription, client_id, sub_id) if n else None

    async def set_auto_save_status(self, sub_id: int, status: str | None) -> None:
        await asyncio.to_thread(
            self._exec, "UPDATE subscriptions SET auto_save_status = ? WHERE id = ?",
            (status, sub_id),
        )

    async def clear_auto_save_status(self, client_id: str, status: str) -> int:
        """重新登录后：把这个人所有因为登录失效而暂停的自动转存恢复。"""
        return await asyncio.to_thread(
            self._exec, "UPDATE subscriptions SET auto_save_status = NULL"
            " WHERE client_id = ? AND auto_save_status = ?", (client_id, status),
        )

    async def log_auto_save(
        self, sub_id: int, share: str, ok: bool, file_count: int, folder: str | None,
        message: str,
    ) -> None:
        await asyncio.to_thread(
            self._exec,
            "INSERT INTO auto_saves (subscription_id, share, ok, file_count, folder, message, ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sub_id, share, int(ok), file_count, folder, message, time.time()),
        )

    async def has_auto_saved(self, sub_id: int) -> bool:
        """这个订阅是否已经成功自动转存过文件（电影只存一次）。"""
        def run() -> bool:
            with self._lock:
                return self._conn.execute(
                    "SELECT 1 FROM auto_saves WHERE subscription_id = ? AND ok = 1"
                    " AND file_count > 0 LIMIT 1", (sub_id,),
                ).fetchone() is not None

        return await asyncio.to_thread(run)

    async def auto_save_log(self, client_id: str, sub_id: int, limit: int = 20) -> list[dict]:
        def run() -> list[dict]:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT a.share, a.ok, a.file_count, a.folder, a.message, a.ts FROM auto_saves a"
                    " JOIN subscriptions s ON s.id = a.subscription_id"
                    " WHERE a.subscription_id = ? AND s.client_id = ? ORDER BY a.ts DESC, a.id DESC"
                    " LIMIT ?", (sub_id, client_id, limit),
                ).fetchall()
            return [{**dict(r), "ok": bool(r["ok"])} for r in rows]

        return await asyncio.to_thread(run)

    async def delete_account(self, session_hash: str) -> None:
        await asyncio.to_thread(self._delete_account, session_hash)
