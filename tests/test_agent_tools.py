"""agent 新工具：lookup_media 查条目拿原名 / 年份，规则模式下搜不够时自动查一次再补搜。"""

from app.models import SearchRequest
from app.services.agent import SearchAgent, tools_for
from app.services.metadata import MediaInfo
from tests.test_agent import ScriptedTavily, make_service, shares


class Lookup:
    def __init__(self):
        self.calls = []

    async def __call__(self, name, year, fresh=False, limit=2):
        self.calls.append((name, year))
        return [MediaInfo(source="tmdb", title="流浪地球2", original_title="The Wandering Earth II",
                          year="2023", media="movie", id="1")]


async def test_rules_lookup_then_search_with_original_title():
    tavily = ScriptedTavily({}, default=shares("ok", 1))  # 只有 1 条：不够，要补搜
    agent = SearchAgent(make_service(tavily))
    agent.lookup = Lookup()
    resp = await agent.run(SearchRequest(query="流浪地球2"))
    tools = [s.tool for s in resp.steps]
    assert "lookup_media" in tools
    look = resp.steps[tools.index("lookup_media")]
    assert look.observation["results"][0]["original_title"] == "The Wandering Earth II"
    later = [s for s in resp.steps[tools.index("lookup_media"):] if s.tool == "search"]
    assert later and any("The Wandering Earth II" in q for q in later[0].args["queries"])


def test_tool_list():
    names = [t["function"]["name"] for t in tools_for(False, True)]
    assert names[-3:] == ["lookup_media", "inspect_share", "finish"]
    assert "lookup_media" not in [t["function"]["name"] for t in tools_for(True)]


def share_client():
    """假夸克分享：顶层一个文件夹，里面 3 集（文件名不带片名）。"""
    import httpx

    async def handler(request):
        if request.url.path.endswith("/sharepage/token"):
            return httpx.Response(200, json={"code": 0, "data": {"stoken": "st"}})
        pdir = request.url.params["pdir_fid"]
        if pdir == "0":
            return httpx.Response(200, json={"data": {"share": {"title": "更新中"}, "list": [
                {"fid": "d1", "file_name": "我不是大师 2026 4K", "dir": True}]}})
        return httpx.Response(200, json={"data": {"list": [
            {"fid": f"f{i}", "file_name": f"{i:02d}.mp4", "size": 10, "dir": False}
            for i in (1, 2, 3)]}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_list_and_summarize_share():
    from app.services.share_inspect import list_share, summarize
    title, files = await list_share("abc", share_client())
    info = summarize(title, files)
    assert info["kind"] == "pack" and info["videos"] == 3 and info["episodes"] == [1, 2, 3]
    assert info["sample"][0] == "我不是大师 2026 4K/01.mp4"


async def test_inspect_turns_uncertain_into_match():
    from app.models import ParsedResource, QualityInfo, QuarkLink
    from app.services.agent import AgentState, to_inspect
    from app.services.relevance import build_target
    tavily = ScriptedTavily({}, default=shares("ok", 1))
    service = make_service(tavily)
    service._client = share_client()
    agent = SearchAgent(service)
    parsed = ParsedResource(resource="我不是大师", search_suggestions=["我不是大师"])
    state = AgentState(parsed=parsed, required=None, refresh=False,
                       target=build_target(parsed, "我不是大师"))
    link = QuarkLink(name="更新中", share="abc", source="t", time="", state="valid",
                     share_title="更新中", files_preview=["01.mp4"], relevance="uncertain",
                     quality=QualityInfo(video_count=3))
    state.candidates["abc"] = link
    assert to_inspect(state) == ["abc"]
    obs = await agent._inspect({"shares": ["abc"]}, state)
    assert obs["shares"]["abc"]["relevance"] == "match" and link.relevance == "match"
    assert to_inspect(state) == []


async def test_check_my_drive_runs_once_when_logged_in():
    calls = []

    async def drive(name, season):
        calls.append((name, season))
        return {"logged_in": True, "folders": ["/QueryPilot/x"], "videos": 2,
                "episodes": {"1": [1, 2]}}

    agent = SearchAgent(make_service(ScriptedTavily({}, default=shares("ok", 6))))
    resp = await agent.run(SearchRequest(query="流浪地球2"), drive=drive)
    step = [s for s in resp.steps if s.tool == "check_my_drive"]
    assert len(step) == 1 and step[0].observation["episodes"] == {"1": [1, 2]}
    assert calls == [("流浪地球2", None)]
    plain = await SearchAgent(make_service(ScriptedTavily({}, default=shares("ok", 6)))).run(
        SearchRequest(query="流浪地球2"))
    assert "check_my_drive" not in [s.tool for s in plain.steps]
    assert "check_my_drive" in [t["function"]["name"] for t in tools_for(False, False, True)]
