# 美股晨報自動化（GitHub Actions 雲端版）

每天台灣時間 **約 06:00–06:20** 自動寄送晨報到你信箱，包含：
- 昨夜美股 QQQ / TSM (台積電 ADR) / SPY 收盤
- USD/TWD 匯率、VIX / SOX / 10Y / DXY / 13W 等總經指標
- **00662 公允價估值**（QQQ beta + USD/TWD 變動 + 歷史平均偏離修正）
- **2330 三模型開盤價預測**（漲跌幅 1:1 + 60日比值回歸 + ADR 衰減模型）
- 加權指數開盤預測、TAIFEX 外資台指期未平倉、TWSE 法人籌碼
- **台股市值前 100 大**動態 universe（每日自 TWSE OpenAPI 重算市值排名）+ 三大法人 + 30 日累積籌碼，LLM 從中挑選「今日關注三檔」
- **預測自我校正**：每日把預測寫入歷史（保留 90 天），隔天用實際開盤誤差做模型加權 + bias 修正（見第四節）
- 新聞自動去重（同事件多來源重貼只留一則）
- 24 小時內國際與台灣財經新聞速報、SEC 8-K 公告
- LLM（預設 DeepSeek）撰寫的繁體中文分析與明確立場
- **資料品質區塊**：列出每個資料來源是 ok / 降級 / 失敗，避免把「抓取失敗」誤判成「市場沒訊號」

---

## 一、你需要做的事（一次性，約 30 分鐘）

### 步驟 1：申請 Gmail App Password

1. 進入 https://myaccount.google.com/security
2. 確認「兩步驟驗證」已開啟（必須）
3. 進入 https://myaccount.google.com/apppasswords
4. 「應用程式名稱」填 `Morning Report`，按建立
5. **複製 16 碼密碼**（去掉空格），等下要用

### 步驟 2：申請 LLM API Key

workflow 預設 `LLM_PROVIDER: deepseek`（中文分析品質佳、每月約 NT$1–6）。三種可選：

| Provider | 申請處 | 月成本 | 備註 |
|---|---|---|---|
| `deepseek`（**預設**） | https://platform.deepseek.com | NT$1–6 | 設 `DEEPSEEK_API_KEY` |
| `gemini`（免費備援） | https://aistudio.google.com/apikey | NT$0 | 設 `GEMINI_API_KEY`，免費層每日 1500 req |
| `anthropic`（品質最佳） | https://console.anthropic.com | NT$30–46 | 設 `ANTHROPIC_API_KEY`，並取消 requirements.txt 中 anthropic 註解 |

> 💡 建議至少同時設定 `GEMINI_API_KEY` 當免費備援——主 provider 失敗時程式不會自動跨 provider 降級，但 Gemini 內部已有多模型降級鏈。若所有 LLM 都失敗，仍會寄出含原始行情與新聞清單的基本版晨報。

### 步驟 3：建 GitHub repo 並上傳檔案

1. 註冊 / 登入 https://github.com
2. 右上 ➕ → **New repository**
3. 名稱填 `morning-report`，**選 Private**（私人，重要！），按 Create
4. 把這資料夾的所有檔案上傳：
   - 在 GitHub 新 repo 頁，按 **uploading an existing file**
   - 拖入 `morning_report.py`、`requirements.txt`、`README.md`
   - 按 **Commit changes**
5. 上傳 `.github/workflows/morning-report.yml`：
   - 在 repo 頁按 **Add file** → **Create new file**
   - 檔名輸入 `.github/workflows/morning-report.yml`（含斜線會自動建資料夾）
   - 把本機 `.github/workflows/morning-report.yml` 內容整個複製貼上
   - 按 **Commit changes**

### 步驟 4：設定 GitHub Secrets

在 repo 頁面：**Settings** → **Secrets and variables** → **Actions** → **New repository secret**

依序新增 Secret：

