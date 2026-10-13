"""Keep complete valid outcome snapshots before applying presentation cutoffs."""


def outright(slug, zh_map, top, min_prob, capture, *, events, yes_prob,
             low_volume, wide_spread):
    found = events({"slug": slug})
    if not found:
        return []
    rows = []
    for market in found[0].get("markets") or []:
        if market.get("closed"):
            continue
        name = str(market.get("groupItemTitle") or "").strip()
        if (not name or name.lower() == "other"
                or name.startswith(("Team ", "Player ", "Party "))):
            continue
        probability = yes_prob(market)
        if probability is None:
            continue
        low, wide = False, False
        try:
            if market.get("volume24hr") is not None:
                low = float(market["volume24hr"]) < low_volume
        except (TypeError, ValueError):
            pass
        try:
            if market.get("spread") is not None:
                wide = float(market["spread"]) >= wide_spread
        except (TypeError, ValueError):
            pass
        rows.append({"name": (zh_map or {}).get(name, name),
                     "hist_key": str(market.get("id") or name),
                     "prob": round(probability * 100), "prob_raw": round(probability * 100, 2),
                     "low_vol": low, "wide": wide})
    rows.sort(key=lambda row: -row["prob_raw"])
    if capture is not None:
        capture.extend(rows)
    return [row for row in rows if row["prob_raw"] >= min_prob * 100][:top]
