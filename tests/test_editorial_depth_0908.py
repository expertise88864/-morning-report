"""Editorial acceptance: no new truth claims, investment authority or paid calls."""
import copy
import json

import content_overlap as overlap
import editorial_priority as priority
import news_memory_selection as history
import podcast_overlap


FACT = '台積電公布高雄廠擴建工程已完成第一階段施工，下一階段將建置生產設備'


def episode(points):
    return {'show': '節目A', 'id': 'unchanged', 'digest': {'summary_points': points,
            'market_view': '主持人觀點，不是本報判斷'}}


def test_podcast_compares_real_report_and_preserves_novel_view_and_original():
    view = '主持人認為設備到廠並不代表能立刻量產，良率與客戶驗證才是關鍵'
    episodes = [episode([FACT, view, view])]
    original = copy.deepcopy(episodes)
    audit = {}
    result = podcast_overlap.project(episodes, '# 新聞\n'+FACT+'。', diagnostics=audit)
    assert result[0]['digest']['summary_points'] == [view]
    assert episodes == original and result[0]['id'] == 'unchanged'
    assert audit['duplicate_points'] == 2
    assert result[0]['digest']['market_view'] == original[0]['digest']['market_view']


def test_short_caveat_is_not_discarded():
    point = FACT + '。但未證實。'
    assert not overlap.covered(point, overlap.sentences(FACT))
    assert podcast_overlap.project([episode([point])], FACT)[0]['digest']['summary_points'] == [point]
    assert not overlap.duplicate(FACT+' https://example.com/a，尚未證實', FACT+' https://example.com/a')
    assert not overlap.covered(FACT+'?', overlap.sentences(FACT))
    assert not overlap.covered('-10%是本次公司公告營收的年變化幅度', overlap.sentences('10%是本次公司公告營收的年變化幅度'))


def test_numbers_negation_and_new_conclusion_are_not_duplicates():
    assert not overlap.duplicate('成長1.0%', '成長10%')
    assert not overlap.duplicate('成長10%', '成長10')
    assert not overlap.duplicate('報酬+10%', '報酬-10%')
    assert not overlap.duplicate('收入100元', '收入$100元')
    assert not overlap.duplicate('the rapist', 'therapist')
    assert not overlap.duplicate('成長する10%', '成長しない10%')
    assert not overlap.duplicate('да 10%', 'нет 10%')
    assert not overlap.duplicate('數值<1', '數值>1')
    assert not overlap.duplicate(FACT+'，訂單增加', FACT+'，訂單減少')
    assert not overlap.duplicate(FACT+'，預估營收成長10%', FACT+'，預估營收成長20%')
    assert not overlap.duplicate(FACT, FACT.replace('已完成', '未完成'))
    assert not overlap.covered(FACT+'。主持人認為高昂的設備折舊可能侵蝕未來的獲利空間',
                               overlap.sentences(FACT))


def test_cross_episode_overlap_keeps_attribution_and_one_point_when_all_repeat():
    unique = '主持人指出供應商付款週期拉長可能影響設備商短期現金流量'
    result = podcast_overlap.project([episode([FACT]), episode([FACT, unique]), episode([FACT])], '')
    assert [e['digest']['summary_points'] for e in result] == [[FACT], [unique], [FACT]]


def test_concrete_event_outweighs_promotional_preview_without_mutating_authority():
    packet = {'news': [{'source_item_id': 'preview', 'title': '新品直播倒數優惠一次看'},
                       {'source_item_id': 'event', 'title': '公司宣布停產並召回產品'}],
              'top_events': {'top_cluster_ids': ['p', 'e']},
              'news_clusters': {'clusters': [
                  {'cluster_id': 'p', 'member_source_ids': ['preview']},
                  {'cluster_id': 'e', 'member_source_ids': ['event']}]}}
    before = copy.deepcopy(packet)
    assert priority.order([{'source_item_id': s} for s in ('preview', 'event')], packet)[0]['source_item_id'] == 'event'
    assert packet == before


def test_different_analysis_of_same_story_survives():
    packet = {'news': [{'source_item_id': s, 'title': FACT} for s in ('a', 'b')]}
    cards = [{'source_item_id': s, 'why_it_matters': FACT} for s in ('a', 'b')]
    assert len(priority.distinct(cards, packet)) == 1
    cards[1]['why_it_matters'] += '。但尚未揭露資本支出。'
    assert len(priority.distinct(cards, packet)) == 2


