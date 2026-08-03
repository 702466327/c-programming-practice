# AI 编程练习助手 - 一键部署脚本
# 同时启动本地服务器 + ngrok 公网隧道并显示地址
# 每项配置留空则使用安全默认行为

$ErrorActionPreference = "Stop"
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$script:shutdownRequested = $false
$script:originalTreatControlCAsInput = $false

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  AI 编程练习助手 - 一键部署" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ==================== 配置区 ====================
# 以下全部留空则使用安全默认行为
# 修改后重启脚本生效

$cfgAdminKey = ""
$cfgAIKey    = ""
$cfgNgrokAuthtoken = ""
$cfgNgrokDomain = ""
$defaultAdminKey = ""
$defaultAIKey = ""
$defaultNgrokAuthtoken = ""
$defaultNgrokDomain = ""

# ---- 读取配置文件（如果存在）----
$configFile = Join-Path $scriptDir "deploy_config.txt"
if (Test-Path $configFile) {
    $lines = Get-Content $configFile -Encoding UTF8
    foreach ($line in $lines) {
        $line = $line.Trim()
        if ($line -match '^(\w+)\s*=\s*(.*)$') {
            $val = $matches[2].Trim()
            switch ($matches[1]) {
                "ADMIN_KEY"            { $cfgAdminKey = $val }
                "AI_API_KEY"           { $cfgAIKey = $val }
                "NGROK_AUTHTOKEN"      { $cfgNgrokAuthtoken = $val }
                "NGROK_DOMAIN"         { $cfgNgrokDomain = $val }
                "DEFAULT_ADMIN_KEY"    { $defaultAdminKey = $val }
                "DEFAULT_AI_API_KEY"   { $defaultAIKey = $val }
                "DEFAULT_NGROK_AUTHTOKEN" { $defaultNgrokAuthtoken = $val }
                "DEFAULT_NGROK_DOMAIN" { $defaultNgrokDomain = $val }
            }
        }
    }
}

# 兼容旧版配置：若未区分默认值，则将现有配置视为默认值
if (-not $defaultAdminKey -and $cfgAdminKey) { $defaultAdminKey = $cfgAdminKey }
if (-not $defaultAIKey -and $cfgAIKey) { $defaultAIKey = $cfgAIKey }
if (-not $defaultNgrokAuthtoken -and $cfgNgrokAuthtoken) { $defaultNgrokAuthtoken = $cfgNgrokAuthtoken }
if (-not $defaultNgrokDomain -and $cfgNgrokDomain) { $defaultNgrokDomain = $cfgNgrokDomain }

function Get-MaskedState([string]$value, [string]$emptyText = "未设置") {
    if ($value) { return "已保存" }
    return $emptyText
}

function Get-BundledToolPath([string[]]$RelativePaths) {
    foreach ($relativePath in $RelativePaths) {
        $fullPath = Join-Path $scriptDir $relativePath
        if (Test-Path $fullPath) {
            return $fullPath
        }
    }
    return $null
}

function Get-SystemCommandPath([string[]]$Names) {
    foreach ($name in $Names) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            if ($cmd.Source) { return $cmd.Source }
            return $cmd.Name
        }
    }
    return $null
}

function Get-PreferredTool([string[]]$BundledRelativePaths, [string[]]$SystemNames) {
    $bundledPath = Get-BundledToolPath $BundledRelativePaths
    if ($bundledPath) {
        return [pscustomobject]@{
            Path = $bundledPath
            Source = "bundled"
            Name = Split-Path -Leaf $bundledPath
        }
    }

    $systemPath = Get-SystemCommandPath $SystemNames
    if ($systemPath) {
        return [pscustomobject]@{
            Path = $systemPath
            Source = "system"
            Name = Split-Path -Leaf $systemPath
        }
    }

    return $null
}

