# redaction.py — GUI 侧敏感信息脱敏（tasks 9.14, Trace: D-FE-008）
#
# design §6.3 敏感处理：验证码仅内存展示、目标地址打码、授权码掩码；
# 全局日志 handler 注入脱敏过滤器（webhook token / 授权码 / 6 位码）。

from __future__ import annotations

import logging
import re

# webhook URL token 段（与核心侧 state.py redact 同规则）
_URL_SECRET_RE = re.compile(
    r"(https?://\S+?/(?:hook|send|robot/send)[/\w-]*?)([A-Za-z0-9_\-]{8,})")
# 6 位验证码（约束 #5：界面展示外一律清除）
_CODE_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")


def mask_url(url: str) -> str:
    """目标地址中间打码：https://oapi.feishu.com/••••/hook/AbCd 形式。"""
    if not url or len(url) <= 12:
        return "****"
    return f"{url[:18]}••••{url[-4:]}"


def mask_secret(value: str | None) -> str | None:
    """授权码/密钥掩码：尾 4 位（••••1234）。"""
    if not value:
        return value
    return "••••" + value[-4:] if len(value) > 4 else "••••"


def mask_code(text: str) -> str:
    """文本中的 6 位验证码替换（日志/控件展示前统一过滤）。"""
    return _CODE_RE.sub("••••••", text or "")


def redact_text(text: str) -> str:
    """全量脱敏：URL token 掩码 + 6 位码清除。"""
    if not text:
        return text
    text = _URL_SECRET_RE.sub(lambda m: m.group(1) + "****" + m.group(2)[-4:], text)
    return mask_code(text)


class RedactingFilter(logging.Filter):
    """全局日志过滤器：Message/args 经 redact_text 处理后输出。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_text(record.getMessage())
            record.args = ()
        except Exception:
            pass  # 脱敏失败不阻断日志
        return True


def install_global_redaction(logger: logging.Logger | None = None) -> None:
    """给（根）logger 注册脱敏过滤器 + handler。"""
    target = logger or logging.getLogger()
    flt = RedactingFilter()
    target.addFilter(flt)
    for handler in target.handlers:
        handler.addFilter(flt)
