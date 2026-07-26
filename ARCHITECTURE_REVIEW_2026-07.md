# 晨報系統 全架構 Review(2026-07-06,Fable 5)

> 範圍:新聞抓取/分析、預測精確度基礎、email 投遞、RAG-ready 資料架構、可維護性/技術債。
> 方法:三路獨立掃碼(新聞管線/預測與資料品質/渲染與存檔)→ 主審逐條查證(有幾條被推翻,見 §F)。
> **讀者是後續執行的模型**:每條標了「桶別」——A=可直接做、B=需使用者拍板、C=需回測+拍板、D=不建議。
> 標 ⚠️ 的=發現時未逐行驗證,**動工前先讀碼確認**;標 ✅ 的=主審已驗證屬實。
> 執行紀律一律照 `CLAUDE.md`(硬規則)與 `OPTIMIZATION_PLAN.md` §0;重構類照 `A5_MODULARIZATION_MAP.md`。

## §A|可直接做(不碰計分、Sonnet 級可執行)——建議的 V3 批次 1

> ### ✅ 執行結果(2026-07-06,Opus 4.8,3 個本地 commit、全套 480 passed)
> 開工逐項讀碼查證後,**六項只有三項是真缺口**——這是誠實的好消息(程式比三路 review 顯示的更完整):
> - **A1 已做**(commit e1d0fbd):bt_factor_ic FACTORS 補到涵蓋 MODEL_FEATURES 全 22 項;已驗證缺的 10 項都在快照 keep 集合。
> - **A4+A6 已做**(commit c55768d):preheader 隱藏預覽文字(+2 測試含冪等);fetch_news docstring 24h→30h。
> - **A5 已做**(commit ad6e951):USD/TWD 失敗採 history 昨值降級(用既有 history、不新增 state 檔;純顯示非計分)。
> - **A2 免做——已存在**:build_data_quality:13929-13949 早已檢查「三大法人非零覆蓋率 <0.3 → error」,
>   且有測試 `test_build_data_quality_detects_zero_filled_institutional`。資料品質「移後台只餵 LLM」是使用者決定,
>   再加信件警示會抵觸該決定 → 不做。
> - **A3 免做——會抵觸使用者決定**:`_render_model_evidence_html` 已有白話判決(綠「邊際優勢」/黃「尚未穩定贏過
>   基準」=漂移預警/「樣本累積中」),且測試註明「詳細表格隱藏,只留一句白話結論」是使用者要求。加原始命中率
>   數字會違反該決定。真正的缺口(獨立 ADMIN_EMAIL 漂移警示)是新功能 → 歸 §B 待拍板。
> - **A7 免做——已有完整測試**:截斷邏輯有 9+ 個 `test_render_html_size_guard_*`(trim 順序/podcast 縮減/
>   compact points/keep 模式/shown episodes/端到端 Gmail 上限),agent 說「零單測」是錯的。
>
> **⚠️ 推送閘門阻塞**:4 個 commit(+§B)仍在**本地**;Codex GPT-5.5 今日額度用罄(2026-07-07 09:52 重置),
> 依 CLAUDE.md §2「APPROVE 才 push」尚未推送。**已排程** `codex-gate-v3-batch1-push`(2026-07-07 10:17 台北)
> 自動跑 Codex review→APPROVE 才 push、並補 §G(見 §G)。
>
> ### ✅ §B 已做(commit 28ce4a4):信件 HTML 存檔(RAG 缺口)
> archive_report_html:寄信後把去識別 HTML 存 state/emails/<date>.html.gz(保留 365 天),週間/週末都存、
> 已進兩條 push 清單。隱私:以 `<!--PF_ROW_START/END-->` 標記精準移除 KPI 持股列(帳上損益金額不落地 repo)。
> §B 另一半「0050/00662 預測寫 history」查證後**早已存在**(pred_0050/fair_00662/pred_taiex),不需做。
>
> ### ⏳ §C 無法現在做(時間鎖)+ §B/§C 說明見各段
> §C 三項都改計分輸入,須 IC 證據(約 2026-09 D1 樣本才夠)。**A1 已完成其前置**(補因子到 bt_factor_ic);
> 就緒提醒鏈(monthly-ic 月報 → d1_readiness)已就位。到期執行工單見 §C 末。


