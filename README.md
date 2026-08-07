# 個人化晨報系統(GitHub Actions 雲端版)

每天台灣時間 **約 06:00–06:20** 自動寄送一封繁體中文晨報。這不是新聞摘要器,而是一個
**個人化情報平台**:美股/台股行情與預測、總經、法人籌碼、預測市場(Polymarket)、
天氣與颱風警示、中彰投雲在地快訊、重大政策深度解析(行政院公報)、Podcast 重點、醫學文獻、
體育賽事與賭盤——並內建模型自我校正、資料品質監控、來源降級與 1,800+ 單元測試。

---

## 〇、信件內容總覽(2026-07 現況)

依信中出現順序:

| 區塊 | 內容 | 資料源 |
|---|---|---|
| KPI 條 | 立場、2330/00662/0050 預測、加權預測、持倉昨日帳上損益 | 內部模型 |
| 今日結論 | LLM 立場、關鍵價位、操作建議、主要風險 | DeepSeek(預設) |
| 天氣卡 | 彰化市/台中北區逐日天氣;**颱風風雨門檻警示**(對照停班停課參考標準)+ 停班停課公告新聞列 | Open-Meteo、Google News |
| 風險事件日曆 | 未來 7 天 CPI/FOMC/結算日/三巫/重點財報(台北時間) | 內建行事曆+財報 API |
| 一、美股收盤 | QQQ / TSM / SPY | Yahoo Finance |
| 二、總經指標 | VIX/SOX/DXY/日經/**KOSPI**/上證/黃金/銅 + 1Y 百分位 + 美債利率環境白話(WTI/BTC 照抓餵模型,僅不顯示) | Yahoo Finance |
| **預測市場觀點** | Polymarket:Fed 最近決議、2026 再升息、2028 美國總統、**台灣九合一政黨盤**、台積電財報 beat(財報季);顯示用,**不入任何模型** | Polymarket Gamma API |
| 五、加權指數開盤預測 | 夜盤台指期、預測點位、合理區間、訊號共識 | TAIFEX+內部模型 |
| 六、個股預測 | 2330 開盤 / 00662 公允價 / 0050 + ETF 進出參考價 + MA200 長線參考 | 三模型+校準 |
| 台股行事曆 | 股票申購(排除債券)、0050/0056 除息與配息(FinMind 自動補值) | TWSE、FinMind |
| 七、昨夜三大重點/世界大事/48h 情境/敘事變化 | LLM 從結構化新聞事件撰寫 | RSS+SEC+抽取器 |
| 八、科技板塊脈動 | 2330 供應鏈美股(NVDA/AMD/AVGO/MU/ASML/AAPL 等)逐檔,附證據分級 | Google News+8-K |
| 九、其他類股 | 金融(含 2882/2891 深追蹤)/生技/航運/傳產/重電/觀光/**房市與建商-中彰投** | Google News、MOPS |
| 十、台灣本地動態 | 台股當日焦點(法說、外資動向) | LLM 綜合 |
| Podcast 重點 | 11 檔節目白名單(股癌/游庭皓/Wall Street Breakfast/Odd Lots/BG2 等),每集重點+個股觀點與本報資料對照 | 獨立轉錄排程 |
| 體育快訊 | 世足(淘汰表+**冠軍機率**+90分鐘賭盤)、中職(比分/賽程/**單場賭盤**/戰績)、MLB(戰績表/焦點賽程/單場賭盤/世界大賽/MVP/賽揚盤)、NBA(**開季自動啟用單場賭盤**/總冠軍/東西區盤)、網球(冠軍收斂+美網冠軍盤);隊名全繁中 | ESPN、Yahoo、Wikipedia、Polymarket |
| 在地快訊 | 彰基/中國醫、建設、房市、產業/科技、學區/文教、交通異動、**選情(2026 九合一)**——台中/彰化/南投/斗六,同事件模糊去重 | Google News |
| 醫學文獻速報 | JAAD/JEADV/BJD/JAMA Derm/NEJM/AJO 近 7 天,標題中譯 | 期刊 RSS |

另有:**週日綜合輕量信**(僅體育/Podcast 有新內容才寄)、
**股癌雷達獨立信**(`gooaye_radar.py`,新集偵測後族群萃取+個股驗證)、
**週一晨報含最近 7 個已結算預測的錯誤檢討**。

### 賭盤語意鐵律

- 「賭盤(90分鐘)」= 足球三向含和的單場市場(DraftKings 運彩),**不可**稱冠軍機率。
- 「冠軍機率」= Polymarket 整屆奪冠市場(含延長/PK)。
- 兩者並列顯示、來源分別標示「(DraftKings 運彩)」「(Polymarket)」。

