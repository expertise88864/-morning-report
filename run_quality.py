# -*- coding: utf-8 -*-
"""**今天的信「跑成了」嗎** —— 不是「有沒有跑」。

## 為什麼需要這個檔

`tools/report_watchdog.py` 檢查 `run_manifest.json` 的 `date` 夠不夠新,
回答的是「排程有沒有被擠掉」。那道守衛是對的,而且救過事 ——
但它對**跑起來了、卻跑壞了**的日子完全無感。

實際發生過的三次,看門狗全程安靜:

  * **2026-08-04 → 08-08(連續五天)**:特化路徑每天都被自己的引用檢查
    擋下、退回 legacy。信照樣寄出、manifest 照樣更新 —— 使用者是把信
    貼進對話裡才發現的。
  * **2026-08-06**:兩階段全文抓取整段 no-op(`clusters: 0`、`targets: 0`),
    因為 `source_item_id` 還沒補。信裡的事件只有 RSS 兩行摘要。
  * **2026-08-08**:昨日觀點閉環的 state 沒進 push 清單,GitHub Actions
    每天新 runner —— 寫了、從不 commit、次日讀不到。整條閉環是 no-op,
    而本機測試全綠。

三次的共同形狀:**每一塊都跑完了、每一塊都回報成功,而合起來的產出
比它該有的樣子差** —— 而沒有任何一個東西負責看「合起來」。

## 判準怎麼訂

只收**「這件事發生了就是缺陷」**的訊號,不收「今天資料比較少」這類
正常波動。誤報會訓練出忽略告警的反射,而那比沒有告警更糟 ——
這個 repo 已經為此拒絕過一次自動遮蔽(V2-N3)。

每一條都要說得出:**看到什麼、為什麼那是缺陷、讀者少了什麼**。
"""
from __future__ import annotations

import analysis_origin as _ao

#: 已知且可接受的降級步驟。**不在這裡的一律報出來** —— 白名單而不是
#: 黑名單,是因為新的降級原因會不斷出現,而「沒見過的降級」正是最
#: 需要被看見的那一種。
#: **一定生得出 ID 的命名空間。** 它們的來源資料每天都會組出來
#: (行情、校準、資料涵蓋度 —— 即使降級也會留下 note/error 欄位),
#: 所以空掉只可能是 prompt 宣告與 registry 對不上。
#: 其餘的(`portfolio:` / `tension:` / `fact:` / `universe:` …)
#: **當日真的沒有那類資料時空掉是正常的** —— 生產那邊的註解本來就
#: 這樣寫,而第一版的判準忽略了它,等於製造可預期的假警報。
ALWAYS_REALIZABLE = frozenset({"market:", "calibration:", "quality:"})

KNOWN_DEGRADED = frozenset({
    # 推理強度沒被 provider 套用:影響深度,不影響管線是否走完。
    "llm:effort_not_applied:primary",
    "llm:effort_not_applied:extractor",
    # 時間預算不夠而跳過的加值步驟(核心報告仍完整)。
    "重大事件全文擷取", "podcast", "story_ledger", "story_ledger_save",
    "medical_journals", "sports", "policy",
})


def _dig(obj, *path, default=None):
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


#: 特化路徑**走完就一定會留下**的紀錄。少任何一格,那份 manifest
#: 就與它自己宣稱的 `analysis_origin` 互相矛盾 —— 而先前
#: `{"llm": {"analysis_origin": "luna_specialized"}}` 這種最小 manifest
#: 會回**空 findings**:空集合真空通過,這個 repo 記過的形狀,
#: 而我自己蓋了一個(外審 P1-3)。
#:
#: 每一格都說得出誰寫的:
#:   payload_budget       ← `payload_budget.apply()`
#:   primary_metrics      ← `_accept_luna()`
#:   recap_saved          ← `_accept_luna()`
#:   request_measurements ← `record_llm_call()`(逐次成對)
SPECIALIZED_REQUIRED = (
    ("payload_budget", "預算政策沒有跑過(`payload_budget.apply`)"),
    ("primary_metrics", "沒有結構化指標(`_accept_luna` 沒走到)"),
    ("recap_saved", "沒有昨日觀點的存檔結果(同上)"),
    ("request_measurements", "沒有逐次的字元/token 量測(沒送出過請求)"),
)

#: strict 模式(CI canary)額外要求的執行身分欄位。
RUN_BINDING_FIELDS = ("git_sha", "github_run_id", "run_nonce")


