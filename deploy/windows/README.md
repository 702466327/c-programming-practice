# Windows Docker 部署

## 环境要求
- Windows 10/11 或 Windows Server 2019+，已启用 **WSL2**
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)（WSL2 后端）
- OpenSSL：`winget install -e --id ShiningLight.OpenSSL`

## 部署
```powershell
# 1. 启动 Docker Desktop 并等待引擎就绪
# 2. 上传/解压项目到本地目录, 如 D:\aijudge
cd D:\aijudge

# 3. 一键部署
powershell -ExecutionPolicy Bypass -File deploy\windows\deploy.ps1
```

脚本自动完成：构建判题镜像 → 生成 .env → 生成自签名证书 → `docker compose up` → 健康检查。

> 应用容器挂载 `data/`（持久化）与 `/var/run/docker.sock`（调度判题容器），判题与部署完全 Docker 隔离。

## 访问
- `https://localhost`（本机，自签名证书需信任）
- `https://<本机IP>`（局域网，先设置 .env 中 ADMIN_KEY 并放行防火墙 443）

## 运维
```powershell
docker compose -f docker\docker-compose.yml logs -f app
docker compose -f docker\docker-compose.yml restart app
docker compose -f docker\docker-compose.yml down
```
