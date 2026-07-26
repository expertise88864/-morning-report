# A5 絞殺者模組化 施工圖(V3)

> 產出:2026-07-06,Fable 5 判讀。**讀者是後續執行的(較小的)模型**:照 §A 程序一步不跳,
> 用 `tools/refactor_audit.py` 驗證,不要憑讀碼直覺判斷「這個函式應該可以搬」。
> 本檔的候選清單是「當時的證據」——程式會演進,**執行時一律重跑 audit 重新驗證**。
> 前情:A5-Step1(llm_postprocess.py)與 Step2(render_utils.py)已完成,照同一模式做。

## 重要修正(推翻計劃書 §0.10 的舊 grep)
OPTIMIZATION_PLAN.md §0.10 的 `grep -oE '(mr|gr|pdg)\.'` 只抓得到 `mr.requests` 屬性式寫法,
**會漏掉九成目標**(tests 178/198 個 monkeypatch 是 `setattr(mr, "name", ...)` 字串式)。
一律改用:`python tools/refactor_audit.py nomove`(兩種寫法都抓,2026-07-06 實測共 **46 個名稱**)。

## A. 施工程序(每個模組一輪,機械化,零裁量)
1. 開工儀式照 CLAUDE.md §1(fetch+reset、pytest 基準綠)。
2. `python tools/refactor_audit.py nomove` → 輸出貼進 commit message 當證據。
3. `python tools/refactor_audit.py group <本輪函式們>` → 必須 **ALL-CLEAR**。
   出現 BLOCK/OPEN/TRAP → 按提示縮群;縮到剩不到 50 行就放棄這一輪(誠實少搬)。
4. 建新模組:模組 docstring 寫「從 morning_report.py 抽出+原則(本體逐字不改、mr 保留同名 re-export)」;
   函式本體**整段剪下貼上一字不改**;工具列出的「隨行常數」一併搬;補齊 stdlib import。
5. morning_report.py 頂部 `from <新模組> import (...)`(函式+常數全部同名 re-export);
   刪原定義;把殘留的 4 個以上連續空行壓成 2 個。
6. `python tools/refactor_audit.py verify-move <新模組> <函式們>` → 全部 OK(與 HEAD 逐字相同)。
7. `ruff check .` → `python -m pytest`(**tests 一字不改**、基準只能增不能減)。
8. 一模組一 commit(繁中,附 nomove 與 verify-move 輸出摘要)→ Codex 閘門(CLAUDE.md §2)→ APPROVE 才 push。
9. 失敗分支:pytest 掉 → `git checkout -- .` 回退本輪,把原因寫進本檔「§E 嘗試紀錄」再停下;
   不確定怎麼縮群 → 停下問使用者,不要猜。

## B. 候選模組(2026-07-06 證據;依風險由低到高執行)
audit 掃出 289 個頂層函式中 **156 個屬可搬閉包**。判 OK ≠ 該搬——以下是判斷後值得搬的:

### B1|`num_utils.py` — 數值基礎 helper(~30 行,風險:極低)【先做,解鎖後面所有輪】
`_safe_number`(L5970)、`safe_float`(L465)、`_to_int`(L1427)、`_sigmoid`(L6322)。
理由:全 codebase 最高頻的黏著劑——audit 的 1,966 行大群其實是十幾個主題群被 `_safe_number` 黏成一團。
先抽它,後面每個新模組直接 `from num_utils import _safe_number`,群組才拆得開。
注意:群組驗證時這 4 個一起下 `group` 指令;搬完後**後續輪的新模組 import num_utils,不是 import morning_report**。

### B2|渲染區塊 → 併入既有 `render_utils.py`(~500 行,風險:低)
`_render_sports_html`(L12119,181 行)、`_render_kpi_strip`(L10368,158 行)、
`_render_podcast_html`+`_podcast_ticker_crosscheck`(L12497/12471,91 行)、
`_render_model_evidence_html`(L10555,64 行)、`_render_event_calendar_html`(L10997,18 行)。
均為零依賴獨立群(audit 已證)。注意:A4 golden 煙霧測試蓋 render_html 主體,這些子區塊搬走後重跑必綠才算。

