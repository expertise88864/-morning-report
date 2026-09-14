import datetime as dt
from copy import deepcopy
from types import SimpleNamespace

import sports_news_selection as sn
from tennis_news_context import completed_preview

NOW = dt.datetime(2026, 9, 14, 7, 32, tzinfo=dt.timezone(dt.timedelta(hours=8)))
RESULT = dict(event_key='US Open', round='Final', winner='A. Zverev', loser='B. Shelton',
              played_at='2026-09-13T20:00:00Z', tour='ATP')
PREVIEW = '舒爾頓爭奪美網首個大滿貫冠軍 挑戰茲維列夫望終結美國男單荒'


def test_same_final_preview_does_not_consume_a_news_slot():
    rows = [{'title': title} for title in [PREVIEW, '美網Zverev擊敗Shelton奪冠', '中網新賽事', '球員新教練']]
    before = deepcopy(rows)
    selected, report = sn.select(rows, NOW, tennis_results=[RESULT])
    assert [r['title'] for r in selected] == [r['title'] for r in rows[1:]]
    assert report['excluded'] == 1
    assert rows == before


def test_ambiguous_or_other_event_news_remains():
    assert completed_preview(PREVIEW, [RESULT], NOW)
    for title in ['回顧美網決賽前舒爾頓挑戰茲維列夫',
                  '歷史上的今天：舒爾頓爭奪美網冠軍，挑戰茲維列夫',
                  '昔日舒爾頓爭奪美網冠軍，挑戰茲維列夫',
                  '當年舒爾頓爭奪美網冠軍，挑戰茲維列夫',
                  PREVIEW.replace('美網', '中網'), PREVIEW.replace('茲維列夫', '辛納'),
                  '2025年' + PREVIEW, '美網Zverev擊敗Shelton奪冠']:
        assert not completed_preview(title, [RESULT], NOW)
    assert completed_preview(PREVIEW + '盼創造歷史', [RESULT], NOW)
    assert completed_preview(PREVIEW + '追平當年紀錄', [RESULT], NOW)
    assert completed_preview(PREVIEW + '重現昔日榮光', [RESULT], NOW)
    for result in [dict(RESULT, round='Semifinal'), dict(RESULT, played_at=''),
                   dict(RESULT, played_at='2026-08-01T20:00:00Z'), dict(RESULT, loser='')]:
        assert not completed_preview(PREVIEW, [result], NOW)


def test_actual_sports_fetch_passes_existing_final_into_news_selection(monkeypatch):
    import morning_report as mr
    def offline(*args, **kwargs):
        raise mr.requests.exceptions.ConnectionError('offline fixture')
    monkeypatch.setattr(mr, '_http_get', offline)
    monkeypatch.setattr(mr.requests, 'get', offline)
    monkeypatch.setattr(mr.requests, 'post', offline)
    monkeypatch.setattr(mr, 'fetch_tennis_digest', lambda now: {'results': [RESULT]})
    monkeypatch.setattr(mr, 'SPORTS_NEWS_QUERIES', [('網球', '網球')])
    monkeypatch.setattr(mr, '_feedparser_parse_url_with_timeout', lambda *a, **kw:
                        SimpleNamespace(entries=[{'title': PREVIEW}, {'title': '美網Zverev擊敗Shelton奪冠'}]))
    result = mr.fetch_sports_digest(now_tpe=NOW)
    assert [r['title'] for r in result['news']['網球']] == ['美網Zverev擊敗Shelton奪冠']
    assert result['tennis']['results'] == [RESULT]
