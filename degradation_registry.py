# -*- coding: utf-8 -*-
"""降級標籤的登記表 —— **資料,不是判準**。

從 `run_quality.py` 抽出(全案審查 2026-09-03 TC-2):那個檔有一道 1000 行的
硬閘門(`test_the_gate_pushed_out_two_more_boundaries`),而閘門的意思是
「把邊界推出去」,不是調高數字 —— 判準本體(`assess()`)留在 run_quality,
這裡只有兩個常數:

  * `KNOWN_DEGRADED`:逐一列舉的已知降級標籤(白名單而不是黑名單:沒見過的
    一定報成 `unknown_degradation`)。
  * `OPEN_FAMILIES`:後綴開放的家族前綴 —— 每一個都有專屬 finding 把話說清楚,
    豁免只是不再以「沒見過」的名義重報一次。**登記 ≠ 可以靜音。**

`run_quality` 以 `from degradation_registry import …` 再匯出,既有的
`rq.KNOWN_DEGRADED` / `rq.OPEN_FAMILIES` 呼叫端與測試不必改。
註冊守衛:`tests/incidents/test_batch_prod_0830.py::test_every_emitted_degradation_label_is_registered`
(AST 掃 `morning_report.py` 每一個 `_DEGRADED_STEPS.append`,對照這兩個常數)。
"""
from __future__ import annotations

#: **後綴開放的降級家族**(前綴豁免逐一列舉)—— 唯一的一份。測試
#: `test_every_emitted_degradation_label_is_registered` 也從這裡讀,不再手抄
#: 第二份(全案審查 2026-09-03 TC-3:測試那份寫 `llm:` 整段,比生產寬;而
#: `test_the_slim_retry_is_not_a_known_degradation` 明確要求 `llm:slim_retry:*`
#: 不得被豁免 —— 同一 repo 兩份判準互相矛盾)。
#: 每一個家族都有**專屬 finding** 把話說清楚(`luna_path_failed` /
#: `persistent_state_corrupt` / `recap_not_previous_session` / `state_write_failed` /
#: `data_quality_error`),豁免只是不再以「沒見過」的名義重報一次 ——
#: **登記 ≠ 可以靜音**:沒有 finding 的家族不得放進來。
OPEN_FAMILIES = ("llm:luna_path_failed:", "state:corrupt:",
                 "recap:not_previous_session:",
                 # 全案審查 2026-09-03 TC-2:這兩個動態家族先前既沒登記也沒有
                 # finding,每次出現都是「沒見過的降級步驟」(08/29 事故同型)。
                 "state:write_failed:", "dq:",
                 # `渲染-<卡片標籤>`(`_safe_render` 的每一張卡)—— 專屬 finding
                 # `render_card_failed` 列出少了哪幾張;主體那個字面另有 defect。
                 "渲染-",
                 # `llm:provider_refused:<payment|auth>`:專屬 finding
                 # `llm_provider_refused_<why>`(run_quality 從 manifest 的錯誤分類推)。
                 "llm:provider_refused:")

#: **刻意不登記、讓 `unknown_degradation` 把它們報出來**的家族 —— 這是作者的
#: 選擇(見 `morning_report` 12887 那段:「不進白名單,所以品質守門會把它當成
#: 沒見過的降級報出來 —— 那正是我們想要的:它每出現一次,那一班的信就少了
#: 推理」),`tests/test_data_validation.py::test_the_slim_retry_is_not_a_known_degradation`
#: 釘著。寫成常數是讓這個選擇**可審**:註冊掃描器對 f-string 家族只認
#: OPEN_FAMILIES(有專屬 finding)或這裡(刻意浮出);兩者都不是就紅。
#:   * `llm:slim_retry:<role>` —— 精簡重試 = 那一班沒有思考。
#:   * `llm:truncated:<role>` —— 輸出被截斷,主分析走跨 provider 備援。
#:   * `llm:<completion 結果>:<role>` —— `completion_contract.classify` 非
#:     NORMAL / TRUNCATED 的其他結果(content_filter 之類),同樣少見且該吵。
SURFACE_AS_UNKNOWN = ("llm:slim_retry:", "llm:truncated:", "llm:")