def test_history_preserves_origin_and_latest_distinct_change_across_weeks():
    rows = [{'title': '高雄廠工程', 'excerpt': f'階段{i}完成', 'published_at': d,
             'evidence_id': str(i), 'source_group': 'wire'} for i, d in enumerate([
                 '2026-08-01', '2026-08-08', '2026-08-15', '2026-08-22', '2026-09-01'])]
    result = history.select(rows, 3)
    assert result[0] == rows[0] and result[-1] == rows[-1]
    assert len(result) == 3
    assert history.select(rows, 1) == rows[:1]


def test_history_reprints_do_not_consume_every_slot():
    rows = [{'title': FACT, 'excerpt': '', 'published_at': f'2026-09-0{i}',
             'evidence_id': str(i)} for i in range(1, 5)]
    assert history.select(rows, 6) == rows[:1]
    rows[-1]['title'] = FACT.replace('已完成', '尚未完成')
    assert len(history.select(rows, 6)) == 2


def test_no_internal_preference_or_diagnostics_in_podcast_copy():
    result = podcast_overlap.project([episode([FACT])], FACT, diagnostics={})
    assert 'duplicate_points' not in json.dumps(result)


def test_deep_topic_keeps_one_representative_not_every_reprint():
    cards = [{'source_item_id': s} for s in ['b', 'a', 'c']]
    packet = {'research': {'deep_topics': [{'member_source_ids': ['a', 'b']}]}}
    assert priority.required_representatives(cards, packet) == cards[:1]


def test_global_dedup_preserves_required_topic_members(monkeypatch):
    import reader_selection
    monkeypatch.setattr(reader_selection, 'article_is_tech', lambda *_: True)
    cards = [{'source_item_id': s} for s in ['outside', 'a', 'b', 'c']]
    packet = {'news': [{'source_item_id': s, 'title': FACT} for s in ['outside', 'a', 'b', 'c']],
              'research': {'deep_topics': [{'member_source_ids': ['a', 'b']},
                                          {'member_source_ids': ['c']}]}}
    selected, _ = reader_selection.select_cards(cards, packet)
    assert [c['source_item_id'] for c in selected] == ['a', 'c']


def test_podcast_failure_is_visible_and_keeps_original(monkeypatch):
    import morning_report as mr
    def broken(*args, **kwargs):
        raise ValueError('fixture')
    monkeypatch.setattr(podcast_overlap, 'project', broken)
    episodes = [episode([FACT])]
    assert podcast_overlap.for_report({'PODCAST_DIGEST': episodes}, FACT, mr._safe_block) is episodes
    assert mr._DEGRADED_STEPS


def test_final_email_uses_projected_points_and_preserves_digest(monkeypatch):
    import morning_report as mr
    from test_golden_faults import _golden_quotes
    from bs4 import BeautifulSoup
    quotes = _golden_quotes()
    distinct = '主持人認為設備供應商付款條件可能影響未來現金流量'
    quotes['PODCAST_DIGEST'] = [episode([FACT, distinct])]
    original = copy.deepcopy(quotes['PODCAST_DIGEST'])
    monkeypatch.setenv('EMAIL_OVERFLOW_MODE', 'full')
    html = mr.render_html(quotes, {'error': 'fixture'}, {'error': 'fixture'},
                          '## 八、科技板塊脈動\n'+FACT+'。', '2026-09-08', '每日報')
    text = BeautifulSoup(html, 'html.parser').get_text()
    assert text.count(FACT) == 1 and distinct in text
    assert quotes['PODCAST_DIGEST'] == original
    assert quotes['PODCAST_SHOWN_EPISODES'][0]['digest']['summary_points'] == [distinct]


def test_podcast_retains_point_when_matching_analysis_is_capped(monkeypatch):
    import morning_report as mr
    from test_golden_faults import _golden_quotes
    from bs4 import BeautifulSoup
    quotes = _golden_quotes()
    distinct = '主持人認為設備供應商付款條件可能影響未來現金流量'
    quotes['PODCAST_DIGEST'] = [episode([FACT, distinct])]
    monkeypatch.setenv('EMAIL_OVERFLOW_MODE', 'full')
    analysis = '## 八、科技板塊脈動\n' + ('其他新聞內容。\n\n' * 9000) + FACT + '。'
    assert FACT not in mr._cap_analysis_text(analysis)
    html = mr.render_html(quotes, {'error': 'fixture'}, {'error': 'fixture'},
                          analysis, '2026-09-08', '每日報')
    text = BeautifulSoup(html, 'html.parser').get_text()
    assert FACT in text and distinct in text
    assert quotes['PODCAST_SHOWN_EPISODES'][0]['digest']['summary_points'] == [FACT, distinct]
