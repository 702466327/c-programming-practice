# AIJudge Windows Docker 一键部署 (Docker Desktop + WSL2 后端)
# 用法: powershell -ExecutionPolicy Bypass -File deploy.ps1 [-PublicIP 1.2.3.4]
param([string]$PublicIP = "", [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'

Write-Host '========================================'
Write-Host ' AIJudge Windows Docker 部署'
Write-Host " 项目目录: $ProjectDir"
Write-Host '========================================'

# 1. Docker 检查
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host '[!] Docker 不可用。请安装 Docker Desktop (WSL2 后端) 并启动:'
    Write-Host '    https://www.docker.com/products/docker-desktop/'
    exit 1
}
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Write-Host '[!] 需要 docker compose v2 插件'; exit 1 }
Write-Host '[OK] Docker 就绪'

# 2. OpenSSL 检查 (生成自签名证书用)
$ossl = Get-Command openssl -ErrorAction SilentlyContinue
if (-not $ossl) {
    Write-Host '[!] 未找到 openssl, 请安装: winget install -e --id ShiningLight.OpenSSL'
    exit 1
}

# 3. 构建判题镜像
Push-Location $ProjectDir
$judgeDf = 'docker\judge.Dockerfile'
if (Test-Path "$judgeDf.cn") { $judgeDf = "$judgeDf.cn" }
Write-Host "[i] 构建判题镜像 ($judgeDf)..."
docker build -f $judgeDf -t aijudge-judge:latest .

# 4. 环境配置
if (-not (Test-Path '.env')) {
    Set-Content -Path '.env' -Value @"
ADMIN_KEY=
AI_API_KEY=
"@ -Encoding ascii
}
Write-Host '[OK] 环境配置: .env (请设置 ADMIN_KEY)'

# 5. 自签名证书
New-Item -ItemType Directory -Force -Path 'certs' | Out-Null
if (-not (Test-Path 'certs\server.crt') -or -not (Test-Path 'certs\server.key')) {
    $cn = if ($PublicIP) { $PublicIP } else { 'localhost' }
    $san = if ($PublicIP) { "IP:$PublicIP" } else { 'IP:127.0.0.1' }
    Write-Host "[i] 生成自签名证书 (CN=$cn)"
    & openssl req -x509 -newkey rsa:2048 -nodes -days 825 `
        -keyout certs\server.key -out certs\server.crt `
        -subj "/CN=$cn" -addext "subjectAltName=$san"
}

# 6. 启动应用容器
Write-Host '[i] 启动应用容器...'
docker compose -f docker\docker-compose.yml up -d --build

# 7. 健康检查
Write-Host '[i] 健康检查...'
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    $code = & curl.exe -sk -o NUL -w '%{http_code}' https://127.0.0.1/api/questions
    if ($code -eq '200') {
        $url = if ($PublicIP) { "https://$PublicIP" } else { 'https://localhost' }
        Write-Host "[OK] 部署成功: $url"
        Pop-Location
        exit 0
    }
}
Write-Host '[!] 健康检查未通过, 查看日志: docker logs aijudge-app'
Pop-Location
exit 1
