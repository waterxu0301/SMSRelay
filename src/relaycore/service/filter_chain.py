# filter_chain.py — 过滤链（tasks 4.3, Trace: D-BSV-003）
#
# design.md §6.2③：顺序 时效 → 发件方白名单 → 关键词白名单 → 静默时段；
# 任一环节拒绝返回 FilterDecision(False, reason)，rowid 照常计入已处理；
# 跨午夜静默：start > end 时按补集区间判断；白名单空 = 防御性 SKIP。
# cfg 键（config 表）：max_age、sender_whitelist/keyword_whitelist（逗号分隔）、
# sender_filter/keyword_filter（0/1 启用）、silent_start/silent_end（HH:MM，空=关闭）。

from __future__ import annotations

import math
import time

from relaycore.model import FilterDecision, SmsRecord


def _parse_list(raw: str | None) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _parse_hhmm(raw: str | None) -> int | None:
    """'HH:MM' → 当天分钟数；空/非法 → None（静默未配置）。"""
    if not raw or ":" not in raw:
        return None
    try:
        h, m = raw.split(":", 1)
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def in_silent_period(now_min: int, start: int, end: int) -> bool:
    """跨午夜安全的静默判定：start > end 时按补集区间（t ≥ start or t ≤ end）。"""
    if start == end:
        return False
    if start < end:
        return start <= now_min < end
    return now_min >= start or now_min <= end


class FilterChain:
    """apply(record, cfg)：时效 → 发件方白名单 → 关键词白名单 → 静默时段。"""

    def apply(self, record: SmsRecord, cfg: dict, now: float | None = None) -> FilterDecision:
        now = time.time() if now is None else now

        # ① 时效：ts 为 nan（D6 单位异常）→ 按不过期投递（不漏码优先）
        max_age = float(cfg.get("max_age", 300))
        if not math.isnan(record.ts) and (now - record.ts) > max_age:
            return FilterDecision(False, "EXPIRED")

        # ② 发件方白名单（启用但空 → 防御性拒绝，保存期已拦截）
        if cfg.get("sender_filter") in ("1", 1, True):
            senders = _parse_list(cfg.get("sender_whitelist"))
            if not senders:
                return FilterDecision(False, "EMPTY_WHITELIST")
            if (record.sender or "") not in senders:
                return FilterDecision(False, "SENDER_NOT_ALLOWED")

        # ③ 关键词白名单（正文命中任一关键词）
        if cfg.get("keyword_filter") in ("1", 1, True):
            keywords = _parse_list(cfg.get("keyword_whitelist"))
            if not keywords:
                return FilterDecision(False, "EMPTY_WHITELIST")
            if not any(k in record.text for k in keywords):
                return FilterDecision(False, "KEYWORD_NOT_ALLOWED")

        # ④ 静默时段（跨午夜安全；仅记录不投递）
        start = _parse_hhmm(cfg.get("silent_start"))
        end = _parse_hhmm(cfg.get("silent_end"))
        if start is not None and end is not None:
            now_min = time.localtime(now).tm_hour * 60 + time.localtime(now).tm_min
            if in_silent_period(now_min, start, end):
                return FilterDecision(False, "SILENT")

        return FilterDecision(True)
