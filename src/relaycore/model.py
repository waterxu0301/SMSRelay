# model.py — 核心数据实体（tasks 2.1, Trace: D-ENTITY-001）
#
# 字段与 design.md §6.2 各层签名、§7 契约出入参、§12 表结构对齐。
# 约束 #5：实体本身不提供验证码/短信正文的持久化方法，SmsRecord.text 仅内存流转。

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SmsRecord:
    """chat.db 中一条待处理短信（内存流转，不落盘正文）。"""
    rowid: int
    sender: str | None      # handle.id；NULL → 显示「未知发件人」
    text: str               # 短信正文（仅内存流转）
    ts: float               # 归一化后的 Unix 秒（D6 自校准）；异常时可为 nan


@dataclass(frozen=True)
class PushTarget:
    """投递目标（push_targets 表行 + Keychain 回指）。"""
    id: str
    name: str
    platform: str           # feishu | dingtalk | wecom
    url: str                # webhook 地址
    enabled: bool
    secret_account: str | None = None  # Keychain 账户名回指（密钥本体不入库）


@dataclass(frozen=True)
class PushResult:
    """单目标单次投递结果（Gateway 统一返回）。"""
    ok: bool
    code: int | None = None     # 平台业务码（飞书 code / 钉钉企微 errcode）
    retryable: bool = False     # 是否可纳入退避重试
    reason: str | None = None   # 失败原因（脱敏，不含验证码/正文）


@dataclass(frozen=True)
class FilterDecision:
    """过滤链判定结果（accept=False 时 rowid 仍计入已处理）。"""
    accept: bool
    reason: str | None = None   # EXPIRED / SENDER_NOT_ALLOWED / KEYWORD_NOT_ALLOWED / SILENT / EMPTY_WHITELIST


@dataclass(frozen=True)
class Readability:
    """chat.db 可读性探测结果。"""
    ok: bool
    reason: str | None = None   # ok / missing_fda / authorization / missing


@dataclass
class AlertEvent:
    """全目标失败告警事件（D10：sha1(rowid + sorted_target_ids) 去重 10 分钟）。"""
    event_key: str
    targets: list[str] = field(default_factory=list)
    reason: str = ""
    deduped: bool = False