---

## 一、初次設定(一次性,約 30 分鐘)

### 步驟 1:申請 Gmail App Password

1. https://myaccount.google.com/security 確認兩步驟驗證已開啟
2. https://myaccount.google.com/apppasswords 建立「Morning Report」,複製 16 碼密碼

### 步驟 2:申請 LLM API Key

| Provider | 申請處 | 月成本 | 備註 |
|---|---|---|---|
| `deepseek`(**預設**) | https://platform.deepseek.com | NT$3–10 | `DEEPSEEK_API_KEY`;固定 `deepseek-v4-flash` + 思考模式;**唯一走特化結構化路徑的 provider** |
| `gemini`(免費備援) | https://aistudio.google.com/apikey | NT$0 | `GEMINI_API_KEY` |
| `anthropic`(品質最佳) | https://console.anthropic.com | NT$30–46 | `ANTHROPIC_API_KEY` |
| `openai`(GPT-5.6 系列) | https://platform.openai.com | NT$22(luna)–NT$220(terra) | `OPENAI_API_KEY`;**排程班沒有設這把金鑰**(2026-08-07 移除),要用需自行加進 workflow;走 legacy prompt,不走特化路徑 |

> 建議同時設 `GEMINI_API_KEY` 當免費備援;全部 LLM 失敗仍會寄出含行情與新聞的基本版。

### 步驟 3:建 GitHub repo(**建議 Private**)並上傳全部檔案

包含 `morning_report.py`、各模組(見「三、程式架構」)、`requirements.txt`、
`.github/workflows/*.yml`、`tests/`。

### 步驟 4:設定 GitHub Secrets / Variables

| Name | 必填 | 放哪 | 說明 |
|---|---|---|---|
| `GMAIL_USER` / `GMAIL_APP_PASSWORD` | ✅ | Secret | 寄信帳號與 App Password |
| `RECIPIENT` | 選填 | Variable 或 Secret | 收件人,逗號分隔多位;不設寄給自己 |
| `DEEPSEEK_API_KEY` | ✅(預設 provider) | Secret | |
| `GEMINI_API_KEY` | 建議 | Secret | 免費備援 |
| `ANTHROPIC_API_KEY` | 選填 | Secret | |
| `OPENAI_API_KEY` | 選填 | **Secret** | 用 `openai` provider 時必填 |
| `CONTACT_EMAIL` | 選填 | Secret | SEC EDGAR User-Agent;預設用 GMAIL_USER |
| `PORTFOLIO_1` / `PORTFOLIO_2` | 選填 | Secret | 持股(`2330:5,0050:10` 或 JSON);信中只顯示彙總 %,**明細絕不落地** |
| `PORTFOLIO_1_NAME` / `PORTFOLIO_2_NAME` | 選填 | Variable | 倉位顯示名 |
| `FINMIND_TOKEN` | 選填 | Secret | 提高 FinMind 配息 API 額度 |
| `NBA_FAVORITE_TEAMS` | 選填 | Variable | 逗號分隔關注隊(如 `Celtics,Lakers`) |
| `EMAIL_OVERFLOW_MODE` | 選填 | Variable | `full`(預設,不砍內容)/`keep`/`trim` |

#### 切換 LLM 模型(全部是 **Variable**,不是 Secret)

> ⚠ **Variables 與 Secrets 是兩個不同的分頁。** workflow 讀的是 `vars.X`,
> 它讀不到 Secret —— 設錯地方會靜默落回預設值,症狀是「一切照舊」:
> 沒有錯誤、沒有告警,只是沒切過去。2026-08-01 實際發生過。
> 從 2026-08-01 起,`state/run_manifest.json` 的 `llm.config` 會寫出本班
> **打算**用什麼;`llm.config.sources` 逐鍵記下
> `{resolved, source}` —— `source` 是 `repo_variable`(你明設的)、
> `workflow_default`(variable 是空的)還是 `workflow_fixed`(改不動)。
> 這樣才答得出「這個值是誰決定的」,而不只是「最後用了什麼」。
> `llm.config_issues` 只留**真正的設定錯誤**(打錯 provider、缺金鑰)。

