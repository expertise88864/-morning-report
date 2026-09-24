"""Exact 9/21 delivered hedge attributions; no general semantic rewriting."""
from __future__ import annotations

import re


_CLAIMS = (
    (
        "cash_futures_coincidence_not_hedge_evidence",
        "但後者與現貨買超同時出現，性質更接近避險而非方向看空",
        "但現貨買超與期貨淨空同時出現，不能由彙總數據判定是否為同一批人避險或方向看空",
    ),
    (
        "cash_futures_pairing_not_established",
        "最合理的解釋是現貨多單搭配期貨避險，而非方向看空",
        "彙總數據無法判定現貨多單是否搭配期貨避險，也不能單憑淨空判定方向",
    ),
    (
        "futures_short_reference_not_dismissed",
        "只能說在現貨同步買超的前提下，淨空的參考性偏低",
        "應分別看待現貨流量與期貨淨部位，不能由兩者推定交易者意圖",
    ),
    (
        "holiday_hedge_purpose_unconfirmed",
        "即使多為避險，在連假前流動性變薄時仍可能放大波動",
        "用途尚未確認；連假前流動性可能變薄，波動風險需留意",
    ),
    (
        "withdrawal_vs_hedge_unidentified",
        "外資台指期淨空持續擴大且現貨同步轉賣，代表資金撤出而非對沖",
        "外資台指期淨空若持續擴大且現貨同步轉賣，須再核對交易者與部位，"
        "不能單憑彙總數據區分撤出或對沖",
    ),
)


def correct_confirmed_0921(text: str) -> tuple[str, tuple[str, ...]]:
    """Change only confirmed analysis phrases; caller has masked quotes/leads."""
    rules: list[str] = []
    for rule, original, replacement in _CLAIMS:
        text, count = re.subn(re.escape(original), replacement, text)
        if count:
            rules.append(rule)
    return text, tuple(rules)
