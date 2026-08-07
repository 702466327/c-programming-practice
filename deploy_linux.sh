#!/usr/bin/env bash
# AIJudge Docker judge - Linux one-shot deploy script (ASCII only)
#
# Usage (as root on the server, from the project directory):
#   bash deploy_linux.sh [project_dir] [public_ip]
# Example:
#   bash deploy_linux.sh /opt/project 43.143.47.242
#
# What it does:
#   1. checks docker
#   2. builds aijudge-judge:latest (uses Dockerfile.cn if present)
#   3. creates deploy_config.env from deploy_config.txt
#   4. generates self-signed certs if missing (needs public IP)
#   5. installs a systemd unit and starts the service (JUDGE_BACKEND=docker)
#   6. health check

set -euo pipefail

PROJ="${1:-$(pwd)}"
PUBLIC_IP="${2:-}"

cd "$PROJ"
echo "[i] Project dir: $PROJ"
echo "[i] Public IP  : ${PUBLIC_IP:-<not set>}"

# 1. Docker check
if ! command -v docker >/dev/null 2>&1; then
    echo "[!] docker CLI not found. Install Docker CE first (the Tencent template already has it)."
    exit 1
fi
docker info >/dev/null 2>&1 || { echo "[!] docker daemon not running (systemctl start docker)"; exit 1; }
echo "[OK] docker available"

# 2. Build judge image (CN mirror variant preferred)
if [ -f docker-judge/Dockerfile.cn ]; then
    cp docker-judge/Dockerfile.cn docker-judge/Dockerfile
    echo "[i] using Tencent-mirror Dockerfile.cn"
fi
if [ -z "$(docker images -q aijudge-judge:latest 2>/dev/null)" ]; then
    docker build -t aijudge-judge:latest docker-judge
fi
echo "[OK] image aijudge-judge:latest ready"

# 3. Env file from deploy_config.txt
if [ ! -f deploy_config.env ]; then
    grep -E '^[A-Za-z_][A-Za-z0-9_]*=' deploy_config.txt > deploy_config.env || true
    sed -i '/^TLS_CERT=/d; /^TLS_KEY=/d; /^PORT=/d; /^TLS_ENABLED=/d' deploy_config.env
    echo "TLS_CERT=$PROJ/certs/server.crt" >> deploy_config.env
    echo "TLS_KEY=$PROJ/certs/server.key"  >> deploy_config.env
    echo "PORT=8081"                        >> deploy_config.env
    echo "TLS_ENABLED=1"                    >> deploy_config.env
fi
echo "[OK] deploy_config.env ready"

# 4. Self-signed certs if missing
mkdir -p certs
if [ ! -f certs/server.crt ] || [ ! -f certs/server.key ]; then
    CN="${PUBLIC_IP:-localhost}"
    if [ -z "$PUBLIC_IP" ]; then
        echo "[!] no public IP given; generating cert with CN=localhost (replace later with certbot for a real domain)"
    fi
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
        -keyout certs/server.key -out certs/server.crt \
        -subj "/CN=$CN" -addext "subjectAltName=IP:$CN"
    echo "[OK] self-signed certs generated (CN=$CN)"
fi

# 5. systemd unit
UNIT=/etc/systemd/system/aijudge.service
cat > "$UNIT" <<EOF
[Unit]
Description=AIJudge Server
After=docker.service network-online.target
Wants=docker.service

[Service]
WorkingDirectory=$PROJ
EnvironmentFile=$PROJ/deploy_config.env
Environment=JUDGE_BACKEND=docker
ExecStart=/usr/bin/python3 $PROJ/code/server.py
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now aijudge
echo "[OK] service installed and started (aijudge)"

# 6. Health check
for i in $(seq 1 30); do
    code=$(curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1:8081/api/questions || true)
    if [ "$code" = "200" ]; then
        echo "[OK] health check passed: https://127.0.0.1:8081"
        echo "     remember to open TCP 8081 in the cloud firewall/security group"
        exit 0
    fi
    sleep 1
done
echo "[!] health check failed. Logs: journalctl -u aijudge -n 50"
exit 1