function Get-CompilerTool {
    return Get-PreferredTool @(
        "runtime\mingw\bin\g++.exe",
        "runtime\mingw\bin\clang++.exe",
        "runtime\mingw\bin\c++.exe"
    ) @("g++", "clang++", "c++")
}

function Get-ListeningProcessId([int]$Port) {
    $netTcp = Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue
    if ($netTcp) {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($conn) { return $conn.OwningProcess }
    }

    $netstatLines = netstat -ano -p tcp 2>$null
    foreach ($line in $netstatLines) {
        if ($line -match "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$") {
            return [int]$matches[1]
        }
    }

    return $null
}

function Stop-ProcessByPort([int]$Port, [string]$Label) {
    $procId = Get-ListeningProcessId $Port
    if (-not $procId) { return $true }

    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    $procName = if ($proc) { $proc.ProcessName } else { "PID $procId" }
    Write-Host "[!] $Label 端口 $Port 已被占用：$procName" -ForegroundColor Yellow
    $choice = Read-Host "    输入 Y 结束该进程并继续，其他任意键取消启动"
    if ($choice -eq "Y" -or $choice -eq "y") {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
        return $true
    }

    return $false
}

function Test-HttpReady([string[]]$Urls) {
    foreach ($url in $Urls) {
        try {
            $null = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 2
            return $true
        } catch {}
    }
    return $false
}

function Write-SessionLog([string]$Path, [string]$Message) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp] $Message`r`n"
    try {
        [System.IO.File]::AppendAllText($Path, $line, [System.Text.Encoding]::UTF8)
    } catch {}
}

function Get-SecretState([string]$Value) {
    if ($Value) { return "set" }
    return "unset"
}

# ---- 交互式确认/修改配置 ----
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  配置 (直接回车 = 使用安全默认行为)" -ForegroundColor DarkGray
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. 管理员密钥
Write-Host "  1. 管理员密钥" -ForegroundColor White
$displayAdminKey = if ($cfgAdminKey) { $cfgAdminKey } else { "未设置（将自动生成强口令）" }
 $displayDefaultAdminKey = if ($defaultAdminKey) { $defaultAdminKey } else { "未保存默认值" }
Write-Host "     当前: $displayAdminKey" -ForegroundColor DarkGray
Write-Host "     默认: $displayDefaultAdminKey" -ForegroundColor DarkGray
$input1 = Read-Host "     输入新密钥；D = 恢复默认；回车 = 保持当前"
if ($input1 -match '^[Dd]$') {
    if ($defaultAdminKey) {
        $cfgAdminKey = $defaultAdminKey
        Write-Host "     [OK] 已恢复默认管理员密钥" -ForegroundColor Green
    } else {
        Write-Host "     [OK] 未保存默认值，保持当前配置" -ForegroundColor DarkGray
    }
} elseif ($input1) {
    $cfgAdminKey = $input1
    Write-Host "     [OK] 当前管理员密钥已更新" -ForegroundColor Green
} else {
    Write-Host "     [OK] 未修改" -ForegroundColor DarkGray
}
Write-Host ""

