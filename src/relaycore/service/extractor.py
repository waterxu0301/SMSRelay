# extractor.py — 验证码提取（tasks 4.2, Trace: D-BSV-002）
#
# 复用 sms_relay.py:41/97-100 的 CODE_PATTERN 与「取最后一个匹配」语义。

from __future__ import annotations

import re

# 复用现状正则（sms_relay.py:41），逐字符一致：6 位数字，左右不得紧邻数字
CODE_PATTERN = re.compile(r"(?<!\d)\d{6}(?!\d)")


def extract(text: str) -> str | None:
    """提取 6 位验证码；多码正文取最后一个；无匹配返回 None。"""
    matches = CODE_PATTERN.findall(text)
    return matches[-1] if matches else None
