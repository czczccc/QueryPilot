"""转存自动分类：判断电影/电视剧/动漫/综艺/纪录片与地区，得出网盘里的目标目录。

有 LLM 时让它看分享标题与文件名判断；LLM 不可用或出错时用规则兜底：
- 类型：文件名里的 S01E01 / 第N集 / EP01，或视频文件 ≥3 个 → 电视剧；
  标题里的动漫/番剧、综艺、纪录片等关键词优先；
- 地区：国产/国语/大陆/港/台 → 华语，美剧/英剧/Netflix → 欧美，韩剧/日剧 → 日韩；
  都没有时，标题只有中文、文件名也无英文片名 → 华语，否则 → 其他。

目录结构（根目录可配置，默认 QueryPilot）：
    /QueryPilot/电影/{华语,欧美,日韩,其他}
    /QueryPilot/电视剧/{国产剧,欧美剧,日韩剧,其他}/片名 (年份)
    /QueryPilot/动漫/片名 (年份)   /QueryPilot/综艺/片名   /QueryPilot/纪录片
"""

import json
import logging
import re
from dataclasses import dataclass

import httpx

from app.services import llm

logger = logging.getLogger(__name__)

KINDS = {"movie": "电影", "tv": "电视剧", "anime": "动漫", "variety": "综艺", "documentary": "纪录片"}
MOVIE_REGION = {"cn": "华语", "west": "欧美", "jpkr": "日韩", "other": "其他"}
TV_REGION = {"cn": "国产剧", "west": "欧美剧", "jpkr": "日韩剧", "other": "其他"}

VIDEO_EXT = re.compile(r"\.(mkv|mp4|avi|ts|m2ts|rmvb|mov|wmv|flv|iso)$", re.IGNORECASE)
EPISODE = re.compile(
    r"S\d{1,2}E\d{1,3}|\bE[Pp]?\d{2,3}\b|第\s*[0-9一二三四五六七八九十百]+\s*[集话話]|全\d+集|更新至",
    re.IGNORECASE,
)
YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20[0-4]\d)(?!\d)")
BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')

KIND_WORDS = [
    ("anime", ("动漫", "动画", "番剧", "国漫", "日漫", "新番", "Anime")),
    ("variety", ("综艺", "真人秀", "脱口秀", "选秀")),
    ("documentary", ("纪录片", "纪录", "BBC", "Documentary", "国家地理")),
]
REGION_WORDS = [
    ("jpkr", ("韩剧", "日剧", "韩国", "日本", "韩影", "日影", "韩综", "日韩")),
    ("west", ("美剧", "英剧", "欧美", "Netflix", "HBO", "英美", "美国", "英国")),
    ("cn", ("国产", "国语", "大陆", "内地", "华语", "港剧", "台剧", "TVB", "港片", "国剧")),
]


@dataclass
class Category:
    kind: str  # movie / tv / anime / variety / documentary
    region: str  # cn / west / jpkr / other
    title: str
    year: str | None = None
    by: str = "rules"  # rules / llm

    def folder(self, root: str = "QueryPilot") -> str:
        """网盘里的目标目录路径（以 / 开头）。"""
        parts = [safe_name(root) or "QueryPilot", KINDS[self.kind]]
        name = safe_name(f"{self.title} ({self.year})" if self.year else self.title)
        if self.kind == "movie":
            parts.append(MOVIE_REGION[self.region])
        elif self.kind == "tv":
            parts.append(TV_REGION[self.region])
            parts.append(name)
        elif self.kind in ("anime", "variety"):
            parts.append(name)
        return "/" + "/".join(p for p in parts if p)

    def label(self) -> str:
        if self.kind == "movie":
            return f"{MOVIE_REGION[self.region]}电影"
        if self.kind == "tv":
            return TV_REGION[self.region]
        return KINDS[self.kind]


def safe_name(name: str) -> str:
    """网盘目录名：去掉非法字符，限长。"""
    return BAD_CHARS.sub(" ", name).strip(" .")[:60]


def clean_title(title: str) -> str:
    """从分享标题里取片名：去掉括号标注、清晰度等尾巴。"""
    t = re.sub(r"[【\[(（].*?[】\])）]", " ", title)
    t = re.split(r"[|｜丨]|\s{2,}|4K|1080[Pp]|2160[Pp]|全\d+集|更新至|完结", t)[0]
    t = YEAR.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip(" .-_") or title.strip()


def classify_rules(title: str, files: list[str]) -> Category:
    text = " ".join([title, *files[:30]])
    videos = [f for f in files if VIDEO_EXT.search(f)]
    kind = next((k for k, words in KIND_WORDS if any(w in title for w in words)), None)
    if kind is None:
        episodic = sum(1 for f in files if EPISODE.search(f)) >= 2 or bool(EPISODE.search(title))
        kind = "tv" if episodic or len(videos) >= 3 else "movie"
    region = next((r for r, words in REGION_WORDS if any(w in text for w in words)), None)
    if region is None:
        has_cjk = bool(re.search(r"[一-鿿]", title))
        latin_names = any(re.search(r"[A-Za-z]{3,}\.[A-Za-z]{3,}", f) for f in files)
        region = "cn" if has_cjk and not latin_names else "other"
    year_match = YEAR.search(title) or YEAR.search(" ".join(files[:10]))
    return Category(kind, region, clean_title(title), year_match.group(1) if year_match else None)


LLM_PROMPT = """你是影视资源分类器。根据网盘分享标题和文件名，判断这是什么资源。
只输出 JSON：{"kind": "movie|tv|anime|variety|documentary", "region": "cn|west|jpkr|other",
"title": "干净的中文片名（没有就用原名）", "year": "首播/上映年份，不确定填 null"}
说明：tv=电视剧；anime=动画/动漫（含国漫、日漫）；variety=综艺；region: cn=中国大陆/港台，
west=欧美，jpkr=日本韩国，other=其他地区。"""


async def classify_llm(
    title: str, files: list[str], api_key: str, client: httpx.AsyncClient, timeout: float = 15.0
) -> Category | None:
    payload = {"share_title": title, "files": files[:30], "video_count": sum(
        1 for f in files if VIDEO_EXT.search(f))}
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
        data = json.loads(message["content"])
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("LLM 资源分类失败（%s），使用规则", type(exc).__name__)
        return None
    kind, region = data.get("kind"), data.get("region")
    if kind not in KINDS or region not in MOVIE_REGION:
        return None
    name = data.get("title") if isinstance(data.get("title"), str) else ""
    year = str(data.get("year")) if data.get("year") else None
    return Category(
        kind, region, name.strip() or clean_title(title),
        year if year and YEAR.fullmatch(year) else None, by="llm",
    )


class Classifier:
    """转存时调用：`await classifier(title, files)` → Category。"""

    def __init__(self, api_key: str = "", client: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._client = client

    async def __call__(self, title: str, files: list[str]) -> Category:
        if self._api_key:
            client = self._client or httpx.AsyncClient(timeout=15.0)
            try:
                got = await classify_llm(title, files, self._api_key, client)
            finally:
                if self._client is None:
                    await client.aclose()
            if got:
                return got
        return classify_rules(title, files)
