# 美股晨報自動化（GitHub Actions 雲端版）

每天台灣時間 **07:00** 自動寄送晨報到你信箱，包含：
- 昨夜美股 QQQ / TSM (台積電 ADR) / SPY 收盤
- USD/TWD 匯率
- **00662 公允淨值換算與今日合理價估值**
- **2330 雙模型開盤價預測**（漲跌幅 1:1 + 60日比值回歸）
- 24 小時內國際與台灣財經新聞速報
- Claude AI 撰寫的 5 分鐘版分析與見解

---

## 一、你需要做的事（一次性，約 30 分鐘）

### 步驟 1：申請 Gmail App Password

1. 進入 https://myaccount.google.com/security
2. 確認「兩步驟驗證」已開啟（必須）
3. 進入 https://myaccount.google.com/apppasswords
4. 「應用程式名稱」填 `Morning Report`，按建立
5. **複製 16 碼密碼**（去掉空格），等下要用

### 步驟 2：申請 Anthropic API Key

1. 註冊 https://console.anthropic.com/
2. 左側 **API Keys** → **Create Key**
3. 名稱填 `morning-report`，複製 key（`sk-ant-...`）
4. 在 **Plans & Billing** 儲值最少 USD $5（可用約 100 次以上）
5. 預估每日成本：USD $0.04 ≈ NT$1.3

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

依序新增 4 個 Secret：

| Name | Value |
|---|---|
| `GMAIL_USER` | `expertise88864@gmail.com` |
| `GMAIL_APP_PASSWORD` | 步驟 1 拿到的 16 碼密碼 |
| `RECIPIENT` | `expertise88864@gmail.com`（如要寄給其他人改這個） |
| `ANTHROPIC_API_KEY` | 步驟 2 拿到的 `sk-ant-...` |

### 步驟 5：手動跑一次測試

1. 進入 repo 的 **Actions** tab
2. 左側選 **Morning Report**
3. 右側按 **Run workflow** → **Run workflow**
4. 約 1-2 分鐘後完成，去信箱確認收到了

---

## 二、排程說明

預設 cron：`0 23 * * 0,1,2,3,4,5`（UTC）

| GitHub Actions 觸發 (UTC) | 台灣時間 | 報告類型 |
|---|---|---|
| 週日 23:00 | 週一 07:00 | 週末綜合報（涵蓋週五美股 + 週末動態） |
| 週一 23:00 | 週二 07:00 | 一般日報（週一美股收盤） |
| 週二 23:00 | 週三 07:00 | 一般日報 |
| 週三 23:00 | 週四 07:00 | 一般日報 |
| 週四 23:00 | 週五 07:00 | 一般日報 |
| 週五 23:00 | 週六 07:00 | 一般日報（週五美股收盤） |

**已排除週六 23:00**（因為週日早上美股沒消息）。

> ⚠️ GitHub Actions cron 偶爾會延遲 5-15 分鐘，這是平台特性，不可避免。如極在意準時，可改部署在 Render / Railway 排程。

---

## 三、本地測試

```bash
pip install -r requirements.txt

# 設環境變數（PowerShell）
$env:GMAIL_USER="expertise88864@gmail.com"
$env:GMAIL_APP_PASSWORD="你的16碼密碼"
$env:ANTHROPIC_API_KEY="sk-ant-..."
$env:DRY_RUN="1"   # 不寄信，只輸出 HTML 預覽

python morning_report.py
# 預覽會寫到 /tmp/morning_report_preview.html
```

---

## 四、雙模型 2330 預測說明

| 模型 | 邏輯 | 何時較準 |
|---|---|---|
| **模型 1：漲跌幅 1:1** | 假設 2330 開盤跟著 TSM ADR 漲跌幅同步 | 一般情況、無重大新聞 |
| **模型 2：60日比值回歸** | 用 60 日 `2330 / (TSM × FX ÷ 5)` 平均比值，反推今日合理價 | 想看「結構性溢/折價」是否合理 |

腳本會同時給出兩個預測值與其區間中值，幫你框出今日合理價帶。

---

## 五、00662 公允淨值說明

00662 追蹤 NASDAQ-100 指數（與 QQQ 同），但有以下溢/折價變數：
1. 申購贖回機制限制
2. 隔日匯率波動
3. 期貨升貼水

腳本估算邏輯：`昨日 00662 收盤 × (1 + QQQ 漲跌幅%)` → 給出粗估的今日合理開盤價。
若實際開盤偏離超過 0.5%，可能存在套利空間。

---

## 六、成本估算

| 項目 | 月成本 |
|---|---|
| GitHub Actions（Private repo 2000 分鐘/月免費，本任務每次 ~2 分鐘 × 22 次 = 44 分鐘） | **NT$0** |
| Anthropic Claude API（每日 ~3000 tokens 輸入 + 2000 tokens 輸出） | **NT$30–40** |
| Gmail SMTP | **NT$0** |
| Yahoo Finance 資料 | **NT$0** |
| **合計** | **約 NT$30–40 / 月** |

---

## 七、故障排查

- **沒收到信** → 進 GitHub repo 的 Actions tab 看最後一次 run 是否紅燈，點進去看 log
- **資料抓取失敗** → Yahoo Finance 偶爾限流，重試即可
- **新聞抓不到** → RSS 來源可能變動，編輯 `morning_report.py` 中 `RSS_FEEDS` 字典
- **Gmail 寄信失敗** → 確認 App Password 沒過期、兩步驟驗證沒關掉

---

## 八、自訂

- 想加 BTC / ETH / NVDA 等其他標的：在 `main()` 內 `quotes` 字典加一行 `"NVDA": fetch_quote("NVDA"),`
- 想改寄送時間：編輯 `.github/workflows/morning-report.yml` 的 `cron` 欄位（注意是 UTC 時間）
- 想改 Claude 分析語氣：編輯 `morning_report.py` 內 `call_claude_analysis()` 的 prompt
