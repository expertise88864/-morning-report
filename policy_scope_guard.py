"""Narrow corrections for the CBC September 2026 investment report.

This is an incident-specific delivery guard, not a general fact checker. The
figures and scopes below come from CBC's September 2026 board briefing, Q1-Q2:
https://www.cbc.gov.tw/dl-227602-efb374350f4b49e0984456d64c2c7bb2.html
"""

import re

from reader_price_causality_guard import _RENDERED_HEADLINE, _SOURCE_HEADLINE
from source_title_provenance import is_registered_emergency_title

_LARGEST = re.compile(
    r"美國(?:以\s*18\.7\s*%\s*)?(?:躍居|成為|是)[^。；，\n]{0,28}"
    r"最大(?:對外)?投資(?:目的)?地"
)
CBC_SOURCE_URL = "https://www.cbc.gov.tw/dl-227602-efb374350f4b49e0984456d64c2c7bb2.html"
_SOURCE = f"（[央行原報告]({CBC_SOURCE_URL})）"
_SCOPE = ("美國在2018至2025年合計占18.7%，略低於中國大陸18.8%；"
          "央行明確指出2023、2024年美國為當年最大對外直接投資地")
_PRODUCTION = re.compile(r"(中國(?:大陸)?生產比重[^。；，\n]{0,12})26\.5%")

def correct_cbc_scope(text: str, manifest: dict | None = None) -> tuple[str, tuple[str, ...]]:
    """Correct only clearly identifiable CBC Q1/Q2 claims in report prose.

    Do not change source headlines or unrelated investment statistics. A new
    CBC publication would require a new verified rule, not a guessed update.
    """
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else "", ()
    rules = []
    lines = []
    for line in text.splitlines(keepends=True):
        line_rules = []
        body = line.lstrip(" \t")
        if (re.match(r"(?:>|「|“|\[)", body) or _SOURCE_HEADLINE.match(body) or is_registered_emergency_title(line, manifest) or
                _RENDERED_HEADLINE.match(body)):
            lines.append(line)
            continue
        if "央行報告" not in line and "央行原報告" not in line:
            lines.append(line)
            continue
        if all(part in line for part in ("2018", "2025", "18.8", "美國")):
            line, count = _LARGEST.subn(_SCOPE, line)
            if count:
                line_rules.append("cbc_2018_2025_vs_2023_2024_scope")
        if "2025" in line:
            line, count = _PRODUCTION.subn(r"\g<1>26.2%", line)
            if count:
                line_rules.append("cbc_2025_china_production_share")
        if line_rules and _SOURCE not in line:
            line = line.rstrip("\r\n") + _SOURCE + ("\n" if line.endswith("\n") else "")
        rules.extend(line_rules)
        lines.append(line)
    return "".join(lines), tuple(dict.fromkeys(rules))
