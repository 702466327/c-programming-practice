# Linux Docker 部署

## 环境要求
- Ubuntu 22.04/24.04 或 Debian 12（2C2G 起，2C4G 更从容）
- Docker Engine + compose v2

## 部署
```bash
# 1. 上传整个项目到服务器 (如 /opt/aijudge), 解压便携环境(如需本地工具)
cd /opt/aijudge

# 2. 一键部署
bash deploy/linux/deploy.sh <服务器公网IP> [域名]
```

脚本自动完成：构建判题镜像（腾讯云源加速版）→ 生成 .env → 生成自签名证书 → `docker compose up` → 健康检查。

## 正式证书（有域名时）
```bash
apt install -y certbot
certbot certonly --standalone -d your.domain -d www.your.domain   # 需放行 80
# 编辑 .env:
#   TLS_CERT=/etc/letsencrypt/live/your.domain/fullchain.pem
#   TLS_KEY=/etc/letsencrypt/live/your.domain/privkey.pem
docker compose -f docker/docker-compose.yml up -d
```

## 运维
```bash
docker compose -f docker/docker-compose.yml logs -f app   # 日志
docker compose -f docker/docker-compose.yml restart app   # 重启
docker compose -f docker/docker-compose.yml down          # 停止
```
- 数据持久化：`data/` 目录（用户/会话/提交/排行榜）
- 防火墙放行：443（Web）、80（证书续期）；22（SSH 建议限制来源）
