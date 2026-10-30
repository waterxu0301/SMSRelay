# email_sender.py — SMTP 邮件转发（tasks 4.7, Trace: D-BSV-007, D-EXT-004）
#
# spec email-forwarding：并行不阻塞 Webhook；正文仅验证码 + 来源标记 + 时间，
# 不含短信正文全文；凭据运行期自 Keychain 读，失败 → CRED_LOST 不中断监听。
# 端口-加密：465=SMTP_SSL / 587=STARTTLS / 25=不加密。

from __future__ import annotations

import smtplib
import time
from email.mime.text import MIMEText
from typing import Callable

from relaycore.model import PushResult

SUBJECT = "SMS 接力验证码转发"
TIMEOUT_SECONDS = 10


class EmailSender:
    channel = "email"

    def __init__(self, secret_provider: Callable[[str], str | None]):
        """secret_provider('email.authcode') -> 授权码或 None（D5 Keychain 只读）。"""
        self._secret_provider = secret_provider

    def send(self, config: dict, code: str, meta: dict | None = None) -> PushResult:
        """config 来自 EmailConfigRepo.get()；meta 可含 sender_label/time。"""
        recipients: list[str] = config.get("recipients") or []
        host = config.get("smtp_host")
        port = config.get("smtp_port") or 465
        security = config.get("security") or "ssl"
        sender = config.get("sender")
        authcode = self._secret_provider("email.authcode")

        if not recipients or not host or not sender:
            return PushResult(ok=False, retryable=False, reason="EMAIL_NOT_CONFIGURED")
        if not authcode:
            # spec 场景：凭据丢失 → 提示重新录入，不中断监听
            return PushResult(ok=False, retryable=False, reason="CRED_LOST")

        when = (meta or {}).get("time") or time.strftime("%Y-%m-%d %H:%M:%S")
        label = (meta or {}).get("sender_label", "")
        # 正文边界：仅验证码 + 来源标记 + 时间，禁止短信正文全文
        body = f"验证码：{code}\n来源：{label or '短信'}\n时间：{when}"
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = SUBJECT
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)

        try:
            if security == "ssl":
                server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_SECONDS)
            else:
                server = smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS)
                if security == "starttls":
                    server.starttls()
            try:
                try:
                    server.login(sender, authcode)
                except smtplib.SMTPServerDisconnected:
                    # QQ SMTP_SSL 认证失败时直接断开连接（不回 535），
                    # login 阶段意外断连按认证失败归类（实测 2026-10-29）
                    return PushResult(ok=False, retryable=False, reason="SMTP_AUTH_FAILED")
                server.sendmail(sender, recipients, msg.as_string())
            finally:
                # 连接已被服务端断开时（如认证失败后）quit() 会抛
                # SMTPServerDisconnected，掩盖原始异常（如 SMTPAuthenticationError）
                try:
                    server.quit()
                except smtplib.SMTPException:
                    pass
        except smtplib.SMTPAuthenticationError:
            return PushResult(ok=False, retryable=False, reason="SMTP_AUTH_FAILED")
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            return PushResult(ok=False, retryable=True, reason=f"SMTP 异常: {exc}")

        return PushResult(ok=True)
