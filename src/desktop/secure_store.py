# secure_store.py — GUI 侧 Keychain 写入（tasks 9.7, Trace: D-FE-009）
#
# design D5：service=com.zero-reconn.sms-relay；account 约定
# `target.<uuid>` / `email.authcode`；核心子进程只读（4.8）。
# 值经 stdin 传入 `security`（避免 argv 出现在 ps）。

from __future__ import annotations

import subprocess

from desktop.redaction import mask_secret

SERVICE = "com.zero-reconn.sms-relay"


def set_secret(account: str, value: str) -> bool:
    """写入/覆盖 generic password（-U upsert）。值经 stdin 传给 security。"""
    proc = subprocess.run(
        ["security", "add-generic-password", "-U",
         "-s", SERVICE, "-a", account, "-w"],
        input=value.encode("utf-8"),
        capture_output=True,
    )
    return proc.returncode == 0


def delete_secret(account: str) -> bool:
    """删除条目；不存在时也视为成功（幂等）。"""
    proc = subprocess.run(
        ["security", "delete-generic-password", "-s", SERVICE, "-a", account],
        capture_output=True,
    )
    return proc.returncode == 0 or b"could not be found" in proc.stderr


def hint(value: str) -> str:
    """回执掩码（尾 4 位）。"""
    return mask_secret(value) or ""


def hint_of(account: str) -> str | None:
    """读取条目并返回掩码 hint（仅 GUI 侧；授权码永不回显明文）。"""
    proc = subprocess.run(
        ["security", "find-generic-password", "-w",
         "-s", SERVICE, "-a", account],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    value = proc.stdout.decode("utf-8", errors="replace").rstrip("\n")
    return mask_secret(value) if value else None
