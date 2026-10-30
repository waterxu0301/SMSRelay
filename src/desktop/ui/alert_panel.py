# alert_panel.py — 告警记录区（tasks 9.5③, Trace: D-FE-001 区域 7）
#
# design §6.3 ⑦：alert 事件实时插入 + 近 7 天告警列表（时间、目标、
# 原因摘要，内容已脱敏；spec failure-alerting）。
# 历史数据无独立查询命令 → 按 tasks 9.5 编排经 get_logs 过滤
# DELIVERY_ALL_FAILED（AlertManager 落 run_log 的告警行）。

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from desktop.redaction import redact_text

_MAX_ROWS = 200
_ALERT_CODE = "DELIVERY_ALL_FAILED"
_ALERT_PREFIX = "投递失败告警："


class AlertPanel:
    """告警记录区：实时 alert 事件 + 近 7 天历史。"""

    def __init__(self, window, frame: tk.Frame):
        self._window = window
        self._body = ttk.Frame(frame)
        self._body.pack(fill=tk.BOTH, expand=True)

        bar = ttk.Frame(self._body)
        bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        ttk.Button(bar, text="刷新", command=self.reload).pack(side=tk.LEFT)
        ttk.Label(bar, text="仅展示失败告警摘要（不含验证码/短信正文）",
                  foreground="#666").pack(side=tk.RIGHT)

        columns = ("time", "targets", "reason")
        self._tree = ttk.Treeview(self._body, columns=columns, show="headings",
                                  height=14)
        titles = {"time": "时间", "targets": "目标", "reason": "原因摘要"}
        widths = {"time": 160, "targets": 220, "reason": 400}
        for col in columns:
            self._tree.heading(col, text=titles[col])
            self._tree.column(col, width=widths[col], anchor="w")
        scroll = ttk.Scrollbar(self._body, orient=tk.VERTICAL,
                               command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        window.on("alert", self._on_alert_event)
        window.on("child_ready", lambda _p: self.reload())
        self.reload()

    # ---------- 数据 ----------
    def reload(self) -> None:
        self._window.request("get_logs", {"level": "ERROR"}, self._on_loaded)

    def _on_loaded(self, resp: dict) -> None:
        if not resp.get("ok"):
            return
        self._tree.delete(*self._tree.get_children())
        count = 0
        for row in resp.get("logs") or []:
            if row.get("code") != _ALERT_CODE:
                continue
            when = time.strftime("%Y-%m-%d %H:%M:%S",
                                 time.localtime(row.get("created_at") or 0))
            targets, reason = self._split_summary(row.get("message") or "")
            self._insert(when, targets, reason)
            count += 1
            if count >= _MAX_ROWS:
                break

    @staticmethod
    def _split_summary(summary: str) -> tuple[str, str]:
        """`投递失败告警：目标1, 目标2 — 原因` → (目标, 原因)。"""
        body = summary.removeprefix(_ALERT_PREFIX)
        if " — " in body:
            targets, reason = body.split(" — ", 1)
        else:
            targets, reason = "全部目标", body
        return targets, reason

    def _on_alert_event(self, payload: dict) -> None:
        """alert 事件实时插入（AlertManager：time/summary/targets）。"""
        when = payload.get("time") or time.strftime("%Y-%m-%d %H:%M:%S")
        targets = ", ".join(payload.get("targets") or []) or "全部目标"
        summary = redact_text(payload.get("summary") or "")
        _t, reason = self._split_summary(summary)
        self._insert(when, targets, reason or summary)

    def _insert(self, when: str, targets: str, reason: str) -> None:
        self._tree.insert("", tk.END, values=(when, targets, reason))
        children = self._tree.get_children()
        if len(children) > _MAX_ROWS:
            self._tree.delete(children[-1])


def build(window) -> AlertPanel:
    """面板工厂（main_window._build_panels 调用）。"""
    return AlertPanel(window, window.frames["alerts"])
