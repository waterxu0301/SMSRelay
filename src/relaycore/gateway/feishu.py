# feishu.py — 飞书网关（tasks 4.4, Trace: D-BSV-004, D-EXT-001）
#
# 复用 sms_relay.py:103-115 payload 结构与 :167 `code == 0` 成功判定；
# 前缀按 design §8 调整为中性产品名（base.TEXT_TEMPLATE）。

from __future__ import annotations

import json
import urllib.error
import urllib.request

from relaycore.gateway.base import TIMEOUT_SECONDS, Gateway, render_text
from relaycore.model import PushResult, PushTarget


class FeishuGateway(Gateway):
    platform = "feishu"

    def _post(self, url: str, code: str, original_text: str = "") -> PushResult:
        payload = {
            "msg_type": "text",
            "content": {"text": render_text(code, original_text)},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            # 4xx 鉴权/参数类不可重试；5xx 可重试
            return PushResult(ok=False, code=exc.code, retryable=exc.code >= 500,
                              reason=f"HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return PushResult(ok=False, retryable=True, reason=f"网络异常: {exc}")
        except ValueError:
            return PushResult(ok=False, retryable=True, reason="响应非 JSON")

        # 复用现状成功判定（sms_relay.py:167）
        ok = body.get("code") == 0
        return PushResult(
            ok=ok, code=body.get("code"), retryable=not ok,
            reason=None if ok else str(body.get("msg") or body.get("code")),
        )

    def push(self, target: PushTarget, code: str, original_text: str = "",
             meta: dict | None = None) -> PushResult:
        return self._post(target.url, code, original_text)
