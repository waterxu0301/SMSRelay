# server.py — IpcServer 协议框架与命令路由（tasks 5.1, Trace: D-PSV-001）
#
# design §6.4 路由表 + §7 协议：stdin 逐行 JSON；请求校验 proto==1 与 id；
# 响应统一单行 JSON 含 rid；未知 cmd → error(UNKNOWN_CMD)；
# 启动即发 child_ready 事件；非法 JSON → error(BAD_REQUEST) 不崩溃。

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, TextIO

from relaycore.dao.chat_db import ChatDbRepository
from relaycore.dao.state import ConfigRepo, EmailConfigRepo, LogRepo, StatsRepo, TargetsRepo, WatermarkRepo
from relaycore.ipc.events import PROTOCOL, EventEmitter
from relaycore.poll_loop import PollLoop
from relaycore.service.dispatch import DeliveryDispatcher


@dataclass
class AppContext:
    """IPC 处理器共享的上下文（由 relaycore.main 组装）。"""
    chat_db: ChatDbRepository
    watermark: WatermarkRepo
    config: ConfigRepo
    targets: TargetsRepo
    email_config: EmailConfigRepo
    logs: LogRepo
    stats: StatsRepo
    poll_loop: PollLoop
    dispatcher: DeliveryDispatcher
    events: EventEmitter
    gateways: dict = field(default_factory=dict)   # platform -> Gateway
    started_at: float = 0.0


def _error(rid, code: str, message: str = "", fields: list | None = None) -> dict:
    err = {"code": code, "message": message}
    if fields:
        err["fields"] = fields
    return {"proto": PROTOCOL, "rid": rid, "ok": False, "error": err}


def _ok(rid, **kwargs) -> dict:
    return {"proto": PROTOCOL, "rid": rid, "ok": True, **kwargs}


class IpcServer:
    """命令分发表：dict[cmd -> handler(ctx, rid, params) -> response dict]。"""

    def __init__(self, ctx: AppContext, stdin: TextIO):
        self._ctx = ctx
        self._stdin = stdin
        self._handlers: dict[str, Callable] = {}
        self._register_all()

    def _register(self, cmd: str, handler: Callable) -> None:
        self._handlers[cmd] = handler

    def _register_all(self) -> None:
        from relaycore.ipc import (handlers_config, handlers_control,
                                   handlers_push, handlers_query, handlers_targets)
        handlers_control.register(self, self._register)
        handlers_config.register(self, self._register)
        handlers_targets.register(self, self._register)
        handlers_push.register(self, self._register)
        handlers_query.register(self, self._register)

    @property
    def commands(self) -> list[str]:
        return sorted(self._handlers)

    def handle_line(self, line: str) -> dict | None:
        """处理一行 stdin 输入，返回响应 dict（None 表示无响应）。"""
        try:
            req = json.loads(line)
        except ValueError:
            return _error(None, "BAD_REQUEST", "非法 JSON")
        if not isinstance(req, dict):
            return _error(None, "BAD_REQUEST", "请求必须是 JSON 对象")

        rid = req.get("id")
        if rid is None:
            return _error(None, "BAD_REQUEST", "缺少 id")
        if req.get("proto") != PROTOCOL:
            return _error(rid, "BAD_REQUEST", f"proto 必须为 {PROTOCOL}")

        cmd = req.get("cmd")
        handler = self._handlers.get(cmd)
        if handler is None:
            return _error(rid, "UNKNOWN_CMD", f"未知命令: {cmd}")

        params = req.get("params") or {}
        try:
            return handler(self._ctx, rid, params)
        except Exception as exc:  # 兜底：处理器异常不得崩溃主循环
            return _error(rid, "INTERNAL", str(exc))

    def serve_forever(self) -> None:
        """stdin 逐行服务；EOF（GUI 退出）→ 返回。启动即发 child_ready。"""
        self._ctx.events.child_ready({"commands": self.commands})
        for line in self._stdin:
            line = line.strip()
            if not line:
                continue
            resp = self.handle_line(line)
            if resp is not None:
                self._write(resp)
            if self._ctx.events.broken:
                break  # GUI 已断开 stdout，退出

    def _write(self, resp: dict) -> None:
        # 经 EventEmitter.write_line 持锁原子写，避免与事件流并发撕裂行
        self._ctx.events.write_line(json.dumps(resp, ensure_ascii=False))
