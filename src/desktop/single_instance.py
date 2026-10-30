# single_instance.py — 单实例与二次启动聚焦（tasks 9.12, Trace: D-FE-005）
#
# D12：Application Support 上的 flock 锁 + $TMPDIR Unix socket 转发 focus；
# socket 残留/损坏 → 删除重建（防御）。

from __future__ import annotations

import fcntl
import json
import os
import socket
from pathlib import Path

LOCK_PATH = Path.home() / "Library/Application Support/SMSRelay/instance.lock"
SOCK_PATH = Path(os.environ.get("TMPDIR", "/tmp")) / "sms-relay.focus.sock"


class SingleInstance:
    """持有锁则为本实例；acquire() 失败 → 已有实例运行。"""

    def __init__(self, lock_path: Path = LOCK_PATH, sock_path: Path = SOCK_PATH):
        self._lock_path = lock_path
        self._sock_path = sock_path
        self._lock_file = None

    def acquire(self) -> bool:
        """获取单实例锁；成功返回 True，已有实例返回 False。"""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = open(self._lock_path, "a+")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            self._lock_file = None
            return False
        return True

    def notify_existing(self) -> bool:
        """向已运行实例发送 focus 消息；成功返回 True。"""
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(2)
            client.connect(str(self._sock_path))
            client.sendall(json.dumps({"cmd": "focus"}).encode("utf-8"))
            client.close()
            return True
        except OSError:
            return False

    def start_focus_server(self, on_focus) -> None:
        """本实例（持锁方）启动 focus socket 监听线程。"""
        import threading

        def _serve() -> None:
            try:
                if self._sock_path.exists():
                    self._sock_path.unlink()  # 残留 sock 删除重建
            except OSError:
                pass
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                server.bind(str(self._sock_path))
                server.listen(1)
                while True:
                    conn, _ = server.accept()
                    try:
                        data = conn.recv(1024)
                        req = json.loads(data.decode("utf-8"))
                        if req.get("cmd") == "focus":
                            on_focus()
                    except (ValueError, OSError):
                        pass  # 损坏消息忽略
                    finally:
                        conn.close()
            except OSError:
                pass  # socket 不可用（不阻断主流程）
            finally:
                server.close()

        threading.Thread(target=_serve, daemon=True, name="focus-socket").start()
