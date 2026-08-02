# -*- coding: utf-8 -*-
"""實驗帳本的**儲存語意**:一次嘗試一列,一天挑一個代表樣本。

## 為什麼不能一天只留一列(第十二輪 P1-4)

原本的鍵是 `(date, experiment_id)`,同日同實驗只留最後一筆。實測:

    06:00 排程:Luna 逾時 → 落回 legacy → 記一列失敗
    09:00 人工改設定後重跑:兩邊都成功 → **蓋掉上面那列**
    → primary_ok_rate 從 0.0 變成 1.0

那次逾時、那次計費、那次落回,全部從帳本消失。而這個實驗要回答的是
**「排程跑起來可不可靠、要花多少錢」** —— 人工重跑答的是另一個問題
(「設定修好之後跑不跑得動」),兩者混在一起,前者就永遠看起來很漂亮。

更糟的是方向:失敗越多、人越會去重跑,於是**越不可靠的日子越容易被洗白**。

## 這個模組的三個決定

  1. **追加,不覆蓋。** 鍵是 `(date, experiment_id, run_id, run_attempt)`。
     同一次嘗試重寫會覆寫(冪等),不同嘗試各留一列。
  2. **一天挑一個代表樣本**,而且**排程優先**:配對數與可靠度都看它。
     沒有排程紀錄的那天才退而用人工那筆,並記下它是人工的。
  3. **成本橫跨所有嘗試。** 重跑要付第二次錢,那筆錢真的花掉了。
     配對可以只算一次,帳單不行。

三件事分開報,因為它們回答三個不同的問題。把它們併成一個數字,
就再也分不出「Luna 不可靠」與「那天我手動修過設定」。
"""
from __future__ import annotations

from typing import Optional

#: 這一列是怎麼跑出來的。**排程與人工要分得出來**,否則可靠度沒有意義。
SCHEDULED, MANUAL, LOCAL = "scheduled", "manual", "local"

#: GitHub Actions 的 `GITHUB_EVENT_NAME` → 本模組的 run_kind。
#: 認不得的事件當人工:**寧可低估排程樣本,不可高估**。
_EVENT_KINDS = {"schedule": SCHEDULED, "workflow_dispatch": MANUAL,
                "repository_dispatch": MANUAL}


def run_identity(env: dict) -> dict:
    """這次執行的身分。`env` 由呼叫端注入(本模組不讀 `os.environ`)。

    本機跑(沒有 `GITHUB_RUN_ID`)算 `local` —— 它既不是排程也不是重跑,
    不該進任何一邊的分母。
    """
    run_id = str(env.get("GITHUB_RUN_ID") or "").strip()
    event = str(env.get("GITHUB_EVENT_NAME") or "").strip()
    if not run_id:
        return {"run_id": "", "run_attempt": 0, "run_kind": LOCAL}
    try:
        attempt = int(str(env.get("GITHUB_RUN_ATTEMPT") or "1"))
    except ValueError:
        attempt = 1
    return {"run_id": run_id, "run_attempt": attempt,
            "run_kind": _EVENT_KINDS.get(event, MANUAL)}


def attempt_key(row: dict) -> tuple:
    """一列的身分。**含 run_id 與 attempt** —— 那正是先前被覆蓋掉的東西。"""
    r = row or {}
    return (str(r.get("date") or ""), str(r.get("experiment_id") or ""),
            str(r.get("run_id") or ""), int(r.get("run_attempt") or 0))


def append(ledger: Optional[list], record: dict) -> list:
    """追加一次嘗試。同一次嘗試重寫會覆寫(冪等),不同嘗試各留一列。

    **這裡刻意不做任何「同一天只留一筆」的收斂** —— 收斂是判讀時的事
    (見 `canonical`),而原始紀錄一旦被覆蓋就補不回來了。
    """
    key = attempt_key(record)
    out = [r for r in (ledger or []) if attempt_key(r) != key]
    out.append(record)
    out.sort(key=lambda r: (str(r.get("date") or ""),
                            str(r.get("experiment_id") or ""),
                            str(r.get("run_id") or ""),
                            int(r.get("run_attempt") or 0)))
    return out


def _rank(row: dict) -> tuple:
    """挑代表樣本的排序鍵:排程 > 人工 > 本機;同類取最後一次嘗試。"""
    kind = str(row.get("run_kind") or LOCAL)
    return ({SCHEDULED: 2, MANUAL: 1}.get(kind, 0),
            int(row.get("run_attempt") or 0))


def canonical(ledger: Optional[list]) -> list:
    """每個 `(date, experiment_id)` 的代表樣本。

    **排程優先。** 人工重跑不取代排程 —— 否則「跑失敗就重跑一次」會把
    可靠度洗成滿分,而那正是這個實驗最不該被污染的數字。
    """
    best: dict = {}
    for r in (ledger or []):
        if not isinstance(r, dict):
            continue
        k = (str(r.get("date") or ""), str(r.get("experiment_id") or ""))
        if k not in best or _rank(r) > _rank(best[k]):
            best[k] = r
    return [best[k] for k in sorted(best)]


def attempt_stats(ledger: Optional[list]) -> dict:
    """嘗試層級的實況 —— **代表樣本看不到的那一面**。

    `manual_reruns` 是「那天已經有排程紀錄,又跑了一次人工」的次數:
    它高不代表系統壞,但它高而可靠度滿分,就代表可靠度是被重跑撐起來的。
    """
    rows = [r for r in (ledger or []) if isinstance(r, dict)]
    days: dict = {}
    for r in rows:
        days.setdefault((str(r.get("date") or ""),
                         str(r.get("experiment_id") or "")), []).append(r)
    reruns = sum(1 for v in days.values()
                 for r in v
                 if str(r.get("run_kind")) == MANUAL
                 and any(str(o.get("run_kind")) == SCHEDULED for o in v))
    ok = [r for r in rows if r.get("primary_ok")]
    return {
        "attempts": len(rows),
        "days_seen": len(days),
        "manual_reruns_after_a_scheduled_run": reruns,
        # 每一次嘗試都付過錢。配對可以只算一次,帳單不行。
        "billable_attempts": len(rows),
        "attempt_primary_ok_rate": (round(len(ok) / len(rows), 3)
                                    if rows else None),
        "scheduled_attempts": sum(1 for r in rows
                                  if str(r.get("run_kind")) == SCHEDULED),
    }
