from types import SimpleNamespace

from app.models import QuarkLink
from app.services.selfcheck import TEST_DIR, SelfCheck, seconds_until


def link(share, state="valid", rel="match"):
    return QuarkLink(url=f"https://pan.quark.cn/s/{share}", share=share, state=state,
                     relevance=rel, name=share, source="test", time="2026-01-01")


class Agent:
    def __init__(self, by_title):
        self.by_title = by_title

    async def run(self, req, fresh_hours=None):
        got = self.by_title[req.query]
        if isinstance(got, Exception):
            raise got
        return SimpleNamespace(links=got)


class Saver:
    def __init__(self, files=3):
        self.files, self.saved, self.removed = files, [], []

    async def save(self, share, pwd=None, to_path=None):
        self.saved.append((share, to_path))
        return SimpleNamespace(file_count=self.files)

    async def remove_dir(self, path):
        self.removed.append(path)
        return True


class Alerts:
    def __init__(self):
        self.sent = []

    async def send(self, key, text):
        self.sent.append(text)
        return True


async def test_all_good_saves_and_cleans():
    saver, alerts = Saver(), Alerts()

    async def factory():
        return saver

    check = SelfCheck(Agent({"片A": [link("s1")], "片B": [link("s2")]}), ["片A", "片B"], factory, alerts)
    report = await check.run()
    assert report.ok and alerts.sent == []
    assert saver.saved[0][0] == "s1" and saver.saved[0][1].startswith(TEST_DIR + "/")
    assert saver.removed == [TEST_DIR]
    assert check.last is report


async def test_problems_alert():
    saver, alerts = Saver(files=0), Alerts()

    async def factory():
        return saver

    agent = Agent({"片A": [link("s1")], "片B": [link("x", state="invalid")], "片C": RuntimeError("boom")})
    report = await SelfCheck(agent, ["片A", "片B", "片C"], factory, alerts).run()
    assert not report.ok
    assert any("片B" in p for p in report.problems) and any("片C" in p for p in report.problems)
    assert any("没有文件" in p for p in report.problems)
    assert len(alerts.sent) == 1 and "发现问题" in alerts.sent[0]


async def test_no_account_only_searches():
    async def factory():
        return None

    report = await SelfCheck(Agent({"片A": [link("s1")]}), ["片A"], factory).run()
    assert report.ok and report.save.startswith("跳过")


def test_seconds_until_beijing_hour():
    # 2026-01-01 00:00 UTC = 北京 08:00；到北京 05:00 还要 21 小时
    assert seconds_until(5, now=1767225600) == 21 * 3600
    assert seconds_until(9, now=1767225600) == 3600
