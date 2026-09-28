"""订阅转存的整理规则（借鉴 MoviePilot「整理」的设计思路，未使用其代码）。

- 每集只留一个版本：视频优先于压缩包，清晰度高的优先，同清晰度取体积大的；
- 按集判断：有视频的集不存压缩包；某一集只有压缩包时存它（不然永远补不齐）；
- 无关文件（txt/url/图片…）不存；
- 字幕只跟着选中的集走；
- 标准文件名：「片名 S01E06.mkv」（电影「片名 (年份).mkv」）。

`plan_tidy` 用于「整理已有目录」：只生成计划（移动 / 重命名 / 建议删除），
删除必须由用户在界面上逐个确认后才执行，默认只移动不删除。
"""

import re
from dataclasses import dataclass, field

from app.services.classify import VIDEO_EXT, episode_no, safe_name
from app.services.quality import RESOLUTION_RANK, required_resolution
from app.services.relevance import seasons_in

SUB_EXT = re.compile(r"\.(srt|ass|ssa|sub|idx|sup|vtt)$", re.IGNORECASE)
ARCHIVE_EXT = re.compile(r"\.(zip|rar|7z|tar|gz|001|part\d+\.rar)$", re.IGNORECASE)


def kind_of(name: str) -> str:
    if VIDEO_EXT.search(name):
        return "video"
    if SUB_EXT.search(name):
        return "subtitle"
    if ARCHIVE_EXT.search(name):
        return "archive"
    return "other"


def _ext(name: str) -> str:
    m = re.search(r"(\.[A-Za-z0-9]{1,5})$", name)
    return m.group(1).lower() if m else ""


def version_rank(f: dict) -> tuple:
    """同一集多个版本时的优先级（越大越好）。"""
    name = str(f.get("file_name") or "")
    res = required_resolution(name)
    return (
        {"video": 2, "archive": 1}.get(kind_of(name), 0),
        RESOLUTION_RANK.get(res or "", 0),
        int(f.get("size") or 0),
    )


_LANG = re.compile(r"(\.(?:chs|cht|chi|sc|tc|zh|cn|eng|en|简体|繁体|中英|双语)[^.]{0,8})?"
                   r"\.[A-Za-z0-9]{1,5}$", re.IGNORECASE)


def _suffix(name: str) -> str:
    """扩展名（字幕连语言标记一起保留，如 .chs.srt）。"""
    if kind_of(name) == "subtitle":
        m = _LANG.search(name)
        if m:
            return m.group(0).lower()
    return _ext(name)


def standard_name(title: str, ext: str, season: int | None = None,
                  episode: int | None = None, year: str | None = None) -> str:
    base = safe_name(title) or "未命名"
    if episode is not None:
        return f"{base} S{season or 1:02d}E{episode:02d}{ext}"
    return f"{base} ({year}){ext}" if year else f"{base}{ext}"


_VERSION_TAG = re.compile(r" (?:2160p|1080p|720p|SD)(?=\.[^.]+$)")


def file_resolution(name: str, default: str | None = None) -> str | None:
    """文件名里的清晰度；文件名没写时用分享整体识别出的（`default`）。"""
    return required_resolution(name) or default


def versioned_name(name: str, res: str | None) -> str:
    """标准名被旧版本占着时（洗版存了新版本），在扩展名前加清晰度：「片名 S01E06 2160p.mkv」。"""
    if not res:
        return name
    stem, dot, ext = name.rpartition(".")
    return f"{stem} {res}.{ext}" if dot else f"{name} {res}"


_SXXEYY = re.compile(r"S(\d{1,2})[ ._-]?E\d{1,3}", re.IGNORECASE)


def season_of(f: dict) -> int | None:
    """文件属于第几季：先看文件名（S02E05、「第二季」），再从近到远看所在目录名
    （「Season 2」「第二季」「S02」）；都没写返回 None。

    `_path` 是分享里的目录路径（转存时展开分享记下的），`folder` 是网盘里的目录。"""
    name = str(f.get("file_name") or "")
    m = _SXXEYY.search(name)
    if m:
        return int(m.group(1))
    found = seasons_in(name.rsplit(".", 1)[0])
    if len(found) == 1:
        return min(found)
    for part in reversed(str(f.get("_path") or f.get("folder") or "").split("/")):
        found = seasons_in(part)
        if len(found) == 1:
            return min(found)
    return None


def season_label(f: dict) -> str:
    """认集号用的文件名：多季合集里挑出来的文件记成「S02E05.2160p.mkv」这样，
    后面去重、筛集、重命名、记清晰度都按它来（原文件名可能只有「05.mkv」）。"""
    return str(f.get("_label") or f.get("file_name") or "")


