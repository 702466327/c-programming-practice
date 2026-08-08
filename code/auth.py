"""用户认证模块 - 注册、登录、会话管理、管理员、密码重置

使用 JSON 文件存储用户数据和会话，纯标准库实现。
密码使用 PBKDF2-SHA256 加盐哈希存储（旧的无盐 SHA256 哈希会自动迁移）。
管理员通过固定管理密钥验证。
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
SUBMISSIONS_FILE = os.path.join(DATA_DIR, "submissions.json")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
FEEDBACK_USAGE_FILE = os.path.join(DATA_DIR, "feedback_usage.json")
DATA_LOCK = threading.RLock()

# 登录/管理密钥失败锁定 (内存态, 重启清零)
LOGIN_FAIL_LIMIT = 5
LOCK_SECONDS = 900
_login_fails = {}      # username -> [时间戳]
_admin_fails = {}      # ip -> [时间戳]

# 管理员密钥必须通过环境变量显式提供
ADMIN_KEY = os.environ.get("ADMIN_KEY", "").strip()

# ===== 密码哈希（PBKDF2-SHA256 加盐） =====
PBKDF2_ITERATIONS = 200_000
PBKDF2_PREFIX = "pbkdf2_sha256$"

# 保留用户名：以 __ 开头的用户名一律禁止注册（防止伪造 __admin__ 管理员）
RESERVED_USERNAME_PREFIXES = ("__",)
RESERVED_USERNAMES = {"__admin__"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{2,10}$")


def _hash_password(password):
    """PBKDF2-SHA256 加盐哈希，格式: pbkdf2_sha256$迭代次数$盐$哈希"""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), PBKDF2_ITERATIONS
    )
    return f"{PBKDF2_PREFIX}{PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def _verify_password(password, stored):
    """校验密码，兼容旧版无盐 SHA256 存储"""
    if not stored:
        return False
    if stored.startswith(PBKDF2_PREFIX):
        try:
            _, iterations, salt, digest = stored.split("$", 3)
            dk = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt.encode("ascii"), int(iterations)
            )
            return hmac.compare_digest(dk.hex(), digest)
        except (ValueError, TypeError):
            return False
    # 旧版无盐 SHA256（登录成功后会原地升级为新格式）
    return hmac.compare_digest(
        hashlib.sha256(password.encode("utf-8")).hexdigest(), stored
    )


def _needs_rehash(stored):
    return bool(stored) and not stored.startswith(PBKDF2_PREFIX)


def _valid_username(username):
    """用户名合法性检查：格式 + 保留字"""
    if not USERNAME_RE.match(username):
        return False
    if username in RESERVED_USERNAMES or username.startswith(RESERVED_USERNAME_PREFIXES):
        return False
    return True


def _load_json(filepath, default=None):
    """加载 JSON 文件"""
    if default is None:
        default = {}
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default


def _save_json(filepath, data):
    """保存 JSON 文件"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(filepath), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ===== 用户管理 =====

