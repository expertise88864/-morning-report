# -*- coding: utf-8 -*-
"""帳本的**嘗試層級統計** —— 代表樣本看不到的那一面。

從 `experiment_ledger` 拆出來(第十四輪)。拆的理由寫在
`tests/test_module_size_freeze.py` 的上限註解裡:那個數字已經被外審撐開
兩次(150 → 185 → 210),而當時就記下「再撐下去該把『代表樣本挑選』與
『嘗試統計』拆成兩個模組,而不是繼續放寬同一個數字」。這次第三次要撐,
所以照當初寫下的做法拆,不動那個上限。

兩邊回答的是不同的問題:
  - `experiment_ledger`:**這一天的代表樣本是誰**(配對數、可靠度)
  - 這裡:**實際跑了幾次、重跑了幾次、花了幾次呼叫**(帳單與偏差)

`canonical()` 刻意看不到重跑;而重跑次數高、可靠度卻滿分,正是
「可靠度是被重跑撐起來的」那個訊號 —— 它只有在這裡看得到。
"""
from __future__ import annotations

from typing import Optional

from experiment_ledger import MANUAL, SCHEDULED


def _later_than_a_scheduled_run(row: dict, same_day: list) -> Optional[bool]:
    """這筆人工執行是不是**排在某次排程之後**。無從判斷時回 `None`。

    r1(Codex,P2):`manual_reruns_after_a_scheduled_run` 這個名字宣稱了
    時序,而原本的實作只看「同一天有沒有兩種」—— 先手動後排程也會被算成
    重跑。這個指標存在的理由正是要抓「**失敗之後才去重跑**」那個偏差,
    算錯方向就等於在製造它要偵測的假象。

    沒有時間戳的舊列**不猜**:回 `None`,由呼叫端另外計數。
    把不知道當成「否」會低估偏差,當成「是」會高估 —— 兩個都是編造。
    """
    # 空白的時間戳等於沒有時間戳 —— `" "` 是 truthy,不 strip 的話
    # 它會被當成一個真的時間拿去比大小,而比出來的結果毫無意義。
    ts = str(row.get("started_at") or "").strip()
    sched = [str(o.get("started_at") or "").strip() for o in same_day
             if str(o.get("run_kind")) == SCHEDULED]
    if not sched:
        return False
    if not ts or not all(sched):
        return None
    return any(ts > t for t in sched)


def _summed(rows: list, field: str) -> tuple:
    """(總和, 有幾列真的帶著數字)。**一列都沒帶才是「不知道」;`0` 是事實。**

    第十四輪 P2-1:原本寫 `sum(...) or None`,於是「每一列都確定沒有呼叫」
    (影子天天被預算擋掉、零修補的一天)會被報成「沒有 telemetry」——
    一個真的量到的事實被說成量不到,而它正是成本完整性的判準。
    `bool` 不算數值:Python 的 `True` 是 1,不擋的話會被加進帳單。
    """
    got = [r[field] for r in rows if isinstance(r.get(field), int)
           and not isinstance(r[field], bool)]
    return (sum(got) if got else None), len(got)


def attempt_stats(ledger: Optional[list]) -> dict:
    """嘗試層級的實況 —— **代表樣本看不到的那一面**。

    重跑次數高不代表系統壞;但它高而可靠度滿分,就代表可靠度是被重跑
    撐起來的。
    """
    rows = [r for r in (ledger or []) if isinstance(r, dict)]
    days: dict = {}
    for r in rows:
        days.setdefault((str(r.get("date") or ""),
                         str(r.get("experiment_id") or "")), []).append(r)
    reruns, unordered = 0, 0
    for v in days.values():
        for r in v:
            if str(r.get("run_kind")) != MANUAL:
                continue
            later = _later_than_a_scheduled_run(r, v)
            if later is None:
                unordered += 1
            elif later:
                reruns += 1
    ok = [r for r in rows if r.get("primary_ok")]
    # 第十三輪 P1-4:**一列不等於一次計費呼叫。** 一份報告可能是
    # 「Luna 一次不合格 + 一次修補 + DeepSeek 影子一次」= 三次計費,
    # 而逾時那種還會計費卻量不到 usage。用列數冒充呼叫數會低估帳單,
    # 而低估的方向正好偏向「這個實驗很便宜」。
    calls, _measured = _summed(rows, "provider_calls")
    unmeasured, _ = _summed(rows, "billable_unmeasured_calls")
    return {
        "recorded_runs": len(rows),
        "days_seen": len(days),
        "manual_reruns_after_a_scheduled_run": reruns,
        # 沒有時間戳、排不出先後的人工執行。**不併進上面那個數字** ——
        # 它宣稱的是時序,而這些排不出時序。
        "manual_attempts_of_unknown_order": unordered,
        # 三個層次分開報:**紀錄列數 ≠ provider 呼叫數 ≠ 量得到金額的呼叫數**。
        # 沒有逐列的呼叫數就回 None,不要拿列數頂替(那是編造)。
        "provider_calls": calls,
        "billable_unmeasured_calls": unmeasured,
        # 有幾列真的帶著呼叫數 —— 它與 `recorded_runs` 差很多時,
        # 說得出上面那個總和只涵蓋了一部分的班次。
        "provider_calls_measured_rows": _measured,
        "run_primary_ok_rate": (round(len(ok) / len(rows), 3)
                                if rows else None),
        "scheduled_attempts": sum(1 for r in rows
                                  if str(r.get("run_kind")) == SCHEDULED),
    }
