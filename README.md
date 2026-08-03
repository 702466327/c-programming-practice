# AI 编程练习助手

面向 C++ 初学者的在线编程练习平台：支持 g++ 在线编译判题、AI 智能点评（多平台大模型）、多用户注册登录、自助找回密码、管理员面板、公网访问与移动端适配。

纯 Python 标准库实现，无需安装任何第三方依赖。

## 功能特性

- **在线判题**：g++ 实时编译运行，自动比对测试用例（题目、测试用例均不下发前端）
- **AI 智能点评**：支持 8 类 OpenAI 兼容接口，可一键拉取平台当前可用模型、测试连通性
- **启动管理窗口**：桌面窗口（或网页版）配置平台/密钥/服务器设置，一键启动、停止并查看实时日志
- **用户体系**：注册 / 登录 / 会话 / 自助找回密码 / 管理员重置与删除用户
- **公网访问**：可选 ngrok 隧道（随机地址或固定域名），也支持自有域名 + 反代
- **多端适配**：响应式页面，手机 / 平板 / 桌面均可使用
- **安全加固**：PBKDF2 加盐哈希、限流、统一登录报错、安全响应头、判题多层防护（详见下文）

## 快速开始

### 方式一：启动管理窗口（推荐）

Windows 下双击项目根目录的 `start.bat`，会弹出桌面窗口「AI 编程练习助手 - 启动管理」：

1. **AI 点评配置**：勾选启用，选择平台，填入 API 密钥；点「拉取可用模型」从平台获取模型列表，或点「测试 AI 连接」验证密钥
2. **管理员密钥**：填入自定义密钥，或留空让系统启动时自动生成随机强口令
3. **服务器设置**：修改监听端口、监听地址（公网 / 仅本机）、是否自动打开浏览器
4. **公网访问**：可选启用 ngrok 隧道，填写固定域名与 authtoken（留空 = 随机地址）
5. 点击「启动服务」，窗口会显示本地/公网地址与运行状态；「停止服务」关闭所有进程

启动界面会实时显示服务健康状态、AI 平台信息、运行日志，并自动保存配置。

> 窗口版需要 tkinter（标准 Python 安装自带）；若当前 Python 缺少 tkinter（如便携版 embeddable），`start.bat` 会自动回退到网页版启动页。

### 方式二：网页版启动页（备用）

```powershell
python code\launcher.py --no-browser
```

浏览器访问 `http://127.0.0.1:8299`。网页版与窗口版共用同一份配置和启停逻辑。

> 网页版启动页仅监听 `127.0.0.1`，请勿将该端口暴露到公网（页面内保存有明文密钥）。

### 方式三：手动启动

```powershell
cd code
$env:ADMIN_KEY="<你的管理员密钥>"
$env:AI_PLATFORM="deepseek"          # 可选，见下方平台表
$env:AI_API_KEY="<你的 AI 密钥>"
$env:AI_MODEL="deepseek-v4-flash"    # 可选，留空用平台默认模型
python server.py
```

浏览器访问 `http://localhost:8081`。不设置 AI 密钥时判题功能正常，仅 AI 点评不可用。

## AI 多平台支持

所有平台均使用 OpenAI 兼容的 `POST /chat/completions` 接口，通过启动页或环境变量配置：

| 平台 | 标识 | 默认接口地址 | 默认模型（2026-08） |
|------|------|--------------|----------|
| 硅基流动 SiliconFlow | `siliconflow` | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V4-Flash` |
| OpenAI | `openai` | `https://api.openai.com/v1` | `gpt-5.6-terra` |
| DeepSeek 深度求索 | `deepseek` | `https://api.deepseek.com/v1` | `deepseek-v4-flash` |
| 智谱 AI (GLM) | `zhipu` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4.7-flash` |
| 阿里云通义千问 DashScope | `dashscope` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Moonshot Kimi | `moonshot` | `https://api.moonshot.cn/v1` | `kimi-k2.6` |
| 腾讯混元 Hunyuan | `hunyuan` | `https://api.hunyuan.cloud.tencent.com/v1` | `hunyuan-turbo` |
| 自定义（任意兼容接口） | `custom` | 必填 | 必填 |

环境变量优先级：显式传入参数 > `AI_API_BASE` / `AI_MODEL` 环境变量 > 平台默认值。

> 在启动页填写平台与密钥后点击「拉取可用模型」，会调用各平台的 `GET /models` 接口返回当前可用模型供选择；若平台不支持该接口，则展示内置的常用模型备选清单。

## 公网访问

### 使用 ngrok（免费，有限流）

