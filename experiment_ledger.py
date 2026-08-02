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


def run_identity(env: dict, *, recorded_at: str = "") -> dict:
    """這次執行的身分。`env` 由呼叫端注入(本模組不讀 `os.environ`)。

    本機跑(沒有 `GITHUB_RUN_ID`)算 `local` —— 它既不是排程也不是重跑,
    不該進任何一邊的分母。

    第十三輪 P2-5:**`started_at` 取自 workflow 最開頭的 `RUN_STARTED_AT`,
    不是這一列被寫下的時間。** 先前兩者混為一談,而寫下的時間是 LLM 分析
    跑完之後 —— 兩個 workflow 重疊時,「先開始、後完成」與「後開始、先完成」
    的順序會相反,而重跑偏差正是靠先後判的。
    記錄時間另存 `recorded_at`:它仍然有用(對照 log),只是不能拿來判先後。
    """
    run_id = str(env.get("GITHUB_RUN_ID") or "").strip()
    event = str(env.get("GITHUB_EVENT_NAME") or "").strip()
    if not run_id:
        return {"run_id": "", "run_attempt": 0, "run_kind": LOCAL,
                "started_at": str(env.get("RUN_STARTED_AT") or "").strip(),
                "recorded_at": str(recorded_at or "")}
    try:
        attempt = int(str(env.get("GITHUB_RUN_ATTEMPT") or "1"))
    except ValueError:
        attempt = 1
    return {"run_id": run_id, "run_attempt": attempt,
            "run_kind": _EVENT_KINDS.get(event, MANUAL),
            "started_at": str(env.get("RUN_STARTED_AT") or "").strip(),
            "recorded_at": str(recorded_at or "")}


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


def scoped(ledger: Optional[list], keep) -> list:
    """只留下**同一個比較範圍**的列(第十三輪 P1-4)。

    `keep(row) -> bool` 由呼叫端給:它知道自己在問哪一群的問題,本模組不知道。

    先前 `canonical()` 只依 `(date, experiment_id)` 分組、不看同群鍵:
    同一天換過模型/強度/profile 時**兩個 cohort 會互相擠掉**,被擠掉的
    那群憑空少一天。`attempt_stats()` 更寬 —— 連 experiment_id 都不分。
    **收斂只在同一個可比範圍內才有意義**,所以範圍要先劃再收斂。
    """
    return [r for r in (ledger or []) if isinstance(r, dict) and keep(r)]


def canonical(ledger: Optional[list]) -> list:
    """每個 `(date, experiment_id)` 的代表樣本。

    **先用 `scoped()` 劃好範圍再叫它** —— 它不會自己分辨同群。

    **排程優先。** 人工重跑不取代排程 —— 否則「跑失敗就重跑一次」會把
    可靠度洗成滿分,而那正是這個實驗最不該被污染的數字。

    r1(Codex,P1):**本機跑完全不算代表樣本。** `run_identity` 的說明
    寫著「本機跑不該進任何一邊的分母」,而這裡原本在沒有更高階紀錄時
    照樣把它留下來 —— 於是我在本機測一次就可能推進十配對、抬高可靠度。
    **宣稱與實作不符,而不符的那一邊是我自己寫下的合約。**
    本機的花費仍然看得到,在 `attempt_stats` 裡。
    """
    best: dict = {}
    for r in (ledger or []):
        if not isinstance(r, dict) or str(r.get("run_kind")) == LOCAL:
            continue
        k = (str(r.get("date") or ""), str(r.get("experiment_id") or ""))
        if k not in best or _rank(r) > _rank(best[k]):
            best[k] = r
    return [best[k] for k in sorted(best)]


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
    calls = sum(int(r.get("provider_calls") or 0) for r in rows)
    unmeasured = sum(int(r.get("billable_unmeasured_calls") or 0) for r in rows)
    return {
        "recorded_runs": len(rows),
        "days_seen": len(days),
        "manual_reruns_after_a_scheduled_run": reruns,
        # 沒有時間戳、排不出先後的人工執行。**不併進上面那個數字** ——
        # 它宣稱的是時序,而這些排不出時序。
        "manual_attempts_of_unknown_order": unordered,
        # 三個層次分開報:**紀錄列數 ≠ provider 呼叫數 ≠ 量得到金額的呼叫數**。
        # 沒有逐列的呼叫數就回 None,不要拿列數頂替(那是編造)。
        "provider_calls": calls or None,
        "billable_unmeasured_calls": unmeasured or None,
        "run_primary_ok_rate": (round(len(ok) / len(rows), 3)
                                if rows else None),
        "scheduled_attempts": sum(1 for r in rows
                                  if str(r.get("run_kind")) == SCHEDULED),
    }
