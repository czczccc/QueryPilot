"""URL 规范化、去重合并与评分排序的单元测试。"""

from app.models import RawSearchResult
from app.services.ranking import dedupe_and_merge, normalize_url, score_and_rank


def make(title: str, url: str, snippet: str = "", provider: str = "tavily",
         relevance: float | None = None) -> RawSearchResult:
    return RawSearchResult(title=title, url=url, snippet=snippet,
                           provider=provider, relevance=relevance)


# ---------------- normalize_url ----------------

def test_lowercases_scheme_and_host():
    assert normalize_url("HTTPS://EXAMPLE.com/Path") == "https://example.com/Path"


def test_drops_fragment():
    assert normalize_url("https://example.com/a#section") == "https://example.com/a"


def test_removes_tracking_params_and_sorts_query():
    url = "https://example.com/?utm_source=x&b=2&a=1&gclid=abc"
    assert normalize_url(url) == "https://example.com/?a=1&b=2"


def test_removes_fbclid():
    assert normalize_url("https://example.com/p?fbclid=xyz&q=1") == "https://example.com/p?q=1"


def test_strips_trailing_slash_on_non_root():
    assert normalize_url("https://example.com/a/b/") == "https://example.com/a/b"


def test_keeps_root_slash():
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_omits_default_ports():
    assert normalize_url("https://example.com:443/a") == "https://example.com/a"
    assert normalize_url("http://example.com:80/a") == "http://example.com/a"
    assert normalize_url("https://example.com:8443/a") == "https://example.com:8443/a"


def test_rejects_non_http():
    assert normalize_url("ftp://example.com/a") is None
    assert normalize_url("javascript:alert(1)") is None


def test_rejects_empty_and_hostless():
    assert normalize_url("") is None
    assert normalize_url("   ") is None
    assert normalize_url("https:///path") is None


# ---------------- dedupe_and_merge ----------------

def test_merges_same_url():
    a = make("标题短", "https://example.com/a")
    b = make("标题比较长的完整标题", "https://example.com/a")
    out, _ = dedupe_and_merge([a, b])
    assert len(out) == 1
    assert out[0].title == "标题比较长的完整标题"


def test_merges_tracking_variants():
    a = make("A", "https://example.com/x?utm_source=1")
    b = make("A", "https://example.com/x")
    assert len(dedupe_and_merge([a, b])[0]) == 1


def test_keeps_distinct_urls():
    a = make("A", "https://example.com/a")
    b = make("B", "https://example.com/b")
    assert len(dedupe_and_merge([a, b])[0]) == 2


def test_drops_invalid_urls():
    a = make("A", "not-a-url")
    b = make("B", "https://example.com/b")
    assert len(dedupe_and_merge([a, b])[0]) == 1


# ---------------- score_and_rank ----------------

def test_score_bounds_and_reason():
    r = make("四人联机游戏推荐", "https://example.com/game", snippet="支持中文的轻量联机游戏",
             relevance=0.9)
    out = score_and_rank([r], keywords=["联机", "中文"])
    assert len(out) == 1
    assert 0 <= out[0].score <= 100
    assert "2/2" in out[0].reason
    assert out[0].sources == ["tavily"]


def test_ranking_is_stable():
    r1 = make("B 标题", "https://example.com/1", snippet="x")
    r2 = make("A 标题", "https://example.com/2", snippet="x")
    out = score_and_rank([r2, r1], keywords=[])
    # 同分时按标题升序
    assert [o.title for o in out] == ["A 标题", "B 标题"]


def test_keyword_coverage_increases_score():
    low = make("无关标题", "https://example.com/l", snippet="无关内容")
    high = make("联机游戏 中文", "https://example.com/h", snippet="四人联机游戏推荐")
    out = score_and_rank([low, high], keywords=["联机", "中文"])
    scores = {o.title: o.score for o in out}
    assert scores["联机游戏 中文"] > scores["无关标题"]


def test_limit_truncates():
    items = [make(f"标题{i}", f"https://example.com/{i}") for i in range(5)]
    assert len(score_and_rank(items, keywords=[], limit=3)) == 3


def test_sources_merge():
    a = make("A", "https://example.com/x", provider="tavily")
    b = make("A", "https://example.com/x?utm_source=1", provider="other")
    out = score_and_rank([a, b], keywords=[])
    assert out[0].sources == ["other", "tavily"]
    assert "来自 2 个来源" in out[0].reason
