# dispatch.py — 投递分发器（tasks 4.9, Trace: D-BSV-008）
#
# D7 环形缓冲 100 条（内存，不落盘）；D8 退避 2/4/8s+抖动（限频起始 5s，总窗 ≤15s）；
# D9 线程池 max_workers=min(8, 启用数)；每次结果写 delivery_log；
# 全目标失败且重试用尽 → AlertManager（幂等键 sha1(rowid+sorted_target_ids)）。

from __future__ import annotations

import hashlib
import random
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from relaycore.dao.state import LogRepo, StatsRepo
from relaycore.gateway.base import Gateway
from relaycore.gateway.email_sender import EmailSender
from relaycore.model import PushTarget, SmsRecord

# D8 退避参数（集中配置便于调参）
BACKOFF_SEQUENCE = (2.0, 4.0, 8.0)
LIMITED_START_DELAY = 5.0
JITTER_MAX = 1.0
TOTAL_WINDOW_MAX = 15.0
MAX_ATTEMPTS = 3
BUFFER_CAPACITY = 100  # D7

BufferEntry = dict  # {rowid, sender_label, code, ts, fingerprint}（不含正文）


def event_key(rowid: int, target_ids: list[str]) -> str:
    """D10 幂等键：sha1(msg_rowid + sorted(target_ids))。"""
    raw = f"{rowid}+" + ",".join(sorted(target_ids))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class DeliveryDispatcher:
    def __init__(self, gateways: dict[str, Gateway], email_sender: EmailSender | None,
                 log_repo: LogRepo, stats_repo: StatsRepo,
                 alert_manager, email_config_provider: Callable[[], dict] | None = None,
                 sleep_fn: Callable[[float], None] = time.sleep):
        self._gateways = gateways
        self._email = email_sender
        self._log = log_repo
        self._stats = stats_repo
        self._alert = alert_manager
        self._email_config_provider = email_config_provider or (lambda: {})
        self._sleep = sleep_fn
        self._buffer: deque[BufferEntry] = deque(maxlen=BUFFER_CAPACITY)

    # ---------- D7 内存环形缓冲 ----------
    def remember(self, record: SmsRecord, code: str) -> None:
        """过滤通过后入缓冲；fingerprint=sha1(正文) 仅用于去重判断，不落盘。"""
        self._buffer.append({
            "rowid": record.rowid,
            "sender_label": record.sender or "未知发件人",
            "code": code,
            "ts": record.ts,
            "fingerprint": hashlib.sha1(record.text.encode("utf-8")).hexdigest(),
        })

    def buffer_entries(self) -> list[BufferEntry]:
        return list(self._buffer)

    # ---------- 分发 ----------
    def dispatch(self, record: SmsRecord, code: str, targets: list[PushTarget]) -> dict:
        """任务粒度 = 消息 × 目标；返回 {target_id: PushResult} 汇总。"""
        if not targets:
            # spec：无启用目标 → 记录 + 提示，不静默丢弃
            self._log.append_run("WARN", "NO_TARGET",
                                 f"消息 {record.rowid} 无启用投递目标，已跳过")
            return {}

        max_workers = min(8, len(targets))
        results: dict = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._deliver_with_retry, record, code, target): target
                for target in targets
            }
            for future, target in futures.items():
                pr, attempts = future.result()
                results[target.id] = pr
                self._log.append_delivery(record.rowid, target.id, target.platform,
                                          pr.ok, attempts, pr.reason)
                self._stats.record(record.rowid, target.id, pr.ok)

        # 全目标失败 → 三重告警（D10 幂等）
        if targets and all(not r.ok for r in results.values()):
            key = event_key(record.rowid, [t.id for t in targets])
            reasons = sorted({r.reason or "未知" for r in results.values()})
            self._alert.alert(key, [t.name for t in targets], "; ".join(reasons))
        return results

    def repush(self, rowid: int, targets: list[PushTarget]) -> dict | None:
        """手动重推：从内存缓冲取；缓冲无此条 → None（spec：不可重推提示）。

        已知取舍（design §3.3）：环形缓冲不存短信原文（约束 #5），故重推时
        record.text 为空 → 推送退化为单行格式（标识 + 验证码 + 时效提示），
        不含原文行。这是「原文不常驻内存」与「重推完整性」之间的显式取舍。
        """
        entry = next((e for e in self._buffer if e["rowid"] == rowid), None)
        if entry is None:
            return None
        record = SmsRecord(rowid=rowid, sender=None, text="", ts=entry["ts"])
        self._log.append_run("INFO", "REPUSH", f"手动重推消息 {rowid}")
        return self.dispatch(record, entry["code"], targets)

    # ---------- 单目标重试 ----------
    def _deliver_with_retry(self, record: SmsRecord, code: str,
                            target: PushTarget) -> tuple:
        from relaycore.model import PushResult
        from relaycore.gateway.dingtalk import RATE_LIMIT_CODES as DT_LIMIT
        from relaycore.gateway.wecom import RATE_LIMIT_CODES as WC_LIMIT
        last: PushResult | None = None
        attempts = 0
        started = time.monotonic()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            attempts = attempt
            if target.platform == "email":
                if self._email is None:
                    return PushResult(ok=False, retryable=False, reason="EMAIL_DISABLED"), attempts
                last = self._email.send(self._email_config_provider(), code,
                                        {"sender_label": target.name})
            else:
                gw = self._gateways.get(target.platform)
                if gw is None:
                    return PushResult(ok=False, retryable=False, reason="NO_GATEWAY"), attempts
                # 原文透传：短信原文仅内存流转进入推送 payload，不落库（约束 #5）
                last = gw.push(target, code, record.text)
            if last.ok:
                return last, attempts
            if not last.retryable or attempt == MAX_ATTEMPTS:
                return last, attempts
            # D8 退避：限频码起始 5s；总窗 ≤15s；各加 0-1s 抖动
            limited = last.code in DT_LIMIT or last.code in WC_LIMIT
            base = LIMITED_START_DELAY if limited else BACKOFF_SEQUENCE[attempt - 1]
            delay = min(base + random.uniform(0, JITTER_MAX),
                        TOTAL_WINDOW_MAX - (time.monotonic() - started))
            if delay <= 0:
                return last, attempts
            self._sleep(delay)
        return last, attempts  # pragma: no cover
