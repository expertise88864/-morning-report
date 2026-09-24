"""Pure aggregation of the existing five-day sector rotation snapshot."""
from __future__ import annotations


def sector_rotation(snapshot: list, min_members: int = 3, top_n: int = 4) -> dict:
    """Group existing industry and five-day returns without fetching new data."""
    def _med(xs: list) -> float:
        sv = sorted(xs)
        n = len(sv)
        return sv[n // 2] if n % 2 else (sv[n // 2 - 1] + sv[n // 2]) / 2

    by_ind: dict[str, list] = {}
    all_p5: list = []
    for e in snapshot:
        p5 = e.get("pct_5d")
        if isinstance(p5, (int, float)):
            ind = str(e.get("industry") or "").strip()
            if ind and ind != "未分類":
                by_ind.setdefault(ind, []).append(p5)
                all_p5.append(p5)
    if not all_p5:
        return {}
    mkt = _med(all_p5)
    ranked = [(ind, round(_med(xs), 2), round(_med(xs) - mkt, 2), len(xs))
              for ind, xs in by_ind.items() if len(xs) >= min_members]
    if len(ranked) < 3:
        return {}
    ranked.sort(key=lambda r: r[1], reverse=True)
    weak = [r for r in ranked[::-1][:2] if r not in ranked[:top_n]]
    # Preserve every sector for the reader's complete rotation table.
    table = [{"industry": ind, "median_5d": med, "relative": rel, "members": n,
              "up_5d": sum(1 for p in by_ind[ind] if p > 0)}
             for ind, med, rel, n in ranked]
    return {"market_median": round(mkt, 2), "strong": ranked[:top_n], "weak": weak,
            "table": table}
