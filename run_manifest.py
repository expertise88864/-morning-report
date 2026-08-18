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

import os

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
    "corporate_actions", "chips", "llm",
    "state_writes", "event_identity",
    # 一次性的 state 清理(2026-08-18 P1-1):清掉幾條錯歸因、留下幾條。
    # **靜默清理與靜默污染一樣糟** —— 它動的是 120 天的歷史。
    "state_migrations",
    # 兩階段抓取的計畫(重構規格 Commit B):**涵蓋了幾個事件、漏了哪幾個**。
    # 只記「抓了幾篇」會讀起來像涵蓋完整 —— 沒有靜默的上限。
    "news",
    # Luna 特化實驗:**這一列是十配對的原始資料**。
    # r2(Codex,#4):刻意獨立於 `llm_shadow` —— 那個鍵在既有路徑結尾是
    # **整包指派**,寫在它底下的失敗紀錄會被靜默蓋掉,而可靠度指標
    # 又回到「只量 Luna 成功的那些天」。
    # 盲評卡的**存在性**(日期/路徑/通道/解碼表在不在)。刻意只有指標,
    # 沒有文字 —— manifest 會 commit 進公開 repo,而卡片含兩份完整分析。
)

#: 刻意**不**落地的鍵:`marks` 是階段計時的中間結構,已經被彙整成 `phases`,
#: 原樣寫出去只是重複且龐大。
TRANSIENT_KEYS = ("marks",)


