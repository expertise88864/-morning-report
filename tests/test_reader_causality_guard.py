"""Offline incident regression: the 9/24 letter inferred ETF flows from cap."""
from __future__ import annotations

import reader_causality_guard as guard


def test_delivered_etf_inferences_are_qualified_without_losing_the_news():
    text = (
        "## 八、科技板塊脈動\n"
        "台積電：台股總市值上升，台積電單日貢獻逾 1 兆元市值（鉅亨）。"
        "傳導機制是被動式資金的機械買盤——0050、006208 等市值型 ETF "
        "因權重膨脹被迫加碼，反過來替 2330 現貨價格做地板。"
        "量級上，昨天這 1 兆元市值增幅是單日事件，無法外推，"
        "但要留意同一批被動資金在台積電回檔時的反向賣壓會同樣機械化。"
        "下一個可驗證點是公司法說。"
    )
    out, rules = guard.correct_etf_flow_inferences(text)
    assert rules == ("etf_forced_buy_from_market_cap",
                     "etf_reverse_flow_from_market_cap")
    assert "被迫加碼" not in out and "價格做地板" not in out
    assert "同一批被動資金" not in out
    assert "申贖與交易資料" in out
    assert "台股總市值上升" in out and "（鉅亨）" in out
    assert "昨天這 1 兆元市值增幅是單日事件，無法外推" in out
    assert "下一個可驗證點是公司法說" in out
    assert out.startswith("## 八、科技板塊脈動\n")


def test_source_headline_and_actual_flow_report_are_not_rewritten():
    text = (
        "**新聞標題：ETF 因權重調整被迫加碼。**\n"
        "基金申贖報告記錄 ETF 實際買入，來源為交易所公告。"
        "市值型 ETF 的市場成交量增加，尚待釐清原因。"
    )
    assert guard.correct_etf_flow_inferences(text) == (text, ())


def test_evidence_in_same_sentence_is_preserved():
    text = (
        "交易所公告申贖資料顯示，傳導機制是被動式資金的機械買盤——"
        "0050 等市值型 ETF 因權重膨脹被迫加碼，反過來替 2330 現貨價格做地板。"
    )
    assert guard.correct_etf_flow_inferences(text) == (text, ())


def test_unrelated_analysis_and_partial_markers_are_unchanged():
    text = "權重膨脹可能影響 ETF。被動資金有反向賣壓，但交易資料尚未提供。"
    assert guard.correct_etf_flow_inferences(text) == (text, ())