| Variable | 預設 | 說明 |
|---|---|---|
| `LLM_PROVIDER` | `deepseek` | `deepseek` / `openai` / `gemini` / `anthropic` |
| `DEEPSEEK_REASONING_EFFORT` | `max` | v4-pro 的映射是 low/high→high、xhigh/max→**max**;送 `high` 只到中段 |
| `OPENAI_MODEL` | `gpt-5.6-terra` | 主分析模型 |
| `OPENAI_REASONING_EFFORT` | `medium` | **支援值依 model + endpoint 為準,先跑 Validate LLM Config**;額度與 timeout 會一起放大 |
| `EXTRACTOR_PROVIDER` | 空 | 事件抽取器可獨立指定;空 = 跟隨主分析 |
| `OPENAI_EXTRACTOR_MODEL` | `gpt-5.6-luna` | 抽取是機械性任務,不必用旗艦 |
| `OPENAI_EXTRACTOR_REASONING` | `low` | 刻意壓低:推理吃光額度會導致 0 產出 |
| `LLM_TOTAL_TIMEOUT_SECONDS` | 空 | 空 = 由程式依 provider 與推理強度算(見 `llm_telemetry.timeout_base`);設了會**壓過**自動放大 |
| `LLM_REQUEST_TIMEOUT_SECONDS` | 空 | 同上;另受「不得超過總預算 70%」上限 |
| `LLM_PRIMARY_PROMPT_PROFILE` | 空 | 主分析的問法。空 = 依 provider 自動選(deepseek→`luna56_xhigh_v1` 特化結構化路徑、其餘→`deepseek_legacy_v1`)。設 `deepseek_legacy_v1` 是**逃生門**:不改程式碼即可回舊 prompt |
| `OPENAI_STORE` | `0` | 1 = 允許 OpenAI 保存這次請求。預設 0 |
| `OPENAI_TEXT_VERBOSITY` | `high` | Responses 的 `text.verbosity` |
| `OPENAI_REASONING_SUMMARY` | `auto` | 推理摘要,**僅供遙測不進信件**;需組織驗證,取不到不算錯 |
| `OPENAI_REASONING_CONTEXT` | `current_turn` | GPT-5.6 預設 all_turns;晨報每天是獨立判斷,故明設 current_turn |
| `OPENAI_PROMPT_CACHE_TTL_SECONDS` | 空 | 快取存活秒數。空 = 用 provider 預設(30 分) |

> 上表的**預設值欄位由 `tests/test_workflow_contract.py` 對 workflow 逐格比對**
> (第十一輪 P2-1)。在此之前它漂過:`DEEPSEEK_REASONING_EFFORT` 寫著預設 `high`
> 且「`high` 是最高檔」,而實際預設已是 `max`、且 v4-pro 的 `high` 只到中段。
> 設定文件漂移的代價不是不專業,是**使用者做了正確的事卻沒有效果**。

> ⚠ 官方 Models 頁面列出的強度**不等於**該 endpoint 真的接受。
> 2026-08-01 實測:`gpt-5.6-luna` 在 chat/completions 上拒絕 `max`,
> 而程式會靜默退回 provider 預設。金絲雀的推理強度矩陣會逐一實測。

推理強度同時放大 **輸出額度** 與 **時間預算**(`llm_telemetry.CAP_MULTIPLIER`
與 `EFFORT_TIME_MULTIPLIER`)。只放大額度不放大時間會必然逾時 —— 額度是上限、
沒用到不計費,時間卻是真的會被花掉並排擠寄信的東西。

### 步驟 5:Actions tab 手動 Run workflow 測試一次

---

## 二、排程與 Workflows

| Workflow | 排程(UTC) | 功能 |
|---|---|---|
| `morning-report.yml` | 每日 22:00(=台北 06:00) | 主晨報;週日走輕量綜合信;寄信成功後 commit state |
| `podcast-digest.yml` | 每日 4 次 | faster-whisper 本地轉錄 + DeepSeek 摘要 → `state/podcast_digest.json` |
| `gooaye-radar.yml` | 週三/六下午 | 股癌新集偵測 → 族群雷達獨立信 |
| `ci.yml` | push/PR | ruff + py_compile + pytest;另有手動 dry-run-preview |
| `monthly-ic-report.yml` | 每月 | 因子 IC / 計分回測報告(離線,不寄信) |

> GitHub Actions cron 可能延遲 5–15 分鐘,平台特性。

---

## 三、程式架構