在启动界面勾选「启用 ngrok 公网隧道」：

- 固定域名留空：自动分配随机公网地址
- 填写固定域名（如 `xxx.ngrok-free.dev`）与 authtoken：使用固定地址

### 使用自有域名

1. 在域名注册商处将域名 A 记录解析到服务器公网 IP
2. 服务器上用 Nginx / Caddy 反向代理：`443 → 127.0.0.1:8081`，配置 HTTPS 证书
3. 中国大陆服务器对外提供服务需先完成 **ICP 备案**
4. 启动时在「服务器设置」中选择「仅本机」监听，仅把 80/443 暴露给反代

## 配置文件

| 文件 | 说明 |
|------|------|
| `launcher_config.json` | 启动页配置（自动生成，含本地密钥） |
| `deploy_config.txt` | 旧版命令行脚本兼容配置，由启动页自动同步生成 |
| `data/users.json` | 用户账号（PBKDF2 加盐哈希） |
| `data/sessions.json` | 登录会话，24 小时过期 |
| `data/submissions.json` | 用户提交记录 |
| `server_stdout.log` / `server_stderr.log` | 服务运行日志，追加写入 |

## 项目结构

```text
project/
├── start.bat            一键启动（打开启动管理页）
├── start.ps1            旧版命令行部署脚本（保留兼容）
├── deploy_config.txt    部署配置（自动生成）
├── launcher_config.json 启动页配置（自动生成）
├── code/                源码
│   ├── launcher_gui.py  桌面窗口版启动管理（tkinter，推荐）
│   ├── launcher.py      网页版启动管理后端（127.0.0.1:8299，备用）
│   ├── launcher.html    网页版启动管理前端
│   ├── server.py        主 HTTP 服务器（端口 8081）
│   ├── ai_client.py     AI 多平台客户端（OpenAI 兼容）
│   ├── judge.py         C++ 编译判题 + 安全防护
│   ├── auth.py          认证 / 会话 / 管理员 / 密码重置
│   ├── ratelimit.py     滑动窗口限流
│   ├── question_bank.py 题库加载
│   └── index.html       前端页面（响应式）
├── data/                数据文件
│   ├── questions.json   15 道 C++ 练习题
│   └── ans.txt          参考答案
└── runtime/             （可选）便携版运行环境
    ├── python/          Python 3.13
    ├── mingw/bin/       g++ 编译器
    └── ngrok/           ngrok 客户端
```

## 环境要求

| 项目 | 说明 |
|------|------|
| Python | 3.8+，仅标准库（http.server / json / subprocess / hashlib），无需 pip |
| C++ 编译器 | g++（MinGW-w64），用于判题 |
| ngrok（可选） | 内网穿透客户端 |
| 浏览器 | Chrome / Edge / Firefox，支持移动端 |
| AI（可选） | 任意 OpenAI 兼容接口密钥 |

若项目内存在 `runtime/` 目录，则 Python / g++ / ngrok 无需预装到系统，启动脚本会优先使用自带版本，其次回退系统环境。

## 系统架构

```text
启动管理窗口 (launcher_gui.py) ─┐
网页版启动页 (launcher.py, 127.0.0.1:8299) ─┼─ 配置注入 ─→ 主服务器 (server.py, 端口 8081)
  │                                          │            ├── auth.py        用户认证 + 管理员
  └─→ 启动 / 停止 / 状态 ─────────────────────┘            ├── question_bank  题库管理
                                                          ├── judge.py       g++ 编译 + 沙箱执行
                                                          ├── ai_client.py   多平台大模型 API
                                                          └── ngrok 隧道（可选）→ 公网地址
```

## API 一览

### 用户 API

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|:---:|------|
| POST | `/api/register` | - | 注册（username, password, recovery_key） |
| POST | `/api/login` | - | 登录，返回 Token |
| POST | `/api/logout` | Bearer | 登出 |
| POST | `/api/recover` | - | 自助找回密码 |
| GET | `/api/user` | Bearer | 当前用户 |

### 练习 API

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|:---:|------|
| GET | `/api/questions` | - | 题目列表（不含测试用例） |
| GET | `/api/question/{id}` | - | 题目详情 |
| GET | `/api/submissions` | Bearer | 用户提交记录 |
| POST | `/api/submit` | Bearer | 提交代码判题 |
| POST | `/api/feedback` | Bearer | 获取 AI 点评 |
| GET | `/api/ai-status` | - | AI 配置状态（平台 / 模型 / 是否可用） |

