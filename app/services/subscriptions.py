"""追剧订阅：定期替用户重搜，有新集数或更高清版本时记一条通知。

检查逻辑：
- 每个订阅按保存的搜索词跑一次 agent（强制全网搜索，不走记忆快速返回）；
- 只看验证有效、且没被判为「片名不符」的链接；
- 最多集数（分享里的视频文件数）超过之前见过的 → 「更新到 N 集」；
- 最高质量分超过之前见过的 → 「出现更高清的版本」；
- 通知写进记忆库，页面轮询读取；配置了 NOTIFY_WEBHOOK 时再推送一份。
"""

import logging

import httpx

from app.models import QuarkLink, SearchRequest, Subscription
from app.services.memory import LinkStore, resource_key

logger = logging.getLogger(__name__)

RES_TEXT = {"2160p": "4K", "1080p": "1080p", "720p": "720p", "SD": "标清"}


def snapshot(links: list[QuarkLink]) -> tuple[int, int, str | None, QuarkLink | None, QuarkLink | None]:
    """(最多集数, 最高质量分, 最高分的清晰度, 集数最多的链接, 质量最高的链接)。"""
    good = [
        lk for lk in links
        if lk.state == "valid" and lk.quality and lk.relevance != "mismatch"
    ]
    if not good:
        return 0, 0, None, None, None
    most = max(good, key=lambda lk: (lk.quality.video_count, lk.quality.score))
    best = max(good, key=lambda lk: (lk.quality.score, lk.quality.video_count))
    return (
        most.quality.video_count, best.quality.score, best.quality.resolution, most, best,
    )


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
    ) -> None:
        self._agent = agent
        self._store = store
        self._webhook = webhook
        self._client = client

    async def baseline(self, resource: str) -> tuple[int, int, str | None]:
        """订阅时的起点：记忆库里这部资源已知的最好情况，避免一订阅就把旧资源当成更新。"""
        episodes, score, res, _, _ = snapshot(await self._store.recall(resource_key(resource)))
        return episodes, score, res

    async def check(self, client_id: str, sub: Subscription) -> list[tuple[str, str, str | None]]:
        """检查一个订阅，返回新产生的通知 (kind, message, share)。"""
        # 强制全网搜索，且记忆里的链接也全部重新验证（集数可能已经变了）
        resp = await self._agent.run(
            SearchRequest(query=sub.query, refresh=True, client_id=client_id), fresh_hours=0
        )
        episodes, score, res, most, best = snapshot(resp.links)
        notes: list[tuple[str, str, str | None]] = []
        if most and episodes > sub.best_episodes:
            before = f"（之前 {sub.best_episodes} 集）" if sub.best_episodes else ""
            notes.append((
                "episodes",
                f"《{sub.resource}》更新到 {episodes} 集{before}：{_link_text(most)}",
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
        await self._store.update_subscription(client_id, sub, notes)
        if notes:
            await self._push(notes)
        return notes

    async def run_once(self, limit: int = 20) -> int:
        """检查最久没检查的一批订阅；单个失败只记日志。返回产生的通知数。"""
        total = 0
        for client_id, sub in (await self._store.list_subscriptions())[:limit]:
            try:
                total += len(await self.check(client_id, sub))
            except Exception:  # 单个订阅失败不影响其余
                logger.exception("订阅检查失败 id=%s", sub.id)
        return total

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
