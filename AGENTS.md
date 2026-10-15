# AGENTS.md — 給自動化程式代理(Codex CLI / Claude Code 等)的專案約定

## 最新使用者定案：每個 PR review 一次（2026-10-15）

本節優先於下方及舊文件中所有「逐 commit review」「review trailer」「audit commit」規則；模型、CI、禁止付費測試與資料保護要求不變。
- 不再逐 commit review，也不用 pre-commit／commit-msg hook 審查（兩支 hook 已刪除）；commit 不需要 review trailer。
- 分支推上 `codex/*` 後執行 `python tools/pr_review.py`：開（或找到）指向 main 的 PR，以 `gpt-6.1-sol`／`xhigh` 唯讀審查 `merge-base(main, head)..head` 整段 diff（`tools/codex_review.sh`，以 session 紀錄驗證實際模型，證明不了就不算一輪），結論與 findings 寫成 PR comment，並在 exact head SHA 設 `codex-review/gpt-6.1-sol` commit status。
- findings 逐條驗證（CONFIRMED／REJECTED／UNCERTAIN），只修 CONFIRMED；修正後 push，再跑 `python tools/pr_review.py --resume`（同一 session）直到 APPROVE。head 一變，舊 APPROVE 即作廢。
- 有 finding 經驗證為 REJECTED、程式不該改時：把證據寫成反駁檔（不可含 diff），執行 `python tools/pr_review.py --resume --rebuttal 反駁檔`，在同一 session 請審查者重新查證；同一 head 只能反駁一次，審查者不接受就維持 REQUEST_CHANGES（使用者定案 2026-10-15）。
- 推 main 時 pre-push hook（`_delivery.py`，policy `pr_review: true`）除候選 CI 外，還要求 exact head 有本人建立的 APPROVE status，且審查範圍涵蓋所有要進 main 的 commit。額度不足時 status 為 quota-pending：可照常發佈但不是通過；額度恢復後用 `python tools/pr_review.py --pending` 找出並補審，結果寫在同一 PR。
- 不再產生 audit commit；「空 audit commit 也走候選流程」已刪除。純文件變更仍走候選分支、CI 與 PR review。
- 切換前帶 `Claude-Opus-5-Review: pending`（或 `GPT-6-1-Sol-Review: pending`）的舊 commit 仍補審（Claude 佇列用 `claude-opus-5-5`／high），結果記在 PR comment 或 issue，不再寫 audit commit。

## 最新使用者定案：禁止付費測試，隔日正式晨報驗收（2026-09-07）

- 本節取代舊規則中晨報上線前必跑付費 dry-run／canary 的要求。
- 禁止為測試、預覽、修正驗證呼叫付費模型 API；不寄信的 DRY_RUN 也會收費，不得執行。不得用手動觸發正式晨報代替測試。
- 保留離線 fixture／渲染回歸、lint、型別、完整遠端 CI、exact-SHA 候選驗證與正常 main 發佈流程；不是略過 CI。
- 實際新聞內容與手機閱讀效果等隔天正式排程寄出的晨報再驗收；此前標記「已上線、內容待隔日驗收」，不得宣稱已實機驗證。有問題修正後仍走相同流程，不另付費重跑。
- 每週 DeepSeek 線上契約 canary 同樣停用。正式晨報、Podcast 正式資料整理及品質告警不變；原有 Codex／Claude 程式審查與額度恢復補審不變。
- 舊任務或排程要求付費驗證時，以本節為準。恢復付費測試需使用者重新明確同意。

