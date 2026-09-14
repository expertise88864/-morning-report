"""Budget pressure must not erase material financial coverage obligations."""
import copy

import finance_editorial as finance
import news_coverage as coverage


def article(sid, title):
    return dict(source_item_id=sid, title=title, published='2026-09-14', source='測試')


def test_material_event_beats_routine_group_matches_with_fixed_budget():
    rows = [article('a', '國泰金個股概覽'), article('b', '國泰人壽捐血活動'),
            article('c', '國泰金公布季度獲利')]
    before = copy.deepcopy(rows)
    kept, _ = coverage.select(rows, set(), 2)
    assert 'c' in {r['source_item_id'] for r in kept}
    assert finance.analysis_candidates(kept) == [['c']]
    assert len(kept) == 2 and rows == before


def test_forced_routine_does_not_satisfy_material_reserve():
    rows = [article('a', '國泰人壽捐血活動'), article('b', '科技新聞'),
            article('c', '國泰金公布季度獲利')]
    kept, _ = coverage.select(rows, {'a'}, 2)
    assert {r['source_item_id'] for r in kept} == {'a', 'c'}
    kept, _ = coverage.select(rows, {'a', 'b'}, 2)
    assert {r['source_item_id'] for r in kept} == {'a', 'b'}


def test_material_reserves_balance_groups_not_repeated_activity():
    rows = [article('a', '中國信託藝術節'), article('b', '國泰人壽捐血活動'),
            article('c', '中信金公布季度獲利'), article('d', '國泰金公布季度獲利')]
    kept, _ = coverage.select(rows, set(), 2)
    assert {r['source_item_id'] for r in kept} == {'c', 'd'}


def test_regulatory_investigation_is_material_without_asserting_guilt():
    row = article('r', '金管會擴大金檢徹查國泰金')
    before = copy.deepcopy(row)
    assert finance.material_groups(row) == {finance.GROUPS[1]}
    assert row == before  # Selection topic is not a finding of wrongdoing.


def test_no_material_keeps_original_order_and_joint_story_uses_one_slot():
    rows = [article('a', '國泰人壽捐血活動'), article('b', '國泰金數位體驗活動')]
    kept, _ = coverage.select(rows, set(), 1)
    assert kept == rows[:1]
    joint = article('j', '中信金與國泰金公布資本計畫')
    kept, _ = coverage.select([*rows, joint], set(), 1)
    assert kept == [joint]
    assert finance.analysis_candidates(kept) == [['j'], ['j']]


def test_merged_originals_retain_own_date_for_material_reserve():
    routine = article('a', '國泰人壽捐血活動')
    merged = article('m', '金融業動態')
    merged['finance_headlines'] = [dict(title='國泰金公布獲利', published='2026-09-13')]
    kept, _ = coverage.select([routine, merged], set(), 1)
    assert kept == [merged]
    merged['finance_headlines'][0]['published'] = ''
    kept, _ = coverage.select([routine, merged], set(), 1)
    assert kept == [routine]


def test_material_order_remains_input_quality_order():
    rows = [article('r', '國泰人壽捐血活動'), article('a', '國泰金公布獲利'),
            article('b', '國泰金監理新規因應')]
    kept, _ = coverage.select(rows, set(), 1)
    assert kept == [rows[1]]
