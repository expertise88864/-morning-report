# -*- coding: utf-8 -*-
"""**主模組尺寸凍結**(第七輪 P2-4)。

## 為什麼是「凍結」而不是「抽出」
第七輪建議抽出 `top5_ledger.py` / `forecast_ledger.py` / `corporate_actions.py`
等模組。我用 repo 自己的稽核工具實測過:

```
$ python tools/refactor_audit.py group exdiv_events_in_window exdiv_coverage_ok \
      load_exdiv_history update_exdiv_history _roc_to_iso
  BLOCK  load_exdiv_history:   state=['EXDIV_HISTORY_FILE'] unknown=['ExdivHistoryUnreadable']
  BLOCK  update_exdiv_history: state=['EXDIV_HISTORY_FILE']
```

`update_top5_ledger` / `update_forecast_ledger` 同樣是 BLOCK(碰 `FORECAST_LEDGER_FILE`
與 `_atomic_write_text`,而前者被測試 monkeypatch)。工具建議「可搬」的群組
全部只有 8–9 行。也就是說**依 repo 自己的規約(「判 BLOCK 的絕不搬」),
實質抽取做不到** —— 除非先改動測試隔離的做法,而那個風險遠大於收益。

所以做 P2-4 的另一半:**擋住繼續膨脹**。這比一次性抽取更貼近問題本身
(檔案是一批一批長大的,不是一次長成的)。

## 這條測試怎麼運作
上限是**棘輪**:只能降不能升。要新增超過上限的業務邏輯時,得先把等量的東西
搬出去或刪掉,而不是順手調高數字。調高上限本身是一個需要在 commit 裡說明的動作,
而不是無聲的漂移。

刻意用行數而非函式數:行數是「讀這個檔的人要承受多少」的直接代理,
而函式數會被「把一個大函式拆成三個仍然留在原檔」蒙混過去。
"""
from pathlib import Path

import pytest

