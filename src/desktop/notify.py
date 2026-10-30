# notify.py — 系统通知（tasks 9.11, Trace: D-FE-004）
#
# design D10 + §6.3 系统通知行：仅告警（alert 事件）推 UNUserNotificationCenter；
# request_auth() 启动时请求权限；未授权 → 顶部横幅兜底 + 「去系统设置」提示；
# dev 态（非 .app bundle）自动降级仅界面告警；通知正文用脱敏摘要，
# 不含 6 位验证码（spec 敏感信息约束）。

from __future__ import annotations

import sys
import uuid

from desktop.redaction import redact_text

try:
    from UserNotifications import (UNMutableNotificationContent,
                                   UNNotificationRequest,
                                   UNUserNotificationCenter)
    HAVE_UN = True
except ImportError:  # dev 环境未装 PyObjC → 降级仅界面告警
    HAVE_UN = False

# UNAuthorizationOptions：alert(4) | sound(2)
_AUTH_OPTIONS = 6

_SETTINGS_URL = ("x-apple.systempreferences:"
                 "com.apple.preference.security?notifications")


def _in_app_bundle() -> bool:
    return ".app/Contents/" in sys.executable


class NotifyService:
    """告警 → 系统通知；未授权横幅兜底；dev 态纯界面。"""

    def __init__(self, window):
        self._window = window
        self._granted = False
        window.on("alert", self._on_alert)

    @property
    def _available(self) -> bool:
        # dev 态（非 bundle）即使装了 PyObjC 也不发系统通知
        return HAVE_UN and _in_app_bundle()

    # ---------- 权限 ----------
    def request_auth(self) -> None:
        """启动时请求通知权限（dev 态/无 PyObjC 直接跳过）。"""
        if not self._available:
            return
        try:
            center = UNUserNotificationCenter.currentNotificationCenter()
            center.requestAuthorizationWithOptions_completionHandler_(
                _AUTH_OPTIONS, self._on_auth_done)
        except Exception:
            self._granted = False

    def _on_auth_done(self, granted: bool, _error) -> None:
        # completion handler 在后台线程 → 派回 Tk 主线程
        self._window._root.after(0, lambda: self._set_granted(granted))

    def _set_granted(self, granted: bool) -> None:
        self._granted = bool(granted)
        if not granted:
            self._window.show_banner(
                "系统通知未授权，告警将仅在应用内显示；"
                "可在系统设置中开启通知")

    # ---------- 告警消费 ----------
    def _on_alert(self, payload: dict) -> None:
        """核心 alert 事件（summary 已脱敏；界面告警由告警面板负责）。"""
        summary = payload.get("summary") or "投递失败告警"
        self.notify_alert(summary)

    def notify_alert(self, text: str) -> None:
        """发送告警系统通知；未授权 → 横幅兜底。"""
        body = redact_text(text)  # 正文脱敏，不含 6 位码
        if not self._available:
            return  # dev 态：仅界面告警（alert 面板 + 状态横幅）
        if not self._granted:
            self._window.show_banner(
                f"告警：{body}（系统通知未授权，可在系统设置中开启）")
            return
        try:
            center = UNUserNotificationCenter.currentNotificationCenter()
            content = UNMutableNotificationContent.alloc().init()
            content.setTitle_("SMS 接力告警")
            content.setBody_(body)
            request = UNNotificationRequest.requestWithIdentifier_content_trigger_(
                str(uuid.uuid4()), content, None)
            center.addNotificationRequest_withCompletionHandler_(
                request, lambda _e: None)
        except Exception:
            # 系统通知失败不阻断界面告警
            self._window.show_banner(f"告警：{body}")
