# -*- coding: utf-8 -*-
"""2026-09-04 生產品質信:`evidence_value_stringified` ——
`market.QQQ.history(DataFrame)` / TSM / SPY 三格被 `str()` 帶過(defect)。

根因不在序列化:`fetch_quote()` 回的 dict 帶 `history`(1 個月 OHLC DataFrame,
給均線/波動度算的),而 packet 的 market 投影整個 dict 照收。修在投影層:
pandas 物件不是證據,是證據的原料 —— 不該進 packet,更不該變成一張文字表格
進 prompt。"""
import re
from pathlib import Path

import pandas as pd

import evidence_packet as ep
import evidence_serialize as es
import morning_report as mr

_ROOT = Path(mr.__file__).resolve().parent


def _clean(text):
    return text


def _quotes():
    hist = pd.DataFrame({"Close": [700.0, 705.5, 717.67], "Volume": [1, 2, 3]})
    return {
        "QQQ": {"ticker": "QQQ", "close": 717.67, "change_pct": 1.19, "history": hist},
        "TSM": {"ticker": "TSM", "close": 417.01, "change_pct": 0.36,
                "history": hist.copy(), "series": hist["Close"]},
        "SPY": {"ticker": "SPY", "close": 773.17, "change_pct": 1.05, "history": hist.copy()},
        "MACRO": {"VIX": {"close": 14.32}},
    }


def test_dataframes_never_enter_the_evidence_packet():
    packet = ep.build(_quotes(), {}, {}, [], [], {}, sanitize=_clean)
    for k in ("QQQ", "TSM", "SPY"):
        assert "history" not in packet["market"][k], packet["market"][k].keys()
        assert packet["market"][k]["close"] > 0            # 其餘欄位原樣保留
    assert "series" not in packet["market"]["TSM"]
    assert packet["market"]["MACRO"] == {"VIX": {"close": 14.32}}
    # **生產的判準**:同一份 packet 過 normalize 不得再有 lossy(那就是那條 defect)
    _tree, hits = es.normalize_json(packet)
    assert not [h for h in hits if h[0] == es.NORM_LOSSY], hits
    assert "DataFrame" not in ep.canonical_json(packet)


def test_the_production_quote_dict_still_carries_the_frame_so_the_projection_matters():
    """`fetch_quote` 仍回 `history` DataFrame(Python 端要用)—— 投影不是多餘的。
    這條把兩端的耦合釘住:哪天 fetch_quote 不再帶 history,這裡會提醒重看。"""
    src = (_ROOT / "morning_report.py").read_text(encoding="utf-8")
    fn = src[src.index("def fetch_quote("):]
    fn = fn[:fn.index("\ndef ", 10)]
    assert re.search(r'"history":\s*hist,', fn), "fetch_quote 不再帶 history?重看投影是否還需要"
    assert "QQQ" in ep.EVIDENCE_QUOTE_KEYS and "TSM" in ep.EVIDENCE_QUOTE_KEYS


def test_the_frame_test_is_duck_typed_against_real_pandas():
    """判準用型別名 + to_dict/iloc(不 import pandas);要真的認得 pandas 的物件,
    也不能把一般 dict/list 誤殺。"""
    assert ep._is_frame(pd.DataFrame({"a": [1]})) and ep._is_frame(pd.Series([1]))
    assert not ep._is_frame({"to_dict": 1, "iloc": 2}) and not ep._is_frame([1, 2])
    assert ep._without_frames({"a": [1, {"b": pd.Series([1])}]}) == {"a": [1, {}]}
