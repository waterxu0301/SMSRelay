# targets_panel.py — 投递目标区（tasks 9.3, Trace: D-FE-003）
#
# design §6.3 ②：目标列表（Treeview 平台图标/地址打码/启用开关/测试推送）
# + 新增/编辑对话框（钉钉 secret 不回显，保存直写 Keychain 9.7 仅存 hint）
# + 删除二次确认（delete_target 级联清理 secret_refs，Keychain 条目 GUI 删）。
# 密钥边界（约束 #5）：secret 明文不出现在任何控件文本/IPC 参数（仅 hint）。

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from desktop import secure_store

# 平台 → (显示名, 图标)
_PLATFORM_LABELS = {
    "feishu": ("飞书", "🪶"),
    "dingtalk": ("钉钉", "📌"),
    "wecom": ("企业微信", "💼"),
}
_PLATFORM_KEYS = list(_PLATFORM_LABELS)


def _platform_key(index: int) -> str:
    return _PLATFORM_KEYS[index] if 0 <= index < len(_PLATFORM_KEYS) else _PLATFORM_KEYS[0]


def _errmsg(resp: dict) -> str:
    err = resp.get("error") or {}
    return err.get("message") or "操作失败"


class TargetsPanel:
    """投递目标区：列表 + 增删改/启停/测试推送。"""

    def __init__(self, window, frame: tk.Frame):
        self._window = window
        self._targets: dict[str, dict] = {}  # tree iid → 序列化目标

        toolbar = ttk.Frame(frame)
        toolbar.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        for text, cmd in (("新增", self._on_add), ("编辑", self._on_edit),
                          ("删除", self._on_delete), ("启用/停用", self._on_toggle),
                          ("测试推送", self._on_test)):
            ttk.Button(toolbar, text=text, command=cmd).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(toolbar, text="刷新", command=self.reload).pack(side=tk.RIGHT)

        columns = ("platform", "name", "url", "secret", "enabled")
        self._tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        self._tree.heading("platform", text="平台")
        self._tree.heading("name", text="名称")
        self._tree.heading("url", text="地址（已打码）")
        self._tree.heading("secret", text="加签密钥")
        self._tree.heading("enabled", text="状态")
        self._tree.column("platform", width=110, anchor="center")
        self._tree.column("name", width=150)
        self._tree.column("url", width=320)
        self._tree.column("secret", width=100, anchor="center")
        self._tree.column("enabled", width=70, anchor="center")
        self._tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._tree.bind("<Double-1>", lambda _e: self._on_edit())

        ttk.Label(frame,
                  text="双击行可编辑；地址不明文回显，修改需重新输入完整地址；"
                       "钉钉加签 secret 仅存 Keychain，界面不回显。",
                  foreground="#888").pack(side=tk.TOP, anchor="w", pady=(4, 0))

        # 核心就绪后才拉得到数据（child_ready 前 list_targets 会 UNAVAILABLE）
        window.on("child_ready", lambda _p: self.reload())
        self.reload()

    # ---------- 数据 ----------
    def reload(self) -> None:
        self._window.request("list_targets", None, self._on_list)

    def _on_list(self, resp: dict) -> None:
        if not resp.get("ok"):
            return
        self._tree.delete(*self._tree.get_children())
        self._targets.clear()
        for t in resp.get("targets") or []:
            label, icon = _PLATFORM_LABELS.get(t["platform"], (t["platform"], "❔"))
            self._targets[t["id"]] = t
            self._tree.insert("", tk.END, iid=t["id"], values=(
                f"{icon} {label}",
                t["name"],
                t["url_masked"],
                "已配置" if t["has_secret"] else "—",
                "启用" if t["enabled"] else "停用",
            ))

    def _selected(self) -> dict | None:
        sel = self._tree.selection()
        return self._targets.get(sel[0]) if sel else None

    # ---------- 动作 ----------
    def _on_add(self) -> None:
        _TargetDialog(self._window, self, None)

    def _on_edit(self) -> None:
        target = self._selected()
        if target is None:
            messagebox.showinfo("编辑目标", "请先在列表中选择一个目标。",
                                parent=self._tree.winfo_toplevel())
            return
        _TargetDialog(self._window, self, target)

    def _on_delete(self) -> None:
        target = self._selected()
        if target is None:
            return
        if not messagebox.askyesno(
                "删除目标",
                f"确认删除目标「{target['name']}」？\n"
                "删除后级联清理引用记录，Keychain 中的密钥条目一并移除，不可恢复。",
                parent=self._tree.winfo_toplevel()):
            return

        def done(resp: dict) -> None:
            if resp.get("ok"):
                secure_store.delete_secret(f"target.{target['id']}")  # 幂等
                self._window.show_banner(f"已删除目标「{target['name']}」", 3000)
                self.reload()
            else:
                self._window.show_banner(_errmsg(resp), 6000)

        self._window.request("delete_target", {"id": target["id"]}, done)

    def _on_toggle(self) -> None:
        target = self._selected()
        if target is None:
            return

        def done(resp: dict) -> None:
            if resp.get("ok"):
                self.reload()
            else:
                self._window.show_banner(_errmsg(resp), 6000)

        self._window.request("set_target_enabled",
                             {"id": target["id"], "enabled": not target["enabled"]}, done)

    def _on_test(self) -> None:
        target = self._selected()
        if target is None:
            return
        if not target["enabled"]:
            messagebox.showinfo("测试推送", "目标已停用，无法测试。",
                                parent=self._tree.winfo_toplevel())
            return

        def done(resp: dict) -> None:
            if not resp.get("ok"):
                self._window.show_banner(_errmsg(resp), 6000)
                return
            # dry-run 零外发：展示渲染预览，用户可再确认真实发送
            _PreviewDialog(self._window, target, resp.get("content", ""),
                           on_real_send=self._real_send)

        self._window.request("test_push",
                             {"target_id": target["id"], "dry_run": True}, done)

    def _real_send(self, target: dict) -> None:
        if not messagebox.askyesno(
                "真实测试推送",
                f"将向「{target['name']}」真实发送一条测试消息（占位码 000000），"
                "不计入统计与告警。是否继续？",
                parent=self._tree.winfo_toplevel()):
            return

        def done(resp: dict) -> None:
            # 真实测试响应：_ok(rid, dry_run=False, ok=result.ok, reason=...)——
            # kwargs 中的 ok 覆盖外层，故 resp["ok"] 即投递结果本身
            if resp.get("ok"):
                self._window.show_banner(f"测试推送成功：{target['name']}", 4000)
            elif "reason" in resp and "error" not in resp:
                self._window.show_banner(
                    f"测试推送失败：{resp.get('reason') or '未知原因'}", 8000)
            else:
                self._window.show_banner(_errmsg(resp), 6000)

        self._window.request("test_push",
                             {"target_id": target["id"], "dry_run": False}, done)


