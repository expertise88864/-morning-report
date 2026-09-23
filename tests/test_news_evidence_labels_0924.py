"""Offline rendering checks for interpretation and nearby source limits."""

import analysis_render_depth as render
import news_impact
import podcast_comparison
from render_utils import _md_to_html


def test_source_limit_stays_beside_interpretation_in_small_print(monkeypatch):
    import analysis_validate

    monkeypatch.setattr(analysis_validate, 'weakly_corroborated', lambda *_: True)
    packet = {'news': [{'source_item_id': 'n', 'title': '公司公布人事調整'}]}
    row = {'source_item_id': 'n', 'why_it_matters': '對後續成本可能有影響。',
           'source_caveat': '只有單一媒體引述文件，尚待公司確認。',
           'invalidation_signal': '公司公布不同數據'}
    text = render._news_line(row, packet)
    assert text.index('本報解讀：') < text.index('保留:') < text.index('若公司公布不同數據')
    html = _md_to_html(text)
    caveat = html.split('<p style="font-size:12px!important', 1)[1].split('</p>', 1)[0]
    assert '只有單一媒體' in caveat
    assert '若公司公布不同數據' not in caveat


def test_news_and_podcast_prompts_separate_layoffs_from_financing():
    assert '裁員本身不能證明融資或現金流受限' in news_impact.WRITING
    assert '沒有可比較的同一命題就留空陣列' in podcast_comparison.RULES


def test_macro_news_keeps_each_headline_with_its_interpretation(monkeypatch):
    import analysis_render
    import fixtures_analysis
    import reader_editorial

    cards = [{'source_item_id': sid, 'why_it_matters': '通膨壓力回升。'}
             for sid in ('macro-a', 'macro-b')]
    packet = {'news': [{'source_item_id': 'macro-a', 'title': '第一則總經新聞'},
                       {'source_item_id': 'macro-b', 'title': '第二則總經新聞'}]}
    monkeypatch.setattr(analysis_render._reader, 'select_cards', lambda *_: (cards, []))
    monkeypatch.setattr(reader_editorial, 'is_macro', lambda *_: True)
    obj = fixtures_analysis.valid_analysis()
    text = analysis_render.render(obj, packet)
    macro = text.split(analysis_render.SECTION_MACRO, 1)[1]
    assert macro.count('本報解讀：通膨壓力回升。') == 2
    assert macro.index('第一則總經新聞') < macro.index('第二則總經新聞')
