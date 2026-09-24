"""Narrow, deterministic correction of two unsupported ETF-flow inferences.

This is an incident guard, not a general fact checker. A change in market
capitalisation alone cannot establish an ETF's trades or their price impact.
Only the two specific inference shapes seen in the 2026-09-24 delivered letter
are corrected; unrelated reporting and source headlines remain untouched.
"""
from __future__ import annotations

import re


_SENTENCE = re.compile(r"[^。！？\n]+[。！？]")
_FLOW_EVIDENCE = ("申贖資料顯示", "交易資料顯示", "交易所公告", "申贖報告", "實際買入", "實際賣出")
_BUY_CAUTION = (
    "單由市值變化，不能判定市值型 ETF 當日被迫買入或對現貨價格形成支撐；"
    "相關申贖與交易資料仍須另行核對。"
)
_SELL_CAUTION = (
    "回檔時是否出現被動資金賣壓，也須看實際申贖與交易資料，"
    "不能由今日市值變化直接外推。"
)
_SELL_CAVEAT = "量級上，昨天這 1 兆元市值增幅是單日事件，無法外推"


def correct_etf_flow_inferences(text: str) -> tuple[str, tuple[str, ...]]:
    """Correct only known unsourced interpretation sentences; return rule IDs.

    Do not edit a headline or a quoted source: the guard applies to prose
    explicitly claiming a transmission mechanism or the same investors'
    reverse trades. The output is used for both specialist and legacy mail.
    """
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else "", ()
    rules: list[str] = []

    def _correct(match: re.Match[str]) -> str:
        sentence = match.group(0)
        lead = sentence[:len(sentence) - len(sentence.lstrip(" \t"))]
        body = sentence[len(lead):]
        if body.startswith(("#", "**", "- **", "「", "“")):
            return sentence
        if any(marker in body for marker in _FLOW_EVIDENCE):
            return sentence
        if ("傳導機制是被動式資金的機械買盤" in body
                and "市值型 ETF" in body and "被迫加碼" in body
                and "反過來替 2330 現貨價格做地板" in body):
            rules.append("etf_forced_buy_from_market_cap")
            return lead + _BUY_CAUTION
        if ("同一批被動資金在台積電回檔時的反向賣壓會同樣機械化" in body):
            rules.append("etf_reverse_flow_from_market_cap")
            if body.startswith(_SELL_CAVEAT + "，但要留意"):
                return lead + _SELL_CAVEAT + "。" + _SELL_CAUTION
            return lead + _SELL_CAUTION
        return sentence

    return _SENTENCE.sub(_correct, text), tuple(rules)
