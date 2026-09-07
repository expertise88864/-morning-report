"""User email regressions: presentation must not expose internal contracts."""
import copy
import pytest

import analysis_render as ar
import analysis_render_depth as ard
import event_identity as ei
import fixtures_analysis as fx
import reader_prose as rp
import render_utils as ru


def test_stance_does_not_become_last_upcoming_field_and_audit_is_private():
    obj = fx.valid_analysis()
    obj['stance']['rationale'] = '抄錄系統計分 STANCE_PY.total = 2（label：中性）。利率壓抑估值。'
    obj['upcoming_event_scenarios'] = [{'event': 'CPI', 'invalidation': '延期', 'base_expectation': '符合預期'}]
    original = copy.deepcopy(obj)
    md = ar.render(obj)
    assert '立場：' + obj['stance']['label'] in md
    assert '立場：失效條件' not in md
    assert 'STANCE_PY' not in md and '利率壓抑估值' in md
    for title in ('資料缺口', '本段的保留事項', '情境與觸發條件',
                  '證據衝突與調和', '昨日觀察點回顧', '觀察觸發點'):
        assert '## ' + title not in md
    assert obj == original


def test_supporting_conditions_are_in_conclusion_not_lost():
    md = rp.public_sections('## 情境與觸發條件\n- **偏多**:通膨降溫\n  - 什麼情況代表它成立:CPI低於預期\n\n'
                            '## 我的明確立場\n立場：中性\n\n## 一句話總結\n等待數據')
    stance = md.split('## 我的明確立場')[1].split('## 一句話總結')[0]
    assert '通膨降溫' in stance and 'CPI低於預期' in stance


def test_categories_limit_and_technology_without_registry_subject():
    news = [{'source_item_id': str(i), 'title': '台積擴產' if i < 9 else '航運運價'} for i in range(18)]
    cards = [{'source_item_id': str(i), 'why_it_matters': '有新的需求'} for i in range(18)]
    pk = {'news': news}
    selected, omitted = rp.select_cards(cards, pk)
    assert len(selected) == 12 and len(omitted) == 6
    assert sum(rp.article_is_tech(c, pk) for c in selected) == 6
    for title in ('美光重返高點', '聯亞營收成長', '廠務工程在手訂單', 'Claude 模型發布'):
        assert rp.article_is_tech({'source_item_id': 'x'}, {'news': [{'source_item_id': 'x', 'title': title}]})


def test_headline_link_and_caveat_survive_safe_html_as_small_text():
    n = {'source_item_id': 'x', 'why_it_matters': '需求成長',
         'mechanism_steps': [{'from_what': '需求', 'to_what': '出貨'}]}
    pk = {'news': [{'source_item_id': 'x', 'title': '模型發布', 'url': 'https://example.com/news?a=1&b=2'}]}
    md = ard._news_line(n, pk)
    assert '\n\n傳導:' in md
    html = ru._style_analysis_html(ru._md_to_html(md + '\n\n保留:單一來源。'))
    assert 'href="https://example.com/news?a=1&amp;b=2"' in html
    assert 'font-size:12px' in html
    assert '<p style="font-size:12px!important;color:#64748b' in html


def test_timeline_hint_keeps_complete_title():
    title = '美軍證實攻擊3艘伊朗油輪，回應伊朗向商船開火'
    assert ei._title_hint(title) == title


def test_ai_model_news_reaches_sanitized_packet_and_reserve():
    import evidence_packet as ep
    quote = {'AI_MODELS': {'news': [{'title': 'Claude 模型發布 external-marker',
              'source': 'AI模型新聞', 'published': '2026-09-07T06:00:00+08:00',
              'link': 'https://example.com/model', 'summary': '新的模型評測'}]}}
    pk = ep.build(quote, {}, {}, [], [], {}, sanitize=lambda s: s.replace('external-marker', 'cleaned'))
    item = pk['news'][0]
    assert item['url'] == 'https://example.com/model'
    assert 'external-marker' not in item['title'] and 'cleaned' in item['title']
    assert 'tech:ai-models' in item['coverage_buckets']
    cards = [{'source_item_id': str(i)} for i in range(7)] + [{'source_item_id': item['source_item_id']}]
    pk['news'] += [{'source_item_id': str(i), 'title': '半導體產能'} for i in range(7)]
    selected, _ = rp.select_cards(cards, pk)
    assert len(selected) == 6 and selected[0]['source_item_id'] == item['source_item_id']


