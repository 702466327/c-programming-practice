"""AI 编程练习助手 - HTTP 服务器"""

import http.server
import json
import os
import sys
from datetime import datetime
import urllib.parse
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
PROJECT_ROOT = BASE_DIR.parent


class TeeStream:
    def __init__(self, console_stream, log_path):
        self.console_stream = console_stream
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = open(self.log_path, "a", encoding="utf-8", buffering=1)

    def write(self, data):
        if not data:
            return 0
        try:
            self.console_stream.write(data)
        except Exception:
            pass
        try:
            self.log_file.write(data)
        except Exception:
            pass
        return len(data)

    def flush(self):
        try:
            self.console_stream.flush()
        except Exception:
            pass
        try:
            self.log_file.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return self.console_stream.isatty()
        except Exception:
            return False

    @property
    def encoding(self):
        return getattr(self.console_stream, "encoding", "utf-8")


STDOUT_LOG = os.environ.get("SERVER_STDOUT_LOG", str(PROJECT_ROOT / "server_stdout.log"))
STDERR_LOG = os.environ.get("SERVER_STDERR_LOG", str(PROJECT_ROOT / "server_stderr.log"))
if os.environ.get("TEE_SERVER_LOGS", "1") != "0":
    sys.stdout = TeeStream(sys.__stdout__, STDOUT_LOG)
    sys.stderr = TeeStream(sys.__stderr__, STDERR_LOG)


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

import question_bank
import judge
import ai_client
import auth
import ratelimit
from ratelimit import (
    LOGIN_LIMIT,
    REGISTER_LIMIT,
    RECOVER_LIMIT,
    ADMIN_LOGIN_LIMIT,
)

PORT = int(os.environ.get("PORT", "8081"))
HOST = os.environ.get("BIND_HOST", "0.0.0.0").strip()
MAX_BODY_SIZE = 100 * 1024
AUTO_OPEN_BROWSER = os.environ.get("OPEN_BROWSER", "").strip().lower() in {"1", "true", "yes", "y"}

# 统一安全响应头
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


def _parse_body(handler):
    content_length = int(handler.headers.get("Content-Length", 0))
    if content_length > MAX_BODY_SIZE:
        return None
    body = handler.rfile.read(content_length) if content_length > 0 else b"{}"
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _get_token(handler):
    auth_header = handler.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


def _client_ip(handler):
    """获取客户端真实 IP（ngrok/反向代理场景读取 X-Forwarded-For）"""
    forwarded = handler.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return handler.client_address[0]


def _get_username(handler):
    token = _get_token(handler)
    if not token:
        return None
    return auth.get_user_by_token(token)


def _require_auth(handler):
    username = _get_username(handler)
    if not username:
        handler._send_json({"error": True, "code": 401, "message": "请先登录"})
        return None
    return username


def _require_admin(handler):
    """验证管理员身份"""
    token = _get_token(handler)
    if not token or not auth.is_admin(token):
        handler._send_json({"error": True, "code": 403, "message": "需要管理员权限"})
        return False
    return True