# 2. 是否启用 AI 功能
Write-Host "  2. 是否启用 AI 点评功能? [Y/n]" -ForegroundColor White
$enableAI = Read-Host "     输入 Y 启用, N 禁用 (回车 = 启用)"
if ($enableAI -eq "" -or $enableAI -eq "Y" -or $enableAI -eq "y") {
    $cfgAIEnabled = $true
    $displayCurrentAI = Get-MaskedState $cfgAIKey "未设置当前密钥"
    $displayDefaultAI = Get-MaskedState $defaultAIKey "未设置默认密钥"
    Write-Host "     [OK] AI 功能已启用" -ForegroundColor Green
    Write-Host "     当前 AI 密钥: $displayCurrentAI" -ForegroundColor DarkGray
    Write-Host "     默认 AI 密钥: $displayDefaultAI" -ForegroundColor DarkGray
    Write-Host "     回车 = 使用当前密钥；D = 恢复默认密钥；M = 手动输入新密钥" -ForegroundColor DarkGray
    $aiMode = Read-Host "     请选择"
    if ($aiMode -eq "D" -or $aiMode -eq "d") {
        if ($defaultAIKey) {
            $cfgAIKey = $defaultAIKey
            Write-Host "     [OK] 已恢复默认 AI 密钥" -ForegroundColor Green
        } else {
            Write-Host "     [OK] 未保存默认 AI 密钥，保持当前配置" -ForegroundColor DarkGray
        }
    } elseif ($aiMode -eq "M" -or $aiMode -eq "m") {
        $inputAIKey = Read-Host "     请输入 AI 密钥"
        if ($inputAIKey) {
            $cfgAIKey = $inputAIKey
            Write-Host "     [OK] 当前 AI 密钥已更新" -ForegroundColor Green
        } else {
            Write-Host "     [OK] 未输入新密钥，保持当前配置" -ForegroundColor DarkGray
        }
    } else {
        if ($cfgAIKey) {
            Write-Host "     [OK] 使用当前 AI 密钥" -ForegroundColor Green
        } elseif ($defaultAIKey) {
            $cfgAIKey = $defaultAIKey
            Write-Host "     [OK] 当前未设置 AI 密钥，已自动使用默认密钥" -ForegroundColor Green
        } else {
            Write-Host "     [OK] 当前和默认 AI 密钥都未设置" -ForegroundColor DarkGray
        }
    }
} else {
    $cfgAIEnabled = $false
    Write-Host "     [OK] AI 功能已禁用" -ForegroundColor Yellow
}
Write-Host ""

# 3. 是否启用公网映射
Write-Host "  3. 是否启用公网映射 (ngrok)? [Y/n]" -ForegroundColor White
Write-Host "     启用后可从校外通过网址访问" -ForegroundColor DarkGray
$enableNgrok = Read-Host "     输入 Y 启用, N 禁用 (回车 = 启用)"
if ($enableNgrok -eq "" -or $enableNgrok -eq "Y" -or $enableNgrok -eq "y") {
    $cfgNgrokEnabled = $true
    Write-Host "     [OK] 公网映射已启用" -ForegroundColor Green
    if ($cfgNgrokDomain) {
        Write-Host "     当前模式: 固定域名 $cfgNgrokDomain" -ForegroundColor DarkGray
    } else {
        Write-Host "     当前模式: 随机公网地址" -ForegroundColor DarkGray
    }
    Write-Host "     当前固定域名: $(if ($cfgNgrokDomain) { $cfgNgrokDomain } else { "未设置" })" -ForegroundColor DarkGray
    Write-Host "     默认固定域名: $(if ($defaultNgrokDomain) { $defaultNgrokDomain } else { "未设置" })" -ForegroundColor DarkGray
    Write-Host "     当前 authtoken: $(Get-MaskedState $cfgNgrokAuthtoken)" -ForegroundColor DarkGray
    Write-Host "     默认 authtoken: $(Get-MaskedState $defaultNgrokAuthtoken "未保存默认值")" -ForegroundColor DarkGray
    Write-Host "     回车 = 使用当前公网配置；D = 恢复默认公网配置；M = 手动修改当前公网配置" -ForegroundColor DarkGray
    $ngrokMode = Read-Host "     请选择"
    if ($ngrokMode -eq "D" -or $ngrokMode -eq "d") {
        $cfgNgrokDomain = $defaultNgrokDomain
        $cfgNgrokAuthtoken = $defaultNgrokAuthtoken
        Write-Host "     [OK] 已恢复默认公网配置" -ForegroundColor Green
    } elseif ($ngrokMode -eq "M" -or $ngrokMode -eq "m") {
        $inputNgrokDomain = Read-Host "     请输入固定域名 (回车保持当前；输入 NONE 改为随机地址)"
        if ($inputNgrokDomain -eq "NONE" -or $inputNgrokDomain -eq "none") {
            $cfgNgrokDomain = ""
            Write-Host "     [OK] 当前公网地址模式已改为随机分配" -ForegroundColor Green
        } elseif ($inputNgrokDomain) {
            $cfgNgrokDomain = $inputNgrokDomain
            Write-Host "     [OK] 当前固定域名已更新" -ForegroundColor Green
        }

        $inputNgrokToken = Read-Host "     请输入 ngrok authtoken (回车保持当前)"
        if ($inputNgrokToken) {
            $cfgNgrokAuthtoken = $inputNgrokToken
            Write-Host "     [OK] 当前 ngrok authtoken 已更新" -ForegroundColor Green
        }
    } else {
        Write-Host "     [OK] 使用当前公网配置" -ForegroundColor Green
    }
} else {
    $cfgNgrokEnabled = $false
    Write-Host "     [OK] 公网映射已禁用，仅本机访问" -ForegroundColor Yellow
}
Write-Host ""

