"""影视资料查询：TMDB（官方 API）与豆瓣（移动端接口），给转存分类提供依据。

- TMDB：需要 `TMDB_API_KEY`（v3 API Key 或 v4 读访问令牌都行，免费申请）；
  国内访问不稳时可用 `TMDB_API_BASE` 换成反代地址。
- 豆瓣：无官方 API，用搜索建议 + 移动端详情接口，无需 key；接口随时可能变或限流，
  任何失败都只是「查不到」，不影响转存。
"""

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass, field

import httpx

from app.models import AirEpisode, CollectionInfo, CollectionPart
from app.services.relevance import seasons_in

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
    id: str | None = None  # TMDB id 或豆瓣 id（看 source）
    poster: str | None = None  # 海报图地址
    episodes: dict[int, int] = field(default_factory=dict)  # 季 → 总集数（剧集）
    season_years: dict[int, str] = field(default_factory=dict)  # 季 → 开播年份（剧集）
    collection: dict | None = None  # 电影所属系列（TMDB）：{id, name, poster}

    def brief(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], "")}


def _year(date: str | None) -> str | None:
    m = re.match(r"(\d{4})", date or "")
    return m.group(1) if m else None


# ---------------- TMDB ----------------

TMDB_IMAGE = "https://image.tmdb.org/t/p/w342"


def _tmdb_poster(path: object) -> str | None:
    return f"{TMDB_IMAGE}{path}" if isinstance(path, str) and path.startswith("/") else None

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
        coll = d.get("belongs_to_collection")
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
            id=str(d.get("id") or hit.get("id") or "") or None,
            poster=_tmdb_poster(d.get("poster_path") or hit.get("poster_path")),
            episodes={
                int(x["season_number"]): int(x.get("episode_count") or 0)
                for x in d.get("seasons") or []
                if isinstance(x, dict) and isinstance(x.get("season_number"), int)
                and x["season_number"] > 0 and x.get("episode_count")
            },
            season_years={
                int(x["season_number"]): y
                for x in d.get("seasons") or []
                if isinstance(x, dict) and isinstance(x.get("season_number"), int)
                and x["season_number"] > 0 and (y := _year(x.get("air_date")))
            },
            collection={"id": str(coll["id"]), "name": coll.get("name") or "",
                        "poster": _tmdb_poster(coll.get("poster_path"))}
            if isinstance(coll, dict) and coll.get("id") else None,
        )

    found = await asyncio.gather(*(detail(h) for h in hits[:limit]))
    return [f for f in found if f and f.title]


async def tmdb_season(
    tv_id: str, season: int, api_key: str, client: httpx.AsyncClient,
    base: str = TMDB_BASE, timeout: float = 8.0,
) -> list[AirEpisode]:
    """一季每集的播出日期（TMDB /tv/{id}/season/{n}，含还没播的集）。"""
    headers, auth = _tmdb_auth(api_key)
    resp = await client.get(
        f"{(base or TMDB_BASE).rstrip('/')}/tv/{tv_id}/season/{season}",
        params={**auth, "language": "zh-CN"}, headers=headers, timeout=timeout,
    )
    resp.raise_for_status()
    out = []
    for e in resp.json().get("episodes") or []:
        if not isinstance(e, dict) or not isinstance(e.get("episode_number"), int):
            continue
        date = e.get("air_date")
        out.append(AirEpisode(
            episode=e["episode_number"],
            air_date=date if isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
            else None,
            name=e.get("name") if isinstance(e.get("name"), str) else None,
        ))
    return out


_SERIES_SUFFIX = re.compile(r"\s*(?:[（(]?系列[）)]?|合集|\s[Cc]ollection)$")


def collection_name(name: str) -> str:
    """「谍影重重（系列）」→「谍影重重」；去完是空的就用原名。"""
    return _SERIES_SUFFIX.sub("", name.strip()) or name.strip()


async def tmdb_collection(
    cid: str, api_key: str, client: httpx.AsyncClient, base: str = TMDB_BASE,
    timeout: float = 8.0, today: str | None = None,
) -> CollectionInfo | None:
    """系列电影的全部作品（TMDB /collection/{id}），按上映日期排序；没定档的排最后。"""
    headers, auth = _tmdb_auth(api_key)
    resp = await client.get(
        f"{(base or TMDB_BASE).rstrip('/')}/collection/{cid}",
        params={**auth, "language": "zh-CN"}, headers=headers, timeout=timeout,
    )
    resp.raise_for_status()
    d = resp.json()
    today = today or time.strftime("%Y-%m-%d")
    raw = []
    for p in d.get("parts") or []:
        if not isinstance(p, dict) or not p.get("id") or p.get("media_type", "movie") != "movie":
            continue
        date = p.get("release_date")
        date = date if isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else None
        raw.append((date or "9999", p, date))
    raw.sort(key=lambda x: x[0])
    parts = [
        CollectionPart(
            index=i, id=str(p["id"]), title=p.get("title") or p.get("original_title") or "",
            original_title=p.get("original_title"), year=_year(date), release_date=date,
            poster=_tmdb_poster(p.get("poster_path")), released=bool(date and date <= today),
        )
        for i, (_, p, date) in enumerate(raw, 1)
    ]
    if not parts:
        return None
    return CollectionInfo(
        id=str(d.get("id") or cid), name=collection_name(d.get("name") or ""),
        poster=_tmdb_poster(d.get("poster_path")) or parts[0].poster, parts=parts,
    )


