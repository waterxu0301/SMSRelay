# handlers_query.py — 重推与统计日志命令（tasks 5.6, Trace: D-PSV-008, D-PSV-009）
#
# repush：命中内存环形缓冲（100 条）→ 重新投递；未命中 → OUT_OF_WINDOW；
# get_stats/get_logs：7 天窗口（约束 #22）；clear_logs 需 confirmed=true；
# set_autostart：本命令仅记录日志（SMAppService 实际操作在 GUI 进程内执行，9.8）。

from __future__ import annotations

from relaycore.ipc.server import _error, _ok


def handle_repush(ctx, rid, params) -> dict:
    params = params or {}
    rowid = params.get("rowid")
    if not isinstance(rowid, int):
        return _error(rid, "VALIDATION", "rowid 必须是整数", fields=["rowid"])
    targets = ctx.targets.list(enabled_only=True)
    result = ctx.dispatcher.repush(rowid, targets)
    if result is None:
        # 缓冲外（超过 100 条或重启后）→ spec：不可重推提示
        return _error(rid, "OUT_OF_WINDOW", "已超出重推窗口（内存缓冲无此消息）")
    return _ok(rid, results={tid: {"ok": r.ok, "reason": r.reason}
                             for tid, r in result.items()})


def handle_get_stats(ctx, rid, params) -> dict:
    params = params or {}
    try:
        days = max(1, min(7, int(params.get("days", 7))))  # 严格 7 天窗口
    except (TypeError, ValueError):
        days = 7
    return _ok(rid, stats=ctx.stats.rates(days=days))


def handle_get_logs(ctx, rid, params) -> dict:
    params = params or {}
    level = params.get("level") or None
    if level not in (None, "DEBUG", "INFO", "WARN", "ERROR"):
        return _error(rid, "VALIDATION", f"非法日志级别: {level}", fields=["level"])
    try:
        days = max(1, min(7, int(params.get("days", 7))))
    except (TypeError, ValueError):
        days = 7
    return _ok(rid, logs=ctx.logs.query_run(level=level, days=days))


def handle_clear_logs(ctx, rid, params) -> dict:
    if not (params or {}).get("confirmed"):
        return _error(rid, "VALIDATION", "清空日志需要二次确认（confirmed=true）",
                      fields=["confirmed"])
    ctx.logs.clear()
    return _ok(rid, cleared=True)


def handle_export_logs(ctx, rid, params) -> dict:
    return _ok(rid, lines=ctx.logs.export_lines())


def handle_set_autostart(ctx, rid, params) -> dict:
    enabled = bool((params or {}).get("enabled"))
    # SMAppService 实际操作在 GUI 进程内执行（9.8）；核心侧仅记录日志留痕
    ctx.logs.append_run("INFO", "AUTOSTART", f"开机自启{'开' if enabled else '关'}")
    return _ok(rid, ok=True, autostart=enabled)


def handle_list_buffered(ctx, rid, params) -> dict:
    """缓冲列表读取（tasks 9.6 派生，同 check_db/get_email 先例）。

    tasks 9.6 原文经 get_status.buffered_codes 拉列表，但该字段只是条数 int
    （poll_loop.status）；重推必须 rowid，故派生本命令直读内存环形缓冲。
    code 仅经 IPC 内存展示供辨识，不落日志/文件（spec 敏感信息约束）。
    """
    entries = ctx.dispatcher.buffer_entries()
    return _ok(rid, buffered=[
        {"rowid": e["rowid"], "sender_label": e.get("sender_label") or "",
         "code": e.get("code") or "", "ts": e.get("ts") or 0}
        for e in entries
    ])


def register(server, add) -> None:
    add("repush", handle_repush)
    add("get_stats", handle_get_stats)
    add("get_logs", handle_get_logs)
    add("clear_logs", handle_clear_logs)
    add("export_logs", handle_export_logs)
    add("set_autostart", handle_set_autostart)
    add("list_buffered", handle_list_buffered)