# ---- 应用安全默认值 ----
if (-not $cfgAdminKey) {
    $cfgAdminKey = [guid]::NewGuid().ToString("N")
    Write-Host "[i] 未设置管理员密钥，已为当前部署生成强口令" -ForegroundColor Yellow
    if (-not $defaultAdminKey) {
        $defaultAdminKey = $cfgAdminKey
    }
}
if ($cfgAIEnabled -and -not $cfgAIKey) {
    Write-Host "[i] 当前与默认 AI 密钥都不可用，AI 功能将自动禁用" -ForegroundColor Yellow
    $cfgAIEnabled = $false
}

$adminKey  = $cfgAdminKey
$aiKey     = if ($cfgAIEnabled -and $cfgAIKey) { $cfgAIKey } else { "" }
$ngrokDomain = $cfgNgrokDomain
$startNgrok = $cfgNgrokEnabled

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  本次部署配置" -ForegroundColor DarkGray
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  管理员密钥:   $adminKey" -ForegroundColor Green

if ($cfgAIEnabled) { Write-Host "  AI 功能:      已启用" -ForegroundColor Green }
else               { Write-Host "  AI 功能:      已禁用" -ForegroundColor Yellow }

if ($cfgNgrokEnabled) {
    if ($ngrokDomain) { Write-Host "  公网映射:     已启用 (固定域名)" -ForegroundColor Green }
    else              { Write-Host "  公网映射:     已启用" -ForegroundColor Green }
} else {
    Write-Host "  公网映射:     已禁用 (仅本机)" -ForegroundColor Yellow
}
Write-Host ""

# ---- 检查 Python ----
$pythonTool = Get-PreferredTool @(
    "runtime\python\python.exe",
    "runtime\python\python3.exe"
) @("python", "py")
$pythonExe = $null
$pythonArgs = @("-u", "server.py")

if ($pythonTool) {
    $pythonExe = $pythonTool.Path
    if ($pythonTool.Source -eq "system" -and $pythonTool.Name -ieq "py") {
        $pythonArgs = @("-3", "-u", "server.py")
    }
    if ($pythonTool.Source -eq "bundled") {
        Write-Host "[OK] 已检测到自带 Python: $($pythonTool.Path)" -ForegroundColor Green
    } else {
        Write-Host "[OK] 已检测到系统 Python: $($pythonTool.Path)" -ForegroundColor Green
    }
}

if (-not $pythonExe) {
    Write-Host "[X] 未检测到 Python。请将便携版 Python 放到 runtime\python\，或先安装 Python 3 并加入 PATH" -ForegroundColor Red
    exit 1
}

