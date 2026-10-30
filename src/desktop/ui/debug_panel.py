# debug_panel.py — 调试动作区（tasks 9.6, Trace: D-FE-001 区域 8）
#
# design §6.3 ⑧：测试推送（dry-run 占位码零外发 + 真实测试）、
# 立即扫描一次（busy 置灰 3s 防抖，重复点击不产生第二次请求）、
# 手动重推（list_buffered 拉内存缓冲 → 选中 → repush，
# OUT_OF_WINDOW → 「已超出重推窗口」，缓冲空重推置灰）。
# 缓冲验证码仅界面内存展示，不写日志/文件（spec 敏感信息约束）。

from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

from desktop.ui.targets_panel import _PreviewDialog, _PLATFORM_LABELS, _errmsg

SCAN_COOLDOWN_MS = 3000  # busy 防抖窗口（tasks 9.6）


class DebugPanel:
    """调试动作区：测试推送 / 立即扫描 / 手动重推。"""

    def __init__(self, window, frame: tk.Frame):
        self._window = window
        self._targets: dict[str, dict] = {}
        self._buffered: dict[str, dict] = {}  # tree iid → 缓冲条目
        self._scan_pending = False

        body = ttk.Frame(frame)
        body.pack(fill=tk.BOTH, expand=True)

        # ① 测试推送
        push = ttk.LabelFrame(body, text="测试推送（占位码 000000）", padding=8)
        push.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        ttk.Label(push, text="目标:").grid(row=0, column=0, sticky="w")
        self._target_box = ttk.Combobox(push, state="readonly", width=32)
        self._target_box.grid(row=0, column=1, sticky="w", padx=(6, 0))
        ttk.Button(push, text="dry-run 预览",
                   command=self._on_dry_run).grid(row=0, column=2, padx=(10, 0))
        ttk.Button(push, text="真实测试投递",
                   command=self._on_real_test).grid(row=0, column=3, padx=(6, 0))
        ttk.Label(push, text="dry-run 零外发；真实测试不计入统计与告警。",
                  foreground="#888").grid(row=1, column=0, columnspan=4,
                                          sticky="w", pady=(4, 0))

        # ② 立即扫描
        scan = ttk.LabelFrame(body, text="立即扫描", padding=8)
        scan.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        self._scan_btn = ttk.Button(scan, text="立即扫描一次",
                                    command=self._on_scan)
        self._scan_btn.pack(side=tk.LEFT)
        self._scan_hint = ttk.Label(scan, text="", foreground="#666")
        self._scan_hint.pack(side=tk.LEFT, padx=(10, 0))

        # ③ 手动重推
        repush = ttk.LabelFrame(body, text="手动重推（内存缓冲最近 100 条，重启清空）",
                                padding=8)
        repush.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        bar = ttk.Frame(repush)
        bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        ttk.Button(bar, text="刷新缓冲", command=self.reload_buffer).pack(
            side=tk.LEFT)
        self._repush_btn = ttk.Button(bar, text="重推选中",
                                      command=self._on_repush, state="disabled")
        self._repush_btn.pack(side=tk.LEFT, padx=(6, 0))
        self._buf_hint = ttk.Label(bar, text="", foreground="#666")
        self._buf_hint.pack(side=tk.LEFT, padx=(10, 0))

        columns = ("ts", "sender", "code")
        self._tree = ttk.Treeview(repush, columns=columns, show="headings",
                                  selectmode="browse", height=7)
        for col, title, width in (("ts", "接收时间", 110),
                                  ("sender", "发送方", 240),
                                  ("code", "验证码", 100)):
            self._tree.heading(col, text=title)
            self._tree.column(col, width=width, anchor="w")
        scroll = ttk.Scrollbar(repush, orient=tk.VERTICAL,
                               command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        window.on("child_ready", lambda _p: (self.reload_targets(),
                                             self.reload_buffer()))
        self.reload_targets()
        self.reload_buffer()

    # ---------- 数据 ----------
    def reload_targets(self) -> None:
        self._window.request("list_targets", None, self._on_targets)

    def _on_targets(self, resp: dict) -> None:
        if not resp.get("ok"):
            return
        self._targets = {t["id"]: t for t in resp.get("targets") or []}
        labels = []
        for t in self._targets.values():
            label, icon = _PLATFORM_LABELS.get(t["platform"],
                                               (t["platform"], "❔"))
            labels.append(f"{icon} {t['name']}（{label}）")
        self._target_box["values"] = labels
        if labels and self._target_box.current() < 0:
            self._target_box.current(0)

    def reload_buffer(self) -> None:
        self._window.request("list_buffered", None, self._on_buffer)

    def _on_buffer(self, resp: dict) -> None:
        self._tree.delete(*self._tree.get_children())
        self._buffered.clear()
        if not resp.get("ok"):
            self._buf_hint.config(text=_errmsg(resp))
            self._repush_btn.config(state="disabled")
            return
        entries = resp.get("buffered") or []
        for e in entries:
            iid = str(e["rowid"])
            self._buffered[iid] = e
            self._tree.insert("", tk.END, iid=iid, values=(
                time.strftime("%H:%M:%S", time.localtime(e.get("ts") or 0)),
                e.get("sender_label") or "-",
                e.get("code") or "-",
            ))
        # 缓冲空 → 重推按钮置灰（9.6 验收）
        self._repush_btn.config(
            state="normal" if self._tree.get_children() else "disabled")
        self._buf_hint.config(
            text=f"缓冲 {len(entries)} 条" if entries else "缓冲为空，不可重推")

    # ---------- 动作 ----------
    def _selected_target(self) -> dict | None:
        idx = self._target_box.current()
        ids = list(self._targets)
        return self._targets[ids[idx]] if 0 <= idx < len(ids) else None

    def _on_dry_run(self) -> None:
        target = self._selected_target()
        if target is None:
            messagebox.showinfo("测试推送", "请先配置投递目标。",
                                parent=self._tree.winfo_toplevel())
            return

        def done(resp: dict) -> None:
            if not resp.get("ok"):
                self._window.show_banner(_errmsg(resp), 6000)
                return
            _PreviewDialog(self._window, target, resp.get("content", ""),
                           on_real_send=self._real_send)

        self._window.request("test_push",
                             {"target_id": target["id"], "dry_run": True}, done)

    def _on_real_test(self) -> None:
        target = self._selected_target()
        if target is None:
            return
        self._real_send(target)

    def _real_send(self, target: dict) -> None:
        if not messagebox.askyesno(
                "真实测试推送",
                f"将向「{target['name']}」真实发送一条测试消息（占位码 000000），"
                "不计入统计与告警。是否继续？",
                parent=self._tree.winfo_toplevel()):
            return

        def done(resp: dict) -> None:
            # _ok kwargs 中 ok 覆盖外层：resp["ok"] 即投递结果本身
            if resp.get("ok"):
                self._window.show_banner(f"测试推送成功：{target['name']}", 4000)
            elif "reason" in resp and "error" not in resp:
                self._window.show_banner(
                    f"测试推送失败：{resp.get('reason') or '未知原因'}", 8000)
            else:
                self._window.show_banner(_errmsg(resp), 6000)

        self._window.request("test_push",
                             {"target_id": target["id"], "dry_run": False}, done)

    def _on_scan(self) -> None:
        # pending 标志：请求在途时重复点击不产生第二次扫描请求（9.6 验收）
        if self._scan_pending:
            return
        self._scan_pending = True
        self._scan_btn.config(state="disabled")

        def done(resp: dict) -> None:
            self._scan_pending = False
            if not resp.get("ok"):
                self._scan_btn.config(state="normal")
                self._scan_hint.config(text="")
                self._window.show_banner(_errmsg(resp), 6000)
                return
            if resp.get("busy"):
                # 忙碌：保持置灰 3s 防抖
                self._scan_hint.config(text="扫描进行中，稍后再试")
                self._window._root.after(
                    SCAN_COOLDOWN_MS,
                    lambda: (self._scan_btn.config(state="normal"),
                             self._scan_hint.config(text="")))
                return
            self._scan_btn.config(state="normal")
            self._scan_hint.config(
                text=f"扫描完成，处理 {resp.get('processed', 0)} 条新消息")

        self._window.request("scan_now", None, done)

    def _on_repush(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        entry = self._buffered.get(sel[0])
        if entry is None:
            return
        if not messagebox.askyesno(
                "手动重推",
                f"重新投递 {time.strftime('%H:%M:%S', time.localtime(entry['ts']))}"
                f" 收到的验证码（{entry.get('sender_label') or '未知发送方'}）？",
                parent=self._tree.winfo_toplevel()):
            return

        def done(resp: dict) -> None:
            err = resp.get("error") or {}
            if err.get("code") == "OUT_OF_WINDOW":
                self._window.show_banner("已超出重推窗口", 6000)
                self.reload_buffer()
                return
            if not resp.get("ok"):
                self._window.show_banner(_errmsg(resp), 6000)
                return
            results = resp.get("results") or {}
            failed = [r.get("reason") or "未知原因"
                      for r in results.values() if not r.get("ok")]
            if failed:
                self._window.show_banner(
                    f"重推完成，{len(failed)} 个目标失败：{failed[0]}", 8000)
            else:
                self._window.show_banner(
                    f"重推成功（{len(results)} 个目标）", 4000)
            self.reload_buffer()

        self._window.request("repush", {"rowid": entry["rowid"]}, done)


def build(window) -> DebugPanel:
    """面板工厂（main_window._build_panels 调用）。"""
    return DebugPanel(window, window.frames["debug"])
