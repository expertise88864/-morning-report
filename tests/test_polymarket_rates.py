import datetime as dt

import morning_report as mr
import polymarket_rates as rates


NOW = dt.datetime(2026, 10, 13, 6, 0, tzinfo=mr.TPE)


def event(month, year, number, *, closed=False):
    return {"title": f"Fed Decision in {month}?", "slug": f"fed-{year}-{number}",
            "endDate": f"{year}-{number:02d}-28T00:00:00Z", "closed": closed}


def helpers(calls, warnings):
    def outright(slug, zh, **kwargs):
        calls.append((slug, kwargs))
        return [{"name": "升息1碼", "prob": 1, "hist_key": slug}]
    return dict(outright=outright, annotate=lambda *_: None,
                format_line=mr._poly_prob_line, is_future=mr._poly_event_is_future,
                warn=warnings.append)


def test_three_future_meetings_include_year_and_small_outcomes():
    calls, warnings = [], []
    events = [event('January', 2027, 1), event('September', 2026, 9),
              event('December', 2026, 12), event('October', 2026, 10),
              event('February', 2027, 2), event('October', 2026, 10)]
    rows = rates.decision_rows(NOW, search=lambda *_, **kw: events,
        months=mr._POLY_MONTH_ZH, outcomes=mr._POLY_FED_OUTCOME_ZH, **helpers(calls, warnings))
    assert [r['label'] for r in rows] == ['Fed 2026年10月決議', 'Fed 2026年12月決議', 'Fed 2027年1月決議']
    assert len(calls) == 3 and all(c[1] == {'top': 5, 'min_prob': 0.0} for c in calls)
    assert all('升息1碼 1%' in r['detail'] for r in rows)
    assert not warnings


def test_bad_and_closed_meetings_do_not_become_rows():
    calls, warnings = [], []
    rows = rates.decision_rows(NOW, search=lambda *_, **kw: [None, {},
        event('December', 2026, 12, closed=True),
        {'title': 'Fed Decision in January?', 'endDate': 'bad', 'slug': 'bad'}],
        months=mr._POLY_MONTH_ZH, outcomes=mr._POLY_FED_OUTCOME_ZH, **helpers(calls, warnings))
    assert not rows and not calls


def test_provider_failure_is_observable_and_does_not_lose_other_meeting():
    calls, warnings = [], []
    opts = helpers(calls, warnings)
    def sometimes(slug, *args, **kwargs):
        if slug == 'fed-2026-10':
            raise TimeoutError('offline fixture')
        return [{'name': '利率不變', 'prob': 50}]
    opts['outright'] = sometimes
    rows = rates.decision_rows(NOW, search=lambda *_, **kw: [event('October', 2026, 10), event('December', 2026, 12)],
        months=mr._POLY_MONTH_ZH, outcomes=mr._POLY_FED_OUTCOME_ZH, **opts)
    assert len(rows) == 1 and '12月' in rows[0]['label']
    assert len(warnings) == 1


def test_total_contract_is_units_not_meetings_and_expires():
    calls, warnings = [], []
    opts = helpers(calls, warnings)
    def outright(slug, zh, **kwargs):
        assert slug == rates.HIKE_TOTAL_SLUG
        return [{'name': zh['2 (50 bps)'], 'prob': 70}]
    opts['outright'] = outright
    def events(_):
        return [{'endDate': '2027-01-01T04:59:00Z'}]
    rows = rates.total_hikes_row(NOW, events=events, **opts)
    assert rows[0]['detail'] == '累計2碼 70%'
    assert '1碼=25基點' in rows[0]['label']
    assert rates.total_hikes_row(NOW.replace(year=2027), events=events, **opts) == []
    before = dt.datetime(2027, 1, 1, 6, tzinfo=mr.TPE)
    assert rates.total_hikes_row(before, events=events, **opts)
    assert rates.total_hikes_row(before.replace(hour=13), events=events, **opts) == []
    assert rates.total_hikes_row(NOW, events=lambda _: [{'closed': True}], **opts) == []


def test_pulse_retains_three_named_parties_even_with_small_quotes(monkeypatch):
    monkeypatch.setattr(mr, '_poly_search_events', lambda *a, **kw: [])
    monkeypatch.setattr(mr, '_poly_annotate_deltas', lambda *a, **kw: None)
    def events(params):
        if params['slug'] == '2026-taiwanese-local-elections-party-winner':
            return [{'markets': [{'groupItemTitle': name, 'outcomePrices': prices}
                for name, prices in [('Kuomintang (KMT)', '["0.85", "0.15"]'),
                    ('Democratic Progressive Party (DPP)', '["0.14", "0.86"]'),
                    ('Taiwan People’s Party (TPP)', '["0.01", "0.99"]'),
                    ('Party A', '["0.9", "0.1"]')]]}]
        return []
    monkeypatch.setattr(mr, '_poly_events', events)
    rows = mr.fetch_polymarket_pulse(NOW)
    party = next(row for row in rows if '最多當選席次政黨' in row['label'])
    assert party['detail'] == '國民黨 85%・民進黨 14%・民眾黨 1%'
