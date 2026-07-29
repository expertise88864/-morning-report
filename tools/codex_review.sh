#!/usr/bin/env bash
# tools/codex_review.sh — 本 repo 唯一的外部 code-review 入口(Codex CLI, read-only)。
#
# 用法:
#   tools/codex_review.sh <mode> <base-ref> [task-context-file]
#   tools/codex_review.sh resume [session-id]
#
# mode:
#   diff      低風險/局部(文案、註解、CSS、tests-only)     medium / 額外檔 3 / findings 3
#   targeted  一般非 trivial 實作(預設)                     medium / 額外檔 12 / findings 5
#   deep      auth、金流、DB migration、併發、資安、大重構    high   / 額外檔 30 / findings 8
#   resume    第二輪(僅限 confirmed P0/P1/material P2 修正後);沿用第一輪 session
#
# 設計原則(勿改):
#   * 絕不把完整 diff 放進 prompt 或 argv;Codex 在 repo 內自行跑 git。
#   * --ignore-user-config 隔離 ~/.codex/config.toml(不載 plugins/apps/browser/notify/node_repl)。
#   * --sandbox read-only:Codex 不得寫檔、commit、跑 tests/build/lint/probe。
#   * 迭代到 APPROVE 為止,無輪數上限(使用者定案 2026-07-13,取代舊「最多兩輪」);
#     每一輪都必須 resume 同一 session,不得重建。
#   * 結果只讀「最後一則訊息」(-o),不掃整份輸出 —— 否則 prompt 回顯裡的
#     "APPROVE or REQUEST_CHANGES" 會被誤判(舊 gate script 的真實 bug)。
#
# 環境變數(選用):
#   CODEX_REVIEW_VERIFICATION  本機驗證結果摘要(單/多行字串),會填入 prompt。
#   CODEX_REVIEW_HARDEN=0      關閉 web_search/apps 的額外 -c 硬化(若該 CLI 版本不認這些鍵)。
#   CODEX_REVIEW_STRICT=0      關閉 --strict-config。
set -uo pipefail

MODEL="gpt-5.6-sol"
HARDEN="${CODEX_REVIEW_HARDEN:-1}"
STRICT="${CODEX_REVIEW_STRICT:-1}"

die() { echo "[codex-review] ERROR: $*" >&2; exit 64; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)" \
  || die "必須在 git repository 內執行。"
REPO_NAME="$(basename "$REPO_ROOT")"
STATE_DIR="$REPO_ROOT/.codex-review"
mkdir -p "$STATE_DIR"
USAGE_TSV="$STATE_DIR/usage.tsv"
SESSION_FILE="$STATE_DIR/last_session_id"
PASS_FILE="$STATE_DIR/last_pass"
LAST_MSG="$STATE_DIR/last_message.txt"
RAW_LOG="$STATE_DIR/last_raw.log"

[ -s "$USAGE_TSV" ] || printf 'timestamp\trepository\tmode\tmodel\teffort\tbase_ref\tsession_id\ttokens_used\tresult\tfindings\tpass\n' > "$USAGE_TSV"

MODE="${1:-}"
[ -n "$MODE" ] || die "缺少 mode。用法: $0 <diff|targeted|deep> <base-ref> [task-context-file] | $0 resume [session-id]"

case "$MODE" in
  diff)     EFFORT="medium"; EXTRA_FILE_LIMIT=3;  FINDING_LIMIT=3 ;;
  targeted) EFFORT="medium"; EXTRA_FILE_LIMIT=12; FINDING_LIMIT=5 ;;
  deep)     EFFORT="high";   EXTRA_FILE_LIMIT=30; FINDING_LIMIT=8 ;;
  resume)   EFFORT="";       EXTRA_FILE_LIMIT="";  FINDING_LIMIT="" ;;
  *) die "未知 mode '$MODE'(可用:diff | targeted | deep | resume)" ;;
esac

