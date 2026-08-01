# Luna 5.6 xhigh vs DeepSeek V4 Pro max — 十配對實驗

分支 `luna56-xhigh-specialization`,base `cd41fee`。
**尚未合併,尚未啟動。** 本文件說明架構、啟動步驟、回切方式與已知限制。

---

## 一、成本前提已被推翻(先讀這段)

規劃時的假設是「Luna $1.00/$6.00 per MTok,比 DeepSeek 貴很多,必須明顯
超越才划算」。**官方頁面查證後,那個假設不成立:**

| 模型 | 輸入 | 快取輸入 | 輸出 | 出處 |
|---|---|---|---|---|
| `gpt-5.6-luna` | $0.20 | $0.02 | $1.20 | developers.openai.com/api/docs/pricing |
| `deepseek-v4-pro` | $0.435 | $0.003625 | $0.87 | api-docs.deepseek.com/quick_start/pricing |

以 2026-08-01 的實際用量估算單班成本:

| | 單班 | 十班 |
|---|---|---|
| DeepSeek v4-pro max | **$0.042** | ~$0.42 |
| Luna xhigh | **$0.032** | ~$0.32 |

**Luna 反而較便宜** —— 輸入單價是 DeepSeek 的 46%,而穩定 developer 前綴
還能吃 $0.02 的快取價。

**含意:** 判準要從「Luna 好到值得多花錢嗎」改成「Luna 至少不比 DeepSeek 差嗎」。
反過來說,DeepSeek 也不再有「便宜很多」的護城河。

---

## 二、架構

```
                     ┌──────────────────────┐
  quotes/news/… ───► │  evidence_packet     │  ← 唯一的證據來源
                     │  build() → sha       │
                     └──────────┬───────────┘
                                │ 同一份 packet、同一個 evidence_sha
                ┌───────────────┴───────────────┐
                ▼                               ▼
   prompt_profiles.build_luna_bundle   prompt_profiles.build_deepseek_legacy_bundle
   ├ 穩定 developer 前綴(可快取)       └ 既有 _build_prompt 的輸出,一字未改
   ├ 當日 evidence payload (JSON)
   └ analysis_schema strict JSON
                │                               │
                ▼                               ▼
      openai_responses (adapter)        既有 _call_deepseek
      ├ build_payload                   (chat/completions)
      ├ normalize_usage ← **關鍵**
      └ extract_output(phase 感知)
                │                               │
                └───────────────┬───────────────┘
                                ▼
                    llm_experiment(同群/配對)
                    analysis_metrics(指標,兩類分開)
```

**公平性** = 兩邊同一個 `evidence_sha`。
**特化** = 兩邊不同的 `prompt_sha`。
兩者都進實驗帳本,事後分得出「模型差異」與「問法差異」。

---

## 三、啟動十配對實驗

> ⚠ **合併之前不要設任何 variable。** 分支未合併時把 `LLM_PROVIDER` 改成
> `openai`,生產跑的會是**舊的 chat/completions 路徑 + DeepSeek 的 prompt
> 餵給 Luna** —— 那正是這次要避免的事。

合併之後,在 repo Settings → Secrets and variables → Actions → **Variables**
(不是 Secrets)設:

```
LLM_PROVIDER=openai
OPENAI_MODEL=gpt-5.6-luna
OPENAI_REASONING_EFFORT=xhigh
EXTRACTOR_PROVIDER=openai
OPENAI_EXTRACTOR_MODEL=gpt-5.6-luna
OPENAI_EXTRACTOR_REASONING=xhigh
LLM_PRIMARY_PROMPT_PROFILE=luna56_xhigh_v1
LLM_SHADOW_PROVIDER=deepseek
LLM_SHADOW_MODEL=deepseek-v4-pro
LLM_SHADOW_REASONING_EFFORT=max
LLM_SHADOW_PROMPT_PROFILE=deepseek_legacy_v1
LLM_COMPARISON_MODE=end_to_end_profiles
LLM_EXPERIMENT_ID=luna56-xhigh-vs-dsv4pro-v1
LLM_EXPERIMENT_TARGET_PAIRS=10
OPENAI_API_MODE=responses
OPENAI_STORE=0
OPENAI_TEXT_VERBOSITY=high
OPENAI_REASONING_SUMMARY=auto
OPENAI_REASONING_CONTEXT=current_turn
```

`OPENAI_API_KEY` 放 **Secrets**。

設完後**先手動跑 Validate LLM Config**:`OPENAI_API_MODE=responses` 時,
金絲雀會真的送一次 Responses + strict schema + 生產的 developer 前綴,
並檢查要求的 `xhigh` 有沒有被靜默退讓。那一步綠了再等排程。