def test_financial_subject_with_ai_words_stays_other_and_both_groups_reserved():
    pk = {'news': [{'source_item_id': str(i), 'title': '航運運價'} for i in range(7)] + [
          {'source_item_id': 'c', 'title': '中信金 AI 營運成長', 'published': '2026-09-07'},
          {'source_item_id': 'k', 'title': '國泰金 獲利', 'published': '2026-09-07'}]}
    selected, _ = rp.select_cards([{'source_item_id': n['source_item_id']} for n in pk['news']], pk)
    assert len(selected) == 6
    assert {'c', 'k'} <= {c['source_item_id'] for c in selected}
    assert not rp.article_is_tech({'source_item_id': 'c'}, pk)


def test_numeric_tech_industry_and_internal_reference_cleanup():
    import industry_class as ic
    assert ic.is_tech_industry('24') and not ic.is_tech_industry('17')
    assert rp.clean_text('利多，但有資金賣壓（見 asset_net_effects 的淨效果判斷）。') == '利多，但有資金賣壓。'


def test_conflict_with_news_evidence_is_integrated_in_that_sector():
    obj = {'top_news_analysis': [{'source_item_id': 't'}],
           'contradictions': [{'supporting_ids': ['t'], 'opposing_ids': ['n2']}]}
    packet = {'news': [{'source_item_id': 't', 'title': '台積擴產'}]}
    md = rp.public_sections('## 八、科技板塊脈動\n需求\n\n## 我的明確立場\n中性\n\n'
                            '## 證據衝突與調和\n- 訂單增加但成本也上升\n', obj, packet)
    assert '訂單增加但成本也上升' in md.split('## 我的明確立場')[0]


def test_unsafe_article_link_does_not_become_clickable():
    n = {'source_item_id': 'x', 'why_it_matters': '需求'}
    md = ard._news_line(n, {'news': [{'source_item_id': 'x', 'title': '<script>壞</script>',
                                   'url': 'javascript:alert(1)'}]})
    html = ru._md_to_html(md)
    assert 'href=' not in html and '<script>' not in html


def test_cleanup_preserves_source_url_and_naturalizes_other_stance_members():
    url = 'https://example.com/moderate-growth?key=asset_net_effects'
    text = rp.clean_text(f'依 `STANCE_PY.label` 判斷，利率影響 moderate。[來源]({url})')
    assert url in text and 'STANCE_PY' not in text and '利率影響 中等' in text


@pytest.mark.parametrize('field,heading', [
    ('world_events', ar.SECTION_WORLD), ('taiwan_policy', ar.SECTION_POLICY),
    ('taiwan_local', ar.SECTION_LOCAL)])
@pytest.mark.parametrize('ref', ['n1', 'fact:n1.0', 'cluster:n1'])
def test_derived_evidence_routes_to_related_existing_section(field, heading, ref):
    obj = {field: [{'what': '事件', 'impact': '影響', 'source_item_id': 'n1'}],
           'contradictions': [{'supporting_ids': [ref]}]}
    packet = {'news_clusters': {'clusters': [{'cluster_id': 'cluster:n1', 'member_source_ids': ['n1']}]}}
    md = rp.public_sections(f'## {heading}\n事件\n\n## 我的明確立場\n中性\n\n'
                            '## 證據衝突與調和\n- 需綜合判讀的變化\n', obj, packet)
    assert '需綜合判讀的變化' in md.split('## 我的明確立場')[0]


def test_selection_uses_full_python_ranking_beyond_top_three():
    cards = [{'source_item_id': str(i)} for i in reversed(range(9))]
    packet = {'news': [{'source_item_id': str(i), 'title': '台積擴產'} for i in range(9)],
              'news_clusters': {'clusters': [{'cluster_id': f'c{i}', 'member_source_ids': [str(i)]} for i in range(9)]},
              'top_events': {'ranked': [{'cluster_id': f'c{i}'} for i in range(9)],
                             'top_cluster_ids': ['c0', 'c1', 'c2']}}
    selected, _ = rp.select_cards(cards, packet)
    assert [c['source_item_id'] for c in selected] == list(map(str, range(6)))


def test_mobile_caveat_inline_priority_overrides_generic_paragraph_size():
    import email_mobile
    html = email_mobile.enhance('<html><head></head><body>' + ru._md_to_html('保留:單一來源。') + '</body></html>')
    assert 'font-size:14px!important' in html
    assert '<p style="font-size:12px!important;' in html


def test_cleanup_after_adjacent_markdown_link_does_not_leak_schema():
    text = rp.clean_text('[標題](https://example.com/x)後續依 STANCE_PY.label 判斷')
    assert '[標題](https://example.com/x)後續依 整體立場 判斷' == text