# ---------- codex 旗標(read-only + 隔離) ----------
build_flags() {   # $1 = effort ; $2 = kind("exec" | "resume",預設 exec)
  # 刻意用不含內層引號的 -c key=value:codex 對 value 先試 TOML,失敗即當字面字串
  # (bare `medium`/`disabled` → 字串;`false` → 布林)。跨 bash/PowerShell quoting 最穩。
  FLAGS=(--ignore-user-config --model "$MODEL" -c "model_reasoning_effort=$1" -o "$LAST_MSG")
  if [ "${2:-exec}" = "resume" ]; then
    # `codex exec resume` 旗標集較小:不接受 --sandbox 也不接受 --cd。
    # sandbox 改用 config 鍵 sandbox_mode 覆寫(policy 軸,值 read-only);
    # 工作目錄改由呼叫端 cd 進 $REPO_ROOT 解決。
    FLAGS+=(-c "sandbox_mode=read-only")
  else
    FLAGS+=(--sandbox read-only --cd "$REPO_ROOT")
  fi
  [ "$STRICT" = "1" ] && FLAGS+=(--strict-config)
  if [ "$HARDEN" = "1" ]; then
    FLAGS+=(-c "web_search=disabled" -c "features.apps=false")
  fi
  # 明確不使用:--ask-for-approval(此 CLI 的 exec 無此旗標,非互動預設即 never)、
  #             --skip-git-repo-check、--ephemeral(第二輪要 resume)、--dangerously-*。
}

# ---------- 解析輸出 ----------
extract_session_id() { grep -oiE 'session id:[[:space:]]*[0-9a-f-]{36}' "$RAW_LOG" 2>/dev/null | head -1 | grep -oiE '[0-9a-f-]{36}' || true; }
# 取「最後一次」tokens used 之後的數字:codex 的 token footer 在輸出結尾;取第一次會被
# transcript 引用的程式碼污染(審查本 wrapper 時,它自己就含 "tokens used" 字樣,曾把
# 38,904 記成 83 —— 與限流誤判同類的 raw-log 掃描污染,2026-07-12 自查)。
extract_tokens() { awk 'tolower($0) ~ /tokens used/ {want=1; next} want && $0 ~ /[0-9]/ {gsub(/[^0-9]/,"",$0); if (length($0)) val=$0; want=0} END {if (length(val)) print val}' "$RAW_LOG" 2>/dev/null || true; }
extract_result() {
  # 只認「最後一個非空白行」的精確裁決(prompt 契約:End with exactly APPROVE / REQUEST_CHANGES;
  # 容忍 **APPROVE** 這類 markdown 包裹)。舊版 grep 全文找字:一句「I cannot APPROVE …」的散文
  # 就會被誤判成核准 → gate 誤 push 上產線(Codex review P1)。非精確裁決一律 UNKNOWN(exit 5,人工看)。
  [ -s "$LAST_MSG" ] || { echo "UNKNOWN"; return; }
  local line
  # 取最後一個非空白行,只去除前後「空白」,再對三種明確形式做精確匹配:
  # 裸字 / 平衡 **粗體** / 平衡 `反引號`。不泛剝 * `——那會把 "* APPROVE"(項目符號)、
  # "APP**ROVE"(內部) 誤判為核准(Codex re-review 兩輪 P1)。非以上形式一律 UNKNOWN。
  line="$(grep -vE '^[[:space:]]*$' "$LAST_MSG" | tail -1 | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  case "$line" in
    APPROVE|'**APPROVE**'|'`APPROVE`')                         echo "APPROVE" ;;
    REQUEST_CHANGES|'**REQUEST_CHANGES**'|'`REQUEST_CHANGES`') echo "REQUEST_CHANGES" ;;
    *)                                                          echo "UNKNOWN" ;;
  esac
}
extract_findings() {
  [ -s "$LAST_MSG" ] || { echo "unavailable"; return; }
  local n; n="$(grep -ciE '^[[:space:]]*[-*]?[[:space:]]*severity:' "$LAST_MSG" || true)"
  if grep -q 'NO_ACTIONABLE_FINDINGS' "$LAST_MSG"; then echo 0
  elif [ -n "$n" ] && [ "$n" -gt 0 ] 2>/dev/null; then echo "$n"
  else echo "unavailable"; fi
}
is_rate_limited() { grep -qiE 'usage limit|rate limit|try again at' "$RAW_LOG" 2>/dev/null; }

log_usage() {  # $1 mode $2 effort $3 base $4 pass
  local sid tok res fnd
  sid="$(extract_session_id)"; [ -n "$sid" ] || sid="unavailable"
  tok="$(extract_tokens)";     [ -n "$tok" ] || tok="unavailable"
  res="$(extract_result)"
  fnd="$(extract_findings)"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$REPO_NAME" "$1" "$MODEL" "$2" "$3" "$sid" "$tok" "$res" "$fnd" "$4" >> "$USAGE_TSV"
  [ "$sid" = "unavailable" ] || printf '%s' "$sid" > "$SESSION_FILE"
  echo "$res"
}

