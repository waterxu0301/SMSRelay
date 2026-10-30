# chat_db.py — DAO-chatdb 层（tasks 3.1, Trace: D-DAO-CHAT-001）
#
# design.md §6.2①：复用现状 SQL（sms_relay.py:71-80），每轮新建 mode=ro 连接；
# 时间解析走 normalize_apple_date()（D6）；异常分类 DbReadDenied/DbLocked/DbMissing。
# 约束 #5：text 仅内存流转（SmsRecord），本模块不写任何落盘动作。

from __future__ import annotations

import sqlite3
from pathlib import Path

from relaycore.model import Readability, SmsRecord
from relaycore.service.timeutil import normalize_apple_date


class DbReadDenied(Exception):
    """chat.db 读取被 TCC 拒绝（authorization denied）。→ 引导页，监听不中断"""


class DbLocked(Exception):
    """chat.db 被占用（database is locked）。→ 记 WARN，下轮重试"""


class DbMissing(Exception):
    """chat.db 文件缺失。→ 停止扫描线程，引导页说明"""


# 复用 SQL（sms_relay.py:71-80）：字段 ROWID/sender/text/date，LEFT JOIN handle
_SQL_READ_NEW = """
    SELECT m.ROWID, h.id AS sender, m.text, m.date
    FROM message m
    LEFT JOIN handle h ON m.handle_id = h.ROWID
    WHERE m.ROWID > ? AND m.text LIKE '%验证码%'
    ORDER BY m.ROWID ASC
"""


def _classify(exc: sqlite3.Error) -> Exception:
    """把 sqlite3 异常按 design §11 兜底表分类。"""
    msg = str(exc).lower()
    if "authorization denied" in msg or "not authorized" in msg:
        return DbReadDenied(str(exc))
    if "locked" in msg or "busy" in msg:
        return DbLocked(str(exc))
    return exc


class ChatDbRepository:
    """chat.db 只读仓库。每轮新建 file:...?mode=ro 连接（不持有长连接）。"""

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        if not self._db_path.exists():
            raise DbMissing(f"chat.db 不存在: {self._db_path}")
        try:
            return sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise _classify(exc) from exc

    def read_new(self, since_rowid: int) -> list[SmsRecord]:
        """读取 ROWID > since_rowid 且含「验证码」的消息（SQL 层下推过滤）。"""
        conn = self._connect()
        try:
            try:
                rows = conn.execute(_SQL_READ_NEW, (since_rowid,)).fetchall()
            except sqlite3.Error as exc:
                raise _classify(exc) from exc
        finally:
            conn.close()

        records: list[SmsRecord] = []
        for rowid, sender, text, raw_date in rows:
            # D6 自校准；异常时 ts=nan → 上层按「不过期」投递 + WARN，不静默漏码
            ts = normalize_apple_date(raw_date)
            records.append(SmsRecord(
                rowid=rowid,
                sender=sender,
                text=text or "",
                ts=ts,
            ))
        return records

    def check_readable(self) -> Readability:
        """启动/重启子进程时的可读性探测（SELECT 1），供授权检测页使用。"""
        try:
            conn = self._connect()
        except DbMissing:
            return Readability(ok=False, reason="missing")
        try:
            try:
                conn.execute("SELECT 1")
            except sqlite3.Error as exc:
                classified = _classify(exc)
                if isinstance(classified, DbLocked):
                    return Readability(ok=True, reason="locked")  # 可读但暂被占用
                if isinstance(classified, DbReadDenied):
                    return Readability(ok=False, reason="authorization")
                return Readability(ok=False, reason="missing_fda")
            return Readability(ok=True, reason="ok")
        finally:
            conn.close()
