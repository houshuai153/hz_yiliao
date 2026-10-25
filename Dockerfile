# ---- 前端构建阶段：只在构建镜像时联网（npm ci）；运行时镜像里没有 Node，也不含任何密钥 ----
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- 运行阶段 ----
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖层：只在构建镜像时联网下载；容器运行时不需要外网
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY seed ./seed
COPY scripts ./scripts
COPY config ./config
COPY knowledge ./knowledge
COPY eval ./eval
# React 构建产物由 FastAPI 同源托管（/assets 与前端路由回落）；API_TOKEN 不在其中
COPY --from=web /web/dist ./web/dist

# 非 root 运行；data/ 放数据库(SQLite 时)、向量索引、审计日志、生成的报告
RUN useradd --create-home --uid 10001 appuser     && mkdir -p /app/data     && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
