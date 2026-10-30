# tray.py — 系统托盘（tasks 9.9, Trace: D-FE-002）
#
# design §6.3 托盘行：NSStatusItem 四项菜单 = 打开主界面 / 修改配置
# （开窗并定位配置区）/ 开始或停止监听（动态标题，经 IPC start/stop）/
# 退出（先 stop → 子进程退出 → destroy，走 window.quit_app）；
# 关窗仅 withdraw()，监听不中断；图标随监听状态变体；
# dev 态无 .app bundle → 文本菜单项降级（PyObjC 不可用则不建托盘）。

from __future__ import annotations

import sys

try:
    import objc
    from AppKit import (NSMenu, NSMenuItem, NSStatusBar, NSObject)
    HAVE_APPKIT = True
except ImportError:  # dev 环境未装 PyObjC → 无托盘，主窗口功能不受影响
    HAVE_APPKIT = False
    objc = None
    NSObject = object  # 保持类定义语法合法

_OPEN_SEL = b"openMain:"
_CONFIG_SEL = b"openConfig:"
_TOGGLE_SEL = b"toggleListen:"
_QUIT_SEL = b"quitApp:"


def _in_app_bundle() -> bool:
    return ".app/Contents/" in sys.executable


class _MenuHandler(NSObject if HAVE_APPKIT else object):
    """菜单动作桥（PyObjC target-action；selector 由方法名推导）。"""

    def init(self):
        if HAVE_APPKIT:
            self = objc.super(_MenuHandler, self).init()
            if self is None:
                return None
        self._tray = None
        return self

    def set_tray(self, tray) -> None:
        # 方法名必须是 snake_case（含下划线）：PyObjC 将 camelCase 名按 ObjC
        # selector 推导参数数，与 Python 签名冲突即 BadPrototypeError（实测：
        # bind/attach/setTray/link 均撞名，含下划线名被保留为纯 Python 方法）
        self._tray = tray

    # PyObjC 暴露为 ObjC selector（末尾下划线 → 冒号）
    def openMain_(self, sender):
        if self._tray:
            self._tray.open_main()

    def openConfig_(self, sender):
        if self._tray:
            self._tray.open_config()

    def toggleListen_(self, sender):
        if self._tray:
            self._tray.toggle_listen()

    def quitApp_(self, sender):
        if self._tray:
            self._tray.quit_app()


class Tray:
    """NSStatusItem 托盘：监听状态同步 + 四项菜单。"""

    def __init__(self, window):
        self._window = window
        self._listening = False
        self._item = None
        self._listen_item = None

        window.on("status", self._on_status)

        if not HAVE_APPKIT:
            return
        try:
            self._build()
        except Exception:
            self._item = None  # 托盘失败不阻断主窗口
            return
        window.request("get_status", None,
                       lambda r: r.get("ok") and self._on_status(r))

    # ---------- 构建 ----------
    def _build(self) -> None:
        status_bar = NSStatusBar.systemStatusBar()
        self._item = status_bar.statusItemWithLength_(-1)  # 可变长度
        button = self._item.button()
        if _in_app_bundle() and button is not None:
            from AppKit import NSImage
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                "bell.fill", "SMS 接力")
            if image is not None:
                image.setTemplate_(True)
                button.setImage_(image)
            else:
                button.setTitle_("SMS 接力")
        else:
            button.setTitle_("SMS 接力")  # dev 态文本降级

        self._handler = _MenuHandler.alloc().init()
        self._handler.set_tray(self)

        menu = NSMenu.alloc().init()
        self._add_item(menu, "打开主界面", _OPEN_SEL)
        self._add_item(menu, "修改配置", _CONFIG_SEL)
        self._listen_item = self._add_item(menu, "开始监听", _TOGGLE_SEL)
        menu.addItem_(NSMenuItem.separatorItem())
        self._add_item(menu, "退出", _QUIT_SEL)
        self._item.setMenu_(menu)

    def _add_item(self, menu, title: str, selector: bytes):
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, None, "")
        item.setAction_(selector)
        item.setTarget_(self._handler)  # target-action 指向桥接实例
        menu.addItem_(item)
        return item

    # ---------- 状态同步 ----------
    def _on_status(self, payload: dict) -> None:
        listening = bool(payload.get("listening"))
        self._set_listening(listening)

    def _set_listening(self, listening: bool) -> None:
        self._listening = listening
        if self._listen_item is not None:
            self._listen_item.setTitle_(
                "停止监听" if listening else "开始监听")
        button = self._item.button() if self._item is not None else None
        if button is not None and not _in_app_bundle():
            button.setTitle_(f"接力·{'运行' if listening else '停'}")

    def set_listen_enabled(self, enabled: bool) -> None:
        """授权引导态置灰「开始监听」（tasks 9.10）。"""
        if self._listen_item is not None:
            self._listen_item.setEnabled_(bool(enabled))

    # ---------- 菜单动作 ----------
    def open_main(self) -> None:
        self._window.show()

    def open_config(self) -> None:
        """修改配置：开窗并定位配置区（过滤与免打扰页签）。"""
        self._window.show()
        self._window._notebook.select(self._window.frames["filters"])

    def toggle_listen(self) -> None:
        # 乐观更新动态标题，status 事件/响应回执兜底纠正
        want = not self._listening
        self._set_listening(want)

        def done(resp: dict) -> None:
            if resp.get("ok"):
                self._set_listening(bool(resp.get("listening")))
            else:
                self._set_listening(not want)  # 失败回滚
                err = resp.get("error") or {}
                self._window.show_banner(err.get("message") or "操作失败")

        self._window.request("start" if want else "stop", None, done)

    def quit_app(self) -> None:
        """退出：先停监听（supervisor.shutdown 内含）→ 销毁窗口。"""
        self._window.quit_app()
