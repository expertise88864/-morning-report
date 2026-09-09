"""Production-shaped anonymous MOPS announcements retain issuer provenance."""
import copy

import pytest

import evidence_packet as ep
import news_memory as memory
import news_normalize
import official_announcements as official
from analysis_render_depth import news_subject
from reader_editorial import recurring_revenue


def raw(code='2884', title='公告本公司115年8月份自結合併營收', **extra):
    return dict(code=code, title=title, summary='', link=official.MOPS_URL,
                published='2026-09-08T05:00:00+08:00', **extra)


def test_actual_packet_archive_and_reader_identity():
    source = raw()
    packet = ep.build({'TW_MOPS': [source]}, {}, {}, [],
        [{'code': '2884', 'name': '玉山金', 'industry': '金融保險業'}], {},
        as_of='2026-09-09T06:00:00+08:00', sanitize=str)
    item = packet['news'][0]
    assert item['title'] == source['title']  # No fabricated issuer in source headline.
    assert official.issuer(item) == '2884'
    assert news_subject(item, packet)['name'] == '玉山金'
    rows, skipped = memory.observations(official.reader_sources([source]),
        '2026-09-08T06:00:00+08:00', sanitize=str)
    assert not any(skipped.values())
    memory._validate(rows[0])
    contexts, history = memory.retrieve(packet['news'], rows, packet['as_of'])
    assert contexts[item['source_item_id']]['evidence_ids'] == [rows[0]['evidence_id']]
    assert recurring_revenue(item, history, news_subject(item, packet)['name'])
    # No universe entry is required to compare a validated issuer code.
    assert recurring_revenue(item, history, '')
    import editorial_priority
    packet['historical_sources'] = history
    packet['research']['contexts'] = contexts
    packet['news'].append({'source_item_id': 'new', 'title': '航運新訂單簽署'})
    cards = [{'source_item_id': item['source_item_id']}, {'source_item_id': 'new'}]
    assert editorial_priority.order(cards, packet) == cards[::-1]
    changed = copy.deepcopy(rows[0])
    changed['announcement_issuer'] = '2882'
    with pytest.raises(ValueError, match='fingerprint'):
        memory._validate(changed)


def test_same_title_different_issuers_and_revisions_survive_normalization():
    sources = official.reader_sources([raw(), raw('2882'), dict(raw(), summary='新增重大資訊')])
    news, _, _ = news_normalize.normalize_news(sources, str)
    assert len(news) == 3
    rows, _ = memory.observations(sources, '2026-09-08T06:00:00+08:00', sanitize=str)
    assert len(rows) == 3
    one = next(n for n in news if official.issuer(n) == '2884' and not n['summary'])
    other = [r for r in rows if official.issuer(r) == '2882']
    assert not recurring_revenue(one, other, '')
    for title in ('公告本公司115年9月份自結合併營收',
                  '公告本公司115年8月份自結合併營收及未知重大事件'):
        assert not recurring_revenue(dict(one, title=title), rows, '')
    assert not recurring_revenue(dict(one, summary='年增20%'), rows, '')


def test_editorial_tags_and_lookalike_urls_never_establish_official_identity():
    item = dict(raw(), source='MOPS', official=True, entities=['2884'])
    assert official.issuer(item) == ''
    assert official.reader_sources([dict(raw(), link='https://mops.twse.com.tw.evil.example/')]) == []
    packet = {'news': [dict(item, source_item_id='n1')],
              'tw_universe': [{'code': '2884', 'name': '玉山金'}]}
    assert news_subject({'source_item_id': 'n1', 'affected_assets': [{'asset_id': '2884'}]}, packet)['name'] == ''


def test_old_archive_fingerprints_remain_compatible():
    rows, _ = memory.observations([dict(raw(), source='MOPS')],
                                  '2026-09-08T06:00:00+08:00', sanitize=str)
    assert 'announcement_issuer' not in rows[0]
    memory._validate(rows[0])


def test_runtime_archives_official_adapter_without_touching_prediction_inputs(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import news_research_runtime as runtime
    ctx = SimpleNamespace(news=[], tw_mops=[raw()], quotes={},
                          recorder=SimpleNamespace(data={}, degraded=[]))
    original = copy.deepcopy(ctx.tw_mops)
    monkeypatch.setattr(runtime, 'search_plan', lambda *a, **kw: [])
    def forbidden(*args, **kwargs):
        raise AssertionError('No external requests in offline validation')
    runtime.enrich(ctx, tmp_path, sanitize=str,
        atomic_write=lambda path, data: path.write_bytes(data), fetch_feed=forbidden,
        make_url=forbidden, entry_time=forbidden, entry_source=forbidden,
        time_left=lambda: 999, reserve=0)
    rows = memory.read_partition(next(tmp_path.glob('*.json.gz')))
    assert official.issuer(rows[0]) == '2884'
    assert ctx.tw_mops == original and ctx.news == []
