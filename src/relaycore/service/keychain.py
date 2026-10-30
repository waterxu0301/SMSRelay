# keychain.py — 核心侧 Keychain 只读（tasks 4.8, Trace: D-BSV-010）
#
# design D5：service 固定 com.zero-reconn.sms-relay；写入仅在 GUI 侧（D-FE-009），
# 本模块不提供 set。读取失败 → None（上层走 CRED_LOST 流程）。

from __future__ import annotations

import subprocess

KEYCHAIN_SERVICE = "com.zero-reconn.sms-relay"


class KeychainStore:
    """子进程内只读：security find-generic-password -w。"""

    def __init__(self, service: str = KEYCHAIN_SERVICE):
        self._service = service

    def get_secret(self, account: str) -> str | None:
        try:
            proc = subprocess.run(
                ["security", "find-generic-password", "-s", self._service,
                 "-a", account, "-w"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None  # 条目不存在/被拒 → 不抛异常
        return proc.stdout.strip() or None


def mask(secret: str | None) -> str:
    """掩码回显：••••1234（尾 4 位）。"""
    if not secret:
        return ""
    return "••••" + secret[-4:]