# ================= resume(第二輪) =================
if [ "$MODE" = "resume" ]; then
  SID="${2:-}"
  if [ -z "$SID" ]; then
    [ -s "$SESSION_FILE" ] || die "找不到第一輪 session id($SESSION_FILE 不存在)。請以明確 session id 執行:$0 resume <session-id>。不要用 --last(可能 resume 到別的專案)。"
    SID="$(cat "$SESSION_FILE")"
  fi
  # session id 必須是完整 UUID。少了這道守衛時,把別的東西(例如 task-context
  # 檔路徑)誤傳進來會**靜默開一個全新 session**,而輸出看起來與正常 resume
  # 完全相同 —— 等於在不知情的情況下違反「每輪必須 resume 同一 session」。
  #
  # **實測驗證**:`codex exec resume dead-beef-cafe-babe-not-a-uuid` 不會報錯,
  # 它開了一個全新 session(019faec7…,與被指定的 019faeb3… 無關)並照常產出
  # 一份 review。所以這裡必須是錨定的 8-4-4-4-12 十六進位比對,
  # 不能用寬鬆 glob(第一版寫 `[0-9a-fA-F]*-*-*-*-*`,上面那個字串照樣通過)。
  if ! printf '%s' "$SID" | grep -Eq \
      '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'; then
    die "session id 格式不正確:'$SID'(需為 UUID)。用法:$0 resume [session-id];省略時會讀 $SESSION_FILE。不要把 task-context 檔傳到這個位置。"
  fi
  PREV_PASS="$(cat "$PASS_FILE" 2>/dev/null || echo 0)"
  case "$PREV_PASS" in (''|*[!0-9]*) PREV_PASS=0 ;; esac
  # 2026-07-28:舊版硬性 `[ "$PREV_PASS" = "1" ]`,即「每個 task 最多兩輪」——
  # 那與 CLAUDE.md 現行「迭代到 APPROVE 為止、無輪數上限」(使用者 2026-07-13 定案)
  # 衝突。實務上只能靠**開新 session 繞過**,而那會丟掉前一輪的上下文、
  # 讓審查者重新探索整個 repo(更貴,而且會重複回報已修的東西)。
  # 改為只要求「至少完成過第一輪」。
  [ "$PREV_PASS" -ge 1 ] || die "續審只能在完成第一輪之後執行(目前 pass=$PREV_PASS)。"
  THIS_PASS=$((PREV_PASS + 1))
  # 軟性提醒:每一輪都要花 token,而且「把審查者跑到綠燈」不是目的。
  # 若某條 finding 經證據判定為 REJECTED,應該據理說明而不是再跑一輪。
  if [ "$THIS_PASS" -ge 6 ]; then
    echo "[codex-review] 提醒:這是第 $THIS_PASS 輪。每輪約 0.1-0.2M tokens;" >&2
    echo "               若剩下的 finding 已是邊角推測,請用判斷收尾,不要迴圈到綠燈。" >&2
  fi

  # 第二輪的 effort 沿用第一輪(從 usage.tsv 最後一筆讀回),預設 medium。
  RESUME_EFFORT="$(tail -1 "$USAGE_TSV" | cut -f5)"; [ -n "$RESUME_EFFORT" ] || RESUME_EFFORT="medium"
  RESUME_BASE="$(tail -1 "$USAGE_TSV" | cut -f6)";   [ -n "$RESUME_BASE" ] || RESUME_BASE="unavailable"
  build_flags "$RESUME_EFFORT" resume

  # 舊版寫死 "Second and final review pass" —— 那會誘導審查者收尾,
  # 而現在的規則是跑到 APPROVE 為止。改成標明實際輪次、不宣稱是最後一輪。
  read -r -d '' RESUME_PROMPT <<RP || true
