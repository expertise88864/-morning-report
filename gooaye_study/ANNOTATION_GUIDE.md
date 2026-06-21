# 股癌題材事件研究 — 事件定義與標註準則 v0.1

> P-1 凍結文件。LLM 抽取 prompt 與人工雙盲稽核**都照本檔執行**。改動前先 git commit。
> 本檔回應 GPT-5.5 複審的兩大致命漏洞:**「提及 ≠ 交易訊號」** 與 **「反向因果」**。

---

## 0. 核心原則
1. **提及不等於建議**。同一段話可能是看多、看空、回顧、吐槽、解釋新聞、或純聊天。
   未分類就拿來算報酬 = 結論無效。分類是第一步,不是事後。
2. **只記錄「真的說過」的**。嚴禁腦補、外推、或用後見之明補成分股。
3. **每個事件必須附 evidence span**(逐字稿原句)+ 出現位置,供人工稽核與信度計算。
4. **t0 一律用 SoundOn RSS 發布日**(站台日期 14.5% 有誤);抽取器只餵「該集逐字稿」,
   不餵後續集,杜絕前視。

---

## 1. 事件單位 (event unit)
一集可產生多個事件。兩種層級:
- **題材事件 (theme event)**:對某產業/族群的方向性觀點(如「功率半導體缺貨看多」)。
- **個股事件 (stock event)**:對某具名公司的方向性觀點(如「世芯看多」)。

> 主分析用「**明確點名 + 有方向**的個股事件」(見 §5 加權)。題材事件用於聚合與
> 「題材→籃子」次方法。

---

## 2. 事件類型 (mention_type) — 六分類【最關鍵】
每個事件必須標一個 `mention_type`。**只有前兩類進主報酬統計**:

| type | 定義 | 進主統計? |
|---|---|---|
| `bullish_call` | 明確看多、且語境是「往前看 / 還可參與」 | ✅ |
| `bearish_call` | 明確看空、提醒風險、建議避開/減碼 | ✅(看空以避險效益計) |
| `neutral` | 有討論但無明確方向,或多空並陳 | ❌(記錄,不計報酬) |
| `retrospective` | **回顧已發生行情**(「之前漲很多」「這波吃到了」),非前瞻 | ❌(關鍵排除) |
| `non_investment` | 反諷/玩笑/政治/生活/心靈雞湯/業配,非投資語境 | ❌ |
| `macro_concept` | 宏觀或概念性,無法對應可交易標的(如「美國要降息」泛談) | ❌(僅質性) |

判斷要訣:
- 「我**現在**還是看好 X / X 還沒反映完」→ `bullish_call`。
- 「X **已經**漲一大段了 / 早該買 / 這波賺到」→ `retrospective`(就算語氣正面也不算前瞻訊號)。
- 立場含糊、「再觀察」「看看」→ `neutral`。
- 同一集對同一標的若先回顧再給前瞻看法 → 以**前瞻那句**為準標 `bullish_call`,evidence 同時存兩句。

---

## 3. 立場與強度 (stance / conviction)
- `stance`:`bullish` / `bearish` / `neutral`。
- `conviction`:`high` / `medium` / `low`。依語氣強度與篇幅(「我重壓」「核心持股」=high;
  「可以注意一下」=low)。`conviction` 進 §5 evidence 加權,不改 mention_type。

---

## 4. 反向因果旗標 (already_ran) 【GPT-5.5 第二漏洞】
每個 `bullish_call`/`bearish_call` 額外標:
- `already_ran`:主持人話中是否顯示「該題材/個股近期已大漲(或大跌)才被討論」。
  - 線索詞:「最近很強」「漲了一段」「停利」「追高小心」「回檔可佈局」。
- 這只是**標註旗標**;真正的反向因果控制靠事件研究端計算**事件前報酬**
  `[-60,-20]`、`[-20,-1]` 與成交量/新聞熱度(見研究計畫 §3.2 修正)。
  `already_ran=true` 的事件在報告中單獨分層,避免把「追漲題材延續」誤判成預測力。

---

## 5. 個股抽取與 evidence-strength 加權
- `mentioned_tickers`:該集**明確點名**的公司/ETF。每筆:`name` / `code`(不確定留空) /
  `market`(TW/US) / `direction`(bullish/bearish/neutral) / `evidence`(原句) /
  `mention_count`(該集出現次數)。
