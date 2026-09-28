"""追剧订阅：定期替用户重搜，有新集数或更高清版本时记一条通知。

检查逻辑：
- 每个订阅按保存的搜索词跑一次 agent（强制全网搜索，不走记忆快速返回）；
- 只看验证有效、且没被判为「片名不符」的链接；
- 最多集数（分享里的视频文件数）超过之前见过的 → 「更新到 N 集」；
- 最高质量分超过之前见过的 → 「出现更高清的版本」；
- 通知写进记忆库，页面轮询读取；配置了 NOTIFY_WEBHOOK 时再推送一份；
- 订阅打开了自动转存时，把带来新集 / 更高清的分享交给 `auto_saver` 存进网盘，
  它返回的结果（已转存 / 失败 / 登录失效）也作为通知写进去。
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable

import httpx

from app.models import (
    CollectionInfo,
    CollectionPart,
    ParsedResource,
    QuarkLink,
    SearchRequest,
    Subscription,
    unreleased,
)
from app.services.memory import LinkStore, resource_key
from app.services.pacing import stagger
from app.services.quality import RESOLUTION_RANK, meets_requirement
from app.services.relevance import build_target, judge

logger = logging.getLogger(__name__)

Note = tuple[str, str, str | None]
# (订阅归属, 订阅, 链接, 要的集号 / None 表示不限) → 要追加的通知；
# 实现方负责把网盘里已有的集写回 `sub.saved_episodes`
AutoSaver = Callable[[str, Subscription, QuarkLink, set[int] | None], Awaitable[list[Note]]]

RES_TEXT = {"2160p": "4K", "1080p": "1080p", "720p": "720p", "SD": "标清"}


def snapshot(links: list[QuarkLink]) -> tuple[int, int, str | None, QuarkLink | None, QuarkLink | None]:
    """(最多集数, 最高质量分, 最高分的清晰度, 集数最多的链接, 质量最高的链接)。"""
    good = [
        lk for lk in links
        if lk.state == "valid" and lk.quality and lk.relevance == "match"  # 待核对的不算
    ]
    if not good:
        return 0, 0, None, None, None
    most = max(good, key=lambda lk: (lk.quality.video_count, lk.quality.score))
    best = max(good, key=lambda lk: (lk.quality.score, lk.quality.video_count))
    return (
        most.quality.video_count, best.quality.score, best.quality.resolution, most, best,
    )


_SEASON_SUFFIX = re.compile(r"\s*第[0-9一二三四五六七八九十]+季$")


def _rank(link: QuarkLink) -> int:
    return RESOLUTION_RANK.get((link.quality and link.quality.resolution) or "", 0)


def strip_season(resource: str) -> str:
    """「漫长的季节 第2季」→「漫长的季节」。"""
    return _SEASON_SUFFIX.sub("", resource)


def passes_filters(link: QuarkLink, include: str | None, exclude: str | None) -> bool:
    """订阅的包含 / 排除关键词（空格分隔，不区分大小写），对分享名和文件名生效。"""
    text = " ".join([link.name, link.share_title or "", *link.files_preview]).lower()
    if include and not all(w in text for w in include.lower().split()):
        return False
    return not (exclude and any(w in text for w in exclude.lower().split()))


def check_error_text(exc: Exception) -> str:
    """检查失败的原因，给用户看：不带地址、key 等内部细节。"""
    if isinstance(exc, httpx.TimeoutException):
        return "搜索服务响应超时，稍后会自动重试"
    if isinstance(exc, httpx.HTTPError):
        return "搜索服务暂时不可用，稍后会自动重试"
    return "检查出错，稍后会自动重试"


def _link_text(link: QuarkLink) -> str:
    url = f"https://pan.quark.cn/s/{link.share}"
    return f"{url}（提取码 {link.pwd}）" if link.pwd else url


class SubscriptionWatcher:
    """对订阅逐个重搜并比较；`agent` 需有 `run(SearchRequest)`。"""

    def __init__(
        self,
        agent,
        store: LinkStore,
        webhook: str = "",
        client: httpx.AsyncClient | None = None,
        auto_saver: AutoSaver | None = None,
    ) -> None:
        self._agent = agent
        self.auto_saver = auto_saver
        self.lookup = None  # MetadataLookup：刷新剧集总集数（main 注入）
        self._year_tried: set[int] = set()
        self.stagger = 0.0  # 批量检查时两个订阅之间平均等几秒（错开对夸克和搜索源的请求）
        self._store = store
        self._webhook = webhook
        self._client = client

    async def baseline(self, resource: str) -> tuple[int, int, str | None]:
        """订阅时的起点：记忆库里这部资源已知的最好情况，避免一订阅就把旧资源当成更新。"""
        links = await self._store.recall(resource_key(resource))
        target = build_target(ParsedResource(resource=resource, search_suggestions=[resource]),
                              resource)
        for link in links:  # 记忆库不存相关性，按片名重新判一遍
            judge(link, target)
        episodes, score, res, _, _ = snapshot(links)
        return episodes, score, res

    async def check(
        self, client_id: str, sub: Subscription, sync_save: bool = False
    ) -> list[tuple[str, str, str | None]]:
        """检查一个订阅，返回新产生的通知 (kind, message, share)。

        `sync_save`：刚打开自动转存或手动「立即检查」时为 True——即使没有新集，
        也把目前集数最多的分享里网盘缺的集补齐（已有的跳过）。
        """
        if sub.state == "paused":
            return []
        if sub.media == "movie" and unreleased(sub.release_date, sub.series):  # 还没上映：先不搜
            await self._store.update_subscription(client_id, sub, [])
            return []
        await self._refresh_meta(sub)
        # 强制全网搜索，且记忆里的链接也全部重新验证（集数可能已经变了）
        resp = await self._agent.run(
            SearchRequest(query=sub.query, refresh=True, client_id=client_id), fresh_hours=0
        )
        required = sub.resolution or resp.required_resolution
        # 按订阅的条目（片名、年份、季）再判一遍相关性：只有确认是这部的才通知 / 转存
        target = self._target(sub)
        for lk in resp.links:
            if lk.relevance == "match":
                judge(lk, target)
        links = [lk for lk in resp.links if passes_filters(lk, sub.include, sub.exclude)]
        episodes, score, res, most, best = snapshot(links)
        if sub.media:
            movie = sub.media == "movie"
        else:  # 没识别出条目：单个视频文件按电影处理
            movie = most is not None and most.quality.video_count <= 1
        notes: list[tuple[str, str, str | None]] = []
        if most and episodes > sub.best_episodes:
            if sub.best_episodes == 0:  # 订阅时还没有资源（或只搜到过无效的）
                count = "" if movie else f"，目前 {episodes} 集"
                notes.append((
                    "found", f"《{sub.resource}》有资源了{count}：{_link_text(most)}", most.share,
                ))
            else:
                notes.append((
                    "episodes",
                    (
                        f"《{sub.resource}》更新到 {episodes} 集（之前 {sub.best_episodes} 集）："
                        f"{_link_text(most)}"
                    ),
                    most.share,
                ))
            sub.best_episodes = episodes
        if best and score > sub.best_score:
            if sub.best_score:  # 第一次拿到质量分只作为起点，不打扰
                label = RES_TEXT.get(res or "", "更高质量")
                notes.append((
                    "quality",
                    f"《{sub.resource}》出现更高清的版本（{label}）：{_link_text(best)}",
                    best.share,
                ))
            sub.best_score = score
            sub.best_resolution = res
        if not most:  # 没有确认相关的：拿不准的只提醒一次，请用户自己核对，不自动转存
            notes += await self._maybe(sub, links)
        ok = [lk for lk in links if lk.state == "valid" and lk.quality
              and lk.relevance == "match" and meets_requirement(lk.quality, required)]
        if sub.auto_save and self.auto_saver is not None:
            notes += await self._auto_save(client_id, sub, movie, ok, notes, sync_save)
        done = self._completed(sub, movie, ok)
        if done:
            notes.append(("completed", f"《{sub.resource}》{done}，订阅已完成，移入订阅历史", None))
        await self._store.update_subscription(client_id, sub, notes)
        if done:
            await self._store.archive_subscription(client_id, sub, done)
        if notes:
            await self._push(notes)
        return notes

    @staticmethod
    def _target(sub: Subscription):
        name = strip_season(sub.resource)
        target = build_target(
            ParsedResource(resource=name, search_suggestions=[name]), sub.resource, sub.year,
        )
        if sub.media == "tv" and sub.season:
            target.season = sub.season
        # 剧集订阅的年份是首播年：后面几季、多季合集写的是更晚的年份，只排除更早的
        target.year_floor = sub.media == "tv"
        return target

    async def _maybe(self, sub: Subscription, links: list[QuarkLink]) -> list[Note]:
        unsure = [lk for lk in links if lk.state == "valid" and lk.quality
                  and lk.relevance == "uncertain"]
        if not unsure:
            return []
        pick = max(unsure, key=lambda lk: (lk.quality.video_count, lk.quality.score))
        if pick.share in await self._store.notified_shares(sub.id):
            return []
        title = pick.share_title or pick.name
        message = (f"《{sub.resource}》找到一个可能相关的资源「{title}」，没能确认是这部，"
                   f"请自己核对：{_link_text(pick)}")
        return [("maybe", message, pick.share)]

    async def _auto_save(
        self, client_id: str, sub: Subscription, movie: bool, ok: list[QuarkLink],
        notes: list[Note], sync_save: bool,
    ) -> list[Note]:
        """挑要转存的分享交给 auto_saver；只用满足清晰度要求与过滤条件的有效链接。"""
        assert self.auto_saver is not None
        if not ok:
            return []
        picks: list[QuarkLink] = []
        wanted: set[int] | None = None
        if movie:
            # 电影：第一次出现满足要求的资源时存一次；之后更高清只提醒（开了洗版才再存）
            if not await self._store.has_auto_saved(sub.id):
                picks = [max(ok, key=lambda lk: lk.quality.score)]
        else:
            lack = sub.lack_episodes
            if lack != []:  # 范围内还没存齐
                wanted = set(lack) if lack is not None else None
                fullest = max(ok, key=lambda lk: (lk.quality.video_count, lk.quality.score))
                by_share = {lk.share: lk for lk in ok}
                picks = [by_share[n[2]] for n in notes if n[2] in by_share]
                # 立即检查，或者分享的集数已经覆盖到缺的集：补齐
                if sync_save or (lack and fullest.quality.video_count >= min(lack)):
                    picks.insert(0, fullest)
        out: list[Note] = []
        for link in {lk.share: lk for lk in picks}.values():
            out += await self._save_one(client_id, sub, link, wanted)
            if wanted is not None and not sub.lack_episodes:
                break
        if not picks or not movie:
            out += await self._upgrade(client_id, sub, movie, ok)
        return out

    async def _save_one(
        self, client_id: str, sub: Subscription, link: QuarkLink, wanted: set[int] | None,
    ) -> list[Note]:
        assert self.auto_saver is not None
        try:
            return await self.auto_saver(client_id, sub, link, wanted)
        except Exception:  # 自动转存出错不影响通知本身
            logger.exception("订阅自动转存异常 id=%s", sub.id)
            return []

    async def _upgrade(
        self, client_id: str, sub: Subscription, movie: bool, ok: list[QuarkLink],
    ) -> list[Note]:
        """洗版：已存的集清晰度没到目标时，把清晰度比最差那集更高的最好分享再存一次
        （只存能升级的集，旧版本不删，由用户在「整理」里确认删除）。每次检查最多一个分享。"""
        better = sub.upgradable(movie)
        if not better or (movie and not await self._store.has_auto_saved(sub.id)):
            return []
        worst = min(better.values())
        up = [lk for lk in ok if _rank(lk) > worst]
        if not up:
            return []
        link = max(up, key=lambda lk: (_rank(lk), lk.quality.video_count, lk.quality.score))
        return await self._save_one(client_id, sub, link, None if movie else set(better))

    @staticmethod
    def _completed(sub: Subscription, movie: bool, ok: list[QuarkLink]) -> str | None:
        """剧集这一季完成了就返回原因（写进订阅历史），否则 None。

        开了自动转存：范围内每一集网盘里都有了才算完成；没开：出现一个集数覆盖整季的有效资源。
        电影不自动完成（之后出现更高清版本还会提醒），可以手动完成。
        """
        if movie and sub.auto_save and sub.upgrade_done:
            return f"已洗版到 {RES_TEXT.get(sub.upgrade_to or '2160p', sub.upgrade_to)}"
        if movie or sub.media != "tv" or not sub.wanted:
            return None
        if sub.auto_save:
            if sub.lack_episodes != [] or sub.upgradable(False):
                return None
            label = RES_TEXT.get(sub.upgrade_to or "2160p", sub.upgrade_to)
            return f"已集齐 {len(sub.wanted)} 集" + (f"，全部达到 {label}" if sub.upgrade else "")
        if any(lk.quality.video_count >= sub.wanted[-1] for lk in ok):
            return f"全 {sub.wanted[-1]} 集资源已出齐"
        return None

    async def _refresh_meta(self, sub: Subscription) -> None:
        """剧集总集数与播出日历随 TMDB / 豆瓣更新（每天最多一次；手动改过总集数的不改集数）。"""
        if self.lookup is None or sub.media != "tv" or not (sub.tmdb_id or sub.douban_id):
            return
        # 还没有季年份的（这个字段上线前建的订阅）不等 24 小时，下次检查就补一次
        if not await self._store.meta_due(sub.id, 24):
            if sub.season_year or not sub.tmdb_id or sub.id in self._year_tried:
                return
            self._year_tried.add(sub.id)
        schedule = getattr(self.lookup, "schedule", None)
        if sub.tmdb_id and schedule is not None:
            eps = await schedule(sub.tmdb_id, sub.season or 1)
            if eps:
                sub.schedule = eps
                await self._store.set_schedule(sub.id, eps)
                years = sorted(e.air_date[:4] for e in eps if e.air_date)
                if years:
                    sub.season_year = years[0]
                last = max(e.episode for e in eps)
                if not sub.manual_total and last > (sub.total_episodes or 0):
                    sub.total_episodes = last
        if sub.manual_total:
            return
        # 查询失败时 lookup 自己返回空列表，不影响检查
        infos = await self.lookup(strip_season(sub.resource), sub.year, fresh=True)
        for info in infos:
            if info.id in (sub.tmdb_id, sub.douban_id):
                sub.season_year = info.season_years.get(sub.season or 1) or sub.season_year
                total = info.episodes.get(sub.season or 1)
                if total and total > (sub.total_episodes or 0):
                    sub.total_episodes = total
                return

    async def add_part(
        self, owner: str, info: CollectionInfo, part: CollectionPart, settings: dict,
    ) -> Subscription | None:
        """把系列里的一部加成订阅（共用系列的规则）；已经订阅着的返回 None。"""
        key = resource_key(part.title)
        for _, x in await self._store.list_subscriptions(owner):
            if x.tmdb_id == part.id or resource_key(x.resource) == key:
                return None
        fields = {k: v for k, v in settings.items() if v not in (None, False, "")}
        sub = await self._store.add_subscription(
            owner, part.title, part.title, await self.baseline(part.title),
            media="movie", year=part.year, tmdb_id=part.id, poster=part.poster or info.poster,
            collection_id=info.id, collection_name=info.name, collection_index=part.index,
            series=True, release_date=part.release_date, **fields,
        )
        if sub is None:
            return None
        # 还没上映的显示「待定」，上映后才开始搜；其余等第一次搜索
        state = "active" if unreleased(part.release_date, True) else "new"
        return await self._store.edit_subscription(owner, sub.id, state=state) or sub

    async def join_new_parts(self) -> int:
        """订阅的系列每天查一次 TMDB：更新各部上映日期；开了「新作自动加入」的，
        出了新的一部就订阅并通知。返回新加的部数。"""
        fetch = getattr(self.lookup, "collection", None)
        if fetch is None:
            return 0
        added, now = 0, time.time()
        for c in await self._store.list_collections():
            if (c["checked"] or 0) > now - 86400:
                continue
            owner, cid = c["client_id"], c["collection_id"]
            await self._store.set_collection(owner, cid, checked=now)
            info = await fetch(cid, fresh=True)
            if info is None:
                continue
            # 已订阅的各部：更新上映日期（定档了才开始搜）
            dates = {p.id: p.release_date for p in info.parts}
            for _, x in await self._store.list_subscriptions(owner):
                if x.collection_id == cid and x.tmdb_id in dates \
                        and dates[x.tmdb_id] != x.release_date:
                    await self._store.edit_subscription(owner, x.id,
                                                        release_date=dates[x.tmdb_id])
            if not c["auto_join"]:
                continue
            new = [p for p in info.parts if p.id not in c["known"]]
            for part in new:
                sub = await self.add_part(owner, info, part, c["settings"])
                if sub is not None:
                    added += 1
                    await self._store.add_notification(
                        owner, sub, "series_new",
                        f"《{info.name}》系列新增《{part.title}》，已为你订阅")
            if new:
                await self._store.set_collection(owner, cid, known=[
                    *c["known"], *(p.id for p in new)])
        return added

    async def run_once(self, limit: int = 20) -> int:
        """检查最久没检查的一批订阅（暂停的跳过）；单个失败只记日志。返回产生的通知数。"""
        try:
            total = await self.join_new_parts()
        except Exception:  # 查系列新作失败不影响订阅检查
            logger.exception("系列新作检查失败")
            total = 0
        subs = [x for x in await self._store.list_subscriptions() if x[1].state != "paused"]
        for i, (client_id, sub) in enumerate(subs[:limit]):
            if i and self.stagger > 0:
                await asyncio.sleep(stagger(self.stagger))
            try:
                total += len(await self.check(client_id, sub))
            except Exception as exc:  # 单个订阅失败不影响其余
                logger.exception("订阅检查失败 id=%s", sub.id)
                await self.failed(sub, exc)
        return total

    async def failed(self, sub: Subscription, exc: Exception) -> None:
        """检查出错：把原因（不带内部细节）记到订阅上，卡片显示「上次检查失败：…」。"""
        try:
            await self._store.set_check_error(sub.id, check_error_text(exc))
        except Exception:
            logger.exception("记录订阅检查失败原因出错 id=%s", sub.id)

    async def _push(self, notes: list[tuple[str, str, str | None]]) -> None:
        if not self._webhook:
            return
        text = "\n".join(message for _, message, _ in notes)
        try:
            client = self._client or httpx.AsyncClient(timeout=10.0)
            try:
                # 通用 {"text"}（Slack、Bark 等）+ 飞书机器人的 msg_type/content 格式
                await client.post(
                    self._webhook,
                    json={"text": text, "msg_type": "text", "content": {"text": text}},
                )
            finally:
                if self._client is None:
                    await client.aclose()
        except httpx.HTTPError:
            logger.warning("订阅通知推送失败")
