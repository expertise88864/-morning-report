"""Recognize attributed market actions without treating them as reader orders."""
from __future__ import annotations

import re


_CORPORATE_ADD = re.compile(
    r"加碼(?:\s*\d+(?:[,.]\d+)?\s*(?:億|兆|萬)(?:元|美元|日圓)?\s*)?"
    r"(?:資本支出|研發|建廠|擴產|產能)"
)
_FLOW_ACTOR = re.compile(
    r"(?:^|[，,；;。])\s*(?:但|而)?\s*(?:昨日|上週|近期)?\s*"
    r"(?:外資|投信|自營商|三大法人|法人|機構投資人|基金|資金)"
    r"(?:\s*(?:與|及|和)\s*(?:外資|投信|自營商|三大法人|法人|機構投資人|基金|資金)){0,2}"
    r"(?:\s*(?:現貨|期貨|大幅|小幅|大舉|持續|同步|轉為|逢低|淨|合計|已|昨天|連(?:續)?[一二三四五六七八九十\d]+[日天])){0,3}\s*$"
)
_POLICY_ACTOR = re.compile(r"(?:^|[，,；;。])\s*(?:Fed|聯準會|央行)\s*$", re.I)
_FUTURES_POSITION = re.compile(r"(?:^|[，,；;。、])\s*期貨(?:淨)?(?:空單|多單)(?:部位)?\s*$")
_EVENT_NOUN = re.compile(r"停損賣壓|追高意願")


def is_factual_action(summary: str, at: int, action: str,
                      prefix: str, suffix: str) -> bool:
    """Only explicit non-reader subjects or event nouns may escape the guard."""
    if _CORPORATE_ADD.match(summary, at) or _EVENT_NOUN.match(summary, at):
        return True
    if action not in {"買進", "賣出", "加碼", "減碼", "進場", "出場"}:
        return False
    if _FLOW_ACTOR.search(prefix):
        return True
    if action in {"加碼", "減碼"} and _FUTURES_POSITION.search(prefix):
        return True
    return (action in {"加碼", "減碼"}
            and bool(_POLICY_ACTOR.search(prefix))
            and bool(re.match(r"\s*(?:降息|升息|寬鬆|緊縮)", suffix)))
