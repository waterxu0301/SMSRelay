# alert.py — 告警管理器（tasks 4.10, Trace: D-BSV-009）
#
# D10：三重告警（IPC alert 事件 + 系统通知请求 + 邮件）；
# 去重键 event_key（sha1(rowid+sorted_target_ids)），10 分钟窗口；
# 内容边界：仅时间/目标名/失败原因摘要，不含验证码/短信正文；
# 通知权限未授予 → notify_denied=True，界面横幅兜底（GUI 消费）。

from __future__ import annotations

import time
from typing import Callable

from relaycore.dao.state import LogRepo

DEDUP_WINDOW_SECONDS = 10 * 60


class AlertManager:
    def __init__(self, log_repo: LogRepo,
                 emit_event: Callable[[dict], None] | None = None,
                 notify_fn: Callable[[str, str], bool] | None = None,
                 email_fn: Callable[[str], object] | None = None):
        """
        emit_event: 向 GUI 发送 IPC alert 事件（payload dict）。
        notify_fn: 请求系统通知，返回是否已展示（False → notify_denied）。
        email_fn: 发送告警邮件（复用邮件通道）；未配置时传 None。
        """
        self._log = log_repo
        self._emit = emit_event or (lambda payload: None)
        self._notify = notify_fn
        self._email = email_fn

    def alert(self, event_key: str, target_names: list[str], reason: str) -> dict:
        """三重告警入口；返回 {deduped, notify_denied, emailed}。"""
        now = int(time.time())
        if self._log.alert_seen(event_key):
            return {"deduped": True, "notify_denied": False, "emailed": False}

        when = time.strftime("%Y-%m-%d %H:%M:%S")
        # 内容边界：时间 + 目标名 + 原因摘要（脱敏由 LogRepo 兜底）
        summary = f"投递失败告警：{', '.join(target_names) or '全部目标'} — {reason}"

        self._log.append_alert(event_key, summary, now + DEDUP_WINDOW_SECONDS)
        self._log.append_run("ERROR", "DELIVERY_ALL_FAILED", summary)

        # ① IPC alert 事件（界面横幅）
        self._emit({"type": "alert", "time": when, "summary": summary,
                    "targets": target_names})

        # ② 系统通知（未授予 → 界面兜底）
        notify_denied = False
        if self._notify is not None:
            notify_denied = not self._notify("SMS 接力", summary)

        # ③ 告警邮件（未配置则跳过，界面 + 通知仍生效）
        emailed = False
        if self._email is not None:
            emailed = bool(self._email(summary))

        return {"deduped": False, "notify_denied": notify_denied, "emailed": emailed}
