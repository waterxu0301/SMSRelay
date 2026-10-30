# email_panel.py — 邮件通道区（tasks 9.4①, Trace: D-FE-003）
#
# design §6.3 ③：配置摘要（host/port/sender/recipients）+ 启用开关
# （set_email_enabled）+ 编辑对话框；授权码输入框 show='*' 永不回显，
# 保存直写 Keychain `email.authcode`（9.7），回显掩码 ••••1234。
# 密钥边界（约束 #5）：授权码不经 IPC（save_email 不接受该参数）。

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from desktop import secure_store

_SECURITY_LABELS = (("ssl", "SSL"), ("starttls", "STARTTLS"), ("none", "不加密（明文）"))
_AUTHCODE_ACCOUNT = "email.authcode"


def _errmsg(resp: dict) -> str:
    err = resp.get("error") or {}
    return err.get("message") or "操作失败"


class EmailPanel:
    """邮件通道区：摘要展示 + 启停 + 编辑对话框。"""

    def __init__(self, window, frame: tk.Frame):
        self._window = window
        self._email: dict = {}

        # 摘要区
        summary = ttk.LabelFrame(frame, text="当前配置", padding=10)
        summary.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
        self._labels: dict[str, ttk.Label] = {}
        for row, (key, title) in enumerate((
                ("enabled", "状态"), ("host", "SMTP 服务器"),
                ("sender", "发件人"), ("recipients", "收件人"),
                ("secret", "授权码"))):
            ttk.Label(summary, text=f"{title}:").grid(
                row=row, column=0, sticky="w", pady=2)
            lbl = ttk.Label(summary, text="—", foreground="#333")
            lbl.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)
            self._labels[key] = lbl
        summary.columnconfigure(1, weight=1)

        toolbar = ttk.Frame(frame)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(toolbar, text="编辑配置", command=self._on_edit).pack(
            side=tk.LEFT, padx=(0, 4))
        self._toggle_btn = ttk.Button(toolbar, text="启用/停用",
                                      command=self._on_toggle)
        self._toggle_btn.pack(side=tk.LEFT)

        window.on("child_ready", lambda _p: self.reload())
        self.reload()

    # ---------- 数据 ----------
    def reload(self) -> None:
        self._window.request("get_email", None, self._on_loaded)

    def _on_loaded(self, resp: dict) -> None:
        if not resp.get("ok"):
            return
        self._email = resp.get("email") or {}
        e = self._email
        self._labels["enabled"].config(
            text="已启用" if e.get("enabled") else "未启用",
            foreground="green" if e.get("enabled") else "red")
        port = e.get("smtp_port") or "-"
        security = e.get("security") or "none"
        host = e.get("smtp_host") or "-"
        self._labels["host"].config(
            text=f"{host}:{port}"
                 + ("" if security == "none" else f"（{security}）"))
        self._labels["sender"].config(text=e.get("sender") or "-")
        recipients = e.get("recipients") or []
        self._labels["recipients"].config(
            text=", ".join(recipients) if recipients else "-")
        # 授权码仅展示掩码 hint（Keychain 读取，明文永不回显）
        hint = secure_store.hint_of(_AUTHCODE_ACCOUNT) if e.get("smtp_host") else None
        self._labels["secret"].config(text=hint or "未配置")

    # ---------- 动作 ----------
    def _on_toggle(self) -> None:
        if not self._email.get("smtp_host"):
            messagebox.showinfo("邮件通道", "请先编辑保存 SMTP 配置。",
                                parent=self._window._root)
            return
        new_state = not bool(self._email.get("enabled"))

        def done(resp: dict) -> None:
            if resp.get("ok"):
                self._email = resp.get("email") or {}
                self._on_loaded({"ok": True, "email": self._email})
            else:
                self._window.show_banner(_errmsg(resp), 6000)

        self._window.request("set_email_enabled", {"enabled": new_state}, done)

    def _on_edit(self) -> None:
        _EmailDialog(self._window, self, self._email)


