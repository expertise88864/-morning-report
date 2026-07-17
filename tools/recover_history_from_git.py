# -*- coding: utf-8 -*-
"""一次性遷移:從 git 歷史恢復月分區缺漏的舊交易日(GPT-5.6 四審 P2)。

背景:月分區上線時以當時記憶體視圖(近 180 日 backfill 視窗)建立分區,
更早的真實快照只存在 git 歷史的舊 model_history.json(commit b75aecc 有 191 日,
2025-09-02 起),其中約 54 個 session(2025-09~2025-12,含整個 2025-09/11 月)
從未進分區。本腳本從指定 commit 讀出舊檔,把「分區沒有的日期」補進對應月分區;
**既有分區日期一律優先,絕不覆蓋**。寫入格式與正式 writer 一致
(canonical compact JSON + gzip mtime=0)。可重複執行(冪等)。
"""
import gzip
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PART_DIR = ROOT / "state" / "model_history"
SOURCE_COMMIT = "b75aecc"


def _dumps(items: list[dict]) -> str:
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    raw = subprocess.run(
        ["git", "show", f"{SOURCE_COMMIT}:state/model_history.json"],
        cwd=ROOT, capture_output=True, check=True).stdout
    old = [r for r in json.loads(raw.decode("utf-8"))
           if isinstance(r, dict) and r.get("session_date")]
    print(f"[recover] {SOURCE_COMMIT} 共 {len(old)} 日 "
          f"({old[0]['session_date']}..{old[-1]['session_date']})")
    by_month: dict[str, dict[str, dict]] = {}
    for rec in old:
        by_month.setdefault(rec["session_date"][:7], {})[rec["session_date"]] = rec
    PART_DIR.mkdir(parents=True, exist_ok=True)
    added_total = 0
    for month in sorted(by_month):
        path = PART_DIR / f"{month}.json.gz"
        existing: dict[str, dict] = {}
        if path.exists():
            for it in json.loads(gzip.decompress(path.read_bytes()).decode("utf-8")):
                if isinstance(it, dict) and it.get("session_date"):
                    existing[it["session_date"]] = it
        merged = dict(by_month[month])
        merged.update(existing)                    # 既有分區優先,絕不覆蓋
        new_dates = len(merged) - len(existing)
        if not new_dates:
            continue
        payload = _dumps(sorted(merged.values(),
                                key=lambda i: i.get("session_date", "")))
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(gzip.compress(payload.encode("utf-8"), mtime=0))
        tmp.replace(path)
        added_total += new_dates
        print(f"[recover] {month}: 分區現有 {len(merged)} 日(新增 {new_dates})")
    print(f"[recover] 完成:共補回 {added_total} 個交易日")


if __name__ == "__main__":
    sys.exit(main())