# ---- 检查 C++ 编译器 ----
$compilerTool = Get-CompilerTool
if ($compilerTool) {
    if ($compilerTool.Source -eq "bundled") {
        Write-Host "[OK] 已检测到自带 C++ 编译器: $($compilerTool.Path)" -ForegroundColor Green
    } else {
        Write-Host "[OK] 已检测到系统 C++ 编译器: $($compilerTool.Path)" -ForegroundColor Green
    }
} else {
    Write-Host "[!] 未检测到 g++ / clang++ / c++。请将 MinGW 放到 runtime\mingw\bin\，否则判题功能不可用" -ForegroundColor Yellow
}

# ---- 检查 ngrok ----
if ($startNgrok) {
    $ngrokTool = Get-PreferredTool @(
        "runtime\ngrok\ngrok.exe",
        "ngrok.exe"
    ) @("ngrok")
    if ($ngrokTool) {
        $ngrokExe = $ngrokTool.Path
        if ($ngrokTool.Source -eq "bundled") {
            Write-Host "[OK] 已检测到自带 ngrok: $ngrokExe" -ForegroundColor Green
        } else {
            Write-Host "[OK] 已检测到系统 ngrok: $ngrokExe" -ForegroundColor Green
        }
    } else {
        Write-Host "[!] 未找到 ngrok.exe，公网隧道不可用" -ForegroundColor Yellow
        Write-Host "    请将 ngrok.exe 放到 runtime\ngrok\ 或项目根目录" -ForegroundColor DarkGray
        $startNgrok = $false
    }
}

# ---- 配置 ngrok authtoken ----
if ($startNgrok) {
    if ($cfgNgrokAuthtoken) {
        & $ngrokExe config add-authtoken $cfgNgrokAuthtoken | Out-Null
        Write-Host "[OK] 已从 deploy_config.txt 自动配置 ngrok authtoken" -ForegroundColor Green
        Write-Host ""
    } else {
        $ngrokYml = Join-Path $env:USERPROFILE "AppData\Local\ngrok\ngrok.yml"
        if (-not (Test-Path $ngrokYml)) {
            Write-Host ""
            Write-Host "[!] 首次使用，需要 ngrok authtoken" -ForegroundColor Yellow
            Write-Host "    1. 浏览器打开 https://dashboard.ngrok.com/get-started/your-authtoken"
            Write-Host "    2. 登录后复制 authtoken"
            Write-Host "    3. 粘贴到下方"
            Write-Host ""
            $authToken = Read-Host "   authtoken"
            if ($authToken) {
                & $ngrokExe config add-authtoken $authToken
                $cfgNgrokAuthtoken = $authToken
                $defaultNgrokAuthtoken = $authToken
                Write-Host "[OK] authtoken 已配置" -ForegroundColor Green
            }
            Write-Host ""
        }
    }
}

# ---- 保存配置到文件 ----
$saveContent = @"
DEFAULT_ADMIN_KEY=$defaultAdminKey
DEFAULT_AI_API_KEY=$defaultAIKey
DEFAULT_NGROK_AUTHTOKEN=$defaultNgrokAuthtoken
DEFAULT_NGROK_DOMAIN=$defaultNgrokDomain
ADMIN_KEY=$cfgAdminKey
AI_API_KEY=$cfgAIKey
NGROK_AUTHTOKEN=$cfgNgrokAuthtoken
NGROK_DOMAIN=$cfgNgrokDomain
"@
$saveContent | Out-File -FilePath $configFile -Encoding utf8 -Force

# ---- 检查端口占用 ----
if (-not (Stop-ProcessByPort 8081 "本地服务器")) {
    Write-Host "[X] 已取消启动，请先释放 8081 端口后重试" -ForegroundColor Red
    exit 1
}

if ($startNgrok) {
    if (-not (Stop-ProcessByPort 4040 "ngrok 管理接口")) {
        Write-Host "[X] 已取消启动，请先释放 4040 端口后重试" -ForegroundColor Red
        exit 1
    }
}

