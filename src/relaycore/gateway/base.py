# base.py — Gateway 统一接口（design.md §6.2④）
#
# push(target, code, original_text, meta) -> PushResult(ok, code, retryable, reason)
# 成功判定严格 code/errcode == 0（contracts §7 #13）。
# 文案模板统一在此（design §8：前缀由 zero-reconn 调整为中性产品名）。
# 推送文案 = 程序标识 + 完整短信原文（change fix-push-format-duplicate-code：
# 标头后直连原文，验证码只由原文承载，模板不再另行渲染验证码/时效提示；
# 原文仅内存流转，不落库）。

from __future__ import annotations

from relaycore.model import PushResult, PushTarget

# 程序专属标识（简洁可识别；原文直连后来源信息由原文自身承载）
_BRAND = "【SMS接力】"
# 有原文：标头 + 原文直连（单行，无换行、无「原文：」前缀、无模板验证码）
# —— 验证码只由原文承载，避免同一验证码在一条推送内出现两次
_TEXT_WITH_ORIGINAL = f"{_BRAND}{{original}}"
# 无原文（测试推送 / 手动重推）：标头 + 验证码。
# 不附加时效提示：原文不存在时无从提示时效（原「请以原文时效为准」失去指代对象）
_TEXT_CODE_ONLY = f"{_BRAND}验证码 {{code}}"

TIMEOUT_SECONDS = 10


def render_text(code: str, original_text: str = "") -> str:
    """渲染推送文案：标头 + 完整短信原文（无原文时退化为标头 + 验证码）。

    original_text 非空（真实投递路径）：输出「标头 + 原文」单行，模板不再另行
    渲染验证码与时效提示 —— 验证码只来自原文，杜绝同一验证码在一条推送内重复
    出现（下游取码程序按首个/末个 6 位数字解析时结果一致）。

    original_text 为空（测试推送、手动重推等无原文路径）：退化为「标头 + 验证码」
    单行形态，不含原文、不含时效提示。

    原文仅 strip 首尾空白，内部逐字保留（含全角标点、时间戳、emoji、多个验证码）。
    """
    original = (original_text or "").strip()
    if not original:
        return _TEXT_CODE_ONLY.format(code=code)
    return _TEXT_WITH_ORIGINAL.format(original=original)


class Preview:
    """测试推送结果（dry-run，不发真实请求）：spec diagnostics 测试推送场景。"""

    def __init__(self, platform: str, url_masked: str, content: str, enabled: bool):
        self.platform = platform
        self.url_masked = url_masked
        self.content = content
        self.enabled = enabled


def mask_url(url: str) -> str:
    """目标地址中间打码（design §6.3 敏感处理）。"""
    if len(url) <= 12:
        return "****"
    return f"{url[:18]}****{url[-4:]}"


class Gateway:
    """平台网关基类；子类实现 push 与 preview。"""

    platform = ""

    def push(self, target: PushTarget, code: str, original_text: str = "",
             meta: dict | None = None) -> PushResult:
        raise NotImplementedError

    def preview(self, target: PushTarget) -> Preview:
        """测试推送：仅渲染占位内容，不发请求（spec：平台上不会收到真实消息）。

        展示与真实投递一致的结构（标识 + 掩码验证码 + 示例原文），
        示例原文中的验证码同样掩码，避免 dry-run 泄露可用的验证码明文
        （change push-message-with-original-text）。
        """
        return Preview(
            platform=self.platform,
            url_masked=mask_url(target.url),
            content=render_text(
                "******",
                "（示例）由水滴发送，登陆验证码：******。时间: 2026-10-29 16:30:05"),
            enabled=target.enabled,
        )
