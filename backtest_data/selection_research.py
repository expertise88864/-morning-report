"""Offline, exploratory Top3 comparison. Never authorizes live replacement.

Snapshot dates are NOT a verified exchange calendar; raw prices are NOT total
returns. Historical data informed the hypothesis, so folds are not untouched OOS.
No network, production imports, state writes or automatic coefficient changes.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sys


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def positive(value):
    value = number(value)
    return value if value is not None and value > 0 else None


def picks(stocks: dict, avoid_heat: bool) -> list[str]:
    """Select before looking at any future price; deterministic ties by code."""
    eligible = {c: s for c, s in stocks.items()
                if s.get('liquidity_eligible') is True
                and number(s.get('ranking_score')) is not None}
    if avoid_heat:
        eligible = {c: s for c, s in eligible.items()
                    if number(s.get('pct_5d')) is not None
                    and number(s.get('ma20_dist_pct')) is not None}
        # Fixed hypothesis, not optimized against these historical returns.
        if eligible:
            k = len(eligible) - math.ceil(len(eligible) / 4)
            momentum = sorted(s['pct_5d'] for s in eligible.values())[k]
            extension = sorted(s['ma20_dist_pct'] for s in eligible.values())[k]
            eligible = {c: s for c, s in eligible.items()
                        if not (s['pct_5d'] > 0 and s['ma20_dist_pct'] > 0
                                and s['pct_5d'] >= momentum
                                and s['ma20_dist_pct'] >= extension)}
    return sorted(eligible, key=lambda c: (-eligible[c]['ranking_score'], c))[:3]


def price(day: dict, code: str, field: str):
    # Label archive covers names leaving the changing selection universe.
    labels = day.get('label_prices') or {}
    stocks = day.get('stocks') or {}
    return positive((labels.get(code) or {}).get(field)) or positive(
        (stocks.get(code) or {}).get(field))


def available_before_open(signal: dict, entry: dict) -> bool:
    try:
        generated = datetime.fromisoformat(signal['generated_at'])
        opening = datetime.fromisoformat(entry['session_date'] + 'T09:00:00+08:00')
        return generated.tzinfo is not None and generated < opening
    except (KeyError, TypeError, ValueError):
        return False


def cohort(day, entry, end, codes, fee, tax, slip):
    gross, net = [], []
    for code in codes:
        op, close = price(entry, code, 'open'), price(end, code, 'close')
        if op is None or close is None:
            return None  # Never replace missing losers with the next-ranked name.
        ratio = close / op
        gross.append(ratio - 1)
        net.append(ratio * (1 - slip) * (1 - fee - tax)
                   / ((1 + slip) * (1 + fee)) - 1)
    # Three equal capital slots; unused slots stay cash with zero interest.
    return {'gross': sum(gross) / 3, 'net': sum(net) / 3,
            'invested_slots': len(codes)}


def summarize(rows):
    if not rows:
        return {'cohorts': 0}
    result = {'cohorts': len(rows)}
    for name in ('baseline', 'avoid_heat'):
        result[name] = {
            'mean_gross_pct': 100 * sum(r[name]['gross'] for r in rows) / len(rows),
            'mean_after_cost_pct': 100 * sum(r[name]['net'] for r in rows) / len(rows),
            'mean_invested_slots': sum(r[name]['invested_slots'] for r in rows) / len(rows)}
    differences = [r['avoid_heat']['net'] - r['baseline']['net'] for r in rows]
    result['paired_mean_improvement_pct'] = 100 * sum(differences) / len(rows)
    result['paired_win_fraction'] = sum(x > 0 for x in differences) / len(rows)
    # Do not compound sparse/missing cohorts into a fictitious continuous NAV.
    return result


def evaluate(days, horizon, fee_bps, sell_tax_bps, slippage_bps):
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
        raise ValueError('horizon must be a positive integer')
    costs = [number(v) for v in (fee_bps, sell_tax_bps, slippage_bps)]
    if any(v is None or v < 0 or v >= 10000 for v in costs):
        raise ValueError('cost assumptions must be finite nonnegative basis points')
    fee, tax, slip = [v / 10000 for v in costs]
    if fee + tax >= 1:
        raise ValueError('sell costs cannot consume the entire proceeds')
    ordered = sorted(days, key=lambda d: d['session_date'])
    dates = [d['session_date'] for d in ordered]
    if len(set(dates)) != len(dates):
        raise ValueError('duplicate snapshot dates')
    skips, rows = {}, []

    def skip(reason):
        skips[reason] = skips.get(reason, 0) + 1

    # First 60% is excluded from performance reporting, not used for tuning.
    # Fixed chronological research folds; all history remains exploratory.
    start = math.ceil(len(ordered) * .60)
    for i in range(start, len(ordered) - horizon, horizon):
        day, entry, end = ordered[i], ordered[i + 1], ordered[i + horizon]
        if not available_before_open(day, entry):
            skip('signal_not_proven_available_before_entry')
            continue
        stocks = day.get('stocks') or {}
        baseline = picks(stocks, False)
        if not baseline:
            skip('no_stored_ranked_candidates')
            continue
        # Use common feature eligibility without silently altering the baseline.
        ranked = [s for s in stocks.values() if s.get('liquidity_eligible') is True
                  and number(s.get('ranking_score')) is not None]
        if any(number(s.get(f)) is None for s in ranked
               for f in ('pct_5d', 'ma20_dist_pct')):
            skip('incomplete_timing_features')
            continue
        candidate = picks(stocks, True)
        a = cohort(day, entry, end, baseline, fee, tax, slip)
        b = cohort(day, entry, end, candidate, fee, tax, slip)
        if a is None or b is None:
            skip('missing_selected_price_paired_cohort')
            continue
        rows.append({'signal_date': dates[i], 'entry_date': entry['session_date'],
                     'exit_date': end['session_date'],
                     'fold': 'early' if i < len(ordered) * .8 else 'late',
                     'baseline': a, 'avoid_heat': b})
    return {
        'decision': 'NO_REPLACEMENT',
        'study_type': 'exploratory_chronological_comparison_not_untouched_oos',
        'horizon_snapshot_sessions': horizon,
        'cost_assumptions_bps': {'fee_each_side': fee_bps, 'sell_tax': sell_tax_bps,
                                 'slippage_each_side': slippage_bps},
        'snapshot_count': len(ordered), 'skipped': skips,
        'summary': summarize(rows),
        'folds': {f: summarize([r for r in rows if r['fold'] == f])
                  for f in ('early', 'late')},
        'cohorts': rows,
        'replacement_blockers': [
            'History already informed the hypothesis; no untouched holdout.',
            'Snapshot dates are not a verified exchange session calendar.',
            'Raw prices: dividends/splits and delistings are not fully resolved.',
            'Open price availability does not prove tradability at limit/halts.',
            'No same-open benchmark series; do not claim market excess return.',
            'No continuous marked-to-market equity curve or certified drawdown.',
            'Stored rankings span model versions; not a replay of one frozen model.',
        ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--horizon', type=int, default=20)
    parser.add_argument('--fee-bps', type=float, required=True)
    parser.add_argument('--sell-tax-bps', type=float, required=True)
    parser.add_argument('--slippage-bps', type=float, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from model_history_store import load_model_history
    days = load_model_history(root / 'state/model_history.json',
                              root / 'state/model_history', strict=True)
    print(json.dumps(evaluate(days, args.horizon, args.fee_bps, args.sell_tax_bps,
                              args.slippage_bps), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
