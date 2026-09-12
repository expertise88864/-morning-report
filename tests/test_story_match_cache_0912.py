"""Memoize repeated identity work without dropping any matching candidate."""
import story_ledger as sl


def test_identity_is_evaluated_once_per_distinct_entity_per_call(monkeypatch):
    calls = []
    original = sl._si.same_subject

    def counted(left, right):
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr(sl._si, 'same_subject', counted)
    rows = {str(i): {'entity': '2882', 'headline': '金控公告'} for i in range(100)}
    event = {'entity': '2330', 'title': '台積電先進封裝擴產計畫'}
    assert sl._match_open_story(event, rows) == ''
    assert calls == [('2882', '2330')]
    sl._match_open_story(event, rows)
    assert calls == [('2882', '2330')] * 2  # No global cache across reports.


def test_later_candidate_still_wins_and_entityless_candidate_is_included():
    event = {'entity': '2330', 'entity_name': '台積電', 'title': '台積電先進封裝擴產計畫'}
    rows = {'unrelated': {'entity': '2330', 'headline': '台積電董事會股利公告'},
            'match': {'entity': '2330', 'headline': event['title']}}
    assert sl._match_open_story(event, rows) == 'match'
    rows['match']['entity'] = ''
    assert sl._match_open_story(event, rows) == 'match'
