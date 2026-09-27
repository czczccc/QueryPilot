"""资源质量识别：从分享文件列表（文件名 + 体积）推断分辨率、HDR、片源、集数、字幕。

纯规则、无网络，便于单测。输入为夸克 detail 接口返回的文件条目
（关心 `file_name`、`size`、`dir`、`obj_category` 字段），外加分享标题。
"""

import re

from app.models import RESOLUTION_RANK, QualityInfo

VIDEO_EXTS = {
    "mkv", "mp4", "avi", "ts", "m2ts", "rmvb", "rm", "flv", "mov", "wmv", "iso", "webm", "mpg",
}
SUB_EXTS = {"srt", "ass", "ssa", "sup", "vtt", "idx"}

# 前后不能紧挨字母数字，避免 "14k"、"x2160p0" 这类误伤
_B = r"(?<![0-9a-z])"
_E = r"(?![0-9a-z])"
# 分辨率只排除前置数字：常见写法 "BD1080P"、"HD2160p" 前面紧挨字母
_BD = r"(?<![0-9])"

RESOLUTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("2160p", re.compile(rf"{_BD}(?:2160p|4k|3840x2160){_E}|{_B}uhd{_E}|4k超清|超高清", re.IGNORECASE)),
    ("1080p", re.compile(rf"{_BD}(?:1080[pi]|1920x1080){_E}|{_B}fhd{_E}|全高清", re.IGNORECASE)),
    ("720p", re.compile(rf"{_BD}(?:720p|1280x720){_E}", re.IGNORECASE)),
    ("SD", re.compile(rf"{_BD}(?:480p|360p|576p){_E}|{_B}dvdrip{_E}|标清", re.IGNORECASE)),
]
HDR_RE = re.compile(rf"{_B}(?:hdr10\+?|hdr|dv|dovi|dolby[ ._-]?vision){_E}|杜比视界", re.IGNORECASE)
CODEC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("H.265", re.compile(rf"{_B}(?:hevc|x265|h\.?265){_E}", re.IGNORECASE)),
    ("H.264", re.compile(rf"{_B}(?:avc|x264|h\.?264){_E}", re.IGNORECASE)),
    ("AV1", re.compile(rf"{_B}av1{_E}", re.IGNORECASE)),
]
SOURCE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("REMUX", re.compile(rf"{_B}remux{_E}|原盘", re.IGNORECASE)),
    ("BluRay", re.compile(rf"{_B}(?:blu-?ray|bdrip|bd)(?![a-z])|蓝光", re.IGNORECASE)),
    ("WEB-DL", re.compile(rf"{_B}(?:web-?dl|webrip|web){_E}", re.IGNORECASE)),
    ("HDTV", re.compile(rf"{_B}hdtv{_E}", re.IGNORECASE)),
]
# 枪版/抢先版：TS 只在去掉扩展名的文件名里匹配，避免与 .ts 扩展名冲突
LOW_QUALITY_RE = re.compile(rf"{_B}(?:hd-?ts|ts|hd-?tc|tc|cam|hdcam){_E}|枪版|抢先版", re.IGNORECASE)
SUB_NAME_RE = re.compile(r"中字|字幕|简中|繁中|双语|中英")

GB = 1024**3

RESOLUTION_SCORE = {"2160p": 40, "1080p": 30, "720p": 15, "SD": 5}
SOURCE_SCORE = {"REMUX": 10, "BluRay": 7, "WEB-DL": 5, "HDTV": 2}


def _split_ext(name: str) -> tuple[str, str]:
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or len(ext) > 5:
        return name, ""
    return stem, ext.lower()


def _first_match(patterns: list[tuple[str, re.Pattern[str]]], texts: list[str]) -> str | None:
    """按优先级返回首个命中的标签（高优先级模式先于低优先级）。"""
    for label, pattern in patterns:
        if any(pattern.search(t) for t in texts):
            return label
    return None


def _resolution_from_size(size: int) -> str:
    """单视频文件体积兜底推断（电影场景；剧集不走这里）。"""
    if size >= 15 * GB:
        return "2160p"
    if size >= 3 * GB:
        return "1080p"
    if size >= 800 * 1024**2:
        return "720p"
    return "SD"


def parse_quality(files: list[dict], title: str = "") -> QualityInfo:
    """根据文件列表和标题推断资源质量。"""
    videos: list[tuple[str, int]] = []
    names: list[str] = [title] if title else []
    has_sub = False
    for f in files:
        name = str(f.get("file_name") or "")
        if not name:
            continue
        stem, ext = _split_ext(name)
        names.append(stem)
        if f.get("dir"):
            continue
        if ext in SUB_EXTS:
            has_sub = True
        elif ext in VIDEO_EXTS or f.get("obj_category") == "video":
            size = f.get("size")
            videos.append((stem, size if isinstance(size, int) and size > 0 else 0))

    resolution = _first_match(RESOLUTION_PATTERNS, names)
    resolution_guessed = False
    if resolution is None and len(videos) == 1 and videos[0][1]:
        resolution = _resolution_from_size(videos[0][1])
        resolution_guessed = True

    info = QualityInfo(
        resolution=resolution,
        resolution_guessed=resolution_guessed,
        hdr=any(HDR_RE.search(n) for n in names),
        codec=_first_match(CODEC_PATTERNS, names),
        source=_first_match(SOURCE_PATTERNS, names),
        low_quality=any(LOW_QUALITY_RE.search(n) for n in names),
        video_count=len(videos),
        size_bytes=sum(s for _, s in videos) or None,
        has_subtitle=has_sub or any(SUB_NAME_RE.search(n) for n in names),
    )
    info.score = quality_score(info)
    return info


def quality_score(info: QualityInfo) -> int:
    """质量分（0~60 左右），用于同有效状态内排序。未识别分辨率给中性分。"""
    score = RESOLUTION_SCORE.get(info.resolution or "", 10)
    if info.resolution_guessed:
        score -= 5  # 体积推断不如文件名可靠
    score += SOURCE_SCORE.get(info.source or "", 0)
    if info.hdr:
        score += 5
    if info.has_subtitle:
        score += 3
    if info.low_quality:
        score -= 40
    return max(score, 0)


def required_resolution(text: str | None) -> str | None:
    """从用户的清晰度要求（如「4K HDR」「1080P」）解析出最低分辨率。"""
    if not text:
        return None
    return _first_match(RESOLUTION_PATTERNS, [text])


def meets_requirement(info: QualityInfo | None, required: str | None) -> bool:
    """有效链接是否满足清晰度要求：枪版一律不满足；有要求时分辨率需已识别且不低于要求。"""
    if info is None or info.low_quality:
        return False
    if required is None:
        return True
    if info.resolution is None:
        return False
    return RESOLUTION_RANK[info.resolution] >= RESOLUTION_RANK[required]