### A1|bt_factor_ic 因子清單補齊(✅已驗證;小工,高槓桿,影響 9 月 D1)
- 證據:`MODEL_FEATURES`(morning_report.py:5999)22 項,`backtest_data/bt_factor_ic.py:17` FACTORS 只涵蓋其中 12 項。
  無 IC 追蹤的:`foreign_lot, invest_lot, invest_30d_lot, foreign_streak, invest_streak, margin_change_lot, news_catalyst_score, trade_value, slippage_bps, rel_strength_5d`。
- 後果:D1 驗收(≈2026-09)以「IC |t|>2」為權重變更門檻——沒追蹤的因子永遠拿不出證據。
- 做法:把缺的加進 FACTORS(先確認 model_history 逐日快照確實有存這些欄位——用一天的 state 資料 grep key 驗證;沒存的欄位列出來回報,不要硬加)。只影響月報輸出,零風險。
- 驗收:本機 `python backtest_data/bt_factor_ic.py`(需 model_history.json,本機沒有就在 monthly-ic workflow 跑)顯示新因子行(樣本不足屬正常)。

### A2|籌碼資料靜默失效 → 可見化(⚠️行號未逐行驗證,方向已確認)
- 證據:`fetch_twse_institutional` 失敗回 `{}` → 三大法人特徵全 0 靜默進排名;`build_data_quality`(L13785 起)只查「有沒有 ~100 檔」不查內容。
- 做法(顯示/監控層,不動計分):data_quality 加「三大法人非零覆蓋率」檢查(<30% → error);信件資料品質區標「籌碼資料異常,今日排名籌碼訊號減弱」。**不要**動 ranking 本身的降權(那是計分,歸 §C)。
- 驗收:單測餵全零 inst dict → data_quality 顯示 error。

### A3|模型監控進信件(顯示層;資料都已算好)
- 證據:`evaluate_model_walk_forward`(L6969)有 direction_hit_pct/MAE、Top5 熔斷有淨報酬——但信件只顯示樣本數/Brier/ECE,沒有「近 N 日命中率 vs 30 日均值」。漂移要等熔斷才被動發現。
- 做法:資料品質(或模型證據)區塊加 2-3 行:「3d 方向命中 近10日 X% / 30日 Y%」「昨日 Top5 平均實現 Z%」。純 render 疊加,不裁任何區塊、不動熔斷邏輯。
- 驗收:單測 + DRY_RUN 預覽。

### A4|preheader 預覽文字(便宜,高感知效益)
- 證據:render_html 無 preheader,Gmail 收件匣預覽抓信首(天氣/KPI 雜訊)。
- 做法:`<body>` 後第一個元素插 `<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">今日:加權{預測} 2330 {預測} 00662 {公允}…</div>`(≤80 字,取當日最重要 3 個數字)。注意隱私:不得含持股資訊。
- 驗收:單測(HTML 含該 div 且在最前)+ DRY_RUN;寄一封實測 iPhone Gmail 預覽。