### B3|`news_rules.py` — 新聞分類/降噪純規則(~250 行,風險:低)
`classify_news_importance`(L8832)、`_matches_any`、`_news_source_grade`、`_grade_from_text`、
`_news_keep_score`、`dedup_news`(L5289)、`_is_low_value_tech_headline`、`_strip_html`、
`_tw_intelligence_importance`+`_tw_intelligence_topic`+`_tw_intelligence_entity_key`+`_tw_intelligence_timeline_key`+`_tw_intelligence_recall_hit`。
隨行常數多(ECON_DATA、FED_EVENTS、NEWS_POSITIVE/NEGATIVE_TERMS、TECH_*、TW_INTELLIGENCE_*、TW_MEDICAL_*)——
工具會列全,**常數也要 re-export**(有測試直接讀)。GOOGLE_NEWS_COMPANIES、RSS_FEEDS 被 patch:它們屬抓取層、
本來就不隨行,若 group 顯示依賴到它們=群組劃錯了,縮群。

### B4|`session_calendar.py` — 交易日/預測日期工具(~160 行,風險:低)
audit 的獨立群:`evaluate_breakout_forecasts`、`build_breakout_tracking`、`_normalize_history_entries`、
`_weekday_session_distance`、`_resolved_prediction_history`、`_actual_open_date_for`、
`_infer_target_session_date`、`_session_distance`、`_next_tw_weekday`、`_target_session_date`。

### B5|`news_events.py` — 結構化事件抽取(~400 行,風險:中——是 Top5 催化劑的輸入)
`extract_structured_events`(L7341)、`_event_type`、`_event_cluster_key`、`_event_surprise_score`、
`_shrunk_event_impact`、`_news_event_direction`、`_freshness_weight`、`_parse_news_time`、
`_parse_news_time_required`、`_entry_published_dt`、`_event_lifecycle`、`_event_timeline_key`、
`apply_event_timeline`、`_event_study_dedupe_key`、`_stock_news_catalysts`(L7577,105 行)、
`_validate_llm_events`+`_LLM_EVENT_TYPES`。依賴 num_utils(B1 先行)與 news_rules(B3 先行)。
風險中的原因:呼叫者含計分鏈——**本體逐字不改+verify-move 全 OK** 是硬條件,任何 DIFF 都回退。

### B6|`model_math.py` — 模型擬合/校準機械(~700 行,風險:中,可放棄)
`_ridge_fit_model`、`_quantile_ridge_fit_model`、`_quantile_ridge_fit_predict`、`_ridge_fit_predict`、
`_linear_model_predict`、`_feature_matrix`、`_model_feature_raw`、`_platt_fit`、`_platt_params_for_rows`、
`_platt_params_for_blended_rows`、`_calibrated_beat_probability`、`_probability_calibration_metrics`、
`_time_decay_weights`、`_nan_weighted_mean_std`、`build_model_training_rows`、
`evaluate_model_rolling_origin`、`evaluate_model_walk_forward`、`_recent_direction_hit_pct`。
隨行常數:MODEL_FEATURES/MODEL_TARGETS/MODEL_PURGE_GAP/MODEL_VERSION(**都要 re-export**,
backtest_data/ 腳本與 tests 有讀)。這是預測核心的機械層——搬遷不改行為,但錯一字就斷預測,
只在 B1-B5 都順利、且同一 session 還有餘裕時做;做完後 `calc_*` 頂層函式**留在 mr 不搬**(域邏輯+高敏感)。

### 明確不搬(留在 morning_report.py)
- 所有 `fetch_*`(網路)、`render_html`/`_build_prompt`/`main`(巨型+狀態+互相糾纏,見 §C)、
  `calc_taiex_prediction`/`calc_stock_price_forecast` 等計分域邏輯(敏感度>收益)、
  state 存取(`save_*`/`load_*`/`_git_commit_and_push_state`)、LLM 呼叫(`_call_llm_text` 被 patch)。
- 46 個 nomove 名稱本身可以搬(re-export 後 patch 仍有效),但**群內互呼被 patch 者=TRAP**,工具會擋。

