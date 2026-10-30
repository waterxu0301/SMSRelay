# events.py — 事件发射器（tasks 5.7, Trace: D-PSV-010）
#
# 线程安全（锁保护 stdout）：所有 PollLoop/Dispatcher/AlertManager 线程经此发事件；
# stdout 写失败（GUI 已退出）→ 静默停止发射，监听核心继续运行不崩溃。
# 事件类型：status / log / scan_result / delivery / alert / error / child_ready。

from __future__ import annotations

import json
import sys
import threading
import time
from typing import TextIO

PROTOCOL = 1


class EventEmitter:
    def __init__(self, stream: TextIO | None = None):
        self._stream = stream if stream is not None else sys.stdout
        self._lock = threading.Lock()
        self._broken = False  # GUI 退出后置 True，静默停发

    def emit(self, evt: str, payload: dict | None = None) -> None:
        if self._broken:
            return
        message = {"proto": PROTOCOL, "evt": evt}
        if payload:
            message.update(payload)
        line = json.dumps(message, ensure_ascii=False)
        self.write_line(line)

    def write_line(self, line: str) -> None:
        """写一行原始文本（不含换行）到 stdout 并 flush。

        所有 stdout 写入必须经此方法（持锁原子写）：IpcServer 响应与事件
        共用同一 stdout，并发直写会撕裂多字节 UTF-8 字符（fix-ipc-read-thread-wedge）。
        """
        with self._lock:
            try:
                self._stream.write(line + "\n")
                self._stream.flush()
            except (BrokenPipeError, ValueError, OSError):
                self._broken = True  # 静默停止，不抛异常

    # 便捷方法（各事件类型字段固定）
    def log(self, level: str, code: str, message: str) -> None:
        self.emit("log", {"level": level, "code": code,
                          "ts": int(time.time()), "message": message})

    def status(self, payload: dict) -> None:
        self.emit("status", payload)

    def delivery(self, msg_rowid: int, target_id: str, ok: bool,
                 attempts: int, reason: str | None) -> None:
        self.emit("delivery", {"msg_rowid": msg_rowid, "target_id": target_id,
                               "ok": ok, "attempts": attempts, "reason": reason})

    def alert(self, event_key: str, targets: list[str], reason: str,
              deduped: bool) -> None:
        self.emit("alert", {"event_key": event_key, "targets": targets,
                            "reason": reason, "deduped": deduped})

    def child_ready(self, capabilities: dict) -> None:
        self.emit("child_ready", {"capabilities": capabilities})

    @property
    def broken(self) -> bool:
        return self._broken
