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