| Name | 必填 | 放哪 | Value |
|---|---|---|---|
| `GMAIL_USER` | ✅ | Secret | 你的 Gmail 位址 |
| `GMAIL_APP_PASSWORD` | ✅ | Secret | 步驟 1 拿到的 16 碼密碼 |
| `RECIPIENT` | 選填 | **Variable** 或 Secret | 收件人；**支援多位，以逗號分隔**（`a@x.com,b@y.com`）。不設則寄給自己 |
| `DEEPSEEK_API_KEY` | ✅（預設 provider） | Secret | 步驟 2 拿到的 DeepSeek key |
| `GEMINI_API_KEY` | 建議 | Secret | 免費備援 |
| `ANTHROPIC_API_KEY` | 選填 | Secret | 只有 `LLM_PROVIDER=anthropic` 才需要 |
| `CONTACT_EMAIL` | 選填 | Secret | SEC EDGAR API 的 User-Agent 聯絡信箱；不設則自動用 `GMAIL_USER` |

> `RECIPIENT` 可放在 **Settings → Secrets and variables → Actions → Variables**（明碼、可多位收件者）或 Secrets，workflow 兩者都讀（`vars` 優先）。

> 程式 import 時**不再**強制要求 Gmail secret 存在（方便本機 / CI 測試），但實際寄信前若缺 `GMAIL_USER` / `GMAIL_APP_PASSWORD` 會明確報錯。

### 步驟 5：手動跑一次測試

1. 進入 repo 的 **Actions** tab
2. 左側選 **Morning Report**
3. 右側按 **Run workflow** → **Run workflow**
4. 約 1-2 分鐘後完成，去信箱確認收到了

---

## 二、排程說明

預設 cron：`0 22 * * 0,1,2,3,4,5`（UTC）

| GitHub Actions 觸發 (UTC) | 台灣時間 | 報告類型 |
|---|---|---|
| 週日 22:00 | 週一 06:00 | 週末綜合報（涵蓋週五美股 + 週末動態） |
| 週一 22:00 | 週二 06:00 | 一般日報（週一美股收盤） |
| 週二 22:00 | 週三 06:00 | 一般日報 |
| 週三 22:00 | 週四 06:00 | 一般日報 |
| 週四 22:00 | 週五 06:00 | 一般日報 |
| 週五 22:00 | 週六 06:00 | 一般日報（週五美股收盤） |

**已排除週六 22:00**（因為週日早上美股沒消息）。

> ⚠️ GitHub Actions cron 偶爾會延遲 5-15 分鐘，這是平台特性，不可避免。如極在意準時，可改部署在 Render / Railway 排程。

---

## 三、本地測試

```bash
pip install -r requirements.txt

# (A) 跑單元測試（不連網、不寄信）
pytest -q

# (B) 跑完整流程預覽（會連 Yahoo / TWSE / LLM，但不寄信）
#     PowerShell：
$env:DRY_RUN="1"            # 不寄信，只輸出 HTML 預覽
$env:LLM_PROVIDER="deepseek"
$env:DEEPSEEK_API_KEY="sk-..."
python morning_report.py
# 預覽會寫到 /tmp/morning_report_preview.html
```

`DRY_RUN=1` 時不需要 Gmail secret。`pytest` 完全不連網，靠 mock 驗證計算與渲染邏輯。

---

## 四、2330 三模型開盤預測 + 校準

| 模型 | 邏輯 | 何時較準 |
|---|---|---|
| **模型 1：漲跌幅 1:1** | 昨日 2330 收盤 × (1 + TSM ADR 漲跌幅) | 一般情況、無重大新聞 |
| **模型 2：60日比值回歸** | 用 60 日 `2330 / (TSM × FX ÷ 5)` 平均比值反推今日合理價 | 看「結構性溢/折價」 |
| **模型 3：ADR 衰減** | 昨收 × (1 + TSM% × decay)，`decay` 用近 60 日實證係數（約 0.75） | ADR 漲跌不會 100% 反映到台股開盤時 |

三個模型可用就取中位數。再經 **`calibrate_predictions()` 自我校正**（見下節）。

### 預測自我校正（`calibrate_predictions`）

晨報每天會把預測寫進 `state/history.json`（保留 **90 天**），隔天自動讀回做兩件事：

1. **三模型 MAE 反比加權**：用各 model 近 20 日的平均絕對誤差，誤差越小權重越高，算出 `weighted_final`。樣本不足時退回等權中位數。
2. **bias 修正**：對 00662 合理價、2330 `weighted_final`、加權指數開盤，各自算近 20 日「(實際開盤 − 預測) / 預測」的平均偏誤，套 `修正後 = 原值 × (1 + 偏誤)`（偏誤夾在 ±2%）。

