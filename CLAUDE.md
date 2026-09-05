# 晨報系統(-morning-report-main)— 常駐工作規則

> 本檔每個 session 自動載入。地圖:`OPTIMIZATION_PLAN.md`(路線圖+進度);
> 重構施工圖:`A5_MODULARIZATION_MAP.md`;檢核工具:`tools/refactor_audit.py`。
> 這三份多為 untracked(reset --hard 不會刪)。**以文件為地圖、以程式碼為準**——
> 文件描述可能過期,動手前先讀碼確認仍成立。回覆一律繁體中文。

## 0. 這是正式營運系統
每天 06:00–07:00 GitHub Actions 產出晨報 email(另有獨立股癌雷達信,schedule 停用中)。
**晨報不可斷**:所有抓取/解析 try + graceful degrade;寧可少一塊資料,不可整封信失敗。

## 1. 開工儀式(每次固定)
1. 先 `git status --short`,再 `git fetch`。若已有任何本機 diff,必須保留並先走
   Claude Opus 5 review;**不得**用 `git reset --hard`、checkout 或 clean 把尚未審查的 diff 消掉。
   只有使用者明確指定要放棄的精確變更才可另行處理。
2. `python -m pytest -q` 確認基準全綠(2026-07-04 基準:**476 passed**;之後以
   OPTIMIZATION_PLAN.md 頂部進度區記載的最新數為準——**只能增、不能減**)。
3. 讀 OPTIMIZATION_PLAN.md 頂部進度區,確認要做的事沒被做過、沒被否決過。

## 2. 驗收流水線(每個 commit)
ruff → `python -m py_compile <改過的檔>` → `python -m pytest`(全套)→
動渲染則 DRY_RUN 預覽 → **Claude Opus 5 diff 閘門** → **Codex 推送閘門** → push。
一主題一 commit;繁中 commit message;結尾 `Co-Authored-By: Claude <當前模型名> <noreply@anthropic.com>`。

### Claude Opus 5 mandatory diff review

**所有 diff 都必須審,包括 docs-only、comment-only、tests-only 與 cosmetic。** 在宣告完成、
commit 或 push 前執行 `python tools/claude_diff_review.py worktree`。額度不足時可先推送已驗證
變更並標記 `Claude-Opus-5-Review: pending`,未審不得聲稱 `APPROVE`。
wrapper 固定完整模型 ID `claude-opus-5`、effort `high`、唯讀工具白名單;**不得 fallback**
到 alias、Sonnet、舊 Opus 或其他模型。未登入、無法證明實際模型、或沒有明確裁決時
一律 fail closed,回報使用者,不得把「review 沒跑成」解讀為通過。

若因額度／限流未取得裁決,wrapper 會把該 diff fingerprint 以 `PENDING` 寫入被 Git 忽略的
`.claude-review/pending_review.json`;代理必須依 CLI 回報的 reset 時間,在同一 task 建立或更新
自動補審排程。補審仍固定 `claude-opus-5` + effort `high`,不得降級。使用者 2026-09-05
明確允許額度不足時先 commit/push 並標記未審。以 `pending` 子命令列出尚未補審 SHA,
逐筆執行 `commit --commit <SHA>`;通過後用空 audit commit 記錄 `passed` 與
`Claude-Opus-5-Reviewed-Commit: <SHA>`。不得改寫已 push 的歷史,詳見 AGENTS.md。

Git 的 `.githooks/pre-commit` / `pre-push` 會再次執行 gate;不得使用 `--no-verify`、改掉
`core.hooksPath` 或以任何方式繞過。安裝/修復指令:
`powershell -NoProfile -ExecutionPolicy Bypass -File tools/install_claude_review_hooks.ps1`。
修正任何 confirmed finding 後 diff 已改變,必須重跑。本閘門是額外的 Claude 第二意見;
下方既有 Codex review 對 non-trivial 變更仍必須執行。

## External Codex review policy

完成 non-trivial 實作與本機驗證之後:

1. 使用本 repo 專用的 Codex review wrapper(`tools/codex_review.sh` / `.ps1`);不得直接呼叫
   `codex exec`,也不得透過 Codex MCP(user-scope `codex` MCP 已於 2026-07-10 移除)。
2. 一般實作用 `targeted` + GPT-5.6 Sol medium。
3. 只有 authentication、authorization、payments、破壞性 DB 變更、concurrency、產線事故、
   資料完整性風險、大型跨模組 refactor 才用 `deep` + GPT-5.6 Sol high。
4. 不得把完整 diff 貼進 Codex prompt(Codex 自行在 repo 內跑 git)。
5. Codex 必須 read-only,且關閉 web search 與 apps。
6. 獨立驗證每一項 Codex finding(CONFIRMED / REJECTED / UNCERTAIN)。
7. 只修 CONFIRMED 的 finding。
8. **迭代到 APPROVE 為止,無輪數上限**(使用者定案 2026-07-13,取代舊「最多兩輪」;
   與 `tools/codex_review.sh` 檔頭一致 —— 全案審查 2026-09-03 DC-1 發現這裡還停在舊政策,
   照字面執行會在 REQUEST_CHANGES 未收斂時提前 push)。
