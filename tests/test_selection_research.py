"""Research safeguards, no production imports or network requests."""
from datetime import date, timedelta

import pytest

from backtest_data.selection_research import cohort, evaluate, picks


def history():
    days = []
    for i in range(30):
        d = str(date(2026, 1, 1) + timedelta(days=i))
        stocks = {str(j): {'ranking_score': 100-j, 'liquidity_eligible': True,
                          'pct_5d': 10-j, 'ma20_dist_pct': 10-j,
                          'open': 100, 'close': 110} for j in range(6)}
        days.append({'session_date': d, 'generated_at': d+'T18:00:00+08:00',
                     'stocks': stocks})
    return days


def test_net_return_accounts_for_both_legs_and_cash():
    day = {'stocks': {'a': {'open': 100, 'close': 110}}}
    got = cohort({}, day, day, ['a'], .001, .003, .002)
    assert got['gross'] == pytest.approx(.1/3)
    assert got['net'] == pytest.approx((1.1*.998*.996/(1.002*1.001)-1)/3)
    assert got['invested_slots'] == 1


def test_selection_does_not_consult_outcomes():
    stocks = history()[0]['stocks']
    assert picks(stocks, False) == ['0', '1', '2']
    assert picks(stocks, True) == ['2', '3', '4']
    stocks['0'].pop('close')
    assert picks(stocks, False) == ['0', '1', '2']


@pytest.mark.parametrize('n, first', [(4, 1), (8, 2), (100, 25)])
def test_top_quartile_boundary(n, first):
    stocks = {str(j): {'ranking_score': n-j, 'liquidity_eligible': True,
                      'pct_5d': n-j, 'ma20_dist_pct': n-j} for j in range(n)}
    assert picks(stocks, True) == [str(j) for j in range(first, first+3)]


def test_missing_selected_price_drops_pair_not_loser():
    days = history()
    days[19]['stocks']['0'].pop('open')
    result = evaluate(days, 2, 10, 30, 10)
    assert result['skipped']['missing_selected_price_paired_cohort'] == 1
    assert all(r['signal_date'] != days[18]['session_date'] for r in result['cohorts'])


def test_delayed_signal_is_not_available_at_old_open():
    days = history()
    days[18]['generated_at'] = days[19]['session_date']+'T09:00:00+08:00'
    result = evaluate(days, 2, 10, 30, 10)
    assert result['skipped']['signal_not_proven_available_before_entry'] == 1


def test_no_overlapping_holdings_and_no_automatic_promotion():
    result = evaluate(history(), 2, 10, 30, 10)
    rows = result['cohorts']
    assert all(a['exit_date'] < b['entry_date'] for a, b in zip(rows, rows[1:]))
    assert result['decision'] == 'NO_REPLACEMENT'
    assert 'not_untouched_oos' in result['study_type']
    assert 'max_drawdown' not in result['summary']


def test_label_archive_preserves_departed_names():
    day = {'stocks': {}, 'label_prices': {'a': {'open': 100, 'close': 110}}}
    assert cohort({}, day, day, ['a'], 0, 0, 0)['gross'] == pytest.approx(.1/3)


@pytest.mark.parametrize('value', [float('nan'), -1, True, float('inf')])
def test_invalid_costs_fail_closed(value):
    with pytest.raises(ValueError):
        evaluate(history(), 2, value, 30, 10)


def test_duplicate_dates_fail_closed():
    days = history()
    with pytest.raises(ValueError):
        evaluate(days + [days[0]], 2, 10, 30, 10)
