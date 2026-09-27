"""一键转存：把分享链接里的文件保存到部署者自己的夸克网盘。

凭证安全（关键决策）：
- 夸克登录 cookie 只从服务器环境变量 / 本地 .env 的 `QUARK_COOKIE` 读取，
  不写数据库、不写日志、不出现在任何接口响应里，也不接受从网页提交；
- cookie 只发往代码里写死的夸克域名（pan.quark.cn / drive-pc.quark.cn）；
- 转存接口另需口令 `SAVE_TOKEN`（请求头 `X-Save-Token`，常量时间比较），
  否则公开部署时任何访客都能往你的网盘里存东西；
- 两个变量任一未配置时功能整体关闭。

流程（来自夸克网页版前端）：分享页 token → detail 取顶层文件的 fid 与 share_fid_token
→ `sharepage/save` 提交转存任务 → 轮询 `task` 直到完成。
"""

import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.services.classify import decide_folder, episode_key
from app.services.quark import DETAIL_URL, TOKEN_URL, UA

logger = logging.getLogger(__name__)

SAVE_URL = "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/save"
TASK_URL = "https://drive-pc.quark.cn/1/clouddrive/task"
PATH_LIST_URL = "https://drive-pc.quark.cn/1/clouddrive/file/info/path_list"
MKDIR_URL = "https://drive-pc.quark.cn/1/clouddrive/file"
LIST_URL = "https://drive-pc.quark.cn/1/clouddrive/file/sort"
COMMON_PARAMS = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}

# 夸克返回的常见错误码 → 给用户看的说明
ERROR_TEXT = {
    31001: "夸克登录已过期，请在服务器 .env 里更新 QUARK_COOKIE",
    41006: "分享已失效",
    41008: "提取码错误",
    41012: "分享已失效",
    32003: "网盘空间不足",
}


class SaveError(Exception):
    """转存失败，`str(e)` 可直接展示给用户（不含任何凭证）。"""


class LoginExpiredError(SaveError):
    """cookie 已失效（夸克错误码 31001）。"""


@dataclass
class SaveResult:
    task_id: str
    file_count: int
    title: str | None
    done: bool  # False 表示任务已提交，但轮询期间还没完成（夸克会在后台继续）
    folder: str | None = None  # 自动分类后存入的目录（如 /QueryPilot/电视剧/国产剧/漫长的季节 (2023)）
    category: str | None = None  # 如「国产剧」「欧美电影」
    basis: str | None = None  # 分类依据，如「TMDB + 豆瓣 + LLM」
    skipped: int = 0  # only_new 时因为网盘里已有同一集而跳过的文件数


