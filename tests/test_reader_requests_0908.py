"""Reader acceptance criteria from the actual scheduled September 8 email."""
import copy

import analysis_render_depth as depth
import econ_terms
import email_mobile
import reader_prose
import render_utils


def test_rotation_keeps_one_rectangular_table_on_mobile():
    rot = {'table': [{'industry': '金融保險業', 'median_5d': 5.4,
                     'relative': 4.6, 'members': 15, 'up_5d': 15}],
           'market_median': .8, 'strong': [('金融保險業', 5.4, 4.6, 15)]}
    html = email_mobile.enhance('<html><head></head><body>' +
        render_utils._render_sector_rotation_table(rot, {}) + '</body></html>')
    table = html.split('<table', 1)[1].split('</table>')[0]
    assert 'data-mobile-layout' in table and 'mail-stack' not in table
    assert table.count('<th ') == 5 and table.count('<td ') == 5
    assert '+5.4%' in table and 'white-space:nowrap' not in table


def test_missing_entities_still_get_named_company_and_separate_link():
    packet = {'news': [{'source_item_id': 'n', 'title': '輝達展望成長',
                       'url': 'https://example.com/news'}]}
    card = {'source_item_id': 'n', 'why_it_matters': '訂單延續。'}
    original = copy.deepcopy(card)
    text = depth._news_line(card, packet)
    assert '**輝達' in text and '**\n\n' in text
    assert '](' in text and '\n\n訂單延續' in text
    assert card == original


def test_calendar_translates_actual_untranslated_titles():
    for english, chinese in [('Main Refinancing Rate', '主要再融資利率'),
                             ('Monetary Policy Statement', '貨幣政策聲明'),
                             ('ECB Press Conference', '歐洲央行記者會'),
                             ('Core PPI m/m', '核心生產者物價指數月增率'),
                             ('Core CPI y/y', '核心消費者物價指數年增率')]:
        assert chinese in econ_terms.annotate(english)


def test_score_explanation_hidden_without_changing_source():
    text = '系統計分在美股休市下只採台灣本地訊號：淨結果為 -1。內部多空並存。'
    out = reader_prose.public_sections('## 我的明確立場\n立場：中性\n' + text)
    assert '系統計分' not in out and '內部多空並存。' in out
    article = '## 九、其他類股資訊\n' + text
    assert reader_prose.public_sections(article) == article


def test_legitimate_compound_risk_text_survives():
    text = '地緣政治風險：油價上升。下行風險：需求轉弱。'
    assert reader_prose.clean_text(text) == text


def test_local_multiline_and_legacy_projection_do_not_drop_text():
    md = '## 十一、台灣本地動態\n- 第一行\n第二行\n\n## 我的明確立場\n立場：中性\n'
    assert '第二行' in reader_prose.public_sections(md)
    obj = {'taiwan_local': [{'what': '第一行\n第二行', 'impact': '影響A\n影響B'}]}
    result = reader_prose.public_sections(md, obj)
    assert all(s in result for s in ('第一行', '第二行', '影響A', '影響B'))
    assert reader_prose.public_sections(result) == result


def test_item_nine_hides_observation_window_but_keeps_reasoning_and_state():
    """User attachment item 9: '刪除 影響觀察窗 量級依據'; keep the reasoning, not labels."""
    card = {'source_item_id': 'n', 'why_it_matters': '需求待確認。', 'horizon': '1-4w',
            'why_this_magnitude': '尚無合約金額。', 'confirmation_signal': '等待營收公告。'}
    original = copy.deepcopy(card)
    rendered = depth._news_line(card)
    assert '影響觀察窗' not in rendered and '1–4 週' not in rendered
    assert '量級依據' not in rendered and '尚無合約金額' in rendered
    assert '等待營收公告' in rendered and card == original