9. 每一輪都 `resume` 同一個 session,不得重建;每次 resume 都必須跟在真正 CONFIRMED 的修正之後,
   REJECTED 的 finding 附證據說明,不為它再跑一輪。
10. documentation-only、comment-only、typo-only、tests-only、純 cosmetic 變更跳過外部 review。

細節見 `.claude/skills/gpt-review/SKILL.md`。**APPROVE 才 push。** 歷史上 Codex 擋下多個真 bug
(state 檔漏登錄、stale FX 誤入計分、`float('nan')` 不拋例外、podcast 集數餓死、2026 世足第 3 名
仍可晉級)——不要跳過。**模型 gpt-5.6-sol 需 alpha CLI ≥0.145.0-alpha.2**;若不可用,**先問使用者**,
勿默默降級。

## 3. 禁改清單(違反即失敗)
- **計分/權重/預測係數**(ranking_score、smart_money、radar_score、us_beta 0.31、模型特徵、
  Top5 熔斷):須「回測證據(IC |t|>2、方向正確、bt_top5 複驗)+ 使用者同意」兩者兼備。顯示/門檻不在此限。
- 信件**區塊不裁**;**Top5 不前移**(使用者已婉拒,不要再提)。
- gooaye-radar.yml schedule 維持停用(使用者 2026-06-22 停)。
- **隱私**:PORTFOLIO 持股代號/股數不得進 HTML、LLM prompt、state、log。
- render 函式**不做網路呼叫**;新增 GET 一律走 `_http_get`(morning_report 內)或該模組自己的
  mini-retry helper(podcast_digest/fz_score 已各有一份,理由見其 docstring),勿寫裸 `requests.get`。
- 禁一次性大爆改;被測試 monkeypatch 的名稱不可無聲搬家(先跑 `python tools/refactor_audit.py nomove`)。

## 4. 結構鐵律
- **import 方向**:gooaye_radar → import morning_report(用 mr._http_get);morning_report →
  函式內 lazy import fz_score(反向會循環);podcast_digest 獨立(勿 import mr,拖入 15k 行+pandas)。
- **搬函式標準手法**:新模組 + morning_report 同名 re-export(`from X import (...)`),tests 零修改仍綠、
  函式本體逐字不改;完整程序照 A5_MODULARIZATION_MAP.md §A 一步不跳。
- 新模組**不得** import morning_report(循環);依賴 mr 符號的函式=不可搬(誠實少搬,不硬搬)。

## 5. 常見地雷(每條都真踩過)
1. 新增 state 檔必須加進 `_git_commit_and_push_state` 的**明確檔名清單**(已踩兩次:N4、V2-N1)。
2. `_http_get` 內部呼叫 `requests.get`(tests patch `mr.requests.get` 仍攔得到);status 判斷用
   `getattr(r, "status_code", 200)` 相容 fake——改它前先讀 tests。
3. TWSE OpenAPI 可能回非 list 的 JSON → 一律 `isinstance` 防禦再迭代。
4. yfinance 在本機被 geo-block(Actions 上正常)→ 本機跑不動 ≠ bug;也因此**本機無法完整跑 pipeline**。
5. Gmail ~102KB 剪信:量的是**解碼後 HTML 大小**,勿再乘 1.37;低優先區塊放信末,被剪先剪它們。
6. 金融股判定:看產業代碼 `"17"` 或產業/描述關鍵字,不能只看中文標籤。
7. LLM 事件抽取重試:只有「解析出非空但全不合格」才重試一次;**合法空結果不重試**(有測試釘死)。
8. 股癌 podcast 週三/六下午發;radar/podcast 收錄少通常是新鮮度/排程,不是 bug,先查 digest 時間戳。

## 6. 升級與求援(能力極限的誠實處理)
- **可自己做**:機械性重構(照施工圖+audit 工具)、修 bug、加測試、文件。
- **要第二意見**:設計取捨、分析結論 → 併行問 Codex GPT-5.5(MCP 或 CLI),整合兩者觀點。
- **必須先問使用者**:改 main() 控制流、任何輸出行為變化、計分相關、刪任何東西、新資料源、
  以及所有規格沒寫到的模糊題/品味題。**不確定就問,查不到就標註,不要編造。**
- **本機 harness 極限**:需要「跑一次完整 pipeline 才能驗證」的改動(如 main() 拆分),
  只能靠 CI dry-run artifact 對比;做不到就明說並擱置,不要硬上。
- 本檔與計劃文件目前 untracked;**未經使用者同意不要 commit 它們**。
