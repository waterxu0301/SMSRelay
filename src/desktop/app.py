# app.py — GUI 主进程入口（tasks 9.1, Trace: D-FE-007）
#
# 编排（design §6.3）：单实例检查（9.12）→ focus socket → Tk 主窗口（9.2）
# → Supervisor 拉起核心子进程（9.13）；关闭窗口仅隐藏至托盘；
# 退出流程：stop → terminate → kill（Supervisor/IpcClient 内含）。

from __future__ import annotations

import sys
from pathlib import Path

# 运行根为 sms-relay/（relaycore 与 desktop 均为顶层包）；
# py2app/源码两种运行方式下均保证包可导入。
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import tkinter as tk  # noqa: E402

from desktop.notify import NotifyService  # noqa: E402
from desktop.redaction import install_global_redaction  # noqa: E402
from desktop.single_instance import SingleInstance  # noqa: E402
from desktop.tray import Tray  # noqa: E402
from desktop.supervisor import Supervisor  # noqa: E402
from desktop.ui.main_window import MainWindow  # noqa: E402


def _focus_window(window: MainWindow) -> None:
    """二次启动聚焦：NSWorkspace 激活 + deiconify + lift（tasks 9.12）。"""
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except ImportError:
        pass  # dev 态无 PyObjC → 仅 Tk 层聚焦
    window.show()


def main() -> int:
    # py2app bundle 态子进程入口：Supervisor 在 frozen 态用
    # `sys.executable --relaycore-child` 复用 .app 二进制拉起监听核心
    if "--relaycore-child" in sys.argv[1:]:
        from relaycore.main import main as core_main
        return core_main()

    install_global_redaction()  # 全局日志脱敏（9.14）

    single = SingleInstance()
    if not single.acquire():
        # 已有实例 → 转发 focus 后自身退出（exit 0）
        single.notify_existing()
        return 0

    root = tk.Tk()
    window = MainWindow(root)

    def _dispatch(fn) -> None:
        # IPC 回调 → Tk 主线程（Tk 控制只在主线程）
        root.after(0, fn)

    supervisor = Supervisor(
        module="relaycore.main",
        dispatcher=_dispatch,
        on_status=window.set_core_status,
        on_event=window.handle_event,
        on_ready=window.on_core_ready,
    )
    window.set_supervisor(supervisor)

    # root 建好后才拉起核心（child_ready 事件有主窗口可投递）
    single.start_focus_server(lambda: _dispatch(lambda: _focus_window(window)))
    supervisor.start()

    tray = Tray(window)  # 系统托盘（9.9；PyObjC 不可用安全降级不建托盘）
    window.set_tray(tray)  # 授权引导态置灰监听菜单（9.10）

    notify = NotifyService(window)  # 系统通知（9.11；仅告警）
    notify.request_auth()  # 启动时请求通知权限（dev 态自动跳过）

    try:
        root.mainloop()
    finally:
        supervisor.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
