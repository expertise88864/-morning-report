"""Compaction savings include class names, inline fallbacks and stylesheet."""
from bs4 import BeautifulSoup

from render_utils import compact_inline_styles


def test_short_repeated_colour_styles_still_save_bytes():
    original = '<head></head><body>' + '<span style="color:#94a3b8;">source</span>' * 45 + '</body>'
    result = compact_inline_styles(original)
    soup = BeautifulSoup(result, 'html.parser')
    assert all(span.get('class') for span in soup.find_all('span'))
    assert 'color:#94a3b8;' in soup.style.get_text()
    assert len(original.encode()) - len(result.encode()) > 400
    assert soup.get_text() == BeautifulSoup(original, 'html.parser').get_text()


def test_stylesheet_overhead_must_not_make_output_larger():
    original = '<head></head><body>' + '<i style="a:1;">x</i>' * 4 + '</body>'
    assert compact_inline_styles(original, min_uses=2) == original


def test_existing_classes_and_their_inline_colour_are_preserved():
    original = '<head></head><body>' + (
        '<span class="source-label" style="color:#94a3b8;">source</span>' * 45) + '</body>'
    result = compact_inline_styles(original)
    soup = BeautifulSoup(result, 'html.parser')
    assert all(span.get('class') == ['source-label'] and span.get('style') == 'color:#94a3b8;'
               for span in soup.find_all('span'))


def test_critical_inline_properties_survive_real_byte_savings():
    original = '<head></head><body>' + (
        '<span style="color:#94a3b8;background:#ffffff;border:1px solid #ccc;'
        'font-size:12px;text-align:right;line-height:1.5;">source</span>' * 45) + '</body>'
    result = compact_inline_styles(original)
    soup = BeautifulSoup(result, 'html.parser')
    assert len(result.encode()) < len(original.encode())
    assert all(span['style'] == 'font-size:12px;text-align:right;line-height:1.5;'
               for span in soup.find_all('span'))
