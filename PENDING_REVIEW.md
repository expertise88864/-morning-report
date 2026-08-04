# ⚠ 已上線但**尚未通過外審**的變更

本 repo 的規約是「push 前先過 Codex(GPT-5.6-sol)read-only 外審,APPROVE 才上線」。
下面這些 commit 是**例外**:使用者在 2026-08-03 明確決定先推上去、標記未審,
等額度恢復後一次補審。

## 待補審的範圍

| 項目 | 值 |
| --- | --- |
| 最後一個**已 APPROVE** 的 commit | `7eb60b3`(批#77) |
| 待審的第一個 commit | `6059d59`(批#78) |
| 阻塞原因 | Codex 額度用罄,重置 **2026-08-08 11:39** |
| 使用者決定 | 2026-08-03「都先 push 上去但是標記未審,等額度回復再一次審」 |

## 額度恢復後要跑的指令

```
bash tools/codex_review.sh targeted 7eb60b3 .codex-review/context.md
```

**base 是 `7eb60b3` 而不是 `origin/main`** —— 用 `origin/main` 的話 diff 是空的,
外審會對著沒有變更的樹說 APPROVE,而那是一個看起來通過、實際什麼都沒審的結果。
這正是本 repo 最常見的失效形狀(守衛在空集合上真空通過)。

`.codex-review/` 沒有被 git 追蹤(在 `.git/info/exclude` 裡),所以 context 檔
要重新寫。內容至少要涵蓋下表的每一批。

## 待審清單

### 批#78 `6059d59` —— P1-4 分側成本與延遲進 durable ledger
- 新模組 `side_telemetry.py`:`from_manifest()` 逐側擷取、`side_costs()` 跨帳本彙總
- `build_record(telemetry=)` → `primary/shadow/extractor_telemetry` 三個欄位
  (進 `PROVENANCE_FIELDS`、**不進 `COHORT_FIELDS`**)
- `record_day` 報表加 `side_costs`(傳全部嘗試,不是代表樣本)
- `_material_live` → `blind_review.material_live`(騰位,不調上限)

**外審應特別看的地方**(我自己知道風險在哪):
1. 抽取器標 `attribution="shared"` 不分攤 —— 這個決定對不對?
2. 「確定沒有失敗嘗試 → `0.0`」與「沒量到 → `None`」的界線畫在哪裡才對
3. `_experiment_row` 新增的 `_st.from_manifest()` 會不會弄壞晨報
   (我查到保護在 `experiment_record.record_failure` 的 try/except,
    但那是**間接**的,值得第二雙眼睛)
4. `days_measured` 與 `rows_seen` 差很多時沒有告警,只是兩個數字都報

**在缺外審的情況下我做過的替代驗證**:
`preflight.sh` exit 0、1686 passed、真實 manifest 形狀跑過、四項突變驗證
(其中「拿掉生產呼叫端的 `telemetry=`」一開始沒紅,補測試後才紅)。

### 批#79 `(下一個 commit)` —— Luna 特化路徑的 TypeError 根因
`evidence_packet.canonical_json` 用 `sort_keys=True`,而 **`sort_keys` 在鍵混
型別時會拋 `TypeError: '<' not supported between instances of 'int' and 'str'`**。
`default=str` 保護的是**值**,沒有人保護鍵 —— 而那個函式的 docstring 自己寫著
「寧可得到穩定字串,也不要讓整個 packet 拋例外」。宣稱與實作差一層,
而差的那一層正好是宣稱要解決的問題。

`build()` 只對 news 算 `core_sha` 所以沒事;`build_luna_bundle()` 對整個
packet 算 `evidence_sha` 才炸 —— Luna 特化路徑因此連兩天(08-03、08-04)
落回 legacy,實驗 0/10。

修法:先用型別感知的順序(`(type(k).__name__, str(k))`)重建整棵樹,
再以 `sort_keys=False` 輸出。**全字串鍵時輸出逐位元組相同**(有測試釘住),
混型別時不再拋。另加 `nonstring_key_paths()` 並寫進 manifest,
下次才知道是哪個上游欄位塞了非字串鍵。

**外審應特別看的地方**:
1. 「逐位元組相同」那條測試是否真的涵蓋所有既有形狀(它決定既有 sha 會不會變)
2. `{1: 'a', '1': 'b'}` 撞鍵時資料會少一筆 —— 那是 `json.dumps` 的既有行為,
   我只釘住它沒有改;這樣處理對不對?
3. 診斷欄位寫進 manifest 會不會洩漏內容(目前只寫鍵與型別,不寫值)


### 批#80 `(下一個 commit)` —— 「還是在堆疊數據」的兩側補法
使用者 2026-08-04 第二次反映「很多地方還都只是在呈現數字、堆疊數據,
沒有詳細分析影響」。查下去是**兩個不同的原因**:

**(a) LLM 側**:prompt 有一條「不要重述 EVIDENCE 裡 Python 已經算好的數字」,
用意是避免重複列表,**結果是那些區塊沒有任何人負責解讀**。
改成「不要逐項重列,但要合起來讀」,並新增 R17 指定在「我的明確立場」段的
理由裡用 2–3 句回答:錢往哪裡去、跟今天的立場一致還是矛盾、什麼會讓它反轉。
七之二 60→90 字,要求寫得出傳導路徑而不是四個字的抽象標籤
(「貿易規則再起法律戰」那種)。兩份 profile 版本 4→5。

**(b) Python 側**:Top5 卡片每檔排出約 15 個數字而一句話都沒有,
prompt 改再多也碰不到。新模組 `top5_readout.py` 把已算好的欄位翻成一句話,
**衝突優先講**(外資買但大戶減、漲卻量縮、法人內部不同調)。

**外審應特別看的地方**:
1. `top5_readout` 的門檻(PER<12 偏低 / >25 偏高、殖利率≥5、量比 0.8/1.5、
   大戶 ±0.10)是**我訂的**,對不對?會不會在某些產業說反話?
2. 「只描述不建議」目前靠一份禁用詞清單掃描 —— 那個清單夠不夠?
   有沒有句子在沒有禁用詞的情況下仍然讀起來像建議?
3. R17 要求「矛盾時要明講」,但沒有任何東西驗證模型真的照做
   (grounding 檢查管的是證據引用,不管這個)。這個缺口要不要補?
4. 七之二 放寬到 90 字會不會讓那段從「速覽」變成第二個八段?


### 批#81 `(下一個 commit)` —— 方向形容詞不是分析(LLM 段落的真正問題)
使用者澄清:**前一批我解錯了**。他說的是 **LLM 自己寫的段落**在堆數據,
不是 Python 排的表。逐條量了今天信裡八段/九段的 10 條:

    用方向形容詞當結論   10/10 (100%)
    說得出量級            0/10 (0%)
    說得出時間            3/10 (30%)
    與當天其他條目有關聯   0/10 (0%)
    同一個「對X…對Y…」骨架 9/10

根因是 prompt **自己在示範那個毛病**:
  * 深度鐵則寫「方向＋幅度＋信心」,而**格式模板那一行根本沒有「幅度」**;
  * 兩個範例都以方向詞收尾,範例 B 更是逐字寫著
    「對 2330 偏正、但因無確定訂單仍屬方向性」——**那就是使用者在抱怨的句子**。
規則要求的東西模板沒有示範,**模板贏**(這是 repo 記憶裡已有的一條教訓,
而我昨天才寫下它,今天又踩到)。

修法:模板加「量級與時間」一格;禁止用方向形容詞收尾並逐字列出那些詞;
判斷不出量級要**明講**(誠實與打發要分得開);至少兩條要跨條連結
(互相排擠 vs 互相加強);句式不得雷同。兩個範例整個重寫成新規則的示範。
Luna 側同批。profile 版本 5→6。

另加 `analysis_metrics.prose_depth()`:量方向詞比例、有量級/時間的行數、
跨條連結數、誠實承認說不出量級的次數。**觀測用,不是門檻**
(這個 repo 栽過「有已知誤判的指標被當判準」)。METRICS_SCHEMA_VERSION 1→2。

`writing_rules.py` 的行數上限 300→420,但**同時把代理換成性質本身**:
新測試用 AST 保證它一行邏輯都沒有。理由:行數上限防的是邏輯膨脹,而這個檔
的長度由「使用者要求信怎麼寫」決定 —— 三天三批回饋、每批都對。

**外審應特別看的地方**:
1. 逐字列出禁用的方向詞,會不會讓模型改用同義詞繞過(「略為正面」)?
   `DIRECTION_LABELS` 是同一份清單,同樣繞得過 —— 這個做法的天花板在哪?
2. 「至少兩條跨條連結」有沒有可能誘發**編造關聯**?
   (規則沒有要求關聯要有證據,而 grounding 檢查管不到這種句子。)
3. 把 `writing_rules.py` 的上限從 300 放寬到 420,同時新增 no-logic 守衛
   —— 這個交換合理嗎,還是我只是在替放寬找理由?
4. `prose_depth` 的關鍵詞清單是我訂的,誤判方向偏哪一邊?


### 批#82 `(下一個 commit)` —— 第十五輪 P1-2/P1-3:渲染層丟資料與段落說謊
外審三條具體 finding,**我逐一驗過都成立**:

**P1-3 段落語意映射錯(最急,明天就會顯現)**:`global_market`(美股→台股
連動)被放進「七之二、世界大事速覽」,而那一段的契約定義是**股市之外的
世界**;`taiwan_market.tsmc_view` 被放進「九、**其他**類股資訊」;整個
`top_news_analysis` 無條件進「八、科技板塊脈動」,即使是金融/航運/生技。
TypeError 昨天才修好,**Luna 明早可能第一次真的跑成**,那時信會頂著錯招牌。
改成:七之二、全球市場與美股台股連動 / 八、重點新聞分析 / 九、台股與台積電。
Luna schema 沒有「股市之外的世界」這個概念 —— **沒有就不要宣稱有**。

**P1-2 渲染層把最像分析的東西丟掉**:`priced_in`(已反映/未反映)整段
沒有被渲染;`falsification_trigger`(schema 必填,理由是「說不出什麼情況
我就錯了的判斷事後無法評分」)、`counterevidence_ids`、
`actions_to_consider` 也都沒有。模型產出了、驗證器檢查了,收件人沒看到。
**渲染層丟資料時,模型再深入也沒用。**

順帶修好一條**本身要求錯事的測試**:
`test_section_titles_match_the_constants_the_pipeline_uses` 斷言渲染層要
沿用 legacy 的段落名 —— 跟一個錯的名字一致不是優點。判準改成
「宣告的段落都要出現」+「不得再掛那幾個語意對不上的名字」。
另一條反向判準也失效了:`("renderer_version", 2)` 寫死當「改過的值」,
而今天預設剛好升到 2,那條 cohort 反向測試就靜靜通過 —— 改成 `+1` 推導。

RENDERER_VERSION 1→2。

**外審應特別看的地方**:
1. 改段落名會不會打壞信件 HTML 的樣式或 `_extract_stance`
   (我加了測試驗立場與總結仍抓得到,但視覺沒辦法自動驗)
2. 「九、台股與台積電」把 summary/taiex_view/tsmc_view 併成一段,
   由粗到細 —— 這個併法對嗎?
3. `priced_in` 只取前 4 條,超過的靜默丟掉 —— 該不該全列?

**尚未處理的第十五輪 finding**(需要使用者決定,見下)
- P1-1 schema v2(driver clusters / mechanism steps / magnitude band):
  這是把「一則新聞一段話」改成「因果圖」,**會重置十配對**,工程量大。
- P1-4 關係要有證據(relationship graph)、P2-1 EvidencePacket 關係圖、
  P2-4 獨立的 cross_market_synthesis 區塊 —— 同一批的一部分。
- P1-6「一句最多一個數字」阻礙比較分析(現值 vs 基準需要兩個數字)。
- P1-7 Top5 跨產業固定門檻(我自己也列過)。


### 批#83 `(下一個 commit)` —— 第十五輪 P1-1:Schema v2(使用者定案要做)
**prompt 叫模型深入分析,而 schema 沒有地方放深度** —— v1 的
`top_news_analysis` 只有五個淺欄位,模型最安全的填法就是「需求增加、
對 2330 偏多」。這是三次「堆疊數據」回饋在結構層的根因。

**schema v2**(`ANALYSIS_SCHEMA_VERSION` 1→2,strict 限制實測 深度6/10、
屬性105/5000、11.3K/120K 字元):
- `top_news_analysis[]` 加:`mechanism_steps[]`(from/to/channel/step_type/
  evidence_ids)、`magnitude_band`(negligible…large/**unknown 是頭等公民**)、
  `why_this_magnitude`、`horizon`、`confirmation_signal`、
  `invalidation_signal`、`relates_to[]`(指向另一則+關係型別+證據)。
- 新增頂層 `cross_market_synthesis`:互相強化/互相抵銷/主導因子/為什麼/
  即日 vs 1–5 日淨效果/資金從哪到哪/什麼會翻盤 + evidence_ids。
  **P2-4 一併解決**:橫向問題有自己的地方,不再塞 stance rationale。

**新的跨欄位不變式**(`analysis_validate.py`,從 analysis_schema 拆出):
- 沒有證據的因果步驟**不得自稱 fact**(fact→fact→fact 的鏈讀起來像事實,
  中間某步其實是猜的 —— 這正是「看起來有根據」的來源)
- `magnitude_band=unknown` 必須說缺哪些資料(誠實與打發要分得開)
- `relates_to` 要指向**今天真的分析過的另一則**,不能指向自己或幽靈
  (**P1-4 的「編造關聯」風險由此擋**)

**渲染**(`analysis_render_depth.py` 新模組;RENDERER_VERSION 2→3):
因果鏈逐步印出(推論/情境要標)、量級+為什麼、成立要看到/什麼會推翻、
與另一則的關係;橫向綜合排在逐條分析**之前**。

**Grounding v2**:cross_market_synthesis 進 RENDERED 與 EVIDENCE_BEARING
(一段沒有根據的橫向綜合最容易寫成漂亮空話)。

**Luna profile v7**:新欄位填法指引(unknown 是誠實不是失敗、編造的關聯
比沒有關聯更糟、五個市場各寫一句不是綜合)。legacy prompt **不動**
(v6 不變,DeepSeek 不吃 schema)。

**模組整理**:analysis_validate.py(引用檢查,150)、
analysis_render_depth.py(深度渲染,130)拆出;analysis_render 250→235
(拆完棘輪跟著縮)。

**突變驗證第一輪抓到兩個洞**:「fact 無證據要擋」與「因果鏈要渲染」
一開始都沒有測試(拿掉實作全套照樣綠),補了
test_a_fact_step_without_evidence_is_rejected 等 7 條後才紅。

**外審應特別看的地方**:
1. mechanism_steps 沒有規定至少幾步 —— 模型可以給一步就過。
   第十五輪建議「高重要性至少 3 步」,我沒做(空鏈也合法),對嗎?
2. relates_to 的 relationship_evidence_ids 我只驗存在性,沒驗那些證據
   真的支持「這種關係」—— 語意上驗不了,這是已知天花板。
3. cross_market_synthesis 的欄位全是自由文字 —— 會不會又變成
   「五個市場各寫一句」的新家?prompt 有講,但沒有機械判準。
4. 十配對:cohort 含 output_schema/renderer/grounding/profile 版本,
   樣本自然分群;但依規約應換新 experiment_id(帳本目前 0 列,代價最小)。


### 批#84 `(下一個 commit)` —— 橫向張力由 Python 先算 + 縱向深度加深
使用者最優先要求:分析內容的橫向與縱向深度。兩個缺口:

**橫向(第十五輪 P2-1)**:模型拿到 97K token 的資料堆,要自己找出
「半導體中位 +3.6% 而台積電 -2.3%」這種張力 —— 它就退化成逐條摘要。
新模組 `signal_tensions.py`:確定性偵測四類張力(外部定價 vs 外資期貨部位、
開盤預測 vs 市場廣度、產業中位 vs 權值領頭、利率變動 vs 科技股),
**只陳述事實不下結論**(禁用詞測試盯著),門檻沿用 repo 既有出處
(±5,000 口、60% 普漲),缺資料記進 `unavailable`(守衛不得靜默 no-op)。
進 EvidencePacket(v2→…schema 版本已進)、整棵樹過消毒器。
prompt v8 要求 cross_market_synthesis **逐條正面處理每個 tension**。
fixture 用 2026-08-04 的真實數字,四組矛盾每組都有測試。

**縱向**:schema v2 的欄位有了,但 mechanism_steps 空的也合法;而修補
額度在「合法但淺」的日子閒置。新增 `depth_advisories()`(結構性判準:
高重要性 <2 步、有量級沒理由、橫向綜合缺衝突欄/主導因子/翻盤條件、
多則新聞零關係)—— **不擋信**(淺而正確落回 legacy 只會更淺),
而是觸發一次 DEEPEN 修補(`deepen_input`,明寫「不得硬湊」),
失敗用留著的第一版。**最壞情況仍是兩次呼叫,與修補相同。**
GROUNDING_VERSION 2→3(接受政策變了)。

**外審應特別看的地方**:
1. 張力門檻(美股 0.8%、產業分歧 1.5%/-1.0%、利率 8bps)是我訂的,
   會不會太鬆/太緊?±5,000 與 60% 有 repo 出處,其餘沒有。
2. DEEPEN 那次呼叫的輸出**整份重新生成** —— 有沒有可能第二版在別的地方
   變差(修好了深度、改壞了立場)?目前只驗合法性,沒有逐欄位比對。
3. depth_advisories 對 `relates_to` 的判準(≥3 則且全零關係才提示)
   會不會反而誘發硬湊?指令有寫「不得硬湊」,但沒有機械驗證。
4. 五個突變驗證都紅過(含「淺被升級成擋信」的反向)。


### 批#85 `(下一個 commit)` —— 第十六輪:證據模型、空內容防線、加深選優
外審 8 條 P1 + 5 條 P2,我逐條驗過。**其中三條是我上一批自己寫進去的缺陷**
(實測確認,不是照單全收)。

**P1-1 typed evidence registry(最深的一條)**:`evidence_ids()` 先前只回
新聞 ID,而行情事實沒有任何合法的引用對象 —— prompt 又要求每個重大結論帶
`evidence_ids`,於是模型只剩三條路:留空(被 grounding 擋)、引一則不相關的
新聞(形式合法、語意錯誤)、或拿新聞 ID 替行情數字背書。
**測試 fixture 自己就示範了第三種**:橫向綜合談美債利率與外資期貨,
卻只引用一則「費半收漲」。改成 typed:`n1` / `market:QQQ.change_pct` /
`tension:t_us_vs_taifex`。

**P1-2/P2-2 空的橫向/縱向不得真空通過**:`validate()` 改成可以吃 packet
(舊呼叫端傳 set 仍可用)。今天有張力就必須回填 `addressed_tension_ids`
(schema v3 新欄位),有新聞就不得交空的 `top_news_analysis`,
有高重要性事件就必須指出主導因子。回填不存在的 ID 一樣擋。

**P1-3 Python 不得下結論**:`note` 先前寫「兩者不可能同時說對今天的方向」
「其中一邊撐不久」—— 那是**經驗法則不是事實**,而且未必成立(外資期貨
淨空可能是避險部位)。改成只給 `left`/`right`(數值+單位+引用)與
`relationship`(幾何性質)。**測試判準也從禁用詞改成結構性**:
禁用詞清單抓不到經驗法則,而「欄位集合 = 這九個」抓得到。

**P1-4 新鮮度**:美股休市時 QQQ 是上一交易日的延續值。沿用 11 維立場分
同一個 `US_HOLIDAY.detected` 判準,標 `usable_for_inference=False` +
`caveat`,**不丟掉**(丟掉的話「沒有張力」與「張力不可用」長得一樣),
且不列入「必須處理」。

**P1-5 三個實作缺陷(我寫的)**:
  (A) 符號錯誤 `pct >= -LEADER_DROP_PCT` = `pct >= +1.0`,註解寫抗跌、
      程式要求上漲 —— 「中位 −2.5% 而權值只跌 0.2%」完全抓不到。
      改成用**差距**判定。順帶把門檻 2.5 → 2.0(2.3pp 正是典型案例),
      並補反向測試釘住「1.5pp 不報」。
  (B) 同產業每個 leader 各發一筆 → 半導體出兩筆幾乎相同的張力,
      而 prompt 要求逐筆處理 → 在信裡重新製造資料堆疊。改成一產業一筆
      (取差距最大的那檔,確定性規則)。
  (C) 利率×科技只涵蓋兩個象限,科技下跌的兩個完全沒有。四個都補上。

**P1-7 因果鏈可以空著通過**:驗證器數 dict 個數,而三個欄位都空的步驟
也算一步 —— renderer 會把空的濾掉,於是「驗證器說有兩步、讀者看不到
任何因果鏈」。改成欄位非空 + **前後連續**(上一步的終點要是下一步的起點)。
**這條守衛第一次跑就抓到參考 fixture 自己是斷的** —— 修 fixture,不是修守衛。

**P1-8 加深可能用更差的第二版覆蓋(我寫的)**:先前只要第二版合法就採用,
而加深那次是**整份重生**。新增 `deepen_is_an_improvement()`:合法性、
深度提示要減少、**立場不得漂移**、六個可數面向(新聞數/高重要性數/
資料缺口/步驟證據/處理過的張力/反面證據)都不得退步 —— 逐項比較而非
合成總分(合成之後「深度 +3、證據 −2」會看起來像進步)。
`deepen_input()` 也改成**附上前一版**並要求保留已成立的內容。

**P2-4** `priced_in` 進 `EVIDENCE_BEARING`(它已進 RENDERED 卻不必帶證據;
「市場已完全反映降息」比新聞摘要更需要根據)。

**版本鏈**:EVIDENCE v3 / ANALYSIS_SCHEMA v3 / GROUNDING v4 / Luna profile v9。
**模組**:再拆 `analysis_depth.py`(深度判準與加深取捨 —— 與合法性的**後果
不同**:不合法會落回 legacy,而淺什麼都不擋)。四個葉模組上限據實量測後
調整並寫下理由(長大的是**契約內容**:schema 欄位、prompt 指令、
grounding 段落,不是邏輯膨脹)。

**外審應特別看的地方**:
1. 上限調高四個(evidence_packet 400→430、analysis_schema 250→270、
   analysis_grounding 105→120、prompt_profiles 250→270)——
   我認為是契約內容長大,但這是**我自己的判斷**,值得第二雙眼睛。
2. `market:` ID 收「每個有值的葉節點」,會不會讓 registry 太寬鬆
   (幾乎什麼都引得到 = 引用檢查失去意義)?
3. 加深的六個「不得退步」面向是我挑的 —— 有沒有漏掉真正重要的?
4. **尚未做**:P1-6 driver clusters(一事件多新聞合成一個分析單位、
   asset-specific 量級)與 P2-1 metrics v3。前者是再一次的分析單位重構。


### 批#86 `(下一個 commit)` —— 第十七輪:證據圖、廣度語意、逐筆張力調和
外審 10 條 P1 + 5 條 P2,逐條實測驗過。

**P1-4(最嚴重,今天就會產出錯誤語意)**:判準寫成
`same = (pred > 0) == (ratio >= 60)` —— 60% 是**強度**門檻不是方向分界,
於是 59.7%(653 檔漲 / 360 檔跌,正向)與 38%(真的偏空)拿到**同一個**
`opposite_sign`。模型收到「方向相反」並被要求正面處理,就會寫出
「市場廣度偏空」。**而且我的 regression test 把這個錯誤語意鎖住了。**
改成方向(50% 分界)與強度(60% 門檻)分離:`aligned_but_narrow` /
`opposite_direction` / `same_direction`。

**P1-1**:registry 只走一層,`market:MACRO.10Y.close`、
`market:SECTOR_HEAT.sectors.<產業>.median_pct` 這些**真正會被分析的數字**
沒有合法引用對象。改成遞迴(深度 5,排除診斷區塊),清單用識別欄位當路徑
而不是索引。新測試同時發現**張力給的路徑與 registry 的正規路徑不同名**
(同一事實兩個名字),已對齊;並加「registry 不得膨脹」的規模斷言。

**P1-3**:`addressed_tension_ids` 只證明「有點名」。改成一對一的
`tension_resolutions`(調和方式 / 哪一側可信 / 憑什麼 / 什麼情況分出勝負 /
證據),驗證器逐筆檢查,renderer 逐筆排進信裡。

**P1-6**:`supportive_for_growth` 是經濟解釋(利率升未必壓抑科技股)。
關係詞全面改成純幾何,並加測試禁止關係詞出現經濟語彙。

**P1-7**:mechanism step 加 `stage`,高重要性事件的鏈要碰到營運層再碰到
財務/估值/股價層 —— 「事件 → 市場關注提高 → 投資情緒改善」是兩步連續的
合法鏈,卻沒有走到任何可驗證的後果。

**P1-8**:選優改吃 packet(先前傳 ID 集合,packet-aware 規則在選優裡
整個不會跑);並從「只比數量」改成**身分保存**:分析過的新聞、處理過的
張力、反面證據、資料缺口的**集合**都不得縮小,立場/時間尺度/主導因子
不得改變,立場分不得大幅漂移。**只比數量的話,「換掉一則新聞」數量根本
不會變。**

**P1-9**:`structured_metrics` 先前用 ID 集合驗證,看不到 packet-aware 規則
—— 帳本可能顯示 `validation_problems=0` 而橫向沒做完。改吃 packet;
`REQUIRED_SECTIONS` 補上 `cross_market_synthesis` 與 `priced_in`
(先前橫向綜合整段消失,完整度仍是 100%);新增 `depth_metrics`
(走到財務層的比例、張力覆蓋、量級說明率、確認/失效率…)。
METRICS_SCHEMA_VERSION 3。

**P2-2**:stale/unavailable 的檢查沒寫進 `data_gaps` 就不合格。

**版本鏈**:EVIDENCE v4 / ANALYSIS_SCHEMA v4 / GROUNDING v5 /
RENDERER v4 / Luna profile v10。新模組 `evidence_serialize.py`
(序列化與指紋有自己的失效方式 —— 就是讓 Luna 連兩天跑不起來的那個)。

**外審應特別看的地方**:
1. **P2-4 沒做**:`LLM_EXPERIMENT_ID` 來自 GitHub repo variable,
   我改不了 —— 需要使用者在設定裡換成新 ID(建議
   `luna-vs-deepseek-depth-v3`)。cohort 欄位理論上會自然分群,
   但操作層很容易把新舊視為同一場實驗。
2. `_MAX_REF_DEPTH = 5` 與 `_NON_EVIDENCE_BLOCKS` 是我訂的 ——
   registry 太寬會讓引用檢查失去意義,太窄又逼模型引錯。
3. 加深的「身分保存」四個集合是我挑的,有沒有漏掉真正重要的?
4. **仍未做**:P1-2 required-news coverage(模型可以只分析一則次要新聞,
   而 materiality 是它自評的)、P1-5 全面 freshness、P1-10 driver clusters
   與 closed claim graph。前三者需要在 packet 端先算出「必分析清單」。


### 批#87 `(下一個 commit)` —— 第十八輪:引用要相關、覆蓋率不得虛胖
外審 11 條 P1,逐條實測驗過。**其中最嚴重的一條外審沒抓到,是我自己找到的。**

**主閘門在生產從來沒吃過 packet(最嚴重)**:上一批把選優
(`deepen_is_an_improvement`)與指標(`structured_metrics`)都接上了 packet,
唯獨 `_luna_analysis` 裡真正會擋下輸出的那一行仍然是
`_sch.validate(obj, ids)`。於是「有張力卻沒處理」「有新聞卻交空陣列」
「有高重要性事件卻沒指出主導因子」**在生產一次都沒跑過**,而測試裡它們
全是綠的。**守衛接錯線與守衛不存在,對收件人是同一件事。**
接上之後,`test_luna_path_routing` 立刻紅 —— 那份極簡行情讓四項橫向檢查
全都跑不成,而新規則要求揭露。那個紅是對的。

**P1-4 幽靈證據路徑(外審第一優先,成立)**:利率那一側掛
`market:MACRO.10Y.change_bps`,而 packet 的 MACRO 只有 `close`/`prev_close`。
它之所以合法,只因為 `evidence_ids()` **無條件收下張力給的 ref** ——
引用檢查在那一刻只證明「名字在集合裡」。改成 `derived:` 命名空間 + 帶來源,
並在 packet 端加通用防線(`phantom_market_refs`,不進 registry 且記進 manifest)。
同批修掉關係名:`same = (dbps < 0) == (qqq > 0)` 內建了「折現率下行有利
成長股」這條假說 —— 利率降也可能是衰退定價。四象限一律要求正面處理,
關係名只說象限。**原本的測試把那個假說鎖住了**,整條重寫。

**P1-5**:調和的證據只驗存在性 —— 拿一則不相干的新聞去調和
「QQQ vs 外資期貨」完全合法,而**測試 fixture 自己就在示範那個寫法**。
改成必須引用該張力本身或兩側各至少一個。

**P1-6**:同一筆張力重複填三次會通過(`got` 是集合),而指標數 `len(res)`
—— 實測 `required=1 / resolved=3`。改成拒絕重複,指標回真正的覆蓋率
並分開報 duplicate / grounded_both_sides。

**完整度的兩個反方向**:`data_gaps=[]` 在證據完整的日子是合法的,卻被
算成少一段(**好報告被扣分**);而 `priced_in` 內部全空時 dict 是 truthy,
被算成有內容(**空報告被放行**)。改成每段自己的語意判準。

**P1-10**:`reaches_financial` 只看 stage **集合** —— 「事件→股價上漲」接
「股價上漲→稼動率提升」被算成兩層都到了,因果倒著走。改成順序判準,
並補上比例(1/1 與 1/5 的計數相同而品質天差地遠)。

**P1-11**:選優只比新聞 ID 的集合 —— 把 n1 從 high 降成 medium,集合不變、
high 數量不變、深度提示還變少,**真正該加深的那則靠降級逃掉**。改成逐則
身分(重要性不得降級、說得出的欄位不得變說不出、量級不得退回 unknown)
+ 信心單次漂移上限 0.25。

**P1-9**:高重要性事件停在情緒仍然不擋(淺而正確落回 legacy 只會更淺),
但**信裡要說出來** —— 加深失敗照原樣寄出而收件人不知道,不是 resilience。

**探針本身是盲的(這一輪的第二個自己找到的)**:渲染探針餵 `render(obj)`
而生產是 `render(obj, packet)`;接受探針餵 ID 集合而生產傳 packet。於是
新行為在快照裡一行都跑不到,「版本升了行為沒變」誤報。兩個探針都改成
生產形狀,並讓固定輸入真的示範新行為。**同一形狀這個 repo 栽過三次。**

**版本鏈**:EVIDENCE v5 / GROUNDING v6 / RENDERER v5 / METRICS v4 /
Luna profile v11(prompt 開頭寫「帶上 `source_item_id`」而後段才說行情用
`market:*` —— 前後矛盾)。新模組 `analysis_stages.py`(階段與深度指標:
錯了會讓收件人讀到假的完整度)、`tension_refs.py`(偵測與查詢是兩種責任
—— P1-4 的缺陷正好落在接縫上)。

**外審應特別看的地方**:
1. 利率×科技四象限**一律**要求正面處理 —— 會不會過度觸發?
   (門檻是 8bps + 0.8%,實務上一週數次)
2. 「引用該張力本身」就算過關,模型很可能永遠只填那一個 ID ——
   這條規則的天花板在哪?
3. 信心漂移上限 0.25 是我訂的,無 repo 出處。
4. `incomplete_chains` 的揭露句只取前 3 則,超過的靜默省略。
5. **仍未做**(需要更大的結構改動):P1-1 完整 registry 命名空間
   (valuation/prediction/calibration/universe/portfolio/quality)、
   P1-2 evidence metadata(value/unit/as_of/session/quality)、
   P1-3 required-news coverage(新聞截斷仍依 grade+時間,不依 materiality)、
   P1-7 alignment 也要結構化、P1-8 全面 freshness 與逐 gap ID、
   driver clusters、asset-specific impact、closed claim graph。

**驗證**:preflight exit 0、1786 passed、八個突變全紅
(A 調和不必相關 / B 重複不擋 / C 完整度回 truthiness / D 逐則退步不擋 /
E 階段順序不計 / F 幽靈路徑照收 / G 傳導未完成不揭露 /
H 主閘門退回吃 ID 集合)。

## 補審完成後

把上面那一列從清單刪掉;清單空了就**刪掉整個檔案** ——
留一個空的待審清單在 repo 裡,下次有人看到會以為已經審過了。
