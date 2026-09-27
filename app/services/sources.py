"""额外搜索源：Telegram 公开频道 + 可配置的资源站。

- Telegram：抓公开频道的网页预览 `https://t.me/s/<频道>?q=<关键词>`（无需 API/账号），
  逐条消息提取夸克链接、提取码、发布时间。国内服务器通常访问不了 t.me，
  可用 `TG_PROXY` 配置代理。
- 资源站：`EXTRA_SITES` 配置搜索页 URL 模板（`{q}` 占位），抓页面提取夸克链接，
  经过与深度抓取相同的公网 URL 校验（防 SSRF）。
"""

import asyncio
import html as html_lib
import logging
import re
import urllib.parse

import httpx

from app.models import QuarkLink
from app.security import is_safe_fetch_url
from app.services.quark import UA, extract_links_with_pwd, make_entry

logger = logging.getLogger(__name__)

TG_BASE = "https://t.me/s/"
CHANNEL_RE = re.compile(r"^[A-Za-z0-9_]{4,64}$")
_MSG_SPLIT_RE = re.compile(r'<div class="tgme_widget_message_wrap')
_TEXT_RE = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL
)
_TIME_RE = re.compile(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})')
_TAG_RE = re.compile(r"<[^>]+>")


def _plain(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    return html_lib.unescape(_TAG_RE.sub("", text)).strip()


def parse_telegram_page(page: str, channel: str) -> list[QuarkLink]:
    """解析频道网页预览：每条消息取首行作标题、发布时间作置信度依据。"""
    out: list[QuarkLink] = []
    seen: set[str] = set()
    for block in _MSG_SPLIT_RE.split(page)[1:]:
        text_m = _TEXT_RE.search(block)
        if not text_m:
            continue
        # 链接常在 <a href> 里，提取码常在正文里：两者拼起来一起找
        raw = html_lib.unescape(text_m.group(1))
        plain = _plain(text_m.group(1))
        title = next((ln.strip() for ln in plain.splitlines() if ln.strip()), channel)
        title = re.sub(r"^(名称|资源名称|标题)\s*[:：]\s*", "", title)
        time_m = _TIME_RE.search(block)
        for sid, pwd in extract_links_with_pwd(f"{raw}\n{plain}"):
            if sid in seen:
                continue
            seen.add(sid)
            out.append(
                make_entry(title, sid, f"Telegram @{channel}", time_m.group(1) if time_m else "", pwd)
            )
    return out


async def search_telegram(
    kw: str, channels: list[str], client: httpx.AsyncClient, timeout: float = 10.0
) -> list[QuarkLink]:
    """并发搜索多个 Telegram 公开频道；单个频道失败只记日志。"""
    valid = [c for c in channels if CHANNEL_RE.match(c)]
    sem = asyncio.Semaphore(4)

    async def one(channel: str) -> list[QuarkLink]:
        async with sem:
            url = f"{TG_BASE}{channel}?q={urllib.parse.quote(kw)}"
            try:
                resp = await client.get(
                    url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
                    timeout=timeout, follow_redirects=True,
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("[Telegram @%s] 搜索失败: %s", channel, type(exc).__name__)
                return []
            return parse_telegram_page(resp.text, channel)

    results = await asyncio.gather(*(one(c) for c in valid))
    return [link for found in results for link in found]


def render_site_url(template: str, kw: str) -> str | None:
    if "{q}" not in template:
        return None
    return template.replace("{q}", urllib.parse.quote(kw))


async def search_sites(
    kw: str, templates: list[str], client: httpx.AsyncClient, timeout: float = 10.0
) -> list[QuarkLink]:
    """按 URL 模板搜索自定义资源站，页面中出现的夸克链接即为候选。"""
    sem = asyncio.Semaphore(4)

    async def one(template: str) -> list[QuarkLink]:
        url = render_site_url(template, kw)
        if not url or not await is_safe_fetch_url(url):
            logger.warning("资源站模板无效或非公网地址: %s", template[:80])
            return []
        async with sem:
            try:
                resp = await client.get(
                    url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("资源站搜索失败 %s: %s", url[:80], type(exc).__name__)
                return []
        host = urllib.parse.urlparse(url).netloc
        text = resp.text
        out = []
        for sid, pwd in extract_links_with_pwd(text):
            # 标题：链接前面最近的一段文字（去标签），取不到就用关键词
            idx = text.find(sid)
            chunk = re.sub(r"<[^>]*$", "", text[max(0, idx - 300) : idx])  # 去掉被截断的标签
            before = _plain(chunk).splitlines()
            title = next((ln.strip() for ln in reversed(before) if len(ln.strip()) >= 2), kw)
            out.append(make_entry(title, sid, f"资源站 {host}", "", pwd))
        return out

    results = await asyncio.gather(*(one(t) for t in templates))
    return [link for found in results for link in found]