### A5|stale-cache 降級:USDTWD 先行(⚠️)
- 證據:USDTWD 抓失敗當天就無資料(L~13820 直接 error),但匯率日波動 ±0.5%,昨值遠勝無值。
- 做法:成功時寫 `state/last_known_usdtwd.json`(**必須加進 `_git_commit_and_push_state` 清單**——CLAUDE.md 地雷 #1);失敗時讀快取,≤7 天就用並在信中標「昨值(N 小時前)」,超過才 error。月營收/TDCC 的同型降級較大,放 §B3。
- 驗收:單測 fake 失敗路徑;確認 state 清單有新檔。

### A6|文件債微修:fetch_news docstring「24 小時」改「30 小時」(✅已驗證不一致,L4093-4095)。順手,不單獨成 commit。

### A7|truncation 單測(測試債,零行為變化)
- 證據:智慧截斷(Podcast 8→5→3 等,L~13651-13743 ⚠️)零單測;A4/preheader、A3 等都會動到信件大小,先有測試網再動。
- 做法:mock `_estimated_email_kb` 強制超標,逐層驗證縮減行為與冪等(重複組裝結果相同)。

> 批次 1 順序建議:A7(先鋪測試網)→ A1 → A2 → A3 → A4 → A5 → A6。每項一 commit,照 CLAUDE.md 流水線。

## §B|需使用者拍板(行為/成本/範圍變化)

### B1|寄出信件原文存檔(RAG 最大缺口)
- 現況:state 只存結構化數字(history.json 450 天滾動),**信件原文無任何存檔**——無法回溯「6/30 那封信實際說了什麼、什麼被截斷了」。0050 預測也不入 history(只有 TAIEX)。
- 方案:寄出後存 `state/emails/YYYY-MM-DD.html.gz`(90KB HTML gzip ≈ 15-25KB,年 ≈ 6-9MB)。要拍板:repo 年增 <10MB 可接受嗎?保留幾年?(替代:只存純文字摘要,年 <2MB。)
- 連帶小件:0050/00662 預測寫入 history(schema 加欄位,向後相容)。

### B2|history.json 日期索引/JSONL 化(RAG 檢索效率)
- 450 天單檔線性掃描;若 B1 通過且未來要做「查過去 N 天」,建議改日檔 JSONL 或加索引。**與 B1 一起設計,不要分兩次動 schema**。

### B3|月營收/TDCC stale-cache 降級(A5 的大版本)
- 快取上次成功資料,失敗日沿用並標新鮮度、smart_money 顯示層標「籌碼資料為 N 天前」。因涉及「舊資料進計分輸入」的語義,需使用者同意(比 USDTWD 敏感——這兩者本來就低頻更新,誤用舊值的窗口大)。

### B4|新聞備援來源(N1:Google News 集中風險)
- Google News 佔來源結構重心(5 主題+每公司+類股查詢),演算法/限流變更=大面積失效。V2-N1 的 per-host 健康追蹤已能「看見」失效;缺的是備援。
- 拍板點:只加 2-3 個備援(候選:Investing.com RSS、鉅亨已有、UDN/工商已有;⚠️候選清單需執行時實測可用性,勿信本清單)。不要擴到 50 個(見 §D)。

### B5|classify 時機(N5):8-K 類深埋催化被判 normal
- classify 只看 title+summary 前 600 字(⚠️),fulltext 之後才抓 → 個股升級邏輯漏深埋催化。改「先抓 critical 候選的 fulltext 再 classify」會改變 critical 集合與 LLM 取材 → 行為變化,需同意+觀察一週。

### B6|workflows pip cache(R6,低):`actions/setup-python` 加 `cache: pip`,每日省 ~1-3 分鐘。安全但動 CI,建議拍板後做。

## §C|需回測+使用者同意(計分輸入相關——§0.2 紀律)
1. **N2 正負詞表**:「成長/訂單/增加」無上下文護欄 → 誤判事件方向。詞表餵 `_news_event_direction` → 事件方向 → 催化分 → 排名。改法(共現護欄如「訂單+取消=負」)合理,但**必須**先在 backtest_data 加「詞表 A/B 對事件方向翻轉率+對 Top5 的影響」證據再提案。
2. **N4 供應鏈映射補 HBM/記憶體鏈**(TW_SUPPLY_CHAIN_BY_US_LABEL 靜態表):加映射=加催化傳導=動排名輸入。同上,先回測。
3. **P5 校準調參包**:EWM 假日跨度、conformal LR 排程、小樣本 MAE 加權懲罰——三路 agent 與主審都同意「想法合理、證據不足」。D1 時點(≈2026-09)一併評估。

### §C 執行工單(時間鎖:D1 就緒才做;2026-07-06 現況=無法執行,只能等資料)
- **為何現在不能做**:三項都改「計分輸入」,依 §0.2 須先有 IC 證據(|t|>2、方向正確、bt_top5 複驗)。
  而 news_catalyst_score / rel_strength_5d 等因子的 20 日 IC 要等樣本累積(自 2026-06 起,約 2026-09 才夠 30 日)。
- **前置已完成**:**A1(2026-07-06)已把這些因子補進 bt_factor_ic FACTORS**——否則 D1 到期也驗不出它們。✅
- **自動就緒提醒鏈**(已就位,勿重做):`monthly-ic-report.yml`(每月 1 日 02:00 UTC 跑)→ `monthly_report.d1_readiness`
  掃 bt_factor_ic「前瞻 20 交易日」段,任一基本面因子 n_days≥30 → 月報頂部標「✅ 可啟動 D1 驗收」。
- **到期執行步驟**(月報標就緒後):
  1. 看 `backtest_data/reports/YYYY-MM.md` 的 20 日 IC:挑 |t|>2 且方向符合預期的因子。
  2. 對候選改動(N2 詞表護欄 / N4 供應鏈映射 / P5 校準)各寫一支 backtest_data/ 腳本,量「改動前後對 Top5 分位超額報酬 / 事件方向翻轉率」的差。
  3. 只有「IC 顯著 + bt_top5 複驗改善 + 不傷其他因子」三者兼備,才**帶證據找使用者拍板**;同意後才動 morning_report 的計分。
  4. 一次只驗一項,別綁包裹改。**未拿到使用者『同意改計分』前,一行計分碼都不准動。**

## §D|不建議做(誘人陷阱——未來 session 不要重提)
1. RSS 來源擴到 50+(timeout×重試拖垮 Actions;Google News 聚合已覆蓋大部分)。
2. 多步驟 LLM 事件提煉(token 成本 ×3,`_event_lifecycle` 規則已覆蓋大部分;LLM 邊際增益低)。
3. 事後股價回饋調新聞權重(後驗偏差污染前瞻性;event_study 已做貝葉斯收縮)。
4. walk-forward origins 16→32(混入舊版模型樣本,污染校準)。
5. calibration min_samples 5→15(犧牲 regime 變化反應速度;該修的是加權不是門檻)。
6. IC 排名自動調權(樣本不足+過擬合;維持人工拍板)。
7. 移除 plain text 只寄 HTML(multipart/alternative 是反垃圾信最佳實務)。
8. dark mode CSS(Gmail app 不支援 prefers-color-scheme,白工)。
9. LLM 全文逐日存 state 做向量 RAG(450 天 ≈ GB 級+embedding 成本;選擇性存檔即可,見 B1)。
10. 抽共用 fetch_utils.py(R5):`fetch_*` 大量在 monkeypatch 不可搬清單(`tools/refactor_audit.py nomove`),搬=違反鐵律;radar 直接用 `mr.*` 的現狀是刻意設計(OPTIMIZATION_PLAN §1.3)。

## §E|「SEO 清單」的誠實對應(使用者原始要求 → email 系統的實情)
| 原要求 | 對應結論 |
|---|---|
| SEO / AI search optimization / internal linking / schema.org(網頁) | **不適用**:私人信件無爬蟲無搜尋。Gmail 的 schema.org email markup 需向 Google 註冊、效益低 → 不建議。信內錨點導航在 Gmail iOS 支援差 → 不做目錄跳轉。 |
| Core Web Vitals | email 對應物=信件大小/截斷/預覽體驗:已有 102KB 防護(95KB 閾值);本次補 A4 preheader、A7 截斷測試。閾值 95→92KB 的說法**證據不足**(現行量的就是解碼後大小,勿因傳聞改參數;先收集 30 天實寄大小再說)。 |
| metadata | subject 現況可;preheader 缺(A4);multipart plain+html 保留。 |
| RAG-ready architecture | **真缺口**:結構化數字有存(history/model_history/事件時間線/podcast digest),但信件原文、LLM 敘述、0050 預測不存 → B1/B2。現況能做「數字回顧」,做不到「重建當日決策脈絡」。 |
| 未來可維護性/技術債 | 主戰場在 `A5_MODULARIZATION_MAP.md`(B1-B6 模組化,預期 14,846→~12,700 行)+ 本檔 A7/render 子區塊測試。 |

## §F|已駁回的發現(查證後推翻,留檔防止重提)
1. 「30h cutoff 會漏凌晨新聞」——誤讀,30h 視窗寬裕(僅 docstring 不一致,見 A6)。
2. 「dedup 短標題易誤合併」——相反,<8 字只做完全比對,是防誤殺的保守設計(L5289)。
3. 「freshness 24-48h 權重 0.45 過緩」——這是計分輸入,且無回測證據,不動。
4. 「feature drift 0.25 閾值需環境感知」——本機 yfinance geo-block 在 Actions 不存在,觸發場景基本不發生。
5. 「週末 source health streak 誤判」——⚠️未證實(streak 是照「有記錄的日」連續計還是照日曆計,需讀 `_persist` 確認);證實前不動。
6. **A2「build_data_quality 只查檔數不查非零覆蓋率」——錯**:13929-13949 早已算 `inst_ratio<0.3 → error`,並有 `test_build_data_quality_detects_zero_filled_institutional`。可見化已在正確位置(後台→LLM prompt)。
7. **A3「模型監控完全沒進信件」——半錯**:`_render_model_evidence_html` 已有白話漂移判決(黃燈「尚未穩定贏過基準」)。加原始命中率數字會抵觸使用者「詳細表格隱藏」決定。ADMIN_EMAIL 警示屬新功能 → §B。
8. **A7「截斷邏輯零單測」——錯**:已有 9+ 個 `test_render_html_size_guard_*` 端到端覆蓋。

## §G|Codex 第二意見(已排程明早自動補跑)
2026-07-06 執行時 Codex 額度用罄(2026-07-07 09:52 後重置)。**已建立排程任務**
`codex-gate-v3-batch1-push`(fireAt 2026-07-07 10:17 台北,在使用者本機 Claude Code 內跑):
會對待推 diff 跑 Codex code-review(APPROVE 才 push 那 4 個 commit),並對本檔跑 §G 架構第二意見
(`bash tools/codex_gate_push.sh` 產 codex_second_opinion.txt),再由該 session 把結論整合回這裡。
手動備援:額度恢復後任何時候可自己跑 `bash tools/codex_gate_push.sh`(冪等)。
**若 Codex 與本檔衝突:小事聽證據,大事問使用者。**

### Codex 第二意見(2026-07-07 實跑,已整合)
> 交叉驗證:Codex 獨立點名的兩點正好與它在 code review 抓到的 blocking 一致,且我已修:
> **A5**「stale 不可進任何計分、須強標 freshness」→ 已修(usdtwd_today 保持真值、預測零變動;資料品質標昨值天數)。
> **B1**「去識別存 repo 風險被低估、marker 精準移除脆弱」→ 已加防禦縱深(移除 PF 列+持倉名遮蔽+測試);
> Codex 進一步建議「紅線測試 + secret scan + fail-closed」——secret-scan 為後續加固候選。

**Codex 認為本檔要修正/風險偏高的結論**:
1. B1 存檔隱私風險被低估(已加固,見上)。
2. **B2 JSONL 化可能過早**:450 天資料量不大,先加 run manifest/index 比改 schema 划算 → **採納,B2 降級**。
3. **D9(不存 LLM 全文向量)方向對,但 RAG 仍應存「來源 URL/標題/時間/摘要/引用片段/prompt+model+version」**,而非只存信件 HTML → 採納,見下「evidence pack」。
4. A5 匯率降級須強標 freshness、不進計分(已符合)。
5. B4 備援來源「只加 2-3 個」偏少:重點不是數量,而是**來源健康分層 + 失敗切換 + 每來源品質評分**。

**Codex 補充的前瞻方向(納入未來候選,非本批)**:
- **新聞精確度**:canonical URL、發布/更新時間分離、同新聞多來源合併、公司實體消歧(entity linking)、付費牆/轉載標記。
- **資料品質「run manifest」**:每個特徵存 freshness/coverage/source/fallback_reason 成每日一份 manifest(比改 history schema 輕、比 B2 划算)。
- **Email 投遞監控**:SPF/DKIM/DMARC、bounce/退信監控、Gmail clipping 實測大小。
- **RAG「evidence pack」**:保存「當日輸入證據包」(結構化來源+prompt+model 版本)而非只存輸出 HTML;render 不做網路是正確硬線。
- **維護性**:先切 read-only adapter + 純函式 render 測試,再拆大檔,避免破 monkeypatch 契約(與 A5_MODULARIZATION_MAP 一致)。

**Codex 的 Top 5 優先序**:1) 信件/證據存檔的隱私安全設計 2) 每日 run manifest 3) 新聞來源 canonicalization + entity linking 4) Email clipping/投遞監控 5) 小步模組化(fetch/quality/render 邊界)。
→ 這些多為「拍板會」或未來批次題材,無一屬「現在就該無腦做」;與本檔 §B/§C/§D 及 A5_MODULARIZATION_MAP 相容。

## §H|建議路線圖(給使用者過目)
1. **V3 批次 1** = §A 全部(1-2 個工作天級,全程可本機驗證)。
2. **拍板會** = §B1/B2(信件存檔+RAG)、B3-B6 逐項 yes/no。
3. **A5 模組化** = 照 `A5_MODULARIZATION_MAP.md` B1→B5 逐輪(與 V3 批次可交錯,各自獨立 commit)。
4. **9 月 D1** = §C 全桶連同因子 IC 驗收一起處理(A1 先做,不然 D1 到期缺資料)。
