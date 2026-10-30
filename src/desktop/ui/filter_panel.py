# filter_panel.py — 过滤与免打扰区（tasks 9.4②, Trace: D-FE-003）
#
# design §6.3 ④：发件方/关键词白名单编辑 + 静默时段（HH:MM 前端拦截，
# 跨午夜提示）→ save_filters + reload_config；VALIDATION.fields 行内红字。

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

_HHMM = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def _errmsg(resp: dict) -> str:
    err = resp.get("error") or {}
    return err.get("message") or "操作失败"


class FilterPanel:
    """过滤与免打扰区：间隔/白名单/静默时段编辑。"""

    def __init__(self, window, frame: tk.Frame):
        self._window = window
        self._body = ttk.Frame(frame)
        self._body.pack(fill=tk.BOTH, expand=True)
        self._errors: dict[str, ttk.Label] = {}

        # 轮询与有效期
        basic = ttk.LabelFrame(self._body, text="轮询", padding=8)
        basic.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        ttk.Label(basic, text="轮询间隔(秒):").grid(row=0, column=0, sticky="w", pady=2)
        self._interval = ttk.Entry(basic, width=8)
        self._interval.grid(row=0, column=1, sticky="w", padx=(6, 0), pady=2)
        self._add_error(basic, 1, "interval")
        ttk.Label(basic, text="验证码有效期(秒):").grid(row=2, column=0, sticky="w", pady=2)
        self._max_age = ttk.Entry(basic, width=8)
        self._max_age.grid(row=2, column=1, sticky="w", padx=(6, 0), pady=2)
        self._add_error(basic, 3, "max_age")

        # 白名单（filter_mode 固定 whitelist——spec filtering：仅白名单模式）
        white = ttk.LabelFrame(self._body, text="白名单（仅白名单模式，逗号或换行分隔）",
                               padding=8)
        white.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(0, 6))
        self._sender_filter = tk.BooleanVar()
        ttk.Checkbutton(white, text="启用发件方白名单",
                        variable=self._sender_filter).grid(
            row=0, column=0, sticky="w")
        self._add_error(white, 1, "sender_whitelist")
        ttk.Label(white, text="发件方:").grid(row=2, column=0, sticky="w")
        self._sender_whitelist = tk.Text(white, height=3)
        self._sender_whitelist.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        self._keyword_filter = tk.BooleanVar()
        ttk.Checkbutton(white, text="启用关键词白名单",
                        variable=self._keyword_filter).grid(
            row=4, column=0, sticky="w", pady=(6, 0))
        self._add_error(white, 5, "keyword_whitelist")
        ttk.Label(white, text="关键词:").grid(row=6, column=0, sticky="w")
        self._keyword_whitelist = tk.Text(white, height=3)
        self._keyword_whitelist.grid(row=7, column=0, sticky="ew", pady=(2, 0))
        white.columnconfigure(0, weight=1)

        # 静默时段
        silent = ttk.LabelFrame(self._body, text="免打扰时段（HH:MM，跨午夜即为夜间到次日凌晨）",
                                padding=8)
        silent.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        ttk.Label(silent, text="开始:").grid(row=0, column=0, sticky="w", pady=2)
        self._silent_start = ttk.Entry(silent, width=8)
        self._silent_start.grid(row=0, column=1, sticky="w", padx=(6, 12), pady=2)
        ttk.Label(silent, text="结束:").grid(row=0, column=2, sticky="w", pady=2)
        self._silent_end = ttk.Entry(silent, width=8)
        self._silent_end.grid(row=0, column=3, sticky="w", padx=(6, 0), pady=2)
        self._silent_hint = ttk.Label(silent, text="", foreground="orange")
        self._silent_hint.grid(row=1, column=0, columnspan=4, sticky="w")
        self._add_error(silent, 2, "silent_period")
        for entry in (self._silent_start, self._silent_end):
            entry.bind("<KeyRelease>", lambda _e: self._update_silent_hint())

        ttk.Button(self._body, text="保存过滤配置",
                   command=self._on_save).pack(side=tk.TOP, anchor="e", pady=(6, 0))

        window.on("child_ready", lambda _p: self.reload())
        self.reload()

    def _add_error(self, parent: tk.Widget, row: int, name: str) -> None:
        lbl = ttk.Label(parent, text="", foreground="red")
        lbl.grid(row=row, column=0, columnspan=4, sticky="w")
        self._errors[name] = lbl

    # ---------- 数据 ----------
    def reload(self) -> None:
        self._window.request("reload_config", None, self._on_loaded)

    def _on_loaded(self, resp: dict) -> None:
        if not resp.get("ok"):
            return
        config = resp.get("config") or {}
        self._set_entry(self._interval, config.get("interval", "3"))
        self._set_entry(self._max_age, config.get("max_age", "300"))
        self._sender_filter.set(config.get("sender_filter", "0") == "1")
        self._keyword_filter.set(config.get("keyword_filter", "0") == "1")
        self._set_text(self._sender_whitelist, config.get("sender_whitelist", ""))
        self._set_text(self._keyword_whitelist, config.get("keyword_whitelist", ""))
        self._set_entry(self._silent_start, config.get("silent_start", ""))
        self._set_entry(self._silent_end, config.get("silent_end", ""))
        self._update_silent_hint()

    @staticmethod
    def _set_entry(entry: ttk.Entry, value: str) -> None:
        entry.delete(0, tk.END)
        entry.insert(0, value or "")

    @staticmethod
    def _set_text(text: tk.Text, value: str) -> None:
        text.delete("1.0", tk.END)
        text.insert("1.0", value or "")

    @staticmethod
    def _list_value(text: tk.Text) -> str:
        raw = text.get("1.0", tk.END).replace(",", "\n")
        return ",".join(sorted({s.strip() for s in raw.splitlines() if s.strip()}))

    def _update_silent_hint(self) -> None:
        start = self._silent_start.get().strip()
        end = self._silent_end.get().strip()
        if _HHMM.match(start) and _HHMM.match(end) and end < start:
            self._silent_hint.config(text="跨午夜：静默将持续到次日 " + end)
        else:
            self._silent_hint.config(text="")

    # ---------- 保存 ----------
    def _on_save(self) -> None:
        for lbl in self._errors.values():
            lbl.config(text="")

        interval = self._interval.get().strip()
        max_age = self._max_age.get().strip()
        sender_wl = self._list_value(self._sender_whitelist)
        keyword_wl = self._list_value(self._keyword_whitelist)
        sender_on = "1" if self._sender_filter.get() else "0"
        keyword_on = "1" if self._keyword_filter.get() else "0"
        silent_start = self._silent_start.get().strip()
        silent_end = self._silent_end.get().strip()

        # 前端拦截（不发请求）：数值 + HH:MM + 成对/白名单非空
        fields = []
        try:
            if float(interval) < 1:
                raise ValueError
        except ValueError:
            fields.append("interval")
        try:
            if float(max_age) <= 0:
                raise ValueError
        except ValueError:
            fields.append("max_age")
        if (silent_start or silent_end) and not (
                _HHMM.match(silent_start) and _HHMM.match(silent_end)):
            fields.append("silent_period")
        if sender_on == "1" and not sender_wl:
            fields.append("sender_whitelist")
        if keyword_on == "1" and not keyword_wl:
            fields.append("keyword_whitelist")
        if fields:
            self._mark_fields(fields)
            return

        params = {
            "interval": interval, "max_age": max_age,
            "sender_filter": sender_on, "sender_whitelist": sender_wl,
            "keyword_filter": keyword_on, "keyword_whitelist": keyword_wl,
            "silent_start": silent_start, "silent_end": silent_end,
        }

        def done(resp: dict) -> None:
            if resp.get("ok"):
                self._window.show_banner("过滤配置已保存并热生效", 3000)
                self._sync_silent_display(silent_start, silent_end)
            else:
                err = resp.get("error") or {}
                if err.get("code") == "VALIDATION":
                    self._mark_fields(err.get("fields") or [])
                self._window.show_banner(_errmsg(resp), 6000)

        self._window.request("save_filters", params, done)

    def _sync_silent_display(self, start: str, end: str) -> None:
        """保存成功后同步状态栏免打扰显示（9.4 验收）。"""
        if start and end:
            note = "（跨午夜）" if end < start else ""
            self._window.set_silent_display(f"{start}-{end}{note}")
        else:
            self._window.set_silent_display("")

    def _mark_fields(self, fields: list[str]) -> None:
        texts = {"interval": "间隔需为 ≥1 的数字（秒）",
                 "max_age": "有效期需为 >0 的数字（秒）",
                 "silent_period": "静默时段需成对填写且格式为 HH:MM",
                 "sender_whitelist": "启用发件方白名单前请填写名单",
                 "keyword_whitelist": "启用关键词白名单前请填写名单"}
        for f in fields:
            if f in self._errors:
                self._errors[f].config(text=texts.get(f, "填写有误"))


def build(window) -> FilterPanel:
    """面板工厂（main_window._build_panels 调用）。"""
    return FilterPanel(window, window.frames["filters"])
