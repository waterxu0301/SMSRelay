# state.py — DAO-state 层（tasks 3.2-3.5, Trace: D-DAO-003..007）
#
# design.md §6.2②：app.db 单写者。本模块所有 Repo 共享调用方传入的连接
# （WAL 模式由 migrate.ensure_schema 设置）。约束 #5：不提供验证码/正文的
# 任何写入接口；写入前对 detail/message 做脱敏。

from __future__ import annotations

import re
import time
import uuid
from sqlite3 import Connection, IntegrityError

from relaycore.model import PushTarget

KEYCHAIN_SERVICE = "com.zero-reconn.sms-relay"
_RETENTION_SECONDS = 7 * 24 * 3600

# 脱敏：webhook URL 中的 token 段、疑似授权码（长随机串）
_URL_SECRET_RE = re.compile(r"(https?://\S+?/(?:hook|send|robot/send)[/\w-]*?)([A-Za-z0-9_\-]{8,})")
# 脱敏：query 参数中的 token（钉钉 access_token、企微 key 等）
_QUERY_SECRET_RE = re.compile(r"([?&](?:access_token|key|token|secret)=)([A-Za-z0-9_\-./+=]{8,})")


def redact(text: str | None) -> str | None:
    """入库前脱敏：URL 末段 token 与 query 参数 token 掩码（保留尾 4 位）。"""
    if not text:
        return text
    masked = _URL_SECRET_RE.sub(lambda m: m.group(1) + _mask_tail(m.group(2)), text)
    return _QUERY_SECRET_RE.sub(lambda m: m.group(1) + _mask_tail(m.group(2)), masked)


def _mask_tail(secret: str) -> str:
    return "****" + secret[-4:] if len(secret) > 4 else "****"


class ValidationError(Exception):
    """业务校验错误（重名、枚举不符等），对应 IPC 错误码 VALIDATION。"""


class WatermarkRepo:
    """3.2：水位线只升不降（SQL 层 max() 保证）。scope 固定 'chat_message'。"""

    _SCOPE = "chat_message"

    def __init__(self, conn: Connection):
        self._conn = conn

    def get(self) -> int:
        row = self._conn.execute(
            "SELECT last_rowid FROM watermark WHERE scope = ?", (self._SCOPE,)
        ).fetchone()
        return row[0] if row else 0

    def advance(self, value: int) -> bool:
        """只升不降：value 小于当前值时不变，返回 False（调用方记 run_log ERROR）。"""
        cur = self._conn.execute(
            "INSERT INTO watermark (scope, last_rowid, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(scope) DO UPDATE SET "
            "last_rowid = max(last_rowid, excluded.last_rowid), "
            "updated_at = excluded.updated_at "
            "WHERE excluded.last_rowid > watermark.last_rowid",
            (self._SCOPE, value, int(time.time())),
        )
        self._conn.commit()
        return cur.rowcount > 0


class ConfigRepo:
    """3.3①：键值配置读写。"""

    def __init__(self, conn: Connection):
        self._conn = conn

    def get_all(self) -> dict:
        rows = self._conn.execute("SELECT key, value FROM config").fetchall()
        return dict(rows)

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, int(time.time())),
        )
        self._conn.commit()


