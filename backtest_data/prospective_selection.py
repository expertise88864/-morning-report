"""Frozen prospective protocol, offline only; never promotes a live model.

Future snapshot dates alone do not establish point-in-time integrity. Existing
calendar, corporate-action, tradability and benchmark blockers remain mandatory.
"""
import argparse
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

START_DATE = '2026-09-21'
PRIMARY_HORIZON = 20
DIAGNOSTIC_HORIZON = 5
FEE_BPS = 15
SELL_TAX_BPS = 30
SLIPPAGE_BPS = (10, 25)
# Set to the committed research implementation; mismatch is a new protocol.
IMPLEMENTATION_SHA256 = 'e0ce0ce03fdc244d5507f25e010c2427a09bf5731e3a5f44926861830d568564'


def run(days, implementation_bytes, *, today):
    actual = hashlib.sha256(implementation_bytes.replace(b'\r\n', b'\n')).hexdigest()
    if actual != IMPLEMENTATION_SHA256:
        raise ValueError('research implementation changed: register a new future protocol')
    from backtest_data.selection_research import evaluate
    observed = [d for d in days if d['session_date'] <= today.isoformat()]
    observed_bytes = json.dumps(observed, ensure_ascii=False, sort_keys=True,
                                separators=(',', ':')).encode('utf-8')
    evaluations = []
    for horizon in (PRIMARY_HORIZON, DIAGNOSTIC_HORIZON):
        for slip in SLIPPAGE_BPS:
            result = evaluate(observed, horizon, FEE_BPS, SELL_TAX_BPS, slip,
                              start_date=START_DATE)
            # Do not present legacy exploratory early/late labels as OOS folds.
            result.pop('folds')
            for row in result['cohorts']:
                row.pop('fold')
            result['study_type'] = 'prospective_protocol_not_certified_oos'
            evaluations.append(result)
    return {'decision': 'NO_REPLACEMENT', 'start_date': START_DATE,
            'as_of_date': today.isoformat(),
            'observed_sha256': hashlib.sha256(observed_bytes).hexdigest(),
            'implementation_sha256': actual, 'evaluations': evaluations,
            'primary_horizon': PRIMARY_HORIZON,
            'diagnostic_horizon': DIAGNOSTIC_HORIZON,
            'limitation': 'Untouched future data still requires provenance, calendar, '
                          'corporate actions, tradability and benchmark validation.'}


def _today_tpe() -> date:
    return datetime.now(ZoneInfo('Asia/Taipei')).date()


def _as_of(argv=None) -> date:
    today = _today_tpe()
    parser = argparse.ArgumentParser(description='Read-only prospective selection study')
    parser.add_argument('--as-of', type=date.fromisoformat, default=today,
                        help='Frozen YYYY-MM-DD cutoff for reproducible historical inspection')
    as_of = parser.parse_args(argv).as_of
    if as_of > today:
        parser.error('--as-of cannot be in the future')
    return as_of


def main(argv=None):
    as_of = _as_of(argv)
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from model_history_store import load_model_history
    days = load_model_history(root / 'state/model_history.json',
                              root / 'state/model_history', strict=True)
    report = run(days, Path(__file__).with_name('selection_research.py').read_bytes(),
                 today=as_of)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
