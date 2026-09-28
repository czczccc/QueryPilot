"""站长后台接口：用量统计、账号管理（封禁 / 单独额度）、IP 封禁、邀请码。

- 全部接口要带请求头 `X-Admin-Token`，与 .env 的 `ADMIN_TOKEN` 常量时间比较；
  没配置 `ADMIN_TOKEN` 时整组接口 404（等于不存在）；
- 只返回用量与账号信息，任何夸克凭证都不出现在这里。
"""

import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.usage import SITE, QuotaGuard, UsageStore, today


class BanRequest(BaseModel):
    reason: str = Field(default="", max_length=200)


class LimitRequest(BaseModel):
    ai_limit: int | None = Field(default=None, ge=0, le=100000)  # null 恢复默认


class IpBanRequest(BaseModel):
    ip: str = Field(min_length=2, max_length=64)
    reason: str = Field(default="", max_length=200)


class InviteRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=50)
    max_uses: int = Field(default=1, ge=0, le=1000)  # 0 不限次数
    note: str = Field(default="", max_length=100)


def admin_router(
    users: UsageStore, token: str, quota: QuotaGuard | None, rate_limit: Callable,
    links=None,
) -> APIRouter:
    def auth(x_admin_token: str = Header(default="")) -> None:
        if not token:
            raise HTTPException(status_code=404, detail="Not Found")
        if not hmac.compare_digest(x_admin_token.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="管理口令不正确")

    router = APIRouter(prefix="/api/admin", dependencies=[Depends(rate_limit), Depends(auth)])

    @router.get("/feedback")
    async def feedback_export() -> dict:
        """用户对搜索结果的「不对 / 失效」反馈，按搜索词分组，可直接当评测集的反例用：
        每组 {query, bad: [{share, reason, title, reports, weight}]}（不含反馈人）。"""
        if links is None:
            raise HTTPException(status_code=404, detail="记忆库未启用")
        groups: dict[str, dict] = {}
        for r in await links.report_export():
            g = groups.setdefault(r["query"] or "", {"query": r["query"] or "", "bad": []})
            g["bad"].append({"share": r["share"], "reason": r["reason"], "title": r["title"],
                             "reports": r["reports"], "weight": r["weight"]})
        return {"items": list(groups.values())}

    @router.get("/overview")
    async def overview() -> dict:
        """今天的全站用量、最近 14 天趋势、用量最多的身份、当前额度设置。"""
        day = today()
        site = (await users.get(day, SITE))
        return {
            "day": day,
            "today": site,
            "trend": await users.site_trend(14),
            "top": await users.usage_by_subject(day, 20),
            "limits": asdict(quota.config) if quota else None,
            "users": (await users.query("SELECT COUNT(*) AS n FROM users"))[0]["n"],
        }

    @router.get("/usage")
    async def usage(
        day: str = Query(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$"),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict:
        """某天各身份的用量：user:账号 / ip:地址 / system（后台任务）。"""
        day = day or today()
        return {"day": day, "site": await users.get(day, SITE),
                "rows": await users.usage_by_subject(day, limit)}

    @router.get("/users")
    async def list_users(
        q: str = Query(default="", max_length=64),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict]:
        return await users.list_users(today(), limit, offset, q)

    async def _set_user(user_id: str, sql: str, args: tuple) -> dict:
        if not await users.execute(sql, (*args, user_id)):
            raise HTTPException(status_code=404, detail="账号不存在")
        return await users.get_user(user_id) or {}

    @router.post("/users/{user_id}/ban")
    async def ban_user(user_id: str, req: BanRequest) -> dict:
        return await _set_user(
            user_id, "UPDATE users SET banned = 1, ban_reason = ? WHERE user_id = ?",
            (req.reason or None,),
        )

    @router.post("/users/{user_id}/unban")
    async def unban_user(user_id: str) -> dict:
        return await _set_user(
            user_id, "UPDATE users SET banned = 0, ban_reason = NULL WHERE user_id = ?", ()
        )

    @router.put("/users/{user_id}/limit")
    async def set_limit(user_id: str, req: LimitRequest) -> dict:
        """单独设置这个账号每天的 AI 搜索次数（null 恢复默认，0 表示不限）。"""
        return await _set_user(
            user_id, "UPDATE users SET ai_limit = ? WHERE user_id = ?", (req.ai_limit,)
        )

    @router.get("/bans")
    async def list_bans() -> list[dict]:
        return await users.query("SELECT ip, reason, created FROM ip_bans ORDER BY created DESC")

    @router.post("/bans")
    async def ban_ip(req: IpBanRequest) -> dict:
        await users.execute(
            "INSERT INTO ip_bans (ip, reason, created) VALUES (?, ?, ?)"
            " ON CONFLICT (ip) DO UPDATE SET reason = excluded.reason",
            (req.ip.strip(), req.reason, time.time()),
        )
        return {"ip": req.ip.strip(), "reason": req.reason}

    @router.delete("/bans/{ip}")
    async def unban_ip(ip: str) -> dict:
        if not await users.execute("DELETE FROM ip_bans WHERE ip = ?", (ip,)):
            raise HTTPException(status_code=404, detail="这个 IP 没有被封")
        return {"deleted": True}

    @router.get("/invites")
    async def list_invites() -> list[dict]:
        return await users.query(
            "SELECT code, note, max_uses, uses, created FROM invites ORDER BY created DESC"
        )

    @router.post("/invites")
    async def create_invites(req: InviteRequest | None = None) -> list[dict]:
        req = req or InviteRequest()
        now = time.time()
        codes = [secrets.token_urlsafe(6) for _ in range(req.count)]
        for code in codes:
            await users.execute(
                "INSERT INTO invites (code, note, max_uses, uses, created) VALUES (?, ?, ?, 0, ?)",
                (code, req.note or None, req.max_uses, now),
            )
        return [{"code": c, "max_uses": req.max_uses, "note": req.note} for c in codes]

    @router.delete("/invites/{code}")
    async def delete_invite(code: str) -> dict:
        if not await users.execute("DELETE FROM invites WHERE code = ?", (code,)):
            raise HTTPException(status_code=404, detail="邀请码不存在")
        return {"deleted": True}

    return router
