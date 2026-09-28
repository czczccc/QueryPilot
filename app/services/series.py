"""订阅选条目：把同一系列的电影（TMDB collection）合并成一个候选，像剧集选季一样选第几部。"""

import asyncio
import re
from collections.abc import Awaitable, Callable

from app.models import CollectionInfo, MediaCandidate
from app.services.metadata import MediaInfo
from app.services.relevance import norm

FetchCollection = Callable[[str], Awaitable[CollectionInfo | None]]


def default_part(query: str, info: CollectionInfo) -> int:
    """默认选中的一部：片名和搜索词对上的（「谍影重重5」→ 第 5 部），否则第 1 部。"""
    q = norm(query)
    if not q:
        return 1
    for part in info.parts:
        if q in {norm(part.title), norm(part.original_title or "")}:
            return part.index
    if re.search(r"\d$", q):  # 「谍影重重3」对上「谍影重重3：最后通牒」
        for part in info.parts:
            if norm(part.title).startswith(q) or norm(part.original_title or "").startswith(q):
                return part.index
    return 1


def _candidate(info: MediaInfo) -> MediaCandidate:
    return MediaCandidate(
        source=info.source, id=info.id, title=info.title, original_title=info.original_title,
        year=info.year, media=info.media, kind=info.media, poster=info.poster,
        seasons=info.seasons, episodes=info.episodes,
    )


def _similar(query: str, title: str) -> bool:
    q, t = norm(query), norm(title)
    return bool(q) and (q == t or t.startswith(q) or (bool(t) and q.startswith(t)))


async def build_candidates(
    query: str, infos: list[MediaInfo], fetch: FetchCollection | None,
) -> list[MediaCandidate]:
    """TMDB / 豆瓣结果 → 候选列表：同一系列的电影合并成一个系列候选（列出全部作品），
    豆瓣里和 TMDB 重复的条目去掉；片名和搜索词像的、系列和剧集排前面。"""
    infos = [i for i in infos if i.media]
    ids = list(dict.fromkeys(
        i.collection["id"] for i in infos
        if fetch is not None and i.source == "tmdb" and i.media == "movie" and i.collection
    ))
    fetched = await asyncio.gather(*(fetch(c) for c in ids)) if ids else []
    colls = {cid: c for cid, c in zip(ids, fetched, strict=True) if c is not None}

    seen: set[tuple[str, str | None]] = set()  # (片名, 年份)：豆瓣去重用
    for c in colls.values():
        seen.update((norm(p.title), p.year) for p in c.parts)
    seen.update((norm(i.title), i.year) for i in infos if i.source == "tmdb")

    out: list[MediaCandidate] = []
    done: set[str] = set()
    for info in infos:
        cid = (info.collection or {}).get("id") if info.source == "tmdb" else None
        if cid in colls:
            if cid in done:
                continue
            done.add(cid)
            c = colls[cid]
            out.append(MediaCandidate(
                source="tmdb", id=c.id, title=c.name, year=c.parts[0].year, media="movie",
                kind="collection", poster=c.poster, collection=c,
                default_part=default_part(query, c),
            ))
        elif info.source == "douban" and (norm(info.title), info.year) in seen:
            continue
        else:
            out.append(_candidate(info))

    def rank(c: MediaCandidate) -> tuple[int, int]:
        titles = [c.title, c.original_title or ""]
        if c.collection:
            titles += [p.title for p in c.collection.parts]
        return (0 if any(_similar(query, t) for t in titles if t) else 1,
                0 if c.kind in ("collection", "tv") else 1)

    return sorted(out, key=rank)
