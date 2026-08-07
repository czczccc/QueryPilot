# 夸克网盘资源搜索（QueryPilot）

夸克网盘资源搜索：输入影视/剧集名（支持自然语言），自动全网搜索夸克网盘分享链接，输出带提取码、可达性验证与置信度的链接清单。

> 当前状态：**v0.2.0 已上线**，部署于国内服务器，公网可访问 `http://124.223.112.9:8000`。仓库根目录的 `quark_search_gui.py` 是早期桌面原型（v3.1），其搜索链路已迁移至 Web 版。

## 工作方式

1. DeepSeek 解析影视资源名、清晰度、别名与英文名，生成 5~6 个搜索查询词；
2. Tavily 多查询搜索 + 对资源页深度抓取，提取夸克网盘分享链接（`quark.cn/s/xxx`）；
3. 夸克云搜引擎并行补充；
4. 按分享码去重，自动识别提取码（4 位）；
5. 逐个验证链接可达性（HTTP 状态），按置信度排序输出。

## 快速开始（本地开发）

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload
```

在 `.env` 中配置 `DEEPSEEK_API_KEY` 与 `TAVILY_API_KEY`（不配置则走规则降级）。

## 开发命令

| 命令 | 用途 |
| --- | --- |
| `python -m uvicorn app.main:app --reload` | 启动本地开发服务 |
| `python -m pytest -q -p no:cacheprovider` | 运行测试 |
| `python -m ruff check app tests` | 静态检查 |
| `docker compose up -d --build` | 服务器部署 |

## 部署到国内服务器（Debian + Docker）

```bash
# 仓库拷到服务器后：
cp .env.example .env   # 填入真实 key
cd /opt/querypilot && docker compose up -d --build
curl http://127.0.0.1:8000/health   # 应返回 ok
```

> 国内服务器 Docker 构建已内置阿里云 pip 镜像（Dockerfile ARG `PIP_INDEX_URL`）。绑域名需 ICP 备案；密钥只存服务器 `.env`，不入仓库。

## 项目文档

- [产品需求文档](docs/PRD.md)
- [开发与架构文档](docs/DEVELOPMENT.md)
- [实施计划](tasks/plan.md)
- [任务清单](tasks/todo.md)

## 安全与合规

- API Key 只通过环境变量/服务器 `.env` 管理，`config.json`、`.env` 已被 Git 忽略；
- 本项目仅聚合公开网页中的分享链接，不托管、破解或下载受版权保护的内容；
- 链接有效性需用户自行判断，工具仅供合规用途。

## 旧版原型

`quark_search_gui.py` 是 Tkinter 桌面原型（v3.1），核心逻辑（正则提取、深度抓取、夸克云搜、验证）已迁移至 `app/services/quark.py`。