KNOWN_DEGRADED = frozenset({
    # 推理強度沒被 provider 套用:影響深度,不影響管線是否走完。
    "llm:effort_not_applied:primary",
    "llm:effort_not_applied:extractor",
    # TAIFEX 來源日期對不上該交易日(2026-08-11 首次在生產觸發:
    # 端點回前一天的資料)。行為是對的 —— 寧可留空也不要錯位
    # (批#83),缺的那一格與原因都在 manifest["chips"]。
    "chips:source_date_mismatch",
    # T86 法人資料當日缺席(2026-08-21 批新增的標籤,**當批漏了註冊**,
    # 缺席日會被誤報成「沒見過的降級」):熱度表只缺法人欄,其餘照常。
    "sector:institutional_missing",
    # 同一個 fetcher 的另一個消費端(全案審查 2026-09-03 FR-1):universe 快照
    # 的法人欄先前在 T86 抓空時靜默全 0,現在與 sector 路徑一樣留痕。
    "universe:institutional_missing",
    # 供應商擋下請求(餘額/金鑰)→ 跳過 legacy 直接走緊急備援
    # (2026-08-26)。**這裡註冊只是為了不落成「沒見過的降級」** ——
    # 它本身已經有專屬 finding(`llm_provider_refused_*`,說得出是哪一種、
    # 該做什麼),那條才是通報的主體。
    "llm:provider_refused:payment",
    "llm:provider_refused:auth",
    # 週回顧的延燒事件素材有壞列被跳過(2026-08-27):段落照出,只少骨架
    "weekend_week_review_rows",
    # 備援班的新鮮寄送紀錄讀取失敗(r2 外審 P1):守衛退回只看工作區
    "backup_idempotence_probe",
    # 代號→名稱對照當日取不到:公司鍵遷移照跑,只跳過錯歸因清理。
    "state:alias_map_unavailable",
    # 中職未來賽程有場次、但一場都對不到球場(CPBL 官網對 Actions 的海外
    # IP 可能 geo-block)。賽程照出、只少場地;明細在 manifest.sports。
    "sports:cpbl_venue_missing",
    # TAIFEX 官網當日報表拿不到,退回已知落後的 OpenAPI(日期守衛仍會
    # 把不匹配的值擋在計分外;這是「今天的籌碼可能是舊的」的訊號)。
    "chips:pcr_site_fallback", "chips:large_site_fallback",
    # 時間預算不夠而跳過的加值步驟(核心報告仍完整)。
    "重大事件全文擷取", "podcast", "story_ledger", "story_ledger_save",
    "medical_journals", "sports", "policy",
    # 全案審查 2026-09-03 TC-2:這幾個先前**發得出來卻沒登記**,而掃描式守衛
    # 的正規式(單行、雙引號、純 ASCII)看不到它們 —— 中文/連字號、經
    # `_run_budget_ok` 間接呼叫、字典查表。守衛已改 AST,這裡把漏的補齊。
    "state-push 失敗", "候選/8-K 補抓全文", "LLM 新聞事件抽取(豐富化)",
    "sports:cpbl_full_year_error", "sports:cpbl_full_year_empty",
    # AST 掃描器第二輪解出的(先前的正規式看不到 `_lbl` 這種變數):兩個 LLM
    # 設定標籤都在 run_quality 的 `_ALARMING` 有專屬 finding(fatal 是 defect、
    # 非致命是 degraded)—— 登記只是讓掃描守衛過得去,不是靜音。
    "llm:config_invalid", "llm:config_issue",
    # 抽取器有批次因時間預算沒啟動:先前是帶批次索引的 f-string(每一個都是
    # 「沒見過」);改成固定字面,索引在 manifest `llm_extractor.batches[].outcome`
    # (`skipped:deadline`)。歸類與「候選/8-K 補抓全文」相同:時間預算不夠而
    # 跳過的加值步驟,核心報告仍完整 —— 已知且可接受,不另開 finding。
    "llm-extractor 批次(時間預算)",
    # ── 2026-08-30:**這一整批本來全都會變成「沒見過的降級步驟」。**
    # 08/29 的品質告警信實際印出「unknown_degradation —— 沒見過的降級
    # 步驟:gazette」,查下去發現不是漏一個,是漏 17 個 —— 只註冊了
    # `weekend_gazette` 而平日路徑發的是 `gazette`。新增標籤要同批註冊
    # 在消費端,否則退化成「未知」;現在有 `test_every_emitted_degradation
    # _label_is_registered` 機械化守住,不再靠記得。
    "gazette", "weekend_gazette", "weekend_policy_analysis",
    "weekend_week_review", "article_extractor", "horizontal_queries",
    "sector_map_unavailable", "story_ledger_corrupt",
    "analysis_recap_unreadable", "policy_keywords_load",
    "policy_keywords_save", "delivery_receipt_publish",
    # 除權息/公司行動(前綴族,逐個列出來才看得出漏了誰)
    "corpact:fetch_failed", "corpact:delisted_fetch_failed",
    "corpact:history_unreadable", "corpact:persist_failed",
    "corpact:update_failed",
})
