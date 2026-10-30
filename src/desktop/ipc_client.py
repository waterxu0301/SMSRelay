# ipc_client.py — GUI↔核心 IPC 客户端（tasks 9.1, Trace: D-FE-007）
#
# design §7：写线程（请求队列 → 子进程 stdin，自增 id + Future 按 rid 配对）
# + 读线程（stdout 逐行 JSON → 按 evt/rid 分发；回调经调度器回主线程）；
# 请求超时（5s）→ error(code='TIMEOUT')；非法响应行不崩溃。

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from typing import Callable

PROTOCOL = 1
REQUEST_TIMEOUT = 5.0


class IpcClient:
    """子进程 stdio JSON Lines 客户端。dispatcher 把回调投递回 Tk 主线程。"""

    def __init__(self, cmd: list[str], dispatcher: Callable[[Callable], None],
                 on_event: Callable[[str, dict], None],
                 on_exit: Callable[[int | None], None]):
        """
        dispatcher(fn)：在 Tk 主线程执行 fn（root.after(0, fn)）；
        on_event(evt, payload)：事件回调（已在主线程）；
        on_exit(returncode)：子进程退出回调（已在主线程）。
        """
        self._dispatcher = dispatcher
        self._on_event = on_event
        self._on_exit = on_exit
        self._pending: dict[str, dict] = {}   # rid -> {"future": Future-like, "event": Event}
        self._pending_lock = threading.Lock()
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._out_queue: queue.Queue = queue.Queue()
        self._proc: subprocess.Popen | None = None
        self._cmd = cmd
        self._alive = False
        # 写线程自愈（change fix-ipc-write-loop-death）：线程引用 + 死因诊断 + 重建锁。
        # 背景：写线程曾因未预期异常静默死亡，此后请求全部 5s 超时，而读线程仍存活
        # 使界面看似正常 —— 用户点击「开始监听」的 start 从未到达核心。
        self._write_thread: threading.Thread | None = None
        self._write_dead_reason: str | None = None
        self._write_lock = threading.Lock()
        # 读线程自愈（change fix-ipc-read-thread-wedge）：读线程曾因非法 UTF-8
        # 字节（UnicodeDecodeError ⊂ ValueError）掉入 EOF 分支，阻塞在无限期
        # proc.wait() 上永久 wedged —— 核心活着但 GUI 收不到任何响应/事件。
        # 与写线程对称：线程引用 + 死因诊断 + 按需重建。
        self._read_thread: threading.Thread | None = None
        self._read_dead_reason: str | None = None
        self._read_lock = threading.Lock()

    # ---------- 生命周期 ----------
    def start(self) -> None:
        # 二进制管道（fix-ipc-read-thread-wedge）：文本模式 readline 遇非法
        # UTF-8 字节抛 UnicodeDecodeError 会损坏流游标；二进制模式下由读侧
        # 逐行 decode("utf-8", "replace") 容错，游标永不损坏。
        self._proc = subprocess.Popen(
            self._cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._alive = True
        self._ensure_write_thread()
        self._ensure_read_thread()

    def stop(self) -> None:
        # 先置 _alive=False：_ensure_write_thread 的硬闸门，确保停止流程后
        # 不会被后续请求触发写线程重建（change fix-ipc-write-loop-death）
        self._alive = False
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    @property
    def running(self) -> bool:
        return self._alive and self._proc is not None and self._proc.poll() is None

    # ---------- 请求 ----------
    def request(self, cmd: str, params: dict | None = None,
                timeout: float = REQUEST_TIMEOUT) -> dict:
        """同步请求（后台线程调用，勿在主线程调用以免阻塞 Tk）。"""
        with self._id_lock:
            self._next_id += 1
            rid = str(self._next_id)
        entry = {"event": threading.Event(), "response": None}
        with self._pending_lock:
            self._pending[rid] = entry
        try:
            # 写线程自愈：若写线程已死亡，请求会进队列后无人消费直至超时。
            # 此处按需重建（幂等），使异常死亡后可恢复而非无限期超时。
            self._ensure_write_thread()
            # 读线程自愈：若读线程已死亡（含曾被解码异常杀死），响应无人
            # 消费，请求同样只会超时。对称地按需重建（fix-ipc-read-thread-wedge）。
            self._ensure_read_thread()
            self._out_queue.put(json.dumps(
                {"proto": PROTOCOL, "id": rid, "cmd": cmd, "params": params or {}},
                ensure_ascii=False))
            if not entry["event"].wait(timeout):
                # 超时：返回 error(TIMEOUT)（spec 9.1）
                return {"proto": PROTOCOL, "rid": rid, "ok": False,
                        "error": {"code": "TIMEOUT", "message": f"{cmd} 请求超时"}}
            return entry["response"] or {"proto": PROTOCOL, "rid": rid, "ok": False,
                                         "error": {"code": "INTERNAL", "message": "空响应"}}
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)

    def request_async(self, cmd: str, params: dict | None = None,
                      callback: Callable[[dict], None] | None = None) -> None:
        """异步请求：结果经 dispatcher 回主线程回调。"""

        def _worker() -> None:
            resp = self.request(cmd, params)
            if callback:
                # 与 _safe_dispatch 同理：主循环退出竞态不得杀死工作线程
                try:
                    self._dispatcher(lambda: callback(resp))
                except RuntimeError:
                    pass  # main thread is not in main loop（关闭中）

        threading.Thread(target=_worker, daemon=True, name=f"ipc-req-{cmd}").start()

    # ---------- 内部线程 ----------
    def _ensure_write_thread(self) -> None:
        """写线程缺失时重建（幂等；stop 后为硬闸门不重建）。

        change fix-ipc-write-loop-death：写线程曾因未预期异常静默死亡，
        导致此后所有请求超时。此处按需恢复，无常开轮询开销。
        """
        if not self._alive:
            return
        t = self._write_thread
        if t is not None and t.is_alive():
            return
        with self._write_lock:
            t = self._write_thread
            if t is not None and t.is_alive():
                return  # 双检：并发请求下只创建一个
            self._write_thread = threading.Thread(
                target=self._write_loop, daemon=True, name="ipc-write")
            self._write_thread.start()

    def _write_loop(self) -> None:
        proc = self._proc
        reason = "stopped"
        while self._alive:
            try:
                line = self._out_queue.get(timeout=0.2)
            except queue.Empty:
                if proc.poll() is not None:
                    reason = "child_exited"
                    break
                continue
            try:
                # 二进制 stdin（fix-ipc-read-thread-wedge）：显式 encode
                proc.stdin.write((line + "\n").encode("utf-8"))
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                reason = f"write_error: {type(exc).__name__}"
                break
        # 死因留痕：写线程死亡不能再是静默的（诊断用，不阻断任何路径）
        self._write_dead_reason = reason

    def _ensure_read_thread(self) -> None:
        """读线程缺失时重建（幂等；stop 后为硬闸门不重建）。

        change fix-ipc-read-thread-wedge：读线程曾因 UnicodeDecodeError 掉入
        EOF 分支阻塞在无限期 proc.wait() 上，此后所有响应/事件丢失、请求全部
        超时，而 is_alive() 恒 True 无法被双检发现 —— 故 EOF 路径改为限时
        wait，保证读线程必然退出，再由本方法按需恢复。
        """
        if not self._alive:
            return
        t = self._read_thread
        if t is not None and t.is_alive():
            return
        with self._read_lock:
            t = self._read_thread
            if t is not None and t.is_alive():
                return  # 双检：并发请求下只创建一个
            self._read_thread = threading.Thread(
                target=self._read_loop, daemon=True, name="ipc-read")
            self._read_thread.start()

    def _read_loop(self) -> None:
        proc = self._proc
        reason = "eof"
        try:
            # 二进制逐行读 + 容错解码（fix-ipc-read-thread-wedge）：
            # 非法 UTF-8 字节（如并发写撕裂的多字节字符）替换为 U+FFFD，
            # 解析失败仅丢弃该行，不抛异常、不损坏流游标。
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue  # 非法响应行不崩溃（spec 9.1）
                if not isinstance(msg, dict):
                    continue
                rid = msg.get("rid")
                if rid is not None and not msg.get("evt"):
                    # 响应：按 rid 配对（乱序响应正确路由）
                    with self._pending_lock:
                        entry = self._pending.get(str(rid))
                    if entry:
                        entry["response"] = msg
                        entry["event"].set()
                    continue
                evt = msg.get("evt")
                if evt:
                    # 事件投递必须容错：Tk 主循环未就绪/已退出时 root.after 抛
                    # RuntimeError（"main thread is not in main loop"）——若此处
                    # 不捕获，读线程会被杀死，此后所有 IPC 请求收不到响应而
                    # 全部超时（实测 2026-10-29：save_target 请求超时根因）。
                    self._safe_dispatch(lambda e=evt, m=msg: self._on_event(e, m))
        except (BrokenPipeError, OSError) as exc:
            reason = f"read_error: {type(exc).__name__}"  # stdout 关闭/异常
        except Exception as exc:  # 未预期异常不得静默（死因留痕）
            reason = f"unexpected: {type(exc).__name__}: {exc}"
        # EOF/异常后：限时等待子进程退出，核心仍活着（孤儿等待）时不得永久
        # 阻塞 —— 永久阻塞曾使读线程 wedged 且 is_alive() 恒 True（设计 D3）
        try:
            returncode = proc.wait(timeout=3)
            self._alive = False  # 子进程确实退出：会话终结
        except subprocess.TimeoutExpired:
            returncode = proc.poll()
            reason = f"child_alive_after_exit: {reason}"
            # 核心仍活着：保持 _alive=True，request() 可经 _ensure_read_thread
            # 重建读线程恢复会话（设计 D4 自愈，硬闸门仅在 stop 时关闭）
        self._read_dead_reason = reason
        with self._pending_lock:
            for entry in self._pending.values():
                entry["event"].set()  # 唤醒等待者（拿到 TIMEOUT/空响应）
        if not self._alive:
            self._safe_dispatch(lambda: self._on_exit(returncode))

    def _safe_dispatch(self, fn) -> None:
        """投递回调到 Tk 主线程；主循环不可用时静默丢弃（读线程不得因此死亡）。

        事件（status/log/delivery）在主循环未就绪期间到达属生命周期竞态：
        - 主循环启动前到达 → 丢弃可接受（首次状态由 get_status 校准拉回）；
        - 主循环退出后到达 → 应用正在关闭，丢弃正确。
        rid 响应不走本路径（_read_loop 直接置 event），不受影响。
        """
        try:
            self._dispatcher(fn)
        except RuntimeError:
            pass  # main thread is not in main loop
        except Exception:
            pass  # 主线程销毁竞态等：读线程生存优先于单次事件投递