```
morning_report.py     主程式:資料抓取、三模型預測、校準、prompt、渲染、寄信、state
render_utils.py       渲染輔助:體育卡、Podcast 卡、MLB/NBA/網球中文隊名、Markdown→HTML
news_rules.py         新聞召回/去重/白名單規則(政策財經白名單、發布者去重)
news_events.py        事件身分/聚合/生命週期(cluster key、期別 bucket、否決與待決判定)
story_ledger.py       線索帳本:敘事狀態機(醞釀→發展→高潮→收斂→沉寂)、軌跡、主旨比對
llm_postprocess.py    LLM 輸出後處理(段落截斷、字數配額、選文理由剝除)
num_utils.py          數值工具
session_calendar.py   台股交易日推算
data_quality.py       來源資料契約(筆數/必要欄位/值域/長期填充率)
model_history_store.py  model_history 分區儲存與完整性稽核(checksum/manifest)
model_confidence.py   預測診斷(Clark-West、Mincer-Zarnowitz、價格序列組裝)
factor_ic.py          因子 IC 計算
alpha_factors.py      因子定義
overfit_check.py      過擬合檢定(SPA/MCS)
fz_score.py           財務體質分數
valuation.py          估值模型
backtest_runner.py    回測執行器(獨立排程)
tw_policy_sources.py  台灣政策一手來源(行政院公報、院會)
portfolio_risk.py     持倉曝險引擎(現僅後台,卡片已依使用者要求隱藏)
podcast_digest.py     Podcast 轉錄與摘要(獨立排程)
gooaye_radar.py       股癌雷達獨立信
tools/                稽核與驗證腳本(codex_review、mz_walkforward、report_watchdog…)
tests/                1,800+ 測試(不連網;conftest 隔離 state 寫入與網路)
state/                執行期狀態(見下);由 workflow 於寄信成功後 commit 回 repo
```

### state/ 檔案

| 檔案 | 用途 |
|---|---|
| `history.json` | 每日預測與實際開盤(450 天),供 MAE 加權與 bias 校正 |
| `model_history/YYYY-MM.json.gz` | 市值前百 point-in-time 快照,按月分區 gzip(上限 520 交易日);舊單檔 `model_history.json` 凍結唯讀 |
| `event_timeline.json` | 新聞事件生命週期(rumor→confirmed→implemented) |
| `podcast_digest.json` | Podcast 摘要與已顯示標記 |
| `conformal_intervals.json` | 預測區間校準 |
| `source_health_history.json` | 來源健康 30 天史 |
| `run_manifest.json` | 每次執行耗時/來源結果(觀測用) |
| `emails/` | 寄出信件去識別存檔(gzip,供檢索) |
| `twse_top100_archive.json` / `revenue_consensus.json` | 選填外部資料 |

---

## 四、預測模型(計分與係數凍結,改動需回測)

### 2330 三模型 + 自我校正

| 模型 | 邏輯 |
|---|---|
| 漲跌幅 1:1 | 昨收 × (1 + TSM ADR%) |
| 60 日比值回歸 | `2330/(TSM×FX÷5)` 均值反推 |
| ADR 衰減 | 昨收 × (1 + TSM% × 實證 decay ≈0.75) |

三模型近 20 日 MAE 反比加權;00662/2330/加權另做 bias 修正(±2% 夾限,
需 5+ 交易日樣本)。回測證實 beta 0.31 近最佳;信中附準確度回顧。

### 00662 公允價

QQQ/00662/TWD 近 3 月估實證 beta 與平均偏離;樣本不足降級 beta=1 並標示。

### 台股關注五檔(內部資料,卡片已隱藏)

point-in-time 市值前百 + 法人 30 日 + 月營收 + 大戶持股 → ridge 模型 +
產業中性化 + regime 權重 + Platt 校準;walk-forward 監控 Brier/ECE/覆蓋率。
**Top5 卡與估值溫度/選擇權/持倉曝險卡皆依使用者要求隱藏,資料照算餵 LLM。**

---

## 五、Polymarket 預測市場整合

- **免金鑰** Gamma API(`gamma-api.polymarket.com`);全部呼叫走統一
  `_poly_get_json` 護欄:單次嘗試 + 8s timeout + **連 2 敗斷路** + 整包 **90s
  硬預算**——賭盤是加值資訊,寧缺勿拖垮晨報。
- 單場:MLB(slug=`mlb-{客}-{主}-{美東日}`)、NBA(開季自動生效)、中職
  (`tag_slug=cpbl`);市場挑選要求「兩結果都是已知隊名」,排除 Over/Under 等 prop。
- Futures:世足冠軍、MLB 世界大賽/MVP/賽揚、NBA 總冠軍/東西區、美網男女單
  (slug 含年份,`_POLYMARKET_FUTURES` 每季更新)。
- 總經/政治(`fetch_polymarket_pulse`):Fed 決議與台積電財報盤動態搜尋;
  年度型 slug 每年更新;佔位項 Team A/Player A/Party A/Other 自動剔除;
  endDate 逐時刻比對防已結算盤誤現。
- **本專案為 read-only 情報工具**:不下單、不自動交易(Polymarket 將台灣列為
  限制開新倉地區);所有機率顯示用、不入模型計分。

