# AI 编程练习助手

面向 C++ 初学者的在线编程练习平台：100 道由易到难的练习题、g++ 在线判题（**Docker 沙箱隔离**）、AI 智能点评（多平台大模型）、多用户与排行榜、自动找回密码、管理员面板。

纯 Python 标准库实现（服务端零第三方依赖），前端单文件无外部资源。

---

## 目录结构

```text
project/
├── README.md                 # 本文件（总览 + 双平台部署）
├── LICENSE
├── .gitignore / .dockerignore
│
├── app/                      # 应用代码（构建进应用镜像）
│   ├── server.py             # 主服务（HTTPS 443, 判题一律 Docker）
│   ├── auth.py               # 认证 / 会话 / 管理员 / 配额 / 锁定
│   ├── judge.py              # 安全扫描与结果摘要工具
│   ├── judge_docker.py       # Docker 判题执行器（fail-closed）
│   ├── ai_client.py          # AI 点评客户端（OpenAI 兼容）
│   ├── question_bank.py      # 题库加载
│   ├── ratelimit.py          # 滑动窗口限流
│   └── index.html            # 前端（响应式单页）
│
├── data/                     # 运行时数据（挂载进容器持久化）
│   ├── questions.json        # 100 道题（含可见/隐藏测试用例）
│   └── ans.txt               # 参考答案
│
├── docker/                   # 镜像与编排（双平台共用）
│   ├── app.Dockerfile        # 应用镜像（python slim + docker CLI）
│   ├── judge.Dockerfile      # 判题镜像（debian + g++ + entrypoint）
│   ├── judge.Dockerfile.cn   # 判题镜像（腾讯云源加速版）
│   ├── entrypoint.sh         # 判题容器入口（编译+运行+超时强杀）
│   └── docker-compose.yml    # 应用编排
│
├── deploy/
│   ├── linux/
│   │   ├── README.md         # Linux Docker 部署文档
│   │   └── deploy.sh         # Linux 一键部署脚本
│   └── windows/
│       ├── README.md         # Windows Docker 部署文档
│       └── deploy.ps1        # Windows 一键部署脚本
│
└── runtime/                  # 便携运行环境（随仓库分发）
    ├── python/               # 便携 Python 3.13（PSF License）
    └── mingw/                # MinGW g++ 编译器（GPL-3.0 + GCC Runtime Exception）
```

## 架构：全部 Docker 隔离

```text
浏览器 ──HTTPS──> [app 容器]  server.py (认证/题库/会话/排行榜)
                        │  挂载 /var/run/docker.sock
                        ▼
              [判题容器] 一次性: 编译+运行 (无网络/非root/只读根文件系统)
```

- 应用与判题均运行在容器内，**没有本机执行模式**
- 判题容器每测试用例一个，运行完即销毁；Docker 不可用时拒绝判题（fail-closed）
- 判题隔离：非 root、只读根文件系统、无网络（`--network none`）、剥离全部 capabilities、默认 seccomp、内存 512MB / CPU 1 核 / 进程数 64 上限

## 部署

### Linux（推荐）

```bash
curl -fsSL https://get.docker.com | sh      # 安装 Docker
cd <项目目录>
bash deploy/linux/deploy.sh <公网IP> [域名]
```

详细步骤、正式证书（Let's Encrypt）与运维命令见 [deploy/linux/README.md](deploy/linux/README.md)。

### Windows（Docker Desktop）

```powershell
winget install -e --id ShiningLight.OpenSSL
cd <项目目录>
powershell -ExecutionPolicy Bypass -File deploy\windows\deploy.ps1
```

详细步骤与注意事项见 [deploy/windows/README.md](deploy/windows/README.md)。

## 便携运行环境（runtime/）

仓库直接携带解压后的便携环境（Python / MinGW g++），克隆或下载后**无需安装任何依赖**即可使用：

```bash
./runtime/python/python.exe --version        # 便携 Python
./runtime/mingw/bin/g++.exe --version        # 便携 g++
```

用途：本地开发调试、离线判题器复现、无系统级环境的快速体验。Docker 部署本身不依赖它（镜像内自带运行环境）。

## 管理员

- 管理密钥：`.env` 中 `ADMIN_KEY`（部署时生成/设置）；网页「管理员入口」登录后可查看/重置/删除用户
- 自动找回密码：注册时设置恢复密钥（≥4 字符）
- 数据备份：备份 `data/` 目录即可（用户/会话/提交/排行榜）

## API 一览

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|:---:|------|
| POST | `/api/register` | - | 注册（需设置恢复密钥） |
| POST | `/api/login` | - | 登录（5 次失败锁定 5 分钟） |
| POST | `/api/recover` | - | 找回密码（统一报错防枚举） |
| GET | `/api/questions` | - | 题目列表（仅示例用例） |
| GET | `/api/question/{id}` | - | 题目详情 |
| POST | `/api/submit` | Bearer | 提交判题（6 次/分钟） |
| POST | `/api/feedback` | Bearer | AI 点评（60 次/小时 + 300 次/天，基于最近一次真实提交） |
| GET | `/api/submissions` | Bearer | 我的提交 |
| GET | `/api/leaderboard` | Bearer | 排行榜（积分制） |
| POST | `/api/admin/*` | 管理员 | 用户管理 |

## 安全说明

- 判题：Docker 沙箱为唯一安全边界，静态黑名单仅作纵深防御（README 明确"不是安全边界"）
- 答案保护：每题 10-15 组用例，仅 3-5 组示例可见，隐藏用例只返回对错
- 密码：PBKDF2-SHA256 加盐（200,000 次迭代）；会话服务端存储，登出即失效
- 限流与锁定：登录/注册/找回/管理/提交/AI 点评均有窗口限流；连续失败锁定
- 安全头：CSP / X-Frame-Options / nosniff / HSTS / no-referrer / no-store
