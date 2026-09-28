"""订阅检查摘要 last_check：搜到几条、筛掉原因、存了几集，一句话 reason。"""

from tests.test_autosave import OWNER
from tests.test_pack_pick import BOURNE_LINKS, link, watcher_with

C = OWNER


async def test_summary_counts_and_reason():
    links = [*BOURNE_LINKS, link("dead00001", "谍影重重 失效")]
    links[-1].state = "invalid"
    other = link("unsure001", "更新中")
    other.relevance, other.relevance_note = "uncertain", "标题里没找到片名"
    store, _, watcher, _ = watcher_with([*links, other])
    sub = await store.add_subscription(C, "谍影重重", "谍影重重", media="movie", auto_save=True)
    await watcher.check(C, sub)
    [(_, now)] = await store.list_subscriptions(C)
    lc = now.last_check
    assert lc["found"] == 6 and lc["dead"] == 1 and lc["valid"] == 5
    assert lc["reasons"]["标题里没找到片名"] == 2  # 碟中谍合集 + 更新中 and lc["saved"] == 1
    assert lc["reason"].startswith("搜到 6 条") and lc["reason"].endswith("已转存")


async def test_summary_unreleased():
    store, _, watcher, _ = watcher_with([])
    sub = await store.add_subscription(C, "新片", "新片", media="movie", series=True,
                                       collection_id="1")
    await watcher.check(C, sub)
    [(_, now)] = await store.list_subscriptions(C)
    assert now.last_check["reason"] == "还没上映 / 开播，先不搜"
