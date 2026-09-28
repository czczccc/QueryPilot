"""成组订阅（系列电影 / 整部剧全部季）统一挑资源。

系列里每部单独找资源时，「谍影重重 1-5 合集」这种分享标题里没有「3」，第 3 部永远认不出来。
所以成组检查时把整组的候选放在一起：先算出每个分享覆盖哪几部 / 几季，再挑一个——
优先一个就能补齐全部缺失的，其次清晰度、集数。有 LLM 时由它在候选里选并给一句理由，
LLM 不可用（没配 key、额度用完、调用失败）时按同样的规则打分。
"""

import json
import logging
import re
from dataclasses import dataclass

import httpx

from app.models import QuarkLink
from app.services import llm
from app.services.quality import RESOLUTION_RANK
from app.services.relevance import YEAR_RE, _num, seasons_in

logger = logging.getLogger(__name__)

_CN = "一二三四五六七八九十"
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9,
          "x": 10, "ⅰ": 1, "ⅱ": 2, "ⅲ": 3, "ⅳ": 4, "ⅴ": 5, "ⅵ": 6, "ⅶ": 7, "ⅷ": 8, "ⅸ": 9}
_N = rf"(\d{{1,2}}|[{_CN}]{{1,3}})"
# 「1-5部」「第1至5部」「1~5合集」
_RANGE = re.compile(rf"第?{_N}\s*[-~～至到]\s*第?{_N}\s*(?:部|合集|系列|全集)")
_ALL = re.compile(rf"全\s*{_N}\s*部|{_N}\s*部曲")
_SQUASH = re.compile(r"[\s·:：,，.。!！?？'\"“”‘’()（）\[\]【】《》<>_]+")
# 片名后面紧跟的第几部：数字 / 中文数字 / 罗马数字；后面不能再接数字或 4K、1080p、季、集
_AFTER = re.compile(rf"^(?:第)?(\d{{1,2}}|[{_CN}]|ⅰ|ⅱ|ⅲ|ⅳ|ⅴ|ⅵ|ⅶ|ⅷ|ⅸ|viii|vii|iii|ix|iv|vi|ii|x|v|i)"
                    r"(?![0-9a-z]|[kKpP季集])")
_NOT_PART_ONE = re.compile(r"^(?:系列|合集|全集|全|部曲|[-~～至到]|\d(?!\d{3}))")  # 年份除外
_DIGIT_GAP = re.compile(r"(?<=\d)[\s·.。_]+(?=\d)")


def squash(text: str) -> str:
    """去掉空格和标点、转小写；两个数字之间的分隔换成「|」，免得「谍影重重2 4K」粘成「24k」。"""
    return _SQUASH.sub("", _DIGIT_GAP.sub("|", text)).lower()


def _part_no(token: str) -> int | None:
    return _ROMAN.get(token) or _num(token)


def parts_in(text: str, series: str, titles: dict[int, str] | None = None,
             limit: int = 30) -> set[int]:
    """文本里出现的系列第几部：「1-5部」「全5部」「三部曲」范围、「谍影重重3」、
    各部片名（「谍影重重3：最后通牒」或副标题「最后通牒」）；只写系列名本身的算第 1 部。"""
    out: set[int] = set()
    for m in _RANGE.finditer(text):
        a, b = _num(m.group(1)), _num(m.group(2))
        if a and b and a < b <= limit:
            out.update(range(a, b + 1))
    for m in _ALL.finditer(text):
        n = _num(next(g for g in m.groups() if g))
        if n and n <= limit:
            out.update(range(1, n + 1))
    flat, name = squash(text), squash(series)
    if name:
        start = flat.find(name)
        while start >= 0:
            rest = flat[start + len(name):]
            m = _AFTER.match(rest)
            n = _part_no(m.group(1)) if m else None
            if n and n <= limit:
                out.add(n)
            elif not _NOT_PART_ONE.match(rest):
                out.add(1)
            start = flat.find(name, start + 1)
    for index, title in (titles or {}).items():
        main, _, sub = title.replace(":", "：").partition("：")
        for piece in (main, sub):
            key = squash(piece)
            if len(key) >= 2 and key != name and re.search(re.escape(key) + r"(?!\d)", flat):
                out.add(index)
    return out


def link_texts(link: QuarkLink) -> list[str]:
    return [link.share_title or "", link.name, *link.files_preview]


def covered(link: QuarkLink, series: str, tv: bool, titles: dict[int, str] | None = None,
            limit: int = 30) -> set[int]:
    """分享覆盖哪几部（电影）/ 哪几季（剧集）；分享里没写的返回空集。"""
    texts = [t for t in link_texts(link) if t]
    names = [squash(n) for n in [series, *(titles or {}).values()] if squash(n)]
    if not any(n in squash(t) for n in names for t in texts):  # 标题和文件名里都没有片名
        return set()
    out: set[int] = set()
    for text in texts:
        if tv:
            out |= {s for s in seasons_in(text) if s <= limit}
        else:
            out |= parts_in(text, series, titles, limit)
    return out


