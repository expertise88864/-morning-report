"""Narrow headline-role guard for a named competitor, not an issuer guess."""

import re


_RIVAL_VERB = r"(?:槓上?|挑戰|對抗|抗衡|超越|力拚)"
_COMPARISON_TARGET = re.compile(_RIVAL_VERB + r"\s*[「『（(]?\s*$", re.I)
_ASCII_TARGET = re.compile(_RIVAL_VERB + r"\s*[「『（(]?\s*$", re.I)
_NOUN_CHALLENGE = re.compile(
    r"(?:關稅|通膨|成本|利率|供應鏈|疫情|市場|技術|新|面臨|面對|迎接|應對|克服)\s*挑戰\s*$"
)
_NOUN_RIVALRY = re.compile(
    r"(?:美中|中美|兩岸|地緣政治|科技戰|貿易戰)\s*(?:對抗|抗衡|超越)[\s，、；：:]+$"
)
_PURCHASE_VERB = re.compile(r"(?:加碼|買進|購入|增持)\s*$")
_PURCHASE_HEADLINE = re.compile(
    r"^(?P<buyer>[\u4e00-\u9fff]{2,12})\s*斥資[^。\n]{0,40}?(?P<verb>加碼|買進|購入|增持)\s*$"
)


def purchase_target_actor(title: str, aliases) -> tuple[str, str]:
    """Return a buyer only when a source headline explicitly names the target.

    A target company's name after a purchase verb is not evidence that its own
    operations changed.  Ambiguous or unanchored headlines stay untouched.
    """
    probe = str(title or "")
    for alias in dict.fromkeys(str(a or "").strip() for a in aliases):
        if len(alias) < 2:
            continue
        for match in re.finditer(re.escape(alias), probe, re.I):
            before = probe[:match.start()]
            found = _PURCHASE_HEADLINE.fullmatch(before)
            if found and found.group("buyer") != alias:
                return found.group("buyer"), found.group("verb")
    return "", ""


def comparison_target(title: str, aliases) -> bool:
    """A name appearing only after a rivalry verb is not the story's actor.

    Keep the complete headline; this merely avoids promoting an affected rival
    to a company heading. Multiple appearances remain eligible if any one is
    outside that narrow target position.
    """
    names = [str(alias or "").strip() for alias in aliases]
    names = [name for name in dict.fromkeys(names) if len(name) >= 2]
    probe = str(title or "")
    # The ticker beside a written name is the same mention, not a second actor.
    if len(names) >= 2:
        display, ticker = names[0], names[-1]
        if display != ticker:
            probe = re.sub(rf"({re.escape(display)})\s*[」』]?\s*[（(]\s*"
                           rf"{re.escape(ticker)}\s*[）)]", r"\1", probe, flags=re.I)
    positions = set()
    for name in names:
        pattern = re.escape(name)
        if name.isascii() and name.isalnum():
            pattern = rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])"
        positions.update(match.start() for match in re.finditer(pattern, probe, re.I))
    return bool(positions) and all(
        not _NOUN_CHALLENGE.search(probe[max(0, pos - 16):pos])
        and not _NOUN_RIVALRY.search(probe[max(0, pos - 16):pos])
        and
        ((_ASCII_TARGET if probe[pos].isascii() else _COMPARISON_TARGET)
         .search(probe[max(0, pos - 8):pos])
         or (_PURCHASE_VERB.search(probe[max(0, pos - 8):pos])
             and bool(purchase_target_actor(probe, names)[0]))) for pos in positions)
