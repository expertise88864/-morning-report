import copy
import datetime as dt

import analysis_render
import fixtures_analysis as fx
import morning_report as mr
import scenario_window as sw


NOW = dt.datetime(2026, 9, 14, 7, 32, tzinfo=sw.TPE)


def test_unknown_clock_is_not_evidence_of_no_events():
    rows = [dict(date=NOW.date(), time="美股收盤", title="三巫日")]
    output = mr._format_event_scenarios(rows, now_tpe=NOW)
    assert sw.NO_VERIFIED_EVENTS in output
    assert "無重大排程事件" not in output


def test_prompt_exact_boundary_no_elapsed_unknown_or_54_hour_event():
    rows = [dict(date=NOW.date(), time="06:00", title="已公布"),
            dict(date=dt.date(2026, 9, 16), time="07:32", title="精確邊界"),
            dict(date=dt.date(2026, 9, 16), time="13:30", title="台指期結算"),
            dict(date=NOW.date(), time="美股盤後", title="未定時"),
            dict(date=NOW.date(), time="25:00", title="無效時鐘")]
    original = copy.deepcopy(rows)
    output = mr._format_event_scenarios(rows, now_tpe=NOW)
    assert "精確邊界" in output
    assert all(text not in output for text in ("已公布", "台指期結算", "未定時", "無效時鐘"))
    assert rows == original


def test_render_does_not_trust_model_date_or_drop_later_scenarios():
    obj = fx.valid_analysis()
    obj['upcoming_event_scenarios'] = [dict(event="FOMC", when="今天", base_expectation="觀察利率"),
                                       dict(event="未知事件", when="2026-09-14 08:00", base_expectation="觀察需求")]
    packet = {'as_of': NOW.isoformat(), 'market': {'EVENT_CALENDAR': [
        dict(title="FOMC", date="2026-09-17", time="02:00")]}}
    original = copy.deepcopy(obj)
    output = analysis_render.render(obj, packet=packet)
    assert sw.NEAR not in output
    assert sw.LATER in output and sw.UNKNOWN in output
    assert "觀察利率" in output and "觀察需求" in output
    assert obj == original


def test_calendar_classification_handles_utc_and_mixed_events():
    packet = {'as_of': '2026-09-13T23:32:00Z', 'market': {'EVENT_CALENDAR': [
        dict(title='PPI', date='2026-09-14', time='20:30'),
        dict(title='ECB', date='2026-09-17', time='20:15')]}}
    assert sw.heading('PPI', packet) == sw.NEAR
    assert sw.heading('PPI 與 ECB', packet) == sw.MIXED
    assert sw.heading('PPI 與新品發布', packet) == sw.UNKNOWN
    packet['as_of'] = '2026-09-18 06:00'
    assert sw.heading('PPI', packet) == sw.PAST
    packet['as_of'] = '2026-09-14'
    assert sw.heading('PPI', packet) == sw.UNKNOWN


def test_exact_calendar_title_replaces_wrong_model_time_and_ambiguous_editions_do_not():
    packet = {'as_of': NOW.isoformat(), 'market': {'EVENT_CALENDAR': [
        dict(title='台指期結算', date='2026-09-16', time='13:30')]}}
    assert sw.heading('台指期結算', packet) == sw.LATER
    assert sw.display_time('台指期結算', '今天早上', packet) == '2026-09-16 13:30（台北）'
    packet['market']['EVENT_CALENDAR'].append(dict(title='台指期結算', date='2026-10-21', time='13:30'))
    assert sw.heading('台指期結算', packet) == sw.UNKNOWN
    assert '未核實' in sw.display_time('台指期結算', '今天', packet)


def test_latin_acronym_substrings_cannot_establish_event_identity():
    packet = {'as_of': NOW.isoformat(), 'market': {'EVENT_CALENDAR': [
        dict(title='PPI', date='2026-09-14', time='20:30')]}}
    assert sw.heading('Shipping disruption', packet) == sw.UNKNOWN
    assert '未核實' in sw.display_time('Shipping disruption', '明天', packet)
    packet['market']['EVENT_CALENDAR'][0]['title'] = 'Shipping disruption'
    assert sw.heading('PPI', packet) == sw.UNKNOWN