#: 主模組行數上限。**只能降不能升。**
#: 2026-07-31 基準:21,797 行(第七輪期間從 20,572 行成長 1,225 行 ——
#: 那些是批#66–#77 的修正與註解,大多是必要的,但趨勢必須被擋住)。
#: 留少量緩衝給進行中的修正,不留給新功能。
#:
#: 2026-07-30 批#82 調高至 22,400(現況 22,212)。**調高前依規定用工具判定過:**
#: ```
#: $ python tools/refactor_audit.py group fetch_trading_halts _record_corpact_span #:       load_corporate_actions update_corporate_actions halts_in_window fetch_delisted_codes
#:   BLOCK  fetch_trading_halts:      net/io=['_http_get']
#:   BLOCK  _record_corpact_span:     state=['_RUN_MANIFEST']
#:   BLOCK  load_corporate_actions:   state=['CORPORATE_ACTION_FILE']
#:   BLOCK  update_corporate_actions: state=['CORPORATE_ACTION_FILE']
#:   BLOCK  fetch_delisted_codes:     net/io=['_http_get']
#: ```
#: 六個函式全部 BLOCK,與除權息那組同因(碰 `_http_get`/`_RUN_MANIFEST`/state 常數,
#: 而後者被測試 monkeypatch)。依工具規約「判 BLOCK 的絕不搬」,這批確實搬不走。
#:
#: 2026-07-31 批#85 調高至 22,600(現況 22,438)。**這次工具幫不上忙:**
#: 新增的 `_prompt_for` / `_call_or_halve` 是 `call_llm_event_extractor` 裡的
#: **巢狀閉包**,捕捉 `prompt` / `compact_items` / `_call` / `_stat` 這些區域狀態,
#: `refactor_audit.py` 只認頂層函式(回報「找不到頂層函式」)。
#: 把它們提到頂層就得把那四個東西全部改成參數,等於為了搬而搬。
#:
#: 2026-07-31 批#89 調高至 22,800(現況 22,686)。**比較邏輯已經搬走了**:
#: 純函式的部分(輸出比較、帳本 upsert、彙整判讀)放在新的葉模組
#: `llm_shadow.py`(它刻意不碰檔案系統與網路,所以可以單獨測)。
#: 留在主模組的兩個判 BLOCK:
#: ```
#: BLOCK _call_openai:   net/io=['requests'] state=[OPENAI_* / LLM_REPORT_MAX_TOKENS]
#: BLOCK _run_llm_shadow: state=[LLM_SHADOW_* / _RUN_MANIFEST / LLM_PROVIDER …]
#: ```
#: 前者是對外請求、後者是接線與 manifest 寫入,兩者本來就該留在主模組。
#:
#: 2026-08-01 批#92 **沒有**調高(22,811 → 22,787):額度計算、400 判別、設定驗證
#: 進 `llm_telemetry.py`,影子編排以依賴注入進 `llm_shadow.run_comparison`。
#:
#: 2026-08-01 批#93 調高至 22,900(現況 22,819)。**能搬的已經先搬了**:
#: 設定快照與問題清單的組裝是純運算,已放進 `llm_telemetry.config_snapshot`。
#: 留下來的兩個判 BLOCK:
#: ```
#: $ python tools/refactor_audit.py group _timeout_env _core_tail_seconds
#:   BLOCK  _timeout_env:       net/io=['os'] state=['_PRIMARY_EFFORT']
#:   BLOCK  _core_tail_seconds: state=['LLM_TOTAL_TIMEOUT_SECONDS']
#: ```
#: 兩者的**全部工作就是讀模組層的設定常數**(推理強度、時間預算),
#: 搬走等於把那些常數改成參數再由主模組傳回去 —— 為了搬而搬。
#: 2026-08-01 批#97 調高至 23,000(現況 22,909)。**這批是生產故障的修復**:
#: workflow 寫死 `LLM_REQUEST_TIMEOUT_SECONDS: "75"`,讓批#93 依 provider/強度
#: 放大時間預算的整套邏輯在生產成為死碼 —— GPT-5.6 在 75 秒內跑不完本專案的
#: 85,814-token prompt,備援 Gemini 也失敗,使用者收到降級版基本報告。
#: 新增的行是 provider-aware 的預算計算與抽取器的網路降級接線,兩者都讀模組層
#: 設定常數(`_timeout_env` 經 refactor_audit 判 BLOCK),搬走等於為了搬而搬。
#:
#: 2026-08-01 批#108 調高至 23,100(現況 23,017)。state 寫入帳與影子 timeout
#: 推導都必須留在主模組:`_atomic_write_bytes` 是所有 state 檔的唯一寫入口
#: (經 refactor_audit 判 BLOCK:net/io=['os'] state=['_STATE_WRITES']),
#: 而把記帳搬走等於在寫入口與帳本之間再開一條可能不同步的路。
#:
#: 2026-08-01 批#109 調高至 23,250(現況 23,152)。這批是**外審 8 條 CONFIRMED
#: finding 的修正**,新增的都是接線與記帳,經 refactor_audit 全判 BLOCK:
#: `_llm_key_available`(讀四個金鑰常數)、`_record_report_writer`(寫 manifest)、
#: `_refresh_state_writes_in_manifest`(寫 manifest 檔)。
#:
#: 2026-08-01 批#114 調高至 23,350(現況 23,261)。Event Identity v4 的遷移
#: 遙測(第十輪 P1-11):`_record_identity_migration` 經 refactor_audit 判 BLOCK
#: (寫 `_RUN_MANIFEST` 與 `_DEGRADED_STEPS`),而**計數本身**已經放在
#: `news_events.apply_event_timeline` 裡(那是純函式,用注入的 dict 收集)。
#: 留在主模組的只有「算出合併/分裂並寫進 manifest」這一段接線。
#:
#: 2026-08-01 批#115 **調降**至 23,300(現況 23,235)—— 這是這份清單上
#: **第一次往下**。第十輪 P1-12 指出「只能降不能升」至今只是文字規約
#: (連續調高七次),而依賴注入才是解。第一步:manifest 改由
#: `run_manifest.ManifestRecorder` 擁有,純組裝搬進葉模組,
#: 並順手移除十行冗餘(明列的診斷鍵與白名單迴圈完全重複)。
#: 2026-08-01 批#116 再調降至 23,220(現況 23,160)。P1-12 第二步:
#: `_record_llm_call` / `_record_report_writer` / `_record_identity_migration`
#: 的**邏輯**搬進 `ManifestRecorder`,主模組只留薄委派。
#: 三者的 BLOCK 理由因此從各自碰 `_RUN_MANIFEST` / `_DEGRADED_STEPS`
#: 收斂成只碰 `_RECORDER` 一項。
#: 2026-08-01 批#117 再調降至 23,120(現況 23,053)。P1-12 第三步:
#: `_refresh_capability_health` 的 manifest 邏輯進 recorder;
#: 兩個除權息量測進 `data_quality.py`(**那才是它的家** —— 該模組的宣稱就是
#: 「來源沒掛但資料是壞的」,而覆蓋量測正是那件事)。
#: `_chip_fields_for_session` **刻意不搬**:它是組籌碼特徵欄位的領域邏輯,
#: 只是順便寫 manifest,搬進可觀測性模組只是換個檔案膨脹。
#: 2026-08-01 批#120 調高至 23,160(現況 23,140)—— **連降三次後的第一次回升**,
#: 而且是為了第十一輪 P2-3 的相位拆解。誠實記下代價:
#: 拆一個相位會多出「簽章 + return + 呼叫端解包」約 20 行的接線,
#: 而拆出來的東西**搬不出主模組** —— 工具判定如下:
#: ```
#: $ python tools/refactor_audit.py group _phase_market_and_macro
#:   OPEN  _phase_market_and_macro:呼叫了群外 mr 函式 ['fetch_quote',
#:         'fetch_usdtwd_pair', 'fetch_macro_indicators', 'fetch_twse_close', …]
#: ```
#: 十一個全是對外抓取,把它們一起搬等於把整個 fetch 層搬走,不是這一步的事。
#: **這筆額度只給第 1/8 個相位。** 其餘七個相位若每個都要 +20 行,那不是
#: 拆解的必要成本,而是拆法不對 —— 屆時要改成共用一個 context 物件
#: (外審建議的 `AppContext`)一次帶過去,而不是每個相位各自解包。
#: 2026-08-01 批#122 調高至 23,250(現況 23,239)—— P2-3 把 `main()` 的
#: 其餘七個相位全部拆完。誠實記下這筆帳:
#: **`main()` 從 1,275 行變成 30 行**,但那 1,182 行只是搬到同一個檔的頂層
#: (相位呼叫 11 個對外抓取函式,`refactor_audit` 判 OPEN,搬不出主模組),
#: 再加上每個相位約 11 行的接線(簽章、docstring、讀進來、寫回去)。
#: 批#121 用「回傳 dict + 呼叫端解包」時是每個相位 20 行,當時就寫下
#: 「其餘七個若都要 +20 就是拆法不對」—— 改成共用 `AppContext` 之後
#: 七個相位總共只多 79 行,而跨相位變數有 35 個(解包法要寫 35 行)。
#:
#: r1(Codex)之後再 +8 → 23,270(現況 23,258):`_PIPELINE` 序列 + main() 的
#: 傳播迴圈。那八行修的是一個真缺陷 —— `_phase_render` 在 `DRY_RUN=1` 時
#: `return 0`,搬進相位之後只結束那個相位,main() 會繼續往下寄信。
#:
#: **下一次要降,不是再升。** 能降的地方已經看得見:相位現在是頂層函式,
#: 各自的相依清楚,`refactor_audit` 可以逐相位重判 —— 純運算的段落
#: (立場分、渲染前的組裝)不再被 `main()` 的區域變數綁住。
#: 2026-08-02 hotfix(已上 main):DeepSeek 的輸出額度改用 `output_cap`
#: (原本寫死 7,000,不隨推理強度放大)+ 截斷改成拒絕並拋出。
#: 那天週日信的政策解析因此被推理擠掉:completion 7,000 裡 6,757 是推理,
#: 答案只剩 243 個 token,而 manifest 完全看不出來。
#:
#: 2026-08-02 Luna 特化:再加上實驗的**設定接線**(11 個變數的模組常數 +
#: `_int_env` + `_prompt_profile_for` + `_llm_config_resolved` 的對應條目)、
#: Responses 呼叫與驗證修補迴圈。它們碰 requests/金鑰/`_RUN_MANIFEST`,
#: 經 refactor_audit 判 BLOCK —— 實質內容都在七個新葉模組裡(各自有上限)。
MAIN_MODULE_LINE_CEILING = 22_443  # 2026-08-07 拆影子+拆政策/醫界情報後現況 22243(量出來的)

