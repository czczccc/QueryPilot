"""失败告警：只发给站长的 Telegram（配了 ALERT_TG_BOT_TOKEN 和 ALERT_TG_CHAT_ID 才启用）。

触发：
- 同一订阅自动转存连续失败 N 次（默认 3）；
- 用户的夸克登录失效（自动转存被暂停）；
- 某个搜索源（含 PanSou）连续报错 N 次（默认 5）。

同一类告警（同一订阅 / 同一用户 / 同一搜索源）冷却期内只发一次（默认 6 小时），
恢复正常后计数清零。发送失败只记日志，不影响业务。消息里不带 cookie、key、完整用户 id。
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"


class Alerter:
    def __init__(
        self, token: str, chat_id: str, client: httpx.AsyncClient | None = None,
        save_fails: int = 3, source_fails: int = 5, cooldown_hours: float = 6.0,
    ) -> None:
        self._token = token
        self._chat = chat_id
        self._client = client
        self.save_fails = save_fails
        self.source_fails = source_fails
        self._cooldown = cooldown_hours * 3600
        self._sent: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self.outbox: list[str] = []  # 发出去的文本（测试和排查用，只留最近 50 条）

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat)

    async def send(self, key: str, text: str) -> bool:
        """冷却期内同一 key 不重复发。返回是否发出。"""
        if not self.enabled:
            return False
        now = time.time()
        if self._sent.get(key, 0) > now - self._cooldown:
            return False
        self._sent[key] = now
        self.outbox = [*self.outbox[-49:], text]
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            resp = await client.post(API.format(token=self._token), json={
                "chat_id": self._chat, "text": f"[QueryPilot] {text}",
                "disable_web_page_preview": True})
            if resp.is_error:
                logger.warning("告警发送失败 HTTP %s", resp.status_code)
        except httpx.HTTPError as e:
            logger.warning("告警发送失败 %s", type(e).__name__)
        finally:
            if self._client is None:
                await client.aclose()
        return True

    async def _streak(self, key: str, ok: bool, limit: int, text: str) -> None:
        if ok:
            self._counts.pop(key, None)
            return
        n = self._counts.get(key, 0) + 1
        self._counts[key] = n
        if n >= limit:
            await self.send(key, text.format(n=n))

    async def save_result(self, sub_id: int, name: str, ok: bool, reason: str = "") -> None:
        await self._streak(f"save:{sub_id}", ok, self.save_fails,
                           f"订阅 #{sub_id}《{name}》自动转存连续失败 {{n}} 次：{reason[:120]}")

    async def login_expired(self, owner: str, name: str) -> None:
        who = owner[-6:]  # 只带用户 id 末尾几位，够区分就行
        await self.send(f"login:{owner}",
                        f"用户 …{who} 的夸克登录已失效，《{name}》的自动转存已暂停")

    async def source_result(self, source: str, ok: bool) -> None:
        await self._streak(f"source:{source}", ok, self.source_fails,
                           f"搜索源 {source} 连续报错 {{n}} 次，看看是不是挂了或被封")
