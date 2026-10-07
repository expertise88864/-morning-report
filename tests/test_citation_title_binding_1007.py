"""Registered destinations do not authenticate model-authored advice labels."""
import pytest

import morning_report as mr
from render_utils import analysis_source_titles, build_news_link_index, packet_source_urls
from reader_citation_registry import matches, source_bindings
from test_writer_packet_citations_1007 import _writer_result


@pytest.mark.parametrize('heading', ['我的明確立場', '一句話總結'])
@pytest.mark.parametrize('source_kind', ['historical_sources', 'news', 'current_news'])
def test_actual_writer_rejects_fabricated_label_on_registered_destination(
        monkeypatch, heading, source_kind):
    url = 'https://example.test/' + source_kind
    source = {'title': '公開來源：訂單仍待驗證', 'url': url, 'link': url}
    news = [source] if source_kind == 'current_news' else []
    packet = {} if source_kind == 'current_news' else {source_kind: [source]}
    raw = f'## {heading}\n[偏多，建議減碼]({url})\n'
    result, manifest = _writer_result(monkeypatch, {}, news, raw, packet)
    assert '建議減碼' not in result
    assert manifest['llm']['conclusion_trade_claims_neutralized'] == 1


@pytest.mark.parametrize('heading', ['我的明確立場', '一句話總結'])
@pytest.mark.parametrize('minimal', [False, True])
def test_actual_renderers_reject_fabricated_label_on_registered_destination(
        monkeypatch, heading, minimal):
    monkeypatch.setattr(mr, '_RUN_MANIFEST', {})
    url = 'https://example.test/source'
    quotes = {'_ANALYSIS_PACKET_URLS': [url],
              'STANCE_PY': {'total': 6, 'label': '偏多', 'components': {}},
              'MACRO': {}, 'TAIEX_PRED': {}, 'NIGHT_TXF': {}}
    raw = f'## {heading}\n[偏多，建議減碼]({url})\n'
    if minimal:
        result = mr._render_minimal_html(quotes, {}, {}, raw, '2026-10-07', '離線合成')
    else:
        result = mr.render_html(quotes, {'error': 'offline'}, {'error': 'offline'},
                                raw, '2026-10-07', '離線合成')
    assert '建議減碼' not in result
    assert mr._RUN_MANIFEST['llm']['conclusion_trade_claims_neutralized'] == 1


@pytest.mark.parametrize('heading', ['我的明確立場', '一句話總結'])
@pytest.mark.parametrize('minimal', [False, True])
@pytest.mark.parametrize('source_kind', ['historical_sources', 'news', 'current_news'])
def test_actual_renderers_preserve_source_title_only_with_matching_python_metadata(
        monkeypatch, heading, minimal, source_kind):
    monkeypatch.setattr(mr, '_RUN_MANIFEST', {})
    url = 'https://example.test/' + source_kind
    title = '法人建議減碼公開範例'
    source = {'url': url, 'link': url, 'title': title}
    quotes = {'STANCE_PY': {'total': 6, 'label': '偏多', 'components': {}},
              'MACRO': {}, 'TAIEX_PRED': {}, 'NIGHT_TXF': {}}
    if source_kind == 'current_news':
        quotes['NEWS_LINK_INDEX'] = build_news_link_index([source])
    else:
        quotes['_ANALYSIS_PACKET_URLS'] = packet_source_urls({source_kind: [source]}, quotes=quotes)
    raw = ('## 我的明確立場\n> **立場：偏多**\n' +
           (f'[{title}]({url})\n## 一句話總結\n偏多，等待下一筆資料。\n'
            if heading == '我的明確立場' else f'## 一句話總結\n[{title}]({url})\n'))
    if minimal:
        result = mr._render_minimal_html(quotes, {}, {}, raw, '2026-10-07', '離線合成')
    else:
        result = mr.render_html(quotes, {'error': 'offline'}, {'error': 'offline'},
                                raw, '2026-10-07', '離線合成')
    assert title in result and url in result
    if minimal:
        assert f'href="{url}"' in result
    assert 'conclusion_trade_claims_neutralized' not in mr._RUN_MANIFEST.get('llm', {})