- **股名→代號**:LLM 抽出後,用 TWSE/TPEx 名稱表模糊比對;**對不上的丟掉並記錄**
  (寧缺勿錯,避免同音誤聽如「世芯/世禾」)。
- **evidence_strength**(0-1,主方法權重來源,取代純等權):
  `0.4×標準化(mention_count) + 0.3×conviction + 0.2×(是否在 summary/標題) + 0.1×(語境字數)`。
- **籃子規則**(主方法 = 明確點名 + 有方向):
  - 單股權重上限 25%(防單一明星股主導)。
  - 流動性過濾:點名前 20 日均量過低者剔除並記錄。
  - 同時產出 equal-weight / cap-weight / liquidity-capped 三版 robustness。
  - 題材**沒有**明確 ticker → 標 `tradeable=false`,只做質性記錄,不硬湊籃子。

---

## 6. 排除帳本 (exclusion ledger) 【GPT-5.5 第三漏洞:選擇偏誤】
全 671 集強制全量過抽取器。每集記錄:
`ep / 總提及數 / 各 mention_type 計數 / 有效交易事件數 / 排除原因分佈`。
- 目的:讓「哪些被抽成事件、哪些被降級」可稽核。
- 報告須比較「全事件」vs「僅 tradeable 事件」的績效差,揭露排除造成的偏移。

---

## 7. 事件輸出 schema(每事件一列,append-only)
```json
{
  "ep": 671, "t0_date": "2026-06-17", "date_source": "rss",
  "level": "stock",                       // stock | theme
  "name": "世芯", "code": "3661", "market": "TW",
  "theme": "ASIC/IP",                     // 正規化後的大類(見 theme_taxonomy)
  "mention_type": "bullish_call",
  "stance": "bullish", "conviction": "high",
  "already_ran": false,
  "mention_count": 3, "evidence_strength": 0.82,
  "tradeable": true,
  "evidence": "我覺得世芯這邊還沒反映完…",   // 原句,供稽核
  "extractor_model": "deepseek-v4-pro", "extracted_at": "..."
}
```

---

## 8. 人工雙盲稽核 (annotation reliability) 【GPT-5.5 補的第四偏誤:NLP 標註偏誤】
- 抽 **≥10%** 事件(pilot 階段至少 60-100 事件)做人工雙盲標註。
- 計算:
  - `mention_type` 一致率(Cohen's κ)
  - `stance` 一致率
  - ticker 對應 precision / recall
- κ < 0.6 或 ticker precision < 0.8 → 回頭修 prompt / taxonomy,不可直接全量。
- 此為**全量放行的硬門檻**。

---

## 9. 已凍結的研究參數(對齊計畫 §3.2,Codex 修正版)
- t0:RSS 發布時間轉 Asia/Taipei;**早於 09:00 開盤才用當日 open,否則一律次日 open**;
  只有日期沒時間 → 保守次日 open。
- 窗格 K = {5,10,20,60,120,250} 交易日;**主結論窗格 = 60 日**。
- 報酬:內部 log return 累加,**報表轉 simple return**(不混用)。全程含息還原價。
- 起漲/起跌:主用**波動調整版**(CAR > c × 事前波動 / rolling z-score);固定 θ=3% 僅探索性。
- 績效主指標:固定持有報酬 / CAR / BHAR / hit rate / 中位超額。**MFE/MAE 只描述路徑風險,不當主績效**。
- 基準:0050 為主,但**並列 市值分組 + 產業 + momentum + liquidity 控制**;0050 非唯一 alpha 證據。
- 看空:主報**避險效益**(改抱 0050 少賠多少);放空損益僅附欄、扣借券、過 t0 當時可放空性。

---

## 10. 結論誠實度護欄
- 第一階段只承諾:**可稽核事件資料庫 + 探索性歷史對照**。
- **不得**出現「會漲 / 建議買 / 保證打贏 0050 / 有預測力」等因果或預測話術。
- 可說:「近 N 年股癌**明確看多**題材,其後 60 日相對 0050 的**歷史**中位超額為 X%(n=…,
  含/不含下市兩版,已控 size/產業/momentum)」。
