"""相关性校验：分享内容是不是用户要找的那部作品。

规则判定（年份、季数、片名）优先；规则判不了的标为 `uncertain`，
agent 有 LLM 时再批量交给 LLM 判断（见 `llm_judge`）。
"""

import json
import logging
import re
from dataclasses import dataclass

import httpx

from app.models import ParsedResource, QuarkLink
from app.services import llm
from app.services.memory import resource_key

logger = logging.getLogger(__name__)


YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_NUM = r"(\d{1,2}|[一二三四五六七八九十]{1,3})"
SEASON_RANGE_RE = re.compile(rf"第{_NUM}\s*[-~至到]\s*{_NUM}季|s(\d{{1,2}})\s*-\s*s?(\d{{1,2}})", re.IGNORECASE)
SEASON_RE = re.compile(
    rf"第{_NUM}季|(?<![a-z])s(\d{{1,2}})(?:e\d{{1,3}})?(?![0-9])|season\s*(\d{{1,2}})",
    re.IGNORECASE,
)


def _num(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if len(text) == 2 and text[0] == "十":
        return 10 + CN_NUM.get(text[1], 0)
    if len(text) == 2 and text[1] == "十":
        return CN_NUM.get(text[0], 0) * 10
    if len(text) == 3 and text[1] == "十":
        return CN_NUM.get(text[0], 0) * 10 + CN_NUM.get(text[2], 0)
    return CN_NUM.get(text)


def seasons_in(text: str) -> set[int]:
    """文本里出现的季数（含「第1-3季」「S01-S03」这类范围）。"""
    out: set[int] = set()
    for m in SEASON_RANGE_RE.finditer(text):
        groups = [g for g in m.groups() if g]
        a, b = _num(groups[0]), _num(groups[1])
        if a and b and a <= b <= 30:
            out.update(range(a, b + 1))
    for m in SEASON_RE.finditer(SEASON_RANGE_RE.sub(" ", text)):
        n = _num(next(g for g in m.groups() if g))
        if n:
            out.add(n)
    return out


def years_in(text: str) -> set[int]:
    return {int(y) for y in YEAR_RE.findall(text)}


BOOK_TITLE_RE = re.compile(r"《([^《》]{1,60})》")
# 搜索词里片名以外的部分（演员名、「的」连接）按这些切开，只作「部分关键词」
_TOKEN_SPLIT = re.compile(r"[\s,，、/|｜的]+")


@dataclass
class RelevanceTarget:
    names: list[str]  # 归一化后的片名/别名/英文名（已去掉季数）
    year: int | None
    season: int | None
    tokens: tuple[str, ...] = ()  # 搜索词按空格 / 「的」切开的片段（如「靳东」「精英律师」）


def _key(text: str) -> str:
    return resource_key(SEASON_RE.sub("", YEAR_RE.sub("", text)))


def query_tokens(*texts: str) -> tuple[str, ...]:
    out: list[str] = []
    for text in texts:
        parts = [_key(p) for p in _TOKEN_SPLIT.split(text)]
        if len([p for p in parts if p]) < 2:  # 只有一段就是整个片名，不算片段
            continue
        out += [p for p in parts if len(p) >= 2 and p not in out]
    return tuple(out)


def share_titles(text: str) -> list[str]:
    """分享标题里用书名号标出的作品名（归一化）；很多分享 / 聚合页都这样写。"""
    return [k for k in (_key(m) for m in BOOK_TITLE_RE.findall(text)) if len(k) >= 2]


def build_target(parsed: ParsedResource, query: str, douban_year: str | None = None) -> RelevanceTarget:
    raw_names = [parsed.resource, *parsed.aliases, parsed.english_name or ""]
    season_set = seasons_in(f"{query} {parsed.resource}")
    names = []
    for n in raw_names:
        key = resource_key(SEASON_RE.sub("", YEAR_RE.sub("", n)))
        if len(key) >= 2 and key not in names:
            names.append(key)
    year_set = years_in(douban_year or "") or years_in(query)
    return RelevanceTarget(
        names=names,
        year=min(year_set) if len(year_set) == 1 else None,
        season=min(season_set) if len(season_set) == 1 else None,
        tokens=tuple(t for t in query_tokens(parsed.resource, query) if t not in names),
    )


def add_aliases(target: RelevanceTarget, infos: list) -> None:
    """用 TMDB / 豆瓣查到的标准中文名、原名补充片名（只认和搜索片名互相包含的条目，免得认错）。"""
    for info in infos:
        keys = [_key(t) for t in (info.title, info.original_title or "") if t]
        if not keys or not any(
            n in keys[0] or keys[0] in n for n in target.names if len(keys[0]) >= 2
        ):
            continue
        for k in keys:
            if len(k) >= 2 and k not in target.names:
                target.names.append(k)


def _title_hits(title: str, target: RelevanceTarget) -> bool:
    """书名号里的作品名是不是要找的那部：互相包含（「鬼吹灯之精绝古城」含「鬼吹灯」，
    「精英律师」在「靳东精英律师」里）；只含演员名这类片段不算。"""
    for n in target.names:
        if n in title or (len(title) >= 2 and title in n):
            return True
    return any(len(t) >= 2 and (title == t or (len(t) >= 3 and t in title))
               for t in target.tokens)


def judge(link: QuarkLink, target: RelevanceTarget) -> None:
    """规则判定并写回 `link.relevance` / `link.relevance_note`。

    以夸克分享页上的真实标题和文件名为准（`share_title` / `files_preview`）；
    搜索结果页的标题（`link.name`，可能是 GitHub 聚合页这类一页上百个链接的页面）
    只在拿不到分享信息时参考，而且那时最多算「待核对」。
    只有 `match` 才算相关；`uncertain` 不进「只看相关」、不触发订阅通知和自动转存。
    """
    primary = [t for t in [link.share_title, *link.files_preview] if t]
    texts = primary or [link.name]
    joined = " ".join(texts)

    if target.year:
        years = years_in(joined)
        if years and all(abs(y - target.year) > 1 for y in years):
            link.relevance, link.relevance_note = "mismatch", f"年份不符：{min(years)}"
            return
    if target.season:
        seasons = seasons_in(joined)
        if seasons and target.season not in seasons:
            link.relevance = "mismatch"
            link.relevance_note = f"季数不符：第{min(seasons)}季"
            return
    if not target.names:
        link.relevance, link.relevance_note = "uncertain", "没能确认是不是这部作品"
        return
    # 分享标题用书名号写明了作品名：以它为准，不是要找的就判不符
    titles = share_titles(link.share_title or "")
    if titles:
        if any(_title_hits(t, target) for t in titles):
            link.relevance, link.relevance_note = "match", None
        else:
            link.relevance, link.relevance_note = "mismatch", f"分享是《{titles[0]}》"
        return
    normalized = [resource_key(t) for t in texts]
    if any(n in t for n in target.names for t in normalized):
        if primary:
            link.relevance, link.relevance_note = "match", None
        else:
            link.relevance, link.relevance_note = "uncertain", "只有搜索页标题，没读到分享内容"
        return
    hits = [t for t in target.tokens if len(t) >= 3 and any(t in x for x in normalized)]
    if primary and hits:
        link.relevance, link.relevance_note = "match", None
    else:  # 可能是别名 / 英文名：留给 LLM 核对，核对前不算相关
        link.relevance, link.relevance_note = "uncertain", "标题里没找到片名"


LLM_PROMPT = (
    "判断每个网盘分享是否是用户要找的作品（同一部作品、同一季；合集包含它也算）。"
    "只输出 JSON：{\"results\": {\"<id>\": true 或 false, ...}}。"
)


async def llm_judge(
    links: list[QuarkLink],
    parsed: ParsedResource,
    target: RelevanceTarget,
    api_key: str,
    client: httpx.AsyncClient,
    timeout: float = 20.0,
) -> int:
    """把 `uncertain` 的链接批量交给 LLM 判定，返回判定成功的条数；失败时保持 uncertain。"""
    todo = [lk for lk in links if lk.relevance == "uncertain"][:20]
    if not todo or not api_key:
        return 0
    payload = {
        "want": {
            "resource": parsed.resource,
            "aliases": parsed.aliases,
            "english_name": parsed.english_name,
            "year": target.year,
            "season": target.season,
        },
        "shares": [
            {"id": str(i), "title": lk.share_title or lk.name, "files": lk.files_preview[:5]}
            for i, lk in enumerate(todo)
        ],
    }
    try:
        message = await llm.chat(
            client,
            {
                "model": llm.MODEL,
                "messages": [
                    {"role": "system", "content": LLM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            api_key,
            timeout,
        )
        results = json.loads(message["content"])["results"]
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("LLM 相关性判定失败（%s），保持待定", type(exc).__name__)
        return 0
    judged = 0
    for i, lk in enumerate(todo):
        verdict = results.get(str(i)) if isinstance(results, dict) else None
        if isinstance(verdict, bool):
            lk.relevance = "match" if verdict else "mismatch"
            lk.relevance_note = "AI 判定" + ("相关" if verdict else "不相关")
            judged += 1
    return judged