@pytest.mark.parametrize('source_kind', ['historical_sources', 'news', 'current_news'])
def test_missing_source_title_does_not_authenticate_advice_label(monkeypatch, source_kind):
    url = 'https://example.test/' + source_kind
    news = [{'link': url}] if source_kind == 'current_news' else []
    packet = {} if source_kind == 'current_news' else {source_kind: [{'url': url}]}
    result, manifest = _writer_result(monkeypatch, {}, news,
        f'## 一句話總結\n[偏多，建議減碼]({url})\n', packet)
    assert '建議減碼' not in result
    assert manifest['llm']['conclusion_trade_claims_neutralized'] == 1


def test_legacy_context_registers_only_its_existing_sanitized_sources(monkeypatch):
    import datetime as dt
    import news_research_context as context
    import news_research_runtime as runtime
    from test_news_research import article, observation

    current = article(5)
    archive = observation(1)
    urls, titles = [], []
    plain = context.legacy_block([current], [archive], '2026-09-06T07:00:00+08:00', sanitize=mr._external_text)
    registered = context.legacy_block([current], [archive], '2026-09-06T07:00:00+08:00',
        sanitize=mr._external_text, source_urls_out=urls, source_titles_out=titles)
    assert registered == plain  # No generated prompt/contract or source selection change.
    assert matches(archive['url'], archive['title'], titles)
    assert matches(current['link'], current['title'], titles)
    assert not matches(current['link'], '偏多，建議減碼', titles)
    real_datetime = dt.datetime

    class FixedDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 9, 6, 7, tzinfo=tz)

    monkeypatch.setattr(runtime.dt, 'datetime', FixedDatetime)
    quotes = {'NEWS_MEMORY': [archive]}
    assert runtime.legacy(quotes, [current], sanitize=mr._external_text) == '\n' + registered
    assert quotes['_ANALYSIS_PACKET_URLS'] == urls
    assert analysis_source_titles(quotes) == titles


def test_bindings_are_bounded_hashes_with_exact_url_and_label_pairing():
    from urllib.parse import quote

    url = 'https://example.test/中文_(測試)'
    title = '法人[報導]建議減碼'
    bindings = source_bindings([{'url': url, 'title': title}])
    assert matches(url, title, bindings)
    assert matches(quote(url, safe=':/?=&%#@+;,$!-_~'), '法人（報導）建議減碼', bindings)
    assert not matches(url + '/wrong', title, bindings)
    assert not matches(url, '偏多，建議減碼', bindings)
    assert all(title not in digest and len(digest) == 64 for _, digest in bindings)
    rows = [{'url': f'https://example.test/{i}', 'title': title} for i in range(601)]
    assert matches(rows[599]['url'], title, source_bindings(rows))
    assert not matches(rows[600]['url'], title, source_bindings(rows))


@pytest.mark.parametrize('safe', [':/?=&%', ':/?=&%#@+;,$!-_~'])
@pytest.mark.parametrize('heading', ['我的明確立場', '一句話總結'])
@pytest.mark.parametrize('source_kind', ['historical_sources', 'news', 'current_news'])
def test_actual_writer_preserves_encoded_source_title(monkeypatch, safe, heading, source_kind):
    from urllib.parse import quote

    url = 'https://example.test/中文_(測試)#anchor'
    encoded = quote(url, safe=safe)
    title = '法人建議減碼公開範例'
    source = {'url': url, 'link': url, 'title': title}
    news = [source] if source_kind == 'current_news' else []
    packet = {} if source_kind == 'current_news' else {source_kind: [source]}
    raw = f'## {heading}\n[{title}]({encoded})\n'
    result, manifest = _writer_result(monkeypatch, {}, news, raw, packet)
    assert result == raw
    assert 'conclusion_trade_claims_neutralized' not in manifest.get('llm', {})


