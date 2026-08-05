# -*- coding: utf-8 -*-
"""**LLM 呼叫的傳輸韌性**(2026-08-05 實機根因之一)。

這個 repo 的 `_http_get` 早就有 429/5xx 退避重試 —— 而** LLM 呼叫沒有**。
2026-08-05 的一次 429(送出後 2.7 秒就被拒)因此讓整天的特化分析
落回 legacy:**暫時性的失敗花掉了一整天的分析**。

拆成獨立模組是因為它與「怎麼問模型」無關,而且它的失效方式自成一類:
退避太短會連續撞牆,退避太長會吃掉晨報的時間預算 —— 兩邊都要有上限。
"""
from __future__ import annotations

import sys
import time

import requests

#: 429/5xx 的重試次數與退避基數。**這個 repo 的 `_http_get` 早就有這一套**,
#: 而 LLM 呼叫沒有 —— 於是 2026-08-05 的一次 429(2.7 秒就被拒)讓整天的
#: 特化分析落回 legacy。**暫時性的失敗不該花掉一整天的分析。**
_LLM_RETRY_STATUS = (429, 500, 502, 503, 504)
_LLM_RETRIES = 3
_LLM_BACKOFF_SEC = 8.0


def post_with_backoff(url: str, body: dict, headers: dict, *,
                      timeout, manifest=None):
    """送一次請求;429/5xx 退避重試。**尊重 `Retry-After`**。

    回傳最後一次的 response(400 仍交給呼叫端做選配欄位退讓)。
    """
    r = None
    for attempt in range(_LLM_RETRIES + 1):
        r = requests.post(url, json=body, headers=headers,
                          timeout=timeout)
        if r.status_code not in _LLM_RETRY_STATUS or attempt >= _LLM_RETRIES:
            return r
        wait = _LLM_BACKOFF_SEC * (attempt + 1)
        try:                       # provider 說要等多久就等多久
            wait = max(wait, float(r.headers.get("Retry-After") or 0))
        except (TypeError, ValueError):
            pass
        wait = min(wait, 45.0)     # 晨報有時間預算,不能無限等
        if manifest is not None:
            manifest.setdefault("llm", {}).setdefault(
                "retry_after_status", []).append(
                    {"status": r.status_code, "wait_seconds": round(wait, 1)})
        print(f"[llm] {r.status_code} 退避 {wait:.0f}s 後重試"
              f"({attempt + 1}/{_LLM_RETRIES})", file=sys.stderr)
        time.sleep(wait)
    return r