class _TargetDialog:
    """新增/编辑对话框：name/platform/url + 钉钉 secret（show='*' 直写 Keychain）。

    地址不明文回显（约束 #5）：编辑态 url 展示打码值占位，修改需重输完整地址
    （核心 update 校验要求合法 url 明文，留空不改在接口层不可表达）。
    """

    def __init__(self, window, panel: TargetsPanel, target: dict | None):
        self._window = window
        self._panel = panel
        self._target = target  # None = 新增
        self._parent = panel._tree.winfo_toplevel()

        self._dlg = tk.Toplevel(self._parent)
        self._dlg.title("编辑目标" if target else "新增目标")
        self._dlg.transient(self._parent)
        self._dlg.grab_set()
        self._dlg.resizable(False, False)

        body = ttk.Frame(self._dlg, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        self._body = body
        self._errors: dict[str, ttk.Label] = {}

        # 名称
        ttk.Label(body, text="名称:").grid(row=0, column=0, sticky="w", pady=(2, 0))
        self._name = ttk.Entry(body, width=36)
        self._name.grid(row=0, column=1, sticky="ew", pady=(2, 0))
        self._add_error_label(1, "name")

        # 平台（新增可选；编辑锁定——平台创建后不可变）
        ttk.Label(body, text="平台:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self._platform = ttk.Combobox(
            body, state="readonly", width=33,
            values=[f"{icon} {label}" for label, icon in _PLATFORM_LABELS.values()])
        self._platform.grid(row=2, column=1, sticky="ew", pady=(6, 0))
        self._add_error_label(3, "platform")

        # 地址（编辑态不明文回显——打码值占位，修改需重输）
        ttk.Label(body, text="Webhook 地址:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self._url = ttk.Entry(body, width=36)
        self._url.grid(row=4, column=1, sticky="ew", pady=(6, 0))
        self._add_error_label(5, "url")

        # 钉钉加签 secret（不回显，直写 Keychain；仅编辑/新增时可填）
        ttk.Label(body, text="加签 secret:").grid(row=6, column=0, sticky="w", pady=(6, 0))
        self._secret = ttk.Entry(body, width=36, show="*")
        self._secret.grid(row=6, column=1, sticky="ew", pady=(6, 0))
        self._secret_note = ttk.Label(body, text="仅钉钉需要；留空保持不变",
                                      foreground="#888")
        self._secret_note.grid(row=7, column=1, sticky="w")

        # 按钮
        btns = ttk.Frame(body)
        btns.grid(row=8, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="取消", command=self._dlg.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btns, text="保存", command=self._on_save).pack(side=tk.RIGHT)

        body.columnconfigure(1, weight=1)
        self._prefill()
        self._name.focus_set()

    def _add_error_label(self, row: int, name: str) -> None:
        lbl = ttk.Label(self._body, text="", foreground="red")
        lbl.grid(row=row, column=0, columnspan=2, sticky="w")
        self._errors[name] = lbl

    def _prefill(self) -> None:
        t = self._target
        if not t:
            self._platform.current(0)
            return
        self._name.insert(0, t["name"])
        self._platform.current(_PLATFORM_KEYS.index(t["platform"])
                               if t["platform"] in _PLATFORM_KEYS else 0)
        self._platform.config(state="disabled")  # 平台创建后锁定
        # 地址不明文回显（约束 #5）：打码值占位提示，聚焦后清空供重输
        self._url_hint = f"当前：{t['url_masked']}（不明文回显，修改请重输完整地址）"
        self._url.insert(0, self._url_hint)
        self._url.config(foreground="gray")
        self._url.bind("<FocusIn>", self._on_url_focus_in)
        self._url.bind("<FocusOut>", self._on_url_focus_out)
        self._secret_note.config(
            text=("已配置，" if t["has_secret"] else "未配置，")
                 + "仅钉钉需要；留空保持不变")

    def _on_url_focus_in(self, _e) -> None:
        if self._url.get() == self._url_hint:
            self._url.delete(0, tk.END)
            self._url.config(foreground="black")

    def _on_url_focus_out(self, _e) -> None:
        if not self._url.get().strip():
            self._url.insert(0, self._url_hint)
            self._url.config(foreground="gray")

    def _url_value(self) -> str:
        val = self._url.get().strip()
        if self._target and val == self._url_hint:
            return ""  # 未修改
        return val

    # ---------- 保存 ----------
    def _on_save(self) -> None:
        for lbl in self._errors.values():
            lbl.config(text="")
        name = self._name.get().strip()
        platform = _platform_key(self._platform.current())
        url = self._url_value()
        secret = self._secret.get()

        fields = []
        if not name:
            fields.append("name")
        if not url:
            fields.append("url")  # 新增必填；编辑态修改需重输（接口校验要求明文）
        if fields:
            self._mark_fields(fields)
            return

        if self._target:
            self._save_edit(name, url, secret)
        else:
            self._save_new(name, platform, url, secret)

    def _mark_fields(self, fields: list[str]) -> None:
        texts = {"name": "名称不能为空", "platform": "请选择平台",
                 "url": "请填写完整的 webhook 地址（修改需重新输入）"}
        for f in fields:
            if f in self._errors:
                self._errors[f].config(text=texts.get(f, "填写有误"))

    def _save_new(self, name: str, platform: str, url: str, secret: str) -> None:
        """新增：先创建拿 id → secret 直写 Keychain → 回传 hint（仅尾4位）。"""

        def after_create(resp: dict) -> None:
            if not resp.get("ok"):
                self._handle_error(resp)
                return
            target = resp.get("target") or {}
            tid = target.get("id")
            if secret and platform == "dingtalk":
                if secure_store.set_secret(f"target.{tid}", secret):
                    # Keychain 写入成功 → 回传 hint（仅尾4位，非明文）
                    self._window.request(
                        "save_target",
                        {"id": tid, "name": name, "url": url,
                         "secret_hint": secure_store.hint(secret)},
                        lambda r: self._finish(target["name"], r))
                    return
                self._window.show_banner(
                    f"目标「{target['name']}」已创建，但 Keychain 写入失败，加签 secret 未保存。",
                    8000)
            self._finish(target["name"], resp)

        # secret 明文不入 IPC（约束 #5）：创建时不带 hint，成功后直写 Keychain
        self._window.request(
            "save_target",
            {"name": name, "platform": platform, "url": url, "enabled": True},
            after_create)

    def _save_edit(self, name: str, url: str, secret: str) -> None:
        """编辑：secret 变更先直写 Keychain，成功后 IPC 仅传 hint。"""
        t = self._target
        if secret and t["platform"] == "dingtalk":
            if not secure_store.set_secret(f"target.{t['id']}", secret):
                self._window.show_banner("Keychain 写入失败，secret 未保存。", 6000)
                return

        params: dict = {"id": t["id"], "name": name, "url": url}
        if secret and t["platform"] == "dingtalk":
            params["secret_hint"] = secure_store.hint(secret)
        self._window.request("save_target", params,
                             lambda r: self._finish(name, r))

    def _handle_error(self, resp: dict) -> None:
        err = resp.get("error") or {}
        if err.get("code") == "VALIDATION":
            self._mark_fields(err.get("fields") or ["name"])
        self._window.show_banner(_errmsg(resp), 6000)

    def _finish(self, name: str, resp: dict) -> None:
        if resp.get("ok"):
            self._window.show_banner(f"已保存目标「{name}」", 3000)
            self._dlg.destroy()
            self._panel.reload()
        else:
            self._handle_error(resp)


class _PreviewDialog:
    """dry-run 预览：渲染内容展示 + 可选真实发送（零外发默认）。"""

    def __init__(self, window, target: dict, content: str, on_real_send):
        dlg = tk.Toplevel(window._root)
        dlg.title(f"投递预览 — {target['name']}")
        dlg.transient(window._root)
        dlg.grab_set()
        dlg.geometry("520x340")

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        label, _icon = _PLATFORM_LABELS.get(target["platform"], (target["platform"], ""))
        ttk.Label(body, text=f"dry-run 预览（平台不会收到真实消息）："
                             f"{label} · {target['url_masked']}").pack(anchor="w")
        text = tk.Text(body, wrap="word", height=12)
        text.pack(fill=tk.BOTH, expand=True, pady=(6, 6))
        text.insert("1.0", content)
        text.config(state="disabled")

        btns = ttk.Frame(body)
        btns.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(btns, text="关闭", command=dlg.destroy).pack(side=tk.RIGHT)
        ttk.Button(btns, text="真实发送测试消息",
                   command=lambda: (dlg.destroy(), on_real_send(target))).pack(
            side=tk.RIGHT, padx=(0, 6))


def build(window) -> TargetsPanel:
    """面板工厂（main_window._build_pages 调用）。"""
    return TargetsPanel(window, window.frames["targets"])
