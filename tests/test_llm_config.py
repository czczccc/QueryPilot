"""第三方 LLM 配置：base URL 规范化、模型名与地址生效。"""

import json

import httpx

from app.services import llm
from app.services.intent import DeepSeekParser
from app.services.llm import chat_url


def test_chat_url_normalization():
    assert chat_url("") == "https://api.deepseek.com/chat/completions"
    assert chat_url("https://api.siliconflow.cn/v1/") == (
        "https://api.siliconflow.cn/v1/chat/completions")
    assert chat_url("https://x.example/v1/chat/completions") == (
        "https://x.example/v1/chat/completions")


async def test_custom_base_url_and_model_are_used(monkeypatch):
    monkeypatch.setattr(llm, "CHAT_URL", "https://llm.example/v1/chat/completions")
    monkeypatch.setattr(llm, "MODEL", "my-model")
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        content = json.dumps({"resource": "沙丘2", "search_suggestions": ["沙丘2 夸克"]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    parser = DeepSeekParser("k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await parser.parse("沙丘2")
    assert out.parsed.resource == "沙丘2"
    assert str(seen[0].url) == "https://llm.example/v1/chat/completions"
    assert json.loads(seen[0].read())["model"] == "my-model"


def test_llm_api_key_env(monkeypatch):
    from app.config import load_settings

    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
    monkeypatch.setenv("LLM_API_KEY", "")
    assert load_settings().deepseek_api_key == "ds"
    monkeypatch.setenv("LLM_API_KEY", "third")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.siliconflow.cn/v1")
    monkeypatch.setenv("LLM_MODEL", "deepseek-ai/DeepSeek-V3")
    s = load_settings()
    assert (s.deepseek_api_key, s.llm_base_url, s.llm_model) == (
        "third", "https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V3")
    assert "third" not in repr(s)


def sse(*chunks: dict) -> httpx.Response:
    body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks)
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=(body + "data: [DONE]\n\n").encode())


def delta(**d) -> dict:
    return {"choices": [{"index": 0, "delta": d}]}


def stream_only_client(seen: list[dict]):
    """模拟只支持流式的中转：非流式请求一律 400。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        seen.append(body)
        if not body.get("stream"):
            return httpx.Response(400, json={"error": {"message": "stream must be true"}})
        if "tools" in body:
            return sse(
                delta(role="assistant", content="先搜"),
                delta(tool_calls=[{"index": 0, "id": "c1", "type": "function",
                                   "function": {"name": "search", "arguments": ""}}]),
                delta(tool_calls=[{"index": 0, "function": {"arguments": '{"queries": ["a"],'}}]),
                delta(tool_calls=[{"index": 0, "function": {"arguments": ' "keyword": "k"}'}}]),
                delta(tool_calls=[{"index": 1, "id": "c2", "type": "function",
                                   "function": {"name": "finish", "arguments": "{}"}}]),
            )
        content = json.dumps({"resource": "沙丘2", "search_suggestions": ["沙丘2 夸克"]},
                             ensure_ascii=False)
        return sse(delta(content=content[:10]), delta(content=content[10:]))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_auto_switches_to_stream_after_400(caplog):
    seen: list[dict] = []
    client = stream_only_client(seen)
    caplog.set_level("INFO")
    out = await DeepSeekParser("k", client=client).parse("沙丘2")
    assert out.fallback_used is False and out.parsed.resource == "沙丘2"
    assert [b.get("stream", False) for b in seen] == [False, True]
    assert "stream must be true" in caplog.text  # 400 的原因写进日志

    # 之后直接流式，不再先试非流式
    await DeepSeekParser("k", client=client).parse("沙丘2")
    assert [b.get("stream", False) for b in seen] == [False, True, True]


async def test_stream_assembles_tool_calls():
    llm._use_stream = True
    message = await llm.chat(stream_only_client([]), {"model": "m", "messages": [], "tools": []},
                             "k", 5)
    assert message["content"] == "先搜"
    calls = message["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["search", "finish"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"queries": ["a"], "keyword": "k"}
    assert calls[0]["id"] == "c1"


async def test_stream_ignored_by_server_returns_plain_json():
    async def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    llm._use_stream = True
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert (await llm.chat(client, {}, "k", 5))["content"] == "hi"


async def test_other_errors_are_logged_and_raised(caplog):
    async def handler(request):
        return httpx.Response(401, json={"error": "bad key"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = await DeepSeekParser("k", client=client).parse("沙丘2")
    assert out.fallback_used is True
    assert "bad key" in caplog.text


async def test_rule_planner_does_not_repeat_keyword_engines():
    from app.models import SearchRequest
    from app.services.agent import SearchAgent
    from tests.test_agent import ScriptedTavily, make_service, shares

    service = make_service(ScriptedTavily({}, default=shares("ok", 2)))
    calls: list[tuple[str, bool]] = []
    real = service.collect

    async def spy(queries, keyword, providers, keyword_engines=True, **kw):
        calls.append((keyword, keyword_engines))
        return await real(queries, keyword, providers, keyword_engines, **kw)

    service.collect = spy
    await SearchAgent(service).run(SearchRequest(query="流浪地球2"))
    assert len(calls) == 2
    # 第二轮换了别名关键词就照常搜；同一关键词则只跑 Tavily
    assert calls[0][1] is True
    assert calls[1][1] is (calls[1][0] != calls[0][0])


async def test_agent_uses_llm_planner_via_stream():
    from app.models import SearchRequest
    from app.services.agent import SearchAgent
    from tests.test_agent import ScriptedTavily, make_service, shares

    seen: list[dict] = []
    agent = SearchAgent(make_service(ScriptedTavily({}, default=shares("ok", 6))),
                        api_key="k", client=stream_only_client(seen))
    resp = await agent.run(SearchRequest(query="流浪地球2"))
    assert resp.planner == "llm"
    assert any(s.tool == "search" and s.planner == "llm" for s in resp.steps)
