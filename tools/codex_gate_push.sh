#!/usr/bin/env bash
# tools/codex_gate_push.sh — push 閘門:跑一次外部 review,APPROVE 才 push。
#
# 用法:
#   bash tools/codex_gate_push.sh [task-context-file]
#   CODEX_GATE_MODE=deep bash tools/codex_gate_push.sh ctx.txt     # 僅限高風險(見下)
#
# 政策(不得在本檔繞過):
#   * 本檔不自行組 reviewer prompt、不自行取 diff、不硬編 reasoning effort、
#     不呼叫 MCP、不使用 --skip-git-repo-check。一律委派 tools/codex_review.sh。
#   * 一般 push 的第一輪使用 `targeted` + medium。
#   * `deep`(high)必須由明確風險分類觸發:auth / 授權 / secrets / 金流 /
#     DB migration / 破壞性資料操作 / 併發 / 冪等 / 產線事故 / 資料完整性 / 大型跨模組重構。
#   * 收到 REQUEST_CHANGES 後由 Claude Code 逐項驗證 findings,只修 CONFIRMED;
#     每次修正後都必須用 `tools/codex_review.sh resume <session-id>` 沿用同一 session,
#     直到 APPROVE,不得另開 session 規避上下文。
#
# 前置:呼叫者(Claude Code)必須已完成 ruff / py_compile / pytest,並把摘要放進
#       CODEX_REVIEW_VERIFICATION 環境變數,或寫進 task-context 檔。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

BASE="${CODEX_GATE_BASE:-origin/main}"
MODE="${CODEX_GATE_MODE:-targeted}"
CTX="${1:-}"

case "$MODE" in
  targeted) ;;
  deep) echo "[gate] 注意:deep(high effort)已啟用 —— 僅限高風險變更。" ;;
  *) echo "[gate] ERROR: CODEX_GATE_MODE 只允許 targeted 或 deep(收到 '$MODE')。" >&2; exit 64 ;;
esac

git fetch origin --quiet 2>/dev/null || true
PENDING="$(git log --oneline "$BASE"..HEAD 2>/dev/null)"
if [ -z "$PENDING" ]; then echo "[gate] 無待推 commit,結束。"; exit 0; fi
echo "[gate] 待推 commit(base=$BASE):"; echo "$PENDING"; echo

echo "[gate] 委派 review → tools/codex_review.sh $MODE $BASE ${CTX:-(no context)}"
if [ -n "$CTX" ]; then
  bash "$(dirname "$0")/codex_review.sh" "$MODE" "$BASE" "$CTX"
else
  bash "$(dirname "$0")/codex_review.sh" "$MODE" "$BASE"
fi
RC=$?

case "$RC" in
  0)
    echo "[gate] >>> APPROVE:push 中…"
    if git push; then echo "[gate] >>> 已 push 到 $BASE。"; else echo "[gate] >>> push 失敗" >&2; exit 3; fi
    ;;
  2)
    echo "[gate] >>> REQUEST_CHANGES:不 push。" >&2
    echo "[gate]     下一步由 Claude Code 逐項驗證 findings(CONFIRMED / REJECTED / UNCERTAIN)," >&2
    echo "[gate]     只修 CONFIRMED;若修正涉及 P0/P1/material P2,沿用同一 session 繼續審到 APPROVE:" >&2
    echo "[gate]     bash tools/codex_review.sh resume \$(cat .codex-review/last_session_id)" >&2
    exit 2
    ;;
  4) echo "[gate] >>> Codex 限流,結果不可信:不 push。稍後重跑。" >&2; exit 4 ;;
  64) echo "[gate] >>> wrapper 參數錯誤:不 push。" >&2; exit 64 ;;
  *) echo "[gate] >>> 未取得明確 APPROVE:保守起見不 push(見 .codex-review/last_message.txt)。" >&2; exit 5 ;;
esac
