"""September 9 attachment: concise conclusion, sector routing and recurrence."""
import copy

import analysis_render as render
import analysis_render_depth as depth
import fixtures_analysis as fx
import reader_editorial as editorial
import reader_prose
import reader_selection


def test_conclusion_not_a_dump_for_scenarios_or_unmatched_watches():
    obj = fx.valid_analysis()
    before = copy.deepcopy(obj)
    md = render.render(obj)
    stance = md.split('## 我的明確立場\n')[1].split('## 一句話總結')[0]
    for block in obj['scenario_tree'].values():
        if isinstance(block, dict) and block.get('narrative'):
            assert block['narrative'] not in stance
            assert block['narrative'] in md
    assert obj == before
    assert reader_prose.public_sections(md) == md


def test_macro_does_not_take_any_of_six_sector_slots():
    news = [{'source_item_id': 'fed', 'title': "Why the Fed's interest rate call matters"},
            {'source_item_id': 'tariff', 'title': "Canada's retaliatory tariffs take effect"}]
    news += [{'source_item_id': str(i), 'title': f'航運新合約第{i}案'} for i in range(6)]
    cards = [{'source_item_id': n['source_item_id'], 'why_it_matters': '有待確認。'} for n in news]
    selected, omitted = reader_selection.select_cards(cards, {'news': news})
    assert len(selected) == 8 and not omitted
    obj = fx.valid_analysis()
    obj['top_news_analysis'] = cards
    md = render.render(obj, {'news': news})
    sector = md.split('## 九、其他類股資訊')[1].split('## ')[0]
    assert '利率與通膨' not in sector and '國際貿易' not in sector
    assert '利率與通膨動態（原文報導）' in md


def test_company_specific_rate_effect_is_not_misrouted():
    packet = {'news': [{'source_item_id': 'c', 'title': '中信金升息後公布營收'}]}
    assert not editorial.is_macro({'source_item_id': 'c'}, packet)


def test_english_headline_uses_honest_chinese_topic_and_original_link():
    card = {'source_item_id': 'n', 'why_it_matters': '影響仍待確認。'}
    packet = {'news': [{'source_item_id': 'n', 'title': 'Fed rate decision preview',
                       'url': 'https://example.com/fed?x=1&y=2'}]}
    before = copy.deepcopy(packet)
    text = depth._news_line(card, packet)
    assert '[利率與通膨動態（原文報導）](https://example.com/fed?x=1&y=2)' in text
    assert 'Fed rate decision preview' not in text and packet == before


def test_revenue_recap_requires_same_issuer_month_and_no_new_facts():
    old = [{'title': '藥華藥8月營收26.2億元創高', 'excerpt': '年增105.4%'}]
    current = {'title': '藥華藥8月營收創歷史新高', 'summary': '26.2億元，年增105.4%'}
    assert editorial.recurring_revenue(current, old, '藥華藥')
    for title in ('藥華藥9月營收創高', '藥華藥8月營收下修為20億元',
                  '藥華藥8月營收創高、新藥獲核准', '藥華藥8月營收創高、納入成分股'):
        assert not editorial.recurring_revenue(dict(current, title=title), old, '藥華藥')
    assert not editorial.recurring_revenue(current, old, '其他公司')
    assert not editorial.recurring_revenue(current, [], '藥華藥')
    for event in ('增資', '董事長辭任', '簽約', '得標', '動工', '減損', '股利', '外洩', '未見過的新重大事件'):
        assert not editorial.recurring_revenue(dict(current, summary=current['summary'] + event), old, '藥華藥')
    assert not editorial.recurring_revenue({'title': '藥華藥8月營收變化-10%'},
        [{'title': '藥華藥8月營收變化10%'}], '藥華藥')
    assert not editorial.recurring_revenue({'title': '藥華藥1月營收創歷史新高'},
        [{'title': '藥華藥11月營收創歷史新高'}], '藥華藥')
    announcement = '藥華藥公告本公司115年8月份自結合併營收'
    assert editorial.recurring_revenue({'title': announcement}, [{'title': announcement}], '藥華藥')
    for extra in ('、外洩', '、公告合併', '、未知重大事件'):
        assert not editorial.recurring_revenue({'title': announcement + extra},
            [{'title': announcement}], '藥華藥')


