"""Narrow delivery correction for an unverified earnings-time inference."""

import re
from reader_price_causality_guard import _SOURCE_HEADLINE
from source_title_provenance import is_registered_emergency_title


_MU_INTRADAY = re.compile(
    r"對台股\s*週三\s*[（(]\s*9/30\s*[）)]\s*盤中\s*才有實質影響"
)


def correct_uncertain_earnings_impact(text: str, events, manifest: dict) -> str:
    """Do not turn a date-only MU event into a 9/30 Taiwan intraday effect."""
    if not isinstance(text, str) or not any(
        isinstance(row, dict) and row.get("title") == "MU 財報"
        and row.get("date_zone_uncertain") for row in (events or [])
    ):
        return text
    changed = 0
    out = []
    for line in text.splitlines(keepends=True):
        if ("美光" in line or re.search(r"\bMU\b", line)) and not line.lstrip().startswith(("#", ">", "[")) and not (_SOURCE_HEADLINE.match(line.lstrip()) or is_registered_emergency_title(line, manifest)) and "原始新聞標題" not in line:
            line, count = _MU_INTRADAY.subn(
                "財報公布時刻未核實，不能推定對台股週三（9/30）盤中已有影響", line)
            changed += count
        out.append(line)
    if changed:
        manifest.setdefault("llm", {})["uncertain_earnings_timing_claims_neutralized"] = changed
    return "".join(out)