# ---- 设置环境变量 ----
$runtimeRoot = Join-Path $scriptDir "runtime"
$env:PROJECT_RUNTIME_ROOT = $runtimeRoot
$env:BUNDLED_COMPILER = ""
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"

if ($compilerTool -and $compilerTool.Source -eq "bundled") {
    $compilerDir = Split-Path -Parent $compilerTool.Path
    if ($env:PATH -notlike "$compilerDir*") {
        $env:PATH = "$compilerDir;$env:PATH"
    }
    $env:BUNDLED_COMPILER = $compilerTool.Path
}

$env:AI_API_KEY = $aiKey
$env:ADMIN_KEY = $adminKey
$env:OPEN_BROWSER = ""
if (-not $cfgAIEnabled) {
    Write-Host "[i] AI 功能已禁用，判题功能正常" -ForegroundColor DarkGray
}

# ---- 启动 Python 服务器 ----
Write-Host "[1/2] 启动本地服务器..." -ForegroundColor Yellow

$serverStdoutLog = Join-Path $scriptDir "server_stdout.log"
$serverStderrLog = Join-Path $scriptDir "server_stderr.log"
$logSessionTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$logSeparator = "==================== [$logSessionTime] Startup Session ===================="
Add-Content -Path $serverStdoutLog -Value ""
Add-Content -Path $serverStdoutLog -Value $logSeparator
Add-Content -Path $serverStderrLog -Value ""
Add-Content -Path $serverStderrLog -Value $logSeparator
$env:SERVER_STDOUT_LOG = $serverStdoutLog
$env:SERVER_STDERR_LOG = $serverStderrLog
$env:TEE_SERVER_LOGS = "1"
Write-SessionLog $serverStdoutLog "Launch configuration:"
Write-SessionLog $serverStdoutLog "  admin_key: $(Get-SecretState $adminKey)"
Write-SessionLog $serverStdoutLog "  ai_enabled: $cfgAIEnabled"
Write-SessionLog $serverStdoutLog "  ai_key: $(Get-SecretState $aiKey)"
Write-SessionLog $serverStdoutLog "  ngrok_enabled: $startNgrok"
Write-SessionLog $serverStdoutLog "  ngrok_domain: $(if ($ngrokDomain) { $ngrokDomain } else { 'random-or-disabled' })"
Write-SessionLog $serverStdoutLog "  python: $pythonExe"
Write-SessionLog $serverStdoutLog "  compiler: $(if ($compilerTool) { $compilerTool.Path } else { 'unavailable' })"
Write-SessionLog $serverStdoutLog "  ngrok: $(if ($startNgrok -and $ngrokExe) { $ngrokExe } else { 'disabled-or-unavailable' })"
Write-SessionLog $serverStderrLog "Launch configuration:"
Write-SessionLog $serverStderrLog "  admin_key: $(Get-SecretState $adminKey)"
Write-SessionLog $serverStderrLog "  ai_enabled: $cfgAIEnabled"
Write-SessionLog $serverStderrLog "  ai_key: $(Get-SecretState $aiKey)"
Write-SessionLog $serverStderrLog "  ngrok_enabled: $startNgrok"
Write-SessionLog $serverStderrLog "  ngrok_domain: $(if ($ngrokDomain) { $ngrokDomain } else { 'random-or-disabled' })"

