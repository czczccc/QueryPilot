"""打开一个夸克分享看里面到底有什么（不需要登录）：逐层展开目录，列出文件，
判断是视频、多集 / 多季合集，还是游戏 / 程序，含哪些季和集。

给搜索 agent 的 inspect_share 工具用：拿不准的资源（标题没写片名、像游戏）打开看一眼。
只读请求、限制展开的目录数和文件数，避免对夸克请求过多。
"""

import httpx

from app.models import QuarkLink
from app.services.classify import episode_no
from app.services.organize import kind_of
from app.services.quark import DETAIL_URL, TOKEN_URL, UA
from app.services.relevance import not_video, seasons_in

MAX_DIRS = 12  # 最多展开几个目录（每个一次请求）
MAX_FILES = 200
MAX_DEPTH = 3


async def list_share(
    share_id: str, client: httpx.AsyncClient, pwd: str | None = None, timeout: float = 8.0,
) -> tuple[str | None, list[dict]] | None:
    """(分享标题, 文件列表)；每个文件 {path, name, size, dir}。打不开返回 None。"""
    headers = {"User-Agent": UA, "Content-Type": "application/json",
               "Referer": f"https://pan.quark.cn/s/{share_id}", "Origin": "https://pan.quark.cn"}
    try:
        resp = await client.post(TOKEN_URL, json={
            "pwd_id": share_id, "passcode": pwd or "", "support_visit_limit_private_share": True,
        }, headers=headers, timeout=timeout)
        stoken = ((resp.json() or {}).get("data") or {}).get("stoken")
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    if not stoken:
        return None
    title: str | None = None
    out: list[dict] = []
    todo: list[tuple[str, str, int]] = [("0", "", 0)]
    opened = 0
    while todo and opened < MAX_DIRS and len(out) < MAX_FILES:
        fid, path, depth = todo.pop(0)
        opened += 1
        try:
            resp = await client.get(DETAIL_URL, params={
                "pwd_id": share_id, "stoken": stoken, "pdir_fid": fid, "force": 0,
                "_page": 1, "_size": 100, "_fetch_share": 1,
            }, headers=headers, timeout=timeout)
            data = resp.json().get("data") or {}
        except (httpx.HTTPError, ValueError, AttributeError):
            continue
        if fid == "0":
            t = (data.get("share") or {}).get("title")
            title = t if isinstance(t, str) else None
        for f in data.get("list") or []:
            if not isinstance(f, dict):
                continue
            name = str(f.get("file_name") or "")
            is_dir = bool(f.get("dir"))
            out.append({"path": path, "name": name, "size": f.get("size") or 0, "dir": is_dir})
            if is_dir and depth + 1 < MAX_DEPTH and f.get("fid"):
                todo.append((str(f["fid"]), f"{path}/{name}", depth + 1))
    return title, out[:MAX_FILES]


def summarize(title: str | None, files: list[dict]) -> dict:
    """分享内容概况：类型（video / pack / program / other）、视频数、季、集号。"""
    plain = [f for f in files if not f["dir"]]
    videos = [f for f in plain if kind_of(f["name"]) == "video"]
    fake = QuarkLink(name=title or "", share="x", source="", time="", share_title=title,
                     files_preview=[f["name"] for f in plain[:30]])
    reason = not_video(fake)
    seasons: set[int] = set()
    for f in files:
        seasons |= seasons_in(f"{f['path']}/{f['name']}")
    episodes = sorted({e for f in videos if (e := episode_no(f["name"], None)) is not None})
    if reason:
        kind = "program"
    elif len(videos) > 1 or len(seasons) > 1:
        kind = "pack"
    elif videos:
        kind = "video"
    else:
        kind = "other"
    return {
        "title": title, "kind": kind, "not_video_reason": reason,
        "videos": len(videos), "files": len(plain), "seasons": sorted(seasons),
        "episodes": episodes[:60],
        "sample": [f"{f['path']}/{f['name']}".lstrip("/") for f in (videos or plain)[:8]],
    }
