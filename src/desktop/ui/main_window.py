# main_window.py — 主窗口骨架与状态栏（tasks 9.2, Trace: D-FE-001）
#
# design §6.3 ①：顶部状态栏（核心态/监听态/轮询间隔/免打扰/水位线/已处理数）
# + 8 区域占位容器（状态栏 + 7 个功能页签，spec ui-elements 清单）；
# status 事件实时刷新（child_ready 前显示「启动中」）；
# 关闭窗口协议 WM_DELETE_WINDOW → withdraw() 隐藏至托盘（关窗≠退出）。
# 状态栏右侧含监听启停按钮（change main-window-listen-toggle：托盘等价入口）。

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from desktop.redaction import redact_text

# 7 个功能页签（spec ui-elements 区域 2-8）
PAGES = (
    ("targets", "投递目标"),
    ("email", "邮件通道"),
    ("filters", "过滤与免打扰"),
    ("stats", "投递统计"),
    ("logs", "日志流"),
    ("alerts", "告警记录"),
    ("debug", "调试动作"),
)


class MainWindow:
    """主窗口：状态栏 + 页签容器 + IPC 事件路由。"""

    def __init__(self, root: tk.Tk):
        self._root = root
        self._supervisor = None
        self._tray = None
        self._listeners: dict[str, list[Callable]] = {}
        self._banner_after_id = None
        self._listen_btn: ttk.Button | None = None
        self._listening = False  # 监听态缓存（按钮文案/乐观更新，change main-window-listen-toggle）

        root.title("SMS 接力")
        root.geometry("920x640")
        root.minsize(760, 520)
        root.protocol("WM_DELETE_WINDOW", self._on_close)  # 关窗仅隐藏

        self._build_status_bar()
        self._build_pages()

    # ---------- 构建 ----------
    def _build_status_bar(self) -> None:
        bar = ttk.Frame(self._root, padding=(10, 6))
        bar.pack(side=tk.TOP, fill=tk.X)

        self._labels: dict[str, tk.Label] = {}
        # (key, 显示名)
        specs = (
            ("core", "核心"),
            ("listening", "监听"),
            ("interval", "间隔"),
            ("silent", "免打扰"),
            ("watermark", "水位线"),
            ("processed", "已处理"),
            ("buffered", "缓冲"),
        )
        for key, title in specs:
            tk.Label(bar, text=f"{title}:", fg="#666").pack(side=tk.LEFT, padx=(8, 2))
            lbl = tk.Label(bar, text="—", fg="#333")
            lbl.pack(side=tk.LEFT)
            self._labels[key] = lbl

        self._retry_btn = ttk.Button(bar, text="手动重试",
                                     command=self._on_manual_retry)
        self._retry_btn.pack(side=tk.RIGHT)
        self._retry_btn.pack_forget()  # 仅 unavailable 态显示

        # 偏好设置入口（9.8 开机自启开关）
        ttk.Button(bar, text="偏好设置", command=self._open_prefs).pack(
            side=tk.RIGHT, padx=(0, 6))

        # 监听启停按钮（change main-window-listen-toggle：托盘等价入口，
        # 文案随 listening 切换；点击逻辑同 tray.toggle_listen）
        self._listen_btn = ttk.Button(bar, text="开始监听",
                                      command=self._on_toggle_listen)
        self._listen_btn.pack(side=tk.RIGHT, padx=(0, 6))

        # 顶部横幅（告警兜底/提示用，9.5/9.11 复用）
        self._banner = tk.Label(self._root, text="", fg="white", bg="#b00020",
                                anchor="w", padx=10)
        # 默认不 pack，show_banner() 时出现

        self.set_core_status("starting")

    def _build_pages(self) -> None:
        notebook = ttk.Notebook(self._root)
        notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=6)
        self._notebook = notebook
        self.frames: dict[str, tk.Frame] = {}
        for key, title in PAGES:
            frame = ttk.Frame(notebook, padding=8)
            notebook.add(frame, text=title)
            self.frames[key] = frame
        self._build_panels()

    def _build_panels(self) -> None:
        """按页签顺序构建各功能面板（9.3-9.6/9.13）。"""
        from desktop.ui import (targets_panel, email_panel, filter_panel,
                                stats_panel, logs_panel, alert_panel,
                                debug_panel, permission_gate)
        targets_panel.build(self)
        email_panel.build(self)
        filter_panel.build(self)
        stats_panel.build(self)
        logs_panel.build(self)
        alert_panel.build(self)
        debug_panel.build(self)
        permission_gate.build(self)  # 授权引导（9.10，非页签为弹层）

    # ---------- 对外 API ----------
    def set_supervisor(self, supervisor) -> None:
        self._supervisor = supervisor

    def set_tray(self, tray) -> None:
        """app.py 注入托盘引用（授权引导态置灰监听菜单用）。"""
        self._tray = tray

    def request(self, cmd: str, params: dict | None = None,
                callback: Callable[[dict], None] | None = None) -> bool:
        """异步 IPC 请求（面板统一入口）；子进程不可用返回 False。"""
        if self._supervisor is None or self._supervisor.client is None:
            if callback:
                callback({"ok": False, "error": {
                    "code": "UNAVAILABLE", "message": "核心子进程不可用"}})
            return False
        self._supervisor.client.request_async(cmd, params, callback)
        return True

    def set_core_status(self, state: str) -> None:
        """核心进程状态：running / restarting / unavailable / starting。"""
        text, color = {
            "starting": ("启动中", "gray"),
            "running": ("核心运行中", "green"),
            "restarting": ("核心重建中", "orange"),
            "unavailable": ("监听不可用", "red"),
        }.get(state, (state, "gray"))
        self._labels["core"].config(text=text, fg=color)
        if state == "unavailable":
            if not self._retry_btn.winfo_ismapped():
                self._retry_btn.pack(side=tk.RIGHT)
        elif self._retry_btn.winfo_ismapped():
            self._retry_btn.pack_forget()

    def apply_status(self, status: dict) -> None:
        """status 事件 / get_status 响应 → 状态栏（真实数据）。"""
        listening = bool(status.get("listening"))
        degraded = bool(status.get("degraded"))
        if degraded:
            self._labels["listening"].config(text="读取受限", fg="orange")
        else:
            self._labels["listening"].config(
                text="监听中" if listening else "已停止",
                fg="green" if listening else "red")
        self._set_listen_btn_text(listening)  # 按钮文案与状态标签同源切换
        self._labels["interval"].config(text=f"{status.get('interval', 0)}s")
        self._labels["watermark"].config(text=str(status.get("watermark", "-")))
        self._labels["processed"].config(text=str(status.get("processed_count", 0)))
        self._labels["buffered"].config(text=str(status.get("buffered_codes", 0)))

    def _set_listen_btn_text(self, listening: bool) -> None:
        """监听按钮文案切换（change main-window-listen-toggle）。"""
        self._listening = listening
        if self._listen_btn is not None:
            self._listen_btn.config(
                text="停止监听" if listening else "开始监听")

    def set_listen_enabled(self, enabled: bool) -> None:
        """监听启停入口统一置灰/恢复（授权引导态；托盘菜单 + 主窗口按钮）。

        permission_gate 调用本方法；按钮与托盘共享同一授权态信号，
        任一侧被引导态置灰时另一侧同步（change main-window-listen-toggle）。
        """
        if self._listen_btn is not None:
            self._listen_btn.config(
                state=tk.NORMAL if enabled else tk.DISABLED)
        if self._tray is not None:
            self._tray.set_listen_enabled(enabled)

    def _on_toggle_listen(self) -> None:
        """监听启停（与 tray.toggle_listen 同模式：乐观更新 → IPC → 同步/回滚）。

        change fix-ipc-write-loop-death：回执的 listening 是核心真实状态，
        用它同时驱动按钮文案与「监听」状态标签，不再依赖 status 事件时序
        （事件可能延迟或在主循环未就绪时被丢弃），杜绝界面自相矛盾。
        """
        if self._listen_btn is None:
            return
        want = not self._listening
        self._set_listen_btn_text(want)  # 乐观更新，回执兜底纠正

        def _show_listen(listening: bool) -> None:
            """按钮文案 + 状态标签同源刷新（单一入口，避免二者不一致）。"""
            self._set_listen_btn_text(listening)
            self._labels["listening"].config(
                text="监听中" if listening else "已停止",
                fg="green" if listening else "red")

        def done(resp: dict) -> None:
            if resp.get("ok"):
                _show_listen(bool(resp.get("listening")))
            else:
                _show_listen(not want)  # 失败回滚（两者同步）
                err = resp.get("error") or {}
                self.show_banner(err.get("message") or "操作失败")

        self.request("start" if want else "stop", None, done)

    def set_silent_display(self, text: str) -> None:
        """过滤免打扰区保存后同步状态栏（9.4 调用）。"""
        self._labels["silent"].config(text=text or "未设置")

    def on(self, evt: str, callback: Callable[[dict], None]) -> None:
        """面板订阅 IPC 事件（log/scan_result/delivery/alert/...）。"""
        self._listeners.setdefault(evt, []).append(callback)

    def handle_event(self, evt: str, payload: dict) -> None:
        """Supervisor 事件回调（主线程）：状态栏内部处理 + 分发面板。"""
        if evt == "status":
            self.apply_status(payload)
        elif evt == "child_ready":
            self.set_core_status("running")
        for callback in self._listeners.get(evt, ()):
            callback(payload)

    def on_core_ready(self) -> None:
        """child_ready 到达：拉取初始配置（间隔/免打扰显示）。"""
        self.set_core_status("running")
        self.request("reload_config", None, self._on_config_loaded)

    def show(self) -> None:
        """二次启动聚焦：恢复显示并前置（tasks 9.12）。"""
        self._root.deiconify()
        self._root.lift()
        self._root.attributes("-topmost", True)
        self._root.after(400, lambda: self._root.attributes("-topmost", False))
        self._root.focus_force()

    def show_banner(self, text: str, duration_ms: int = 6000) -> None:
        """顶部横幅提示（告警兜底/异常提示，自动消失）。"""
        self._banner.config(text=redact_text(text))
        if not self._banner.winfo_ismapped():
            self._banner.pack(side=tk.TOP, fill=tk.X, before=self._notebook)
        if self._banner_after_id:
            self._root.after_cancel(self._banner_after_id)
        self._banner_after_id = self._root.after(
            duration_ms, lambda: self._banner.pack_forget())

    def quit_app(self) -> None:
        """退出（托盘菜单「退出」调用）：先停核心再销毁窗口。"""
        if self._supervisor:
            self._supervisor.shutdown()
        self._root.destroy()

    # ---------- 内部 ----------
    def _on_close(self) -> None:
        self._root.withdraw()  # 关窗仅隐藏至托盘（9.9 承接菜单）

    def _on_manual_retry(self) -> None:
        if self._supervisor:
            self._supervisor.manual_retry()

    def _open_prefs(self) -> None:
        """偏好设置对话框（9.8 开机自启开关入口）。"""
        from desktop.ui import preferences
        preferences.open_dialog(self)

    def _on_config_loaded(self, resp: dict) -> None:
        if not resp.get("ok"):
            return
        config = resp.get("config") or {}
        start = (config.get("silent_start") or "").strip()
        end = (config.get("silent_end") or "").strip()
        if start and end:
            note = "（跨午夜）" if end < start else ""
            self.set_silent_display(f"{start}-{end}{note}")
        else:
            self.set_silent_display("")
