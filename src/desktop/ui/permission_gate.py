# permission_gate.py — 完全磁盘访问授权引导页（tasks 9.10, Trace: D-FE-003）
#
# design §6.3 授权引导页 + §10.3 数据流：
# 启动（child_ready）→ IPC check_db 探测（复用 3.1 check_readable）→
# readable=False → 引导弹层（原因文案 + 打开 FDA 设置 + 重新检测 +
# 重启生效提示）；ok → 恢复；运行中 log 事件含授权被拒（SCAN_AUTH_DENIED）
# → 引导弹层叠加，**监听保持存活不崩溃**；引导态托盘「开始监听」置灰。

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# check_db reason → 引导文案（readable=False 的分类说明）
_REASON_TEXT = {
    "authorization": "系统未授予「完全磁盘访问权限」，无法读取信息数据库。",
    "missing_fda": "未检测到完全磁盘访问授权，无法读取信息数据库。",
    "missing": "未找到信息数据库（chat.db），请确认本机存在信息应用数据。",
    "locked": "数据库当前被占用，请稍后重新检测。",
}

_FDA_URL = ("x-apple.systempreferences:"
            "com.apple.preference.security?Privacy_AllFiles")


def build(window) -> "PermissionGate":
    """面板构建入口（main_window 统一调用）。"""
    gate = PermissionGate(window)
    window.permission_gate = gate
    return gate


class PermissionGate:
    """授权引导：探测 → 引导弹层 → 重新检测闭环。"""

    def __init__(self, window):
        self._window = window
        self._gate_win: tk.Toplevel | None = None
        window.on("child_ready", self._on_child_ready)
        window.on("log", self._on_log)

    # ---------- 探测 ----------
    def _on_child_ready(self, _payload: dict) -> None:
        """子进程就绪（启动/重连）→ 探测一次。"""
        self.probe()

    def probe(self) -> None:
        """IPC check_db 探测（启动与「重新检测」共用）。"""
        self._window.request("check_db", None, self._on_probe)

    def _on_probe(self, resp: dict) -> None:
        if not resp.get("ok"):
            return  # 探测失败不强制引导（核心不可用由状态栏呈现）
        if resp.get("readable"):
            self.close()
        else:
            self.open(resp.get("reason") or "authorization")

    # ---------- 运行中授权被撤销 ----------
    def _on_log(self, payload: dict) -> None:
        code = payload.get("code") or ""
        message = payload.get("message") or ""
        if code == "SCAN_AUTH_DENIED" or "authorization" in message:
            # 监听不中断（核心下轮重试），仅叠加引导弹层
            self.open("authorization")

    # ---------- 引导弹层 ----------
    def open(self, reason: str) -> None:
        """展示引导弹层（重复触发时仅刷新文案）。"""
        text = _REASON_TEXT.get(reason, _REASON_TEXT["authorization"])
        if self._gate_win is not None and self._gate_win.winfo_exists():
            self._label.config(text=text)
            self._gate_win.lift()
            return

        win = tk.Toplevel(self._window._root)
        win.title("需要完全磁盘访问权限")
        win.geometry("460x220")
        win.resizable(False, False)
        win.transient(self._window._root)

        tk.Label(win, text="无法读取信息数据库", font=("", 14, "bold"),
                 anchor="w").pack(fill=tk.X, padx=16, pady=(16, 6))
        label = tk.Label(win, text=text, anchor="w", justify=tk.LEFT,
                         wraplength=420)
        label.pack(fill=tk.X, padx=16)
        tk.Label(win, text="重新授权后需重启应用生效。",
                 fg="#666", anchor="w").pack(fill=tk.X, padx=16, pady=(6, 0))

        btns = ttk.Frame(win, padding=16)
        btns.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(btns, text="打开完全磁盘访问设置",
                   command=self._open_settings).pack(side=tk.LEFT)
        ttk.Button(btns, text="我已授权，重新检测",
                   command=self.probe).pack(side=tk.RIGHT)

        self._gate_win = win
        self._label = label
        win.protocol("WM_DELETE_WINDOW", win.withdraw)  # 引导必须可见，仅可隐藏
        # 引导态统一置灰监听入口（change main-window-listen-toggle：
        # MainWindow.set_listen_enabled 内部同时控制托盘菜单与主窗口按钮）
        self._window.set_listen_enabled(False)

    def close(self) -> None:
        """探测通过 → 关闭弹层并恢复监听入口。"""
        if self._gate_win is not None:
            if self._gate_win.winfo_exists():
                self._gate_win.destroy()
            self._gate_win = None
            self._label = None
        self._window.set_listen_enabled(True)

    # ---------- 动作 ----------
    def _open_settings(self) -> None:
        try:
            from AppKit import NSWorkspace
            from Foundation import NSURL
            NSWorkspace.sharedWorkspace().openURL_(
                NSURL.URLWithString_(_FDA_URL))
        except ImportError:
            # dev 态无 PyObjC → 引导手动打开
            self._window.show_banner(
                "请手动打开：系统设置 → 隐私与安全性 → 完全磁盘访问权限")
