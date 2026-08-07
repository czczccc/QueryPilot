# QueryPilot

QueryPilot 是一个可解释的 AI 聚合搜索 Web 应用。用户可以用自然语言描述想找的游戏、影视、音乐、软件或其他公开信息；系统负责识别意图、改写查询、聚合公开搜索结果、去重排序，并解释结果与需求的匹配原因。

> 当前状态：**Web MVP 已实现**（T1–T7 全部完成），本地调试通过，可通过 Docker 部署到国内轻量服务器。仓库根目录的 `quark_search_gui.py` 是早期桌面原型（夸克网盘资源搜索工具 v3.1），仅作迁移参考。

## 为什么做这个项目

传统搜索要求用户自己拆分关键词、重复查询并判断结果质量。QueryPilot 把这套过程变成一条可观察的搜索流水线：

1. 理解自然语言中的资源类型、关键词和约束；
2. 生成多个互补查询；
3. 并发调用可替换的搜索提供方；
4. 将异构结果标准化、去重和排序；
5. 返回来源、相关性和处理耗时，便于用户判断结果是否可信。

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
- DeepSeek 意图解析与查询改写，失败时自动规则降级；
- Tavily 搜索源（适配器接口预留多源扩展）；
- 统一结果模型、URL 归一化、去重与确定性排序；
- AI 或搜索源失败时的可用降级（部分成功 / 受控 503）；
- 响应式 Web 页面与搜索过程指标；
- FastAPI OpenAPI 文档、自动化测试、Docker 和 CI。

## 技术方案

- Python 3.11+（本地 3.12 验证）
- FastAPI + Pydantic v2
- Jinja2 + 原生 JavaScript/CSS
- DeepSeek Chat Completions API（模型 `deepseek-v4-flash`）
- Tavily Search API
- HTTPX（异步、超时与连接池）
- pytest + pytest-asyncio + Ruff
- Docker + GitHub Actions + 国内轻量服务器（Nginx）

详细设计见 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)，架构决策见 [`docs/decisions/0001-web-mvp-architecture.md`](docs/decisions/0001-web-mvp-architecture.md)。

## 快速开始（本地开发）

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload
```

在 `.env` 中配置：

```dotenv
DEEPSEEK_API_KEY=replace_me
TAVILY_API_KEY=replace_me
```

> 未配置 key 时应用仍可启动：意图解析走规则降级；无搜索源时 `/api/search` 返回受控 503。配置 key 后即可完整搜索。

浏览器访问 `http://127.0.0.1:8000`，接口文档位于 `http://127.0.0.1:8000/docs`。

## 开发命令

| 命令 | 用途 |
| --- | --- |
| `python -m uvicorn app.main:app --reload` | 启动本地开发服务 |
| `python -m pytest -q -p no:cacheprovider` | 运行测试 |
| `python -m ruff check app tests` | 静态检查 |
| `docker build -t querypilot .` | 构建生产镜像 |
| `docker compose up -d` | 本地/服务器启动镜像 |

## 部署到国内服务器（Debian + Docker）

1. 将仓库拷到服务器（或 git clone）；
2. 创建 `.env`（`cp .env.example .env`）并填入真实 key；
3. 构建并启动：

```bash
docker compose up -d --build
```

4. （可选）Nginx 反向代理到 `http://127.0.0.1:8000`，绑定域名并配置 HTTPS；
5. 健康检查：`curl http://127.0.0.1:8000/health` 应返回 `{"status":"ok","version":"0.1.0"}`。

> 注意：国内服务器绑定域名需按云平台要求完成 ICP 备案；仅 IP 访问或测试用途可暂缓。密钥只通过服务器 `.env` 管理，绝不提交仓库。

## 项目文档

- [产品需求文档](docs/PRD.md)
- [开发与架构文档](docs/DEVELOPMENT.md)
- [实施计划](tasks/plan.md)
- [任务清单](tasks/todo.md)

## 安全与合规

- API Key 只能通过环境变量或服务器 Secret 管理，不得提交到仓库（`config.json`、`.env` 已被 Git 忽略）；
- 本项目只聚合公开网页信息，不托管、破解或下载受版权保护的内容；
- 搜索结果属于第三方内容，用户需要自行判断其合法性、准确性与可用性；
- 新增数据源前应检查其 API 条款、抓取政策和署名要求。

## 旧版原型

`quark_search_gui.py` 是 Tkinter 桌面原型（夸克网盘资源搜索工具 v3.1），用于验证查询改写、网页检索、链接提取和结果去重。它不代表目标 Web 架构，且不应在公开仓库中携带真实密钥。
