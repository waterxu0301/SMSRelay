# poll_loop.py — 轮询调度组件（tasks 4T.1, Trace: D-TASK-001）
#
# design §6.2⑤ + sms_relay.py:141-191 主循环：
# sleep(interval，可被 scan_now Event 唤醒) → read_new(watermark) → Extractor
# + FilterChain → dispatch → WatermarkRepo.advance。
# 异常分类：DbReadDenied/DbLocked → WARN 下轮重试（监听不中断）；DbMissing → degraded。
# 幂等：running 标志 + 单例线程；scan_once 持锁，扫描中返回 busy。

from __future__ import annotations

import threading
import time
from typing import Callable

from relaycore.dao.chat_db import ChatDbRepository, DbLocked, DbMissing, DbReadDenied
from relaycore.dao.state import ConfigRepo, WatermarkRepo
from relaycore.model import SmsRecord
from relaycore.service.dispatch import DeliveryDispatcher
from relaycore.service.extractor import extract
from relaycore.service.filter_chain import FilterChain

TARGETS_LOADER = Callable[[], list]  # () -> list[PushTarget]（启用目标，热加载）


class PollLoop:
    def __init__(self, chat_db: ChatDbRepository, watermark: WatermarkRepo,
                 config: ConfigRepo, filter_chain: FilterChain,
                 dispatcher: DeliveryDispatcher, targets_loader: TARGETS_LOADER,
                 emit_status: Callable[[], None] | None = None,
                 emit_log: Callable[[str, str, str], None] | None = None,
                 interval_fn: Callable[[], float] | None = None):
        self._chat_db = chat_db
        self._watermark = watermark
        self._config = config
        self._filter = filter_chain
        self._dispatcher = dispatcher
        self._targets_loader = targets_loader
        self._emit_status = emit_status or (lambda: None)
        self._emit_log = emit_log or (lambda level, code, msg: None)
        self._interval_fn = interval_fn or self._default_interval

        self.running = False
        self.degraded = False          # DbMissing → True（停止扫描线程）
        self.processed_count = 0
        self._started_at: float | None = None
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._scan_lock = threading.Lock()
        self._wm_write_failures = 0    # 水位线连续写失败计数（3 次告警）
        # 轮询线程死因诊断（change fix-poll-loop-silent-death）：
        # 线程曾因未捕获异常静默死亡（stderr 被 DEVNULL 吞掉、run_log 无记录），
        # 而 running 残留 True 使界面持续显示「监听中」。死因留痕供诊断。
        self._dead_reason: str | None = None

    def _default_interval(self) -> float:
        try:
            return max(1.0, float(self._config.get("interval", "3")))
        except (TypeError, ValueError):
            return 3.0

    # ---------- 控制 ----------
    def _thread_alive(self) -> bool:
        """轮询线程是否真实存活（change fix-poll-loop-silent-death）。

        `running` 只是用户意图标志；线程可能因未预期异常已死。
        `status()` 的 listening 必须以本判定为准，否则界面会停留在
        错误的「监听中」。
        """
        t = self._thread
        return t is not None and t.is_alive()

    def _spawn_thread(self) -> None:
        """创建并启动轮询线程（set_running 与自愈分支共用）。"""
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="poll-loop")
        self._thread.start()

    def set_running(self, on: bool) -> bool:
        """幂等启停；返回是否发生状态变化。

        change fix-poll-loop-silent-death：原实现在 `running == on` 时直接
        短路返回 False——若 running 已为 True 但轮询线程已死，用户再次点击
        「开始监听」无法恢复。现改为：目标态为 True 且线程已死时重建线程。
        """
        if self.running == on:
            if on and not self._thread_alive():
                # running 已真但线程已死 → 重建（自愈）
                self._started_at = time.time()
                self._spawn_thread()
                self._emit_status()
                return True
            return False
        self.running = on
        if on:
            self.degraded = False
            self._started_at = time.time()
            if not self._thread_alive():
                self._spawn_thread()
        else:
            self._wake.set()  # 唤醒以便线程退出
        self._emit_status()
        return True

    def scan_once(self, force: bool = True) -> dict:
        """立即扫描一次；扫描中返回 busy（不重复投递）。"""
        if not self._scan_lock.acquire(blocking=False):
            return {"busy": True}
        try:
            processed = self._scan()
            return {"busy": False, "processed": processed}
        finally:
            self._scan_lock.release()
            self._emit_status()

    def status(self) -> dict:
        # change fix-poll-loop-silent-death：listening 语义从「用户意图」收紧为
        # 「轮询线程真实存活」——界面以此为准，线程已死时不得再显示「监听中」。
        return {
            "listening": self.running and self._thread_alive(),
            "degraded": self.degraded,
            "interval": self._interval_fn(),
            "watermark": self._safe_watermark(),
            "processed_count": self.processed_count,
            "buffered_codes": len(self._dispatcher.buffer_entries()),
            "uptime_s": (time.time() - self._started_at) if self._started_at else 0,
        }

    def _safe_watermark(self) -> int:
        try:
            return self._watermark.get()
        except Exception:
            return -1

    # ---------- 主循环 ----------
    def _loop(self) -> None:
        """轮询主循环（change fix-poll-loop-silent-death：异常永不退出）。

        原实现无 try/except：单轮 scan_once() 的任何逃逸异常都会杀死线程，
        而 running 残留 True 使界面持续显示「监听中」（实测 2026-10-29）。
        且子进程 stderr 被 DEVNULL 吞掉，traceback 与 run_log 双不可见。
        现改为：异常记 WARN 后按 interval 继续下一轮，仅 running/degraded
        变化时退出。
        """
        reason = "stopped"
        while self.running and not self.degraded:
            try:
                self.scan_once()
            except Exception as exc:  # 兜底：轮询线程不得因单轮异常死亡
                self._log("WARN", "POLL_LOOP_ERROR",
                          f"轮询异常，下轮重试: {type(exc).__name__}: {exc}")
            if not self.running or self.degraded:
                reason = "degraded" if self.degraded else "stopped"
                break
            self._wake.wait(timeout=self._interval_fn())
            self._wake.clear()
        # while 条件假（含进入前即 degraded）也要记对死因
        self._dead_reason = "degraded" if self.degraded else reason

    def _scan(self) -> int:
        """单轮扫描：返回处理条数（含被过滤的）。"""
        if self.degraded:
            return 0
        watermark = self._safe_watermark()
        if watermark < 0:
            watermark = 0
        try:
            records = self._chat_db.read_new(watermark)
        except DbReadDenied as exc:
            # 授权被撤销：监听保持存活，下轮重试（spec 场景）
            self._log("WARN", "SCAN_AUTH_DENIED", f"chat.db 读取被拒，下轮重试: {exc}")
            return 0
        except DbLocked as exc:
            self._log("WARN", "SCAN_LOCKED", f"chat.db 被占用，下轮重试: {exc}")
            return 0
        except DbMissing:
            self.degraded = True
            self._log("ERROR", "SCAN_DB_MISSING", "chat.db 缺失，扫描已暂停")
            self._emit_status()
            return 0
        except Exception as exc:  # 兜底：不崩溃
            self._log("WARN", "SCAN_FAIL", f"扫描异常，下轮重试: {exc}")
            return 0

        # change fix-poll-loop-silent-death：配置与目标原在 try 之外裸露，
        # 任一异常都会逃逸 _scan → 杀死轮询线程。现纳入保护：本轮跳过、
        # 记 WARN、下轮重试（与 read_new 失败处理策略一致）。
        try:
            cfg = self._config.get_all()
            targets = self._targets_loader()
        except Exception as exc:
            self._log("WARN", "SCAN_CONFIG_FAIL",
                      f"配置/目标读取失败，本轮跳过: {type(exc).__name__}: {exc}")
            return 0

        max_rowid = watermark
        for record in records:
            max_rowid = max(max_rowid, record.rowid)
            # 单条消息失败隔离：不阻断同轮其余消息，也不卡住水位线
            try:
                code = extract(record.text)
                if code is not None:
                    decision = self._filter.apply(record, cfg)
                    if decision.accept:
                        self._dispatcher.remember(record, code)
                        self._dispatcher.dispatch(record, code, targets)
                    else:
                        # 跳过仅记录（含静默时段：spec 静默仅记录）
                        level = ("WARN" if decision.reason == "EMPTY_WHITELIST"
                                 else "INFO")
                        self._log(level, f"SKIP_{decision.reason}",
                                  f"消息 {record.rowid} 未投递: {decision.reason}")
                    self.processed_count += 1
            except Exception as exc:
                self._log("WARN", "MSG_PROCESS_FAIL",
                          f"消息 {record.rowid} 处理失败，跳过: "
                          f"{type(exc).__name__}: {exc}")
            # 水位线推进移到循环末尾：坏消息与非验证码消息也必须推进，
            # 否则同一 ROWID 每轮重复读取（_advance_watermark 自身已有
            # try/except 保护写失败）
            self._advance_watermark(max_rowid)
        if records:
            self._emit_status()
        return len(records)

    def _advance_watermark(self, value: int) -> None:
        """水位线推进；写失败 → 内存继续 + 每轮重试，连续 3 次告警。"""
        try:
            if not self._watermark.advance(value) and value > self._safe_watermark():
                # 只升不降层返回 False 且数据源更大 → 异常，仅记录不强制
                pass
            self._wm_write_failures = 0
        except Exception as exc:
            self._wm_write_failures += 1
            self._log("WARN", "WATERMARK_WRITE_FAIL",
                      f"水位线落盘失败({self._wm_write_failures}/3): {exc}")
            if self._wm_write_failures >= 3:
                self._log("ERROR", "WATERMARK_WRITE_FAIL_3X",
                          "水位线连续 3 次写入失败，请检查存储")

    def _log(self, level: str, code: str, message: str) -> None:
        self._emit_log(level, code, message)
