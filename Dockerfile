FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 默认使用国内镜像加速构建；CI 等环境可通过 --build-arg 覆盖
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

# 先装依赖以利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt

COPY app ./app

# 非 root 运行
RUN useradd -m appuser && mkdir -p /app/data && chown appuser /app/data
USER appuser

# 记忆库（SQLite）放在数据卷里，重建容器不丢
ENV MEMORY_DB_PATH=/app/data/querypilot.db
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
