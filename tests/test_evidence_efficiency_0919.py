from copy import deepcopy

import payload_budget
from numeric_evidence_audit import summarize


def test_small_packet_still_removes_plumbing_without_losing_evidence():
    packet = {'news': [{'source_item_id': 'n1', 'summary': '完整內容'}],
              'tw_universe': [{'code': 'TEST', 'price_forecast': {
                  'model_version': 'internal' * 100, 'expected_price': 101,
                  'error': 'unavailable', 'as_of': '2026-09-19'}}]}
    before = deepcopy(packet)
    manifest = {}
    result = payload_budget.apply(packet, manifest)
    assert packet == before
    assert result['news'] == packet['news']
    assert 'gap:payload_compacted' not in result.get('required_disclosures', {})
    assert result['tw_universe'][0]['price_forecast'] == {
        'expected_price': 101, 'error': 'unavailable', 'as_of': '2026-09-19'}
    assert manifest['llm']['payload_compact']['chars_after'] < manifest['llm']['payload_compact']['chars_before']
    assert 'numeric_evidence_audit' in manifest['llm']


def test_packet_time_is_not_observation_time():
    got = summarize({'as_of': '2026-09-19T06:00:00+08:00',
                     'market': {'QQQ': {'close': 500}}})
    assert got['numeric_entries'] == 1
    assert got['missing_observation_time'] == 1


def test_numeric_source_clock_and_missing_fields_are_visible():
    packet = {'as_of': '2026-09-19T06:00:00+08:00', 'news': [{
        'source_item_id': 'n1', 'published': '2026-09-20T01:00:00+08:00',
        'numeric_facts': [{'value': 5, 'unit': '%'}]}]}
    before = deepcopy(packet)
    got = summarize(packet)
    assert got['future_source_time'] == got['missing_source'] == 1
    assert packet == before
    assert 'n1' not in str(got)
