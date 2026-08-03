"""AI 编程练习助手 - 本地启动器（启动页后端）

在 127.0.0.1:8299 提供本地网页，把原来 start.ps1 的命令行配置搬到 UI：
  - 多平台 AI 密钥配置（OpenAI / DeepSeek / 硅基流动 / 智谱 / 通义 / Kimi / 混元 / 自定义）
  - 管理员密钥、监听端口、公网映射(ngrok)、自动打开浏览器等服务器设置
  - 一键启动/停止主服务器，实时查看状态、公网地址与运行日志

仅监听本机回环地址，请勿把该端口暴露到公网（页面内含有明文密钥）。
"""

import http.server
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
CONFIG_FILE = PROJECT_ROOT / "launcher_config.json"
DEPLOY_CONFIG_FILE = PROJECT_ROOT / "deploy_config.txt"
LAUNCHER_HTML = BASE_DIR / "launcher.html"
LAUNCHER_PORT = int(os.environ.get("LAUNCHER_PORT", "8299"))
NGROK_API_PORT = 4040

DEFAULT_CONFIG = {
    "admin_key": "",
    "ai_enabled": False,
    "ai_platform": "siliconflow",
    "ai_api_key": "",
    "ai_model": "",
    "ai_base_url": "",
    "port": 8081,
    "bind_host": "0.0.0.0",
    "open_browser": True,
    "ngrok_enabled": False,
    "ngrok_domain": "",
    "ngrok_authtoken": "",
}

import ai_client  # noqa: E402  (同目录模块，用于平台注册表与连接测试)

state_lock = threading.RLock()
server_proc = None
ngrok_proc = None
started_at = 0.0
last_error = ""
public_url = ""

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def timestamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ==================== 配置读写 ====================

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: data[k] for k in DEFAULT_CONFIG if k in data})
        except (json.JSONDecodeError, OSError):
            pass
    else:
        _migrate_from_deploy_config(cfg)
    return cfg


def _migrate_from_deploy_config(cfg):
    """首次运行：把旧的 deploy_config.txt 配置导入启动页"""
    if not DEPLOY_CONFIG_FILE.exists():
        return
    try:
        text = DEPLOY_CONFIG_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    mapping = {
        "ADMIN_KEY": "admin_key",
        "AI_API_KEY": "ai_api_key",
        "NGROK_AUTHTOKEN": "ngrok_authtoken",
        "NGROK_DOMAIN": "ngrok_domain",
    }
    for line in text.splitlines():
        m = re.match(r"^(\w+)\s*=\s*(.*)$", line.strip())
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if key in mapping and value:
            cfg[mapping[key]] = value
    cfg["ai_enabled"] = bool(cfg.get("ai_api_key"))
    cfg["ngrok_enabled"] = bool(cfg.get("ngrok_authtoken"))


def save_config(cfg):
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG})
    merged["port"] = int(merged["port"] or 8081)
    CONFIG_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_deploy_config_mirror(merged)
    return merged


def _write_deploy_config_mirror(cfg):
    """同步一份旧格式配置，兼容仍在使用的 start.ps1"""
    lines = [
        "# AI 编程练习助手 - 部署配置（由启动页自动生成，请在启动页中修改）",
        "DEFAULT_ADMIN_KEY=",
        "DEFAULT_AI_API_KEY=",
        "DEFAULT_NGROK_AUTHTOKEN=",
        "DEFAULT_NGROK_DOMAIN=",
        f"ADMIN_KEY={cfg.get('admin_key', '')}",
        f"AI_API_KEY={cfg.get('ai_api_key', '')}",
        f"NGROK_AUTHTOKEN={cfg.get('ngrok_authtoken', '')}",
        f"NGROK_DOMAIN={cfg.get('ngrok_domain', '')}",
    ]
    try:
        DEPLOY_CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def mask_secret(value):
    if not value:
        return ""
    if len(value) <= 6:
        return "*" * len(value)
    return value[:3] + "****" + value[-3:]


def _masked_config(cfg):
    masked = dict(cfg)
    masked["admin_key"] = mask_secret(cfg.get("admin_key", ""))
    masked["ai_api_key"] = mask_secret(cfg.get("ai_api_key", ""))
    masked["ngrok_authtoken"] = mask_secret(cfg.get("ngrok_authtoken", ""))
    return masked


