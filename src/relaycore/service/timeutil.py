# timeutil.py — chat.db 时间单位自校准（tasks 4.1, Trace: D-BSV-001, 决策 D6）
#
# 实测结论（2026-10-22, macOS 27.0.1 arm64, design §16 O1 闭环）：
#   chat.db message.date = **纳秒**（Apple 纪元 2001-01-01 起），基准换算 ts = raw/1e9 + 978307200。
# D6 兜底：逐值自校准（1e9 → 1e6 → 1e3），全部越界返回 nan（异常标志），
# 上层按「不过期」投递 + WARN（不漏码优先）；单位判定结果进程内缓存沿用。
#
# 可信区间：[2001-01-01（Apple 纪元对应 Unix 978307200）, now + 1 天]。

from __future__ import annotations

import math
import time

# Apple epoch (2001-01-01) → Unix epoch 偏移（秒），证据 sms_relay.py:33/86
APPLE_EPOCH_OFFSET = 978307200
# 合理区间下界：Apple 纪元本身
_LOWER = float(APPLE_EPOCH_OFFSET)

# 单位候选（除数, 单位名），按设计基准顺序 ns 优先
_UNITS = ((1e9, "ns"), (1e6, "us"), (1e3, "ms"))

# 进程内单位缓存（一次判定全局沿用；None = 尚未判定）
_unit_hint: str | None = None


def get_unit_hint() -> str | None:
    """返回当前进程沿用的时间单位判定（未判定过返回 None）。"""
    return _unit_hint


def reset_unit_hint() -> None:
    """重置单位缓存（测试用）。"""
    global _unit_hint
    _unit_hint = None


def normalize_apple_date(raw, now: float | None = None) -> float:
    """把 chat.db message.date 归一化为 Unix 秒。

    - 优先按进程缓存的单位解释；
    - 逐值校验：在合理区间 [2001-01-01, now+1d] 内的候选中取最新时间（max ts），
      消除 us/ms 输入被低阶单位误读为 2001 年附近值的歧义；
    - 三单位均越界 / 输入非法 → 返回 nan（异常标志，上层按不过期投递 + WARN）。
    换算误差 ≤1s（float 精度内）。
    """
    global _unit_hint

    if raw is None:
        return math.nan
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return math.nan
    if not math.isfinite(value):
        return math.nan

    now_s = time.time() if now is None else now
    upper = now_s + 86400.0  # now + 1 天

    # 缓存单位优先；其次按 ns/us/ms 全量自校准
    ordered = list(_UNITS)
    if _unit_hint:
        ordered.sort(key=lambda u: 0 if u[1] == _unit_hint else 1)

    best: float | None = None
    for divisor, name in ordered:
        ts = value / divisor + APPLE_EPOCH_OFFSET
        if not (_LOWER <= ts <= upper):
            continue
        if _unit_hint and name == _unit_hint:
            return ts  # 沿用已判定单位
        if best is None or ts > best:
            best = ts
            _unit_hint = name

    if best is not None:
        return best

    # 仅缓存单位越界时仍需兜底尝试其余单位（数据源单位漂移防御）
    if _unit_hint:
        for divisor, name in _UNITS:
            if name == _unit_hint:
                continue
            ts = value / divisor + APPLE_EPOCH_OFFSET
            if _LOWER <= ts <= upper:
                _unit_hint = name
                return ts

    return math.nan  # 异常标志：三单位均越界
