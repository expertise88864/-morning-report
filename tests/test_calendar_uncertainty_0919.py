import datetime as dt
from copy import deepcopy

from calendar_uncertainty import nearby_undated_times, format_uncertain

NOW = dt.datetime.fromisoformat('2026-09-19T07:00:00+08:00')


def test_date_only_events_remain_uncertain_at_window_edges():
    rows = [dict(date=d, title='事件', time='') for d in
            ['2026-09-18', '2026-09-19', '2026-09-20', '2026-09-21', '2026-09-22']]
    before = deepcopy(rows)
    assert nearby_undated_times(rows, NOW) == rows[1:4]
    text = format_uncertain(rows, NOW, str)
    assert '未確認是否落在未來 48 小時' in text
    assert '時間待確認' in text and '00:00' not in text
    assert rows == before


def test_known_times_bad_dates_and_past_dates_are_not_unknown_candidates():
    rows = [None, {}, dict(date='bad', title='事件'),
            dict(date='2026-09-19', title='壞時鐘', time='25:00'),
            dict(date='2026-09-20', title='事件', time='14:00'),
            dict(date='2026-09-18', title='事件', time='待定')]
    assert nearby_undated_times(rows, NOW) == []
    assert nearby_undated_times(rows, 'bad clock') == []


def test_real_prompt_formatter_retains_date_only_calendar_entry():
    import morning_report as mr
    text = mr._format_event_scenarios([
        dict(date='2026-09-20', title='測試央行決議', time='', note='不猜時間')], NOW)
    assert '測試央行決議' in text
    assert '時間待確認' in text
    assert '00:00' not in text
