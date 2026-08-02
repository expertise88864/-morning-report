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

import sys
from typing import Optional

import llm_telemetry as _lt

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
    # Luna 特化實驗:**這一列是十配對的原始資料**。
    # r2(Codex,#4):刻意獨立於 `llm_shadow` —— 那個鍵在既有路徑結尾是
    # **整包指派**,寫在它底下的失敗紀錄會被靜默蓋掉,而可靠度指標
    # 又回到「只量 Luna 成功的那些天」。
    "llm_experiment",
    # 盲評卡的**存在性**(日期/路徑/通道/解碼表在不在)。刻意只有指標,
    # 沒有文字 —— manifest 會 commit 進公開 repo,而卡片含兩份完整分析。
    "llm_experiment_review",
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

    def __init__(self, data: Optional[dict] = None,
                 degraded: Optional[list] = None, log=None):
        self.data = {"marks": []} if data is None else data
        self.data.setdefault("marks", [])
        # 降級清單與 manifest 是同一件事的兩面(「發生了什麼」與「哪裡不對」),
        # 由同一個物件持有才不會有人只更新其中一邊。
        # 同樣是**同一個 list 物件**,不是複本。
        self.degraded = [] if degraded is None else degraded
        self._log = log or (lambda m: print(m, file=sys.stderr))

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

    # ── LLM 呼叫 ────────────────────────────────────────────────────
    def record_llm_call(self, role: str, provider: str, model: str, *,
                        requested_effort: str = "", applied_effort: str = "",
                        usage: Optional[dict] = None, accepted: bool = False,
                        finish_reason: str = "", error: str = "",
                        elapsed: float = 0.0, **extra) -> None:
        """記錄一次 LLM 呼叫。**依角色分槽**(第九輪 P0-2)。

        批#90d 的第一版把 primary / extractor / shadow 寫進**同一個槽位**,
        每次呼叫覆蓋 provider 與 model、token 全部相加。實際執行順序是
        抽取器 → 主分析 → 影子,所以開了影子之後 manifest 會宣稱
        **「這封信由影子模型撰寫」** —— 而影子的輸出根本沒進信件。
        token 也混成一團:不同模型單價不同,加總之後算不出成本。
        **錯誤的可觀測性比沒有更危險**,它給的是一個看似精確、語意卻錯的答案。

        `accepted=True` 只代表**API 回應可用**(content 非空、finish 不是
        length),不代表那份輸出真的寫出了這封信 —— 報告層的驗收在更外層,
        由 `record_report_writer` 另外記在 `llm.writer`。

        **設了卻沒生效必須自己跳出來**(批#104):使用者把推理強度設成 `max`、
        API 拒絕、退讓後用 provider 預設跑完,信照常寄出而沒有任何告警。
        """
        rec = _lt.build_record(provider, model, requested_effort=requested_effort,
                               applied_effort=applied_effort, usage=usage,
                               finish_reason=finish_reason, error=error,
                               elapsed=elapsed)
        rec.update({k: v for k, v in extra.items() if v not in (None, "", False)})
        if accepted and requested_effort and applied_effort != requested_effort:
            self.degraded.append(f"llm:effort_not_applied:{role}")
            self._log(f"[llm] ⚠ {role} 要求推理強度 {requested_effort},"
                      f"實際生效 {applied_effort or '(provider 預設)'}"
                      + (f" —— {extra.get('backoff_reason')}"
                         if extra.get("backoff_reason") else ""))
        slot = self.data.setdefault("llm", {})
        if accepted:
            slot[role] = _lt.merge_same_role(slot.get(role), rec)
        else:
            slot.setdefault("attempts", []).append({"role": role, **rec})

    def record_report_writer(self, complete: bool) -> None:
        """記下**真正寫出這封信的是誰**(第十輪外審 #7)。

        `record_llm_call(accepted=True)` 是 **API 層**的驗收;報告層的驗收
        (有沒有「我的明確立場」與一句話總結)發生在更外層,失敗時會走短版
        重試、Gemini 備援、最後退到確定性備援文字。也就是說 `llm.primary`
        可能宣稱某個 provider 是 writer,而信其實不是它寫的。

        `complete` 由呼叫端提供(它才知道報告層的判準)—— 這樣本模組
        不必認識 `_analysis_complete_enough`。
        """
        slot = self.data.setdefault("llm", {})
        if not complete:
            slot["writer"] = {"source": "deterministic_fallback",
                              "reason": "報告驗收未通過(缺立場或總結)"}
            return
        rec = slot.get("primary")
        if isinstance(rec, dict) and rec.get("provider"):
            slot["writer"] = {"source": "primary", "provider": rec["provider"],
                              "model": rec.get("model")}
        else:
            slot["writer"] = {"source": "unknown"}

    def record_identity_migration(self, stats: dict, coverage: dict,
                                  schema_version: int) -> dict:
        """Event Identity 的遷移結果(第十輪 P1-11)。

        `changed_pairs` 是「舊指紋 → 新指紋」的對照,由它算出兩件事:
          - **合併**:多個舊指紋收斂到同一個新指紋 —— 那正是新世代要的;
          - **分裂**:同一個舊指紋跑出多個新指紋 —— **那是缺陷**,
            代表正規化不是決定性的,必須當場看得見。

        `coverage` 由呼叫端統計(它才有 model_history),本模組只組裝與判讀。
        """
        # 第十一輪 P1-1:`changed_pairs` 是**觀測清單**,不是 dict ——
        # dict 會讓同一個舊指紋的第二次觀測覆蓋第一次,而「分裂」正是
        # 「同一個舊指紋跑出多個新指紋」,於是它在結構上就不可能被看到。
        # 舊格式(dict)仍收:歷史 state 裡可能還有,轉成單筆觀測。
        raw = stats.get("changed_pairs") or []
        if isinstance(raw, dict):
            raw = [{"entity": "", "event_type": "", "old": o, "new": n}
                   for o, n in raw.items()]
        by_old: dict = {}
        merged: dict = {}
        for obs in raw:
            if not isinstance(obs, dict):
                continue
            key = (obs.get("entity") or "", obs.get("event_type") or "",
                   obs.get("old") or "")
            by_old.setdefault(key, set()).add(obs.get("new") or "")
            merged.setdefault(obs.get("new") or "", set()).add(key)
        splits = {k: sorted(v) for k, v in by_old.items() if len(v) > 1}
        out = {"schema_version": schema_version,
               "recomputed": stats.get("recomputed", 0),
               "canonicalized": stats.get("canonicalized", 0),
               "collisions": sum(1 for v in merged.values() if len(v) > 1),
               "splits": len(splits),
               "recomputed_by_schema": stats.get("by_schema") or {},
               "history_schema_coverage": coverage}
        self.data["event_identity"] = out
        if splits:
            self.degraded.append("event_identity:split")
            self._log(f"[event-id] ⚠ 同一個舊指紋產生多個新指紋:{len(splits)} 組")
        return out

    def refresh_capability_health(self, health_fn, dq_summary=None) -> dict:
        """重算並寫回 `capability_health`(第十輪 P1-12 自主模組搬入)。

        `health_fn(summary, extra_inactive) -> dict` 由呼叫端注入 ——
        「哪些能力算健康」是資料品質的判準,不是 manifest 的職責。

        留在這裡的是**manifest 知識**:`data_checks` 的沿用、以及
        「抽取器跑過卻零存活 = 失效」這條判定(它讀的正是 manifest 裡的
        `llm_extractor`)。可重複呼叫 —— 抽取器跑完之後要能再補算一次,
        否則算的時候 `llm_extractor` 還不存在,抽取器**永遠不會**出現在
        `inactive_capabilities` 裡,而 manifest 與信件都看不到它失效。
        """
        summary = dq_summary if dq_summary is not None else (
            self.data.get("data_checks") or {})
        extra = []
        lx = self.data.get("llm_extractor") or {}
        try:
            survived = int(lx.get("survived") or 0)
        except (TypeError, ValueError):
            survived = 0
        if lx.get("called") and not survived:
            extra.append("llm_event_extractor")
        try:
            health = health_fn(summary, extra)
        except Exception as e:                  # noqa: BLE001 - 觀測性不得擋晨報
            self._log(f"[capability] 健康狀態彙整略過: {type(e).__name__}: {e}")
            return self.data.get("capability_health") or {}
        self.data["capability_health"] = health
        return health
