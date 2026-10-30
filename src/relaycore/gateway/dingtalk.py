# dingtalk.py — 钉钉网关（query 加签）（tasks 4.5, Trace: D-BSV-005, D-EXT-002）
#
# 加签规范：sign = urlencode(base64(hmac_sha256(secret, f"{timestamp}\n{secret}")))
# timestamp = int(time.time()*1000)，与 sign 同源；缺密钥 → 零请求 SIGN_MISSING。
# 成功判定 errcode == 0（contracts §7 #13）。

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from relaycore.gateway.base import TIMEOUT_SECONDS, Gateway, render_text
from relaycore.model import PushResult, PushTarget

# 限频类错误码（D8：起始退避提升至 5s，交由 dispatch 判定 retryable+limited）
RATE_LIMIT_CODES = {130101, 130102, 410100}


def build_sign(secret: str, timestamp_ms: int) -> str:
    """钉钉自定义机器人加签：HmacSHA256 → Base64 → urlencode。"""
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                      hashlib.sha256).digest()
    return urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))


class DingtalkGateway(Gateway):
    platform = "dingtalk"

    def __init__(self, secret_provider: Callable[[str], str | None]):
        """secret_provider(account) -> 密钥或 None（Keychain 只读，D5）。"""
        self._secret_provider = secret_provider

    def push(self, target: PushTarget, code: str, original_text: str = "",
             meta: dict | None = None) -> PushResult:
        secret = self._secret_provider(target.secret_account) if target.secret_account else None
        if not secret:
            # spec 场景：缺加签密钥 → 不发请求
            return PushResult(ok=False, retryable=False, reason="SIGN_MISSING")

        timestamp_ms = int(time.time() * 1000)
        sign = build_sign(secret, timestamp_ms)
        url = f"{target.url}&timestamp={timestamp_ms}&sign={sign}"
        payload = {"msgtype": "text",
                   "text": {"content": render_text(code, original_text)}}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return PushResult(ok=False, code=exc.code, retryable=exc.code >= 500,
                              reason=f"HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return PushResult(ok=False, retryable=True, reason=f"网络异常: {exc}")
        except ValueError:
            return PushResult(ok=False, retryable=True, reason="响应非 JSON")

        errcode = body.get("errcode")
        ok = errcode == 0
        reason = None if ok else str(body.get("errmsg") or errcode)
        return PushResult(
            ok=ok, code=errcode,
            retryable=(not ok) and (errcode in RATE_LIMIT_CODES or errcode is None),
            reason=reason,
        )