### 預期成果
B1-B5 全做完 ≈ 再瘦 1,300-1,500 行(14,846 → ~13,400);加 B6 ≈ ~12,700。
每輪獨立成立,做到哪停到哪都是淨改善。

## C. main() 拆分(原 A5-Step3)——凍結,需使用者拍板
main() 637 行、~100 個交錯 local。**不要在本機做**:驗證需要完整跑 pipeline,而本機無 LLM 金鑰、
yfinance 被 geo-block。誠實的限制:**連 CI 上「改前 vs 改後各跑一次 dry-run 比 HTML」都不是逐字可比**——
兩次執行抓的是不同時刻的即時行情/新聞,天然就有差異。可行方案(給使用者選):
1. **結構比對法**(便宜):CI 先在 main 連跑兩次 dry-run 取得「天然差異基線」(區塊清單、各區塊行數級大小),
   拆分支的 artifact 與基線比「區塊存在性+順序+數量級」而非逐字。可信度中。
2. **錄放法**(貴,~2-3 天工):加 RECORD/REPLAY 模式把所有 fetch 結果錄成 fixture,replay 下逐字比對 HTML。
   可信度高,且 fixture 日後可用於回歸測試。是正解但要立項。
3. **不拆**:B1-B6 完成後 main 佔比已下降,維持現狀的成本可接受。
未拿到使用者決定前,任何人不得動 main()。

## D. 誠實條款(harness 極限)
- 本檔候選是靜態分析+人工判讀的結論;**執行時 audit 說了算**,衝突時以工具+縮群為準。
- 工具的 BLOCK 判定偏保守(os/Path/time 一律擋)——寧可少搬,不要放寬工具去遷就候選清單。
- 模組命名/邊界是品味題:照本檔命名即可;想改名或重劃邊界=問使用者,不要自由發揮。
- 搬遷不會讓晨報「更準」——它買的是可維護性與後續改動的安全性。別為搬而搬。

## E. 嘗試紀錄(執行者隨做隨記)
- **B1 ✅(2026-07-10,03879c9)**:num_utils.py — safe_float/_to_int/_safe_number/_sigmoid。
- **B2 ✅(2026-07-10,fdc06d3)**:render_utils.py +6 區塊渲染(_render_kpi_strip/_render_sports_html/
  _render_podcast_html/_podcast_ticker_crosscheck/_render_model_evidence_html/_render_event_calendar_html)。
- **B3 ✅(2026-07-10,b375cb8)**:news_rules.py — 13 新聞分類/降噪函式 + 21 關鍵字常數。
- **B4 ✅(2026-07-10,281646d)**:session_calendar.py — 10 交易日/預測日期工具。
- **累計:morning_report 14,975→13,805 行(移出 ~1,170)。全部 Codex gpt-5.6-sol APPROVE、tests 零修改 489 passed。**

**做法要點(給後續輪沿用)**:
1. 大搬移用 scratchpad 的 AST 腳本(move_bN.py),依「原始行序」搬(保常數間相依),再 `re.sub(r"\n{4,}","\n\n\n")` 收空行。
2. **audit 的依賴分析有盲點:漏抓 `typing.Optional` 這類註解 import**(B2/B3/B4 都補了 `from typing import Optional`)。新模組若函式簽名用 Optional/Any/Callable,記得補 typing import。
3. **re-export 精簡**:不盲目 re-export 全部;先 `ruff` 找 F401,再 grep tests/其他 .py 確認「零外部引用」的就從 morning_report import 移除(只留本體/測試/其他模組實際引用者),保命名空間乾淨。被 test 經 mr.* 讀取但本體沒用的加 `# noqa: F401`。
4. 每輪:audit group ALL-CLEAR → 腳本搬 → 加 re-export → verify-move byte-identical → ruff+compile → 全套 pytest(不可跌破 489)→ Codex gpt-5.6-sol APPROVE → rebase → push。

**下一輪:B5(news_events,結構化事件抽取,~400 行,依賴 num_utils+news_rules,風險中——是 Top5 催化輸入,verify-move byte-identical 是硬條件)。之後 B6(model_math,可放棄)。**
