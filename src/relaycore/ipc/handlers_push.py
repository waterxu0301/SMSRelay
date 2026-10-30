# handlers_push.py — 邮件配置与测试推送命令（tasks 5.5, Trace: D-PSV-007）
#
# save_email：写 email_config 单行（授权码不入参——GUI 直写 Keychain email.authcode）；
# test_push：dry-run 用占位码渲染 preview，**零外发请求**（spec diagnostics）；
# 真实测试投递：占位码 000000 走一次完整 gateway，结果回执，不触发告警/统计计数。

from __future__ import annotations

from relaycore.dao.state import ValidationError
from relaycore.ipc.server import _error, _ok

TEST_CODE = "000000"  # contracts §2.0：dry-run/真实测试投递统一占位码


def handle_get_email(ctx, rid, params) -> dict:
    """邮件区配置摘要读取（tasks 9.4 派生，同 check_db 先例）。

    配置非敏感（host/port/sender/recipients）；授权码永远不经 IPC（Keychain）。
    """
    return _ok(rid, email=ctx.email_config.get())


def handle_save_email(ctx, rid, params) -> dict:
    params = params or {}
    fields: list[str] = []

    smtp_host = params.get("smtp_host") or None
    try:
        smtp_port = int(params.get("smtp_port") or 465)
    except (TypeError, ValueError):
        smtp_port = 0
        fields.append("smtp_port")
    security = params.get("security") or "ssl"
    sender = params.get("sender") or None
    recipients = params.get("recipients") or []
    if isinstance(recipients, str):
        recipients = [r.strip() for r in recipients.split(",") if r.strip()]
    enabled = bool(params.get("enabled", False))

    if enabled and not smtp_host:
        fields.append("smtp_host")
    if enabled and not sender:
        fields.append("sender")
    if enabled and not recipients:
        fields.append("recipients")
    if security not in ("ssl", "starttls", "none"):
        fields.append("security")
    if fields:
        return _error(rid, "VALIDATION", "邮件配置校验失败", fields=sorted(set(fields)))

    # 授权码不入参：GUI 直写 Keychain `email.authcode`，此处仅存非敏感配置
    try:
        ctx.email_config.set(enabled, smtp_host, smtp_port,
                             None if security == "none" else security,
                             sender, recipients)
    except ValidationError as exc:
        return _error(rid, "VALIDATION", str(exc))
    return _ok(rid, email=ctx.email_config.get())


def handle_set_email_enabled(ctx, rid, params) -> dict:
    enabled = bool((params or {}).get("enabled"))
    current = ctx.email_config.get()
    if enabled and not (current.get("smtp_host") and current.get("recipients")):
        return _error(rid, "VALIDATION", "未配置 SMTP，无法启用", fields=["smtp_host"])
    ctx.email_config.set(enabled, current.get("smtp_host"), current.get("smtp_port"),
                         current.get("security"), current.get("sender"),
                         current.get("recipients") or [])
    return _ok(rid, email=ctx.email_config.get())


def handle_test_push(ctx, rid, params) -> dict:
    params = params or {}
    target_id = params.get("target_id")
    dry_run = bool(params.get("dry_run", True))

    target = ctx.targets.get(target_id) if target_id else None
    if target is None:
        return _error(rid, "VALIDATION", "目标不存在", fields=["target_id"])
    if not target.enabled:
        return _error(rid, "TARGET_DISABLED", f"目标已停用: {target.name}")

    if dry_run:
        # spec：dry-run 平台上不会收到真实消息，零外发请求
        gateway = ctx.gateways.get(target.platform)
        if gateway is None:
            return _error(rid, "VALIDATION", f"未知平台: {target.platform}")
        preview = gateway.preview(target)
        return _ok(rid, dry_run=True, platform=preview.platform,
                   url_masked=preview.url_masked, content=preview.content)

    # 真实测试投递：占位码走一次完整 gateway，结果回执；不计统计/告警（contracts §6）
    gateway = ctx.gateways.get(target.platform)
    if gateway is None:
        return _error(rid, "VALIDATION", f"未知平台: {target.platform}")
    result = gateway.push(target, TEST_CODE)
    ctx.logs.append_run(
        "INFO" if result.ok else "WARN", "TEST_PUSH",
        f"真实测试推送 {target.name}: {'成功' if result.ok else result.reason}",
    )
    return _ok(rid, dry_run=False, ok=result.ok, reason=result.reason)


def register(server, add) -> None:
    add("get_email", handle_get_email)
    add("save_email", handle_save_email)
    add("set_email_enabled", handle_set_email_enabled)
    add("test_push", handle_test_push)
