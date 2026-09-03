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
                      timeout, manifest=None, deadline_at=None):
    """送一次請求;429/5xx 退避重試。**尊重 `Retry-After`**。

    回傳最後一次的 response(400 仍交給呼叫端做選配欄位退讓)。
    """
    # 第二十二輪 P1-7:**deadline 是絕對時間戳,不是本函式自己的碼表。**
    # 上一版把 `deadline` 解讀成「從進入函式起算的秒數」—— 於是選配欄位
    # 退讓、修補、加深每次重新進來都拿到完整預算,四層加起來可以超過
    # 整個 LLM 階段的總限制。改吃 `deadline_at`(monotonic 時間戳,
    # 與主模組的 `_LLM_DEADLINE` 同一個),**每次動作前後都重算剩餘**;
    # 進來時已經過期就直接回 None,一次 API 都不打。
    def _note(key, entry):
        if manifest is not None:
            manifest.setdefault("llm", {}).setdefault(key, []).append(entry)

    def _gave_up(status, why):
        """**放棄也要記**(2026-08-09 P2)。上一版只記「退避了幾次」——
        而 429 打到預算用完那天,manifest 與乾淨的一天長得一模一樣:
        重試清單非空只說明「遇到過阻力」,說不出「最後有沒有拿到答案」。

        `status` 一律是**觸發重試的那個狀態**(HTTP 碼或例外類別名),
        `attempt` 一律是**真的送出去過幾次請求** —— 四個放棄出口用
        同一套語義,否則同一份清單裡的兩筆讀起來意思不一樣
        (外審:迴圈頂端那個出口原本記 `"deadline"` 與一個多算一次的
        次數,而 `retry_after_status` 用的是 HTTP 碼)。
        """
        _note("retry_gave_up", {"status": status, "reason": why,
                                "attempt": sent})

    r = None
    sent = 0
    last_trigger = None
    last_exc = None
    for attempt in range(_LLM_RETRIES + 1):
        left = None if deadline_at is None else deadline_at - time.monotonic()
        if left is not None and left <= 0:
            if not sent:
                # **None ⇔ 一次都沒送**(全案審查 2026-09-03 LM-8):呼叫端據此
                # 把這次失敗記成「不計費」。送過的話下面兩條路都不會回 None。
                return None
            _gave_up(last_trigger, "退避途中預算用完")
            if r is None and last_exc is not None:
                # 送過、但每一次都是傳輸層例外(可能含 ReadTimeout:伺服器也許
                # 已經做工)—— 狀態未知,不可冒充「沒送」;把最後的例外拋回去,
                # 呼叫端走既有的 billable 路徑。
                raise last_exc
            return r
        try:
            sent += 1
            r = requests.post(url, json=body, headers=headers,
                              timeout=timeout if left is None else min(timeout, left))
            last_exc = None     # 這一次有回應:更早的傳輸例外不再代表最新狀態
        except requests.exceptions.RequestException as e:
            # 傳輸層斷線也要退避重試(2026-08-07 E2E 第六次:DeepSeek 回應
            # 中途斷線 ChunkedEncodingError,一發就整天放棄特化路徑)。
            # 額度用完或預算不夠就把例外丟回去 —— 呼叫端要記 billable。
            left = None if deadline_at is None else deadline_at - time.monotonic()
            if attempt >= _LLM_RETRIES or (left is not None and left <= 1.0):
                _gave_up(type(e).__name__,
                         "重試次數用完" if attempt >= _LLM_RETRIES
                         else "剩餘預算不足")
                raise
            wait = min(_LLM_BACKOFF_SEC * (attempt + 1), 45.0)
            if left is not None:
                wait = min(wait, max(0.0, left - 1.0))
            last_trigger = type(e).__name__
            last_exc = e
            r = None            # **最後一次的結果才算數**(Codex deep r1 P3):留著更早那個
                                # 429 回應,deadline 出口會回它而不是拋這次的例外
            _note("retry_after_status",
                  {"status": last_trigger, "wait_seconds": round(wait, 1)})
            print(f"[llm] 傳輸中斷({type(e).__name__})退避 {wait:.0f}s 後重試"
                  f"({attempt + 1}/{_LLM_RETRIES})", file=sys.stderr)
            time.sleep(wait)
            continue
        if r.status_code not in _LLM_RETRY_STATUS:
            # **成功之前送了幾次也要記**(第二十七輪外審 P2-2):
            # 上一版只在最終放棄時寫 `retry_gave_up` —— 而「第 3 次才成功」
            # 與「第 1 次就成功」在帳本裡長得一樣,call volume、
            # 每次請求的延遲、以及「這一天服務有多不穩」全部看不出來。
            # 邏輯呼叫數(`llm.<role>`)與**實體請求數**是兩件事。
            if sent > 1:
                _note("physical_attempts",
                      {"status": r.status_code, "attempts": sent,
                       "retried_on": last_trigger})
            return r
        if attempt >= _LLM_RETRIES:
            _gave_up(r.status_code, "重試次數用完")
            return r
        # **request 之後重算** —— 上一版用 request 前的舊值決定 sleep。
        left = None if deadline_at is None else deadline_at - time.monotonic()
        if left is not None and left <= 1.0:
            _gave_up(r.status_code, "剩餘預算不足")
            return r
        wait = _LLM_BACKOFF_SEC * (attempt + 1)
        wait = max(wait, _retry_after_seconds(r.headers.get("Retry-After")))
        wait = min(wait, 45.0)     # 晨報有時間預算,不能無限等
        if left is not None:
            wait = min(wait, max(0.0, left - 1.0))
        last_trigger = r.status_code
        _note("retry_after_status",
              {"status": last_trigger, "wait_seconds": round(wait, 1)})
        print(f"[llm] {r.status_code} 退避 {wait:.0f}s 後重試"
              f"({attempt + 1}/{_LLM_RETRIES})", file=sys.stderr)
        time.sleep(wait)
    return r