---

## 六、個人化區塊備忘

- 天氣卡:彰化市/台中北區;颱風門檻(平均風 50/陣風 89/雨量 350 km·h/mm)
  達標紅字+固定免責「以縣市政府公告為準」;人事總處 dgpa.gov.tw 憑證缺 SKI
  程式抓不到 → 停班停課走新聞源(視窗=台北昨日 16:00 起)。
- 在地快訊七主題(彰基/中國醫、建設、房市、產業/科技、學區/文教、交通異動、
  選情);同事件模糊去重(bigram overlap ≥0.5,短標題 ≥0.85)。
- Email 大小:`EMAIL_OVERFLOW_MODE=full`(預設)接受 Gmail 102KB 摺疊不砍內容;
  `trim` 模式會依優先序縮減,且被砍掉的 Podcast **不會**被誤標已顯示。

---

## 七、本地測試

```bash
pip install -r requirements.txt
pytest -q                        # 1,800+ 測試,不連網、不寄信

# 完整流程預覽(連真實資料,不寄信);PowerShell:
$env:DRY_RUN="1"; $env:LLM_PROVIDER="deepseek"; $env:DEEPSEEK_API_KEY="sk-..."
python morning_report.py         # 預覽寫到 /tmp/morning_report_preview.html
```

---

## 八、成本估算

| 項目 | 月成本 |
|---|---|
| GitHub Actions(每次 2–8 分 × 每日) | NT$0(免費額度內) |
| DeepSeek API(晨報+Podcast 摘要+雷達) | NT$5–15 |
| 其餘資料源(Yahoo/TWSE/ESPN/Polymarket/Open-Meteo/FinMind 免費層) | NT$0 |

---

## 九、故障排查

- **沒收到信** → Actions tab 看最後一次 run;資料源失敗會降級寄出,「完全沒信」
  通常是 SMTP 或排程問題。
- **資料品質區塊** → 信內列出每來源 ok/降級/失敗;LLM prompt 同步收到,
  不會把「抓不到」誤判成「沒訊號」。
- **Polymarket 全缺** → 看 log 是否斷路器觸發(連 2 敗/90s 預算);隔天自動恢復。
- **state push 失敗** → 只印警告不影響寄信;workflow 需 `contents: write` +
  `fetch-depth: 0`。
- **中職比分/官網 geo-block** → 已固定走 Yahoo 運動 API + Wikipedia 戰績備援。
- **政府網站(dgpa/mohw/cbc)** → 憑證缺 Subject Key Identifier,python 全環境
  驗不過,一律走新聞源替代,**勿 verify=False**。

---

## 十、開發紀律(給協作 AI / 未來的自己)

1. **計分/預測係數凍結**:任何改動需先回測(`backtest_data/`、monthly IC)。
2. **端點先探活**:新資料源/查詢一律先 live 實測召回與結構,才寫程式。
3. **外部 code review**:非瑣碎改動需經 `tools/codex_review.sh`(GPT-5.6,
   read-only)審到 APPROVE 才 push;文件/測試-only 可跳過。
4. **隱私**:持股明細只存在 Secrets;信件存檔去識別;個人任職/房產資訊
   不落地於信件、log 或公開檔案。
5. **顯示層與模型層分離**:使用者要求隱藏的卡片(Top5/曝險/估值溫度/選擇權)
   關顯示不關計算;新增資訊(Polymarket/在地)一律不餵計分。
6. **降級優先**:每個來源獨立 try;晨報必達,內容寧缺。

## 已知限制

- repo 若為 Public,state 與查詢主題會暴露個人關注領域(建議轉 Private)。
- `state/model_history/` 月分區保存 520 交易日;更長歷史需另存 archive。
- state 每日 commit 回 repo,長期會膨脹 git 歷史(遷移外部儲存為中期方向)。
- NBA 單場賭盤 slug 已以上季市場驗證,開季首日仍應目視確認一次。

### 換模型之前:先按金絲雀

Actions → **Validate LLM Config** → Run workflow。它讀**與晨報同一組** repo
variable,**不寄信、不寫 state**(唯讀權限),只回答四個問題:

- 模型 ID 存在嗎(打錯字的症狀是 400,而 400 有太多其他原因)
- 推理強度收得下嗎
- 抽取器的結構化輸出支援嗎
- **跑一次要多久** —— 拿實測耗時比對程式算出來的單次上限

最後一項是 2026-08-01 真正缺的那個數字:逾時只告訴你「超過 75 秒」,
不告訴你 240 秒夠不夠。注意探測用的是短 prompt,生產的 prompt 約 85,000
token,所以那個秒數是**下界**。
