"""影视资料查询：TMDB（官方 API）与豆瓣（移动端接口），给转存分类提供依据。

- TMDB：需要 `TMDB_API_KEY`（v3 API Key 或 v4 读访问令牌都行，免费申请）；
  国内访问不稳时可用 `TMDB_API_BASE` 换成反代地址。
- 豆瓣：无官方 API，用搜索建议 + 移动端详情接口，无需 key；接口随时可能变或限流，
  任何失败都只是「查不到」，不影响转存。
"""

import asyncio
import logging
import re
from dataclasses import asdict, dataclass, field

import httpx

logger = logging.getLogger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
DOUBAN_SUGGEST = "https://movie.douban.com/j/subject_suggest"
DOUBAN_DETAIL = "https://m.douban.com/rexxar/api/v2/{kind}/{id}"
UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)


@dataclass
class MediaInfo:
    source: str  # tmdb / douban
    title: str
    original_title: str | None = None
    year: str | None = None
    media: str | None = None  # movie / tv
    genres: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)  # ISO 代码或中文地区名
    language: str | None = None
    seasons: int | None = None

    def brief(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], "")}


def _year(date: str | None) -> str | None:
    m = re.match(r"(\d{4})", date or "")
    return m.group(1) if m else None


# ---------------- TMDB ----------------

def _tmdb_auth(api_key: str) -> tuple[dict, dict]:
    """v4 读访问令牌（eyJ 开头的 JWT）走 Bearer，v3 key 走查询参数。"""
    if api_key.startswith("eyJ"):
        return {"Authorization": f"Bearer {api_key}"}, {}
    return {}, {"api_key": api_key}


async def search_tmdb(
    query: str, year: str | None, api_key: str, client: httpx.AsyncClient,
    base: str = TMDB_BASE, limit: int = 2, timeout: float = 8.0,
) -> list[MediaInfo]:
    headers, auth = _tmdb_auth(api_key)
    base = (base or TMDB_BASE).rstrip("/")
    resp = await client.get(
        f"{base}/search/multi",
        params={**auth, "query": query, "language": "zh-CN", "include_adult": "false"},
        headers=headers, timeout=timeout,
    )
    resp.raise_for_status()
    hits = [r for r in resp.json().get("results") or [] if r.get("media_type") in ("movie", "tv")]
    if year:  # 年份吻合的排前面
        hits.sort(key=lambda r: _year(r.get("release_date") or r.get("first_air_date")) != year)

    async def detail(hit: dict) -> MediaInfo | None:
        media = hit["media_type"]
        try:
            r = await client.get(
                f"{base}/{media}/{hit['id']}", params={**auth, "language": "zh-CN"},
                headers=headers, timeout=timeout,
            )
            r.raise_for_status()
            d = r.json()
        except (httpx.HTTPError, ValueError):
            d = hit
        countries = [c.get("iso_3166_1") for c in d.get("production_countries") or []
                     if isinstance(c, dict)] or list(d.get("origin_country") or [])
        return MediaInfo(
            source="tmdb",
            title=d.get("title") or d.get("name") or "",
            original_title=d.get("original_title") or d.get("original_name"),
            year=_year(d.get("release_date") or d.get("first_air_date")),
            media=media,
            genres=[g["name"] for g in d.get("genres") or [] if isinstance(g, dict)
                    and g.get("name")],
            countries=[c for c in countries if c],
            language=d.get("original_language"),
            seasons=d.get("number_of_seasons"),
        )

    found = await asyncio.gather(*(detail(h) for h in hits[:limit]))
    return [f for f in found if f and f.title]


# ---------------- 豆瓣 ----------------

async def search_douban(
    query: str, year: str | None, client: httpx.AsyncClient, limit: int = 2, timeout: float = 8.0
) -> list[MediaInfo]:
    headers = {"User-Agent": UA, "Referer": "https://m.douban.com/movie/",
               "Accept-Language": "zh-CN,zh;q=0.9"}
    resp = await client.get(DOUBAN_SUGGEST, params={"q": query}, headers=headers,
                            timeout=timeout)
    resp.raise_for_status()
    items = [i for i in resp.json() if isinstance(i, dict) and i.get("id")]
    if year:
        items.sort(key=lambda i: str(i.get("year")) != year)

    async def detail(item: dict) -> MediaInfo:
        info = MediaInfo(
            source="douban", title=item.get("title") or "",
            original_title=item.get("sub_title") or None, year=str(item.get("year") or "") or None,
            media="tv" if item.get("episode") else None,
        )
        for kind in ("tv", "movie") if info.media == "tv" else ("movie", "tv"):
            try:
                r = await client.get(DOUBAN_DETAIL.format(kind=kind, id=item["id"]),
                                     headers=headers, timeout=timeout)
                if r.status_code != 200:
                    continue
                d = r.json()
            except (httpx.HTTPError, ValueError):
                continue
            info.countries = [c for c in d.get("countries") or [] if isinstance(c, str)]
            info.genres = [g for g in d.get("genres") or [] if isinstance(g, str)]
            info.media = "tv" if d.get("is_tv") or kind == "tv" else "movie"
            info.year = str(d.get("year") or "") or info.year
            break
        return info

    found = await asyncio.gather(*(detail(i) for i in items[:limit]))
    return [f for f in found if f.title]


# ---------------- 汇总 ----------------

class MetadataLookup:
    """并发查 TMDB 与豆瓣，结果缓存在内存（同一部剧反复转存不重复查）。"""

    def __init__(
        self, tmdb_key: str = "", tmdb_base: str = TMDB_BASE, douban: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._tmdb_key = tmdb_key
        self._tmdb_base = tmdb_base
        self._douban = douban
        self._client = client
        self._cache: dict[tuple[str, str | None], list[MediaInfo]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._tmdb_key) or self._douban

    async def __call__(self, name: str, year: str | None) -> list[MediaInfo]:
        key = (name, year)
        if key in self._cache:
            return self._cache[key]
        client = self._client or httpx.AsyncClient(timeout=10.0, follow_redirects=True)
        try:
            tasks = []
            if self._tmdb_key:
                tasks.append(search_tmdb(name, year, self._tmdb_key, client, self._tmdb_base))
            if self._douban:
                tasks.append(search_douban(name, year, client))
            results = await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if self._client is None:
                await client.aclose()
        found: list[MediaInfo] = []
        for r in results:
            if isinstance(r, BaseException):
                logger.warning("影视资料查询失败（%s）", type(r).__name__)
            else:
                found += r
        if len(self._cache) > 500:
            self._cache.clear()
        self._cache[key] = found
        return found
