"""首页热门影视：搜索框占位文字和「试试」用。

来源优先级：TMDB 本周热门（配了 TMDB_API_KEY）→ 豆瓣热门（无需 key）→ 内置列表。
结果缓存 6 小时；不需要登录、不用 LLM。
"""

import asyncio
import logging
import time

import httpx

from app.services.metadata import TMDB_BASE, UA, _tmdb_auth, _tmdb_poster, _year

logger = logging.getLogger(__name__)

CACHE_SECONDS = 6 * 3600
LIMIT = 16
DOUBAN_HOT = "https://movie.douban.com/j/search_subjects"

# 两边都拿不到时用（保证首页不空）
DEFAULT = [
    {"title": t, "year": y, "media": m, "poster": None}
    for t, y, m in [
        ("哪吒之魔童闹海", "2025", "movie"), ("漫长的季节", "2023", "tv"),
        ("繁花", "2023", "tv"), ("流浪地球2", "2023", "movie"),
        ("庆余年 第二季", "2024", "tv"), ("我的阿勒泰", "2024", "tv"),
        ("热辣滚烫", "2024", "movie"), ("三体", "2023", "tv"),
        ("沙丘2", "2024", "movie"), ("狂飙", "2023", "tv"),
        ("奥本海默", "2023", "movie"), ("周处除三害", "2023", "movie"),
    ]
]


async def tmdb_trending(
    api_key: str, client: httpx.AsyncClient, base: str = TMDB_BASE, timeout: float = 8.0,
) -> list[dict]:
    headers, auth = _tmdb_auth(api_key)
    resp = await client.get(
        f"{(base or TMDB_BASE).rstrip('/')}/trending/all/week",
        params={**auth, "language": "zh-CN"}, headers=headers, timeout=timeout,
    )
    resp.raise_for_status()
    out = []
    for r in resp.json().get("results") or []:
        if not isinstance(r, dict) or r.get("adult") or r.get("media_type") not in ("movie", "tv"):
            continue
        title = r.get("title") or r.get("name")
        if not isinstance(title, str) or not title.strip():
            continue
        out.append({
            "title": title.strip(),
            "year": _year(r.get("release_date") or r.get("first_air_date")),
            "media": r["media_type"],
            "poster": _tmdb_poster(r.get("poster_path")),
        })
    return out


async def douban_hot(client: httpx.AsyncClient, timeout: float = 8.0) -> list[dict]:
    """豆瓣「热门」电视剧和电影，交替排列（接口不给年份）。"""
    headers = {"User-Agent": UA, "Referer": "https://movie.douban.com/"}

    async def one(kind: str) -> list[dict]:
        resp = await client.get(DOUBAN_HOT, params={
            "type": kind, "tag": "热门", "page_limit": LIMIT // 2, "page_start": 0,
        }, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return [
            {"title": s["title"].strip(), "year": None, "media": kind,
             "poster": s.get("cover") if isinstance(s.get("cover"), str) else None}
            for s in resp.json().get("subjects") or []
            if isinstance(s, dict) and isinstance(s.get("title"), str) and s["title"].strip()
        ]

    tv, movie = await asyncio.gather(one("tv"), one("movie"))
    mixed = [x for pair in zip(tv, movie, strict=False) for x in pair]
    return mixed + tv[len(mixed) // 2:] + movie[len(mixed) // 2:]


class Trending:
    def __init__(
        self, tmdb_key: str = "", tmdb_base: str = TMDB_BASE, douban: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._tmdb_key = tmdb_key
        self._tmdb_base = tmdb_base
        self._douban = douban
        self._client = client
        self._cache: tuple[float, dict] | None = None
        self._lock = asyncio.Lock()

    async def __call__(self) -> dict:
        """{"source": tmdb/douban/default, "items": [...]}，items 每条 {title, year, media, poster}。"""
        async with self._lock:  # 同时来很多请求时只查一次
            if self._cache and time.time() - self._cache[0] < CACHE_SECONDS:
                return self._cache[1]
            result = await self._fetch()
            # 内置列表只缓存 10 分钟，外部源恢复后尽快换回
            ts = time.time() if result["source"] != "default" else time.time() - CACHE_SECONDS + 600
            self._cache = (ts, result)
            return result

    async def _fetch(self) -> dict:
        client = self._client or httpx.AsyncClient(timeout=10.0, follow_redirects=True)
        try:
            sources = []
            if self._tmdb_key:
                sources.append(("tmdb", lambda: tmdb_trending(
                    self._tmdb_key, client, self._tmdb_base)))
            if self._douban:
                sources.append(("douban", lambda: douban_hot(client)))
            for name, fetch in sources:
                try:
                    items = _dedupe(await fetch())
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                    logger.warning("热门影视获取失败 %s（%s）", name, type(e).__name__)
                    continue
                if items:
                    return {"source": name, "items": items[:LIMIT]}
        finally:
            if self._client is None:
                await client.aclose()
        return {"source": "default", "items": DEFAULT}


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for it in items:
        if it["title"] not in seen:
            seen.add(it["title"])
            out.append(it)
    return out