def test_macro_exact_overlap_preserves_qualifications_and_links():
    sentence = '本週將公布通膨資料，這是重要的利率決策參考。'
    out = editorial.integrate_macro([sentence, sentence, sentence + '但未經證實。',
                                    '[原始報導](https://example.com)'])
    assert out == [sentence, sentence + '但未經證實。', '[原始報導](https://example.com)']
    caveat = '保留:此消息尚未獲官方確認。'
    rows = [f'[消息{i}](https://example.com/{i})\n\n{caveat}' for i in range(2)]
    assert sum(row.count(caveat) for row in editorial.integrate_macro(rows)) == 2


def test_recap_ranking_uses_only_valid_history_and_reserves_deep_topics():
    import editorial_priority
    packet = {'as_of': '2026-09-09T06:00:00+08:00',
              'tw_universe': [{'code': '6446', 'name': '藥華藥', 'industry': '生技醫療業'}],
              'news': [{'source_item_id': 'old', 'title': '藥華藥8月營收創歷史新高'},
                       {'source_item_id': 'new', 'title': '航運新訂單簽署'}],
              'historical_sources': [{'evidence_id': 'history:x',
                 'title': '藥華藥8月營收創高', 'excerpt': '', 'url': 'https://example.com/old',
                 'published_at': '2026-09-08T06:00:00+08:00',
                 'observed_at': '2026-09-08T06:00:00+08:00', 'content_level': 'title_only'}],
              'research': {'contexts': {'old': {'evidence_ids': ['history:x']}}}}
    cards = [{'source_item_id': 'old'}, {'source_item_id': 'new'}]
    original = copy.deepcopy(packet)
    assert editorial_priority.order(cards, packet)[0] == cards[1]
    assert packet == original
    packet['news'][1]['title'] = '直播倒數優惠一次看'
    assert editorial_priority.order(cards, packet)[0] == cards[0]
    packet['news'][1]['title'] = '航運新訂單簽署'
    packet['research']['deep_topics'] = [{'member_source_ids': ['old']}]
    assert reader_selection.select_cards(cards, packet)[0][0] == cards[0]
    packet['historical_sources'][0]['observed_at'] = '2026-09-10T06:00:00+08:00'
    assert editorial_priority.order(cards, packet)[0] == cards[0]


def test_macro_cards_remain_in_final_html_loss_audit():
    import email_content_audit
    md = '## 十、總體經濟與政策環境\n成本 → 通膨。'
    html = '<h2>十、總體經濟與政策環境</h2><p>總經摘要</p>'
    assert email_content_audit.audit(md, html)['lost_cards'] == 1


def test_quality_warning_counts_rendered_macro_cards():
    from test_run_quality import _ok_manifest
    import run_quality
    manifest = _ok_manifest()
    manifest.setdefault('llm', {})['news_render'] = {
        'analyzed': 4, 'rendered_tech': 1, 'rendered_other': 1, 'rendered_macro': 1,
        'dropped': [{'sid': 'missing', 'section': 'macro'}]}
    finding = next(f for f in run_quality.assess(manifest) if f['code'] == 'news_cards_dropped')
    assert '只渲染 3 則' in finding['detail']


def test_legacy_unmapped_observations_keep_neutral_section_not_macro():
    import email_content_audit
    import render_utils
    md = '## 昨日觀察點回顧\n台積電新廠驗收尚未完成。\n\n## 我的明確立場\n立場：中性\n'
    out = reader_prose.public_sections(md)
    assert '## ' + editorial.OUTLOOK_HEADING in out
    assert '台積電新廠驗收尚未完成。' in out
    assert '## ' + editorial.MACRO_HEADING not in out
    assert '48 小時' not in out
    assert reader_prose.public_sections(out) == out
    assert email_content_audit.audit(out, render_utils._md_to_html(out))['missing_sections'] == []
    assert editorial.OUTLOOK_HEADING in email_content_audit.audit(out, '')['missing_sections']