def assess(manifest, *, mode: str = "watchdog",
           expected_sha: str = "", expected_run_id: str = "") -> list:
    """回 `[{code, severity, detail}]`(空 = 今天的信跑成了)。

    `severity`:`defect` = 程式或接線壞了;`degraded` = 讀者今天拿到的
    比應有的少,但可能是外部因素(額度、服務不穩)。兩者都值得說,
    分開是因為**要修的東西不一樣**。

    `mode`(外審 P1-3):
      * `watchdog` —— 每日生產。額度用罄那幾天退回 legacy 是外部因素,
        報 `degraded` 讓使用者知道,但不該天天當成程式缺陷。
      * `strict` —— CI canary。那個 job 的名字是「**證明特化輸出真的
        產生了**」,所以退回 legacy 就是不通過;而且要證明這份 manifest
        是**這一次執行**產生的(舊檔案永遠滿足不了 SHA/run id 綁定)。
    """
    m = manifest if isinstance(manifest, dict) else {}
    strict = str(mode) == "strict"
    out: list = []

    def add(code, severity, detail):
        out.append({"code": code, "severity": severity, "detail": detail})

    # ---- 1. 主分析走了哪條路
    origin = _ao.normalize(_dig(m, "llm", "analysis_origin"))
    if origin == _ao.EMERGENCY_FALLBACK:
        add("analysis_emergency", "degraded",
            "信裡沒有任何模型判斷,寄出的是 Python 組的備援文字")
    elif origin != _ao.LUNA_SPECIALIZED:
        add("analysis_not_specialized", "degraded",
            f"主分析走的是「{_ao.describe(origin)}」—— "
            "特化路徑的事件卡、淨效果、橫向綜合今天都不在信裡")

    # ---- 2. 特化路徑被自己的驗證擋下(2026-08-04→08 連續五天)
    #
    # **修補成功的日子不算缺陷**(第一輪外審 F2):`luna_problems` 是
    # 累積的,第一次不合格、第二次修補成功時它仍然留著 —— 只看它非空
    # 就報 defect,會在**特化輸出順利寄出的日子**讓 canary 紅、看門狗
    # 回 2。誤報是這個模組最該避免的東西(見模組 docstring)。
    problems = _dig(m, "llm", "luna_problems", default=[]) or []
    if problems and origin != _ao.LUNA_SPECIALIZED:
        add("luna_rejected", "defect",
            f"特化輸出被驗證擋下 {len(problems)} 條:"
            + "；".join(str(p) for p in problems[:3]))

    # ---- 3. prompt 宣告了 registry 生不出來的命名空間
    #
    # **空掉不必然是缺陷**(第一輪外審 F4)—— 生產那邊的註解自己就寫著
    # 「當日真的沒有持倉/張力時空掉是對的」,而我在這裡把每一個空掉的
    # 命名空間都報成程式缺陷,等於製造可預期的假警報。
    # 只報 `ALWAYS_REALIZABLE`:它們的來源資料**每天都會組出來**
    # (行情、校準、涵蓋度即使降級也會留下 note/error 欄位),
    # 空掉只可能是 prompt 宣告與 registry 對不上 —— 那正是 2026-08-08
    # 讓特化分析連續五天作廢的那個缺陷。
    unreal = [x for x in (_dig(m, "llm", "unrealizable_namespaces",
                               default=[]) or [])
              if str(x) in ALWAYS_REALIZABLE]
    if unreal:
        add("namespace_unrealizable", "defect",
            "這些命名空間宣告在 prompt 裡卻生不出任何 ID(模型照規則猜"
            f"名字必被判不存在):{'、'.join(str(x) for x in unreal)}")

    phantom = _dig(m, "llm", "phantom_evidence_refs", default=[]) or []
    if phantom:
        add("phantom_refs", "defect",
            f"張力宣稱了 packet 沒有的路徑:{'、'.join(str(x) for x in phantom[:3])}")

    # ---- 4. payload 超出硬閘門
    if _dig(m, "llm", "payload_budget", "over_budget") is True:
        add("payload_over_budget", "defect",
            "請求超過硬閘門 —— 裁背景與第二層壓縮都沒能把它壓下來")

    # ---- 5. 兩階段全文抓取的接線(2026-08-06 整段 no-op)
    plan = _dig(m, "news", "fulltext_plan", default={}) or {}
    if plan:
        clusters = int(plan.get("clusters") or 0)
        targets = len(plan.get("targets") or []) if isinstance(
            plan.get("targets"), list) else int(plan.get("targets") or 0)
        # **「分不出群」與「今天沒有新聞」是兩件事**(第一輪外審 F3)。
        # 零新聞時零群集是必然結果,不是接線壞了 —— 報成 defect 會讓
        # 上游斷料的日子看起來像程式有 bug,而該查的地方完全不同。
        avail = plan.get("available_news")
        if clusters == 0 and isinstance(avail, int) and avail == 0:
            add("news_upstream_empty", "degraded",
                "今天一則新聞都沒抓到 —— 上游斷料,不是分群壞了。"
                "信裡的事件段落會是空的")
        elif clusters == 0:
            add("fetch_plan_no_clusters", "defect",
                f"有 {avail if isinstance(avail, int) else '未知數量'} 則新聞卻"
                "分不出任何事件群 —— 信裡的事件只會有 RSS 兩行摘要"
                "(2026-08-06 的形狀:source_item_id 還沒補)")
        elif targets == 0:
            add("fetch_plan_no_targets", "defect",
                f"分出了 {clusters} 個事件群卻一篇全文都沒排 —— 接線斷了")

    # ---- 6. 昨日觀點閉環(2026-08-08:state 沒進 push 清單)
    if origin == _ao.LUNA_SPECIALIZED and _dig(m, "llm", "recap_saved") is False:
        add("recap_not_saved", "defect",
            "分析成功但昨日觀點沒存下來 —— 明天的延續事件沒有 diff 基準")

    # ---- 7. 字元閘門還擋得住嗎(代理的誤差要被量,不是被假設)
    import payload_budget as _pb
    head = _pb.proxy_headroom(m)
    if head:
        implied = head["implied_token_ceiling"]
        floor = _pb.OBSERVED_REJECTED_TOKENS * _pb.PROXY_ALERT_FRACTION
        if implied >= floor:
            add("payload_proxy_thin", "defect",
                f"字元上限今天只換到 {implied:,} token 的餘裕 —— 逼近實測會被"
                f"拒收的 {head['observed_rejected_tokens']:,}"
                f"(今日 {head['chars_per_token']} 字元/token)。"
                "閘門擋的是字元、provider 算的是 token,而這個比例隨當日"
                "語言組合浮動:偏中文的日子同樣的字元預算會換到多得多的 token")

    # ---- 8. **宣稱走了特化路徑,就要留下那條路徑的紀錄**(外審 P1-3)
    if origin == _ao.LUNA_SPECIALIZED:
        missing = [f"`{k}`({why})" for k, why in SPECIALIZED_REQUIRED
                   if _dig(m, "llm", k) is None]
        if missing:
            add("manifest_incomplete", "defect",
                "manifest 說走的是特化路徑,卻缺了那條路徑必然會寫下的"
                "紀錄:" + "、".join(missing)
                + " —— 要嘛是主流程中途死掉,要嘛這份 manifest 不是"
                  "這一次執行產生的")

    # ---- 9. 最終 request 被閘門擋下(packet 沒超不代表 request 沒超)
    # **終局的超標才是缺陷**(第二輪外審 F2)。加深那次超標時主流程會
    # `break` 並沿用留著的合法第一版 —— 那條路徑走完仍是特化輸出,
    # 對讀者沒有任何損失。只看旗標會對一份成功的報告報 defect。
    if (_dig(m, "llm", "payload_budget", "final_request_over_budget") is True
            and _dig(m, "llm", "payload_budget",
                     "final_request_over_budget_recovered") is not True):
        add("final_request_over_budget", "defect",
            "最終 request 超過硬閘門而被擋下,且沒有回收 —— packet 層的 "
            "over_budget 是 False,只看那一格會完全看不到這件事")

    # ---- 10. strict(CI canary):綠燈必須代表「特化輸出真的產生了」
    if strict:
        if origin != _ao.LUNA_SPECIALIZED:
            add("canary_not_specialized", "defect",
                f"canary 的判準是「特化輸出真的產生了」,而這一班走的是"
                f"「{_ao.describe(origin)}」—— 退回 legacy 對每日生產是"
                "可接受的降級,對 canary 是不通過")
        missing_bind = [k for k in RUN_BINDING_FIELDS if not _dig(m, k)]
        if missing_bind:
            add("run_binding_missing", "defect",
                f"manifest 沒有執行身分:{'、'.join(missing_bind)} —— "
                "沒有它就證明不了這份檔案是這一次跑出來的")
        for got, want, what in ((_dig(m, "git_sha"), expected_sha, "git_sha"),
                                (_dig(m, "github_run_id"), expected_run_id,
                                 "github_run_id")):
            if want and str(got or "") != str(want):
                add("run_binding_mismatch", "defect",
                    f"{what} 對不上:manifest 是 {got or '(空)'}、"
                    f"這一次是 {want} —— **讀到的是別次執行的 manifest**"
                    "(`state/run_manifest.json` 進版控,checkout 之後就在那裡)")

    # ---- 11. 沒見過的降級
    unknown = [s for s in (m.get("degraded_steps") or [])
               if str(s) not in KNOWN_DEGRADED]
    if unknown:
        add("unknown_degradation", "degraded",
            "沒見過的降級步驟:" + "、".join(str(s) for s in unknown))

    return out


def summarize(findings) -> str:
    """給告警信用的一段文字(空 = 回空字串)。"""
    rows = [f for f in (findings or []) if isinstance(f, dict)]
    if not rows:
        return ""
    defects = [f for f in rows if f.get("severity") == "defect"]
    head = (f"今天的晨報跑起來了,但有 {len(rows)} 項不對"
            + (f"(其中 {len(defects)} 項是程式或接線的缺陷)" if defects else "")
            + ":")
    return head + "\n" + "\n".join(
        f"  [{f.get('severity')}] {f.get('code')} —— {f.get('detail')}"
        for f in rows)