class TargetsRepo:
    """3.3②③：push_targets CRUD + secret_refs 回指（不接收密钥明文）。"""

    def __init__(self, conn: Connection):
        self._conn = conn

    @staticmethod
    def _row_to_target(row) -> PushTarget:
        return PushTarget(
            id=row[0], name=row[1], platform=row[2], url=row[3],
            enabled=bool(row[4]), secret_account=row[5],
        )

    def list(self, enabled_only: bool = False) -> list[PushTarget]:
        sql = "SELECT id, name, platform, webhook, enabled, secret_account FROM push_targets"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY created_at ASC"
        return [self._row_to_target(r) for r in self._conn.execute(sql).fetchall()]

    def get(self, target_id: str) -> PushTarget | None:
        row = self._conn.execute(
            "SELECT id, name, platform, webhook, enabled, secret_account FROM push_targets WHERE id = ?",
            (target_id,),
        ).fetchone()
        return self._row_to_target(row) if row else None

    def create(self, name: str, platform: str, url: str,
               secret_hint: str | None = None) -> PushTarget:
        if platform not in ("feishu", "dingtalk", "wecom"):
            raise ValidationError(f"不支持的平台: {platform}")
        target_id = uuid.uuid4().hex
        secret_account = f"target.{target_id}"
        now = int(time.time())
        try:
            self._conn.execute(
                "INSERT INTO push_targets (id, name, platform, webhook, secret_account, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (target_id, name, platform, url, secret_account, now, now),
            )
            # secret_refs 回指：只存 service/account/hint，不存明文
            self._conn.execute(
                "INSERT INTO secret_refs (key, service, account, hint) VALUES (?, ?, ?, ?)",
                (f"target.{target_id}", KEYCHAIN_SERVICE, secret_account, secret_hint),
            )
        except IntegrityError as exc:
            self._conn.rollback()
            raise ValidationError(f"目标名称已存在或数据非法: {name}") from exc
        self._conn.commit()
        result = self.get(target_id)
        assert result is not None
        return result

    def update(self, target_id: str, name: str | None = None,
               url: str | None = None, secret_hint: str | None = None) -> PushTarget:
        sets, params = [], []
        if name is not None:
            sets.append("name = ?"); params.append(name)
        if url is not None:
            sets.append("webhook = ?"); params.append(url)
        if secret_hint is not None:
            self._conn.execute(
                "UPDATE secret_refs SET hint = ? WHERE key = ?", (secret_hint, f"target.{target_id}")
            )
        if not sets:
            existing = self.get(target_id)
            if existing is None:
                raise ValidationError(f"目标不存在: {target_id}")
            return existing
        sets.append("updated_at = ?"); params.append(int(time.time()))
        params.append(target_id)
        try:
            cur = self._conn.execute(
                f"UPDATE push_targets SET {', '.join(sets)} WHERE id = ?", params
            )
        except IntegrityError as exc:
            self._conn.rollback()
            raise ValidationError(f"目标名称已存在: {name}") from exc
        if cur.rowcount == 0:
            self._conn.rollback()
            raise ValidationError(f"目标不存在: {target_id}")
        self._conn.commit()
        result = self.get(target_id)
        assert result is not None
        return result

    def set_enabled(self, target_id: str, enabled: bool) -> None:
        cur = self._conn.execute(
            "UPDATE push_targets SET enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, int(time.time()), target_id),
        )
        if cur.rowcount == 0:
            raise ValidationError(f"目标不存在: {target_id}")
        self._conn.commit()

    def delete(self, target_id: str) -> None:
        cur = self._conn.execute("DELETE FROM push_targets WHERE id = ?", (target_id,))
        self._conn.execute("DELETE FROM secret_refs WHERE key = ?", (f"target.{target_id}",))
        if cur.rowcount == 0:
            self._conn.rollback()
            raise ValidationError(f"目标不存在: {target_id}")
        self._conn.commit()


class EmailConfigRepo:
    """3.3④：email_config 单行（id=1）读写；凭据走 Keychain（email.authcode）。"""

    def __init__(self, conn: Connection):
        self._conn = conn

    def get(self) -> dict:
        row = self._conn.execute(
            "SELECT enabled, smtp_host, smtp_port, security, sender, recipients FROM email_config WHERE id = 1"
        ).fetchone()
        if not row:
            return {"enabled": False}
        return {
            "enabled": bool(row[0]), "smtp_host": row[1], "smtp_port": row[2],
            "security": row[3], "sender": row[4],
            "recipients": [r for r in (row[5] or "").split(",") if r],
        }

    def set(self, enabled: bool, smtp_host: str | None, smtp_port: int | None,
            security: str | None, sender: str | None, recipients: list[str]) -> None:
        if security is not None and security not in ("ssl", "starttls"):
            raise ValidationError(f"不支持的加密方式: {security}")
        self._conn.execute(
            "INSERT INTO email_config (id, enabled, smtp_host, smtp_port, security, sender, recipients, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled, smtp_host=excluded.smtp_host, "
            "smtp_port=excluded.smtp_port, security=excluded.security, sender=excluded.sender, "
            "recipients=excluded.recipients, updated_at=excluded.updated_at",
            (1 if enabled else 0, smtp_host, smtp_port, security, sender,
             ",".join(recipients), int(time.time())),
        )
        self._conn.commit()