def run_binding() -> dict:
    """這一次執行的身分:`git_sha` / `github_run_id` / `run_nonce`。

    CI 上兩個 env 都有;本機跑時退回 `git rev-parse HEAD`,
    再不行就留空字串 —— **留空是誠實的**,而斷言端會把「該有卻沒有」
    當成不通過(見 `tools/assert_run_quality.py` 的 strict 模式)。
    """
    import subprocess
    import uuid
    # **nonce 由外面給才擋得住「同一次 run 的第二次執行」**
    # (第二十七輪外審 P2-5):自己隨機產生的話,斷言端只驗得了「非空」,
    # 而那只是一個存在性欄位 —— 證明不了「這是那一次 process invocation」。
    # workflow 產生一次、同時交給生產與斷言,比對才有意義;
    # 沒有給就退回隨機值(本機跑時仍然是誠實的)。
    nonce_env = os.environ.get("RUN_NONCE") or ""
    sha = os.environ.get("GITHUB_SHA") or ""
    if not sha:
        try:
            sha = subprocess.run(["git", "rev-parse", "HEAD"],
                                 capture_output=True, text=True,
                                 timeout=10).stdout.strip()
        except Exception:                      # noqa: BLE001
            sha = ""
    return {
        "git_sha": sha,
        "github_run_id": os.environ.get("GITHUB_RUN_ID") or "",
        # nonce 讓「同一個 SHA、同一個 run」的兩次執行也分得開
        # (手動 dispatch + 排程班撞在一起時)。
        "run_nonce": nonce_env or uuid.uuid4().hex,
    }


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

    def record_fulltext_plan(self, plan: dict) -> None:
        """兩階段抓取的計畫(重構規格 Commit B)。

        **記的是「涵蓋了幾個事件、漏了哪幾個」** —— 只記「抓了幾篇」
        會讀起來像涵蓋完整,而逐事件群分配預算正是為了解掉那個錯覺。
        """
        p = plan if isinstance(plan, dict) else {}
        entry = {
            "targets": len(p.get("targets") or []),
            "clusters": len(p.get("per_cluster") or []),
            "uncovered_clusters": list(p.get("uncovered_clusters") or [])[:10],
            "basis": str(p.get("basis") or ""),
        }
        # **「分不出群」與「今天根本沒有新聞」要分得開**(第二輪外審 F2)。
        # `plan()` 記了 `available_news`,而這裡重建 entry 時只抄四個欄位 ——
        # 於是判準拿到 `None`,零群集又被一律報成接線缺陷。
        # 這是「生產端記了、下游沒帶」—— 逐欄複製的清單漂移形狀。
        # **逐欄複製就是清單漂移**(上一次已經被抓過一次):改成逐項帶,
        # 加一格判準只要在這個 tuple 裡加一個名字。
        for _k in ("available_news", "fetchable_candidates",
                   "already_fulltext", "no_fetch_link"):
            if isinstance(p.get(_k), int):
                entry[_k] = p[_k]
        self.data.setdefault("news", {})["fulltext_plan"] = entry

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
    def build(self, *, date: str, report_kind: str, budget_seconds: float,
              news_workers: int, degraded_steps,
              feeds: Optional[dict] = None) -> dict:
        """組出要落地的 manifest。**純函式** —— 不寫檔、不印東西。

        診斷鍵一律由 `DIAGNOSTIC_KEYS` 統一帶出,不逐項明列:
        逐項明列正是那個發生過八次的坑(而且會與這裡的 tuple 重複)。
        """
        out = {
            "date": date,
            # **這一班寄的是哪一種信**(2026-08-09 生產)。週日綜合信走的是
            # 輕量路徑,根本不跑主分析 —— 而 `run_quality` 不知道有這回事,
            # 於是每個週日都會發一封「有段落沒跑成」。
            # **誤報是這類守衛最貴的失效方式**:它會讓人開始忽略那封信。
            # 沒有這一格時當成平日報(那是會出聲的那一邊)。
            "report_kind": str(report_kind),
            # **這份 manifest 屬於哪一次執行**(外審 P1-2)。
            #
            # `state/run_manifest.json` 是**進版控的**,所以 CI checkout
            # 之後它本來就在那裡 —— 若這一班在寫 manifest 之前就掛掉,
            # 斷言腳本讀到的是**上一班的檔案**,而上一班可能剛好是健康的:
            # 「canary 綠」於是證明不了任何事。
            #
            # 綁定是**性質**而不是程序:`rm -f` 那種前置步驟會被忘記,
            # 而「SHA/run id 對不上」是舊檔案永遠滿足不了的條件。
            **run_binding(),
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
        # **成對的字元/token 要獨立留一份**(第二輪外審 F1)。
        # `merge_same_role` 把 token **累加**、其餘欄位取最新 ——
        # 加深成功那天有兩次 accepted 呼叫,於是 `request_chars` 是第二次的、
        # `prompt_tokens` 是兩次的和,除出來的比例沒有意義,而且會偏小
        # (看起來像「字元換到很多 token」)→ 假的 `payload_proxy_thin`。
        # 角色槽是**彙總**(成本、次數),量測要的是**逐次成對**,
        # 兩種需求不該共用同一個容器。
        _chars, _toks = rec.get("request_chars"), rec.get("prompt_tokens")
        if (isinstance(_chars, int) and _chars > 0
                and isinstance(_toks, int) and _toks > 0):
            slot.setdefault("request_measurements", []).append(
                {"role": role, "chars": _chars, "tokens": _toks,
                 "accepted": bool(accepted)})
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


def call_counts(llm: Optional[dict]) -> dict:
    """這一班真的送出了幾次 provider 呼叫、其中幾次計費卻量不到 usage。

    第十三輪 P1-4:一份報告可能是「Luna 不合格 + 修補 + 影子」= 三次,
    而帳本一天只有一列。用列數冒充呼叫數會低估帳單,而**低估的方向正好
    偏向「這個實驗很便宜」** —— 也就是這個實驗要下的那個結論。
    """
    d = llm if isinstance(llm, dict) else {}
    att = [a for a in (d.get("attempts") or []) if isinstance(a, dict)]
    # r1(Codex,#2):**accepted 的那一格可能已經是多次呼叫的累加。**
    # `merge_same_role` 維護 `calls`(抽取器重試、短版重試都會讓它 >1),
    # 而原本一律算 1 —— 呼叫數又被低估,方向還是偏向「這個實驗很便宜」。
    # 缺 `calls` 的舊紀錄才退回 1。
    accepted = [d.get(s) for s in ("primary", "extractor", "shadow")]
    return {"provider_calls": len(att) + sum(
                int(a.get("calls") or 1) for a in accepted if isinstance(a, dict)),
            "billable_unmeasured_calls": sum(
                1 for a in att if a.get("billable_unmeasured"))}


def luna_path_failure(exc, *, redact, packet_built: bool) -> tuple:
    """Luna 特化路徑失敗時要留下的痕跡,回 `(降級標籤, manifest 條目)`。

    2026-08-03 實機:那條路徑失敗了,而**失敗原因沒有任何地方留下來** ——
    降級清單只有一個沒有型別的標籤、例外訊息只進 job log(公開 repo 匿名
    讀不到),而 packet 還沒建好所以連實驗紀錄都沒有。那一天因此完全無法
    診斷,只知道「失敗了」。

    `stage` 是關鍵:packet 建好了沒,決定失敗發生在**組裝證據**還是
    **呼叫模型** —— 那正是當天分不出來、而排查方向完全不同的一件事。
    """
    why = f"{type(exc).__name__}: {redact(str(exc))}"[:200]
    # 2026-08-07 flash E2E:AttributeError 只有一行訊息,stage 也只分兩段,
    # 光看紀錄找不到炸點在哪個函式 —— 把 traceback 最後三格的「檔:行 函式」
    # 一併記下(不含原始碼行,避免把外部字串帶進 manifest)。
    frames = []
    tb = getattr(exc, "__traceback__", None)
    while tb is not None:
        code = tb.tb_frame.f_code
        frames.append(f"{code.co_filename.rsplit(chr(92), 1)[-1].rsplit('/', 1)[-1]}"
                      f":{tb.tb_lineno}:{code.co_name}")
        tb = tb.tb_next
    entry = {"error": why,
             "stage": "analysis" if packet_built else "packet_build"}
    if frames:
        entry["trace"] = frames[-3:]
    return (f"llm:luna_path_failed:{type(exc).__name__}", entry)
