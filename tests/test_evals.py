"""评测集回归检查：手工构造的易错样例每次都跑，指标掉下来就失败；
evals/recordings 里有真实录制时也离线回放一遍。"""

import json

import httpx
import pytest

from evals import harness
from evals.metrics import Case, gold, score_case
from evals.run import evaluate


def test_titles_file_is_well_formed():
    cases = harness.load_cases()
    assert len(cases) >= 50 and len({c.id for c in cases}) == len(cases)
    for c in cases:
        assert c.media in ("movie", "tv") and c.names, c.id


async def test_synthetic_hard_cases_do_not_regress():
    summary, results = await evaluate("synthetic")
    assert summary["errors"] == 0, [r.error for r in results]
    assert summary["precision"] >= 0.95, [(r.query, r.wrong) for r in results]
    assert summary["recall"] >= 0.9, [(r.query, r.missed) for r in results]
    assert summary["found"] >= 0.95 and summary["top1"] >= 0.95


async def test_recordings_replay_offline():
    if not any(harness.RECORDINGS.glob("*.json")):
        pytest.skip("还没有真实录制（本地跑 python -m evals.run --live 生成）")
    summary, _ = await evaluate("replay")
    assert summary["errors"] == 0
    assert (summary["precision"] or 0) >= 0.8


async def test_record_then_replay_roundtrip_without_secrets():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"echo": json.loads(request.read())["q"]})

    tape = harness.Tape()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with harness.recording(tape):
        r = await client.post("https://api.example.com/search?api_key=SECRET&x=1",
                              json={"q": "流浪地球", "api_key": "SECRET"},
                              headers={"Authorization": "Bearer SECRET", "Cookie": "c=SECRET"})
    assert r.json() == {"echo": "流浪地球"}
    dumped = json.dumps(tape.data, ensure_ascii=False)
    assert "SECRET" not in dumped  # 录制文件里没有 key、cookie
    offline = httpx.AsyncClient()
    with harness.replaying(tape):
        again = await offline.post("https://api.example.com/search?api_key=OTHER&x=1",
                                   json={"q": "流浪地球", "api_key": "OTHER"})
        assert again.json() == {"echo": "流浪地球"}
        with pytest.raises(httpx.ConnectError):
            await offline.get("https://api.example.com/unknown")
    assert len(seen) == 1


def test_gold_judge():
    from app.models import QuarkLink

    case = Case(id="x", query="飞驰人生2", title="飞驰人生2", year="2024", media="movie")
    link = QuarkLink(name="p", share="a" * 10, source="t", time="", state="valid",
                     share_title="飞驰人生 2019 1080P", relevance="match")
    assert gold(link, case) is False
    link.share_title = "《飞驰人生2》4K"
    assert gold(link, case) is True
    assert score_case(case, [link]).true_positive == 1
