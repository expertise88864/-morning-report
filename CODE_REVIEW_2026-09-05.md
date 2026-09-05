# 全專案 code review：2026-09-05

## 結論與範圍

本輪是 repo-wide、風險導向審查，不是只審新增 Claude hooks 的 diff。審查基準為
`7d75f3a`；期間同步至 `15fcf85` 的兩筆遠端更新只修改 Podcast state。
確認 4 項既有問題，其中 2 項 P1、2 項 P2。**未修改這些產品邏輯，也未更動模型係數。**
沒有證据可以把「測試通過」或「workflow diff 獲准」說成整個專案沒有缺陷。

檢查方式分三層：全案靜態檢查與既有測試、核心路徑人工追蹤、對高風險假設做
離線故障注入。不是所有歷史資料、每一條條件分支或每個外部 API 都做了實機驗證。
未寄真實信、未呼叫生產 LLM、未改寫真實 state；重現只使用臨時合成資料。

| 領域 | 本輪追蹤的邊界與證據 |
| --- | --- |
| 晨報管線 | `_PIPELINE`、各 phase 邊界、LLM 備援、整體／區塊渲染備援、SMTP 後才持久化 |
| 狀態與完整性 | `state_store`、`model_history_store`、主程式月分區寫入、`state_publish`、push allowlist |
| LLM 與可信度 | 外部文字消毒、圍欄與歷史回流、Python 立場權威、結構化驗證／渲染、重試總預算 |
| 新聞與事件 | 分群／排序入口、事件來源身分、story 狀態轉移與提示詞出口、回歸測試 |
| 預測與研究 | point-in-time history loader、分區／legacy 消費端、IC／alpha／overfit 工具的樣本來源 |
| 輸出與隱私 | HTML escape、`safe_href`、持倉彙總邊界、無持倉明細的報告與 review evidence |
| Podcast／雷達 | 逐集儲存、壞檔不覆寫、跨 runner pending 佔位、寄送成功與 shown/sent 標記 |
| CI／監控 | 主班→artifact→publish、收據獨立發佈、schema gate、watchdog、依賴鎖版與權限分離 |
| 新審查流程 | 唯讀 reviewer、精確模型證據、staged/untracked/outgoing diff、quota 分類、Git-backed pending／audit |

## CONFIRMED findings

### CR-01 — P1：重建 manifest 會抹除已遺失月份的證據

位置：`model_history_store.py:122-134`、`:164-172`；呼叫端
`morning_report.py:7427-7431`。

`write_partition_manifest()` 以空的 `partitions` 開始，只遍歷目前磁碟存在的
`*.json.gz`。它為「還存在但損壞」的檔案保留舊 checksum，卻沒有保留
「舊 manifest 登錄過，但磁碟已不存在」的檔名。因此下一次日常寫入就會把該月份
從 manifest 刪掉，之後 strict 稽核不再知道歷史少了一個月。

可重現情境：某個舊月份已不在 legacy／本次合併視圖中，分區意外遺失；本日繼續
寫入其他月份。`save_model_history_records()` 的保護是逐個待寫月份，並不阻止末尾
整體重建 manifest。不是說所有缺檔都會觸發：若該月份被重建為一份檔案，仍可能由
舊 checksum 偵測；漏洞在於完全沒有出現在 glob／合併視圖中的月份。

離線實測：建立兩個合法月份和 manifest → 刪除**臨時測試資料**的第一個月份 →
`verify_history_integrity()` 報 `missing_partition` →
`write_partition_manifest(..., rewritten=set())` →
`verify_history_integrity(..., strict=True)` 竟回 `ok=True`。

影響：原應 fail-closed 的研究／月報可能把缺失的歷史當成完整樣本。既有測試涵蓋
checksum 變動、壞 JSON、未登錄分區，未阻止「缺檔→日常重寫 manifest→轉綠」。

最小修正方向：以既有 manifest 條目為基底；只有明確且獲准的保留政策操作才能移除
登錄。缺檔條目繼續保留並告警，另加上述跨兩次操作的回歸測試。

### CR-02 — P1：非必要歸因失敗會清掉有效的 Python 立場，降級成 LLM 權威

位置：`morning_report.py:25937-25953`，以及 `:26036-26046`。

Python 分數計算和「今日相對昨日的歸因」放在同一個 try。只要 `_stance_attribution()`
拋例外，except 就把**已成功計算**的 `STANCE_PY` 清成 `{}`。後續持久化邊界在
Python total 缺席時會將 LLM 的 label／score 寫入主要 `stance_label/stance_score`，
隔日敘事回顧又消費這兩個主欄位。這違反「LLM 只能抄錄、不能回流成權威」的不變式。

離線故障注入：mock `_compute_stance_score()` 回有效 `total=4`，歷史分項含 `NaN`，
使真正的 `_stance_attribution()` 在 `int(NaN)` 拋 `ValueError`；執行真正的 phase，結果
`STANCE_PY == {}`，而 `_DEGRADED_STEPS` 仍為空。LLM 呼叫已 mock，沒有連網。

