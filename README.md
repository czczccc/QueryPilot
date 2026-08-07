# QueryPilot

QueryPilot 是一个可解释的 AI 聚合搜索 Web 应用。用户可以用自然语言描述想找的游戏、影视、音乐、软件或其他公开信息；系统负责识别意图、改写查询、聚合公开搜索结果、去重排序，并解释结果与需求的匹配原因。

> 当前状态：**规划与 Web 化改造阶段**。仓库中的 `quark_search_gui.py` 是早期桌面原型，目标版本及验收标准见 [`docs/PRD.md`](docs/PRD.md) 和 [`tasks/plan.md`](tasks/plan.md)。

## 为什么做这个项目

传统搜索要求用户自己拆分关键词、重复查询并判断结果质量。QueryPilot 把这套过程变成一条可观察的搜索流水线：

1. 理解自然语言中的资源类型、关键词和约束；
2. 生成多个互补查询；
3. 并发调用可替换的搜索提供方；
4. 将异构结果标准化、去重和排序；
5. 返回来源、相关性和处理耗时，便于用户判断结果是否可信。

该项目重点展示 AI 应用/FDE 场景中的需求澄清、第三方系统集成、失败降级、可解释性和端到端交付能力。

## 目标体验

用户输入：

```text
想找一款适合四个人玩的轻量联机游戏，最好支持中文
```

QueryPilot 返回：

- 识别出的类型、关键词与约束；
- AI 生成的 3 个搜索查询；
- 去重并排序后的公开网页结果；
- 每条结果的来源、摘要、相关性分数和匹配理由；
- 各搜索提供方的状态、召回数量与耗时。

## MVP 功能

- 通用自然语言搜索，支持游戏、影视、音乐、软件和其他主题；
- DeepSeek 意图解析与查询改写；
- Tavily 搜索源（适配器接口预留多源扩展）；
- 统一结果模型、URL 归一化、去重与确定性排序；
- LLM 或单一搜索源失败时的可用降级；
- 响应式 Web 页面与搜索过程指标；
- FastAPI OpenAPI 文档、自动化测试、Docker 和 CI；
- 可在线部署的单体应用。

## 技术方案

- Python 3.11+
- FastAPI + Pydantic
- Jinja2 + 原生 JavaScript/CSS
- DeepSeek Chat Completions API
- Tavily Search API
- pytest + Ruff
- Docker + GitHub Actions
- 国内轻量服务器（Docker + Nginx）部署

详细设计见 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)，架构决策见 [`docs/decisions/0001-web-mvp-architecture.md`](docs/decisions/0001-web-mvp-architecture.md)。

## 计划中的快速开始

Web 版本完成后，本地启动方式如下：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload
```

在 `.env` 中配置：

```dotenv
DEEPSEEK_API_KEY=replace_me
TAVILY_API_KEY=replace_me
```

浏览器访问 `http://127.0.0.1:8000`，接口文档位于 `http://127.0.0.1:8000/docs`。

## 计划中的开发命令

| 命令 | 用途 |
| --- | --- |
| `python -m uvicorn app.main:app --reload` | 启动本地开发服务 |
| `python -m pytest -q` | 运行测试 |
| `python -m ruff check .` | 静态检查 |
| `docker build -t querypilot .` | 构建生产镜像 |
| `docker run --env-file .env -p 8000:8000 querypilot` | 本地运行镜像 |

## 项目文档

- [产品需求文档](docs/PRD.md)
- [开发与架构文档](docs/DEVELOPMENT.md)
- [实施计划](tasks/plan.md)
- [任务清单](tasks/todo.md)

## 安全与合规

- API Key 只能通过环境变量或部署平台的 Secret 管理，不得提交到仓库；
- 本项目只聚合公开网页信息，不托管、破解或下载受版权保护的内容；
- 搜索结果属于第三方内容，用户需要自行判断其合法性、准确性与可用性；
- 新增数据源前应检查其 API 条款、抓取政策和署名要求。

## 旧版原型

`quark_search_gui.py` 是 Tkinter 桌面原型，用于验证查询改写、网页检索、链接提取和结果去重。它不代表目标 Web 架构，且不应在公开仓库中携带真实密钥。完成 MVP 后再决定将其迁移到 `legacy/` 或删除。

