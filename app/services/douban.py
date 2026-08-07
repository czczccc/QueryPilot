"""豆瓣链接识别：从豆瓣 subject 链接提取影视资源信息。

豆瓣 PC 端对无 Cookie 请求返回 JS 挑战页，移动版 m.douban.com 可直接抓取，
从 `og:title`（格式如「漫长的季节 (2023) - 电视剧」）提取片名/年份/类型。
"""

import logging
import re

import httpx

logger = logging.getLogger(__name__)

DOUBAN_RE = re.compile(
    r"https?://(?:(?:www|m)\.|(?:movie|book|music)\.)?douban\.com/"
    r"(?:(?:movie|tv)/)?subject/(\d+)",
    re.IGNORECASE,
)

UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)


def extract_douban_id(text: str) -> str | None:
    """从任意文本中提取豆瓣 subject id；不是豆瓣链接返回 None。"""
    m = DOUBAN_RE.search(text)
    return m.group(1) if m else None


def parse_douban_html(html: str) -> dict:
    """从移动版页面提取 {title, year, kind}；无法识别返回空 dict。"""
    og = re.search(r'property="og:title" content="([^"]+)"', html)
    if not og:
        return {}
    parts = [p.strip() for p in og.group(1).split(" - ") if p.strip()]
    title = parts[0] if parts else ""
    kind = parts[1] if len(parts) > 1 else ""
    year = None
    m = re.match(r"^(.+?)\s*\((\d{4})\)$", title)
    if m:
        title, year = m.group(1), m.group(2)
    if not title:
        return {}
    return {"title": title, "year": year, "kind": kind}


async def fetch_douban_meta(
    subject_id: str, client: httpx.AsyncClient, timeout: float = 10.0
) -> dict | None:
    """抓取移动版豆瓣页并解析；失败返回 None。"""
    url = f"https://m.douban.com/movie/subject/{subject_id}/"
    try:
        resp = await client.get(
            url,
            headers={"User-Agent": UA_MOBILE, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=timeout,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return None
        meta = parse_douban_html(resp.text)
        return meta if meta.get("title") else None
    except httpx.HTTPError as exc:
        logger.warning("豆瓣解析失败 subject_id=%s error=%s", subject_id, type(exc).__name__)
        return None