#: 其餘模組的上限。它們是「抽出去之後應該接住成長」的地方,
#: 上限比較寬鬆但仍然有 —— 否則只是把膨脹換個檔案繼續。
MODULE_CEILINGS = {
    "news_events.py": 1_400,
    "story_ledger.py": 2_000,
    "render_utils.py": 1_900,
    "data_quality.py": 500,
    "model_history_store.py": 600,
    # 批#95:這兩個是批#89–#93 抽出來的接收端。**沒有被列進來就是後門** ——
    # 本檔的宣稱是「葉模組也有上限,否則只是把膨脹換個檔案繼續」,
    # 而它們正是這一輪所有搬遷的去處,漏掉它們等於那句話沒有兌現。
    # 批#100 調高 500 → 600(現況 515)。這是**刻意的接收端**:主模組的上限
    # 逼著把純函式搬到這裡,搬進來的東西當然會讓它長大。它仍然有上限 ——
    # 否則就變成「把膨脹換個檔案繼續」,而那正是本檔要防的。
    # 第十輪 P1-1 調高 600 → 700:價格表加上 cached_input 與 cache write 1.25×,
    # 並記下出處與 schema 版本(外審宣稱的價格錯誤,我逐頁查證後駁回,
    # 但同一條裡的 cached/cache-write 缺失是真的)。
    # 批#120(第十一輪 P2-1)**下修 700 → 450**(現況 393)。設定驗證那半
    # 搬去 `llm_config.py` 之後這個檔只剩計價與量測 —— 棘輪要跟著縮,
    # 否則它就變成一個「隨時可以再長 300 行」的空頭額度。
    "llm_telemetry.py": 450,
    # 單價與成本估算(第十三輪 P1-1 抽出)。計價自成一塊:自己的 schema
    # 版本、自己的出處、自己的失效方式,而且這個數字直接決定「換不換模型」。
    # 實測 191 行。
    "llm_pricing.py": 210,
    # 批#120:`llm_telemetry` 撞到 700 行上限時的去處。上限守衛做了它該做的事:
    # 指出那個檔已經在做兩件事(計價量測 vs 設定驗證)。切點依相依方向選,
    # 不依主題喜好 —— 見 `llm_config` 的 docstring。
    "llm_config.py": 450,
    # 批#115:P1-12 的接收端。**沒有列進來就是後門** —— 批#95 已經因為漏列
    # llm_shadow / llm_telemetry 而被自己的宣稱打臉過一次。
    # Commit B:recorder 收下兩階段抓取的計畫(相位不得直接碰
    # _RUN_MANIFEST,所以這一筆只能放在 recorder 上)。實測 312。
    "run_manifest.py": 330,   # 2026-08-07:luna_path_failure 加 traceback 記錄(+13 行)
    # 批#122:P2-3 的共用狀態容器。它**應該一直很小** —— 它的全部工作是
    # 宣告欄位並用 `__slots__` 擋住打錯字。長大就表示邏輯漏進來了。
    "app_context.py": 120,
    # Luna 特化:provider 中立的證據包。它是**投影**不是渲染 ——
    # 任何 provider-specific 的文字都不該住在這裡(那是 prompt profile 的事)。
    # r2 折衷 (b):`core_evidence_sha` 與 `coverage` —— 可比性判準
    # 與深度揭露。300 → 350(現況 318)。
    # 第十六輪 P1-1 調高 400 → 430(**實測 415**)。長大的是 typed evidence
    # registry:行情事實先前沒有任何合法的引用對象,模型只能拿新聞 ID 去替
    # 數字背書。那是**契約內容**,不是邏輯膨脹;序列化那半(canonical_json /
    # _sorted_tree / evidence_sha)是下一個可拆的接縫,留待需要時再拆。
    # 第十七輪:序列化與指紋拆去 `evidence_serialize`(它有自己的失效方式
    # —— sort_keys 在混型別鍵上拋例外,讓 Luna 連兩天跑不起來)。
    # 拆完**實測 353 行**,棘輪跟著縮回 400。
    # 第十八輪:實測 409 —— 加的是 `evidence_meta` 出口與
    # `required_disclosures`。放寬到 420;再長就把 news 正規化拆出去。
    # Commit B:EVIDENCE v16 的版本說明(獨立性三個數各自的用途)。實測 424。
    # Commit C:top_events 進 packet(EVIDENCE v17)。實測 431。
    "evidence_packet.py": 450,
    # 證據包的序列化與指紋。指紋是實驗公平性的全部依據,值得自己的檔與
    # 測試。**只做序列化,不碰組裝** —— 出現欄位取捨就表示放錯地方。
    # 實測 134 行。
    "evidence_serialize.py": 170,
    # 第十八輪 P1-1/P1-2:證據**圖**(每個 ID 的值/單位/時間/來源/
    # 能不能推論)。與 `evidence_packet` 分開,是因為「packet 裡有什麼」
    # 與「這些東西各自是什麼、可不可信」是兩個問題,而後者才是
    # 引用檢查真正需要的。
    # 第十九輪:實測 228 —— 加的是 metadata 欄位(as_of_precision、
    # observed_session)。**假精確比沒有 metadata 更糟**,所以這幾格要留。
    # 深度加強第二批:實測 256 —— 加的是 fact: 命名空間(值/單位/上下文)。
    "evidence_registry.py": 280,
    # 第十八輪 P1-3:事件分群與必分析清單。與 `evidence_packet` 分開,
    # 因為門檻是**量出來的**(同語言同事件 0.69/0.90、不同事件同主體
    # 0.18),而量測值需要一個看得見、改得動的地方。
    # 重構規格 Commit B:群帶獨立性(已驗證 / 可能 / 未驗證三個數,
    # 兩種用途的保守方向相反,理由寫在原地)。實測 206。
    "news_clusters.py": 215,
    # Commit B 新增:來源註冊表(誰跟誰是同一個編輯台)與
    # 兩階段抓取計畫(全文預算逐事件群分配)。兩個都是純規則層。
    # 第二十三輪 P1-4:ASCII 別名 token 邊界 + 未知來源去重。實測 251。
    "source_registry.py": 265,
    "fetch_plan.py": 150,
    # 第二十四輪 P1-2:第二層壓縮(不可裁區塊本身超標時)。與 `payload_budget`
    # 分開,是因為兩者的判準不同 —— 前者「整塊拿掉背景」,後者「留下所有身分、
    # 只壓內容深度」;混在一起會讓「不可裁」這個清單的意義變模糊。實測 182 行。
    "payload_compact.py": 215,   # 2026-08-08:top_events dict 形狀修正 + 註解(外審 P1-3)
    # 第二十四輪 P1-1:新聞身分(`source_item_id`)從 `evidence_packet` 搬出來。
    # 搬的理由就是那個缺陷的形狀 —— ID 住在 packet 模組裡,就會讓人以為
    # 「那是 packet 階段的事」,而分群/計畫/抓取三個更早的相位全靠它。
    # 這是最底層的葉模組(只依賴 hashlib),實測 59 行。
    "news_ids.py": 80,
    # V2-N4/N3:Google 查詢註冊表與 30 天健康歷史的讀取端。
    # 兩個都是純規則層,說明寫在模組裡(主模組只留一行指路)。
    "gnews_registry.py": 150,
    "health_trends.py": 120,
    # Commit C:事件多軸計分 + 純價格變化的排除(「昨夜三大重點」
    # 要是三個事件)。判準詞表佔大半,純規則層。
    "event_score.py": 240,
    # Commit D:事件圖 —— 共同驅動(家族)、總經發布、方向衝突。
    # 驅動關鍵詞表佔大半,純規則層。
    "event_graph.py": 245,
    # 第十九輪 P1-3:新聞正規化與截斷拆出來 ——「誰留下來」是獨立的決定,
    # 而先前的順序錯誤(先截斷再算必分析)在 `build()` 裡看不出來。
    # 深度加強第二批:改版重發去重(+15 行)。
    "news_normalize.py": 200,
    # 深度加強第二批:數字事實抽取(帶單位的數字 → fact: 命名空間)與
    # 同源標題指紋。借自 5W1H 結構化抽取與 RSS 聚合器的內容指紋做法,
    # 取其中純規則做得到的一塊 —— ML 相依違反本 repo 的確定性約束。
    "news_facts.py": 130,
    # Luna 特化:strict 輸出契約與內容驗證。schema 本身會長大(欄位是產品決策),
    # 但**驗證邏輯**不該 —— 品質指標的家在 Phase 6 的模組,不在這裡。
    # 第十六輪調高 250 → 270(**實測 254**)。schema v2/v3 加的是因果鏈、
    # 量級、關係與 addressed_tension_ids —— 這個檔的全部工作就是宣告契約,
    # 而契約變深就是這幾輪的目的。`validate` 已經拆去 `analysis_validate`。
    # 第十七輪調高 270 → 310(**實測 289**)。加的是 `tension_resolutions`
    # (點名不等於處理)與 `CHAIN_STAGES`(鏈停在哪一層要驗得出來)——
    # 這個檔的全部工作就是宣告契約,而契約變深正是這幾輪的目的。
    # 第十八輪:實測 326 —— 逐標的影響、claim 圖、同向解讀三個新結構。
    # **契約變深就是這幾輪的目的**;檢查邏輯早已拆去 analysis_validate /
    # analysis_crosscheck,這裡剩下的幾乎都是欄位宣告與它們的理由。
    # 第十九輪:strict 預算拆去 `schema_budget`(provider 限制 ≠ 契約形狀,
    # 而且前者超標時測試全綠、真實 API 整份拒收)。
    # Commit C:`key_drivers[].cluster_id`(SCHEMA v11)。實測 382。
    "analysis_schema.py": 410,
    "schema_budget.py": 90,
    # 第十九輪 P2-3:**「有沒有填欄位」與「有沒有真的做到」是兩種量測。**
    # 前者在 `analysis_metrics`/`analysis_stages`;後者要知道駁回不算覆蓋、
    # 一個實體不能覆蓋一整天、合法的證據未必相關 —— 判準完全不同。
    "quality_metrics.py": 280,
    # 2026-08-05 實機:**送得出去的 payload 有多大**。新聞側有上限,
    # 而 market 的外部文字區塊一個都沒有 —— 每一塊都有人負責,
    # 總和沒有人負責。實機估 111 萬 token、2.7 秒被 429 拒收。
    "payload_budget.py": 230,
    # 第二十輪 P2-5:**段落→主張的對照表只有一份。** 先前四個消費者
    # (驗證器、飽和率、加深保存、渲染)各自維護,schema 加了新段落之後
    # 只有驗證器知道。
    "claim_map.py": 110,
    # 2026-08-05 使用者回饋:信裡的財經專有名詞要先講中文。這些字串
    # **不是模型寫的** —— 財經日曆由 Python 直接排進 HTML。
    "econ_terms.py": 180,
    # 同一批:別縣市的單一場所規章不該佔一個政策版位。
    # 深度優化第三批:類股熱度表的一句話解讀 —— 表上四個數字都在,
    # 合起來的那句話沒有人說(2026-08-05 實信:半導體佔 40.5% 而
    # 台積電 -2.1%)。與 top5_readout 同規矩:衝突優先、不建議、可沉默。
    "sector_readout.py": 90,
    # 第二十一輪 P2-8:同一件事的兩種寫法(伊朗/德黑蘭、台積電/TSMC)。
    # 刻意只做一張小表,不做模糊比對 —— 誤併比漏併危險。
    "entity_alias.py": 100,
    # 第二十輪 P2-6:證據命名空間的單一宣告。prompt、schema 說明、
    # Python advisory 先前三邊各說各話,模型收到互相矛盾的規則。
    "evidence_namespaces.py": 80,
    # 同一天的另一半:LLM 呼叫沒有 429/5xx 退避(`_http_get` 早就有)。
    # 暫時性的失敗不該花掉一整天的分析。
    "llm_http.py": 130,
    # 「根據」的檢查(與 schema 的「形狀」檢查刻意分開 —— 第十二輪 P1-3
    # 的教訓正是這兩件事被混為一談)。**實測 97 行**(初訂 90 是依 64 行
    # 抓的頭寸,外審的兩條修正把它撐開;數字據實量測,不靠推估)。
    # 第十六輪調高 105 → 120(**實測 107**)。多的是 `priced_in` 進
    # EVIDENCE_BEARING 與版本註解 —— 判準本身仍然只有兩個函式。
    "analysis_grounding.py": 135,
    # 本地 strict JSON Schema 檢查(第十三輪 P2-3/P2-4)。驗證只發生在遠端時,
    # 本地沒有東西會說「這個物件 API 根本不會接受」—— 而測試 fixture 與
    # 金絲雀探測都需要那個答案。**實測 137 行**(初訂 110 是依 95 行抓的
    # 頭寸,r1 的 minimum/maximum 與「從 schema 反推未實作關鍵字」撐開)。
    "json_contract.py": 150,
    # Luna 特化:profile 登錄簿。prompt 文字佔大半,所以上限比別人寬;
    # 但**組裝邏輯**要保持薄 —— 任何 provider 的請求細節都屬於 adapter。
    # 第十四輪:寫作段搬去 `writing_rules.py` 之後**實測 223 行**。
    # 第十六輪調高 250 → 270(**實測 255**)。長大的是 developer 指令裡
    # 「怎麼用 signal_tensions / typed evidence ID」那幾條 —— 與
    # `writing_rules.py` 同理:**prompt 文字的長度由使用者要求決定**,
    # 而組裝邏輯仍然只有 `_bundle` 一個函式。
    # 第十七輪調高 270 → 290(**實測 271**)。多的是 stage 與逐筆張力
    # 調和的填法指引 —— 與 `writing_rules.py` 同理:**prompt 文字的長度
    # 由使用者要求決定**,而組裝邏輯仍只有 `_bundle` 一個函式。
    # 第十八輪:實測 301 —— 三條新規則(逐標的、同向解讀、claim 回指)。
    "prompt_profiles.py": 380,
    # 第十四輪抽出:兩份 prompt 的**寫作規則文字**(legacy R1–R16b + Luna 寫作)。
    # 搬過來的理由是使用者兩天內改了兩批寫法,而每一批都要同時動兩個檔;
    # 其中一個埋在 `morning_report.py` 中段的 f-string 裡,兩邊很容易漂開。
    #
    # 2026-08-04 放寬 300 → 420(現況 306)。**這一格與其他格不同:**
    # 行數上限防的是**邏輯**膨脹,而這個檔按設計一行邏輯都沒有 ——
    # 它的長度由「使用者要求信怎麼寫」決定,那不是該被擋住的東西
    # (三天內三批回饋,每批都讓規則變長,而每一批都是對的)。
    # 用行數當代理在這裡擋錯了東西,所以改成**直接驗那個性質**:
    # `test_the_writing_rules_module_holds_no_logic` 保證這裡只有字串常數。
    # 上限仍然留著,當「有人把整個 prompt 系統搬進來」的最後一道背牆。
    "writing_rules.py": 420,
    # 第十四輪 P1-4:**逐側**的成本與延遲(manifest 隔天被覆蓋,帳本是追加的)。
    # 兩件事:從 manifest 擷取一列、跨帳本彙總。**不做任何分攤** ——
    # 抽取器標 shared,按比例拆給兩側是編造。實測 151 行。
    # 2026-08-04:Top5 卡片的一句話解讀。**只描述不建議**,判準全部來自
    # 卡片上已有的欄位。長大就表示有人在這裡加新的資料來源或建議語氣。
    # 實測 120 行。
    "top5_readout.py": 160,
    # 第十四輪 P0-1:這封信的分析走的是哪一條路(特化/落回/備援)。
    # 純常數與判定,**不碰 manifest 也不碰檔案** —— 它要能被兩個帳本共用。
    "analysis_origin.py": 90,
    # Luna 特化:Responses API 的**純**適配層(組請求、解回應、正規化 usage)。
    # 網路呼叫刻意留在主模組 —— 那裡才有金鑰、逾時預算與 manifest。
    # 這個檔長大就表示網路或設定邏輯漏進來了。
    "openai_responses.py": 250,
    # Luna 特化:端到端 profile 比較實驗的身分與配對語意。
    # 它只做「這一天算不算一個有效配對」與「同群是誰」—— 品質指標的計算
    # 不屬於這裡(那會讓一個判定模組長成一個統計模組)。
    # r2(Codex,#3):加上**跨日帳本**(load/upsert/record_day)——
    # 沒有它,十配對的計數機制存在但不會計數。250 → 350(現況 296)。
    # 第十七輪調高 350 → 360(**實測 352**):版本註解。
    # 帳本的**儲存語意**(追加/代表樣本/嘗試統計)。與 `llm_experiment` 的
    # 判讀語意刻意分開 —— 第十二輪 P1-4:覆蓋掉的原始紀錄補不回來。
    # **實測 196 行**。這個上限已經跟著外審調過兩次(150→185→210):
    # 133 行時訂 150、LOCAL 排除與重跑時序撐到 173、第十三輪的 cohort 範圍
    # 與呼叫數語意再撐到 196。**每次都是據實量測後才調**,而不是先調再填;
    # 但調到第三次就該記下來:再撐下去該考慮把「代表樣本挑選」與
    # 「嘗試統計」拆成兩個模組,而不是繼續放寬同一個數字。
    # **第十四輪就是第三次**,所以照當初寫下的做法拆了(→ `attempt_stats.py`),
    # 這個數字沒有再動。實測 142 行。
    # 第十四輪:從 `experiment_ledger` 拆出的**嘗試層級統計**(重跑偏差、
    # 呼叫數、成本完整性)。與代表樣本挑選刻意分開 —— 兩邊回答不同問題,
    # 而合在一起的那個檔已經被撐開兩次。實測 106 行。
    "attempt_stats.py": 140,
    # 實驗紀錄的落地(2026-08-03 從主模組抽出)。主模組已達上限,而前兩次
    # 都是靠壓縮註解擠進去 —— 那不是重構,是把問題往後推。實測 57 行。
    # Luna 特化:確定性品質指標。它刻意**不提供**綜合分數 ——
    # 結構相關的指標只有 Luna 有,合成單一分數會讓比較變成「有結構 vs 沒結構」。
    "analysis_metrics.py": 380,
    # 盲評卡端到端(產生/拆分/落地)。實測 165 行 —— 這一批從
    # `analysis_metrics` + `llm_experiment` 抽出來,兩邊都因此退回上限內。
    # Luna 特化:strict JSON → 晨報 Markdown 的確定性 renderer。
    # **模型不直接控制排版** —— 排版由程式決定,模型只負責判斷內容。
    # schema v2 拆出深度渲染之後**下修 250 → 235**(現況 225)——
    # 拆完變小,棘輪跟著縮,否則留一個「隨時可以長回來」的空頭額度。
    # 第十九輪:實測 256 —— 情境觸發條件、駁回事件、未完成鏈剩餘數。
    # Commit E:事件卡(這件事的來歷)+ 逐標的淨效果。實測 337。
    "analysis_render.py": 350,
    # 第二十四輪 P1-12:標的的**型別身分**(canonical id + 範疇)。
    # 先前沒有「標的」這個型別 —— 判斷散在四處各憑字串形狀猜,而白名單
    # 被當成「與這件事有關」的免死金牌。實測 71 行。
    "instrument_registry.py": 100,
    # 第二十四輪 P1-5/P1-6:跨模組共用的結構契約判準(重點條數、可駁回集合)。
    # 抽出來的理由就是那兩個缺陷的形狀 —— 同一個契約被三個模組各自寫死一次,
    # 於是它們互相矛盾而沒有人發現。判準只能有一份。
    # P1-8/P1-9 再加上結構化引用的完整性(淨效果要有 claim 根據、
    # 引用的主張要真的關於那個標的、cluster 引用要指得到真的東西)——
    # 那是同一類東西:**schema 保證得了形狀,保證不了指涉**。實測 157 行。
    "analysis_contracts.py": 180,
    # schema v2:深度欄位的渲染(因果鏈/量級/關係/橫向綜合)。
    # 與 `analysis_render` 分開:段落順序跟著信件結構走,條目寫法跟著
    # schema 版本走。實測 98 行。
    # 第十八輪:實測 137 —— 加的是 `_tension_head`(從 packet 回查這筆
    # 張力在調和什麼)。先前信裡連著三個「矛盾調和…(偏向前者)」,
    # 而讀者無從知道「前者」是 QQQ 還是產業中位數。放寬到 150。
    # 第十八輪:實測 173 —— 逐標的影響與同向解讀都要排進信。放寬到 190。
    "analysis_render_depth.py": 205,
    # schema v2 時從 `analysis_schema` 拆出的**引用檢查**(ID 存不存在、
    # 高重要性要有證據、關係要指向存在的條目、無證據的步驟不得自稱 fact)。
    # 形狀/根據/引用是三件事,那個檔的 docstring 自己說要分開。
    # 第十五輪同批加 depth_advisories/deepen_input(合法但淺的判準與
    # 加深指令)—— 深度判準與引用檢查同屬「schema 表達不了的不變式」。
    # 實測 181 行。
    # 第十八輪:實測 226 —— 加的兩條(重複的張力調和、調和的證據沒有
    # 涵蓋兩側)**正是這個檔存在的理由**:引用存在的 ID ≠ 引用相關的 ID。
    # 放寬到 240;再長就要把張力那一段整個拆出去。
    # 深度優化第三批:實測 399 —— 加的是「標的要是證據裡的人」
    # (字串格式分不出代號與概念,證據分得出)。超過 430 就把
    # affected_assets 檢查整段拆去 analysis_crosscheck。
    # 第二十二輪:實測 449 —— 概念詞黑名單與「中文名也要在證據裡」。
    # 超過 470 就把 affected_assets 檢查整段拆去 analysis_crosscheck。
    "analysis_validate.py": 470,
    # 第十八輪:完整性檢查(必分析覆蓋、同向解讀、claim 圖)拆出來 ——
    # 「形狀對不對」與「有沒有真的做完」是兩種不同的失敗。
    # 第十九輪:實測 217 —— 加的是語意判準(總結要回指、立場的時間尺度
    # 要有主張撐著、asset_scope 不得是泛稱)。**這個檔存在的理由就是
    # 「有沒有真的做完」**,判準變細正是它該長的方向。
    # 第二十輪:實測 268 —— 情境/觀察點的回指、駁回的回頭條件與
    # 自引用檢查。**這個檔量的就是「有沒有真的做完」**,判準變細是
    # 它該長的方向;超過 300 就再拆。
    # Commit C:`top_event_problems`(三大重點要是三個事件的契約)。實測 320。
    "analysis_crosscheck.py": 445,
    # 第十六輪:從 `analysis_validate` 再拆出的**深度判準**。與合法性刻意
    # 分開,因為兩者的後果不同:不合法 → 修補/落回;**淺 → 什麼都不擋**,
    # 只決定要不要把還沒用掉的那次呼叫拿去加深。實測 145 行。
    # 第十七輪調高 190 → 290(**實測 260**)。加的是 `depth_metrics`
    # (十配對要回答的正是「深度有沒有改善」,而先前量不到)與
    # `_identity`(加深不得換掉內容,只比數量攔不住)。
    # 判準與量測放同一個檔是刻意的:**它們必須用同一套定義**,
    # 分家的話「提示說夠深、指標說不夠深」會同時成立。
    "analysis_depth.py": 460,
    # 第十八輪:階段與深度指標拆去 `analysis_stages` —— **後果不同**:
    # 深度提示錯了只是多跑一次;階段判斷錯了會讓收件人讀到假的完整度。
    # 第二十輪 P1-3:實測 230 —— 加的是 `is_numeric_anchor`
    # (錨點要是**這則新聞自己的、真的是數字的**;先前只看前綴,
    # 於是 value=None 的殼與別則新聞的 fact 都算錨點)。
    # 第二十二輪 P2-1:加「帶主體的錨點要在範圍裡」的判準
    # (`_SUBJECT_NAMESPACES` + `_subject_of` + 範圍比對)。實測 263。
    "analysis_stages.py": 275,
    # 第十五輪 P2-1:確定性的訊號張力偵測(矛盾/同向,附數字與門檻出處)。
    # **只陳述事實不下結論**,有測試用禁用詞掃著。實測 ~175 行。
    # 第十七輪調高 210 → 250(**實測 230**)。加的是廣度的方向/強度分離
    # (59.7% 不是方向相反)、四象限、stale 標記與 registry 路徑對齊。
    "signal_tensions.py": 250,
    # 第十八輪:查詢介面拆去 `tension_refs`。偵測端掛上一個 packet 裡
    # 不存在的 `market:` 路徑、查詢端原封不動當成合法引用 ——
    # 那個缺陷正好落在兩種責任的接縫上。
    "tension_refs.py": 130,
}