最小修正方向：分開分數與歸因的錯誤邊界。歸因失敗只清歸因；分數真正不可得時
顯示未知並留下結構化降級，不提升 LLM 輸出為權威。補測歸因單點失敗與歷史回流。

### CR-03 — P2：三支研究工具仍讀凍結 legacy，漏掉已存在的新樣本

位置：`alpha_factors.py:111-118`、`factor_ic.py:43-50`、
`overfit_check.py:160-168`。

三者直接讀 `state/model_history.json`，不經目前共用的 strict 分區 loader。
本機只輸出日期與筆數的驗證結果：legacy **143 筆、末日 2026-07-15**；
`load_model_history(strict=True)` **245 筆、末日 2026-09-04**。
因此重新跑研究也不會加入後續樣本；新 snapshot 因子是否有效仍在用舊資料判斷，
也繞過 manifest 完整性防線。

這不代表目前 monthly report 主入口也有同一個問題；它的共用 loader 遷移不能
證明其餘三支已一併遷移。既有測試大多驗數學函式，未比較所有研究入口的資料覆蓋。

最小修正方向：共用 `load_model_history(strict=True)`，保留現有公式與參數；
在只有 legacy 的 fixture 外，加入 legacy + 新月份分區的整合測試。

### CR-04 — P2：state 發佈／下載失敗不進主班告警，可能呈現綠燈

位置：`.github/workflows/morning-report-b.yml:485-497`、`:563-570`。

state artifact 下載採 `continue-on-error: true`；下載失敗只讓後續 publish step
被略過，`publish-state` 可以保持成功。就算實際 git push 失敗而 job 變紅，
`alert-on-failure` 也只 needs／判斷 `send-report`，不會為 publish-state 寄告警。

既有 watchdog **有補抓 state gap 的能力**，因此不能說此問題永遠沒人發現。
但它是每日單次排程；在 watchdog 已執行後才完成的延遲主班／手動重跑，沒有
生產者自己的發佈失敗通知。單純下載失敗的 run 甚至沒有整體紅燈可供 GitHub 通知。
這項由 workflow 控制流確認，**未故意破壞線上 artifact／push 來實驗**。

最小修正方向：區分「預期沒有 artifact」與「state_dirty 且契約成功但下載失敗」；
後者必須 fail／留下 output。另建發佈告警或讓告警 job 消費 publish-state 結果，
文案明確說「信已寄、state 未落地」，避免觸發重複寄信。加 workflow 契約測試。

## 驗證紀錄

- 全案 Ruff：通過。
- 專案 `.venv` 的 mypy boundary modules：5 個模組、零錯誤。
- 專案 `.venv` 的 typing + Claude workflow focused tests：32 passed。
- 隨後修正獨立審查提出的 8 項 workflow 缺陷；新增 replace-ref、跨分支、amend、
  非 fast-forward、並行 snapshot、額度文字變體與暫存遺失案例後，Claude workflow
  並補上正常 tag push／quota 分流與已發佈 tag 不可改寫測試，focused tests：**37 passed**，
  全案 Ruff 再次通過。此增量驗證不冒充重跑整套測試。
- 初次完整 pytest 誤用全域 Python：3455 passed、2 skipped、1 failed；失敗原因是
  該 interpreter 沒有 mypy，**不可記成全綠**。改用專案 `.venv` 重跑完整套件後：
  **3467 passed、2 skipped、667 warnings，退出碼 0，768.78 秒**。warnings 多為
  既有測試讀檔未關閉的 ResourceWarning，未在本次流程設定中批量修改。
- CR-01／CR-02 的合成資料故障注入：確認重現；CR-03 的日期／樣本數比對：確認。
- 未驗證：真實 SMTP、外部來源即時可用性、付費 LLM 生產呼叫、Linux Actions 實跑。
  本機是 Windows；不以它取代 GitHub CI 的平台驗證。

## Claude 補審與交付政策

依使用者 2026-09-05 最新明確指示，所有改動（含文件與測試）使用
`claude-opus-5`、`high`，不替換模型。額度不足可先提交／推送本次已驗證的改動；
commit 以 `Claude-Opus-5-Review: pending` 和 `Claude-Opus-5-Review-Effort: high`
標示，**不是通過**。非額度錯誤、模型證據不符與 `REQUEST_CHANGES` 不得假裝通過。

補審以完整 SHA 追蹤，成功後另推空 audit commit 記錄 passed／reviewed SHA；不改寫
已發佈歷史。排程屬本機 Codex task，電腦必須開機且 Codex 可執行；Claude 必須保持登入。
這是 Codex 本機 Git 專案的工作流程，不能宣稱已攔截所有純網頁 ChatGPT/cloud 專案。
