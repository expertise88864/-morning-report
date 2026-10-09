from datetime import date, timedelta
import hashlib
from pathlib import Path

import pytest

from backtest_data import prospective_selection as study


def test_registered_implementation_and_future_window_are_fixed():
    source = (Path(__file__).resolve().parents[1]
              / 'backtest_data/selection_research_frozen.py').read_bytes()
    got = study.run([], source, today=date(2026, 9, 19))
    assert got['decision'] == 'NO_REPLACEMENT'
    assert got['as_of_date'] == '2026-09-19'
    assert got['observed_sha256'] == hashlib.sha256(b'[]').hexdigest()
    assert got['start_date'] > '2026-09-19'
    assert len(got['evaluations']) == 4
    assert all(x['summary']['cohorts'] == 0 for x in got['evaluations'])
    assert got == study.run([], source.replace(b'\r\n', b'\n'), today=date(2026, 9, 19))


def test_changed_formula_cannot_reuse_registered_protocol():
    with pytest.raises(ValueError, match='implementation changed'):
        study.run([], b'different formula', today=date(2026, 9, 19))


def test_frozen_protocol_with_missing_selected_prices_cannot_report_improvement():
    from test_selection_research import history
    days = history()
    for i, day in enumerate(days):
        day['session_date'] = str(date(2026, 9, 21) + timedelta(days=i))
        day['generated_at'] = day['session_date'] + 'T18:00:00+08:00'
    days[1]['stocks']['0'].pop('open')
    source = (Path(__file__).resolve().parents[1]
              / 'backtest_data/selection_research_frozen.py').read_bytes()
    got = study.run(days, source, today=date(2026, 10, 21))
    assert got['implementation_sha256'] == study.IMPLEMENTATION_SHA256
    assert all(row['summary'] is None and row['comparison_status'] == 'invalid_selected_price_coverage'
               for row in got['evaluations'])


def test_explicit_window_does_not_shift_as_history_grows():
    from backtest_data.selection_research import evaluate
    days = [{'session_date': f'2026-09-{d:02d}'} for d in range(1, 20)]
    assert evaluate(days, 5, 15, 30, 10, start_date='2026-09-21')['summary']['cohorts'] == 0
    with pytest.raises(ValueError):
        evaluate([], 5, 15, 30, 10, start_date='not-a-date')


def test_as_of_cutoff_is_explicit_and_future_dates_are_rejected():
    assert study._as_of(['--as-of', '2026-09-23']) == date(2026, 9, 23)
    with pytest.raises(SystemExit):
        study._as_of(['--as-of', 'invalid'])
    with pytest.raises(SystemExit):
        study._as_of(['--as-of', (study._today_tpe() + timedelta(days=1)).isoformat()])
