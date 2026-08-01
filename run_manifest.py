# -*- coding: utf-8 -*-
"""執行 manifest 的**記錄器**:主模組全域 `_RUN_MANIFEST` 的擁有者。

第十輪 P1-12。外審指出主模組的「棘輪」已經調高七次而只是文字規約,
真正的解是用依賴注入取代模組全域,並點名從 `_RUN_MANIFEST` 開始 ——
它是被最多函式碰的那一個(17 個頂層函式、57 處)。

## 為什麼從這裡開始有效
`refactor_audit.py` 判 BLOCK 的最常見理由就是 `state=['_RUN_MANIFEST']`。
把它換成一個可注入的物件之後,那些函式從「碰模組全域」變成「收一個參數」,
BLOCK 的理由消失,才有可能真的搬走。

## 相容性是刻意的
`ManifestRecorder.data` **就是**主模組的 `_RUN_MANIFEST` 那個 dict 物件
(不是複本、也不重新綁定)。131 處測試引用全部是就地變更
(`.pop` / `[k] = v` / `.setdefault`),因此不需要改動任何一條。
一次到位的大改寫在這種耦合度下必然出事,所以先開出接縫、再逐步收斂。

## 這個模組不碰檔案系統與網路
組裝是純函式,寫檔與 Actions summary 留在主模組 —— 與 `llm_shadow` /
`llm_telemetry` 同一個原則,這樣它可以單獨測,也不受 conftest 的 state 隔離影響。
"""
from __future__ import annotations

from typing import Optional

#: 各階段寫進 manifest 的**診斷鍵**,由 `build()` 統一帶出。
#:
#: 這個 writer 是重建白名單 dict,沒列到的鍵一律被靜默丟掉(記憶體裡有值、
#: 檔案裡沒有)。**這個坑發生過八次**(stance_dual / data_checks / mz_shadow /
#: llm_extractor / delivery / capability_health / forecast_mixed_versions /
#: exdiv_preview),所以改成「一處宣告 + AST 掃描比對」,不再靠人記得改兩個地方。
#: 新增鍵時只要加進這裡;忘了加,測試會指名是哪一個鍵。
DIAGNOSTIC_KEYS = (
    "model_history_days", "d1_samples", "d1_ready", "stance_dual",
    "data_checks", "mz_shadow", "llm_extractor", "delivery",
    "capability_health", "forecast_mixed_versions", "exdiv_preview",
    "corporate_actions", "chips", "policy_deepdive", "llm_shadow", "llm",
    "state_writes", "event_identity",
)

#: 刻意**不**落地的鍵:`marks` 是階段計時的中間結構,已經被彙整成 `phases`,
#: 原樣寫出去只是重複且龐大。
TRANSIENT_KEYS = ("marks",)


class ManifestRecorder:
    """擁有 manifest dict,並提供記錄操作。

    刻意**不**在建構時複製傳進來的 dict:主模組的 `_RUN_MANIFEST` 必須與
    `self.data` 是同一個物件,否則既有的 131 處測試引用會與記錄器分家 ——
    而那種分家是靜默的(兩邊都「有資料」,只是不是同一份)。
    """

    def __init__(self, data: Optional[dict] = None):
        self.data = {"marks": []} if data is None else data
        self.data.setdefault("marks", [])

    # ── 階段計時 ────────────────────────────────────────────────────
    def mark_phase(self, label: str, clock: float) -> None:
        """在階段邊界插一個時間標記(相鄰標記差 = 該階段耗時)。純觀測。"""
        self.data["marks"].append((label, clock))

    def phases(self) -> list:
        marks = self.data.get("marks") or []
        return [{"label": marks[i][0],
                 "seconds": round(marks[i + 1][1] - marks[i][1], 1)}
                for i in range(len(marks) - 1)]

    def total_seconds(self) -> float:
        marks = self.data.get("marks") or []
        return round(marks[-1][1] - marks[0][1], 1) if len(marks) >= 2 else 0.0

    # ── state 寫入帳 ────────────────────────────────────────────────
    def record_state_writes(self, writes: dict) -> list:
        """把寫入帳彙整進 manifest,回傳失敗的檔名(呼叫端決定要不要降級)。

        **只記成功的等於沒記** —— 失敗才是要看的,所以 `detail` 只留失敗項。
        """
        failed = sorted(k for k, v in (writes or {}).items() if not v.get("ok"))
        self.data["state_writes"] = {
            "attempted": len(writes or {}),
            "failed": failed,
            "detail": {k: v for k, v in sorted((writes or {}).items())
                       if not v.get("ok")}}
        return failed

    # ── 組裝 ────────────────────────────────────────────────────────
    def build(self, *, date: str, budget_seconds: float, news_workers: int,
              degraded_steps, feeds: Optional[dict] = None) -> dict:
        """組出要落地的 manifest。**純函式** —— 不寫檔、不印東西。

        診斷鍵一律由 `DIAGNOSTIC_KEYS` 統一帶出,不逐項明列:
        逐項明列正是那個發生過八次的坑(而且會與這裡的 tuple 重複)。
        """
        out = {
            "date": date,
            "total_seconds": self.total_seconds(),
            "budget_seconds": budget_seconds,
            "news_workers": news_workers,
            "degraded_steps": list(dict.fromkeys(degraded_steps or [])),
            "phases": self.phases(),
            "feeds": {h: {"ok": int((s or {}).get("ok", 0)),
                          "fail": int((s or {}).get("fail", 0))}
                      for h, s in (feeds or {}).items()},
        }
        out.update({k: self.data.get(k) for k in DIAGNOSTIC_KEYS})
        return out