class APIHandler(http.server.BaseHTTPRequestHandler):
    # 隐藏服务端技术栈版本信息
    server_version = "AIJudge/1.0"
    sys_version = ""

    def log_message(self, format, *args):
        # 健康检查/图标请求属于轮询噪音，不写入日志
        try:
            path = urllib.parse.urlparse(self.path).path
        except Exception:
            path = ""
        if path in ("/api/ai-status", "/favicon.ico"):
            return
        print(f"[{timestamp()}] [{self.command}] {args[0]}")

    def _apply_security_headers(self):
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/questions":
            self._send_json(question_bank.load_questions_public())
            return
        if path.startswith("/api/question/"):
            try:
                qid = int(path.split("/")[-1])
                q = question_bank.get_question_by_id_public(qid)
                if q: self._send_json(q)
                else: self._send_error(404, "题目未找到")
            except ValueError:
                self._send_error(400, "无效的题目 ID")
            return
        if path == "/api/categories":
            self._send_json(question_bank.get_categories())
            return
        if path == "/api/ai-status":
            self._send_json(ai_client.get_info())
            return
        if path == "/api/user":
            username = _require_auth(self)
            if username: self._send_json({"username": username})
            return
        if path == "/api/submissions":
            username = _require_auth(self)
            if username:
                subs = auth.get_all_submissions(username)
                self._send_json({"submissions": subs, "username": username})
            return
        if path == "/favicon.ico":
            self._send_error(404, "Not Found")
            return
        if path == "/" or path == "/index.html":
            self._serve_html()
            return
        self._send_error(404, "页面未找到")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        data = _parse_body(self)
        if data is None:
            self._send_error(400, "无效的 JSON 数据")
            return

        if path == "/api/register":
            ip = _client_ip(self)
            if not REGISTER_LIMIT.allow(ip):
                self._send_error(429, "注册过于频繁，请稍后再试")
                return
            username = data.get("username", "").strip()
            password = data.get("password", "")
            recovery_key = data.get("recovery_key", "").strip()
            success, msg = auth.register_user(username, password, recovery_key)
            self._send_json({"success": success, "message": msg})
            return
        if path == "/api/recover":
            ip = _client_ip(self)
            username = data.get("username", "").strip()
            recovery_key = data.get("recovery_key", "").strip()
            new_password = data.get("new_password", "")
            if not RECOVER_LIMIT.allow(f"{ip}|{username}"):
                self._send_error(429, "操作过于频繁，请稍后再试")
                return
            success, msg = auth.recover_password(username, recovery_key, new_password)
            self._send_json({"success": success, "message": msg})
            return
        if path == "/api/login":
            ip = _client_ip(self)
            username = data.get("username", "").strip()
            password = data.get("password", "")
            limit_key = f"{ip}|{username}"
            if not LOGIN_LIMIT.allow(f"ip:{ip}") or not LOGIN_LIMIT.allow(limit_key):
                self._send_error(429, "尝试过于频繁，请稍后再试")
                return
            token, msg = auth.login_user(username, password)
            if token:
                LOGIN_LIMIT.reset(limit_key)
            self._send_json({"success": token is not None, "message": msg, "token": token, "username": username if token else None})
            return
        if path == "/api/logout":
            token = _get_token(self)
            if token: auth.logout_user(token)
            self._send_json({"success": True})
            return
        # ---- 管理员 API ----
        if path == "/api/admin/login":
            ip = _client_ip(self)
            if not ADMIN_LOGIN_LIMIT.allow(ip):
                self._send_error(429, "尝试过于频繁，请稍后再试")
                return
            admin_key = data.get("admin_key", "")
            token, msg = auth.admin_login(admin_key)
            self._send_json({"success": token is not None, "message": msg, "token": token})
            return
        if path == "/api/admin/users":
            if not _require_admin(self): return
            self._send_json({"users": auth.list_all_users()})
            return
        if path == "/api/admin/reset-password":
            if not _require_admin(self): return
            username = data.get("username", "").strip()
            new_password = data.get("new_password", "")
            success, msg = auth.reset_password(username, new_password)
            self._send_json({"success": success, "message": msg})
            return
        if path == "/api/admin/delete-user":
            if not _require_admin(self): return
            username = data.get("username", "").strip()
            success, msg = auth.delete_user(username)
            self._send_json({"success": success, "message": msg})
            return
        if path == "/api/submit":
            username = _require_auth(self)
            if not username: return
            code = data.get("code", "")
            question_id = data.get("question_id")
            if not code.strip():
                self._send_error(400, "代码不能为空")
                return
            q = question_bank.get_question_by_id(question_id)
            if not q:
                self._send_error(400, "题目未找到")
                return
            auth.save_submission(username, question_id, code)
            judge_result = judge.run_test_cases(code, q.get("test_cases", []))
            exec_summary = judge.format_execution_summary(judge_result)
            self._send_json({"judge_result": judge_result, "execution_summary": exec_summary})
            return
        if path == "/api/feedback":
            username = _require_auth(self)
            if not username: return
            code = data.get("code", "")
            question_id = data.get("question_id")
            execution_result = data.get("execution_result", "")
            q = question_bank.get_question_by_id(question_id)
            if not q:
                self._send_error(400, "题目未找到")
                return
            feedback = ai_client.get_feedback(question_title=q["title"], question_description=q["description"], user_code=code, execution_result=execution_result)
            self._send_json({"feedback": feedback})
            return
        self._send_error(404, "接口未找到")

    def _serve_html(self):
        html_path = BASE_DIR / "index.html"
        if html_path.exists():
            self._send_file(html_path, "text/html; charset=utf-8")
        else:
            self._send_error(500, "index.html 未找到")

    def _send_file(self, filepath, content_type):
        try:
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(content))
            self._apply_security_headers()
            self.end_headers()
            self._safe_write(content)
        except IOError:
            self._send_error(500, "文件读取失败")

    def _safe_write(self, body):
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            # Client disconnected before the response body was fully written.
            pass

    def _send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self._apply_security_headers()
        self.end_headers()
        self._safe_write(body)

    def _send_error(self, code, message):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        body = json.dumps({"error": True, "code": code, "message": message}, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", len(body))
        self._apply_security_headers()
        self.end_headers()
        self._safe_write(body)


def main():
    server = http.server.ThreadingHTTPServer((HOST, PORT), APIHandler)
    server.daemon_threads = True
    url = f"http://localhost:{PORT}"
    print(f"[{timestamp()}] AI 编程练习助手已启动")
    print(f"[{timestamp()}]    {url}")
    ai_info = ai_client.get_info()
    if ai_info["available"]:
        print(f"[{timestamp()}]    AI: ok ({ai_info['platform_name']} · {ai_info['model']})")
    else:
        print(f"[{timestamp()}]    AI: off")
    if HOST and HOST != "0.0.0.0":
        print(f"[{timestamp()}]    监听地址: {HOST}（仅本机）")
    else:
        print(f"[{timestamp()}]    监听地址: 0.0.0.0（公网可访问）")
    if AUTO_OPEN_BROWSER:
        try:
            webbrowser.open(url)
        except Exception:
            print(f"[{timestamp()}] browser open skipped")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"[{timestamp()}] server stopped")
        server.shutdown()


if __name__ == "__main__":
    main()
