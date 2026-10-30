# logs_panel.py — 日志流区（tasks 9.5②, Trace: D-FE-001 区域 6）
#
# design §6.3 ⑥：run_log 实时 tail（内存环形 200 行 + IPC log 事件追加）、
# 导出（export_logs 落文件）/ 清空（clear_logs(confirmed=true) 二次确认，
# 取消则不发请求——9.5 验收）。

from __future__ import annotations

import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from desktop.redaction import redact_text

RING_LIMIT = 200  # 内存环形行数（design §6.3 ⑥）

_LEVEL_COLORS = {"DEBUG": "#999999", "INFO": "#333333",
                 "WARN": "#cc7700", "ERROR": "#cc0000"}


class LogsPanel:
    """日志流区：环形 tail + 导出/清空。"""

    def __init__(self, window, frame: tk.Frame):
        self._window = window
        self._body = ttk.Frame(frame)
        self._body.pack(fill=tk.BOTH, expand=True)

        bar = ttk.Frame(self._body)
        bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        ttk.Button(bar, text="刷新", command=self.reload).pack(side=tk.LEFT)
        ttk.Button(bar, text="导出日志…",
                   command=self._on_export).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(bar, text="清空日志",
                   command=self._on_clear).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(bar, text=f"实时显示最近 {RING_LIMIT} 行",
                  foreground="#666").pack(side=tk.RIGHT)

        self._text = tk.Text(self._body, wrap="none", state="disabled",
                             font=("Menlo", 11), background="#fafafa")
        scroll_y = ttk.Scrollbar(self._body, orient=tk.VERTICAL,
                                 command=self._text.yview)
        self._text.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        for level, color in _LEVEL_COLORS.items():
            self._text.tag_configure(level, foreground=color)

        window.on("log", self._on_log_event)
        window.on("child_ready", lambda _p: self.reload())
        self.reload()

    # ---------- 数据 ----------
    def reload(self) -> None:
        self._window.request("get_logs", None, self._on_loaded)

    def _on_loaded(self, resp: dict) -> None:
        if not resp.get("ok"):
            return
        rows = resp.get("logs") or []
        # query_run 为 id DESC（新→旧）；取最新 200 条后按时间正序填充
        rows = rows[:RING_LIMIT]
        rows.reverse()
        self._set_lines(
            [self._format(r.get("created_at"), r.get("level"),
                          r.get("code"), r.get("message")) for r in rows],
            [r.get("level") for r in rows],
        )

    def _on_log_event(self, payload: dict) -> None:
        """log 事件实时 tail（主线程回调）。"""
        self._append(payload.get("level", "INFO"),
                     self._format(payload.get("ts"), payload.get("level"),
                                  payload.get("code"), payload.get("message")))

    @staticmethod
    def _format(ts, level, code, message) -> str:
        when = time.strftime("%H:%M:%S",
                             time.localtime(ts)) if ts else "--:--:--"
        return f"{when} [{level or '-'}] {code or '-'} {message or ''}"

    # ---------- 环形缓冲渲染 ----------
    def _set_lines(self, lines: list[str], levels: list[str] | None = None) -> None:
        self._text.config(state="normal")
        self._text.delete("1.0", tk.END)
        for i, line in enumerate(lines):
            level = (levels or [None] * len(lines))[i]
            self._text.insert(tk.END, redact_text(line) + "\n",
                              level if level in _LEVEL_COLORS else ())
        self._text.see(tk.END)
        self._text.config(state="disabled")

    def _append(self, level: str, line: str) -> None:
        self._text.config(state="normal")
        self._text.insert(tk.END, redact_text(line) + "\n",
                          level if level in _LEVEL_COLORS else ())
        # 环形：超出上限即从头部裁剪（1000 行注入不卡 UI）
        count = int(float(self._text.index("end-1c").split(".")[0]))
        if count > RING_LIMIT:
            self._text.delete("1.0", f"{count - RING_LIMIT + 1}.0")
        self._text.see(tk.END)
        self._text.config(state="disabled")

    # ---------- 按钮 ----------
    def _on_export(self) -> None:
        def done(resp: dict) -> None:
            if not resp.get("ok"):
                err = resp.get("error") or {}
                self._window.show_banner(err.get("message") or "导出失败")
                return
            lines = resp.get("lines") or []
            path = filedialog.asksaveasfilename(
                title="导出日志", defaultextension=".txt",
                filetypes=[("文本文件", "*.txt")],
                initialfile=time.strftime("sms-relay-log-%Y%m%d-%H%M%S.txt"))
            if not path:
                return  # 取消保存位置则不落文件
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + "\n")
            except OSError as exc:
                self._window.show_banner(f"导出失败：{exc}")
                return
            self._window.show_banner(f"已导出 {len(lines)} 行日志", 4000)

        self._window.request("export_logs", None, done)

    def _on_clear(self) -> None:
        # 二次确认在前端；取消则不发请求（9.5 验收）
        if not messagebox.askyesno("清空日志",
                                   "确认清空全部运行日志、投递日志与告警记录？\n"
                                   "该操作不可恢复。"):
            return

        def done(resp: dict) -> None:
            if resp.get("ok"):
                self._set_lines([])
                self._window.show_banner("日志已清空", 3000)
            else:
                err = resp.get("error") or {}
                self._window.show_banner(err.get("message") or "清空失败")

        self._window.request("clear_logs", {"confirmed": True}, done)


def build(window) -> LogsPanel:
    """面板工厂（main_window._build_panels 调用）。"""
    return LogsPanel(window, window.frames["logs"])
