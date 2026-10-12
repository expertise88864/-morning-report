import copy

from news_cluster_selection import project


def test_projection_keeps_identity_and_uses_editorial_order_without_mutation():
    info = {'clusters': [
        {'cluster_id': 'cluster:dropped', 'representative_source_id': 'dropped',
         'member_source_ids': ['dropped', 'a', 'b']},
        {'cluster_id': 'cluster:gone', 'representative_source_id': 'gone',
         'member_source_ids': ['gone']}],
        'required_cluster_ids': ['cluster:gone']}
    before = copy.deepcopy(info)
    result = project(info, [{'source_item_id': 'b'}, {'source_item_id': 'a'}])
    assert info == before
    assert result['required_cluster_ids'] == ['cluster:gone']
    assert result['clusters'] == [dict(info['clusters'][0],
        representative_source_id='b', member_source_ids=['a', 'b'])]


def test_surviving_representative_is_preserved():
    info = {'clusters': [{'cluster_id': 'cluster:a', 'representative_source_id': 'a',
                          'member_source_ids': ['a', 'b']}]}
    assert project(info, [{'source_item_id': 'b'}, {'source_item_id': 'a'}]) == info