class QuarkSaver:
    def __init__(
        self,
        cookie: str,
        to_pdir_fid: str = "0",
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        poll_interval: float = 1.0,
        poll_times: int = 10,
        classifier=None,
        root_dir: str = "QueryPilot",
    ) -> None:
        """`classifier`：`await classifier(title, files) -> Category`，为 None 时不分类，
        直接存到 `to_pdir_fid`；分类或建目录失败时也退回 `to_pdir_fid`。"""
        self._cookie = cookie
        self._to_pdir_fid = to_pdir_fid or "0"
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._poll_times = poll_times
        self._classifier = classifier
        self._root_dir = root_dir

    def __repr__(self) -> str:  # 防止对象被打印时带出 cookie
        return f"QuarkSaver(to_pdir_fid={self._to_pdir_fid!r})"

    def _headers(self, share_id: str) -> dict[str, str]:
        return {
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Referer": f"https://pan.quark.cn/s/{share_id}",
            "Origin": "https://pan.quark.cn",
            "Cookie": self._cookie,
        }

    @staticmethod
    def _check(body: dict, default: str) -> dict:
        code = body.get("code")
        if code == 31001:
            raise LoginExpiredError(ERROR_TEXT[31001])
        if code not in (0, None):
            raise SaveError(ERROR_TEXT.get(code, f"{default}（夸克错误码 {code}）"))
        return body.get("data") or {}

    async def save(
        self, share_id: str, pwd: str | None = None, to_path: str | None = None,
        only_new: bool = False,
    ) -> SaveResult:
        """`to_path`：指定网盘目标目录路径（不存在就创建），给出时跳过自动分类。

        `only_new`：只存目标目录里还没有的集（按「季x集」比对，认不出集数的按文件名），
        用于订阅自动转存，避免同一集存两遍；读不到目标目录时报错而不是盲存。
        """
        headers = self._headers(share_id)
        try:
            return await self._save(share_id, pwd, headers, to_path, only_new)
        except httpx.HTTPError as e:
            # 只记录异常类型，不记录请求（请求头里有 cookie）
            logger.warning("转存请求失败 share=%s error=%s", share_id, type(e).__name__)
            raise SaveError("连接夸克失败，请稍后重试") from None
        except ValueError:
            raise SaveError("夸克返回了无法解析的内容") from None

    async def _save(
        self, share_id: str, pwd: str | None, headers: dict, to_path: str | None = None,
        only_new: bool = False,
    ) -> SaveResult:
        # 1) 分享页 token
        resp = await self._client.post(
            TOKEN_URL,
            json={"pwd_id": share_id, "passcode": pwd or "",
                  "support_visit_limit_private_share": True},
            headers=headers, timeout=self._timeout,
        )
        if resp.status_code == 404:
            raise SaveError("分享已失效")
        stoken = self._check(resp.json(), "打开分享失败").get("stoken")
        if not stoken:
            raise SaveError("打开分享失败")

        # 2) 顶层文件列表（转存整个分享）
        resp = await self._client.get(
            DETAIL_URL,
            params={"pwd_id": share_id, "stoken": stoken, "pdir_fid": "0", "force": 0,
                    "_page": 1, "_size": 100, "_fetch_share": 1},
            headers=headers, timeout=self._timeout,
        )
        data = self._check(resp.json(), "读取分享内容失败")
        items = [
            f for f in data.get("list") or []
            if isinstance(f, dict) and f.get("fid") and f.get("share_fid_token")
        ]
        if not items:
            raise SaveError("分享里没有可转存的文件")
        title = (data.get("share") or {}).get("title")
        title = title if isinstance(title, str) else None

        # 3) 目标目录：指定路径优先，否则自动分类（失败就存到默认目录）
        to_fid, folder, category, basis = self._to_pdir_fid, None, None, None
        try:
            if to_path:
                to_fid, folder = await self._ensure_dir(to_path, headers), to_path
            elif self._classifier is not None:
                names = await self._file_names(share_id, stoken, items, headers)
                place = await decide_folder(
                    self._classifier, title or (names[0] if names else share_id), names,
                    self._root_dir,
                )
                to_fid = await self._ensure_dir(place.path, headers)
                folder, category, basis = place.path, place.label, place.basis
        except LoginExpiredError:
            raise
        except (SaveError, httpx.HTTPError, ValueError, KeyError) as e:
            logger.warning("目标目录准备失败（%s），存到默认目录", type(e).__name__)

        # 3.5) 只存新的集：顶层是单个文件夹时展开一层逐个比对，直接存进目标目录
        pdir, skipped = "0", 0
        if only_new:
            if len(items) == 1 and items[0].get("dir"):
                pdir = str(items[0]["fid"])
                items = await self._sub_items(share_id, stoken, pdir, headers)
            have_names, have_eps = await self._existing(to_fid, headers)
            fresh = [
                f for f in items
                if str(f.get("file_name") or "") not in have_names
                and (episode_key(str(f.get("file_name") or "")) or "") not in have_eps
            ]
            skipped = len(items) - len(fresh)
            items = fresh
            if not items:
                return SaveResult("", 0, title, True, folder, category, basis, skipped)

        # 4) 提交转存任务
        resp = await self._client.post(
            SAVE_URL,
            params=COMMON_PARAMS,
            json={
                "fid_list": [f["fid"] for f in items],
                "fid_token_list": [f["share_fid_token"] for f in items],
                "to_pdir_fid": to_fid,
                "pwd_id": share_id,
                "stoken": stoken,
                "pdir_fid": pdir,
                "scene": "link",
            },
            headers=headers, timeout=self._timeout,
        )
        task_id = self._check(resp.json(), "提交转存失败").get("task_id")
        if not task_id:
            raise SaveError("提交转存失败")

        # 5) 轮询任务状态：status 2 = 完成
        for i in range(self._poll_times):
            resp = await self._client.get(
                TASK_URL,
                params={**COMMON_PARAMS, "task_id": task_id, "retry_index": i},
                headers=headers, timeout=self._timeout,
            )
            task = self._check(resp.json(), "转存失败")
            if task.get("status") == 2:
                return SaveResult(task_id, len(items), title, True, folder, category, basis,
                                  skipped)
            await asyncio.sleep(self._poll_interval)
        return SaveResult(task_id, len(items), title, False, folder, category, basis, skipped)

    async def _sub_items(
        self, share_id: str, stoken: str, dir_fid: str, headers: dict
    ) -> list[dict]:
        """分享里某个文件夹下的文件（带转存需要的 share_fid_token）。"""
        resp = await self._client.get(
            DETAIL_URL,
            params={"pwd_id": share_id, "stoken": stoken, "pdir_fid": dir_fid, "force": 0,
                    "_page": 1, "_size": 200},
            headers=headers, timeout=self._timeout,
        )
        data = self._check(resp.json(), "读取分享内容失败")
        return [f for f in data.get("list") or []
                if isinstance(f, dict) and f.get("fid") and f.get("share_fid_token")]

    async def _list_dir(self, fid: str, headers: dict) -> list[dict]:
        resp = await self._client.get(
            LIST_URL,
            params={**COMMON_PARAMS, "pdir_fid": fid, "_page": 1, "_size": 200,
                    "_fetch_total": 1, "_sort": "file_type:asc,updated_at:desc"},
            headers=headers, timeout=self._timeout,
        )
        data = self._check(resp.json(), "读取网盘目录失败")
        return [f for f in data.get("list") or [] if isinstance(f, dict)]

    async def _existing(self, fid: str, headers: dict) -> tuple[set[str], set[str]]:
        """目标目录（含下一层子目录）里已有的文件名与「季x集」。"""
        names: set[str] = set()
        for f in await self._list_dir(fid, headers):
            if f.get("dir") or f.get("file_type") == 0:
                if f.get("fid"):
                    names |= {str(g.get("file_name") or "")
                              for g in await self._list_dir(str(f["fid"]), headers)}
            else:
                names.add(str(f.get("file_name") or ""))
        names.discard("")
        return names, {k for k in map(episode_key, names) if k}

    async def _file_names(
        self, share_id: str, stoken: str, items: list[dict], headers: dict
    ) -> list[str]:
        """分类用的文件名：顶层只有一个文件夹时再看一层（多数分享是「片名/各集」结构）。"""
        names = [str(f.get("file_name") or "") for f in items]
        if len(items) == 1 and items[0].get("dir"):
            try:
                resp = await self._client.get(
                    DETAIL_URL,
                    params={"pwd_id": share_id, "stoken": stoken, "pdir_fid": items[0]["fid"],
                            "force": 0, "_page": 1, "_size": 100},
                    headers=headers, timeout=self._timeout,
                )
                sub = (resp.json().get("data") or {}).get("list") or []
                names += [str(f.get("file_name") or "") for f in sub if isinstance(f, dict)]
            except (httpx.HTTPError, ValueError):
                pass
        return [n for n in names if n]

    async def _ensure_dir(self, path: str, headers: dict) -> str:
        """按路径找到网盘目录的 fid，不存在就逐级创建。"""
        resp = await self._client.post(
            PATH_LIST_URL, params=COMMON_PARAMS,
            json={"file_path": [path], "namespace": "0"},
            headers=headers, timeout=self._timeout,
        )
        body = resp.json()
        if body.get("code") == 31001:
            raise LoginExpiredError(ERROR_TEXT[31001])
        found = body.get("data") if isinstance(body.get("data"), list) else []
        if found and isinstance(found[0], dict) and found[0].get("fid"):
            return str(found[0]["fid"])
        resp = await self._client.post(
            MKDIR_URL, params=COMMON_PARAMS,
            json={"pdir_fid": "0", "file_name": "", "dir_path": path, "dir_init_lock": False},
            headers=headers, timeout=self._timeout,
        )
        fid = self._check(resp.json(), "创建目录失败").get("fid")
        if not fid:
            raise SaveError("创建目录失败")
        return str(fid)
