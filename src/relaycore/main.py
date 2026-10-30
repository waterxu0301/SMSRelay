# main.py — 监听核心子进程入口（tasks 5.1 组装, Trace: D-PSV-001）
#
# design §6.2 五层组装：ensure_schema → app.db 长连接（单写者）→ 各 Repo →
# ChatDbRepository（chat.db 只读）→ Keychain 只读 → 网关 → 邮件 → 三重告警 →
# 分发器 → 过滤链 → 轮询 → 保留清理 → AppContext → IpcServer.serve_forever()。
# GUI 以 [sys.executable, "-u", "-m", "relaycore.main"] 拉起（cwd=sms-relay/），
# stdin/stdout 承载 JSON Lines；EOF（GUI 退出）→ 清理资源后退出。

from __future__ import annotations

import smtplib
import sqlite3
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path

from relaycore.dao.chat_db import ChatDbRepository
from relaycore.dao.migrate import ensure_schema
from relaycore.dao.state import (ConfigRepo, EmailConfigRepo, LogRepo,
                                 StatsRepo, TargetsRepo, WatermarkRepo)
from relaycore.gateway.dingtalk import DingtalkGateway
from relaycore.gateway.email_sender import EmailSender
from relaycore.gateway.feishu import FeishuGateway
from relaycore.gateway.wecom import WeComGateway
from relaycore.ipc.events import EventEmitter
from relaycore.ipc.server import AppContext, IpcServer
from relaycore.poll_loop import PollLoop
from relaycore.retention import RetentionWorker
from relaycore.service.alert import AlertManager
from relaycore.service.dispatch import DeliveryDispatcher
from relaycore.service.filter_chain import FilterChain
from relaycore.service.keychain import KeychainStore

APP_DB_PATH = Path.home() / "Library" / "Application Support" / "SMSRelay" / "app.db"
CHAT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"
ALERT_SUBJECT = "SMS 接力投递失败告警"
SMTP_TIMEOUT_SECONDS = 10


def _alert_email_fn(email_config: EmailConfigRepo, keychain: KeychainStore):
    """告警邮件发送器（D10 ③）：复用邮件通道 SMTP 配置，正文仅时间/目标/原因摘要。

    未配置 / 未启用 / 授权码丢失 → 返回 False（不抛：界面 + 通知仍生效，design D10）。
    """

    def send(summary: str) -> bool:
        cfg = email_config.get()
        host = cfg.get("smtp_host")
        sender = cfg.get("sender")
        recipients = cfg.get("recipients") or []
        if not cfg.get("enabled") or not host or not sender or not recipients:
            return False
        authcode = keychain.get_secret("email.authcode")
        if not authcode:
            return False

        port = cfg.get("smtp_port") or 465
        security = cfg.get("security") or "ssl"
        body = f"{summary}\n时间：{time.strftime('%Y-%m-%d %H:%M:%S')}"
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = ALERT_SUBJECT
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)

        try:
            if security == "ssl":
                server = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT_SECONDS)
            else:
                server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS)
                if security == "starttls":
                    server.starttls()
            try:
                server.login(sender, authcode)
                server.sendmail(sender, recipients, msg.as_string())
            finally:
                server.quit()
        except Exception:  # 告警邮件失败不得影响主流程（D10：界面/通知兜底）
            return False
        return True

    return send


def main() -> int:
    # ① schema + app.db 单写者长连接（WAL 由 ensure_schema 设置为库级属性）
    ensure_schema(APP_DB_PATH)
    conn = sqlite3.connect(str(APP_DB_PATH), check_same_thread=False,
                           timeout=30.0)
    conn.execute("PRAGMA foreign_keys=ON")

    # ② DAO-state 各仓库共享同一连接（design §6.2②）
    watermark = WatermarkRepo(conn)
    config = ConfigRepo(conn)
    targets = TargetsRepo(conn)
    email_config = EmailConfigRepo(conn)
    logs = LogRepo(conn)
    stats = StatsRepo(conn)

    # ③ DAO-chatdb + 事件 + 凭据（核心侧 Keychain 只读，D5）
    chat_db = ChatDbRepository(CHAT_DB_PATH)
    events = EventEmitter()
    keychain = KeychainStore()

    # ④ 网关与邮件通道（secret_provider 运行期读 Keychain，明文不落盘）
    gateways = {
        "feishu": FeishuGateway(),
        "dingtalk": DingtalkGateway(keychain.get_secret),
        "wecom": WeComGateway(),
    }
    email_sender = EmailSender(keychain.get_secret)

    # ⑤ 三重告警（D10）：IPC 事件 + 邮件；系统通知由 GUI 侧消费 alert 事件后发起
    alert = AlertManager(
        logs,
        emit_event=lambda payload: events.emit(payload.get("type", "alert"), payload),
        notify_fn=None,
        email_fn=_alert_email_fn(email_config, keychain),
    )

    # ⑥ 分发器（D7 缓冲 / D8 退避 / D9 线程池 / D10 全失败告警）
    dispatcher = DeliveryDispatcher(
        gateways, email_sender, logs, stats, alert,
        email_config_provider=email_config.get,
    )

    # ⑦ 轮询（每轮热加载启用目标；status/log 经事件流回 GUI）
    poll_loop = PollLoop(
        chat_db, watermark, config, FilterChain(), dispatcher,
        targets_loader=lambda: targets.list(enabled_only=True),
        emit_status=lambda: events.status(poll_loop.status()),
        emit_log=events.log,
    )

    # ⑧ 7 天日志保留清理（每小时一次，低优先级线程）
    retention = RetentionWorker(logs)
    retention.start()

    ctx = AppContext(
        chat_db=chat_db, watermark=watermark, config=config, targets=targets,
        email_config=email_config, logs=logs, stats=stats,
        poll_loop=poll_loop, dispatcher=dispatcher, events=events,
        gateways=gateways, started_at=time.time(),
    )
    logs.append_run("INFO", "CORE_START", "监听核心已启动")

    server = IpcServer(ctx, sys.stdin)
    try:
        server.serve_forever()  # child_ready → 命令循环；EOF（GUI 退出）→ 返回
    finally:
        poll_loop.set_running(False)
        retention.stop()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
