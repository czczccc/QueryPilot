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
    assert names[-2:] == ["lookup_media", "finish"]
    assert "lookup_media" not in [t["function"]["name"] for t in tools_for(True)]
