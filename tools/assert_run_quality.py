# -*- coding: utf-8 -*-
"""CI canary 的斷言步驟:**exit 0 不等於跑成了**。

`python morning_report.py` 在下面這些情況都會 exit 0:

  * 特化路徑失敗、退回 legacy(信變淺)
  * 兩階段全文抓取整段 no-op(事件只剩 RSS 兩行摘要)
  * 昨日觀點沒存下來(明天的延續事件沒有 diff 基準)
  * **主流程在寫 manifest 之前就掛掉** —— 而 `state/run_manifest.json`
    是進版控的,checkout 之後就在那裡:斷言會讀到**上一班**的檔案,
    而上一班可能剛好是健康的(外審 P1-2)

canary 是一個很貴的 job(連真實服務、燒 API 額度、跑到 30 分鐘)——
它的綠燈如果只代表「process 沒有炸」,那筆成本買不到任何保證。

判準**不在這個檔裡**:它與每日看門狗共用 `run_quality.assess()`,
差別只在 `mode`:

    watchdog  每日生產。額度用罄退回 legacy 是外部因素,報 degraded。
    strict    canary。這個 job 的名字是「證明特化輸出真的產生了」,
              退回 legacy 就是不通過;而且要證明這份 manifest 是
              **這一次執行**產生的(SHA / run id 綁定,舊檔案永遠滿足不了)。

兩份判準各自演化的話,「canary 綠而生產壞」會再發生一次 ——
而那正是 2026-08-04 → 08-08 那五天的形狀。
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_quality as rq  # noqa: E402

DEFAULT_MANIFEST = Path("state/run_manifest.json")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="canary 的執行品質斷言")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                    help="要檢查的 manifest(預設 state/run_manifest.json)")
    ap.add_argument("--mode", default="strict", choices=("strict", "watchdog"))
    ap.add_argument("--expected-sha", default=os.environ.get("GITHUB_SHA", ""))
    ap.add_argument("--expected-run-id",
                    default=os.environ.get("GITHUB_RUN_ID", ""))
    args = ap.parse_args(argv)

    path = Path(args.manifest)
    if not path.is_file():
        print("::error::canary 跑完了但沒有 run_manifest.json —— "
              "主流程在寫 manifest 之前就結束了")
        return 1
    try:
        m = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as e:                      # noqa: BLE001
        print(f"::error::run_manifest.json 解析失敗:{e}")
        return 1
    findings = rq.assess(m, mode=args.mode,
                         expected_sha=args.expected_sha,
                         expected_run_id=args.expected_run_id)
    if not findings:
        print("[canary] 特化路徑走完了,判準全過")
        return 0
    for f in findings:
        level = "error" if f.get("severity") == "defect" else "warning"
        print(f"::{level}::{f.get('code')} —— {f.get('detail')}")
    print(rq.summarize(findings))
    return 1 if any(f.get("severity") == "defect" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
