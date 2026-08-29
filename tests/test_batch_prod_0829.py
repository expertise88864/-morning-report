# -*- coding: utf-8 -*-
"""2026-08-29 實信:**Luna 特化路徑第一次上線**,信裡露出兩個缺陷。

這一批的價值在於:legacy 路徑走不到那幾段,所以三千條測試全綠、外審
連過十輪,缺陷仍然要等特化真的被接受那一天才會現形。
"""
import io
import re
from pathlib import Path

import analysis_render as ar

_ROOT = Path(ar.__file__).resolve().parent


def _obj(**over):
    base = {
        "stance": {"label": "偏空", "score": -3, "rationale": "利率重定價"},
        "executive_summary": "開低約 1%",
        # 昨日觀察點回顧:實信裡就是這一段把立場蓋掉的
        "watch_review": [
            {"watch_id": "w1", "status": "triggered",
             "what_happened": "費半重挫"},
            {"watch_id": "w2", "status": "not_triggered",
             "what_happened": ""}],
    }
    base.update(over)
    return base


def _packet():
    return {"yesterday_watch": [
        {"watch_id": "w1", "trigger": "十年期殖利率突破 4.745%"},
        {"watch_id": "w2", "trigger": "NVDA財報前AI板塊資金動向"}]}


def test_the_stance_line_is_the_stance_not_the_last_watch_trigger():
    """**實信的原形**:信裡印的是
    「立場:NVDA財報前AI板塊資金動向(2330是否站穩2400)」——
    那是最後一條**昨日觀察點**的文字,不是立場。

    根因是變數洩漏:`label` 在函式開頭綁定成立場,而「昨日觀察點回顧」
    的迴圈用同一個名字當區域變數,把它蓋掉;底下的「立場:{label}」
    於是印出迴圈的最後一次賦值。反例要讓**兩者不同**才量得到。
    """
    md = ar.render(_obj(), _packet()) or ""
    assert md, "渲染不出東西就什麼都量不到"
    m = re.search(r"立場：(.+)", md)
    assert m, md[:300]
    assert m.group(1).strip().startswith("偏空"), m.group(1)
    # 昨日觀察點自己那一段照樣要在(修法不是把它拿掉)
    assert "NVDA財報前AI板塊資金動向：未觸發" in md, md[:600]
    assert "十年期殖利率突破 4.745%：已觸發" in md


def test_the_stance_label_is_bound_once_before_it_is_used():
    """機械化守衛:立場行之前,`label` 只能被綁定一次。
    這條在「有人又拿 `label` 當區域變數」時會紅,不必等生產露出來。"""
    src = io.open(_ROOT / "analysis_render.py", encoding="utf-8").read()
    i = src.index('stance_lines = [f"立場：{label}"]')
    binds = re.findall(r"^\s+label = ", src[:i], re.M)
    assert len(binds) == 1, f"立場行之前 label 被綁定 {len(binds)} 次"


def test_the_letter_never_shows_two_closes_for_the_same_stock(monkeypatch):
    """08/29 實信:2330 在第六段是 2410(yfinance,**落後一個交易日**)、
    在長線趨勢是 2420(TWSE 官方)。同一封信兩個昨收,而且**頭條的預測
    漲跌是拿舊基準算的**(-1.07% 應為 -1.48%)。

    `fetch_ma200_status` 早就為這件事改用官方值,註解還寫「與第六點一致」
    —— 那句宣稱是假的:第六點一直用 yfinance。修在了錯的那一邊。
    """
    import morning_report as mr
    import pandas as pd
    monkeypatch.setattr(mr, "fetch_twse_close", lambda code: 2420.0)
    # 比值回歸那一段會抓 TSM/匯率的 6 個月歷史 —— 測試不碰網路
    # (不擋的話這一條會花 30 秒在被守衛擋下的重試上)
    class _T:
        def __init__(self, *a, **k):
            pass

        def history(self, *a, **k):
            return pd.DataFrame()
    monkeypatch.setattr(mr.yf, "Ticker", _T)
    hist = pd.DataFrame({"Close": [2400.0, 2405.0, 2410.0]})   # yfinance 落後
    out = mr.calc_2330_predictions(417.52, 427.30, 31.63, hist)
    assert out.get("last_2330") == 2420.0, out
    # 官方值拿不到就退回 yfinance(晨報不可斷)
    monkeypatch.setattr(mr, "fetch_twse_close", lambda code: None)
    out2 = mr.calc_2330_predictions(417.52, 427.30, 31.63, hist)
    assert out2.get("last_2330") == 2410.0, out2


def test_both_close_sources_prefer_the_official_value():
    """機械化:兩個算收盤的地方都要先問 TWSE 官方值。少一邊就會再次
    出現「同一封信兩個昨收」—— 那正是這條要防的。"""
    src = io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    for anchor in ('last_2330 = safe_float(hist_2330.iloc[-1]["Close"])',
                   'out[sym] = {"name": name, "close": round(last, 2)'):
        i = src.index(anchor)
        seg = src[max(0, i - 600):i + 400]
        assert "fetch_twse_close" in seg, anchor
