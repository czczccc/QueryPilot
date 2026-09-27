"""LLM 接口配置：默认 DeepSeek 官方，可换成任何兼容 OpenAI chat/completions 格式的服务。

- `LLM_BASE_URL`：如 `https://api.deepseek.com`、`https://api.siliconflow.cn/v1`，
  自动补上 `/chat/completions`（已经写全的保持不变）；
- `LLM_MODEL`：模型名，如 `deepseek-v4-flash`、`deepseek-ai/DeepSeek-V3`；
- 密钥用 `LLM_API_KEY`，没填时沿用 `DEEPSEEK_API_KEY`；
- `LLM_STREAM`：auto（默认）/ true / false，见下方 `chat`。

注意：agent 规划用到 function calling（tools），换的模型需要支持它；
不支持时规划会报错并自动退回规则规划器，其余功能不受影响。
"""

import json
import logging

import httpx

from app.config import load_settings

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


def chat_url(base_url: str) -> str:
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


_settings = load_settings()
CHAT_URL = chat_url(_settings.llm_base_url)
MODEL = _settings.llm_model or DEFAULT_MODEL


# ---------------- 统一调用 ----------------
# 有些第三方中转只接受流式请求（stream=true），非流式直接 400。
# LLM_STREAM=auto（默认）：先按非流式请求，遇到 400 自动改用流式重试，成功后本进程一直用流式；
# LLM_STREAM=true：始终流式；LLM_STREAM=false：从不流式。
STREAM_MODE = _settings.llm_stream if _settings.llm_stream in ("auto", "true", "false") else "auto"
_use_stream = STREAM_MODE == "true"

logger = logging.getLogger(__name__)


def _snippet(text: str) -> str:
    return " ".join(text.split())[:300]


async def chat(
    client: httpx.AsyncClient, payload: dict, api_key: str, timeout: float
) -> dict:
    """发一次 chat/completions，返回 `message`（含 content 与可能的 tool_calls）。

    失败时抛 httpx.HTTPError / ValueError / KeyError，调用方按原有逻辑降级；
    接口报错时把响应内容（前 300 字）写进日志，方便排查模型名、参数不支持等问题。
    """
    global _use_stream
    headers = {"Authorization": f"Bearer {api_key}"}
    if _use_stream:
        return await _stream(client, payload, headers, timeout)
    resp = await client.post(CHAT_URL, json=payload, headers=headers, timeout=timeout)
    if resp.status_code == 400 and STREAM_MODE == "auto":
        logger.info("LLM 接口返回 400（%s），改用流式重试", _snippet(resp.text))
        message = await _stream(client, payload, headers, timeout)
        _use_stream = True
        logger.info("LLM 流式调用成功，之后都用流式")
        return message
    if resp.is_error:
        logger.warning("LLM 接口返回 %s：%s", resp.status_code, _snippet(resp.text))
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


async def _stream(
    client: httpx.AsyncClient, payload: dict, headers: dict, timeout: float
) -> dict:
    """流式请求并把增量拼回完整的 message（文本与工具调用）。"""
    content: list[str] = []
    calls: dict[int, dict] = {}
    async with client.stream(
        "POST", CHAT_URL, json={**payload, "stream": True}, headers=headers, timeout=timeout
    ) as resp:
        if resp.is_error:
            await resp.aread()
            logger.warning("LLM 流式接口返回 %s：%s", resp.status_code, _snippet(resp.text))
            resp.raise_for_status()
        if "text/event-stream" not in resp.headers.get("content-type", ""):
            # 个别服务忽略 stream 参数，直接返回完整 JSON
            await resp.aread()
            return resp.json()["choices"][0]["message"]
        async for line in resp.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if not chunk.get("choices"):
                continue
            delta = chunk["choices"][0].get("delta") or {}
            if delta.get("content"):
                content.append(delta["content"])
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", len(calls)), {
                    "id": "", "type": "function", "function": {"name": "", "arguments": ""},
                })
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                slot["function"]["name"] += fn.get("name") or ""
                slot["function"]["arguments"] += fn.get("arguments") or ""
    message: dict = {"role": "assistant", "content": "".join(content)}
    if calls:
        message["tool_calls"] = [calls[i] for i in sorted(calls)]
    return message
