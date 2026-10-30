# handlers_control.py — 状态与轮询控制命令（tasks 5.2, Trace: D-PSV-002..004）
#
# get_status/start/stop/scan_now/check_db；状态全部读自真实组件（不伪造）。
# check_db 为 tasks 9.10 派生（授权引导页经 IPC 触发核心 check_readable）。

from __future__ import annotations

from relaycore.ipc.server import _ok


def handle_status(ctx, rid, params) -> dict:
    return _ok(rid, **ctx.poll_loop.status())


def handle_start(ctx, rid, params) -> dict:
    ctx.poll_loop.set_running(True)
    ctx.events.status(ctx.poll_loop.status())
    return _ok(rid, listening=True)


def handle_stop(ctx, rid, params) -> dict:
    """重复 stop 幂等；不因 stop 退出进程（spec）。"""
    ctx.poll_loop.set_running(False)
    ctx.events.status(ctx.poll_loop.status())
    return _ok(rid, listening=False)


def handle_scan_now(ctx, rid, params) -> dict:
    result = ctx.poll_loop.scan_once(force=True)
    if result.get("busy"):
        return _ok(rid, busy=True)
    ctx.events.emit("scan_result", {"processed": result.get("processed", 0)})
    return _ok(rid, busy=False, processed=result.get("processed", 0))


def handle_check_db(ctx, rid, params) -> dict:
    """授权引导页探测（tasks 9.10，复用 3.1 check_readable）。

    返回 readable/reason：ok（可读）/ locked（可读但占用）/
    authorization（TCC 拒绝）/ missing / missing_fda。
    """
    result = ctx.chat_db.check_readable()
    return _ok(rid, readable=result.ok, reason=result.reason)


def register(server, add) -> None:
    add("get_status", handle_status)
    add("start", handle_start)
    add("stop", handle_stop)
    add("scan_now", handle_scan_now)
    add("check_db", handle_check_db)