## 最新使用者定案：遠端 CI 候選驗證與正式發佈（2026-09-06）
本節取代下方／舊任務／舊排程中「每次候選 push 前完整本機 CI」及「直接推 main 再修」的規則；不變更醫療內容核可、獨立 Codex/Claude review 或資料保護要求。
- 四個專案採本機快速檢查與相關回歸測試 → codex/* 候選分支 → 完整遠端 CI → 已驗證同一 SHA 才正常快轉進 main；候選 push 不要求先有完整本機 CI。
- 使用 _delivery.py 與 _delivery_policy.json；正式 push 的 pre-push hook 必須驗證 exact SHA 的候選 push workflows/jobs/steps 成功，並確認最新 main 是候選祖先。新的修改、生成、整合或 rebase 使旧證據失效。
- 網站還須 same-repository PR、exact-SHA Vercel Preview 與瀏覽器檢查；Vercel 正式建置前另驗證候選 CI，未驗證版本不可上線。部署成功不等於 CI 通過。
- HsiaoEye 視覺基準只能由 Ubuntu 產生並人工確認，不自動接受差異；保留 CMS 新修改，衝突停止，不 force-push。CMS 存檔不等於正式發佈完成。
- 晨報候選 CI 不得寄信、寫回正式 state 或觸發正式排程；變更產報/LLM/外部資料關鍵路徑時另做不寄信 dry-run。既有正式寄信排程不得因候選驗證中斷。
- CI 失敗持續診斷並修正可確認缺陷，修正後重跑完整遠端驗證；取消、逾時、缺失、讀不到及應跑卻跳過皆不通過。禁止 skip-ci、降門檻或繞 hook 製造全綠。
- 審查方式見上方 2026-10-15 定案（每個 PR 一次，gpt-6.1-sol／xhigh／read-only）；quota pending 只延後模型審查，不豁免正式發佈的遠端 CI。
- main 發佈後還要驗證 exact-SHA 正式 CI／部署及適用 smoke checks，才可宣告交付；純文件變更也走候選流程。
- 詳細入口、範圍與限制見 REMOTE_CI_DELIVERY.md。不得把本機快速檢查說成完整 CI，也不得把候選 CI 綠燈當作正式部署成功。

## 使用者最新定案：push 前本機 CI 全綠，push 後驗證 GitHub CI（2026-09-05）

- 所有專案、所有改動都適用，包括程式、文件、設定、測試、生成物及修正；任何分支的 push 都不得自行豁免。
- 任何分支 push 前，核對實際待推內容的完整適用本機 CI 等效檢查，全部必須成功；push 後追蹤該完整 SHA 的 GitHub CI，全綠才可宣告交付完成。失敗、取消、逾時、等待、應跑卻跳過、無結果或讀不到，都不是通過。
- 保留可核對的 SHA、CI 執行連結／ID 與各項結果。本機測試、lint、build、preflight 通過，以及模型 review APPROVE，都不能冒充遠端 CI 成功。
- 修正、重新生成、合併、rebase 或新增 commit 後，舊版本的 CI 結果不得直接沿用；必須驗證新的實際待推版本。
- 模型審查額度不足時，PR review 記為 quota-pending、額度恢復後補審（2026-10-15 定案）；這只延後模型審查，絕不豁免 CI 全綠。
- 使用者已明確選擇「任何分支 push 前先通過本機 CI 等效檢查，push 後再驗證 GitHub CI」；不要再要求第一次 push 前先有遠端 CI。任何分支都無本機檢查豁免；缺 CI 或本機必要檢查無法執行時仍須回報並取得決定，不能視為全綠。
- 不得透過 `--no-verify`、skip-ci 標記、關閉／刪除檢查、降低門檻、改 branch protection 或宣稱非必要檢查等方式製造全綠。
- 本定案優先於本檔及舊任務／排程中「本機驗證後可直接 push」「額度不足可先 push」等較寬鬆敘述；既有醫療內容核可、模型、effort 與 review 要求仍須同時滿足。


本檔由 Codex CLI 原生讀取,故**外部審查時會自動生效**,不必每輪在 prompt 裡重述。
與 `CLAUDE.md` 的關係:CLAUDE.md 是使用者對 Claude Code 的工作流程規範(何時審、
怎麼審);本檔是**專案本身的不變式**,任何代理修改本 repo 都適用。

## 這個專案是什麼

台股晨報自動化系統。GitHub Actions 每天 06:00(台北)寄一封繁體中文 HTML 信,
內容涵蓋行情預測、新聞分析、政策解析、生活與體育資訊。單一使用者、單人維護。

**最高原則:晨報不可斷。** 任何區塊的失敗都必須降級成「該區塊缺席」,
絕不可讓整封信寄不出去。已有四條「整封信失敗」路徑的兜底,不要削弱它們。

## 不可違反的不變式

1. **Python 權威、LLM 只能抄錄。**
   立場分數、狀態機轉移、排名、機率等一切判斷由 Python 計算;LLM 只負責把已算好
   的結論寫成文字。渲染層有合規防線會偵測並替換不符的 LLM 文字。
   新增任何「讓 LLM 自行判斷」的路徑前,先確認它不會回流成權威資料。

2. **外部文字一律先消毒再圍欄。**
   所有抓取來的文字(新聞標題/摘要/全文、政府公報、Podcast 轉錄、跨日回流的
   歷史標題)進入 LLM prompt 前,必須過 `_external_text()`,並包在
   `<UNTRUSTED_SOURCE_DATA>` 圍欄內;**安全規則必須寫在圍欄外**,否則規則本身
   也變成可被覆寫的素材。圍欄**不得巢狀**(內層結束標籤會提前關閉外層)。

3. **降級與靜默是兩回事。**
   任何降級都必須留下痕跡:`_DEGRADED_STEPS`、run manifest、或 `::warning::`。
   **禁止**在「自己會吞例外的函式」外面包 try——那是死碼,曾重複犯過三次。
   模組應把失敗拋給呼叫端,由呼叫端決定降級。

4. **state 檔一律 atomic write,且必須登錄 push 清單。**
   新增跨日累積的 state 檔時,務必加進 `_state_push_paths()`——CI 每天是全新
   runner,沒 commit 回 repo 等於次日讀不到,而本機完全正常。
   已有 meta 測試會擋住漏登錄(`tests/test_state_and_fallback.py`)。
   **state 讀取失敗時不得用局部重建覆蓋既有檔案**(那是不可逆的歷史遺失)。

5. **不得擅自更動計分權重、預測係數、conformal 參數。**
   這些經過回測驗證(如 beta 0.31)。要改必須先有 IC / MCS / event study 證據,
   並同步更新 `model_version` 與相關測試。

6. **隱私**:PORTFOLIO 持股代號與股數不得進入 HTML、LLM prompt、state、log。
   本 repo 目前為**公開**,新增文件前請確認不含持倉組成。

## 工程慣例

### 所有改動的 CI 推送門檻（使用者 2026-09-05 定案）

- 任何分支 push 前，先依目前 CI workflow 完成本機等效檢查，全部必須退出碼 0。
  本 repo 的 push CI 為 `.github/workflows/ci.yml`：鎖版依賴驗證、全案 compileall、
  Ruff、mypy 與完整 pytest；不得用 focused tests 冒充全套。記錄本機／runner 差異。
- push 後必須追蹤**該次完整 SHA** 的 GitHub CI，所有適用必要檢查成功才可宣告交付完成。
  失敗、等待、取消、逾時、未觸發或讀不到結果都不是通過；條件未適用的 job 另行註明。
- 所有文件、測試、設定與補審修正同樣適用。內容改變就重新驗證相關
  項目。模型審查額度不足只允許延後 PR review，**不能豁免 CI**。
- 本機必要檢查無法執行時回報並取得決定；禁止跳過 hook、加 skip-CI、停用測試／job
  或放寬門檻來製造全綠。CI 真缺陷修正後以新 commit 走同一流程，不改寫已發佈歷史。

### GPT-6.1-Sol PR review(使用者 2026-10-15 定案,取代 2026-10-14 逐 commit 閘門)

- **每個 PR review 一次**,不再逐 commit / 逐 push 審查;pre-commit、commit-msg hook 已刪除,
  commit 不加 review trailer,也不再產生 audit commit。所有 PR 都要審(含文件/tests/cosmetic)。
- 推上 `codex/*` 後執行 `python tools/pr_review.py`:開(或找到)指向 main 的 PR,以
  `gpt-6.1-sol` / `xhigh` 經 `tools/codex_review.sh`(Codex CLI、`--sandbox read-only`、
  `--ignore-user-config`、關閉 web search 與 apps)審查 `merge-base(main, head)..head`;
  **不得 fallback** 到其他模型或較低 effort。wrapper 以該次 Codex session rollout 的 `turn_context`
  證明實際模型/effort/read-only、本 repo 與本次呼叫(`tools/gpt_diff_review.py verify-session`),
  證明不了、未登入、非明確裁決一律不算一輪。CLI 由 `python tools/gpt_diff_review.py which` 選
  (gpt-6.1-sol 需要較新的 Codex CLI;也可用 `GPT_REVIEW_CODEX_BIN` 明示)。
- 結論與 findings 寫成 PR comment,並在 exact head SHA 設 `codex-review/gpt-6.1-sol` commit status
  (`APPROVE base=<SHA>` / `REQUEST_CHANGES base=<SHA>` / `quota-pending base=<SHA>`)。
  推 main 時 `.githooks/pre-push`(`_delivery.py`,policy `pr_review: true`;候選 policy 不能關掉
  main 上已要求的 pr_review)除候選 CI 外,還要求本人建立的 APPROVE(或 quota-pending,會警告)
  且審查範圍涵蓋所有要進 main 的 commit。
- **額度不足**:status 記 quota-pending,可發佈但**不是 APPROVE**;額度恢復後
  `python tools/pr_review.py --pending` 列出未 APPROVE 的 PR,checkout 其 head 後以
  `--pr <號碼>` 補審,結果寫在同一 PR。不得 amend 或 force-push 已發佈歷史。
- **審查意見不照單全收**:finding 必須逐項由主代理對目前程式碼驗證(CONFIRMED / REJECTED /
  UNCERTAIN 附證據);只修 `CONFIRMED`,修正 push 後 `python tools/pr_review.py --resume`
  (同一 session)直到 APPROVE。head 一變,舊 APPROVE 即作廢。
  REJECTED 的 finding 不改程式,以 `--resume --rebuttal 反駁檔`(證據,不可含 diff)請審查者在同一
  session 重新查證;同一 head 只能反駁一次。
- **歷史 pending**:切換前帶 `Claude-Opus-5-Review: pending` 的 commit 仍以 `claude-opus-5-5` /
  `high` 補審(`python tools/claude_diff_review.py pending` 與 `commit --commit <SHA>`);
  帶 `GPT-6-1-Sol-Review: pending` 的以 gpt-6.1-sol / xhigh 補審(`python tools/gpt_diff_review.py
  pending` 列出)。Claude 審過即算(使用者 2026-10-14)。結果記在 PR comment 或 issue,不再寫 audit commit。
- 安裝/修復 hook:`powershell -NoProfile -ExecutionPolicy Bypass -File tools/install_claude_review_hooks.ps1`
  (檔名沿用,只安裝 pre-push)。**禁止**以 `--no-verify`、改 `core.hooksPath` 或其他方式繞過。

- **驗證一律取真退出碼**:`pytest -q > f 2>&1; E=$?`。
  **絕不可用 `pytest | grep`**——管線會吃掉非零退出碼,曾因此把壞掉的樹推上線。
- 多行字串置換用編輯器工具,不要用 heredoc 內嵌 Python(`\n` 會被吃掉,反覆踩過)。
- 新功能請開**獨立模組**,不要再往 `morning_report.py` 疊。
  該檔已超過 **26,000 行**(確切上限看 `tests/test_module_size_freeze.py` 的
  `MAIN_MODULE_LINE_CEILING` —— 這裡不再手抄數字:全案審查 2026-09-03 DC-4 發現
  這句手抄的行數又過期了,而它自己舉的例子正是「文件比實況少 7,000 行」;
  餘裕已從 600 收到 100):
  **新功能一律開模組**;bug fix 需要幾行就調幾行(理由寫進 diff),
  抽出之後必須往下調。這行先前寫「約 19,000 行」——**文件比實況少了
  7,000 行**,而那正是這條規則沒有被執行的證據(2026-09-03 架構外審 P1)。
- 外部來源必須**實際 HTTP 請求驗證**後才寫進程式,不可憑文件或第三方轉述;
  URL 尤其不可憑印象寫(曾寫出不存在的政府頁面 id)。
- 測試若用 `hasattr` 守衛去 patch,名字打錯會被**靜默跳過**而讓測試偷偷連網;
  要切斷網路請直接 patch `_http_get` / `requests.get`。

## 審查重點(給外部審查代理)

優先找這幾類,它們在本專案反覆出現:
- **同一條防線只裝一半**(熔斷只在 A 沒在 B、消毒只在主線沒在回流路徑)
- **靜默失效**(不拋例外、不讓測試變紅,只是讓輸出悄悄變差)
- **測試固化了錯誤行為**(測試與程式由同一輪產生,編碼的是實作現況而非規格)
- **前視偏誤**(用當日或未來資訊回測過去)

## state/ 的 diff 被刻意抑制

`.gitattributes` 把 `state/` 標成 `-diff`,`git diff` 只印
「Binary files differ」而非整份 JSON。**檔案本身照常版本化、照常可還原。**
需要看內容時用 `git show <ref>:<path>` 或 `git diff --text -- state/...`。

理由:近 30 天 390 個 commit 有 124 個是純 state 更新(32%),而外部審查代理是
自己跑 `git diff` 讀 repo 的——那些噪音會直接吃掉審查的 context。
