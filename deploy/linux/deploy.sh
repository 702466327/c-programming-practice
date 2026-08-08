#!/usr/bin/env bash
# AIJudge Linux Docker 一键部署
# 用法: bash deploy.sh [公网IP] [域名(可选, 用于Let's Encrypt提示)]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJ="$(cd "$SCRIPT_DIR/../.." && pwd)"
PUBLIC_IP="${1:-}"
DOMAIN="${2:-}"
cd "$PROJ"

echo "========================================"
echo " AIJudge Linux Docker 部署"
echo " 项目目录: $PROJ"
echo "========================================"

# 1. Docker 检查
if ! command -v docker >/dev/null 2>&1; then
    echo "[!] 未找到 docker, 请先安装: curl -fsSL https://get.docker.com | sh"
    exit 1
fi
docker info >/dev/null 2>&1 || { echo "[!] Docker 守护进程未运行"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "[!] 需要 docker compose v2 插件"; exit 1; }
echo "[OK] Docker 就绪"

# 2. 构建判题镜像 (国内用腾讯云源版)
JUDGE_DF="docker/judge.Dockerfile"
[ -f "$JUDGE_DF.cn" ] && JUDGE_DF="docker/judge.Dockerfile.cn"
echo "[i] 构建判题镜像 (使用 $JUDGE_DF)..."
docker build -f "$JUDGE_DF" -t aijudge-judge:latest docker/

# 3. 环境变量 (.env 供 compose 使用)
if [ ! -f .env ]; then
    [ -f deploy_config.txt ] && mv deploy_config.txt .env || true
fi
if [ ! -f .env ]; then
    cat > .env <<'EOF'
# AIJudge 环境配置
ADMIN_KEY=
AI_API_KEY=
EOF
fi
echo "[OK] 环境配置: .env (请检查 ADMIN_KEY 是否已设置)"

# 4. TLS 证书
mkdir -p certs
if [ ! -f certs/server.crt ] || [ ! -f certs/server.key ]; then
    CN="${DOMAIN:-${PUBLIC_IP:-localhost}}"
    echo "[i] 生成自签名证书 (CN=$CN)"
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
        -keyout certs/server.key -out certs/server.crt \
        -subj "/CN=$CN" -addext "subjectAltName=IP:${PUBLIC_IP:-127.0.0.1},DNS:${DOMAIN:-localhost}"
    if [ -n "$DOMAIN" ]; then
        echo "[i] 有域名可申请正式证书 (HTTP-01 需放行 80):"
        echo "    apt install -y certbot && certbot certonly --standalone -d $DOMAIN"
        echo "    然后将 .env 中 TLS_CERT/TLS_KEY 指向 /etc/letsencrypt/live/$DOMAIN/{fullchain.pem,privkey.pem} 并重启"
    fi
fi

# 5. 启动应用容器
echo "[i] 启动应用容器..."
docker compose -f docker/docker-compose.yml up -d --build

# 6. 健康检查
echo "[i] 健康检查..."
for i in $(seq 1 30); do
    code=$(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1/api/questions || true)
    [ "$code" = "200" ] && { echo "[OK] 部署成功: https://${DOMAIN:-$PUBLIC_IP}"; exit 0; }
    sleep 2
done
echo "[!] 健康检查未通过, 查看日志: docker logs aijudge-app"
exit 1
