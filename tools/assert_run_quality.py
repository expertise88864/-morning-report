# -*- coding: utf-8 -*-
"""CI canary 的斷言步驟:**exit 0 不等於跑成了**。

`python morning_report.py` 在下面三種情況都會 exit 0:

  * 特化路徑失敗、退回 legacy(信變淺)
  * 兩階段全文抓取整段 no-op(事件只剩 RSS 兩行摘要)
  * 昨日觀點沒存下來(明天的延續事件沒有 diff 基準)

canary 是一個很貴的 job(連真實服務、燒 API 額度、跑到 30 分鐘)——
它的綠燈如果只代表「process 沒有炸」,那筆成本買不到任何保證。

判準**不在這個檔裡**:它與每日看門狗共用 `run_quality.assess()`。
兩份判準各自演化的話,「canary 綠而生產壞」會再發生一次 ——
而那正是 2026-08-04 → 08-08 那五天的形狀。

`degraded` 只報 warning、`defect` 才 exit 1:外部服務不穩(額度用罄、
LLM 逾時)不該讓 CI 紅,而接線斷了該。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_quality as rq  # noqa: E402

MANIFEST = Path("state/run_manifest.json")


def main(path: Path = MANIFEST) -> int:
    if not path.is_file():
        print("::error::canary 跑完了但沒有 run_manifest.json —— "
              "主流程在寫 manifest 之前就結束了")
        return 1
    try:
        m = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as e:                      # noqa: BLE001
        print(f"::error::run_manifest.json 解析失敗:{e}")
        return 1
    findings = rq.assess(m)
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
