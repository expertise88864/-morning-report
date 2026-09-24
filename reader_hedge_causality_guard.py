"""Qualify aggregate cash/futures hedge claims without changing source text."""
from __future__ import annotations

import re

from reader_hedge_0921 import correct_confirmed_0921


_QUOTED = re.compile(r"「[^」\n]*(?:」|$)|“[^”\n]*(?:”|$)|『[^』\n]*(?:』|$)")
_HEADLINE = re.compile(
    r"^(?:[-*]\s*)?(?:\*\*)?(?:新聞標題|來源標題)[:：]"
    r"|^[^：:\n]{1,24}報導[:：]"
    r"|^(?:\*\*[^*\n]+\*\*｜)?\[[^]\n]+\]\(https?://"
    r"|^\*\*[^*\n]+\*\*(?:（[^）\n]*）)?[。！？]?(?:\n|$)"
)
_COMPANY_LEAD = re.compile(r"^\*\*[^*\n]+\*\*｜")
_CASH_FUTURES = re.compile(r"(外資台指期淨空\s*[\d,]+\s*口)\s*（現貨同步大買，多為避險）")
_WATCH = re.compile(
    r"外資現貨大買但期貨淨空是今天的核心矛盾；\s*若現貨轉賣且淨空擴大，避險的解釋就不成立")
def correct_aggregate_hedge_inferences(text: str) -> tuple[str, tuple[str, ...]]:
    """Correct confirmed 9/21 and 9/23 prose, retaining source text and data."""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else "", ()
    marker = "\ue100"
    while marker in text:
        marker += "\ue100"
    protected: list[str] = []
    masked: list[str] = []

    def protect(value: str) -> str:
        protected.append(value)
        return f"{marker}{len(protected) - 1}{marker}"

    def hide(match: re.Match[str]) -> str:
        return protect(match.group(0))

    lines = text.splitlines(keepends=True)
    for line in lines:
        stripped = line.lstrip(" \t")
        # Bold company/category leads are source text; known report headings are analysis.
        is_company_lead = bool(_COMPANY_LEAD.match(stripped)
                               and not stripped.startswith(("**外資部位**｜", "**今日結論**｜",
                                                            "**風險觀察**｜")))
        lead = _HEADLINE.match(stripped) or (_COMPANY_LEAD.match(stripped) if is_company_lead else None)
        if lead:
            split = line.find("本報解讀：", len(line) - len(stripped) + lead.end())
            masked.append(protect(line[:split]) + _QUOTED.sub(hide, line[split:])
                          if split >= 0 else protect(line))
            continue
        masked.append(_QUOTED.sub(hide, line))
    corrected = "".join(masked)
    rules: list[str] = []
    corrected, count = _CASH_FUTURES.subn(
        lambda match: match.group(1)
        + "（現貨同步買超，但彙總數據不能判定是否為同一批人避險）", corrected)
    if count:
        rules.append("cash_futures_aggregate_does_not_prove_hedge")
    corrected, count = _WATCH.subn(
        "外資現貨買超與期貨淨空並存，但不能由彙總數據判定是否避險；"
        "若現貨轉賣且淨空擴大，仍須核對交易者與部位變化", corrected)
    if count:
        rules.append("watch_hedge_hypothesis_not_established")
    corrected, extra_rules = correct_confirmed_0921(corrected)
    rules.extend(extra_rules)
    for index, original in enumerate(protected):
        corrected = corrected.replace(f"{marker}{index}{marker}", original)
    return corrected, tuple(rules)
