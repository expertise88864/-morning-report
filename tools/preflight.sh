#!/usr/bin/env bash
# push 之前的本機閘門:**跑與 CI 完全相同的檢查**(使用者定案 2026-08-01)。
#
# 為什麼要有這個檔:2026-08-01 我用 `ruff check . | tail -1` 判定 lint,
# 看到最後一行 `No fixes available (1 hidden fix…)` 就當成通過,結果 CI 紅。
# **用 tail 看檢查結果本身就是一個會靜默通過的檢查器。** 這裡一律看 exit code,
# 而且任一步失敗就立刻停(set -e),不讓後面的綠燈蓋掉前面的紅燈。
#
# 這三步與 .github/workflows/ci.yml 的 `test` job 逐字對應,
# 由 tests/test_workflow_contract.py 的契約測試擋住漂移 ——
# CI 加了新檢查而這裡沒跟上,那條測試會紅。
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[preflight] 1/3 語法檢查"
python -m compileall -q . -x '(\.git|\.venv|__pycache__)'

echo "[preflight] 2/3 Lint"
python -m ruff check .

echo "[preflight] 3/3 單元測試"
python -m pytest -q

echo "[preflight] ✅ 與 CI 相同的三項檢查全部通過"
