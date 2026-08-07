"""URL 规范化、去重合并与确定性评分（纯函数，无网络依赖）。

评分是启发式结果：只表示关键词/内容层面的相关性，不代表真实性或质量保证。
"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models import RawSearchResult, SearchResult


# 追踪参数：utm_* 前缀，以及 gclid / fbclid
def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k.startswith("utm_") or k in ("gclid", "fbclid")


def normalize_url(url: str) -> str | None:
    """规范化 URL；非 http(s) 或无效输入返回 None。

    规则：scheme/host 小写；去掉 fragment；去掉 utm_*/gclid/fbclid 追踪参数；
    查询参数稳定排序；非根路径去掉末尾斜杠。
    """
    url = url.strip()
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None

    scheme = parts.scheme.lower()
    netloc = host
    if parts.port is not None and not (
        (scheme == "http" and parts.port == 80)
        or (scheme == "https" and parts.port == 443)
    ):
        netloc = f"{host}:{parts.port}"

    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _is_tracking(k)
        )
    )
    path = parts.path
    if path and path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, query, ""))  # fragment 丢弃


def dedupe_and_merge(
    results: list[RawSearchResult],
) -> tuple[list[RawSearchResult], dict[str, list[str]]]:
    """按规范 URL 合并：标题/摘要更完整的条目作为主记录。

    只按规范 URL 精确合并，不做模糊标题合并，避免误伤不同页面。
    返回 `(主记录列表, {规范URL: 来源列表})`。
    """
    primary: dict[str, RawSearchResult] = {}
    sources: dict[str, list[str]] = {}
    order: list[str] = []
    for r in results:
        norm = normalize_url(r.url)
        if norm is None:
            continue
        if norm not in primary:
            order.append(norm)
            primary[norm] = r
            sources[norm] = []
        if r.provider not in sources[norm]:
            sources[norm].append(r.provider)
        prev = primary[norm]
        if len(r.title) > len(prev.title):
            prev.title = r.title
        if len(r.snippet) > len(prev.snippet):
            prev.snippet = r.snippet
    return [primary[k] for k in order], sources


def _keyword_coverage(r: RawSearchResult, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    haystack = f"{r.title} {r.snippet}".lower()
    hit = sum(1 for k in keywords if k and k.lower() in haystack)
    return hit / len(keywords)


def _content_completeness(r: RawSearchResult) -> float:
    score = 0.0
    if r.title.strip():
        score += 1 / 3
    if r.snippet.strip():
        score += 1 / 3
    if normalize_url(r.url) is not None:
        score += 1 / 3
    return score


def score_and_rank(
    results: list[RawSearchResult],
    keywords: list[str],
    limit: int = 12,
) -> list[SearchResult]:
    """确定性评分、去重合并、排序并生成匹配理由。

    score = keyword_coverage * 55 + provider_relevance * 30 + content_completeness * 15
    """
    merged, sources = dedupe_and_merge(results)

    out: list[SearchResult] = []
    for r in merged:
        norm = normalize_url(r.url) or ""
        coverage = _keyword_coverage(r, keywords)
        relevance = r.relevance if r.relevance is not None else 0.5
        completeness = _content_completeness(r)
        score = round(coverage * 55 + relevance * 30 + completeness * 15)
        score = max(0, min(100, score))
        srcs = sorted(set(sources.get(norm, [])))
        hit_kw = sum(1 for k in keywords if k and k.lower() in f"{r.title} {r.snippet}".lower())
        reason = f"标题和摘要覆盖 {hit_kw}/{len(keywords)} 个关键词；来自 {len(srcs)} 个来源"
        out.append(
            SearchResult(
                title=r.title,
                url=norm,
                snippet=r.snippet,
                sources=srcs,
                score=score,
                reason=reason,
            )
        )
    out.sort(key=lambda s: (-s.score, s.title))
    return out[:limit]
