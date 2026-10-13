"""Unique binary-market selection, shared by display and snapshot capture."""
import math
import re


def current_probability(row):
    """Prefer the unique structured quote; reject invalid data, not reinterpret it."""
    if "outcomes" in row:
        outcomes = row["outcomes"]
        if not isinstance(outcomes, list) or len(outcomes) != 1 or not isinstance(outcomes[0], dict):
            return None
        value = outcomes[0].get("prob_raw", outcomes[0].get("prob"))
    else:
        match = re.match(r"機率 ([0-9]+(?:\.[0-9]+)?)%(?:$|\()", str(row.get("detail") or ""))
        value = float(match[1]) if match else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        return None
    return value if math.isfinite(value) else None


def detail(key, markets, now, question_re, capture, *, yes_prob, track,
           suffix, low_volume, wide_spread, warn):
    candidates = [m for m in (markets or []) if yes_prob(m) is not None]
    if question_re:
        candidates = [m for m in candidates
                      if re.search(question_re, str(m.get("question") or ""), re.I)]
    if not candidates:
        return None
    if len(candidates) > 1:
        warn(f"[poly] {key}: {len(candidates)} ambiguous binary markets; omitted")
        return None
    market = candidates[0]
    probability = yes_prob(market) * 100
    if capture is not None:
        capture.append({"hist_key": str(market.get("id") or market.get("question") or "yes"),
                        "name": "機率", "prob_raw": probability})
    delta = track(key, {"yes": round(probability, 2)}, now).get("yes") or {}
    low = False
    try:
        if market.get("volume24hr") is not None:
            low = float(market["volume24hr"]) < low_volume
    except (TypeError, ValueError):
        pass
    spread = ""
    try:
        if (float(market.get("spread")) >= wide_spread
                and market.get("bestBid") is not None and market.get("bestAsk") is not None):
            spread = (f"(買{round(float(market['bestAsk']) * 100)}"
                      f"/賣{round(float(market['bestBid']) * 100)})")
    except (TypeError, ValueError):
        pass
    return (f"機率 {round(probability)}%" + suffix(delta.get("pp"), delta.get("days", 1))
            + spread + ("(量低)" if low else ""))
