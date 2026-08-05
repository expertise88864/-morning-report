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


def _retry_after_seconds(raw) -> float:
    """`Retry-After` 可能是秒數,**也可能是 HTTP-date**(RFC 7231)。
    先前只當 float —— 日期格式整個被忽略。"""
    txt = str(raw or "").strip()
    if not txt:
        return 0.0
    try:
        return max(0.0, float(txt))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        import datetime as _dt
        when = parsedate_to_datetime(txt)
        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        return max(0.0, (when - _dt.datetime.now(_dt.timezone.utc)).total_seconds())
    except Exception:                   # noqa: BLE001 - 格式不認得就當沒給
        return 0.0


def post_with_backoff(url: str, body: dict, headers: dict, *,
                      timeout, manifest=None, deadline=None):
    """送一次請求;429/5xx 退避重試。**尊重 `Retry-After`**。

    回傳最後一次的 response(400 仍交給呼叫端做選配欄位退讓)。
    """
    # 第二十一輪 P2-5:**退避要吃絕對 deadline。** 先前每次都用完整的
    # request timeout、sleep 不計入預算 —— 四次呼叫理論上可以超過
    # 整個 LLM 階段的總時間預算。
    started = time.monotonic()
    r = None
    for attempt in range(_LLM_RETRIES + 1):
        left = None if deadline is None else deadline - (time.monotonic() - started)
        if left is not None and left <= 0:
            return r if r is not None else requests.post(
                url, json=body, headers=headers, timeout=1)
        r = requests.post(url, json=body, headers=headers,
                          timeout=timeout if left is None else min(timeout, left))
        if r.status_code not in _LLM_RETRY_STATUS or attempt >= _LLM_RETRIES:
            return r
        wait = _LLM_BACKOFF_SEC * (attempt + 1)
        wait = max(wait, _retry_after_seconds(r.headers.get("Retry-After")))
        wait = min(wait, 45.0)     # 晨報有時間預算,不能無限等
        if left is not None:
            wait = min(wait, max(0.0, left - 1.0))
        if manifest is not None:
            manifest.setdefault("llm", {}).setdefault(
                "retry_after_status", []).append(
                    {"status": r.status_code, "wait_seconds": round(wait, 1)})
        print(f"[llm] {r.status_code} 退避 {wait:.0f}s 後重試"
              f"({attempt + 1}/{_LLM_RETRIES})", file=sys.stderr)
        time.sleep(wait)
    return r