def pick_files(
    files: list[dict], movie: bool, season: int | None = None, offset: int = 0,
    total: int | None = None,
) -> tuple[list[dict], list[dict]]:
    """从分享（已展平）的文件里挑要存的：(要存的, 没选的)。

    按集判断：有视频版本的集只存最好的视频；某一集只有压缩包时存这一集的压缩包
    （否则订阅永远补不齐）。电影同理：有视频存视频，没有才存压缩包。

    剧集按季：多季合集只取订阅那一季（文件名或所在目录写明的季）；没写季的文件只在
    合集里没有别的季时才算。`offset`：前面各季的总集数，这一季按绝对集号编
    （第 2 季写成 13~25）时换算回本季集号。
    """
    names = [str(f.get("file_name") or "") for f in files]
    media = [f for f, n in zip(files, names, strict=True) if kind_of(n) in ("video", "archive")]
    chosen: list[dict] = []
    if movie:
        if media:
            chosen = [max(media, key=version_rank)]  # 视频排在压缩包前面
    else:
        want = season or 1
        labeled = {season_of(f) for f in media} - {None}
        only_this = labeled <= {want}

        def mine(f: dict) -> bool:
            s = season_of(f)
            return s == want or (s is None and only_this)

        def ep_of(f: dict) -> int | None:
            name = str(f.get("file_name") or "")
            if _SXXEYY.search(name):
                return episode_no(name, want, any_ext=True)
            return episode_no(name, None, any_ext=True)

        eps = {id(f): ep_of(f) for f in files if mine(f)}
        found = [e for e in eps.values() if e is not None]
        if offset and found and min(found) > offset and max(found) > (total or offset) and (
            not total or max(found) - offset <= total
        ):
            eps = {k: (e - offset if e is not None else None) for k, e in eps.items()}
        best: dict[int, dict] = {}
        for f in media:
            ep = eps.get(id(f))
            if ep is None:
                continue
            if ep not in best or version_rank(f) > version_rank(best[ep]):
                best[ep] = f
        chosen = [best[e] for e in sorted(best)]
        chosen += [f for f in files if kind_of(str(f.get("file_name") or "")) == "subtitle"
                   and eps.get(id(f)) in best]
        for f in chosen:
            name = str(f.get("file_name") or "")
            res = required_resolution(name)
            f["_label"] = (f"S{want:02d}E{eps[id(f)]:02d}" + (f".{res}" if res else "")
                           + _suffix(name))
    ids = {id(f) for f in chosen}
    return chosen, [f for f in files if id(f) not in ids]


@dataclass
class TidyPlan:
    target: str  # 订阅锁定的目录
    moves: list[dict] = field(default_factory=list)  # {fid, name, from, to_name}
    deletes: list[dict] = field(default_factory=list)  # 建议删除：{fid, name, folder, reason, size}
    untouched: list[dict] = field(default_factory=list)  # 认不出、不动：{fid, name, folder}

    def as_dict(self) -> dict:
        return {"target": self.target, "moves": self.moves, "deletes": self.deletes,
                "untouched": self.untouched}


def plan_tidy(
    entries: list[dict], target: str, title: str, movie: bool,
    season: int | None = None, year: str | None = None,
) -> TidyPlan:
    """`entries`：订阅相关目录（锁定目录 + 以前存过的目录）里的文件，
    每项 {fid, file_name, size, folder(所在目录路径), dir(是否文件夹)}。"""
    plan = TidyPlan(target)
    files = [e for e in entries if not e.get("dir")]
    known = [f for f in files if kind_of(f["file_name"]) in ("video", "archive", "subtitle")]
    chosen, rest = pick_files(known, movie, season)
    keep_ids = {f["fid"] for f in chosen}
    for f in chosen:
        name = f["file_name"]
        ep = None if movie else episode_no(season_label(f), season, any_ext=True)
        to_name = standard_name(title, _suffix(name), season, ep, year)
        # 洗版存的「片名 S01E06 2160p.mkv」已经是标准名，不再改
        if f["folder"] != target or _VERSION_TAG.sub("", name) != to_name:
            plan.moves.append({"fid": f["fid"], "name": name, "from": f["folder"],
                               "to_name": to_name, "size": f.get("size") or 0})
    for f in rest:
        name = f["file_name"]
        other = not movie and season_of(f) not in (None, season or 1)
        ep = None if other else episode_no(name, season, any_ext=True)
        if not movie and ep is None:
            plan.untouched.append({"fid": f["fid"], "name": name, "folder": f["folder"]})
            continue
        reason = "压缩包，已有视频版本" if kind_of(name) == "archive" else "重复版本，已保留更好的"
        plan.deletes.append({"fid": f["fid"], "name": name, "folder": f["folder"],
                             "reason": reason, "size": f.get("size") or 0})
    for f in files:
        if f["fid"] not in keep_ids and kind_of(f["file_name"]) == "other":
            plan.untouched.append({"fid": f["fid"], "name": f["file_name"], "folder": f["folder"]})
    # 整理后会变空的文件夹（分享自带的嵌套目录、以前分错的目录）
    leaving = {m["fid"] for m in plan.moves} | {d["fid"] for d in plan.deletes}
    for d in sorted((e for e in entries if e.get("dir")), key=lambda e: -len(e["path"])):
        inside = [e for e in entries if e["folder"] == d["path"] or
                  e["folder"].startswith(d["path"] + "/")]
        if d["path"] != target and not target.startswith(d["path"] + "/") and all(
            e["fid"] in leaving for e in inside
        ):
            plan.deletes.append({"fid": d["fid"], "name": d["file_name"], "folder": d["folder"],
                                 "reason": "整理后是空文件夹", "size": 0})
            leaving.add(d["fid"])
    return plan
