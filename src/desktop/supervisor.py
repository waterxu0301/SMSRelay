# supervisor.py — 核心子进程守护（tasks 9.13, Trace: D-FE-006）
#
# design D2：EOF/非零退出 → 状态栏「监听不可用」→ 1/2/4s 退避拉起（最多 3 次）；
# 连续 3 次失败 → unavailable 态 + 手动重试；不得静默停留在「看似监听中」。

from __future__ import annotations

import sys
import threading
from typing import Callable

from desktop.ipc_client import IpcClient

BACKOFF_SEQUENCE = (1.0, 2.0, 4.0)
MAX_RESTARTS = 3


class Supervisor:
    """管理 IpcClient 生命周期：崩溃退避拉起、重连回调、退出流程。"""

    def __init__(self, module: str = "relaycore.main",
                 dispatcher: Callable[[Callable], None] | None = None,
                 on_status: Callable[[str], None] | None = None,
                 on_event: Callable[[str, dict], None] | None = None,
                 on_ready: Callable[[], None] | None = None):
        """
        on_status(state)：'running' | 'restarting' | 'unavailable'（主线程回调）；
        on_ready()：child_ready 事件到达（重连成功提示用）。
        """
        if getattr(sys, "frozen", False):
            # py2app bundle 态：sys.executable 指向 stub 解释器（MacOS/python），
            # 不透传自定义参数（实测报 unknown option）。须从 Info.plist 取
            # CFBundleExecutable 定位真正的 app 二进制，经 --relaycore-child
            # 参数进入监听核心入口（app.main 分发）。
            import plistlib
            from pathlib import Path
            contents = Path(sys.executable).resolve().parent.parent
            plist = plistlib.loads((contents / "Info.plist").read_bytes())
            exe = contents / "MacOS" / plist["CFBundleExecutable"]
            self._cmd = [str(exe), "-u", "--relaycore-child"]
        else:
            self._cmd = [sys.executable, "-u", "-m", module]
        self._dispatcher = dispatcher or (lambda fn: fn())
        self._on_status = on_status or (lambda s: None)
        self._on_event = on_event or (lambda e, m: None)
        self._on_ready = on_ready or (lambda: None)
        self._restart_count = 0
        self._had_crash = False
        self._client: IpcClient | None = None
        self._stopped = False

    @property
    def client(self) -> IpcClient | None:
        return self._client

    def start(self) -> None:
        """首次拉起子进程。"""
        self._spawn()

    def manual_retry(self) -> None:
        """unavailable 态下用户点击「手动重试」：重置计数再拉起。"""
        self._restart_count = 0
        self._stopped = False
        self._on_status("restarting")
        self._spawn()

    def shutdown(self) -> None:
        """退出流程：先 stop → terminate → kill（IpcClient.stop 内含）。"""
        self._stopped = True
        if self._client:
            try:
                self._client.request("stop", timeout=2.0)
            except Exception:
                pass
            self._client.stop()

    # ---------- 内部 ----------
    def _spawn(self) -> None:
        def _on_event(evt: str, payload: dict) -> None:
            if evt == "child_ready":
                if self._restart_count > 0:
                    self._had_crash = True
                self._restart_count = 0
                self._on_status("running")
                self._on_ready()
            self._on_event(evt, payload)

        def _on_exit(returncode: int | None) -> None:
            # 断开瞬间即刻改态（spec：不得静默停留在看似监听中）
            if self._stopped:
                return
            self._had_crash = True
            if self._restart_count >= MAX_RESTARTS:
                self._on_status("unavailable")
                return
            self._restart_count += 1
            self._on_status("restarting")
            delay = BACKOFF_SEQUENCE[min(self._restart_count - 1,
                                         len(BACKOFF_SEQUENCE) - 1)]
            threading.Timer(delay, self._spawn).start()

        self._client = IpcClient(
            self._cmd, self._dispatcher, _on_event, _on_exit)
        self._client.start()

    @property
    def had_crash(self) -> bool:
        """是否曾发生异常退出（重连后提示用）。"""
        return self._had_crash
