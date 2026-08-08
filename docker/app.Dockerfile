# AIJudge 应用镜像 (服务端 + 判题调度)
# 构建: docker build -f docker/app.Dockerfile -t aijudge-app:latest .
FROM python:3.12-slim

# docker CLI: 用于调度"兄弟判题容器" (judge_docker.py)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        docker.io \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app/ ./app/

# data/ 与 certs/ 通过卷挂载 (见 docker-compose.yml)
EXPOSE 443

CMD ["python", "app/server.py"]