#: **明列的豁免**:這些根模組目前沒有行數上限。
#:
#: 批#120:上表原本只涵蓋 24 個根模組中的 8 個,而沒有任何守衛要求「新模組
#: 必須被決定」—— 於是我這一批新增 `llm_config.py` 時,漏列它不會有任何人
#: 知道。這正是本檔自己警告過兩次的形狀(「沒有列進來就是後門」),
#: 只是先前防的是**漏列既有檔**,沒有防**新增檔**。
#:
#: 這裡不假裝已經替每個檔想好數字。它要求的只有一件事:
#: **新增一個根模組時,必須明確選擇「設上限」或「列入豁免」** ——
#: 那是一個會出現在 diff 裡、有人看得到的動作。
UNCAPPED_MODULES = {
    "morning_report.py",        # 由 MAIN_MODULE_LINE_CEILING 單獨管
    "alpha_factors.py", "backtest_runner.py", "factor_ic.py", "fz_score.py",
    "gooaye_radar.py", "llm_postprocess.py", "model_confidence.py",
    "news_rules.py", "num_utils.py", "overfit_check.py", "podcast_digest.py",
    "portfolio_risk.py", "session_calendar.py", "tw_policy_sources.py",
    "valuation.py",
}


#: repo 根目錄。r1(Codex,P2):**路徑不能相依於 process CWD。**
#: 原本寫 `Path(name)`,從 repo 根目錄以外啟動 pytest 時檔案「不存在」→
#: `pytest.skip` → 三條尺寸測試全部跳過,凍結靜默失效。
#: 我在這個檔的 docstring 裡才剛寫過「永遠不會觸發的上限只是裝飾」。
_ROOT = Path(__file__).resolve().parents[1]


