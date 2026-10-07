"""Actual writer boundary retains Python-registered packet-only citations."""
import pytest

import analysis_origin as ao
import morning_report as mr
from render_utils import _md_to_html, analysis_source_urls, packet_source_urls


def _writer_result(monkeypatch, quotes, news, raw, packet):
    manifest = {}
    recorded = []
    monkeypatch.setattr(mr, '_RUN_MANIFEST', manifest)
    monkeypatch.setattr(mr, '_record_report_writer', recorded.append)

    def already_rendered_packet(actual_quotes, *_args):
        # Replace only paid generation, not the shared writer boundary/guards.
        actual_quotes['_ANALYSIS_PACKET_URLS'] = packet_source_urls(packet, quotes=actual_quotes)
        mr._set_analysis_origin(ao.LUNA_SPECIALIZED)
        return raw

    monkeypatch.setattr(mr, '_call_llm_analysis_impl', already_rendered_packet)
    result = mr.call_llm_analysis(quotes, {}, {}, news)
    assert recorded == [result]
    return result, manifest


@pytest.mark.parametrize('heading', ['我的明確立場', '一句話總結'])
@pytest.mark.parametrize('source_kind', ['historical_sources', 'news', 'current_news'])
def test_actual_writer_preserves_registered_packet_and_current_news_title(
        monkeypatch, heading, source_kind):
    url = 'https://example.test/' + source_kind
    raw = f'## {heading}\n[法人建議減碼公開範例]({url})\n'
    quotes = {'NEWS_LINK_INDEX': [{'u': url}]} if source_kind == 'current_news' else {}
    news = [{'link': url, 'title': '法人建議減碼公開範例'}] if source_kind == 'current_news' else []
    packet = {} if source_kind == 'current_news' else {source_kind: [{'url': url, 'title': '法人建議減碼公開範例'}]}
    result, manifest = _writer_result(monkeypatch, quotes, news, raw, packet)
    assert result == raw
    assert 'conclusion_trade_claims_neutralized' not in manifest.get('llm', {})
    # The same registered title/link must reach the real formatting path.
    output = _md_to_html(result, allowed_urls=analysis_source_urls(quotes))
    assert f'href="{url}"' in output and '法人建議減碼公開範例' in output


@pytest.mark.parametrize('heading', ['我的明確立場', '一句話總結'])
def test_actual_writer_keeps_registered_source_but_removes_reader_advice(monkeypatch, heading):
    url = 'https://example.test/history'
    title = f'[法人建議減碼公開範例]({url})'
    raw = f'## {heading}\n{title} 讀者可以逢回買進。\n'
    result, manifest = _writer_result(monkeypatch, {}, [], raw,
                                      {'historical_sources': [{'url': url, 'title': '法人建議減碼公開範例'}]})
    assert title in result
    assert '讀者可以逢回買進' not in result
    assert manifest['llm']['conclusion_trade_claims_neutralized'] == 1


@pytest.mark.parametrize('heading', ['我的明確立場', '一句話總結'])
def test_actual_writer_does_not_register_model_supplied_link_metadata(monkeypatch, heading):
    url = 'https://example.test/unregistered'
    raw = f'## {heading}\n[偏多，建議減碼公開範例]({url})\n'
    quotes = {'untrusted_model_links': [{'url': url}]}
    result, manifest = _writer_result(monkeypatch, quotes, [], raw,
                                      {'untrusted_model_links': [{'url': url}]})
    assert '建議減碼' not in result
    assert manifest['llm']['conclusion_trade_claims_neutralized'] == 1
    assert url not in analysis_source_urls(quotes)
