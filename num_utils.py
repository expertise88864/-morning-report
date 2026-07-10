"""數值基礎工具(A5-B1:自 morning_report.py 抽出)。

全 codebase 最高頻的數值黏著劑:安全轉 float/int、有限數防護、sigmoid。
刻意只依賴 stdlib(math / typing),不 import morning_report——如此後續模組
(news_rules / news_events / model_math…)可直接 `from num_utils import _safe_number`
而不會與 morning_report 形成循環。

搬遷原則(見 A5_MODULARIZATION_MAP.md §A):函式本體逐字不改;morning_report.py
於頂部 `from num_utils import (...)` 同名 re-export,既有呼叫與測試零修改仍可用。
"""
import math
from typing import Optional


def safe_float(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> int:
    """容忍逗號、空字串、None、float 字串"""
    if v is None:
        return 0
    s = str(v).replace(",", "").strip()
    if not s or s in ("-", "NA"):
        return 0
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _safe_number(value, default: float = 0.0) -> float:
    """將模型特徵轉成有限浮點數。"""
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, value))))
