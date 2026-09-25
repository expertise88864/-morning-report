"""Evidence-bound correction of a buyer/target reversal in legacy prose.

The old free-form report writer can head an investment article with the company
whose shares were bought.  Only an explicit linked source title and matching
sentence authorize a correction; otherwise the original prose is preserved.
"""

import re

import analysis_origin
from news_actor_roles import purchase_target_actor
from price_reaction_guard import neutralize


_LEAD = re.compile(r"^(?P<space>\s*)\*\*(?P<label>[^*\n：:]{2,100})\*\*(?P<colon>[：:])")
_LINK = re.compile(r"\[[^\]\n]*\]\((https?://[^\s)]+)\)")


def correct_investment_target_headings(text: str, news: list[dict], *,
                                       origin: str, manifest: dict) -> str:
    """Use the source's named buyer, never a guessed issuer or asset label."""
    if origin not in {analysis_origin.LEGACY_PRIMARY,
                      analysis_origin.LEGACY_AFTER_LUNA_FAILURE}:
        return text
    titles = {str(n.get("link") or n.get("url") or ""): str(n.get("title") or "")
              for n in news if isinstance(n, dict)}
    if not titles or not isinstance(text, str):
        return text
    parts = re.split(r"(\n\s*\n)", text)
    changed = 0
    for i, part in enumerate(parts):
        lead = _LEAD.match(part)
        if not lead:
            continue
        target = re.split(r"[（(]", lead.group("label"), maxsplit=1)[0].strip()
        if not target:
            continue
        body = part[lead.end():]
        for url in dict.fromkeys(_LINK.findall(body)):
            buyer, verb = purchase_target_actor(titles.get(url, ""), (target,))
            if not buyer or not re.search(re.escape(buyer) + r"[^。；\n]{0,55}" +
                                          r"(?:加碼|買進|購入|增持)", body[:110]):
                continue
            replacement = f"{lead.group('space')}**{buyer}{verb}{target}（非{target}營運新聞）**{lead.group('colon')}"
            parts[i] = replacement + body
            changed += 1
            break
    if changed:
        manifest.setdefault("llm", {})["investment_target_headings_corrected"] = changed
    return "".join(parts)


def correct_reader_claims(text: str, news: list[dict], *,
                          origin: str, manifest: dict) -> str:
    """Apply the narrow actor and price-causality safeguards before recap."""
    if origin == analysis_origin.EMERGENCY_FALLBACK:
        return text  # This path is a verbatim source-title list, not model prose.
    corrected = correct_investment_target_headings(
        text, news, origin=origin, manifest=manifest)
    return neutralize(corrected, manifest)