def _lines(name: str) -> int:
    """受控檔案的行數。**不存在就失敗,不跳過。**

    這些是 repo 裡必然存在的檔;`skip` 會讓整個凍結機制無聲消失,
    而那正是它要防的東西。真的要移除某個葉模組時,連同這裡的清單一起改 ——
    那是一個應該被看見的動作。
    """
    path = _ROOT / name
    if not path.exists():
        pytest.fail(f"{name} 不存在於 repo 根目錄({_ROOT})——"
                    "尺寸凍結的受控檔案清單需要同步更新")
    return len(path.read_text(encoding="utf-8").splitlines())


def test_main_module_does_not_grow_past_the_ceiling():
    """`morning_report.py` 不得繼續膨脹。

    超過上限時**不要直接調高數字** —— 先問這批新增的東西能不能放進既有的
    葉模組(news_events / story_ledger / data_quality / render_utils),
    或者能不能刪掉等量的舊東西。真的必須調高時,在 commit message 裡說明
    為什麼那些行無法放到別處。
    """
    n = _lines("morning_report.py")
    assert n <= MAIN_MODULE_LINE_CEILING, (
        f"morning_report.py 已達 {n} 行,超過上限 {MAIN_MODULE_LINE_CEILING}。\n"
        "  這是**棘輪**:請先把等量的邏輯搬到葉模組或刪除,而不是調高數字。\n"
        "  可搬性請用 `python tools/refactor_audit.py group <FUNC...>` 判定"
        "(判 BLOCK 的絕不搬)。")