def validate_config(cfg):
    errors = []
    try:
        port = int(cfg.get("port") or 0)
        if not (1 <= port <= 65535):
            errors.append("端口必须是 1-65535 之间的数字")
    except (TypeError, ValueError):
        errors.append("端口必须是数字")

    if cfg.get("ai_enabled"):
        _, _, base, model, key = ai_client.resolve_config(
            api_key=cfg.get("ai_api_key", ""),
            platform=cfg.get("ai_platform", ""),
            base_url=cfg.get("ai_base_url", ""),
            model=cfg.get("ai_model", ""),
        )
        if not key:
            errors.append("已启用 AI，但未填写 API 密钥")
        if not base:
            errors.append("已启用 AI，但缺少接口地址（请选择平台或填写自定义地址）")
        if not model:
            errors.append("已启用 AI，但缺少模型名（请选择平台或填写自定义模型）")

    bind = str(cfg.get("bind_host") or "0.0.0.0").strip()
    if bind not in ("0.0.0.0", "127.0.0.1", "localhost"):
        errors.append("监听地址仅支持 0.0.0.0（公网）或 127.0.0.1（仅本机）")

    if cfg.get("ngrok_enabled") and cfg.get("ngrok_domain"):
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9.\-]*$", str(cfg["ngrok_domain"]).strip()):
            errors.append("ngrok 固定域名格式不正确")
    return errors


# ==================== 运行环境探测 ====================

def _tool_candidates(rel_paths, system_names):
    for rel in rel_paths:
        p = PROJECT_ROOT / rel
        if p.exists():
            return {"path": str(p), "source": "bundled"}
    for name in system_names:
        found = shutil.which(name)
        if found:
            return {"path": found, "source": "system"}
    return None


def find_python():
    tool = _tool_candidates(
        ["runtime/python/python.exe", "runtime/python/python3.exe"],
        ["python"],
    )
    if tool:
        return tool
    # 兼容 py 启动器
    if shutil.which("py"):
        return {"path": "py", "source": "system", "args": ["-3"]}
    return None


def find_compiler():
    return _tool_candidates(
        [
            "runtime/mingw/bin/g++.exe",
            "runtime/mingw/bin/clang++.exe",
            "runtime/mingw/bin/c++.exe",
        ],
        ["g++", "clang++", "c++"],
    )


def find_ngrok():
    return _tool_candidates(["runtime/ngrok/ngrok.exe", "ngrok.exe"], ["ngrok"])


def _tool_label(tool):
    if not tool:
        return "未找到"
    return f"{Path(tool['path']).name}（{tool['source']}）"


# ==================== 端口 / 进程管理 ====================

def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pid_by_port(port):
    if sys.platform != "win32":
        return None
    try:
        r = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        pattern = re.compile(r"\bTCP\s+\S+:%d\s+\S+\s+LISTENING\s+(\d+)" % port)
        for line in r.stdout.splitlines():
            m = pattern.search(line)
            if m:
                pid = int(m.group(1))
                return pid if pid != 0 else None
    except Exception:
        pass
    return None


def kill_pid(pid):
    if not pid:
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        else:
            os.kill(pid, 9)
        return True
    except Exception:
        return False


def process_alive(pid):
    if not pid:
        return False
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            return f"\"{pid}\"" in r.stdout or f" {pid} " in r.stdout
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _read_pid_file(name):
    p = PROJECT_ROOT / name
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _write_pid_file(name, pid):
    try:
        (PROJECT_ROOT / name).write_text(str(pid), encoding="utf-8")
        return True
    except OSError:
        return False


def _remove_pid_file(name):
    try:
        (PROJECT_ROOT / name).unlink(missing_ok=True)
    except OSError:
        pass


def _adopted_pid(name):
    pid = _read_pid_file(name)
    if pid and process_alive(pid):
        return pid
    return None


