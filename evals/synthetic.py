"""手工构造的离线样例（用户反馈过的易错情况），不联网，CI 每次都跑。

每个样例给出搜索源返回的页面（page）和夸克分享的真实标题、文件列表；
`dead: true` 表示分享已失效。片名和标准答案取自 titles.json。
"""

import json
from pathlib import Path

import httpx

from app.models import QuarkLink, RawSearchResult, SearchRequest
from app.services.agent import SearchAgent
from app.services.intent import DeepSeekParser
from app.services.memory import LinkStore
from app.services.metadata import MediaInfo
from app.services.search import QuarkSearchService
from evals.metrics import Case

ROOT = Path(__file__).resolve().parent


class ScriptedSource:
    name = "tavily"

    def __init__(self, results: list[dict]):
        self.results = results

    async def search(self, query: str, limit: int = 8) -> list[RawSearchResult]:
        return [RawSearchResult(title=r["page"], url=f"https://pan.quark.cn/s/{r['share']}",
                                snippet="", provider="tavily") for r in self.results]


def quark(results: list[dict]) -> httpx.AsyncClient:
    by_share = {r["share"]: r for r in results}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sharepage/token"):
            r = by_share.get(json.loads(request.read())["pwd_id"], {})
            if r.get("dead") or not r:
                return httpx.Response(404, json={"code": 41006})
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "t"}})
        if request.url.path.endswith("/sharepage/detail"):
            r = by_share[request.url.params["pwd_id"]]
            return httpx.Response(200, json={"code": 0, "data": {
                "share": {"status": 1, "title": r.get("title")},
                "list": [{"file_name": n, "size": 2 << 30} for n in r.get("files", [])]}})
        return httpx.Response(200, text="")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class FakeLookup:
    """模拟配了 TMDB：返回标准片名和英文原名（取自 titles.json 的别名）。"""

    def __init__(self, case: Case):
        english = next((a for a in case.aliases if a.isascii()), None)
        self.info = MediaInfo(source="tmdb", title=case.title, original_title=english,
                              year=case.year, media=case.media)

    async def __call__(self, name, year, fresh=False):
        return [self.info]


def load() -> list[tuple[Case, list[dict]]]:
    cases = {c["id"]: Case(**c) for c in json.loads((ROOT / "titles.json").read_text("utf-8"))}
    spec = json.loads((ROOT / "synthetic.json").read_text("utf-8"))
    return [(cases[s["id"]], s["results"]) for s in spec]


async def run_synthetic(case: Case, results: list[dict]) -> list[QuarkLink]:
    service = QuarkSearchService(
        parser=DeepSeekParser(api_key=""), tavily=ScriptedSource(results),
        use_qkyunso=False, use_bing=False, client=quark(results), store=LinkStore(":memory:"),
    )
    agent = SearchAgent(service, lookup=FakeLookup(case))
    resp = await agent.run(SearchRequest(query=case.query, client_id="evaluation"))
    return resp.links