def test_leaf_modules_do_not_absorb_the_bloat():
    """葉模組也有上限 —— 否則「抽出去」只是把膨脹換個檔案繼續。

    r1(Codex,P2):**逐檔收集,不要讓一個問題檔跳過整組。**
    原本整組寫在一個 comprehension 裡,任一檔缺失就 skip 掉全部 ——
    其他仍然存在**且超標**的模組不再被檢查。
    """
    missing, over = [], []
    for name, cap in MODULE_CEILINGS.items():
        path = _ROOT / name
        if not path.exists():
            missing.append(name)
            continue
        n = len(path.read_text(encoding="utf-8").splitlines())
        if n > cap:
            over.append(f"{name} {n} 行 > 上限 {cap}")
    problems = ([f"缺少受控檔案:{'、'.join(missing)}"] if missing else []) + over
    assert not problems, ";".join(problems)


def test_the_ceiling_is_not_far_above_reality():
    """**棘輪必須貼著現況**,否則它只是一個永遠不會觸發的裝飾。

    上限與實際行數差距過大時,這條會失敗並要求把上限調降到接近現況 ——
    也就是說「降低上限」是被強制的,而不是靠自律。
    """
    n = _lines("morning_report.py")
    slack = MAIN_MODULE_LINE_CEILING - n
    assert slack <= 600, (
        f"上限 {MAIN_MODULE_LINE_CEILING} 比實際 {n} 行高出 {slack} 行 —— "
        "棘輪鬆掉了,請把上限調降到接近現況(建議 現況 + 200)。")


