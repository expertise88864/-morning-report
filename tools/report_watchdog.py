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

**批#N(2026-08-08):「有跑」與「跑成了」是兩件事。**
2026-08-04 → 08-08 連續五天,特化路徑每天被自己的引用檢查擋下、
退回 legacy;信照樣寄出、manifest 照樣更新 —— 這個看門狗全程安靜,
使用者是把信貼進對話裡才發現的。判準搬到 `run_quality.assess()`
(純函式,吃 manifest);這裡只負責接線與告警文字。

回傳碼:0=正常,1=沒跑起來/沒寄到,2=跑起來了但**跑壞了**
(呼叫端據此寄不同主旨的告警信)。
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


def delivery_state(path: Path = MANIFEST) -> dict:
    """manifest 裡的寄送結果。讀不到或舊格式沒有這個欄位時回 {}。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    d = raw.get("delivery")
    return d if isinstance(d, dict) else {}


def quality_findings(path: Path = MANIFEST) -> list:
    """今天這一班的品質判準(判準本體在 `run_quality`)。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import run_quality as _rq
    return _rq.assess(raw)


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
    # 批#73(第七輪 P2-2):**「有跑過」不等於「有寄到」。**
    # 只看時間戳的話,這些情境會被誤判成正常:
    #   - 05:30 手動跑過、06:00 正式排程在 pending 被擠掉 → 07:30 時 age < 3h
    #   - manifest 更新了,但在寄信那一步失敗
    # 而看門狗存在的理由正是後者。
    delivery = delivery_state()
    if not delivery:
        # 舊格式 manifest 沒有這個欄位。**不當成異常**——那會在部署當天
        # 產生一次確定的假警報,而假警報會訓練人忽略告警。
        print(f"[watchdog] 正常(舊格式 manifest,無寄送欄位):{info}"
              f"({age:.1f} 小時前)")
        return _quality_exit(info)
    if delivery.get("skipped_reason"):
        # 刻意不寄(週日無新內容)。批#69 r2 才剛修掉同型的假警報。
        print(f"[watchdog] 正常:{info} 刻意未寄信"
              f"({delivery.get('skipped_reason')})")
        return 0        # 刻意不寄的日子沒有「信的品質」可談
    if not delivery.get("success"):
        print(f"[watchdog] 異常:{info} 有執行但**沒有成功寄出**"
              f"(attempted={delivery.get('attempted')}、"
              f"run_kind={delivery.get('run_kind')})", file=sys.stderr)
        return 1
    print(f"[watchdog] 正常:{info} 已寄出({age:.1f} 小時前、"
          f"run_kind={delivery.get('run_kind')})")
    return _quality_exit(info)


def _quality_exit(info: str) -> int:
    """跑起來也寄到了 —— 再問一次「跑成了嗎」。

    **回 2 而不是 1**:呼叫端要能分辨「今天沒有信」與「今天的信比它
    該有的樣子差」—— 兩者的緊急程度與該做的事都不同。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import run_quality as _rq
    findings = quality_findings()
    if not findings:
        return 0
    print(f"[watchdog] 品質異常({info}):\n" + _rq.summarize(findings),
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
