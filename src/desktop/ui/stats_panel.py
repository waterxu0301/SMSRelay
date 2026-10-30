# stats_panel.py — 投递统计区（tasks 9.5①, Trace: D-FE-001 区域 5）
#
# design §6.3 ⑤：7 天成功率（消息 × 目标、各目标成败分布）；
# rate < 0.995 数值标红 + 「低于 99.5%」提示（spec multi-channel-push）。

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def _fmt_rate(rate) -> str:
    return f"{float(rate) * 100:.2f}%"


class StatsPanel:
    """投递统计区：7 天成功率列表 + 整体汇总。"""

    def __init__(self, window, frame: tk.Frame):
        self._window = window
        self._body = ttk.Frame(frame)
        self._body.pack(fill=tk.BOTH, expand=True)
        self._names: dict[str, str] = {}
        self._stats: list[dict] = []

        bar = ttk.Frame(self._body)
        bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        ttk.Button(bar, text="刷新", command=self.reload).pack(side=tk.LEFT)
        self._hint = ttk.Label(bar, text="", foreground="red")
        self._hint.pack(side=tk.LEFT, padx=(10, 0))

        columns = ("target", "total", "ok", "fail", "rate", "status")
        self._tree = ttk.Treeview(self._body, columns=columns, show="headings",
                                  height=12)
        titles = {"target": "目标", "total": "投递数", "ok": "成功",
                  "fail": "失败", "rate": "7 天成功率", "status": "状态"}
        widths = {"target": 220, "total": 80, "ok": 80, "fail": 80,
                  "rate": 110, "status": 130}
        for col in columns:
            self._tree.heading(col, text=titles[col])
            self._tree.column(col, width=widths[col], anchor="w")
        scroll = ttk.Scrollbar(self._body, orient=tk.VERTICAL,
                               command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        # rate < 0.995 标红（9.5 验收：注入 0.98 数据标红出现）
        self._tree.tag_configure("low", foreground="red")

        self._summary = ttk.Label(self._body, text="")
        self._summary.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))

        window.on("child_ready", lambda _p: self.reload())
        self.reload()

    # ---------- 数据 ----------
    def reload(self) -> None:
        # 先取目标名映射，再取统计（两个请求独立异步）
        self._window.request("list_targets", None, self._on_targets)
        self._window.request("get_stats", {"days": 7}, self._on_stats)

    def _on_targets(self, resp: dict) -> None:
        if resp.get("ok"):
            self._names = {t["id"]: t["name"]
                           for t in resp.get("targets") or []}
            if self._stats:
                self._render()

    def _on_stats(self, resp: dict) -> None:
        if not resp.get("ok"):
            return
        self._stats = resp.get("stats") or []
        self._render()

    def _render(self) -> None:
        self._tree.delete(*self._tree.get_children())
        low_count = 0
        total_all = ok_all = 0
        for row in self._stats:
            total = int(row.get("total") or 0)
            ok = int(row.get("ok") or 0)
            rate = float(row.get("rate") or 1.0)
            low = bool(row.get("degraded")) or rate < 0.995
            if low:
                low_count += 1
            total_all += total
            ok_all += ok
            name = self._names.get(row.get("target_id", ""),
                                   row.get("target_id", "?"))
            self._tree.insert("", tk.END, tags=("low",) if low else (),
                              values=(name, total, ok, total - ok,
                                      _fmt_rate(rate),
                                      "低于 99.5%" if low else "正常"))

        if total_all:
            overall = ok_all / total_all
            self._summary.config(
                text=f"整体（消息 × 目标）：{total_all} 条，成功 {ok_all}，"
                     f"成功率 {_fmt_rate(overall)}")
        else:
            self._summary.config(text="近 7 天暂无投递记录")
        self._hint.config(
            text="有目标成功率低于 99.5%，请检查投递配置"
            if low_count else "")


def build(window) -> StatsPanel:
    """面板工厂（main_window._build_panels 调用）。"""
    return StatsPanel(window, window.frames["stats"])
