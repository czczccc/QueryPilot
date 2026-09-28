"""每日自检：用固定片单跑一遍「搜索 → 验证 → 转存到测试目录 → 核对 → 清理」，异常就告警站长。

等于每天有人替站长手动测一遍。片单和账号都是配置项（SELFCHECK_TITLES / SELFCHECK_USER），
不配片单就不跑。转存只用片单第一部搜到的最好链接，存进单独的测试目录，核对后整个目录删掉
（进夸克回收站）。结果只记日志和告警，不写用户数据。
"""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.models import SearchRequest

logger = logging.getLogger(__name__)

# 固定不可配置：清理时会把这个目录整个删掉，配错成真实目录就危险了
TEST_DIR = "/QueryPilot自检"


@dataclass
class TitleResult:
    title: str
    found: int = 0  # 候选
    good: int = 0  # 有效且确认是这部
    error: str | None = None


@dataclass
class Report:
    at: float
    titles: list[TitleResult] = field(default_factory=list)
    save: str = "跳过"  # 转存结果的一句话
    problems: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.problems

    def text(self) -> str:
        lines = [f"每日自检{'正常' if self.ok else '发现问题'}（{self.seconds:.0f} 秒）"]
        lines += [f"· {t.title}：{t.good} 条可用 / {t.found} 条候选" + (f"，出错 {t.error}" if t.error else "")
                  for t in self.titles]
        lines.append(f"· 转存：{self.save}")
        lines += [f"⚠ {p}" for p in self.problems]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"at": self.at, "ok": self.ok, "seconds": round(self.seconds, 1), "save": self.save,
                "problems": self.problems,
                "titles": [t.__dict__ for t in self.titles]}


class SelfCheck:
    """`saver_factory()` 返回站长账号的 QuarkSaver，拿不到（没配 / 登录失效）返回 None。"""

    def __init__(self, agent, titles: list[str], saver_factory: Callable[[], Awaitable],
                 alerts=None) -> None:
        self._agent = agent
        self.titles = [t for t in titles if t.strip()]
        self._saver_factory = saver_factory
        self._alerts = alerts
        self.test_dir = TEST_DIR
        self.last: Report | None = None

    async def run(self) -> Report:
        started = time.monotonic()
        report = Report(at=time.time())
        best = None
        for title in self.titles:
            item = TitleResult(title)
            report.titles.append(item)
            try:
                res = await self._agent.run(SearchRequest(query=title, refresh=True), fresh_hours=0)
            except Exception as e:  # 单部出错继续下一部
                logger.exception("自检搜索失败 %s", title)
                item.error = type(e).__name__
                report.problems.append(f"《{title}》搜索出错：{type(e).__name__}")
                continue
            good = [x for x in res.links if x.state == "valid" and x.relevance == "match"]
            item.found, item.good = len(res.links), len(good)
            if not good:
                report.problems.append(f"《{title}》没搜到可用资源")
            elif best is None:
                best = good[0]
        await self._save(best, report)
        report.seconds = time.monotonic() - started
        self.last = report
        logger.info("每日自检：%s", report.text().replace("\n", " | "))
        if not report.ok and self._alerts is not None:
            await self._alerts.send("selfcheck", report.text())
        return report

    async def _save(self, link, report: Report) -> None:
        if link is None:
            report.save = "跳过（没有可用链接）"
            return
        try:
            saver = await self._saver_factory()
        except Exception:
            logger.exception("自检取转存账号失败")
            saver = None
        if saver is None:
            report.save = "跳过（没配 SELFCHECK_USER / QUARK_COOKIE，或登录已失效）"
            return
        path = f"{self.test_dir}/{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            result = await saver.save(link.share, link.pwd, to_path=path)
        except Exception as e:
            logger.exception("自检转存失败")
            report.save = f"失败：{str(e)[:80] or type(e).__name__}"
            report.problems.append(f"转存失败：{report.save[3:]}")
        else:
            if result.file_count > 0:
                report.save = f"成功，{result.file_count} 个文件"
            else:
                report.save = "失败：目录里没有文件"
                report.problems.append("转存后测试目录里没有文件")
        try:
            await saver.remove_dir(self.test_dir)  # 整个测试目录进回收站
        except Exception as e:
            logger.exception("自检清理测试目录失败 %s", type(e).__name__)
            report.problems.append("清理测试目录失败，请手动删网盘里的「QueryPilot自检」")


def seconds_until(hour: int, now: float | None = None, tz_offset_hours: float = 8.0) -> float:
    """距离下一个北京时间 `hour` 点还有几秒。"""
    now = time.time() if now is None else now
    local = now + tz_offset_hours * 3600
    day = 86400
    target = (local // day) * day + hour * 3600
    if target <= local:
        target += day
    return target - local
