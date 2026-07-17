"""共用 model_history loader(純 stdlib,無第三方依賴)。

正式流程(morning_report)與 backtest_data 離線腳本(月報/IC 回測)必須走同一
讀取邏輯:legacy 單檔(凍結唯讀)+ 按月分區 gzip 合併、同一交易日以分區為準。
GPT-5.6 三審 P1:先前三個回測腳本直接讀 legacy 單檔,分區上線後 legacy 停在
2026-07-15/143 筆,月報與 D1/IC 評估的樣本會凍結或倒退。
"""
import gzip
import json
import sys
from pathlib import Path

DEFAULT_LEGACY_FILE = Path("state/model_history.json")
DEFAULT_PARTITION_DIR = Path("state/model_history")
DEFAULT_SESSIONS = 520


def load_model_history(legacy_file: Path = DEFAULT_LEGACY_FILE,
                       partition_dir: Path = DEFAULT_PARTITION_DIR,
                       sessions: int = DEFAULT_SESSIONS) -> list[dict]:
    """讀取 point-in-time 股票池歷史:legacy + 分區合併(分區優先),
    回傳依日期排序的最近 sessions 筆。單一分區壞檔只略過該檔(晨報不可斷)。"""
    merged: dict[str, dict] = {}
    if legacy_file.exists():
        try:
            data = json.loads(legacy_file.read_text(encoding="utf-8"))
            for item in data if isinstance(data, list) else []:
                if isinstance(item, dict) and item.get("session_date"):
                    merged[item["session_date"]] = item
        except Exception as e:
            print(f"[model_state] legacy 載入失敗: {e}", file=sys.stderr)
    if partition_dir.exists():
        for path in sorted(partition_dir.glob("*.json.gz")):
            try:
                data = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
                for item in data if isinstance(data, list) else []:
                    if isinstance(item, dict) and item.get("session_date"):
                        merged[item["session_date"]] = item   # 分區優先(較新)
            except Exception as e:
                print(f"[model_state] 分區 {path.name} 載入失敗(略過): {e}",
                      file=sys.stderr)
    history = sorted(merged.values(), key=lambda item: item.get("session_date", ""))
    return history[-sessions:]
