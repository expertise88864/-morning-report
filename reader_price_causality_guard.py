"""Qualify confirmed 9/23 price-causality prose; protect quoted source text."""
from __future__ import annotations

import re
_QUOTE = re.compile(r"「[^」\n]*(?:」|$)|“[^”\n]*(?:”|$)|『[^』\n]*(?:』|$)", re.M)
_SENTENCE = re.compile(r"[^。！？\n]+(?:[。！？]|(?=\n|$))")
_SOURCE_HEADLINE = re.compile(r"^(?:[-*]\s*)?(?:\*\*)?(?:新聞標題|來源標題)[:：]")
_RENDERED_HEADLINE = re.compile(
    r"^(?:\*\*[^*\n]+\*\*｜)?\[[^\]\n]+\]\(https?://[^)\n]+\)"
    r"(?:（[^）\n]*）)?[。！？]?$|^\*\*[^*\n]+\*\*(?:（[^）\n]*）)?[。！？]?$")
_OWN_ANALYSIS = re.compile(r"^(?:\*\*風險觀察\*\*｜)?(?:本報解讀[:：]|(?:\*\*)?利率與科技股評價風險(?:[:：]|\*\*[:：｜]))")
_PRICE_CLAIM = "科技股昨天照樣創新高，說明市場目前願意忽略"
_RISK_CLAIM = "這是尚未被反映的風險"
_FUTURE_CLAIM = "兩邊都成立，差別在時間尺度：即日由風險偏好主導，1 到 4 週由折現率主導"

def _qualify_price(value: str) -> str:
    return (value.replace("說明市場目前願意忽略", "但單日股價無法判定市場如何看待")
            .replace("這是尚未被反映的風險，而不是已被否證的風險",
                     "這項風險是否已被反映，仍需更多證據判斷")
            .replace(_RISK_CLAIM, "這項風險是否已被反映，仍需更多證據判斷"))

def _correct_prose(text: str) -> tuple[str, tuple[str, ...]]:
    rules: list[str] = []
    def _correct(match: re.Match[str]) -> str:
        sentence = match.group(0)
        if _SOURCE_HEADLINE.match(sentence.lstrip(" \t")) or _RENDERED_HEADLINE.match(sentence.lstrip(" \t")):
            return sentence
        line_start = text.rfind("\n", 0, match.start()) + 1
        context = text[line_start:match.end()].lstrip(" \t")
        own_start = (match.start() if _OWN_ANALYSIS.match(context)
                     else text.find("本報解讀：", line_start, match.end()))
        if own_start < 0:
            return sentence
        cut = max(0, own_start - match.start())
        head, body = sentence[:cut], sentence[cut:]
        corrected = sentence
        if _PRICE_CLAIM in body and _RISK_CLAIM in body:
            rules.append("price_gain_does_not_prove_rate_risk_ignored")
            corrected = head + _qualify_price(body)
        if _FUTURE_CLAIM in body:
            rules.append("single_day_price_does_not_prove_future_driver")
            corrected = head + corrected[cut:].replace(
                _FUTURE_CLAIM, "短線股價與殖利率風險同時值得觀察；不能由單日價格確定未來 1 到 4 週的主導因素")
        return corrected
    corrected = _SENTENCE.sub(_correct, text)
    lines = corrected.splitlines(keepends=True)
    for index, lead in enumerate(lines):
        if not _OWN_ANALYSIS.match(lead.lstrip(" \t")) or _PRICE_CLAIM not in lead:
            continue
        for end in range(index + 1, min(index + 3, len(lines))):
            body = lines[end].lstrip(" \t")
            if not body.strip() or _SOURCE_HEADLINE.match(body) or _RENDERED_HEADLINE.match(body):
                break
            if _RISK_CLAIM in body and body.startswith(("這是", "但", "然而", "不過")):
                lines[index], lines[end] = _qualify_price(lead), _qualify_price(lines[end])
                rules.append("price_gain_does_not_prove_rate_risk_ignored")
                break
            if body.rstrip().endswith(("。", "！", "？")):
                break
    return "".join(lines), tuple(rules)

def correct_price_risk_inferences(text: str) -> tuple[str, tuple[str, ...]]:
    """Mask source quotes, correct surrounding prose, then restore quotes."""
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else "", ()
    marker = "\ue000"
    while marker in text:
        marker += "\ue000"
    parts: list[str] = []
    quotes: list[str] = []
    start = 0
    for quote in _QUOTE.finditer(text):
        parts.extend((text[start:quote.start()], f"{marker}{len(quotes)}{marker}"))
        quotes.append(quote.group(0))
        start = quote.end()
    parts.append(text[start:])
    corrected, rules = _correct_prose("".join(parts))
    for index, original in enumerate(quotes):
        corrected = corrected.replace(f"{marker}{index}{marker}", original)
    return corrected, rules
