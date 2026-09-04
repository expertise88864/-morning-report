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

# **用 repo 自己的 .venv**(2026-09-04):全域的 site-packages 和別的專案
# 共用,版本不會是 lock 釘的那些。用 PATH 前置而不是換掉底下的指令 ——
# 那三條必須與 ci.yml **逐字相同**(test_preflight_runs_exactly_what_ci_gates_on)。
if [ -d .venv/Scripts ]; then export PATH="$PWD/.venv/Scripts:$PATH"; fi
if [ -d .venv/bin ]; then export PATH="$PWD/.venv/bin:$PATH"; fi

echo "[preflight] 0/4 環境是否與 CI 一致"
python tools/env_drift.py

echo "[preflight] 1/4 語法檢查"
python -m compileall -q . -x '(\.git|\.venv|__pycache__)'

echo "[preflight] 2/4 Lint"
python -m ruff check .

echo "[preflight] 3/4 單元測試"
# `--junitxml` 是為了 CI 的 annotation(job log 要 admin 才讀得到)。
# 這裡照抄同一條指令是刻意的:契約是**逐字對應**,放寬成「忽略報告用的
# 旗標」就等於開了一類「CI 有而本機沒有」的缺口。順帶本機失敗時也有
# 同一份 pytest-report.xml 可以看。
python -m pytest -q --junitxml=pytest-report.xml

echo "[preflight] ✅ 與 CI 相同的三項檢查全部通過(環境版本亦已對齊)"
