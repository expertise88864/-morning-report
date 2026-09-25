"""Safely copy a Python-owned stance score without a paid repair round."""

from typing import Any

from stance_score_claims import claims_are_consistent


def align_model_score(obj: dict[str, Any], packet: dict[str, Any]) -> bool:
    """Align only when direction and explicit score claims remain consistent."""
    stance = obj.get("stance") if isinstance(obj, dict) else None
    market = packet.get("market") if isinstance(packet, dict) else None
    sp = market.get("STANCE_PY") if isinstance(market, dict) else None
    if not isinstance(stance, dict) or not isinstance(sp, dict):
        return False
    total, label = sp.get("total"), sp.get("label")
    if type(total) is not int or not label or stance.get("label") != label:
        return False
    score = stance.get("score")
    if type(score) is int and score == total:
        return False
    if not claims_are_consistent(obj, str(stance.get("rationale") or ""), total):
        return False
    stance["score"] = total
    return True