@pytest.mark.parametrize('safe', [':/?=&%', ':/?=&%#@+;,$!-_~'])
@pytest.mark.parametrize('heading', ['我的明確立場', '一句話總結'])
@pytest.mark.parametrize('minimal', [False, True])
@pytest.mark.parametrize('source_kind', ['historical_sources', 'news', 'current_news'])
def test_actual_renderers_preserve_encoded_source_title(
        monkeypatch, safe, heading, minimal, source_kind):
    from urllib.parse import quote

    monkeypatch.setattr(mr, '_RUN_MANIFEST', {})
    url = 'https://example.test/中文_(測試)#anchor'
    encoded = quote(url, safe=safe)
    title = '法人建議減碼公開範例'
    source = {'url': url, 'link': url, 'title': title}
    quotes = {'STANCE_PY': {'total': 6, 'label': '偏多', 'components': {}},
              'MACRO': {}, 'TAIEX_PRED': {}, 'NIGHT_TXF': {}}
    if source_kind == 'current_news':
        quotes['NEWS_LINK_INDEX'] = build_news_link_index([source])
    else:
        quotes['_ANALYSIS_PACKET_URLS'] = packet_source_urls({source_kind: [source]}, quotes=quotes)
    raw = ('## 我的明確立場\n> **立場：偏多**\n' +
           (f'[{title}]({encoded})\n## 一句話總結\n偏多，等待下一筆資料。\n'
            if heading == '我的明確立場' else f'## 一句話總結\n[{title}]({encoded})\n'))
    if minimal:
        result = mr._render_minimal_html(quotes, {}, {}, raw, '2026-10-07', '離線合成')
    else:
        result = mr.render_html(quotes, {'error': 'offline'}, {'error': 'offline'},
                                raw, '2026-10-07', '離線合成')
    assert title in result and encoded in result
    if minimal:
        assert f'href="{encoded}"' in result
    assert 'conclusion_trade_claims_neutralized' not in mr._RUN_MANIFEST.get('llm', {})


@pytest.mark.parametrize('safe', [':/?=&%', ':/?=&%#@+;,$!-_~'])
def test_encoded_source_binding_still_needs_allowed_destination_and_literal_label(safe):
    from urllib.parse import quote
    from reader_citation_provenance import guarded_parts

    url = 'https://example.test/中文_(測試)#anchor'
    encoded = quote(url, safe=safe)
    title = '法人建議減碼公開範例'
    bindings = source_bindings([{'url': url, 'title': title}])
    literal = f'[{title}]({encoded})'
    assert matches(encoded, title, bindings)  # Metadata supports the variant.
    assert guarded_parts(literal, ['https://example.test/other'], bindings)[0] == ''
    assert guarded_parts(f'[偏多，建議減碼]({encoded})', [url], bindings)[0] == ''
    assert guarded_parts(literal, [url], ())[0] == ''


@pytest.mark.parametrize('safe', [':/?=&%', ':/?=&%#@+;,$!-_~'])
def test_actual_writer_rejects_fabricated_encoded_source_label(monkeypatch, safe):
    from urllib.parse import quote

    url = 'https://example.test/中文_(測試)#anchor'
    raw = f'## 一句話總結\n[偏多，建議減碼]({quote(url, safe=safe)})\n'
    result, manifest = _writer_result(monkeypatch, {}, [], raw,
        {'historical_sources': [{'url': url, 'title': '公開範例：仍待驗證'}]})
    assert '建議減碼' not in result
    assert manifest['llm']['conclusion_trade_claims_neutralized'] == 1


@pytest.mark.parametrize('fragment', ['anchor', '來源', 'evidence-1'])
def test_fragment_extract_preserves_complete_literal_citation(fragment):
    body = f'[法人建議減碼公開範例](https://example.test/source#{fragment})'
    raw = f'## 一句話總結\n{body}\n## 九、其他新聞\n不屬於總結。'
    assert mr._extract_summary(raw) == body


@pytest.mark.parametrize('next_heading', ['# 九、其他新聞', '## 九、其他新聞', '  ## 九、其他新聞'])
def test_fragment_extract_does_not_capture_next_markdown_heading(next_heading):
    assert mr._extract_summary(f'## 一句話總結\n{next_heading}\n公開標題。') == ''
