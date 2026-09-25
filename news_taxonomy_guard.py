"""Keep retained-earnings accounting language out of purchase-event taxonomy."""

import re


_PURCHASE_OPENING = re.compile(
    r"^[^。，；\n]{0,80}?(?:斥資[^。，；\n]{0,40}加碼|買進|購入|增持)"
    r"[^。，；\n]{0,45}?(?:\d[\d,.]*\s*(?:張|股)|股票|股份|持股|證券)"
)
_RETAINED_EARNINGS = re.compile(r"保留盈餘|retained earnings", re.I)


def earnings_evidence_text(text: str) -> str:
    """Ignore a balance-sheet phrase only in an explicit share-purchase story.

    Other occurrences and actual EPS/profit/report language remain available to
    the ordinary earnings detector.  No source text or event is discarded.
    """
    value = str(text or "")
    return _RETAINED_EARNINGS.sub("", value) if _PURCHASE_OPENING.search(value) else value
