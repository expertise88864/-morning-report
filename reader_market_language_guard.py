"""Guard model-authored conclusions without editing quoted news or headlines."""
from __future__ import annotations

import re
from conclusion_guard import summary_word
from earnings_timing_guard import correct_uncertain_earnings_impact
from reader_flow_guard import neutralize_flow
from reader_market_actor import is_factual_action
from policy_scope_guard import correct_cbc_scope
from reader_source_line import actor_prefix
from reader_citation_provenance import guarded_parts
from source_title_provenance import is_registered_emergency_title

_HEADING = re.compile(r"^\s*#{1,4}\s*(.*)$")
_STANCE = re.compile(r"^\s*(?:\*\*)?(中性|偏多|偏空|資料不足)(?:\*\*)?[，,、：:]?")
_TRADE = re.compile(r"加碼|減碼|買進|賣出|停損|追高|逢回買|進場|出場|續抱")
_SUMMARY_CONTAINER = re.compile(r"^(\s*(?:(?:>\s*)|(?:[-*+]\s+))+)(.*)$")
_PRICE_TRIGGER = re.compile(r"守穩|失守|支撐價|停損價")
_ACTION_LEAD = re.compile(r"(?:建議|可以|可|考慮|宜|應)$")
_READER_DIRECTION = re.compile(r"建議|考慮|可以|投資人|讀者|散戶|我們|你|可(?:在|趁|逢|分批)|應(?:該|先|減|買|賣|加)")
def neutralize_summary(summary: str, manifest: dict) -> str:
    """Retain a safe market observation, not an opening-price trade order."""
    if not isinstance(summary, str):
        return summary
    def _is_factual(match: re.Match) -> bool:
        prefix = summary[:match.start()]
        clause = re.split(r"[，,；;。]", prefix)[-1]
        suffix = re.split(r"[，,；;。]", summary[match.end():], maxsplit=1)[0]
        if (_READER_DIRECTION.search(clause) or _ACTION_LEAD.search(prefix.rstrip())
                or _PRICE_TRIGGER.search(prefix) or re.search(r"評等|評級|目標價|建議|推薦", suffix)):
            return False
        return is_factual_action(summary, match.start(), match.group(0),
                                 actor_prefix(prefix), suffix)
    action = next((match for match in _TRADE.finditer(summary)
                   if not _is_factual(match)), None)
    if action is None:
        return summary
    stance = _STANCE.match(summary)
    before_action = summary[:action.start()]
    complete_end = max((m.start() for m in re.finditer(r"[，；;。]|,(?!\d)", before_action)), default=-1)
    prefix = before_action[:complete_end].rstrip("不，,；;、 ") if complete_end >= 0 else ""
    if stance:
        prefix = prefix or stance.group(1)
        if (_PRICE_TRIGGER.search(prefix) or _READER_DIRECTION.search(prefix)
                or _ACTION_LEAD.search(prefix)):
            prefix = stance.group(1)
        revised = prefix + "；開盤預測僅供行情觀察，不是買賣訊號。"
    else:
        observation = before_action.rstrip("不，,；;、 ")
        if _PRICE_TRIGGER.search(observation) or _READER_DIRECTION.search(observation) or re.search(r"重申|評等|喊進|推薦", observation):
            observation = prefix
        if (_PRICE_TRIGGER.search(observation) or _READER_DIRECTION.search(observation)
                or _ACTION_LEAD.search(observation)):
            observation = ""
        revised = ((observation + "；") if len(observation) >= 5 or summary_word(observation, "")
                   else "") + "開盤預測僅供行情觀察，不是買賣訊號。"
    manifest.setdefault("llm", {})["conclusion_trade_claims_neutralized"] = 1
    return revised

def record_cbc_scope(text: str, manifest: dict) -> str:
    """Preserve applied delivery corrections for either report path."""
    corrected, rules = correct_cbc_scope(text, manifest)
    if rules:
        llm = manifest.setdefault("llm", {})
        llm["policy_scope_guard_rules"] = list(dict.fromkeys(
            [*(llm.get("policy_scope_guard_rules") or []), *rules]))
    return corrected

def neutralize_report(text: str, manifest: dict, *, us_stale: bool = False,
                      event_calendar=None, allowed_urls=(), source_titles=()) -> str:
    """Guard stance/summary language and a verified CBC claim at delivery."""
    if not isinstance(text, str) or not text:
        return text
    section = ""
    out = []
    for line in text.splitlines(keepends=True):
        heading = _HEADING.match(line)
        if heading:
            title = heading.group(1)
            section = ("summary" if "一句話總結" in title else
                       "stance" if "我的明確立場" in title else "")
        elif section in {"summary", "stance"} and line.strip():
            body = line.rstrip("\r\n")
            wrapped = _SUMMARY_CONTAINER.match(body)
            prefix, summary = (wrapped.group(1), wrapped.group(2)) if wrapped else ("", body)
            if not is_registered_emergency_title(line, manifest):
                quoted, original, checked = guarded_parts(summary, allowed_urls, source_titles)
                corrected = neutralize_summary(checked, manifest) if section == "summary" or "](" in summary else checked
                if section == "stance":
                    corrected = neutralize_flow(corrected, manifest)
                line = prefix + quoted + (corrected if corrected != checked else original) + line[len(body):]
        if us_stale and section in {"stance", "summary"} and not heading and "](" not in line:
            revised = line.replace("今日美股休市", "美股行情未更新").replace("美股昨日休市", "美股行情未更新")
            if revised != line:
                manifest.setdefault("llm", {})["unverified_us_holiday_claims_neutralized"] = (
                    manifest.setdefault("llm", {}).get("unverified_us_holiday_claims_neutralized", 0) + 1)
                line = revised
        out.append(line)
    corrected = correct_uncertain_earnings_impact("".join(out), event_calendar, manifest)
    return record_cbc_scope(corrected, manifest)
