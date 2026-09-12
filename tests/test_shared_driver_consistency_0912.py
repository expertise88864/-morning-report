"""The coverage and reference gates must agree on the same declared group."""
import pytest

import analysis_contracts as ac
import analysis_crosscheck as cc


def packet():
    return {'news_clusters': {'clusters': [{'cluster_id': c} for c in ['a', 'b', 'c']]},
            'event_graph': {'shared_driver_groups': [
                {'driver': 'us_monetary', 'label': '美國貨幣政策', 'cluster_ids': ['a', 'b', 'c']}]}}


def obj(driver, ids):
    return {'key_drivers': [{'cluster_id': 'a'}, {'cluster_id': 'b'}],
            'cross_market_synthesis': {'shared_driver_notes': [
                {'driver': driver, 'cluster_ids': ids, 'why_not_double_counted': '共同驅動只計一次'}]}}


@pytest.mark.parametrize('driver', ['us_monetary', '美國貨幣政策', '美國貨幣政策（us_monetary）',
                                    'us_monetary(美國貨幣政策)'])
def test_declared_equivalent_driver_names_pass_both_gates(driver):
    value, evidence = obj(driver, ['a', 'b', 'c']), packet()
    assert not ac.reference_problems(value, evidence)
    assert not cc.event_graph_problems(value, evidence)


@pytest.mark.parametrize('driver,ids', [('us_monetary', ['a', 'b']),
                                       ('ai_capex', ['a', 'b', 'c']),
                                       ('不是us_monetary', ['a', 'b', 'c'])])
def test_wrong_or_partial_group_is_never_marked_handled(driver, ids):
    value, evidence = obj(driver, ids), packet()
    assert ac.reference_problems(value, evidence)
    assert cc.event_graph_problems(value, evidence)
