"""A feed's short 'final' record must not masquerade as a confirmed result."""
import copy
import datetime as dt
import html

import pytest

import morning_report as mr
from sports_quality import cpbl_result_note


def game(inning=3, away='0', home='0'):
    return {'status_type': 'status.type.final', 'current_period_id': inning,
            'minimum_periods': 9, 'total_away_points': away, 'total_home_points': home,
            'away_team_id': 'away', 'home_team_id': 'home',
            'start_time': 'Sat, 12 Sep 2026 09:05:00 +0000'}


def fetch(monkeypatch, records):
    buckets = iter(records)

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'service': {'scoreboard': {
                'games': {'cpbl.g.260912326': next(buckets)},
                'teams': {'away': {'display_name': '統一'}, 'home': {'display_name': '味全'}}}}}

    monkeypatch.setattr(mr, '_http_get', lambda *a, **k: Response())
    return mr.fetch_cpbl_scores(dt.datetime(2026, 9, 13, 7, tzinfo=mr.TPE))


@pytest.mark.parametrize('inning', [3, 5, 7, '3'])
def test_short_feed_final_is_qualified_not_deleted(inning):
    source = game(inning)
    before = copy.deepcopy(source)
    assert '結束方式待確認' in cpbl_result_note(source)
    assert source == before


@pytest.mark.parametrize('inning', [9, 10, 12, None, '', 'bad', float('nan'), float('inf')])
def test_do_not_invent_short_game_from_missing_or_invalid_inning(inning):
    assert not cpbl_result_note(game(inning))


def test_live_game_is_not_reclassified_as_finished():
    assert not cpbl_result_note(dict(game(), status_type='status.type.inprogress'))


def test_actual_fetch_to_render_preserves_provisional_score(monkeypatch, capsys):
    rows = fetch(monkeypatch, [game(3, '2', '0'), game(3, '2', '0')])
    assert len(rows) == 1
    assert rows[0]['winner'] == ''
    assert rows[0]['away_score'] == 2
    assert '::warning::CPBL' in capsys.readouterr().err
    rendered = mr._render_sports_html({'news': {}, 'cpbl_scores': rows}, html)
    assert '中華職棒 比分與狀態' in rendered
    assert '比分暫列' in rendered and '統一 2' in rendered
    assert "<b style='color:#b91c1c;'>統一 2</b>" not in rendered
    assert '因雨' not in rendered and '取消' not in rendered


@pytest.mark.parametrize('reverse', [False, True])
def test_complete_bucket_wins_over_provisional_bucket(monkeypatch, reverse):
    records = [game(3), game(9, '4', '2')]
    rows = fetch(monkeypatch, records[::-1] if reverse else records)
    assert len(rows) == 1
    assert rows[0]['away_score'] == 4 and rows[0]['winner'] == 'away'
    assert not rows[0]['result_note']
    rendered = mr._render_sports_html({'news': {}, 'cpbl_scores': rows}, html)
    assert '中華職棒 最新賽果' in rendered and '比分暫列' not in rendered


def test_result_note_is_escaped_and_cannot_claim_a_winner():
    row = {'away': '統一', 'home': '味全', 'away_score': 2, 'home_score': 0,
           'winner': 'away', 'result_note': '<script>unsafe</script>'}
    rendered = mr._render_sports_html({'news': {}, 'cpbl_scores': [row]}, html)
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered
    assert "<b style='color:#b91c1c;'>統一 2</b>" not in rendered
