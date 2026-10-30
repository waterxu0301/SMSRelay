# wecom.py — 企业微信网关（tasks 4.6, Trace: D-BSV-006, D-EXT-003）
#
# body {"msgtype":"text","text":{"content":...}}，成功判定 errcode == 0。

from __future__ import annotations

import json
import urllib.error
import urllib.request

from relaycore.gateway.base import TIMEOUT_SECONDS, Gateway, render_text
from relaycore.model import PushResult, PushTarget

# 企微群机器人限频/系统繁忙错误码（D8 退避起始 5s 判定用）
RATE_LIMIT_CODES = {45009, 45033}


class WeComGateway(Gateway):
    platform = "wecom"

    def push(self, target: PushTarget, code: str, original_text: str = "",
             meta: dict | None = None) -> PushResult:
        payload = {"msgtype": "text",
                   "text": {"content": render_text(code, original_text)}}
        req = urllib.request.Request(
            target.url,
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
        return PushResult(
            ok=ok, code=errcode,
            retryable=(not ok) and (errcode in RATE_LIMIT_CODES or errcode is None),
            reason=None if ok else str(body.get("errmsg") or errcode),
        )