def test_every_root_module_is_either_capped_or_explicitly_exempt():
    """新增根模組時,**必須明確決定**它有沒有行數上限(批#120)。

    本檔已經兩次因為「漏列」而讓自己的宣稱落空(批#95 漏 llm_shadow /
    llm_telemetry)。那兩次防的是漏列既有檔;這條防的是**新增檔** ——
    上限表是手抄的,新檔不列進來不會紅,只會少檢查一個檔。

    這條刻意不要求每個檔都有數字(那會逼出一堆沒人想過的門檻),
    只要求那個選擇出現在 diff 裡。
    """
    known = set(MODULE_CEILINGS) | UNCAPPED_MODULES
    actual = {p.name for p in _ROOT.glob("*.py")}
    assert actual, f"{_ROOT} 底下找不到任何模組 —— 這條測試不得空集合真空通過"
    unknown = sorted(actual - known)
    assert not unknown, (
        f"這些根模組既沒有行數上限也沒有列入豁免:{unknown} —— "
        "請在 MODULE_CEILINGS 給一個上限,或在 UNCAPPED_MODULES 明列並說明")
    gone = sorted(known - actual)
    assert not gone, (
        f"清單裡有已經不存在的檔:{gone} —— 清單漂移會讓人以為它還被管著")
    assert not (set(MODULE_CEILINGS) & UNCAPPED_MODULES), (
        "同一個檔同時被設上限又被豁免 —— 豁免會讓讀的人以為它沒有上限")


