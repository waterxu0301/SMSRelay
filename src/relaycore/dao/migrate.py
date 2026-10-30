# migrate.py — app.db 启动期迁移执行器（tasks 1.2, Trace: D-DB-001）
#
# 职责：
#   1. 首次启动对 app.db 幂等执行 001_init_app_db.sql（9 表 + 初始化字典）；
#   2. 执行前设置 PRAGMA journal_mode=WAL（R8 单写者 + 轮询读并发）与 foreign_keys=ON；
#   3. 按 config.schema_version 记录已执行版本，重复启动跳过建表。
#
# DDL 脚本定位顺序（打包态优先于开发态）：
#   1) 环境变量 SMS_RELAY_DDL 指定的绝对路径；
#   2) 包内资源 relaycore/sql/001_init_app_db.sql（py2app 打包时随包分发）。
#
# 注：v1.0.0 起 DDL 已随包分发（commit b3a2fe2「DDL 脚本入包」），全新机器首启
# 不再因找不到建表脚本而失败；本文件不再回落到任何仓库外路径。

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1
DDL_FILENAME = "001_init_app_db.sql"

_SQLITE_MASTER_TABLES = (
    "config", "push_targets", "secret_refs", "email_config", "watermark",
    "delivery_log", "run_log", "alert_log", "stats_daily",
)


def default_ddl_path() -> Path:
    """按优先级解析 DDL 脚本路径（不校验存在性，由 ensure_schema 报错）。"""
    env = os.environ.get("SMS_RELAY_DDL")
    if env:
        return Path(env)
    # 包内资源：relaycore/sql/ 随包整目录分发（打包态与源码态同址）
    return Path(__file__).resolve().parents[1] / "sql" / DDL_FILENAME


def ensure_schema(db_path: str | os.PathLike, ddl_path: str | os.PathLike | None = None) -> int:
    """确保 app.db 处于目标 schema 版本。

    - 首次启动：执行 DDL（幂等 IF NOT EXISTS）并写入 schema_version=1；
    - 重复启动：检测到 schema_version >= SCHEMA_VERSION 时跳过建表（WAL pragma 照常设置）；
    - 返回执行后的 schema 版本（0 表示 DDL 未执行但库可打开时的异常兜底不发生，正常为 1）。
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    try:
        # WAL 为库级持久属性，首次设置后保留；重复设置无副作用
        conn.execute("PRAGMA journal_mode=WAL").fetchone()
        conn.execute("PRAGMA foreign_keys=ON")

        current = _read_schema_version(conn)
        if current >= SCHEMA_VERSION:
            # 冷启动防御：版本号已到但表缺失（例如手工删表）时仍补跑 DDL（IF NOT EXISTS 幂等）
            if _tables_present(conn):
                return current

        ddl = Path(ddl_path) if ddl_path else default_ddl_path()
        if not ddl.exists():
            raise FileNotFoundError(f"DDL 脚本不存在: {ddl}")
        conn.executescript(ddl.read_text(encoding="utf-8"))
        conn.commit()

        return _read_schema_version(conn)
    finally:
        conn.close()


def _read_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM config WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        return 0  # config 表尚未建立
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def _tables_present(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r[0] for r in rows}
    return all(t in names for t in _SQLITE_MASTER_TABLES)
