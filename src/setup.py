# setup.py — py2app 打包入口
#
# 构建：.venv-pack/bin/python setup.py py2app
# 产物：dist/SMSRelay.app（后续 ad-hoc 签名 + zip 分发）
#
# 说明：
# - 生产代码 stdlib-only；PyObjC 框架仅为 GUI 系统集成依赖，随 bundle 携带。
# - 子进程（监听核心）通过 `--relaycore-child` 参数复用同一二进制（supervisor.py），
#   故 relaycore 与 desktop 均需整体入包。
# - LSUIElement=True：托盘应用形态，不在 Dock 显示（NSStatusItem 托盘 + Tk 窗口；
#   关闭窗口仅隐藏至托盘）。

from setuptools import setup

APP = ["desktop/app.py"]
DATA_FILES = []
OPTIONS = {
    "argv_emulation": False,  # 无需 Finder 文档打开事件
    "plist": {
        "CFBundleName": "SMSRelay",
        "CFBundleDisplayName": "SMS 接力",
        "CFBundleIdentifier": "com.smsrelay.desktop",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",  # SMAppService.mainApp 要求 macOS 13+
        "LSUIElement": True,               # 托盘应用：无 Dock 图标
        "NSUserNotificationsUsageDescription":
            "验证码到达时发送系统通知",
        "NSDesktopFolderUsageDescription":
            "读取 iMessage 短信数据库以转发验证码",
    },
    "packages": ["relaycore", "desktop"],
    "includes": [
        "AppKit", "Foundation", "objc",
        "UserNotifications", "ServiceManagement",
        # 显式锁定：urllib HTTPS 建连需 IDNA 域名编码，py2app 模块扫描
        # 偶发漏收 encodings.idna → 运行时 unknown encoding: idna（实测 2026-10-29）
        "encodings.idna",
    ],
    # setuptools 是 py2app 自身的构建期依赖，运行期用不到；排除可减小 bundle
    # 体积，并避免其 __pycache__ 中的构建机绝对路径随包分发。
    "excludes": ["setuptools", "setuptools._vendor", "pkg_resources"],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
