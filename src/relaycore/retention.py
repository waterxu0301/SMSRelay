# retention.py — 7 天滚动清理组件（tasks 4T.2, Trace: D-TASK-002）
#
# 独立低优先级线程，每小时执行 LogRepo.purge_older_than_7d()；
# purge 按时间删除天然幂等；异常 → run_log WARN，不影响 PollLoop。

from __future__ import annotations

import threading
import time

from relaycore.dao.state import LogRepo

INTERVAL_SECONDS = 3600


class RetentionWorker:
    def __init__(self, log_repo: LogRepo, interval: float = INTERVAL_SECONDS):
        self._log = log_repo
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return  # 幂等
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="retention-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def run_once(self) -> int:
        """执行一次清理，返回删除行数；异常不外抛。"""
        try:
            return self._log.purge_older_than_7d()
        except Exception as exc:
            try:
                self._log.append_run("WARN", "RETENTION_FAIL", f"日志清理失败: {exc}")
            except Exception:
                pass  # 清理线程异常不得传播
            return 0

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self.run_once()