def register_user(username, password, recovery_key=""):
    """注册新用户

    Args:
        username: 用户名
        password: 密码
        recovery_key: 找回密钥（可选，用于自助找回密码）

    Returns:
        (success, message)
    """
    if not username or not password:
        return False, "用户名和密码不能为空"
    if len(username) < 2 or len(username) > 10:
        return False, "用户名需 2-10 个字符"
    if len(password) < 8 or len(password) > 15:
        return False, "密码需 8-15 个字符"
    if len(recovery_key) < 4:
        return False, "找回密钥至少 4 个字符（用于自助找回密码）"
    if not _valid_username(username):
        return False, "用户名不合法或为保留用户名"

    with DATA_LOCK:
        users = _load_json(USERS_FILE, {})

        if username in users:
            return False, "用户名已存在"

        users[username] = {
            "password": _hash_password(password),
            "recovery_key": _hash_password(recovery_key),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        _save_json(USERS_FILE, users)
    return True, "注册成功"


def login_user(username, password):
    """用户登录

    Returns:
        (token, message) - token 为 None 表示登录失败
    """
    with DATA_LOCK:
        users = _load_json(USERS_FILE, {})

        if username not in users:
            # 统一报错，避免用户名枚举
            return None, "用户名或密码错误"

        stored = users[username].get("password", "")
        if not _verify_password(password, stored):
            return None, "用户名或密码错误"

        # 旧版无盐哈希登录成功后原地升级为 PBKDF2
        if _needs_rehash(stored):
            users[username]["password"] = _hash_password(password)
            _save_json(USERS_FILE, users)

        token = secrets.token_hex(32)

        sessions = _load_json(SESSIONS_FILE, {})
        sessions[token] = {
            "username": username,
            "created_at": time.time()
        }
        _save_json(SESSIONS_FILE, sessions)

    return token, "登录成功"


def logout_user(token):
    """登出"""
    with DATA_LOCK:
        sessions = _load_json(SESSIONS_FILE, {})
        sessions.pop(token, None)
        _save_json(SESSIONS_FILE, sessions)


def get_user_by_token(token):
    """根据 token 获取用户名，失败返回 None"""
    if not token:
        return None
    with DATA_LOCK:
        sessions = _load_json(SESSIONS_FILE, {})
        session = sessions.get(token)
        if not session:
            return None
        if time.time() - session["created_at"] > 86400:
            sessions.pop(token, None)
            _save_json(SESSIONS_FILE, sessions)
            return None
        return session["username"]


# ===== 代码提交管理 =====

def save_submission(username, question_id, code):
    """保存用户的代码提交

    Args:
        username: 用户名
        question_id: 题目ID
        code: 提交的代码
    """
    with DATA_LOCK:
        submissions = _load_json(SUBMISSIONS_FILE, {})
        key = str(question_id)

        if username not in submissions:
            submissions[username] = {}

        entry = submissions[username].get(key, {})
        entry["code"] = code
        entry["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        submissions[username][key] = entry
        _save_json(SUBMISSIONS_FILE, submissions)


def get_submission(username, question_id):
    """获取用户某道题的最近提交"""
    with DATA_LOCK:
        submissions = _load_json(SUBMISSIONS_FILE, {})
        user_subs = submissions.get(username, {})
        sub = user_subs.get(str(question_id))
        return sub["code"] if sub else None


def get_submission_entry(username, question_id):
    """获取用户某道题的提交记录条目（含 last_summary 等元数据），无则 None"""
    with DATA_LOCK:
        submissions = _load_json(SUBMISSIONS_FILE, {})
        user_subs = submissions.get(username, {})
        return user_subs.get(str(question_id))


def get_all_submissions(username):
    """获取用户所有提交"""
    with DATA_LOCK:
        submissions = _load_json(SUBMISSIONS_FILE, {})
        return submissions.get(username, {})


# ===== 判题统计与排行榜 =====

def record_judge_result(username, question_id, passed, total, summary=None):
    """记录判题结果: 尝试次数 / 是否已解出（全部用例通过即视为解出）/ 最近一次执行摘要"""
    with DATA_LOCK:
        submissions = _load_json(SUBMISSIONS_FILE, {})
        user_subs = submissions.setdefault(username, {})
        entry = user_subs.setdefault(str(question_id), {})
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        if passed > 0 and total > 0 and passed == total:
            if not entry.get("solved"):
                entry["solved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            entry["solved"] = True
        if summary is not None:
            entry["last_summary"] = summary
        _save_json(SUBMISSIONS_FILE, submissions)


def consume_feedback_quota(username, daily_limit):
    """消耗一次 AI 点评每日配额; 超限返回 False"""
    with DATA_LOCK:
        usage = _load_json(FEEDBACK_USAGE_FILE, {})
        today = time.strftime("%Y-%m-%d")
        rec = usage.get(username, {})
        if rec.get("date") != today:
            rec = {"date": today, "count": 0}
        if rec["count"] >= daily_limit:
            return False
        rec["count"] += 1
        usage[username] = rec
        _save_json(FEEDBACK_USAGE_FILE, usage)
        return True


# ===== 登录失败锁定 =====

def _prune_fails(store, key):
    now = time.time()
    store[key] = [t for t in store.get(key, []) if t > now - LOCK_SECONDS]


def is_login_locked(username):
    with DATA_LOCK:
        _prune_fails(_login_fails, username)
        return len(_login_fails.get(username, [])) >= LOGIN_FAIL_LIMIT


def record_login_failure(username):
    with DATA_LOCK:
        _prune_fails(_login_fails, username)
        _login_fails.setdefault(username, []).append(time.time())


def record_login_success(username):
    with DATA_LOCK:
        _login_fails.pop(username, None)


def is_admin_locked(ip):
    with DATA_LOCK:
        _prune_fails(_admin_fails, ip)
        return len(_admin_fails.get(ip, [])) >= LOGIN_FAIL_LIMIT


def record_admin_failure(ip):
    with DATA_LOCK:
        _prune_fails(_admin_fails, ip)
        _admin_fails.setdefault(ip, []).append(time.time())


def record_admin_success(ip):
    with DATA_LOCK:
        _admin_fails.pop(ip, None)


def get_leaderboard(weights, limit=50):
    """排行榜: 积分 = 已解出题目的难度权重之和（weights: {题目ID字符串: 权重}）

    排序: 积分降序 -> 已解数降序 -> 尝试次数升序 -> 用户名升序
    """
    submissions = _load_json(SUBMISSIONS_FILE, {})
    rows = []
    for username, subs in submissions.items():
        score = 0
        solved = 0
        attempts = 0
        for qid, s in subs.items():
            if s.get("solved"):
                solved += 1
                score += int(weights.get(str(qid), 1))
            attempts += int(s.get("attempts", 0))
        if solved > 0 or attempts > 0:
            rows.append({
                "username": username,
                "score": score,
                "solved": solved,
                "attempts": attempts,
            })
    rows.sort(key=lambda r: (-r["score"], -r["solved"], r["attempts"], r["username"]))
    for i, r in enumerate(rows[:limit], 1):
        r["rank"] = i
    return rows[:limit]


# ===== 管理员功能 =====

def admin_login(admin_key):
    """管理员登录

    Returns:
        (token, message)
    """
    if not ADMIN_KEY:
        return None, "管理员入口未配置，请先设置 ADMIN_KEY"
    if not hmac.compare_digest(str(admin_key), ADMIN_KEY):
        return None, "管理密钥错误"

    with DATA_LOCK:
        token = secrets.token_hex(32)
        sessions = _load_json(SESSIONS_FILE, {})
        sessions[token] = {
            "username": "__admin__",
            "created_at": time.time()
        }
        _save_json(SESSIONS_FILE, sessions)
    return token, "管理员登录成功"


def is_admin(token):
    """检查 token 是否为管理员"""
    username = get_user_by_token(token)
    return username == "__admin__"


def list_all_users():
    """列出所有用户（不含密码哈希）"""
    with DATA_LOCK:
        users = _load_json(USERS_FILE, {})
        submissions = _load_json(SUBMISSIONS_FILE, {})
        result = {}
        for name, info in users.items():
            if name == "__admin__":
                continue
            subs = submissions.get(name, {})
            result[name] = {
                "created_at": info.get("created_at", ""),
                "submission_count": len(subs)
            }
        return result


def reset_password(username, new_password):
    """管理员重置用户密码

    Returns:
        (success, message)
    """
    if not new_password or len(new_password) < 8 or len(new_password) > 15:
        return False, "新密码需 8-15 个字符"

    with DATA_LOCK:
        users = _load_json(USERS_FILE, {})
        if username not in users:
            return False, "用户不存在"

        users[username]["password"] = _hash_password(new_password)
        _save_json(USERS_FILE, users)

        sessions = _load_json(SESSIONS_FILE, {})
        to_remove = [t for t, s in sessions.items() if s.get("username") == username]
        for t in to_remove:
            sessions.pop(t, None)
        _save_json(SESSIONS_FILE, sessions)

    return True, "密码重置成功"


def delete_user(username):
    """管理员删除用户及其所有数据

    Returns:
        (success, message)
    """
    with DATA_LOCK:
        users = _load_json(USERS_FILE, {})
        if username not in users:
            return False, "用户不存在"
        if username == "__admin__":
            return False, "不能删除管理员账号"

        users.pop(username, None)
        _save_json(USERS_FILE, users)

        submissions = _load_json(SUBMISSIONS_FILE, {})
        submissions.pop(username, None)
        _save_json(SUBMISSIONS_FILE, submissions)

        sessions = _load_json(SESSIONS_FILE, {})
        to_remove = [t for t, s in sessions.items() if s.get("username") == username]
        for t in to_remove:
            sessions.pop(t, None)
        _save_json(SESSIONS_FILE, sessions)

    return True, f"用户 {username} 已删除"


# ===== 自助找回密码 =====

def recover_password(username, recovery_key, new_password):
    """通过找回密钥重置密码

    Returns:
        (success, message)
    """
    if not username or not recovery_key or not new_password:
        return False, "请填写所有字段"
    if len(new_password) < 8 or len(new_password) > 15:
        return False, "新密码需 8-15 个字符"

    with DATA_LOCK:
        users = _load_json(USERS_FILE, {})
        # 统一错误文案, 不区分 用户不存在 / 未设置密钥 / 密钥错误, 防用户枚举
        stored_key = users.get(username, {}).get("recovery_key", "")
        if not stored_key or not _verify_password(recovery_key, stored_key):
            return False, "用户名或密钥错误"

        users[username]["password"] = _hash_password(new_password)
        # 旧版无盐恢复密钥校验成功后原地升级
        if _needs_rehash(stored_key):
            users[username]["recovery_key"] = _hash_password(recovery_key)
        _save_json(USERS_FILE, users)

        sessions = _load_json(SESSIONS_FILE, {})
        to_remove = [t for t, s in sessions.items() if s.get("username") == username]
        for t in to_remove:
            sessions.pop(t, None)
        _save_json(SESSIONS_FILE, sessions)

    return True, "密码重置成功，请使用新密码登录"
