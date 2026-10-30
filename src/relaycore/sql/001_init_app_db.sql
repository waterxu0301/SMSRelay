-- 001_init_app_db.sql — SMS 接力桌面应用 app.db 初始化 DDL
-- 依据：openspec/changes/sms-relay-desktop-app/design.md §12 数据库表 DDL 草案
-- 目标库：~/Library/Application Support/SMSRelay/app.db（由 relaycore/dao/migrate.py 在启动期执行）
-- 特性：幂等（CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE），可重复执行
-- 约束：app.db 禁止出现验证码与短信正文（proposal 约束 #5）

-- ============================================================
-- 1. config — 非敏感标量配置（键值）
-- ============================================================
CREATE TABLE IF NOT EXISTS config (
    key        TEXT    NOT NULL PRIMARY KEY,
    value      TEXT,
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 2. push_targets — 投递目标（平台枚举 CHECK，名称唯一）
-- ============================================================
CREATE TABLE IF NOT EXISTS push_targets (
    id             TEXT    NOT NULL PRIMARY KEY,
    name           TEXT    NOT NULL UNIQUE,
    platform       TEXT    NOT NULL CHECK (platform IN ('feishu', 'dingtalk', 'wecom')),
    webhook        TEXT    NOT NULL,
    secret_account TEXT,                      -- Keychain 账户名回指（密钥本体不入库）
    enabled        INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at     INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 3. secret_refs — Keychain 回指（不含机密本体）
-- ============================================================
CREATE TABLE IF NOT EXISTS secret_refs (
    key     TEXT NOT NULL PRIMARY KEY,        -- 形如 target.<uuid> / email.authcode
    service TEXT NOT NULL,                    -- com.zero-reconn.sms-relay
    account TEXT NOT NULL,                    -- Keychain account 名
    hint    TEXT                              -- 掩码尾 4 位（界面回显用）
);

-- ============================================================
-- 4. email_config — 邮件转发非凭据配置（单行表）
-- ============================================================
CREATE TABLE IF NOT EXISTS email_config (
    id          INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
    enabled     INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    smtp_host   TEXT,
    smtp_port   INTEGER,
    security    TEXT CHECK (security IS NULL OR security IN ('ssl', 'starttls')),
    sender      TEXT,
    recipients  TEXT,                         -- 逗号分隔的收件人列表
    updated_at  INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 5. watermark — 已处理水位线（只升不降，UPDATE 层 max() 保证）
-- ============================================================
CREATE TABLE IF NOT EXISTS watermark (
    scope       TEXT    NOT NULL PRIMARY KEY, -- 固定 'chat_message'
    last_rowid  INTEGER NOT NULL DEFAULT 0,
    updated_at  INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 6. delivery_log — 投递滚动日志（7 天保留，消息×目标为统计单元）
-- ============================================================
CREATE TABLE IF NOT EXISTS delivery_log (
    id         INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    msg_rowid  INTEGER NOT NULL,
    target_id  TEXT,
    channel    TEXT NOT NULL CHECK (channel IN ('feishu', 'dingtalk', 'wecom', 'email')),
    ok         INTEGER NOT NULL CHECK (ok IN (0, 1)),
    attempts   INTEGER NOT NULL DEFAULT 1,
    reason     TEXT,                          -- 失败原因（脱敏，不含验证码/正文）
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_delivery_log_created_rowid ON delivery_log (created_at, msg_rowid);

-- ============================================================
-- 7. run_log — 运行事件日志（7 天保留，导出/清空入口）
-- ============================================================
CREATE TABLE IF NOT EXISTS run_log (
    id         INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    level      TEXT NOT NULL CHECK (level IN ('DEBUG', 'INFO', 'WARN', 'ERROR')),
    code       TEXT,                          -- 事件码，如 SCAN_FAIL / SKIP_EXPIRED / SIGN_MISSING
    message    TEXT NOT NULL,                 -- 脱敏后文本
    detail     TEXT,                          -- 脱敏后补充信息
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_run_log_created_level ON run_log (created_at, level);

-- ============================================================
-- 8. alert_log — 告警去重与审计（7 天保留）
-- ============================================================
CREATE TABLE IF NOT EXISTS alert_log (
    event_key   TEXT    NOT NULL PRIMARY KEY, -- sha1(rowid + sorted_target_ids)
    reason      TEXT,
    dedup_until INTEGER NOT NULL,             -- 去重窗口截止（10 分钟）
    created_at  INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_alert_log_created ON alert_log (created_at);

-- ============================================================
-- 9. stats_daily — 消息×目标 日聚合缓存（可由 delivery_log 重建）
-- ============================================================
CREATE TABLE IF NOT EXISTS stats_daily (
    day       TEXT    NOT NULL,               -- YYYY-MM-DD
    target_id TEXT    NOT NULL,
    total     INTEGER NOT NULL DEFAULT 0,
    ok        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, target_id)
);

-- ============================================================
-- 初始化字典（幂等，重复执行不覆盖用户修改）
-- ============================================================
INSERT OR IGNORE INTO config (key, value, updated_at) VALUES
    ('interval',       '3',        strftime('%s', 'now')),
    ('max_age',        '300',      strftime('%s', 'now')),
    ('filter_mode',    'whitelist', strftime('%s', 'now')),
    ('silent_start',   '',         strftime('%s', 'now')),
    ('silent_end',     '',         strftime('%s', 'now')),
    ('autostart',      '0',        strftime('%s', 'now')),
    ('schema_version', '1',        strftime('%s', 'now'));