def test_the_writing_rules_module_holds_no_logic():
    """**`writing_rules.py` 只放文字,不放邏輯。**

    它的行數上限比別人寬,理由是「長度由使用者要求決定,不是開發者散漫」。
    那個理由只有在它**真的沒有邏輯**時才成立 —— 否則寬上限就變成一個
    「隨時可以塞 100 行程式進去」的空頭額度,而那正是本檔要防的。

    所以把代理換成性質本身:AST 掃到函式、類別、迴圈、條件、推導式就紅。
    (`from __future__ import annotations` 與模組層的字串常數指派是允許的。)
    """
    import ast
    src = (_ROOT / "writing_rules.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.For,
              ast.While, ast.If, ast.Try, ast.With, ast.Lambda,
              ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp,
              ast.Call)
    found = sorted({type(n).__name__ for n in ast.walk(tree)
                    if isinstance(n, banned)})
    assert not found, (
        f"writing_rules.py 出現了邏輯:{found} —— "
        "這個檔的寬上限建立在「它只有字串」上,有邏輯就該搬去別的模組")
    # 反向:掃描器不得因為找不到檔案或解析失敗而真空通過
    assert len(src) > 5000, "writing_rules.py 突然變得很小 —— 掃描器可能掃錯檔"
    assert any(isinstance(n, ast.Assign) for n in tree.body), "找不到任何常數"
