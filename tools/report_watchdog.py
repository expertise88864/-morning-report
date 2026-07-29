# -*- coding: utf-8 -*-
"""晨報看門狗:07:00 檢查今天的信到底有沒有產出過。

**為什麼需要它(不是「多一層保險」而已)**:
`.github/workflows/morning-report.yml` 的註解已經寫明殘餘風險——
morning 與 podcast 共用 `state-writers` 這個 concurrency group 且不取消,
一旦某個 run 在 **pending 階段**被第三個 run 擠掉,job 根本不會啟動,
於是**連 workflow 內的告警步驟也不會執行**。那種失敗是完全無聲的:
沒有信、沒有告警、Actions 頁面上只是一個被取消的排隊項目。

所以看門狗必須:
  1. 跑在**不同的 concurrency group**——否則它會排在它要監看的那個 run 後面,
     等到輪到它時,要監看的 run 早就結束了(或它自己也被擠掉)。
  2. 只讀不寫——它不能參與 state 競爭,否則自己變成問題來源。

判定依據是 `state/run_manifest.json` 的 `date`(每次執行都會更新)。
用它而不是 history.json:週日輕量信在沒有新內容時本來就可能不寄,
但 manifest 只要跑過就會更新,不會產生假警報。

回傳碼:0=正常,1=逾時未更新(呼叫端據此寄告警信)。
"""
import datetime as dt
import json
import os
import sys
from pathlib import Path

TPE = dt.timezone(dt.timedelta(hours=8))
MANIFEST = Path("state/run_manifest.json")
#: 排程 22:00 UTC(台北 06:00),Actions 常延遲 5-15 分。07:30 檢查給足緩衝。
MAX_AGE_HOURS = float(os.environ.get("WATCHDOG_MAX_AGE_HOURS", "3"))


def manifest_age_hours(now: dt.datetime, path: Path = MANIFEST):
    """回 (age_hours, 讀到的日期字串)。檔案不存在或無法解析回 (None, 原因)。"""
    if not path.exists():
        return None, "run_manifest.json 不存在"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"run_manifest.json 解析失敗: {e}"
    stamp = str((raw or {}).get("date") or "").strip()
    if not stamp:
        return None, "run_manifest.json 沒有 date 欄位"
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            when = dt.datetime.strptime(stamp, fmt).replace(tzinfo=TPE)
        except ValueError:
            continue
        return (now - when).total_seconds() / 3600.0, stamp
    return None, f"run_manifest.json 的 date 無法解析: {stamp!r}"


def main() -> int:
    now = dt.datetime.now(TPE)
    age, info = manifest_age_hours(now)
    if age is None:
        print(f"[watchdog] 異常:{info}", file=sys.stderr)
        return 1
    if age > MAX_AGE_HOURS:
        print(f"[watchdog] 異常:最後一次執行是 {info}"
              f"({age:.1f} 小時前,上限 {MAX_AGE_HOURS} 小時)"
              "——今天的晨報可能整個沒有跑起來", file=sys.stderr)
        return 1
    print(f"[watchdog] 正常:最後一次執行 {info}({age:.1f} 小時前)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
