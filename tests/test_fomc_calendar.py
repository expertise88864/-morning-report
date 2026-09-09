import datetime as dt

import fomc_calendar as calendar


def test_summer_decision_is_next_day_in_taipei():
    meetings = [dt.date(2026, 9, 16)]
    assert calendar.events(meetings, dt.date(2026, 9, 9), dt.date(2026, 9, 16)) == []
    event, = calendar.events(meetings, dt.date(2026, 9, 17), dt.date(2026, 9, 17))
    assert event['date'] == dt.date(2026, 9, 17) and event['time'] == '02:00'


def test_winter_decision_observes_us_standard_time():
    event, = calendar.events([dt.date(2026, 1, 28)], dt.date(2026, 1, 29), dt.date(2026, 1, 29))
    assert event['time'] == '03:00'


def test_production_rule_calendar_uses_taipei_boundary():
    import morning_report as mr
    events = mr._rule_based_events(dt.date(2026, 9, 10))
    event, = [e for e in events if 'FOMC' in e['title']]
    assert event['date'] == dt.date(2026, 9, 17) and event['time'] == '02:00'