class _EmailDialog:
    """邮件编辑对话框：host/port/security/sender/recipients + 授权码（不回显）。"""

    def __init__(self, window, panel: EmailPanel, email: dict):
        self._window = window
        self._panel = panel

        dlg = tk.Toplevel(window._root)
        self._dlg = dlg
        dlg.title("邮件通道配置")
        dlg.transient(window._root)
        dlg.grab_set()
        dlg.resizable(False, False)

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        self._body = body
        self._errors: dict[str, ttk.Label] = {}

        row = 0
        ttk.Label(body, text="SMTP 服务器:").grid(row=row, column=0, sticky="w", pady=(2, 0))
        self._host = ttk.Entry(body, width=36)
        self._host.grid(row=row, column=1, sticky="ew", pady=(2, 0))
        row = self._next(row + 1, "smtp_host", "smtp_host")

        ttk.Label(body, text="端口:").grid(row=row, column=0, sticky="w", pady=(2, 0))
        self._port = ttk.Entry(body, width=10)
        self._port.grid(row=row, column=1, sticky="w", pady=(2, 0))
        row = self._next(row + 1, "smtp_port", "smtp_port")

        ttk.Label(body, text="加密:").grid(row=row, column=0, sticky="w", pady=(2, 0))
        self._security = ttk.Combobox(
            body, state="readonly", width=33,
            values=[label for _key, label in _SECURITY_LABELS])
        self._security.grid(row=row, column=1, sticky="ew", pady=(2, 0))
        row = self._next(row + 1, "security", "security")

        ttk.Label(body, text="发件人:").grid(row=row, column=0, sticky="w", pady=(2, 0))
        self._sender = ttk.Entry(body, width=36)
        self._sender.grid(row=row, column=1, sticky="ew", pady=(2, 0))
        row = self._next(row + 1, "sender", "sender")

        ttk.Label(body, text="收件人:").grid(row=row, column=0, sticky="nw", pady=(2, 0))
        self._recipients = tk.Text(body, width=36, height=3)
        self._recipients.grid(row=row, column=1, sticky="ew", pady=(2, 0))
        ttk.Label(body, text="多个地址用逗号或换行分隔", foreground="#888").grid(
            row=row + 1, column=1, sticky="w")
        row = self._next(row + 2, "recipients", "recipients")

        ttk.Label(body, text="授权码:").grid(row=row, column=0, sticky="w", pady=(2, 0))
        self._authcode = ttk.Entry(body, width=36, show="*")
        self._authcode.grid(row=row, column=1, sticky="ew", pady=(2, 0))
        hint = secure_store.hint_of(_AUTHCODE_ACCOUNT)
        ttk.Label(body,
                  text=("已配置 " + hint if hint else "未配置")
                       + "；不回显明文，留空保持不变，保存时直接写入钥匙串",
                  foreground="#888").grid(row=row + 1, column=1, sticky="w")

        btns = ttk.Frame(body)
        btns.grid(row=row + 2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="取消", command=dlg.destroy).pack(
            side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btns, text="保存", command=self._on_save).pack(side=tk.RIGHT)

        body.columnconfigure(1, weight=1)
        self._prefill(email)
        self._host.focus_set()

    def _next(self, row: int, name: str, field: str) -> int:
        lbl = ttk.Label(self._body, text="", foreground="red")
        lbl.grid(row=row, column=0, columnspan=2, sticky="w")
        self._errors[name] = lbl
        return row + 1

    def _prefill(self, email: dict) -> None:
        if email.get("smtp_host"):
            self._host.insert(0, email["smtp_host"])
        self._port.insert(0, str(email.get("smtp_port") or 465))
        security = email.get("security") or "ssl"
        keys = [k for k, _ in _SECURITY_LABELS]
        self._security.current(keys.index(security) if security in keys else 0)
        if email.get("sender"):
            self._sender.insert(0, email["sender"])
        recipients = email.get("recipients") or []
        if recipients:
            self._recipients.insert("1.0", "\n".join(recipients))

    # ---------- 保存 ----------
    def _on_save(self) -> None:
        for lbl in self._errors.values():
            lbl.config(text="")
        host = self._host.get().strip()
        port_text = self._port.get().strip()
        security = _SECURITY_LABELS[self._security.current()][0]
        sender = self._sender.get().strip()
        recipients_raw = self._recipients.get("1.0", tk.END)
        authcode = self._authcode.get()
        enabled = bool(self._panel._email.get("enabled"))

        fields = []
        try:
            port = int(port_text or "465")
            if port <= 0 or port > 65535:
                raise ValueError
        except ValueError:
            port = 0
            fields.append("smtp_port")
        recipients = [r.strip() for r in
                      recipients_raw.replace(",", "\n").splitlines() if r.strip()]
        if enabled and not host:
            fields.append("smtp_host")
        if enabled and not sender:
            fields.append("sender")
        if enabled and not recipients:
            fields.append("recipients")
        if fields:
            self._mark_fields(fields)
            return

        # 授权码先直写 Keychain（留空保持不变）；明文不经 IPC（约束 #5）
        if authcode:
            if not secure_store.set_secret(_AUTHCODE_ACCOUNT, authcode):
                self._window.show_banner("钥匙串写入失败，授权码未保存。", 6000)
                return

        params = {"smtp_host": host or None, "smtp_port": port,
                  "security": security, "sender": sender or None,
                  "recipients": recipients, "enabled": enabled}

        def done(resp: dict) -> None:
            if resp.get("ok"):
                self._window.show_banner("邮件配置已保存", 3000)
                self._dlg.destroy()
                self._panel._on_loaded(resp)
            else:
                err = resp.get("error") or {}
                if err.get("code") == "VALIDATION":
                    self._mark_fields(err.get("fields") or [])
                self._window.show_banner(_errmsg(resp), 6000)

        self._window.request("save_email", params, done)

    def _mark_fields(self, fields: list[str]) -> None:
        texts = {"smtp_host": "请填写 SMTP 服务器", "smtp_port": "端口需为 1-65535 的整数",
                 "security": "请选择加密方式", "sender": "请填写发件人",
                 "recipients": "请填写至少一个收件人"}
        for f in fields:
            if f in self._errors:
                self._errors[f].config(text=texts.get(f, "填写有误"))


def build(window) -> EmailPanel:
    """面板工厂（main_window._build_panels 调用）。"""
    return EmailPanel(window, window.frames["email"])
