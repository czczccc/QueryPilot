# ---- 第一阶段：构建新前端（web/，引用 packages/cz-design-system 源码）----
FROM node:22-slim AS web

# 默认使用国内镜像加速构建；CI 等环境可通过 --build-arg 覆盖
ARG NPM_REGISTRY=https://registry.npmmirror.com

WORKDIR /src
COPY packages/cz-design-system/package.json packages/cz-design-system/package-lock.json packages/cz-design-system/
COPY web/package.json web/package-lock.json web/
RUN cd packages/cz-design-system && npm ci --no-audit --no-fund --registry=${NPM_REGISTRY} \
 && cd /src/web && npm ci --no-audit --no-fund --registry=${NPM_REGISTRY}
COPY packages/cz-design-system packages/cz-design-system
COPY web web
RUN mkdir -p app && cd web && npm run build

# ---- 第二阶段：运行 FastAPI ----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

# 先装依赖以利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt

COPY app ./app
COPY --from=web /src/app/web ./app/web

# 非 root 运行
RUN useradd -m appuser && mkdir -p /app/data /app/logs && chown appuser /app/data /app/logs
USER appuser

# 记忆库（SQLite）放在数据卷里，重建容器不丢
ENV MEMORY_DB_PATH=/app/data/querypilot.db
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