def http_get_json(url, timeout=2):
    try:
        req = urllib.request.Request(url, headers={"Connection": "close"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, None
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", errors="replace"))
        except Exception:
            return e.code, None
    except Exception:
        return None, None


def _query_ngrok_url():
    status, data = http_get_json(f"http://127.0.0.1:{NGROK_API_PORT}/api/tunnels")
    if status != 200 or not isinstance(data, dict):
        return ""
    for tunnel in data.get("tunnels") or []:
        if tunnel.get("proto") == "https" and tunnel.get("public_url"):
            return tunnel["public_url"]
    return ""


# ==================== 服务启停 ====================

def _build_env(cfg, port, compiler):
    env = os.environ.copy()
    env.update({
        "ADMIN_KEY": cfg.get("admin_key", ""),
        "AI_PLATFORM": cfg.get("ai_platform", "") if cfg.get("ai_enabled") else "",
        "AI_API_KEY": cfg.get("ai_api_key", "") if cfg.get("ai_enabled") else "",
        "AI_API_BASE": cfg.get("ai_base_url", ""),
        "AI_MODEL": cfg.get("ai_model", ""),
        "PORT": str(port),
        "BIND_HOST": cfg.get("bind_host", "0.0.0.0"),
        "OPEN_BROWSER": "0",
        "TEE_SERVER_LOGS": "1",
        "SERVER_STDOUT_LOG": str(PROJECT_ROOT / "server_stdout.log"),
        "SERVER_STDERR_LOG": str(PROJECT_ROOT / "server_stderr.log"),
        "PROJECT_RUNTIME_ROOT": str(PROJECT_ROOT / "runtime"),
        "BUNDLED_COMPILER": str(compiler["path"]) if compiler and compiler["source"] == "bundled" else "",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    if compiler and compiler["source"] == "bundled":
        compiler_dir = str(Path(compiler["path"]).parent)
        env["PATH"] = compiler_dir + os.pathsep + env.get("PATH", "")
    return env


def _wait_server_health(proc, port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False, f"服务器进程已退出（退出码 {proc.returncode}），请查看日志"
        status, _ = http_get_json(f"http://127.0.0.1:{port}/api/ai-status")
        if status == 200:
            return True, ""
        time.sleep(0.5)
    return False, f"服务器在 {timeout} 秒内未就绪，请查看日志"


def stop_services_locked():
    global server_proc, ngrok_proc, public_url, started_at, last_error
    stopped = []
    targets = [
        ("server", server_proc, "server.pid"),
        ("ngrok", ngrok_proc, "ngrok.pid"),
    ]
    for label, proc, pid_file in targets:
        pid = None
        if proc and proc.poll() is None:
            pid = proc.pid
        if not pid:
            pid = _adopted_pid(pid_file)
        if pid:
            if kill_pid(pid):
                stopped.append(label)
        _remove_pid_file(pid_file)
    server_proc = None
    ngrok_proc = None
    public_url = ""
    started_at = 0.0
    last_error = ""
    return stopped


def start_services(cfg):
    global server_proc, ngrok_proc, started_at, last_error, public_url
    with state_lock:
        stop_services_locked()

        errors = validate_config(cfg)
        if errors:
            return {"ok": False, "errors": errors}

        generated_key = ""
        if not cfg.get("admin_key"):
            cfg["admin_key"] = secrets.token_hex(16)
            generated_key = cfg["admin_key"]
        cfg = save_config(cfg)
        port = int(cfg["port"])

        python = find_python()
        if not python:
            return {
                "ok": False,
                "errors": ["未检测到 Python。请将便携版放到 runtime\\python\\，或安装 Python 3 并加入 PATH。"],
            }

        # 刚结束旧进程后端口可能尚未释放，等待片刻再判定占用
        if port_in_use(port):
            for _ in range(10):
                time.sleep(0.3)
                if not port_in_use(port):
                    break
        if port_in_use(port):
            pid = pid_by_port(port)
            extra = f"（PID {pid}）" if pid else ""
            return {
                "ok": False,
                "errors": [f"端口 {port} 已被占用{extra}。可先在启动页点击「结束占用端口」，或手动结束该进程后重试。"],
            }
        if cfg.get("ngrok_enabled") and port_in_use(NGROK_API_PORT):
            return {
                "ok": False,
                "errors": [f"ngrok 管理端口 {NGROK_API_PORT} 已被占用，请先在启动页结束占用进程后重试。"],
            }

        compiler = find_compiler()
        warnings = []
        if not compiler:
            warnings.append("未检测到 g++ 编译器，判题功能不可用（仅 AI 点评可用）")

        env = _build_env(cfg, port, compiler)
        cmd = [python["path"]] + list(python.get("args", [])) + ["-u", "server.py"]
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(BASE_DIR),
                env=env,
                creationflags=creationflags,
                close_fds=True,
            )
        except OSError as e:
            return {"ok": False, "errors": [f"无法启动 Python 服务器：{e}"]}

        server_proc = proc
        started_at = time.time()
        local_url = f"http://127.0.0.1:{port}"
        if not _write_pid_file("server.pid", proc.pid):
            warnings.append("无法写入 PID 文件，关闭窗口后重新启动时将无法接管该服务进程")

    # ---- 以下为耗时操作，不占用 state_lock，避免界面卡顿 ----
    ok, message = _wait_server_health(proc, port)
    if not ok:
        with state_lock:
            last_error = message
            if proc.poll() is not None:
                server_proc = None
                _remove_pid_file("server.pid")
        return {"ok": False, "errors": [message], "log_tail": _tail_logs(60)}

    ngrok_url = ""
    ngrok_pid = None
    if cfg.get("ngrok_enabled"):
        with state_lock:
            ngrok = find_ngrok()
            if not ngrok:
                warnings.append("未找到 ngrok.exe，公网隧道未启动（服务器已正常运行）")
            elif port_in_use(NGROK_API_PORT):
                warnings.append(f"ngrok 管理端口 {NGROK_API_PORT} 已被占用，公网隧道未启动")
            else:
                if cfg.get("ngrok_authtoken"):
                    subprocess.run(
                        [ngrok["path"], "config", "add-authtoken", cfg["ngrok_authtoken"]],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                args = ["http", str(port), "--log=stdout"]
                if cfg.get("ngrok_domain"):
                    args.append(f"--domain={cfg['ngrok_domain']}")
                try:
                    ngrok_proc = subprocess.Popen(
                        [ngrok["path"]] + args,
                        env=env,
                        creationflags=creationflags,
                        close_fds=True,
                    )
                    ngrok_pid = ngrok_proc.pid
                except OSError as e:
                    ngrok_proc = None
                    warnings.append(f"ngrok 启动失败：{e}")

        # 锁外等待公网地址
        if ngrok_pid:
            deadline = time.time() + 25
            while time.time() < deadline:
                ngrok_url = _query_ngrok_url()
                if ngrok_url:
                    break
                if ngrok_proc.poll() is not None:
                    break
                time.sleep(1)
            if ngrok_url:
                _write_pid_file("ngrok.pid", ngrok_pid)
                with state_lock:
                    public_url = ngrok_url
            else:
                if ngrok_proc.poll() is None:
                    kill_pid(ngrok_pid)
                with state_lock:
                    ngrok_proc = None
                warnings.append("未获取到 ngrok 公网地址（请检查 authtoken 与固定域名配置）")

    if cfg.get("open_browser"):
        try:
            webbrowser.open(ngrok_url or local_url)
        except Exception:
            pass

    result = {
        "ok": True,
        "local_url": local_url,
        "public_url": ngrok_url,
        "warnings": warnings,
        "generated_admin_key": generated_key,
    }
    if generated_key:
        result["message"] = "未设置管理员密钥，已自动生成，请立即保存："
    return result


def stop_services():
    with state_lock:
        stopped = stop_services_locked()
    return {"ok": True, "stopped": stopped}


def build_state():
    with state_lock:
        cfg = load_config()
        port = int(cfg.get("port") or 8081)
        server_pid = server_proc.pid if (server_proc and server_proc.poll() is None) else _adopted_pid("server.pid")
        ngrok_pid = ngrok_proc.pid if (ngrok_proc and ngrok_proc.poll() is None) else _adopted_pid("ngrok.pid")
        err = last_error
        started = started_at if server_pid else 0.0

    health = None
    ai_info = None
    if server_pid:
        status, data = http_get_json(f"http://127.0.0.1:{port}/api/ai-status")
        health = status == 200
        ai_info = data if isinstance(data, dict) else None

    public = _query_ngrok_url() if ngrok_pid else ""
    return {
        "ok": True,
        "server_running": bool(server_pid),
        "server_pid": server_pid,
        "health": health,
        "ai_info": ai_info,
        "ngrok_running": bool(ngrok_pid),
        "public_url": public,
        "local_url": f"http://127.0.0.1:{port}",
        "started_at": started,
        "last_error": err,
        "config": _masked_config(cfg),
        "tools": {
            "python": _tool_label(find_python()),
            "compiler": _tool_label(find_compiler()),
            "ngrok": _tool_label(find_ngrok()),
        },
    }


def _tail_file(path, lines):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return [l.rstrip("\n") for l in all_lines[-lines:]]
    except OSError:
        return []


def _tail_logs(lines=80):
    return {
        "stdout": _tail_file(PROJECT_ROOT / "server_stdout.log", lines),
        "stderr": _tail_file(PROJECT_ROOT / "server_stderr.log", lines),
    }


# ==================== HTTP 处理 ====================

class LauncherHandler(http.server.BaseHTTPRequestHandler):
    server_version = "Launcher/1.0"
    sys_version = ""

    def log_message(self, fmt, *args):
        print(f"[{timestamp()}] [launcher] {args[0]}")

    def _headers(self):
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)

    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._headers()
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _send_html(self):
        try:
            content = LAUNCHER_HTML.read_bytes()
        except OSError:
            self._send_json({"error": "launcher.html 未找到"}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self._headers()
        self.end_headers()
        try:
            self.wfile.write(content)
        except OSError:
            pass

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            return None
        if length <= 0 or length > 1024 * 1024:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        allowed = {f"http://127.0.0.1:{LAUNCHER_PORT}", f"http://localhost:{LAUNCHER_PORT}"}
        return origin in allowed

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html", "/launcher.html"):
            self._send_html()
            return
        if path == "/api/state":
            self._send_json(build_state())
            return
        if path == "/api/platforms":
            platforms = [
                {
                    "id": pid,
                    "name": info["name"],
                    "default_model": info["default_model"],
                    "base_url": info["base_url"],
                    "note": info["note"],
                }
                for pid, info in ai_client.AI_PLATFORMS.items()
            ]
            self._send_json({"ok": True, "platforms": platforms})
            return
        if path == "/api/logs":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                lines = int(query.get("lines", ["80"])[0])
                lines = max(10, min(lines, 500))
            except ValueError:
                lines = 80
            self._send_json({"ok": True, **(_tail_logs(lines))})
            return
        self._send_json({"error": "Not Found"}, 404)

    def do_POST(self):
        if not self._same_origin():
            self._send_json({"error": "跨域请求被拒绝"}, 403)
            return
        path = urllib.parse.urlparse(self.path).path
        data = self._read_body() or {}

        if path == "/api/config":
            cfg = load_config()
            for key in DEFAULT_CONFIG:
                if key in data:
                    cfg[key] = data[key]
            errors = validate_config(cfg)
            if errors:
                self._send_json({"ok": False, "errors": errors})
                return
            cfg = save_config(cfg)
            self._send_json({"ok": True, "config": _masked_config(cfg)})
            return

        if path == "/api/start":
            cfg = load_config()
            for key in DEFAULT_CONFIG:
                if key in data:
                    cfg[key] = data[key]
            result = start_services(cfg)
            self._send_json(result)
            return

        if path == "/api/stop":
            self._send_json(stop_services())
            return

        if path == "/api/kill-port":
            try:
                port = int(data.get("port") or 0)
            except (TypeError, ValueError):
                port = 0
            if not (1 <= port <= 65535):
                self._send_json({"ok": False, "error": "端口无效"})
                return
            pid = pid_by_port(port)
            if not pid:
                self._send_json({"ok": False, "error": f"端口 {port} 当前没有被监听"})
                return
            if kill_pid(pid):
                time.sleep(0.5)
                self._send_json({"ok": True, "killed": pid, "port": port})
            else:
                self._send_json({"ok": False, "error": f"结束 PID {pid} 失败，请手动处理"})
            return

        if path == "/api/test-ai":
            ok, message = ai_client.test_connection(
                api_key=data.get("api_key", ""),
                platform=data.get("platform", ""),
                base_url=data.get("base_url", ""),
                model=data.get("model", ""),
            )
            self._send_json({"ok": ok, "message": message})
            return

        if path == "/api/list-models":
            ok, message, models = ai_client.list_models(
                api_key=data.get("api_key", ""),
                platform=data.get("platform", ""),
                base_url=data.get("base_url", ""),
            )
            self._send_json({"ok": ok, "message": message, "models": models})
            return

        self._send_json({"error": "Not Found"}, 404)


def main():
    global last_error
    print("=" * 52)
    print("  AI 编程练习助手 - 启动管理")
    print("=" * 52)
    cfg = load_config()
    print(f"[{timestamp()}] 配置: {CONFIG_FILE}")
    print(f"[{timestamp()}] 启动页仅监听 127.0.0.1:{LAUNCHER_PORT}，请勿暴露到公网")

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", LAUNCHER_PORT), LauncherHandler)
    except OSError as e:
        print(f"[X] 启动页端口 {LAUNCHER_PORT} 不可用：{e}")
        print("    可用环境变量 LAUNCHER_PORT=xxxx 修改，或先结束占用进程。")
        return 1
    server.daemon_threads = True

    url = f"http://127.0.0.1:{LAUNCHER_PORT}"
    print(f"[{timestamp()}] 启动页: {url}")
    if "--no-browser" not in sys.argv and os.environ.get("NO_OPEN_BROWSER") != "1":
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[{timestamp()}] 启动器已退出（已启动的服务不受影响，可重新运行启动器接管）")
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
