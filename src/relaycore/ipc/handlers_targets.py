# handlers_targets.py — 目标 CRUD 命令（tasks 5.4, Trace: D-PSV-006）
#
# list_targets/save_target/delete_target/set_target_enabled；
# 密钥边界：钉钉加签 secret 不经 IPC 明文传输（GUI 直写 Keychain 9.7），
# IPC 仅回执 hint（尾 4 位）；URL 回显一律打码（design §6.3）。

from __future__ import annotations

from urllib.parse import urlparse

from relaycore.dao.state import ValidationError
from relaycore.gateway.base import mask_url
from relaycore.ipc.server import _error, _ok

_PLATFORMS = ("feishu", "dingtalk", "wecom")


def _serialize(target) -> dict:
    """回执不含明文 URL/密钥：url 打码 + secret hint（约束 #5）。"""
    return {
        "id": target.id,
        "name": target.name,
        "platform": target.platform,
        "url_masked": mask_url(target.url),
        "enabled": bool(target.enabled),
        "has_secret": bool(target.secret_account),
    }


def _validate(name, platform, url) -> list[str]:
    fields: list[str] = []
    if not (name or "").strip():
        fields.append("name")
    if platform not in _PLATFORMS:
        fields.append("platform")
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        fields.append("url")
    return fields


def _notify_targets_changed(ctx) -> None:
    """目标变更后让 PollLoop 即刻使用新目标（targets_loader 每轮直读 DB，
    此处推 status 事件同步 GUI 状态栏）。"""
    ctx.events.status(ctx.poll_loop.status())


def handle_list_targets(ctx, rid, params) -> dict:
    return _ok(rid, targets=[_serialize(t) for t in ctx.targets.list()])


def handle_save_target(ctx, rid, params) -> dict:
    params = params or {}
    target_id = params.get("id")
    name = params.get("name")
    platform = params.get("platform")
    url = params.get("url")
    enabled = bool(params.get("enabled", True))
    # 密钥边界：入参不接受 secret 明文；GUI 直写 Keychain 后仅传 hint（尾 4 位）
    secret_hint = params.get("secret_hint") or None

    fields = _validate(name, platform, url)
    if fields:
        return _error(rid, "VALIDATION", "目标参数校验失败", fields=fields)

    try:
        if target_id:
            target = ctx.targets.update(target_id, name=name.strip(),
                                        url=url, secret_hint=secret_hint)
        else:
            target = ctx.targets.create(name.strip(), platform, url,
                                        secret_hint=secret_hint)
            if not enabled:
                ctx.targets.set_enabled(target.id, False)
                target = ctx.targets.get(target.id)
    except ValidationError as exc:
        return _error(rid, "VALIDATION", str(exc), fields=["name"])

    _notify_targets_changed(ctx)
    return _ok(rid, target=_serialize(target))


def handle_delete_target(ctx, rid, params) -> dict:
    params = params or {}
    target_id = params.get("id")
    if not target_id:
        return _error(rid, "VALIDATION", "缺少 id", fields=["id"])
    try:
        # 级联清理 secret_refs 引用行（Keychain 条目由 GUI 侧删除，9.7）
        ctx.targets.delete(target_id)
    except ValidationError as exc:
        return _error(rid, "VALIDATION", str(exc), fields=["id"])
    _notify_targets_changed(ctx)
    return _ok(rid, deleted=target_id)


def handle_set_target_enabled(ctx, rid, params) -> dict:
    params = params or {}
    target_id = params.get("id")
    if not target_id:
        return _error(rid, "VALIDATION", "缺少 id", fields=["id"])
    try:
        ctx.targets.set_enabled(target_id, bool(params.get("enabled")))
        target = ctx.targets.get(target_id)
    except ValidationError as exc:
        return _error(rid, "VALIDATION", str(exc), fields=["id"])
    assert target is not None
    _notify_targets_changed(ctx)
    return _ok(rid, target=_serialize(target))


def register(server, add) -> None:
    add("list_targets", handle_list_targets)
    add("save_target", handle_save_target)
    add("delete_target", handle_delete_target)
    add("set_target_enabled", handle_set_target_enabled)