Follow-up review pass #$THIS_PASS. Inspect only the corrections made for CONFIRMED
findings from the previous review. Verify that those defects are resolved and
that the corrections introduced no concrete regression. Do not repeat the
original full repository exploration. Remain strictly read-only: do not modify
files, run tests, builds, linters, package managers, application code, or ad hoc
probes, and do not use web search, browser, apps, connectors, or external MCP
tools. End with exactly APPROVE or REQUEST_CHANGES.
RP

  echo "[codex-review] resume session=$SID effort=$RESUME_EFFORT (pass $THIS_PASS)"
  : > "$LAST_MSG"
  # resume 無 --cd:改在 subshell 內 cd 進 repo,讓 Codex 在正確目錄跑 git。
  ( cd "$REPO_ROOT" && codex exec resume "$SID" "${FLAGS[@]}" "$RESUME_PROMPT" ) 2>&1 | tee "$RAW_LOG"
  CODEX_RC=${PIPESTATUS[0]}
  # codex 啟動失敗(參數錯誤等)且完全沒產生 review → 不消耗額度、不計為第二輪。
  if [ "$CODEX_RC" -ne 0 ] && [ ! -s "$LAST_MSG" ]; then
    die "codex exec resume 啟動失敗(exit=$CODEX_RC),未產生任何 review;不計為一輪(pass 仍為 $PREV_PASS,修正後可再試)。"
  fi
  echo "$THIS_PASS" > "$PASS_FILE"
  RESULT="$(log_usage "resume" "$RESUME_EFFORT" "$RESUME_BASE" "$THIS_PASS")"
  echo; echo "[codex-review] result=$RESULT (pass $THIS_PASS)"
  # 限流只在「沒有取得明確裁決」時才有意義:transcript 內文提到 rate limit 字樣
  # (例如 review 對象本身在討論限流)不可推翻已完成的裁決(2026-07-12 實際誤判過)。
  case "$RESULT" in
    APPROVE) exit 0 ;;
    REQUEST_CHANGES) exit 2 ;;
    *)
      if is_rate_limited; then echo "[codex-review] Codex 限流,結果不可信。" >&2; exit 4; fi
      exit 5 ;;
  esac
fi

# ================= 第一輪 =================
BASE="${2:-}"
[ -n "$BASE" ] || die "缺少 base-ref。例:$0 $MODE origin/main [task-context-file]"
git -C "$REPO_ROOT" rev-parse --verify --quiet "${BASE}^{commit}" >/dev/null \
  || die "base-ref '$BASE' 不存在或無法解析為 commit。請提供有效的 base(如 origin/main)。"

CTX_FILE="${3:-}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
if [ -n "$CTX_FILE" ]; then
  [ -f "$CTX_FILE" ] || die "task-context 檔不存在:$CTX_FILE"
  if grep -qE '^(diff --git |@@ |index [0-9a-f]+\.\.)' "$CTX_FILE"; then
    die "task-context 檔看起來含有 diff。task-context 只能放任務摘要/驗收標準/預期行為/non-goals/本機測試結果/已知限制。"
  fi
  cp "$CTX_FILE" "$TMP/ctx.txt"
else
  printf '(no task context supplied)\n' > "$TMP/ctx.txt"
fi
if [ -n "${CODEX_REVIEW_VERIFICATION:-}" ]; then
  # verification 同樣會進 prompt:套用與 task-context 相同的 diff 攔截(prompt 絕不含 diff 的
  # 不變式必須涵蓋「每一條」入 prompt 的路徑,不能只守 ctx 檔;Codex re-review P1)。
  if printf '%s\n' "$CODEX_REVIEW_VERIFICATION" | grep -qE '^(diff --git |@@ |index [0-9a-f]+\.\.)'; then
    die "CODEX_REVIEW_VERIFICATION 看起來含有 diff。驗證摘要只放本機 lint/type/test/build 結果,不得貼 diff。"
  fi
  printf '%s\n' "$CODEX_REVIEW_VERIFICATION" > "$TMP/ver.txt"
else printf '(not supplied by caller)\n' > "$TMP/ver.txt"; fi

# 靜態 prompt(quoted heredoc:反引號/大括號皆為字面值,絕不含 diff)
cat > "$TMP/prompt.txt" <<'PROMPT'
You are an independent senior software engineer performing a read-only code
review of an implementation written by another coding model.

REVIEW MODE:
{{MODE}}

REVIEW BASE:
{{BASE}}

TASK CONTEXT:
{{TASK_CONTEXT}}

LOCAL VERIFICATION:
{{VERIFICATION_RESULTS}}

Operate strictly in read-only mode.

Do not modify, create, delete, rename, format, stage, commit, revert, or patch
any file.

Do not install dependencies.

Do not run tests, builds, linters, formatters, package managers, migrations,
application code, or ad hoc Python/Node probes.

Do not use web search, browser, computer use, apps, connectors, plugins, or
external MCP tools.

Start from the repository's actual Git state:

1. Inspect `git status --short`.
2. Inspect staged and unstaged changes.
3. When a valid base ref is supplied, calculate the merge base and inspect the
   branch diff against it.
4. Include relevant untracked source files explicitly.
5. Identify the exact runtime behavior changed by the implementation.

Start from the diff, but do not limit the review to changed lines when directly
related repository context is required.

Repository exploration must be driven by a concrete concern raised by the
diff.

Prioritize:

1. Direct callers and downstream consumers.
2. Referenced interfaces, schemas, shared types, and contracts.
3. Tests directly related to the changed behavior.
4. One analogous implementation when required.

Do not perform a whole-repository audit.

Unless a concrete P0 or P1 risk requires expansion:

- inspect no more than {{EXTRA_FILE_LIMIT}} additional files outside the diff
- report no more than {{FINDING_LIMIT}} findings
- do not inspect unrelated directories
- do not inspect generated files, vendored code, build output, caches, or
  dependency directories
- stop when no high-confidence actionable failure path remains

Report only concrete defects involving:

- incorrect behavior
- regression
- security or authorization
- data integrity
- compatibility
- concurrency or idempotency
- resource leaks
- material error-handling failures
- realistic performance pathologies

Do not report:

- style preferences
- naming preferences
- formatting
- optional refactors
- generic best practices
- speculative concerns
- pre-existing unrelated problems
- missing comments
- duplicated findings
- issues already prevented by existing validation or contracts

Every finding must include:

- severity: P0, P1, P2, or P3
- confidence: high, medium, or low
- exact file and smallest useful line range
- concrete trigger
- observable failure
- repository evidence
- why existing tests do not detect it
- minimal correction direction

If no qualifying defect is found, output:

NO_ACTIONABLE_FINDINGS

End with exactly one of:

APPROVE
REQUEST_CHANGES
PROMPT

# 以 sed 注入。s||| 置換只用於 MODE(白名單 case)與上限(數字字面值)。
# BASE 是使用者可控值:git 允許 ref 名含 | & +,rev 語法還有 @{...} 帶空白——
# 一律走與 TASK_CONTEXT 相同的 r/d 檔案注入(內容逐位元組照放,無 sed replacement 語意;
# Codex review P2 兩輪:跳脫在 GNU sed 上實測不可靠、字元白名單又誤拒合法 ref,r/d 才是正解;
# 亦與 .ps1 的字面 .Replace() 行為對齊)。置換失敗即中止,不帶壞 prompt 呼叫 codex。
printf '%s\n' "$BASE" > "$TMP/base.txt"
sed -i -e "s|{{MODE}}|$MODE|" \
       -e "s|{{EXTRA_FILE_LIMIT}}|$EXTRA_FILE_LIMIT|" -e "s|{{FINDING_LIMIT}}|$FINDING_LIMIT|" "$TMP/prompt.txt" \
  || die "prompt 構建失敗(sed 置換錯誤)"
sed -i -e "/{{BASE}}/r $TMP/base.txt" -e "/{{BASE}}/d" "$TMP/prompt.txt"
sed -i -e "/{{TASK_CONTEXT}}/r $TMP/ctx.txt" -e "/{{TASK_CONTEXT}}/d" "$TMP/prompt.txt"
sed -i -e "/{{VERIFICATION_RESULTS}}/r $TMP/ver.txt" -e "/{{VERIFICATION_RESULTS}}/d" "$TMP/prompt.txt"

build_flags "$EFFORT"
echo "[codex-review] mode=$MODE effort=$EFFORT base=$BASE model=$MODEL (pass 1, read-only, user-config ignored)"
: > "$LAST_MSG"
codex exec "${FLAGS[@]}" "$(cat "$TMP/prompt.txt")" 2>&1 | tee "$RAW_LOG"
CODEX_RC=${PIPESTATUS[0]}
# codex 啟動失敗(參數錯誤等)且完全沒產生 review → 不消耗額度、不記為一輪、不寫 pass。
if [ "$CODEX_RC" -ne 0 ] && [ ! -s "$LAST_MSG" ]; then
  die "codex exec 啟動失敗(exit=$CODEX_RC),未產生任何 review。請檢查 CLI 版本與旗標。"
fi

echo 1 > "$PASS_FILE"
RESULT="$(log_usage "$MODE" "$EFFORT" "$BASE" 1)"
echo; echo "[codex-review] result=$RESULT (pass 1)  usage → $USAGE_TSV"
# 限流只在「沒有取得明確裁決」時才有意義(理由同 resume 區塊;曾實際誤判)。
case "$RESULT" in
  APPROVE) exit 0 ;;
  REQUEST_CHANGES) exit 2 ;;
  *)
    if is_rate_limited; then echo "[codex-review] Codex 限流,結果不可信,勿據此 push。" >&2; exit 4; fi
    echo "[codex-review] 未取得明確 APPROVE/REQUEST_CHANGES;請人工檢視 $LAST_MSG" >&2; exit 5 ;;
esac