### 管理员 API

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|:---:|------|
| POST | `/api/admin/login` | - | 管理员登录（admin_key） |
| POST | `/api/admin/users` | Admin | 用户列表 |
| POST | `/api/admin/reset-password` | Admin | 重置用户密码 |
| POST | `/api/admin/delete-user` | Admin | 删除用户 |

### 启动页 API（网页版，仅本机）

> 窗口版直接调用与这些 API 相同的后端函数，不经过 HTTP。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/state` | 运行状态、脱敏配置、工具探测 |
| GET | `/api/platforms` | 支持的 AI 平台列表 |
| GET | `/api/logs` | 最近服务日志 |
| POST | `/api/config` | 保存配置 |
| POST | `/api/start` / `/api/stop` | 启动 / 停止服务 |
| POST | `/api/test-ai` | 测试 AI 连接 |
| POST | `/api/list-models` | 拉取平台当前可用模型列表 |
| POST | `/api/kill-port` | 结束占用指定端口的进程 |

## 题目列表

共 15 题，按难度排序：

| # | 题目 | 难度 | 知识点 |
|---|------|------|--------|
| 1 | A + B 问题 | 简单 | 输入输出 |
| 2 | 判断闰年 | 简单 | 条件判断 |
| 3 | 递归求阶乘 | 简单 | 递归 |
| 4 | 最大公约数 | 简单 | 辗转相除法 |
| 5 | 回文判断 | 简单 | 双指针 |
| 6 | 斐波那契数列 | 中等 | 循环 |
| 7 | 数组去重排序 | 中等 | STL sort/unique |
| 8 | 字符统计 | 中等 | cctype |
| 9 | 判断素数 | 中等 | 函数 |
| 10 | 冒泡排序 | 中等 | 排序算法 |
| 11 | 二分查找 | 中等 | 查找算法 |
| 12 | 矩阵转置 | 中等 | 二维数组 |
| 13 | 十进制转二进制 | 中等 | 进制转换 |
| 14 | 词频统计 | 困难 | STL map + 排序 |
| 15 | 学生成绩管理 | 困难 | struct + 综合 |

## 安全机制

- **答案保护**：前端 API 不下发测试用例，F12 无法偷看预期输出
- **密码安全**：PBKDF2-SHA256 加盐哈希（200,000 次迭代），旧的无盐 SHA256 哈希登录时自动迁移
- **特权账号防护**：禁止注册 `__` 开头的用户名，杜绝伪造 `__admin__`
- **统一登录报错**：用户名不存在与密码错误返回相同文案，避免用户枚举
- **接口限流**：登录 / 注册 / 找回密码 / 管理员登录均为滑动窗口限流
- **安全响应头**：CSP、`X-Frame-Options: DENY`、`X-Content-Type-Options`、`Referrer-Policy`、`Cache-Control: no-store`
- **密钥注入**：管理员密钥与 AI 密钥通过环境变量传入主服务器，不写入代码
- **判题三层防护**：
  1. 源码文本扫描（危险 API / 宏拼接 / 本地 include / 命令字符串）
  2. 编译后二进制扫描（链接器导入表 + 危险字符串 + 基线对比，对抗宏混淆绕过）
  3. 受限执行（临时目录隔离、5s 超时、输出 128KB 截断、进程树强杀、Windows Job Object 内存限制）
- **启动页防护**：仅监听回环地址，跨源请求一律拒绝，密钥不回传浏览器（仅显示脱敏预览）

> **注意**：Windows 单机判题仍无法达到 Docker 级沙箱。若面向不可信用户公开提供判题服务，建议将判题放到独立容器 / 虚拟机中运行。

## 部署到云服务器

1. **最小打包**：仅上传 `code/`、`data/`、`start.bat`、`start.ps1` 与 README（`runtime/` 体积大，按需选择是否传输）
2. **安装依赖**：Windows 服务器安装 Python 3.13 与 MinGW-w64（推荐 [winlibs](https://winlibs.com/) 便携版，解压后把 `bin` 加入 PATH）；或把完整 `runtime/` 一并上传，免安装
3. **配置**：运行 `start.bat`，在启动页填写管理员密钥与 AI 密钥
4. **安全组/防火墙**：仅开放必要端口（网页 80/443 或 8081），RDP(3389) 限制为管理员本机 IP，删除不用的 20/21/22 等端口
5. **域名 + HTTPS**：见上文「使用自有域名」；中国大陆服务器需先完成 ICP 备案

## 移动端适配

- 登录卡片适配手机宽度
- 侧边栏在移动端变为顶部横条，点击展开题目列表
- 输入框 16px 字号，防止 iOS 自动缩放
- 选中题目后侧边栏自动收起
