"""轻量内存限流模块（纯标准库）

用于登录、注册、找回密码、管理员登录等敏感接口的滑动窗口限流。
进程重启后计数清零，适合单机小应用；如需多实例/持久化限流，应换用
Redis 等集中式方案。
"""

import threading
import time


class RateLimiter:
    """滑动窗口限流器（线程安全）"""

    def __init__(self, max_attempts, window_seconds):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._events = {}
        self._lock = threading.Lock()

    def allow(self, key):
        """尝试消费一次额度，返回 True 表示允许通过。"""
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events.setdefault(key, [])
            # 只保留窗口内的事件
            events[:] = [t for t in events if t > cutoff]
            if len(events) >= self.max_attempts:
                return False
            events.append(now)
            return True

    def reset(self, key):
        """清除某个 key 的计数（例如登录成功后清零）"""
        with self._lock:
            self._events.pop(key, None)

    def clear_expired(self):
        """清理已过期的 key，防止内存无限增长"""
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            expired = [k for k, v in self._events.items()
                       if not any(t > cutoff for t in v)]
            for key in expired:
                self._events.pop(key, None)


# ===== 各接口限流阈值 =====

# 登录：同一 IP 10 次 / 5 分钟；同一 IP+账号 10 次 / 5 分钟
LOGIN_LIMIT = RateLimiter(10, 300)

# 注册：同一 IP 5 次 / 10 分钟
REGISTER_LIMIT = RateLimiter(5, 600)

# 找回密码：同一 IP+账号 5 次 / 5 分钟
RECOVER_LIMIT = RateLimiter(5, 300)

# 管理员登录：同一 IP 5 次 / 5 分钟
ADMIN_LOGIN_LIMIT = RateLimiter(5, 300)

# AI 点评：同一用户 60 次 / 1 小时 (另有每日配额见 auth.py)
FEEDBACK_LIMIT = RateLimiter(60, 3600)

# 判题提交：同一用户 6 次 / 1 分钟 (防止滥用判题资源)
SUBMIT_LIMIT = RateLimiter(6, 60)