def part_of_file(f: dict, series: str, titles: dict[int, str] | None = None) -> int | None:
    """分享里的这个文件是系列第几部：先看文件名，再从近到远看所在目录名；认不出返回 None。"""
    name = str(f.get("file_name") or "").rsplit(".", 1)[0]
    for text in [name, *reversed(str(f.get("_path") or "").split("/"))]:
        found = parts_in(text, series, titles) if text else set()
        if len(found) == 1:
            return min(found)
    return None


def year_of_file(f: dict) -> set[str]:
    return set(YEAR_RE.findall(f"{f.get('_path') or ''}/{f.get('file_name') or ''}"))


@dataclass
class Pick:
    link: QuarkLink
    parts: set[int]  # 这个分享能补上的缺失部分
    reason: str  # 一句理由，写进转存记录
    by: str  # "ai" / "rule"


def _res(link: QuarkLink) -> int:
    return RESOLUTION_RANK.get((link.quality and link.quality.resolution) or "", 0)


def _key(link: QuarkLink, cover: set[int], missing: set[int]) -> tuple:
    q = link.quality
    return (len(cover & missing), _res(link), q.video_count if q else 0, q.score if q else 0)


def _nums(parts: set[int], unit: str) -> str:
    return "第 " + "、".join(map(str, sorted(parts))) + f" {unit}"


def rule_pick(cands: list[tuple[QuarkLink, set[int]]], missing: set[int], tv: bool) -> Pick | None:
    """规则挑选：补上的缺失部分越多越好，再比清晰度、视频数、质量分。"""
    cands = [(lk, c) for lk, c in cands if c & missing]
    if not cands:
        return None
    link, cover = max(cands, key=lambda x: _key(x[0], x[1], missing))
    gets = cover & missing
    unit = "季" if tv else "部"
    res = link.quality.resolution if link.quality and link.quality.resolution else None
    whole = "一个资源就补齐了缺的" if gets == missing else "能补上缺的"
    reason = f"{whole}{_nums(gets, unit)}" + (f"，清晰度 {res}" if res else "") + "（按规则挑选）"
    return Pick(link, gets, reason, "rule")


PROMPT = (
    "你在替用户的成组订阅（系列电影的几部，或一部剧的几季）挑一个网盘分享来转存。"
    "missing 是还缺的部 / 季，每个候选的 covers 是它包含的部 / 季。"
    "优先选一个就能补齐全部缺失的（合集、全季包），其次清晰度高、文件完整；"
    "标题看起来不是这部作品的不要选。只输出 JSON："
    "{\"pick\": \"<候选 id，都不合适时为 null>\", \"reason\": \"一句中文理由，20 字以内\"}"
)


class PackChooser:
    """LLM 挑选；不可用或输出不合规时退回 `rule_pick`。"""

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None,
                 timeout: float = 20.0) -> None:
        self._key = api_key
        self._client = client
        self._timeout = timeout

    async def __call__(
        self, cands: list[tuple[QuarkLink, set[int]]], missing: set[int], want: str, tv: bool,
    ) -> Pick | None:
        cands = [(lk, c) for lk, c in cands if c & missing]
        fallback = rule_pick(cands, missing, tv)
        if not self._key or len(cands) < 2 or not llm.enabled():
            return fallback
        payload = {
            "want": want, "kind": "剧集的季" if tv else "系列电影的部",
            "missing": sorted(missing),
            "candidates": [
                {"id": str(i), "title": lk.share_title or lk.name, "files": lk.files_preview[:8],
                 "covers": sorted(c), "resolution": lk.quality.resolution if lk.quality else None,
                 "videos": lk.quality.video_count if lk.quality else None}
                for i, (lk, c) in enumerate(cands[:15])
            ],
        }
        client = self._client or httpx.AsyncClient()
        try:
            message = await llm.chat(client, {
                "model": llm.MODEL,
                "messages": [{"role": "system", "content": PROMPT},
                             {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            }, self._key, self._timeout)
            got = json.loads(message["content"])
            pick, reason = got.get("pick"), str(got.get("reason") or "").strip()
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
            logger.warning("LLM 挑选合集失败（%s），按规则挑选", type(e).__name__)
            return fallback
        finally:
            if self._client is None:
                await client.aclose()
        if pick is None:
            return None
        if not (isinstance(pick, str | int) and str(pick).isdigit()
                and int(pick) < len(payload["candidates"])):
            return fallback
        link, cover = cands[int(pick)]
        unit = "季" if tv else "部"
        reason = (reason[:60] or f"补上缺的{_nums(cover & missing, unit)}") + "（AI 挑选）"
        return Pick(link, cover & missing, reason, "ai")
