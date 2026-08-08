# AIJudge 应用镜像 (服务端 + 判题调度)
# 构建: docker build -f docker/app.Dockerfile -t aijudge-app:latest .
FROM python:3.12-slim

# docker CLI: 用于调度"兄弟判题容器" (judge_docker.py)
# Debian trixie 的 docker.io 包不含客户端, 这里安装官方静态 CLI
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
 && curl -fsSL -o /tmp/docker.tgz \
        https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz \
 && tar -xzf /tmp/docker.tgz -C /tmp \
 && install -m 0755 /tmp/docker/docker /usr/local/bin/docker \
 && rm -rf /tmp/docker* /var/lib/apt/lists/*

WORKDIR /app
COPY app/ ./app/

# data/ 与 certs/ 通过卷挂载 (见 docker-compose.yml)
EXPOSE 443

CMD ["python", "app/server.py"]