### 「十天」的定義

**10 個成功且可比較的配對**,不是 10 個日曆日。以下情形當天不進分母
(但紀錄保留,它們餵可靠度指標):

| 排除原因 | 意義 |
|---|---|
| `other_cohort` | 那天在跑另一組設定 |
| `missing_evidence_sha` | 沒有證據指紋,無從證明可比 |
| `evidence_mismatch` | 兩邊看到的證據不同 |
| `primary_failed` / `shadow_failed` | 任一邊失敗 |

**實驗進行中不得改 prompt / schema / profile 版本。** 真的要改就換一個新的
`LLM_EXPERIMENT_ID` 重新起算 —— 混在一起的平均沒有意義。

---

## 四、回切 DeepSeek

**不需要 revert 任何 commit,不需要改程式碼。** 改兩個 variable:

```
LLM_PROVIDER=deepseek
LLM_PRIMARY_PROMPT_PROFILE=      (清空)
```

`LLM_PRIMARY_PROMPT_PROFILE` 空 = 依 provider 自動選,deepseek → `deepseek_legacy_v1`。
影子要一併關掉就清空 `LLM_SHADOW_PROVIDER`。

這條由 `tests/test_luna_experiment_wiring.py` 盯著:
四個關鍵變數的**預設值就是現況**,而且都是 repo variable。

---

## 五、判讀時要注意的一個不對稱

**Luna 產出結構化 JSON,DeepSeek legacy 產出 Markdown。**
「schema 合規率」「claim 帶證據的比例」「矛盾數」在 DeepSeek 側**算不出來**。

`analysis_metrics` 因此把指標分成兩類,而且**刻意不提供合成單一分數的函式**
(由測試在介面層擋住 `overall_score` / `winner` 這類名字):

| 類別 | 內容 | 能不能直接對比 |
|---|---|---|
| `text_metrics` | 數字一致性、證據涵蓋、來源多樣性、立場、成本、延遲 | **可以** |
| `structured_metrics` | schema 驗證、完整度、證據支持率、無支持的重大主張、可證偽率、矛盾、重複、資料缺口誠實度 | **不可以**(只有 Luna 有) |

把兩類混成一個分數,比的是「有結構 vs 沒結構」,不是模型能力。

另外 `numeric_consistency` **有已知誤判**:模型合法地會算衍生數字(百分比、
差值),那些不在證據裡卻不是錯的。所以它回報比率與未命中清單供人判讀,
不回報「錯誤數」。

---

## 六、尚未完成 / 已知限制

1. **執行期尚未接線。** Phase 1–7 建立了契約、adapter、指標與設定入口,
   但 `_phase_llm_analysis` 目前仍走既有路徑 —— 設了上面的變數,
   `OPENAI_API_MODE=responses` 還不會實際改變主分析走哪個端點。
   這是啟動實驗前**必須先完成**的一步。
2. **`reasoning.summary` 需要組織驗證**(官方文件),我無法本機驗證這個
   帳號有沒有開通。adapter 已做成「被拒絕就移除該欄位重試」,不影響寄信。
3. **`SCHEDULED_MAX_EFFORT["openai"]["extractor"]` 是 `low`**,而實驗要
   `xhigh`。那個常數的語義是「生產已驗證的上限」,所以守衛會報一條設定問題
   —— **那句話是真的**,不該為了消音而調高。實驗記錄應該把「抽取器 xhigh
   未經生產驗證」明確寫下來。
   (2026-07-31 的教訓:抽取器推理過頭導致 1560 則事件 0 產出。)
4. **Luna 的 `max` 不可用**(2026-08-01 金絲雀實測,chat/completions 端點
   明確拒絕)。實驗用 `xhigh`,那是它的最高檔。
5. **十配對的門檻不是自動切換的依據。** 達標只代表「可以做判讀」,
   判讀仍需人工盲評與逐日 stance flip 裁決。

---

## 七、本分支的檔案

| 檔 | 責任 |
|---|---|
| `evidence_packet.py` | provider 中立的證據包 + `evidence_sha` |
| `prompt_profiles.py` | 兩個 profile 的 PromptBundle |
| `analysis_schema.py` | Luna 的 strict 輸出契約 + 內容驗證 |
| `openai_responses.py` | Responses API 純適配層(組請求/解回應/正規化 usage) |
| `llm_experiment.py` | 同群身分與「可比較配對」語意 |
| `analysis_metrics.py` | 確定性品質指標(兩類分開) |
| `tests/test_deepseek_legacy_golden.py` | DeepSeek 現況的逐位元組凍結 |
