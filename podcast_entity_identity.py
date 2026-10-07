"""Avoid presenting an inferred Podcast company as a verified transcript identity."""
from __future__ import annotations

import re

# Chinese transcript text may touch the English name without whitespace;
# Unicode \b treats Han characters as word characters, so use ASCII boundaries.
_ENGLISH_ENERGY_NAME = re.compile(
    r"(?<![A-Za-z])[A-Z][A-Za-z-]+ Energy(?![A-Za-z])", re.IGNORECASE)
_CATEGORY_MODIFIERS = {"Clean", "Nuclear", "Solar", "Renewable", "Green", "Wind"}
_CATEGORY_CONTEXT = re.compile(
    r"\s*(?:這個|這類|的)?\s*(?:裡面|領域|產業|板塊|族群|市場|題材|sector)",
    re.IGNORECASE,
)

def conflicting_spoken_name(ticker: dict) -> str:
    """Flag conflicting spoken Energy names, without verifying ticker identity."""
    name = ticker.get("name")
    evidence = ticker.get("direction_evidence")
    if not isinstance(name, str) or not _ENGLISH_ENERGY_NAME.fullmatch(name.strip()):
        return ""
    if not isinstance(evidence, dict):
        return ""
    quote = evidence.get("quote")
    if not isinstance(quote, str):
        return ""
    matches = list(_ENGLISH_ENERGY_NAME.finditer(quote))
    spoken: list[str] = []
    for match in matches:
        candidate = match.group()
        modifier = candidate.rsplit(" ", 1)[0]
        # Function words and lowercase sector modifiers do not identify companies.
        if modifier.casefold() in {"of", "the", "new", "and"} or (
                modifier.islower() and modifier.title() in _CATEGORY_MODIFIERS):
            continue
        # A category modifier plus a sector noun is not another company name;
        # without that context, leave company-like spoken names unresolved.
        if modifier.title() in _CATEGORY_MODIFIERS and _CATEGORY_CONTEXT.match(
                quote[match.end():match.end() + 16]):
            continue
        if all(candidate.casefold() != x.casefold() for x in spoken):
            spoken.append(candidate)
    return "／".join(spoken) if spoken and (len(spoken) > 1 or spoken[0].casefold() != name.strip().casefold()) else ""
