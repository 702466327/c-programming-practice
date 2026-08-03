"""AI 编程练习助手 - 启动管理（桌面窗口版）

tkinter 实现，复用 launcher.py 的后端逻辑（配置读写、服务启停、状态查询）。
若当前 Python 缺少 tkinter（如便携版 embeddable），会提示并退出，start.bat 将自动回退到网页版启动页。

用法：
    python code\\launcher_gui.py [--smoke]
    --smoke 仅做界面构建自检（窗口隐藏后立即退出），不显示窗口。
"""

import os
import queue
import sys
import threading
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog
except ImportError:
    print("[X] 当前 Python 缺少 tkinter，无法打开窗口版启动页。")
    print("    将自动改用网页版启动页：python code\\launcher.py")
    sys.exit(2)

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import launcher  # noqa: E402
import ai_client  # noqa: E402

ACCENT = "#1a73e8"
BG = "#f5f7fa"
OK_GREEN = "#137333"
ERR_RED = "#c5221f"
WARN = "#7f6000"


class LauncherApp:
    def __init__(self, root):
        self.root = root
        self.platforms = [(pid, info["name"]) for pid, info in ai_client.AI_PLATFORMS.items()]
        self.platform_ids = [pid for pid, _ in self.platforms]
        self.saved_config = {}
        self.clear_flags = {}          # 字段 -> True 表示用户要求清空已保存值
        self.busy = False
        self.last_running = False
        self.task_queue = queue.Queue()
        self._last_base_default = None
        self._last_model_default = None
        self._build_ui()
        self._apply_config(launcher.load_config())
        self._refresh_state()
        self._schedule_refresh()
        self._schedule_queue_poll()

    def _post(self, payload):
        """工作线程把结果放进队列，主线程轮询处理（tkinter 线程安全）"""
        self.task_queue.put(payload)

    def _schedule_queue_poll(self):
        try:
            while True:
                payload = self.task_queue.get_nowait()
                try:
                    self._handle_task(payload)
                except Exception as e:
                    self._show_msg(f"任务处理异常：{e}", ERR_RED)
        except queue.Empty:
            pass
        self.root.after(150, self._schedule_queue_poll)

    def _handle_task(self, payload):
        kind = payload[0]
        if kind == "start":
            self._on_start_done(payload[1])
        elif kind == "stop":
            self._on_stop_done(payload[1])
        elif kind == "testai":
            self._show_msg(payload[2], OK_GREEN if payload[1] else ERR_RED)
            self.ai_test_mark.config(text="✓" if payload[1] else "✗",
                                     fg=OK_GREEN if payload[1] else ERR_RED)
        elif kind == "models":
            self._on_models_done(payload[1], payload[2], payload[3])

    # ==================== 界面构建 ====================

    def _build_ui(self):
        self.root.title("AI 编程练习助手 - 启动管理")
        self.root.geometry("1120x780")
        self.root.minsize(960, 680)
        self.root.configure(bg=BG)

        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TLabelframe", background=BG)
        style.configure("TLabelframe.Label", background=BG, foreground="#333", font=("Microsoft YaHei", 10, "bold"))
        style.configure("TLabel", background=BG)
        style.configure("TCheckbutton", background=BG)

        # ---- 顶栏 ----
        top = tk.Frame(self.root, bg="#ffffff", height=64)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(top, text="AI 编程练习助手 · 启动管理", bg="#ffffff", fg=ACCENT,
                 font=("Microsoft YaHei", 15, "bold")).pack(side="left", padx=18, pady=14)
        self.status_light = tk.Canvas(top, width=14, height=14, bg="#ffffff", highlightthickness=0)
        self.status_light.pack(side="right", padx=(0, 8), pady=24)
        self.status_text = tk.Label(top, text="正在检测...", bg="#ffffff", fg="#666",
                                    font=("Microsoft YaHei", 10, "bold"))
        self.status_text.pack(side="right", padx=(0, 18), pady=20)

        # ---- 主体 ----
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=12)

        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(body, bg=BG, width=360)
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        self._build_settings(left)
        self._build_status(right)
        self._build_logs(right)

        self.msg_var = tk.StringVar()
        self.msg_label = tk.Label(self.root, text="", bg=BG, fg="#333", anchor="w", justify="left",
                                  font=("Microsoft YaHei", 9), wraplength=1000)
        self.msg_label.pack(fill="x", padx=16, pady=(0, 6))

        self.generated_key_var = tk.StringVar()
        tk.Label(self.root, textvariable=self.generated_key_var, bg="#fef7e0", fg=WARN,
                 font=("Microsoft YaHei", 9, "bold"), anchor="w", justify="left", wraplength=1000).pack(
            fill="x", padx=16, pady=(0, 10))

    def _build_settings(self, parent):
        # ===== AI 配置 =====
        ai_frame = ttk.LabelFrame(parent, text="AI 点评配置（多平台）")
        ai_frame.pack(fill="x", pady=(0, 10))
        ai_frame.columnconfigure(1, weight=1)

        self.ai_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(ai_frame, text="启用 AI 智能点评（关闭后判题功能仍正常）",
                        variable=self.ai_enabled).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 2))

        ttk.Label(ai_frame, text="AI 平台").grid(row=1, column=0, sticky="w", padx=12, pady=6)
        self.platform_var = tk.StringVar()
        self.platform_combo = ttk.Combobox(ai_frame, textvariable=self.platform_var, state="readonly",
                                           values=[n for _, n in self.platforms], width=30)
        self.platform_combo.grid(row=1, column=1, sticky="we", padx=12, pady=6)
        self.platform_combo.bind("<<ComboboxSelected>>", self._on_platform_change)

        self.platform_note_var = tk.StringVar()
        ttk.Label(ai_frame, textvariable=self.platform_note_var, foreground="#888",
                  font=("Microsoft YaHei", 8)).grid(row=2, column=1, sticky="w", padx=12)

        ttk.Label(ai_frame, text="API 密钥").grid(row=3, column=0, sticky="w", padx=12, pady=6)
        key_row = tk.Frame(ai_frame, bg=BG)
        key_row.grid(row=3, column=1, sticky="we", padx=12, pady=6)
        key_row.columnconfigure(0, weight=1)
        self.ai_key = ttk.Entry(key_row, show="*")
        self.ai_key.grid(row=0, column=0, sticky="we")
        self.ai_key_saved = ttk.Label(key_row, text="", foreground=OK_GREEN, font=("Microsoft YaHei", 8))
        self.ai_key_saved.grid(row=0, column=1, padx=(6, 0))
        ttk.Button(key_row, text="显示", width=5, command=lambda: self._toggle_show(self.ai_key)).grid(
            row=0, column=2, padx=(6, 0))
        ttk.Button(key_row, text="清空", width=5, command=lambda: self._clear_field("ai_api_key", self.ai_key)).grid(
            row=0, column=3, padx=(6, 0))

        ttk.Label(ai_frame, text="模型名称").grid(row=4, column=0, sticky="w", padx=12, pady=6)
        model_row = tk.Frame(ai_frame, bg=BG)
        model_row.grid(row=4, column=1, sticky="we", padx=12, pady=6)
        model_row.columnconfigure(0, weight=1)
        self.model_combo = ttk.Combobox(model_row, state="normal")
        self.model_combo.grid(row=0, column=0, sticky="we")
        self.model_saved = ttk.Label(model_row, text="", foreground=OK_GREEN, font=("Microsoft YaHei", 8))
        self.model_saved.grid(row=0, column=1, padx=(6, 0))
        ttk.Button(model_row, text="默认", width=5,
                   command=lambda: self._reset_default("ai_model", self.model_combo)).grid(row=0, column=2, padx=(6, 0))

        ttk.Label(ai_frame, text="接口地址").grid(row=5, column=0, sticky="w", padx=12, pady=6)
        base_row = tk.Frame(ai_frame, bg=BG)
        base_row.grid(row=5, column=1, sticky="we", padx=12, pady=6)
        base_row.columnconfigure(0, weight=1)
        self.base_url = ttk.Entry(base_row)
        self.base_url.grid(row=0, column=0, sticky="we")
        self.base_saved = ttk.Label(base_row, text="", foreground=OK_GREEN, font=("Microsoft YaHei", 8))
        self.base_saved.grid(row=0, column=1, padx=(6, 0))
        ttk.Button(base_row, text="默认", width=5,
                   command=lambda: self._reset_default("ai_base_url", self.base_url)).grid(row=0, column=2, padx=(6, 0))

        self.model_fetch_note = ttk.Label(ai_frame, text="填写平台和密钥后，可拉取该平台当前可用模型",
                                          foreground="#666", font=("Microsoft YaHei", 8))
        self.model_fetch_note.grid(row=6, column=1, sticky="w", padx=12, pady=(0, 2))
        btn_row = tk.Frame(ai_frame, bg=BG)
        btn_row.grid(row=7, column=0, columnspan=3, sticky="w", padx=12, pady=(2, 10))
        ttk.Button(btn_row, text="拉取可用模型", command=self._fetch_models).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="测试 AI 连接", command=self._test_ai).pack(side="left")
        self.ai_test_mark = tk.Label(btn_row, text="", bg=BG, font=("Microsoft YaHei", 15, "bold"))
        self.ai_test_mark.pack(side="left", padx=(8, 0))

        # ===== 管理员密钥 =====
        admin_frame = ttk.LabelFrame(parent, text="管理员密钥（后台管理入口）")
        admin_frame.pack(fill="x", pady=(0, 10))
        admin_frame.columnconfigure(1, weight=1)
        ttk.Label(admin_frame, text="管理密钥").grid(row=0, column=0, sticky="w", padx=12, pady=8)
        admin_row = tk.Frame(admin_frame, bg=BG)
        admin_row.grid(row=0, column=1, sticky="we", padx=12, pady=8)
        admin_row.columnconfigure(0, weight=1)
        self.admin_key = ttk.Entry(admin_row, show="*")
        self.admin_key.grid(row=0, column=0, sticky="we")
        self.admin_key_saved = ttk.Label(admin_row, text="", foreground=OK_GREEN, font=("Microsoft YaHei", 8))
        self.admin_key_saved.grid(row=0, column=1, padx=(6, 0))
        ttk.Button(admin_row, text="显示", width=5, command=lambda: self._toggle_show(self.admin_key)).grid(
            row=0, column=2, padx=(6, 0))
        ttk.Button(admin_row, text="生成随机", width=8, command=self._gen_admin_key).grid(row=0, column=3, padx=(6, 0))
        ttk.Button(admin_row, text="清空", width=5, command=lambda: self._clear_field("admin_key", self.admin_key)).grid(
            row=0, column=4, padx=(6, 0))

        # ===== 服务器设置 =====
        srv_frame = ttk.LabelFrame(parent, text="服务器设置")
        srv_frame.pack(fill="x", pady=(0, 10))
        srv_frame.columnconfigure(1, weight=1)
        srv_frame.columnconfigure(3, weight=1)
        ttk.Label(srv_frame, text="监听端口").grid(row=0, column=0, sticky="w", padx=12, pady=8)
        self.port_var = tk.StringVar(value="8081")
        ttk.Spinbox(srv_frame, from_=1, to=65535, textvariable=self.port_var, width=10).grid(
            row=0, column=1, sticky="w", padx=12, pady=8)
        ttk.Label(srv_frame, text="监听地址").grid(row=0, column=2, sticky="w", padx=(18, 12), pady=8)
        self.bind_var = tk.StringVar(value="0.0.0.0")
        ttk.Combobox(srv_frame, textvariable=self.bind_var, state="readonly", width=22,
                     values=["0.0.0.0（公网可访问）", "127.0.0.1（仅本机）"]).grid(
            row=0, column=3, sticky="we", padx=12, pady=8)
        self.open_browser = tk.BooleanVar(value=True)
        ttk.Checkbutton(srv_frame, text="启动成功后自动打开浏览器", variable=self.open_browser).grid(
            row=1, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 8))

        # ===== 公网访问 =====
        ng_frame = ttk.LabelFrame(parent, text="公网访问（ngrok 隧道，免费域名有限流）")
        ng_frame.pack(fill="x", pady=(0, 10))
        ng_frame.columnconfigure(1, weight=1)
        self.ngrok_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(ng_frame, text="启用 ngrok 公网隧道", variable=self.ngrok_enabled).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 2))
        ttk.Label(ng_frame, text="固定域名").grid(row=1, column=0, sticky="w", padx=12, pady=6)
        self.ngrok_domain = ttk.Entry(ng_frame)
        self.ngrok_domain.grid(row=1, column=1, sticky="we", padx=12, pady=6)
        ttk.Label(ng_frame, text="authtoken").grid(row=2, column=0, sticky="w", padx=12, pady=6)
        tok_row = tk.Frame(ng_frame, bg=BG)
        tok_row.grid(row=2, column=1, sticky="we", padx=12, pady=6)
        tok_row.columnconfigure(0, weight=1)
        self.ngrok_token = ttk.Entry(tok_row, show="*")
        self.ngrok_token.grid(row=0, column=0, sticky="we")
        self.ngrok_token_saved = ttk.Label(tok_row, text="", foreground=OK_GREEN, font=("Microsoft YaHei", 8))
        self.ngrok_token_saved.grid(row=0, column=1, padx=(6, 0))
        ttk.Button(tok_row, text="显示", width=5, command=lambda: self._toggle_show(self.ngrok_token)).grid(
            row=0, column=2, padx=(6, 0))
        ttk.Button(tok_row, text="清空", width=5,
                   command=lambda: self._clear_field("ngrok_authtoken", self.ngrok_token)).grid(
            row=0, column=3, padx=(6, 0))

        # ===== 操作 =====
        op_frame = ttk.LabelFrame(parent, text="操作")
        op_frame.pack(fill="x")
        btn_row2 = tk.Frame(op_frame, bg=BG)
        btn_row2.pack(fill="x", padx=12, pady=10)
        self.save_btn = ttk.Button(btn_row2, text="保存配置", command=self._save_config)
        self.save_btn.pack(side="left", padx=(0, 8))
        self.start_btn = ttk.Button(btn_row2, text="启动服务", command=self._start_server)
        self.start_btn.pack(side="left", padx=(0, 8))
        self.stop_btn = ttk.Button(btn_row2, text="停止服务", command=self._stop_server)
        self.stop_btn.pack(side="left", padx=(0, 8))
        ttk.Button(btn_row2, text="结束占用端口", command=self._kill_port).pack(side="left")

    def _build_status(self, parent):
        frame = ttk.LabelFrame(parent, text="运行状态")
        frame.pack(fill="x")
        frame.columnconfigure(1, weight=1)
        self.status_items = {}
        rows = [
            ("server", "服务状态"), ("health", "健康检查"), ("local", "本地地址"),
            ("public", "公网地址"), ("ai", "AI 状态"), ("pid", "进程 PID"),
            ("python", "Python"), ("compiler", "C++ 编译器"), ("ngrok", "ngrok"),
        ]
        for i, (key, label) in enumerate(rows):
            ttk.Label(frame, text=label, foreground="#777").grid(row=i, column=0, sticky="w", padx=12, pady=4)
            value = ttk.Label(frame, text="-", foreground="#333", wraplength=300, justify="right")
            value.grid(row=i, column=1, sticky="e", padx=12, pady=4)
            self.status_items[key] = value

    def _build_logs(self, parent):
        frame = ttk.LabelFrame(parent, text="运行日志")
        frame.pack(fill="both", expand=True, pady=(10, 0))
        self.log_text = tk.Text(frame, height=14, bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4",
                                font=("Consolas", 9), wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(6, 4))
        log_ctl = tk.Frame(frame, bg=BG)
        log_ctl.pack(fill="x", padx=10, pady=(0, 8))
        self.auto_scroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(log_ctl, text="自动滚动", variable=self.auto_scroll).pack(side="left")
        ttk.Button(log_ctl, text="刷新日志", command=self._refresh_logs).pack(side="right")

    # ==================== 配置读写 ====================

    def _platform_id(self):
        idx = self.platform_combo.current()
        if 0 <= idx < len(self.platform_ids):
            return self.platform_ids[idx]
        return self.platform_ids[0] if self.platform_ids else "custom"

    def _mask(self, value):
        return launcher.mask_secret(value)

    def _apply_config(self, cfg):
        self.saved_config = cfg or {}
        self.ai_enabled.set(bool(cfg.get("ai_enabled")))
        pid = cfg.get("ai_platform") or "siliconflow"
        if pid in self.platform_ids:
            self.platform_combo.current(self.platform_ids.index(pid))
        self._on_platform_change()
        self.port_var.set(str(cfg.get("port") or 8081))
        bind = cfg.get("bind_host") or "0.0.0.0"
        self.bind_var.set("0.0.0.0（公网可访问）" if bind == "0.0.0.0" else "127.0.0.1（仅本机）")
        self.open_browser.set(cfg.get("open_browser", True))
        self.ngrok_enabled.set(bool(cfg.get("ngrok_enabled")))
        self.ngrok_domain.delete(0, "end")
        self.ngrok_domain.insert(0, cfg.get("ngrok_domain") or "")

        # 密钥/模型/地址只显示脱敏提示，真实值不回填
        self._set_saved_label(self.ai_key_saved, cfg.get("ai_api_key"))
        self._set_saved_label(self.admin_key_saved, cfg.get("admin_key"))
        self._set_saved_label(self.ngrok_token_saved, cfg.get("ngrok_authtoken"))
        self._set_saved_label(self.model_saved, cfg.get("ai_model"), prefix="当前模型")
        self._set_saved_label(self.base_saved, cfg.get("ai_base_url"), prefix="当前地址")
        self.clear_flags = {}

    @staticmethod
    def _set_saved_label(label, value, prefix="已保存"):
        if value:
            label.config(text=f"{prefix}: {launcher.mask_secret(value)}")
        else:
            label.config(text="")

    def _collect_config(self):
        # 以已保存配置为基底：输入栏留空 = 沿用已保存值（密钥等不显示在输入框）
        cfg = launcher.load_config()
        cfg["ai_enabled"] = bool(self.ai_enabled.get())
        cfg["ai_platform"] = self._platform_id()
        try:
            cfg["port"] = int(self.port_var.get() or 8081)
        except ValueError:
            cfg["port"] = 0  # 交给 validate_config 报错
        cfg["bind_host"] = "0.0.0.0" if self.bind_var.get().startswith("0.0.0.0") else "127.0.0.1"
        cfg["open_browser"] = bool(self.open_browser.get())
        cfg["ngrok_enabled"] = bool(self.ngrok_enabled.get())
        cfg["ngrok_domain"] = self.ngrok_domain.get().strip()

        fields = [
            ("ai_api_key", self.ai_key),
            ("admin_key", self.admin_key),
            ("ngrok_authtoken", self.ngrok_token),
            ("ai_model", self.model_combo),
            ("ai_base_url", self.base_url),
        ]
        for key, widget in fields:
            value = widget.get().strip()
            if value:
                cfg[key] = value
            elif self.clear_flags.get(key):
                cfg[key] = ""
        return cfg

    def _clear_field(self, key, widget):
        widget.delete(0, "end")
        self.clear_flags[key] = True

    def _reset_default(self, key, widget):
        """把模型/接口地址恢复为当前平台的默认值"""
        info = ai_client.AI_PLATFORMS.get(self._platform_id(), {})
        value = info.get("default_model" if key == "ai_model" else "base_url", "")
        widget.delete(0, "end")
        if value:
            widget.insert(0, value)
        self.clear_flags[key] = False

    def _toggle_show(self, widget):
        widget.config(show="" if widget.cget("show") else "*")

    def _gen_admin_key(self):
        import secrets
        self.admin_key.delete(0, "end")
        self.admin_key.insert(0, secrets.token_hex(16))
        self.admin_key.config(show="")
        self.clear_flags["admin_key"] = False
        self._show_msg(f"已生成随机管理员密钥：{self.admin_key.get()}", OK_GREEN)

    def _on_platform_change(self, *_):
        pid = self._platform_id()
        info = ai_client.AI_PLATFORMS.get(pid, {})
        note = info.get("note", "")
        default_model = info.get("default_model", "")
        default_base = info.get("base_url", "")
        self.platform_note_var.set(f"提示：{note}" if note else "")

        saved_model = (self.saved_config.get("ai_model") or "").strip()
        saved_base = (self.saved_config.get("ai_base_url") or "").strip()

        # 接口地址：为空或仍是上一个平台的默认值 -> 填入已保存值或本平台默认值
        current_base = self.base_url.get().strip()
        if not current_base or (self._last_base_default and current_base == self._last_base_default):
            self._set_entry(self.base_url, saved_base or default_base)
        self._last_base_default = default_base

        # 模型：同上，并把默认模型放入下拉候选
        current_model = self.model_combo.get().strip()
        if not current_model or (self._last_model_default and current_model == self._last_model_default):
            self._set_entry(self.model_combo, saved_model or default_model)
        candidates = []
        for m in [saved_model or default_model, default_model, current_model]:
            if m and m not in candidates:
                candidates.append(m)
        self.model_combo.configure(values=candidates)
        self._last_model_default = default_model
        self.model_fetch_note.config(text="填写平台和密钥后，可拉取该平台当前可用模型", foreground="#666")
        self.ai_test_mark.config(text="", fg="#333")

    @staticmethod
    def _set_entry(widget, value):
        widget.delete(0, "end")
        if value:
            widget.insert(0, value)

    # ==================== 状态与日志 ====================

    def _schedule_refresh(self):
        self.root.after(3000, self._refresh_state)

    def _refresh_state(self):
        try:
            st = launcher.build_state()
        except Exception:
            self._schedule_refresh()
            return
        running = bool(st.get("server_running"))
        health = st.get("health")

        # 顶栏状态
        if running and health:
            self.status_light.delete("all")
            self.status_light.create_oval(1, 1, 13, 13, fill="#34a853", outline="")
            self.status_text.config(text="服务运行中", fg=OK_GREEN)
        elif running:
            self.status_light.delete("all")
            self.status_light.create_oval(1, 1, 13, 13, fill="#ea4335", outline="")
            self.status_text.config(text="运行异常", fg=ERR_RED)
        else:
            self.status_light.delete("all")
            self.status_light.create_oval(1, 1, 13, 13, fill="#9e9e9e", outline="")
            self.status_text.config(text="服务已停止", fg="#5f6368")

        self._set_status("server", "运行中" if running else "已停止",
                         OK_GREEN if running else ERR_RED)
        self._set_status("health", "正常" if health else ("异常" if running else "-"),
                         OK_GREEN if health else ("#c5221f" if running else "#999"))
        self._set_status("local", st.get("local_url") or "-", ACCENT if running else "#999")
        if st.get("public_url"):
            self._set_status("public", st.get("public_url"), ACCENT)
        else:
            self._set_status("public", "获取中..." if st.get("ngrok_running") else "未启用", "#999")
        ai = st.get("ai_info") or {}
        if ai.get("available"):
            self._set_status("ai", f"{ai.get('platform_name')} · {ai.get('model')}", OK_GREEN)
        else:
            self._set_status("ai", "已禁用", ERR_RED)
        self._set_status("pid", f"PID {st['server_pid']}" if st.get("server_pid") else "-", "#333")
        tools = st.get("tools") or {}
        self._set_status("python", tools.get("python") or "-")
        self._set_status("compiler", tools.get("compiler") or "-")
        self._set_status("ngrok", tools.get("ngrok") or "-")

        self.start_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")

        if st.get("last_error") and not running:
            self._show_msg(st["last_error"], ERR_RED)
        if running and not self.last_running:
            self._refresh_logs()
        self.last_running = running
        self._schedule_refresh()

    def _set_status(self, key, text, color="#333"):
        label = self.status_items.get(key)
        if label:
            label.config(text=text, foreground=color)

    def _refresh_logs(self):
        data = launcher._tail_logs(200)
        lines = []
        if data["stdout"]:
            lines.append("── server_stdout.log ──")
            lines.extend(data["stdout"])
        if data["stderr"]:
            lines.append("")
            lines.append("── server_stderr.log ──")
            lines.extend(data["stderr"])
        text = "\n".join(lines) if lines else "暂无日志"
        stick = self.auto_scroll.get()
        top = None if stick else self.log_text.yview()[0]  # 关闭自动滚动时保留当前位置
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", text)
        self.log_text.config(state="disabled")
        if stick:
            self.log_text.see("end")
        elif top is not None:
            self.log_text.yview_moveto(top)

    def _show_msg(self, text, color="#333"):
        self.msg_label.config(text=text, fg=color)

    # ==================== 操作（后台线程执行，避免卡界面） ====================

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.save_btn, self.start_btn, self.stop_btn):
            btn.config(state=state)

    def _save_config(self):
        cfg = self._collect_config()
        errors = launcher.validate_config(cfg)
        if errors:
            self._show_msg("\n".join(errors), ERR_RED)
            return
        launcher.save_config(cfg)
        self._apply_config(launcher.load_config())
        self._show_msg("配置已保存。", OK_GREEN)

    def _start_server(self):
        if self.busy:
            self._show_msg("正在执行其他操作（启动/停止中），请稍候", WARN)
            return
        cfg = self._collect_config()
        errors = launcher.validate_config(cfg)
        if errors:
            self._show_msg("\n".join(errors), ERR_RED)
            return
        self._set_busy(True)
        self._show_msg("正在启动服务，请稍候...", "#666")

        def worker():
            result = launcher.start_services(cfg)
            self._post(("start", result))

        threading.Thread(target=worker, daemon=True).start()

    def _on_start_done(self, result):
        self._set_busy(False)
        if result.get("ok"):
            lines = ["服务启动成功"]
            if result.get("public_url"):
                lines.append(f"公网地址：{result['public_url']}")
            self._show_msg("\n".join(lines), OK_GREEN)
            if result.get("generated_admin_key"):
                self.generated_key_var.set(
                    "⚠ 未设置管理员密钥，已自动生成（请立即保存）：" + result["generated_admin_key"])
            if result.get("warnings"):
                self._show_msg("服务已启动，但有警告：\n" + "\n".join(result["warnings"]), WARN)
            self._refresh_state()
            self._refresh_logs()
        else:
            errors = result.get("errors") or ["启动失败"]
            self._show_msg("\n".join(errors), ERR_RED)
            tail = result.get("log_tail") or {}
            if tail:
                lines = (tail.get("stdout") or []) + (tail.get("stderr") or [])
                self.log_text.config(state="normal")
                self.log_text.delete("1.0", "end")
                self.log_text.insert("1.0", "\n".join(lines) or "（无日志）")
                self.log_text.config(state="disabled")
            self._refresh_state()

    def _stop_server(self):
        if self.busy:
            self._show_msg("正在执行其他操作（启动/停止中），请稍候", WARN)
            return
        if not messagebox.askyesno("停止服务", "确定停止服务吗？已启动的 ngrok 隧道会一并关闭。"):
            return
        self._set_busy(True)
        self._show_msg("正在停止服务...", "#666")

        def worker():
            result = launcher.stop_services()
            self._post(("stop", result))

        threading.Thread(target=worker, daemon=True).start()

    def _on_stop_done(self, result):
        self._set_busy(False)
        if result.get("ok"):
            stopped = "、".join(result.get("stopped") or ["无"])
            self._show_msg(f"服务已停止：{stopped}", OK_GREEN)
            self.generated_key_var.set("")
        else:
            self._show_msg("停止失败", ERR_RED)
        self._refresh_state()

    def _fetch_models(self):
        if self.busy:
            self._show_msg("正在执行其他操作，请稍候再试", WARN)
            return
        key, platform, base, _ = self._ai_payload()
        self.model_fetch_note.config(text="正在拉取，请稍候...", foreground="#666")

        def worker():
            ok, msg, models = ai_client.list_models(
                api_key=key,
                platform=platform,
                base_url=base,
            )
            self._post(("models", ok, msg, models))

        threading.Thread(target=worker, daemon=True).start()

    def _on_models_done(self, ok, msg, models):
        if models:
            current = self.model_combo.get()
            values = list(models)
            if current and current not in values:
                values.insert(0, current)
            self.model_combo.configure(values=values)
            color = OK_GREEN if ok else WARN
            suffix = "（点击模型输入框可下拉选择）" if ok else "（以下为内置备选，仅供参考）"
            self.model_fetch_note.config(text=f"{msg}{suffix}", foreground=color)
        else:
            self.model_fetch_note.config(text=f"{msg or '拉取失败'}，可直接手动输入模型名", foreground=ERR_RED)

    def _test_ai(self):
        if self.busy:
            self._show_msg("正在执行其他操作，请稍候再试", WARN)
            return
        key, platform, base, model = self._ai_payload()
        self._show_msg("正在测试连接（最长约 15 秒），请稍候...", "#666")
        self.ai_test_mark.config(text="…", fg="#999")

        def worker():
            try:
                ok, msg = ai_client.test_connection(
                    api_key=key,
                    platform=platform,
                    base_url=base,
                    model=model,
                )
            except Exception as e:
                ok, msg = False, f"测试异常：{e}"
            self._post(("testai", ok, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _ai_payload(self):
        """在主线程读取 AI 相关输入（含已保存值回退），供工作线程使用"""
        saved = self.saved_config
        key = self.ai_key.get() or ("" if self.clear_flags.get("ai_api_key") else saved.get("ai_api_key", ""))
        base = self.base_url.get() or ("" if self.clear_flags.get("ai_base_url") else saved.get("ai_base_url", ""))
        model = self.model_combo.get() or ("" if self.clear_flags.get("ai_model") else saved.get("ai_model", ""))
        return key, self._platform_id(), base, model

    def _kill_port(self):
        port = simpledialog.askinteger("结束占用端口", "请输入要结束占用的端口号：",
                                       initialvalue=int(self.port_var.get() or 8081), minvalue=1, maxvalue=65535)
        if not port:
            return
        pid = launcher.pid_by_port(port)
        if not pid:
            self._show_msg(f"端口 {port} 当前没有被监听", ERR_RED)
            return
        if launcher.kill_pid(pid):
            self._show_msg(f"已结束占用端口 {port} 的进程（PID {pid}）", OK_GREEN)
        else:
            self._show_msg(f"结束 PID {pid} 失败，请手动处理", ERR_RED)


def main():
    root = tk.Tk()
    app = LauncherApp(root)
    if "--smoke" in sys.argv:
        root.withdraw()
        root.update_idletasks()
        print("GUI build OK")
        root.destroy()
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