$p = Start-Process $pythonExe -ArgumentList $pythonArgs `
    -WorkingDirectory (Join-Path $scriptDir "code") `
    -WindowStyle Normal `
    -PassThru

# 等服务器就绪
$ready = $false
$portListening = $false
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 1
    if ($p.HasExited) {
        break
    }
    $portListening = [bool](Get-ListeningProcessId 8081)
    if (Test-HttpReady @(
        "http://127.0.0.1:8081/api/ai-status",
        "http://127.0.0.1:8081/",
        "http://127.0.0.1:8081/api/questions"
    )) {
        $ready = $true
        break
    }
}
if ($ready -or ($portListening -and -not $p.HasExited)) {
    if (-not $ready) {
        Write-Host "[!] 健康检查未稳定返回，但 8081 端口已监听，按已启动处理" -ForegroundColor Yellow
    }
    Write-Host "[OK] 本地服务器已就绪" -ForegroundColor Green
} else {
    Write-Host "[X] 服务器启动超时，请检查 Python 环境" -ForegroundColor Red
    if ($p.HasExited) {
        Write-Host "---- process exit code ----" -ForegroundColor DarkGray
        Write-Host $p.ExitCode
    } else {
        Write-Host "---- process state ----" -ForegroundColor DarkGray
        if ($portListening) {
            Write-Host "Python 进程仍在运行，8081 端口已监听，但 HTTP 请求未在 45 秒内稳定返回"
        } else {
            Write-Host "Python 进程仍在运行，但 127.0.0.1:8081 未在 45 秒内监听"
        }
    }
    if (Test-Path $serverStdoutLog) {
        Write-Host "---- server stdout ----" -ForegroundColor DarkGray
        Get-Content -Path $serverStdoutLog -ErrorAction SilentlyContinue | Select-Object -Last 20
    }
    if (Test-Path $serverStderrLog) {
        Write-Host "---- server stderr ----" -ForegroundColor DarkGray
        Get-Content -Path $serverStderrLog -ErrorAction SilentlyContinue | Select-Object -Last 20
    }
    exit 1
}

# ---- 启动 ngrok ----
if ($startNgrok) {
    Write-Host "[2/2] 启动 ngrok 隧道..." -ForegroundColor Yellow

    if ($ngrokDomain) {
        Write-Host "  使用固定域名: $ngrokDomain" -ForegroundColor DarkGray
        $n = Start-Process $ngrokExe -ArgumentList "http 8081 --domain=$ngrokDomain --log=stdout" `
            -WindowStyle Minimized `
            -PassThru
    } else {
        $n = Start-Process $ngrokExe -ArgumentList "http 8081 --log=stdout" `
            -WindowStyle Minimized `
            -PassThru
    }

    # ---- 获取公网地址 ----
    Write-Host "    获取公网地址..." -ForegroundColor Yellow
    $publicUrl = $null
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        try {
            $r = Invoke-RestMethod "http://127.0.0.1:4040/api/tunnels" -TimeoutSec 2
            $publicUrl = ($r.tunnels | Where-Object { $_.proto -eq "https" }).public_url
            if ($publicUrl) { break }
        } catch {}
    }
}

# ---- 显示 ----
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  部署完成！" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "  本地地址:     http://localhost:8081" -ForegroundColor DarkGray

if ($startNgrok) {
    if ($ngrokDomain) {
        Write-Host "  公网地址:     https://$ngrokDomain" -ForegroundColor Green
    } elseif ($publicUrl) {
        Write-Host "  公网地址:     $publicUrl" -ForegroundColor Green
    } else {
        Write-Host "  公网地址:     获取中 -> http://127.0.0.1:4040" -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "  [发送公网地址给其他人即可从校外访问]" -ForegroundColor DarkGray
}

$launchUrl = "http://127.0.0.1:8081"
if ($startNgrok) {
    if ($ngrokDomain) {
        $launchUrl = "https://$ngrokDomain"
    } elseif ($publicUrl) {
        $launchUrl = $publicUrl
    }
}

Write-Host ""
Write-Host "  管理员入口:   点击页面底部-管理员入口" -ForegroundColor DarkGray
Write-Host "  管理密钥:     $adminKey" -ForegroundColor DarkGray
if (-not $cfgAIEnabled) { Write-Host "  AI 功能:      已禁用" -ForegroundColor Yellow }
Write-Host ""
Write-Host "  [修改配置: 重启脚本, 或编辑 deploy_config.txt]" -ForegroundColor DarkGray
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  按 Ctrl+C 停止所有服务" -ForegroundColor DarkGray
Write-Host "========================================" -ForegroundColor Cyan

try {
    Start-Process $launchUrl | Out-Null
    Write-Host "[OK] 已自动打开: $launchUrl" -ForegroundColor Green
} catch {
    Write-Host "[i] 自动打开浏览器失败，请手动访问: $launchUrl" -ForegroundColor Yellow
}

# ---- 保持运行 ----
try {
    $script:originalTreatControlCAsInput = [Console]::TreatControlCAsInput
    [Console]::TreatControlCAsInput = $true

    while ($true) {
        for ($tick = 0; $tick -lt 30; $tick++) {
            while ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if ($key.Key -eq [ConsoleKey]::C -and ($key.Modifiers -band [ConsoleModifiers]::Control)) {
                    $script:shutdownRequested = $true
                }
            }
            if ($script:shutdownRequested) { break }
            Start-Sleep -Milliseconds 100
        }
        if ($script:shutdownRequested) {
            Write-Host ""
            Write-Host "[i] 收到 Ctrl+C，准备关闭所有服务..." -ForegroundColor Yellow
            Write-SessionLog $serverStdoutLog "Ctrl+C received. Preparing graceful shutdown."
            break
        }
        if ($p.HasExited) {
            Write-Host ""
            Write-Host "[X] Python 服务器已退出" -ForegroundColor Red
            Write-Host "    ExitCode: $($p.ExitCode)" -ForegroundColor DarkGray
            Write-SessionLog $serverStdoutLog "Python server process exited."
            if ($p.ExitCode -ne 0) {
                Write-SessionLog $serverStderrLog "Python server process exited with code $($p.ExitCode)."
            }
            break
        }
    }
} finally {
    try { [Console]::TreatControlCAsInput = $script:originalTreatControlCAsInput } catch {}
    Write-Host "正在关闭..." -ForegroundColor Yellow
    $shutdownReasonStdout = "Shutdown reason: launcher requested stop while Python process was still running."
    $shutdownReasonStderr = $null
    if ($p) {
        if ($p.HasExited) {
            Write-Host "  关闭原因: Python 服务器进程已退出 (ExitCode=$($p.ExitCode))" -ForegroundColor DarkGray
            $shutdownReasonStdout = "Shutdown reason: Python process already exited with code $($p.ExitCode)."
            $shutdownReasonStderr = $shutdownReasonStdout
        } elseif ($script:shutdownRequested) {
            Write-Host "  关闭原因: 用户按下 Ctrl+C" -ForegroundColor DarkGray
            $shutdownReasonStdout = "Shutdown reason: Ctrl+C requested by user."
            $shutdownReasonStderr = $shutdownReasonStdout
        } else {
            Write-Host "  关闭原因: 启动脚本收到结束信号或主动结束" -ForegroundColor DarkGray
            $shutdownReasonStdout = "Shutdown reason: launcher requested stop while Python process was still running."
        }
    }
    if ($p -and -not $p.HasExited) {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        Wait-Process -Id $p.Id -ErrorAction SilentlyContinue -Timeout 5
    }
    if ($n -and -not $n.HasExited) {
        Stop-Process -Id $n.Id -Force -ErrorAction SilentlyContinue
        Wait-Process -Id $n.Id -ErrorAction SilentlyContinue -Timeout 5
    }
    Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-SessionLog $serverStdoutLog $shutdownReasonStdout
    Write-SessionLog $serverStdoutLog "Launcher shutdown requested."
    Write-SessionLog $serverStdoutLog "All managed processes stopped."
    if ($serverStderrLog -and $shutdownReasonStderr) {
        Write-SessionLog $serverStderrLog $shutdownReasonStderr
    }
    if ($serverStderrLog) {
        Write-SessionLog $serverStderrLog "Launcher session closed."
    }
    Write-Host "已全部关闭，再见" -ForegroundColor Green
}
