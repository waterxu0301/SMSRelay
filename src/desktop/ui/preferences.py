# preferences.py — 偏好设置对话框（tasks 9.8 开关入口, Trace: D-FE-010）
#
# 开机自启开关：真实态取 AutostartService.status()；dev 态（非 .app bundle）
# → unsupported 禁用控件并说明「打包后生效」；操作成功后经 IPC set_autostart
# 通知子进程记 run_log（5.6 第 4 点）；全程无 LaunchAgent plist。

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from desktop.autostart import AutostartService

_NOTES = {
    "unsupported": "dev 态（源码运行）不支持自启，打包为 .app 后生效。",
    "requiresApproval": "已提交注册，需在 系统设置 → 通用 → 登录项 中批准后生效。",
    "notFound": "未找到应用条目（打包为 .app 后可注册）。",
}


def open_dialog(window) -> None:
    """打开偏好设置对话框（状态栏「偏好设置」按钮调用）。"""
    PreferencesDialog(window)


class PreferencesDialog:
    """偏好设置：开机自启开关。"""

    def __init__(self, window):
        self._window = window
        parent = window._root
        self._dlg = tk.Toplevel(parent)
        self._dlg.title("偏好设置")
        self._dlg.transient(parent)
        self._dlg.grab_set()
        self._dlg.resizable(False, False)

        body = ttk.Frame(self._dlg, padding=14)
        body.pack(fill=tk.BOTH, expand=True)

        self._autostart_var = tk.BooleanVar()
        self._toggle = ttk.Checkbutton(
            body, text="开机自动启动（登录时自动打开 SMS 接力）",
            variable=self._autostart_var,
            command=self._on_autostart_toggle)
        self._toggle.pack(anchor="w")

        self._note = ttk.Label(body, text="", foreground="#666", wraplength=360)
        self._note.pack(anchor="w", pady=(6, 0))

        ttk.Button(body, text="关闭", command=self._dlg.destroy).pack(
            anchor="e", pady=(12, 0))

        self._refresh()

    # ---------- 状态 ----------
    def _refresh(self) -> None:
        state = AutostartService.status()
        self._toggle.config(state="disabled" if state == "unsupported"
                            else "normal")
        self._autostart_var.set(state == "enabled")
        self._note.config(text=_NOTES.get(state, ""))

    # ---------- 切换 ----------
    def _on_autostart_toggle(self) -> None:
        requested = self._autostart_var.get()
        ok, state = AutostartService.set_enabled(requested)
        if not ok:
            self._autostart_var.set(not requested)  # 失败回滚
            self._window.show_banner(
                f"开机自启设置失败：{state}", 6000)
            self._refresh()
            return
        # 操作成功 → IPC 通知子进程记 run_log（set_autostart 仅记日志）
        self._window.request("set_autostart", {"enabled": requested})
        self._refresh()
        if state == "requiresApproval":
            self._window.show_banner(
                "已提交开机自启注册，需在系统设置中批准", 6000)
        else:
            self._window.show_banner(
                f"开机自启已{'开启' if requested else '关闭'}", 3000)
