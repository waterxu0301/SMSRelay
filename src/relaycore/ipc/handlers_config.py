# handlers_config.py — 配置热加载与过滤配置（tasks 5.3, Trace: D-PSV-005）
#
# reload_config：清 ConfigCache（本实现 ConfigRepo 无缓存，直读即热生效）；
# save_filters：写 config 表，校验 HH:MM 与 interval ≥ 1s，失败 → VALIDATION。

from __future__ import annotations

import re

from relaycore.ipc.server import _error, _ok

_HHMM = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")

# save_filters 可写键（白名单，防任意键注入）
_WRITABLE_KEYS = {
    "interval", "max_age", "filter_mode",
    "sender_filter", "sender_whitelist",
    "keyword_filter", "keyword_whitelist",
    "silent_start", "silent_end",
}


def handle_reload_config(ctx, rid, params) -> dict:
    # ConfigRepo 直读 SQLite，无内存缓存 → 保存即热生效（无需重启子进程）
    ctx.events.status(ctx.poll_loop.status())
    return _ok(rid, config=ctx.config.get_all())


def handle_save_filters(ctx, rid, params) -> dict:
    fields: list[str] = []
    updates: dict[str, str] = {}

    for key, value in (params or {}).items():
        if key not in _WRITABLE_KEYS:
            continue
        value = "" if value is None else str(value)
        if key == "interval":
            try:
                if float(value) < 1:
                    fields.append("interval")
                    continue
            except ValueError:
                fields.append("interval")
                continue
        if key == "max_age":
            try:
                if float(value) <= 0:
                    fields.append("max_age")
                    continue
            except ValueError:
                fields.append("max_age")
                continue
        if key in ("silent_start", "silent_end") and value and not _HHMM.match(value):
            fields.append(key)
            continue
        if key in ("sender_filter", "keyword_filter") and value not in ("0", "1"):
            fields.append(key)
            continue
        updates[key] = value

    # 静默时段成对校验：一个非空则两个都必须非空
    merged_silent_start = updates.get("silent_start", ctx.config.get("silent_start", ""))
    merged_silent_end = updates.get("silent_end", ctx.config.get("silent_end", ""))
    if (merged_silent_start or merged_silent_end) and not (
            merged_silent_start and merged_silent_end):
        fields.append("silent_period")

    # 空白名单拒绝启用（spec：白名单不能为空）
    if updates.get("sender_filter") == "1" and not (
            updates.get("sender_whitelist") or ctx.config.get("sender_whitelist")):
        fields.append("sender_whitelist")
    if updates.get("keyword_filter") == "1" and not (
            updates.get("keyword_whitelist") or ctx.config.get("keyword_whitelist")):
        fields.append("keyword_whitelist")

    if fields:
        return _error(rid, "VALIDATION", "过滤配置校验失败", fields=sorted(set(fields)))

    for key, value in updates.items():
        ctx.config.set(key, value)
    return _ok(rid, config=ctx.config.get_all())


def register(server, add) -> None:
    add("reload_config", handle_reload_config)
    add("save_filters", handle_save_filters)
