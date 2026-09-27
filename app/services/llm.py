"""LLM 接口配置：默认 DeepSeek 官方，可换成任何兼容 OpenAI chat/completions 格式的服务。

- `LLM_BASE_URL`：如 `https://api.deepseek.com`、`https://api.siliconflow.cn/v1`，
  自动补上 `/chat/completions`（已经写全的保持不变）；
- `LLM_MODEL`：模型名，如 `deepseek-v4-flash`、`deepseek-ai/DeepSeek-V3`；
- 密钥用 `LLM_API_KEY`，没填时沿用 `DEEPSEEK_API_KEY`。

注意：agent 规划用到 function calling（tools），换的模型需要支持它；
不支持时规划会报错并自动退回规则规划器，其余功能不受影响。
"""

from app.config import load_settings

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


def chat_url(base_url: str) -> str:
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


_settings = load_settings()
CHAT_URL = chat_url(_settings.llm_base_url)
MODEL = _settings.llm_model or DEFAULT_MODEL
