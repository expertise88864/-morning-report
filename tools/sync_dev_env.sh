#!/usr/bin/env bash
# 把本機開發環境對齊 requirements-dev.lock(2026-09-04)。
#
# **為什麼要有一個 repo 自己的 .venv**:全域的 site-packages 是和別的專案
# 共用的。那天我直接把全域升到 lock 的版本,連帶違反了另外兩個專案的約束
# (ortools 要 protobuf<6.34、opencv 要 numpy<2.3)—— 對齊一個專案不該是
# 弄壞另一個專案的方式。
#
# **為什麼不能直接 `pip install --require-hashes -r requirements-dev.lock`**:
# 那份 lock 是用 `--python-platform linux` 編的,hash 只涵蓋 Linux wheel;
# 在 Windows 上會找不到對應檔案。所以這裡只取 `名字==版本`(版本仍然是
# lock 釘的那一個,少的是 hash 這一層保護 —— 本機開發可以接受,CI 不行)。
set -euo pipefail
cd "$(dirname "$0")/.."

LOCK="requirements-dev.lock"
[ -f "$LOCK" ] || { echo "找不到 $LOCK"; exit 1; }

# **從頭重建**(外審 2026-09-04 r1 P1):沿用既有的 .venv 只會裝上/升級 lock
# 裡的東西,**不會移除已經從 lock 拿掉的**。一個殘留的 pytest plugin 可以自己
# 註冊 marker、改變收集行為 —— 本機因此綠,而 CI 那個從 lock 全新建起來的環境
# 紅。那正是這整套機制要消滅的假綠燈。CI 每次都是全新 runner,本機也該如此。
echo "[sync] 重建 .venv(既有的會被刪掉)"
rm -rf .venv
python -m venv .venv
if [ -x .venv/Scripts/python.exe ]; then
  PY=.venv/Scripts/python.exe
else
  PY=.venv/bin/python
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
# 只取 `名字==版本`;`\` 結尾的 hash 續行一律丟掉
grep -oE '^[A-Za-z0-9._-]+==[^ \\]+' "$LOCK" > "$TMP"
echo "[sync] 安裝 $(wc -l < "$TMP") 個釘住的版本到 .venv"
"$PY" -m pip install -q -r "$TMP"

echo "[sync] 確認"
"$PY" tools/env_drift.py
