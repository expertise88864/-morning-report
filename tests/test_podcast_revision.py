import copy

import analysis_depth
import podcast_revision as pr
from test_podcast_comparison import fixture


def test_unseen_news_cannot_gain_a_new_comparison_even_if_news_id_was_seen_before():
    _, row = fixture()
    obj = {'top_news_analysis': [row]}
    assert pr.repair_problems(obj, set(), set())
    assert pr.repair_problems(obj, {'n1'}, set()) == []
    assert pr.repair_problems(obj, None, set()) == []


def test_format_only_may_keep_but_not_rewrite_full_context_comparison():
    _, row = fixture()
    obj = {'top_news_analysis': [row]}
    original = pr.signatures(obj)
    assert pr.repair_problems(obj, set(), original) == []
    row['podcast_comparisons'][0]['comparison'] = '換成另一個結論'
    assert pr.repair_problems(obj, set(), original)


def test_deepening_cannot_silently_drop_one_of_the_compared_episodes():
    _, row = fixture()
    before = {'top_news_analysis': [row]}
    after = copy.deepcopy(before)
    after['top_news_analysis'][0]['podcast_comparisons'] = []
    assert any('podcast:' in e for e in analysis_depth.news_regressions(before, after))
    assert analysis_depth.news_regressions(before, before) == []


def test_bad_shape_does_not_crash_revision_tracking_before_schema_rejection():
    for value in (None, 'bad', 3, [None]):
        assert pr.signatures({'top_news_analysis': value}) == set()
        assert pr.signatures({'top_news_analysis': [{'podcast_comparisons': value}]}) == set()
