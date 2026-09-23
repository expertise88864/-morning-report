from datetime import date, timedelta
import hashlib
from pathlib import Path

import pytest

from backtest_data import prospective_selection as study


def test_registered_implementation_and_future_window_are_fixed():
    source = (Path(__file__).resolve().parents[1]
              / 'backtest_data/selection_research.py').read_bytes()
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