async def tmdb_tv_seasons(
    tv_id: str, api_key: str, client: httpx.AsyncClient, base: str = TMDB_BASE,
    timeout: float = 8.0, today: str | None = None,
) -> CollectionInfo | None:
    """整部剧的各季（TMDB /tv/{id}）：每季一个 part，id 为 s<季号>，不含特别篇（第 0 季）。"""
    headers, auth = _tmdb_auth(api_key)
    resp = await client.get(
        f"{(base or TMDB_BASE).rstrip('/')}/tv/{tv_id}",
        params={**auth, "language": "zh-CN"}, headers=headers, timeout=timeout,
    )
    resp.raise_for_status()
    d = resp.json()
    name = d.get("name") or d.get("original_name") or ""
    today = today or time.strftime("%Y-%m-%d")
    poster = _tmdb_poster(d.get("poster_path"))
    parts = []
    for x in d.get("seasons") or []:
        if not isinstance(x, dict) or not isinstance(x.get("season_number"), int):
            continue
        n = x["season_number"]
        if n <= 0:
            continue
        date = x.get("air_date")
        date = date if isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else None
        parts.append(CollectionPart(
            index=n, id=f"s{n}", title=name if n == 1 else f"{name} 第{n}季",
            year=_year(date), release_date=date,
            poster=_tmdb_poster(x.get("poster_path")) or poster,
            released=bool(date and date <= today),
            episodes=x.get("episode_count") if isinstance(x.get("episode_count"), int)
            and x["episode_count"] > 0 else None,
        ))
    if not name or not parts:
        return None
    parts.sort(key=lambda p: p.index)
    return CollectionInfo(id=f"tv:{d.get('id') or tv_id}", name=name, poster=poster,
                          parts=parts, media="tv", tmdb_id=str(d.get("id") or tv_id),
                          year=_year(d.get("first_air_date")))


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
            media="tv" if item.get("episode") else None, id=str(item["id"]),
            poster=item.get("img") if isinstance(item.get("img"), str) else None,
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
            pic = d.get("pic") if isinstance(d.get("pic"), dict) else {}
            info.poster = pic.get("normal") or pic.get("large") or info.poster
            count = d.get("episodes_count")
            if info.media == "tv" and isinstance(count, int) and count > 0:
                # 豆瓣每季是单独的条目：季号从标题里认（「第二季」），认不出按第 1 季
                seasons = seasons_in(info.title)
                info.episodes = {min(seasons) if seasons else 1: count}
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
        self._cache: dict[tuple[str, str | None, int], list[MediaInfo]] = {}
        self._collections: dict[str, tuple[float, CollectionInfo | None]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._tmdb_key) or self._douban

    async def __call__(
        self, name: str, year: str | None, fresh: bool = False, limit: int = 2
    ) -> list[MediaInfo]:
        """`fresh`：跳过缓存重新查（订阅定期刷新总集数时用）；`limit`：每个来源取几条
        （转存分类只要最像的 2 条，订阅选条目取 10 条）。"""
        key = (name, year, limit)
        if key in self._cache and not fresh:
            return self._cache[key]
        client = self._client or httpx.AsyncClient(timeout=10.0, follow_redirects=True)
        try:
            tasks = []
            if self._tmdb_key:
                tasks.append(search_tmdb(name, year, self._tmdb_key, client, self._tmdb_base,
                                         limit=limit))
            if self._douban:
                tasks.append(search_douban(name, year, client, limit=limit))
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

    async def collection(self, cid: str, fresh: bool = False) -> CollectionInfo | None:
        """系列电影的全部作品，或 `tv:<id>` 整部剧的各季（缓存 1 天）；
        没配 TMDB key 或查询失败时为 None。"""
        if not self._tmdb_key or not cid:
            return None
        hit = self._collections.get(cid)
        if hit and not fresh and time.monotonic() - hit[0] < 86400:
            return hit[1]
        client = self._client or httpx.AsyncClient(timeout=10.0, follow_redirects=True)
        try:
            if cid.startswith("tv:"):  # 整部剧：各季当成系列的各部
                info = await tmdb_tv_seasons(cid[3:], self._tmdb_key, client, self._tmdb_base)
            else:
                info = await tmdb_collection(cid, self._tmdb_key, client, self._tmdb_base)
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("系列电影查询失败（%s）", type(e).__name__)
            return None
        finally:
            if self._client is None:
                await client.aclose()
        if len(self._collections) > 500:
            self._collections.clear()
        self._collections[cid] = (time.monotonic(), info)
        return info

    async def schedule(self, tmdb_id: str, season: int) -> list[AirEpisode]:
        """剧集一季的播出日历；没配 TMDB key（豆瓣没有每集日期）或查询失败时为空。"""
        if not self._tmdb_key or not tmdb_id:
            return []
        client = self._client or httpx.AsyncClient(timeout=10.0, follow_redirects=True)
        try:
            return await tmdb_season(tmdb_id, season, self._tmdb_key, client, self._tmdb_base)
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("播出日历查询失败（%s）", type(e).__name__)
            return []
        finally:
            if self._client is None:
                await client.aclose()