`calibration` 欄位會說明是否套用、偏誤幅度、樣本數。**需累積約 5+ 個交易日**才會開始套用，在那之前用未校正值（晨報「資料品質」區塊會標示）。

---

## 五、00662 公允價估值說明

00662 追蹤 NASDAQ-100（與 QQQ 同），但有申購贖回限制、隔日匯率波動、期貨升貼水等溢/折價變數。

估算邏輯（非單純套 QQQ%）：
1. 從 yfinance 抓 QQQ / 00662.TW / TWD=X 近 3 個月對照，估出 **實證 beta**（00662 對 QQQ 的敏感度）與 **歷史平均偏離**
2. `合理價 = 昨收 × (1 + QQQ% × beta + 匯率變動% + 平均偏離%)`
3. 樣本 < 15 筆時自動降級為簡化版（beta=1、無偏離修正），並在 `method` 欄位標示
4. 最後再經 `calibrate_predictions()` 的 bias 自我校正（見第四節）

若實際開盤偏離合理價過大，可能存在套利空間。

---

## 六、成本估算

| 項目 | 月成本 |
|---|---|
| GitHub Actions（Private repo 2000 分鐘/月免費，本任務每次 ~2 分鐘 × 22 次 = 44 分鐘） | **NT$0** |
| **DeepSeek API**（預設 provider，22 次/月） | **NT$1–6** |
| Gmail SMTP / Yahoo Finance / TWSE / TAIFEX / SEC | **NT$0** |
| **合計** | **約 NT$1–6 / 月** |

> 📌 改用 `gemini` 為 **NT$0**；改用 `anthropic` 為每月約 NT$30–46。

---

## 七、測試與 CI

- `pytest -q`：本機跑單元測試，全程不連網、不寄信（mock 掉 yfinance）。
- `.github/workflows/ci.yml`：每次 push / PR 自動跑 `py_compile` + `pytest`。
- CI 另有一個手動觸發（workflow_dispatch）的 `dry-run-preview` job，會用真實資料跑一次並把 HTML 預覽上傳成 artifact，但**不寄信**；此 job 失敗不影響 CI 綠燈。
- 排程寄信的 workflow（`morning-report.yml`）與 CI 完全獨立，CI 改動不會影響每日自動寄信。

---

## 八、故障排查

- **沒收到信** → 進 repo 的 Actions tab 看 `Morning Report` 最後一次 run 是否紅燈，點進去看 log。即使資料源失敗，程式也會降級寄出（該區塊顯示「資料缺失」），所以「完全沒收到信」通常是寄信或排程問題。
- **Yahoo Finance 失敗** → 偶爾限流；`fetch_quote` 已內建 3 次重試。QQQ/TSM 真的抓不到時，00662 估值 / 2330 預測會降級顯示「資料缺失」，其餘區塊照常。
- **TWSE / TAIFEX 失敗** → 官方端點偶爾改版或當日未更新；程式會往前找最近交易日，全失敗則該區塊標示抓取失敗，不影響寄信。
- **Gemini 429 / 503** → 已內建多模型降級鏈（flash → flash-lite → 2.0）與指數退避重試；全失敗會降級寄出基本版。
- **Gmail SMTP 失敗** → 確認 App Password 沒過期、兩步驟驗證沒關掉；缺 `GMAIL_USER` / `GMAIL_APP_PASSWORD` 會明確報錯。
- **GitHub Actions push state 失敗** → `save_history_state` 的 git push 失敗只會印警告，不影響當天寄信；workflow 需保留 `permissions: contents: write` 與 `actions/checkout` 的 `fetch-depth: 0`。
- **新聞抓不到** → RSS 來源可能變動，編輯 `morning_report.py` 中 `RSS_FEEDS` 字典。
- **資料品質區塊** → 晨報內含「資料品質」表，列出每個來源是 ok / 降級 / 失敗，可一眼判斷當天哪些資料可信。

---

## 九、自訂

- 想加 BTC / ETH / NVDA 等其他標的：在 `main()` 內 `quotes` 字典加一行 `"NVDA": fetch_quote("NVDA"),`
- 想改寄送時間：編輯 `.github/workflows/morning-report.yml` 的 `cron` 欄位（注意是 UTC 時間）
- 想改 LLM 分析語氣：編輯 `morning_report.py` 內 `_build_prompt()` 的 prompt
