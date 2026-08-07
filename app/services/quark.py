"""夸克网盘分享链接提取、深度抓取、可达性验证与夸克云搜引擎（httpx 异步）。

逻辑移植自早期桌面原型 quark_search_gui.py v3.1（本地验证过），
重写为异步 HTTP 客户端版本供 Web 服务使用。
"""

import asyncio
import logging
import re
import urllib.parse
from datetime import UTC, datetime

import httpx

from app.models import QuarkLink

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 QueryPilot/0.1"
)

# 夸克分享链接：支持 pan/share/drive 域名变体，分享码 10~20 位
QUARK_RE = re.compile(r"(?:pan|share|drive)?\.?quark\.cn/s/([0-9a-zA-Z]{10,20})", re.IGNORECASE)
# 提取码：匹配“提取码/访问码/pwd/code”后的 4 位字符
PWD_RE = re.compile(
    r"(?:提取码|提取密码|访问码|pwd|passcode|code)\s*[:：为是=]?\s*[（(]?\s*([0-9a-zA-Z]{4})\s*[）)]?",
    re.IGNORECASE,
)
# 深度抓取时跳过的反爬重站
BLOCKED_DOMAINS = ("weibo.com", "tieba.baidu.com", "bilibili.com", "zhihu.com")

QKYUNSO_BASE = "https://qkyunso.com"


def find_pwd(text: str) -> str | None:
    """从文本中找提取码（4 位）。"""
    m = PWD_RE.search(text)
    return m.group(1) if m else None


def extract_links_with_pwd(text: str) -> list[tuple[str, str | None]]:
    """从 HTML/文本提取 [(share_id, pwd)]，pwd 取链接前后 200 字符内的提取码。"""
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for m in QUARK_RE.finditer(text):
        sid = m.group(1)
        if sid in seen:
            continue
        seen.add(sid)
        around = text[max(0, m.start() - 200) : m.end() + 200]
        out.append((sid, find_pwd(around)))
    return out


def make_entry(title: str, sid: str, source: str, pub: str, pwd: str | None = None) -> QuarkLink:
    """构造结果条目（不含验证，验证统一在最后做）。"""
    conf = "中"
    if pub:
        try:
            pd = datetime.strptime(pub[:10], "%Y-%m-%d").replace(tzinfo=UTC)
            days = (datetime.now(UTC) - pd).days
            conf = "高" if days <= 30 else ("中" if days <= 180 else "低")
        except ValueError:
            pass
    return QuarkLink(
        name=(title[:40] or "?"),
        share=sid,
        pwd=pwd,
        source=source,
        time=pub[:10] or "未知",
        conf=conf,
        http=None,
    )


def _headers(referer: str | None = None) -> dict[str, str]:
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer
    return headers


async def deep_fetch_links(
    url: str, client: httpx.AsyncClient, timeout: float = 10.0
) -> list[tuple[str, str | None]]:
    """抓取页面完整 HTML，提取 [(share_id, pwd)]（失败返回空列表）。"""
    try:
        resp = await client.get(url, headers=_headers(), timeout=timeout, follow_redirects=True)
        text = resp.text
    except httpx.HTTPError:
        return []
    return extract_links_with_pwd(text)


async def verify_quark(
    share_id: str, client: httpx.AsyncClient, timeout: float = 12.0
) -> int | None:
    """验证夸克链接可达性（弱验证：静态壳页均返回 200）。"""
    try:
        url = f"https://pan.quark.cn/s/{share_id}"
        resp = await client.get(
            url,
            headers=_headers(referer="https://www.quark.cn/"),
            timeout=timeout,
            follow_redirects=True,
        )
        return resp.status_code
    except httpx.HTTPError:
        return None


# ---------------- 引擎：夸克云搜（始终启用） ----------------
def parse_qkyunso_search(html: str) -> list[dict]:
    items: list[dict] = []
    blocks = re.split(r'data-resource-id="(\d+)"', html)
    for i in range(1, len(blocks), 2):
        block = blocks[i + 1] if i + 1 < len(blocks) else ""
        name_m = re.search(r'data-resource-name="([^"]+)"', block)
        time_m = re.search(r"<span>([0-9]+个月前|更早)</span>", block)
        items.append({
            "id": blocks[i],
            "name": name_m.group(1) if name_m else "?",
            "time": time_m.group(1) if time_m else "未知",
        })
    if not items:
        for m in re.finditer(r'detail\?id=(\d+)[^>]*data-resource-name="([^"]+)"', html):
            items.append({"id": m.group(1), "name": m.group(2), "time": "未知"})
    return items


def parse_qkyunso_detail(html: str) -> tuple[str | None, int | None]:
    links = QUARK_RE.findall(html)
    if not links:
        return None, None
    times = [int(t) for t in re.findall(r"([0-9]+)个月前", html)]
    return links[0], min(times) if times else None


def confidence_from_months(m: int | None) -> str:
    if m is None:
        return "中"
    if m <= 1:
        return "高"
    if m <= 6:
        return "中"
    return "低"


async def search_qkyunso(
    kw: str, client: httpx.AsyncClient, timeout: float = 15.0
) -> list[QuarkLink]:
    """夸克云搜：搜索页 → 详情页（并发）→ 提取分享链接与提取码。"""
    out: list[QuarkLink] = []
    try:
        url = f"{QKYUNSO_BASE}/search?keyword={urllib.parse.quote(kw)}"
        resp = await client.get(url, headers=_headers(referer=f"{QKYUNSO_BASE}/"), timeout=timeout)
        items = parse_qkyunso_search(resp.text)
        logger.info("夸克云搜: 找到 %d 条资源记录", len(items))
        sem = asyncio.Semaphore(5)

        async def fetch_detail(it: dict) -> QuarkLink | None:
            async with sem:
                try:
                    durl = f"{QKYUNSO_BASE}/detail?id={it['id']}"
                    dresp = await client.get(durl, headers=_headers(referer=url), timeout=timeout)
                    sid, months = parse_qkyunso_detail(dresp.text)
                    if not sid:
                        return None
                    pwd = find_pwd(dresp.text)
                    return QuarkLink(
                        name=it["name"],
                        share=sid,
                        pwd=pwd,
                        source=f"夸克云搜 detail?id={it['id']}",
                        time=f"{months}个月前" if months else it["time"],
                        conf=confidence_from_months(months),
                        http=None,
                    )
                except httpx.HTTPError as exc:
                    logger.warning("夸克云搜详情解析失败: %s", exc)
                    return None

        found = await asyncio.gather(*(fetch_detail(it) for it in items[:10]))
        out = [link for link in found if link is not None]
    except httpx.HTTPError as exc:
        logger.warning("夸克云搜搜索失败: %s", exc)
    return out
