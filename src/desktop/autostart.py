# autostart.py — 开机自启（tasks 9.8, Trace: D-FE-010）
#
# design D4：SMAppService.mainApp().register()/unregister()（macOS 13+ 标准
# 登录项）；status() 驱动界面开关真实态；dev 态（非 .app bundle）→ unsupported，
# 界面说明打包后生效；全程无 LaunchAgent plist（4T.3 验收）。
# 操作结果由 GUI 经 IPC set_autostart 通知子进程记 run_log（5.6 第 4 点）。

from __future__ import annotations

import sys

# SMAppServiceStatus 枚举（PyObjC 返回 NSInteger）
_STATUS_NAMES = {
    0: "notRegistered",
    1: "enabled",
    2: "requiresApproval",
    3: "notFound",
}


def _in_app_bundle() -> bool:
    """py2app 打包态判定：可执行文件位于 .app/Contents/MacOS 下。"""
    return ".app/Contents/" in sys.executable


def _service():
    """取 SMAppService.mainApp()；PyObjC 不可用或非 bundle → None。"""
    if not _in_app_bundle():
        return None
    try:
        from ServiceManagement import SMAppService
    except ImportError:
        return None
    try:
        return SMAppService.mainApp()
    except Exception:
        return None


class AutostartService:
    """开机自启开关（GUI 进程内直接调 SMAppService）。"""

    @staticmethod
    def status() -> str:
        """enabled / notRegistered / requiresApproval / notFound / unsupported。"""
        svc = _service()
        if svc is None:
            return "unsupported"
        try:
            return _STATUS_NAMES.get(int(svc.status()), "notRegistered")
        except Exception:
            return "unsupported"

    @staticmethod
    def set_enabled(enabled: bool) -> tuple[bool, str]:
        """注册/注销登录项；返回 (是否成功, 状态或错误说明)。"""
        svc = _service()
        if svc is None:
            return False, "unsupported"
        try:
            if enabled:
                svc.register_()  # PyObjC：selector register → register_
            else:
                svc.unregister()
        except Exception as exc:
            return False, str(exc)
        return True, AutostartService.status()
