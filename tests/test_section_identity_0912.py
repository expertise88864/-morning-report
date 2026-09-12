"""Both production section routes must use the same declared industry data."""
import pytest

import morning_report as mr
from reader_selection import article_is_tech


@pytest.mark.parametrize('industry', ['半導體業', '24', 24])
@pytest.mark.parametrize('entity', ['2408', '南亞科'])
def test_memory_issuer_name_and_code_use_declared_industry(industry, entity):
    universe = [{'code': '2408', 'name': '南亞科', 'industry': industry}]
    event = {'entity': entity, 'title': '南亞科公布營收', 'event_type': 'earnings'}
    assert mr.assign_event_sections([event], universe)[0]['section'] == mr._SECTION_TECH
    packet = {'tw_universe': universe, 'news': [dict(event, source_item_id='n1', entities=[entity])]}
    assert article_is_tech({'source_item_id': 'n1'}, packet)


@pytest.mark.parametrize('entity', ['2882', '國泰金'])
def test_financial_issuer_is_not_reclassified_by_incidental_ai(entity):
    universe = [{'code': '2882', 'name': '國泰金', 'industry': '金融保險業'}]
    event = {'entity': entity, 'title': '國泰金導入AI服務', 'event_type': 'general'}
    assert mr.assign_event_sections([event], universe)[0]['section'] == mr._SECTION_OTHER
    packet = {'tw_universe': universe, 'news': [dict(event, source_item_id='n1', entities=[entity])]}
    assert not article_is_tech({'source_item_id': 'n1'}, packet)


def test_world_priority_and_unknown_subject_are_preserved():
    universe = [{'code': '2408', 'name': '南亞科', 'industry': '24'}]
    rows = mr.assign_event_sections([
        {'entity': '南亞科', 'title': '出口管制公告', 'event_type': 'export_controls'},
        {'entity': '', 'title': '記憶體市場綜合評論'},
        {'entity': '未知公司', 'title': 'AI採購公告'},
    ], universe)
    assert [r['section'] for r in rows] == [mr._SECTION_WORLD, mr._SECTION_TOP3, mr._SECTION_OTHER]
