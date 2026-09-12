"""Same-day corroboration must not masquerade as longitudinal development."""
from copy import deepcopy

import pytest
import news_temporal_context as temporal
import news_research_context as research
from test_news_research import article, observation, packet


@pytest.mark.parametrize('day,expected', [(1, 'earlier_reporting'),
    (5, 'same_day_reporting'), (6, 'later_reporting')])
def test_publication_day_not_ingestion_time_defines_relation(day, expected):
    assert temporal.relation(article(5), observation(day)) == expected


def test_timezone_and_missing_date_are_not_guessed():
    assert temporal.relation({'published': '2026-09-05T01:00:00+08:00'},
                             {'published_at': '2026-09-04T17:00:00Z'}) == 'same_day_reporting'
    assert temporal.relation(dict(article(5), date_missing=True), observation(1)) == 'unknown'


def test_real_pipeline_retains_same_day_sources_but_labels_them_correctly():
    archive = [observation(5, title='台積電高雄廠擴建工程最新進度')]
    before = deepcopy(archive)
    pk = packet(archive=archive)
    ids = pk['research']['contexts']['n1']['evidence_ids']
    assert ids
    assert set(pk['research']['contexts']['n1']['publication_relations'].values()) == {'same_day_reporting'}
    text = research.history_prose({'source_item_id': 'n1'}, pk)
    assert text.startswith('同日相關報導:') and '原始報導' in text
    assert archive == before
    counts = research.metrics({}, pk)['publication_relation_articles']
    assert counts['same_day_reporting'] == 1 and counts['earlier_reporting'] == 0


def test_label_is_derived_not_trusted_from_context_metadata():
    pk = packet()
    pk['research']['contexts']['n1']['publication_relations'] = {}
    assert research.history_prose({'source_item_id': 'n1'}, pk).startswith('先前報導脈絡:')


def test_both_model_paths_receive_the_same_temporal_rule():
    import prompt_profiles as profiles
    rule = '同日來源可比較共同說法與分歧'
    assert rule in profiles.LUNA_DEVELOPER_INSTRUCTIONS
    legacy = research.legacy_block([article(5)], [observation()],
                                   '2026-09-06T07:00:00+08:00', sanitize=str)
    assert rule in legacy
    assert legacy.index(rule) < legacy.index('<UNTRUSTED_SOURCE_DATA>')


def test_budget_drops_relation_ids_together_with_sources(monkeypatch):
    monkeypatch.setattr(research, 'MAX_HISTORY_CHARS', 0)
    pk = packet()
    assert pk['research']['contexts']['n1']['publication_relations'] == {}
    assert pk['historical_sources'] == []


def test_same_day_advice_and_schema_do_not_require_longitudinal_change():
    import analysis_schema as schema
    pk = packet(archive=[observation(5, title='台積電高雄廠擴建工程最新進度')])
    advice = research.advisories({'top_news_analysis': [{'source_item_id': 'n1'}]}, pk)
    assert advice and '同日相關報導' in advice[0]
    assert '有可追溯的跨日報導' not in advice[0]
    description = schema.ANALYSIS_OUTPUT_SCHEMA['properties']['top_news_analysis']['items']['properties']['historical_context']['properties']['evolution']['description']
    assert '同日比較共同說法與分歧' in description


def test_render_dates_and_order_use_instants_and_taipei_days():
    pk = packet()
    pk['historical_sources'] = [observation(1), observation(2), observation(3)]
    pk['research']['contexts']['n1']['evidence_ids'] = [s['evidence_id'] for s in pk['historical_sources']]
    sources = pk['historical_sources']
    assert len(sources) == 3
    for source, time in zip(sources, ['2026-09-04T17:00:00Z',
                                    '2026-09-05T08:00:00+08:00',
                                    '2026-09-05T02:00:00Z']):
        source['published_at'] = time
    text = research.history_prose({'source_item_id': 'n1'}, pk)
    assert text.startswith('同日相關報導:')
    assert '2026-09-04' not in text
    assert sources[0]['url'] in text and sources[2]['url'] in text
    assert sources[1]['url'] not in text