class LogRepo:
    """3.4：run_log/delivery_log/alert_log 写入、7 天清理、查询导出。写入前脱敏。"""

    def __init__(self, conn: Connection):
        self._conn = conn

    def append_run(self, level: str, code: str | None, message: str,
                   detail: str | None = None) -> None:
        if level not in ("DEBUG", "INFO", "WARN", "ERROR"):
            raise ValidationError(f"非法日志级别: {level}")
        self._conn.execute(
            "INSERT INTO run_log (level, code, message, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (level, code, redact(message), redact(detail), int(time.time())),
        )
        self._conn.commit()

    def append_delivery(self, msg_rowid: int, target_id: str | None, channel: str,
                        ok: bool, attempts: int = 1, reason: str | None = None) -> None:
        if channel not in ("feishu", "dingtalk", "wecom", "email"):
            raise ValidationError(f"非法渠道: {channel}")
        self._conn.execute(
            "INSERT INTO delivery_log (msg_rowid, target_id, channel, ok, attempts, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_rowid, target_id, channel, 1 if ok else 0, attempts,
             redact(reason), int(time.time())),
        )
        self._conn.commit()

    def append_alert(self, event_key: str, reason: str, dedup_until: int) -> None:
        """去重：窗口内已存在 event_key 则跳过（冲突不报错）。"""
        self._conn.execute(
            "INSERT OR IGNORE INTO alert_log (event_key, reason, dedup_until, created_at) "
            "VALUES (?, ?, ?, ?)",
            (event_key, redact(reason), dedup_until, int(time.time())),
        )
        self._conn.commit()

    def alert_seen(self, event_key: str) -> bool:
        """去重窗口内是否已告警过（dedup_until 未过期）。"""
        row = self._conn.execute(
            "SELECT dedup_until FROM alert_log WHERE event_key = ?", (event_key,)
        ).fetchone()
        return row is not None and row[0] > int(time.time())

    def purge_older_than_7d(self, now: int | None = None) -> int:
        cutoff = (now if now is not None else int(time.time())) - _RETENTION_SECONDS
        total = 0
        for table in ("delivery_log", "run_log", "alert_log"):
            cur = self._conn.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))
            total += cur.rowcount
        self._conn.commit()
        return total

    def query_run(self, level: str | None = None, days: int = 7) -> list[dict]:
        cutoff = int(time.time()) - days * 24 * 3600
        sql = "SELECT id, level, code, message, detail, created_at FROM run_log WHERE created_at >= ?"
        params: list = [cutoff]
        if level:
            sql += " AND level = ?"
            params.append(level)
        sql += " ORDER BY id DESC"
        return [
            {"id": r[0], "level": r[1], "code": r[2], "message": r[3],
             "detail": r[4], "created_at": r[5]}
            for r in self._conn.execute(sql, params).fetchall()
        ]

    def export_lines(self) -> list[str]:
        """GUI 导出：run_log 7 天内逐行文本。"""
        rows = self.query_run(days=7)
        return [
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['created_at']))} "
            f"[{r['level']}] {r['code'] or '-'} {r['message']}"
            for r in reversed(rows)
        ]

    def clear(self) -> None:
        for table in ("delivery_log", "run_log", "alert_log"):
            self._conn.execute(f"DELETE FROM {table}")
        self._conn.commit()


class StatsRepo:
    """3.5：7 天消息×目标聚合（delivery_log 为准，stats_daily 为缓存）。"""

    def __init__(self, conn: Connection):
        self._conn = conn

    def record(self, msg_rowid: int, target_id: str, ok: bool) -> None:
        """投递计数 upsert stats_daily 缓存（写路径调用；delivery_log 由 LogRepo 落）。"""
        day = time.strftime("%Y-%m-%d")
        self._conn.execute(
            "INSERT INTO stats_daily (day, target_id, total, ok) VALUES (?, ?, 1, ?) "
            "ON CONFLICT(day, target_id) DO UPDATE SET "
            "total = total + 1, ok = ok + excluded.ok",
            (day, target_id, 1 if ok else 0),
        )
        self._conn.commit()

    def rates(self, days: int = 7) -> list[dict]:
        cutoff = int(time.time()) - days * 24 * 3600
        rows = self._conn.execute(
            "SELECT target_id, COUNT(*) AS total, SUM(ok) AS ok "
            "FROM delivery_log WHERE created_at >= ? AND target_id IS NOT NULL "
            "GROUP BY target_id",
            (cutoff,),
        ).fetchall()
        results = []
        for target_id, total, ok in rows:
            rate = (ok or 0) / total if total else 1.0
            results.append({
                "target_id": target_id, "total": total, "ok": ok or 0,
                "rate": round(rate, 4), "degraded": rate < 0.995,
            })
        return results
