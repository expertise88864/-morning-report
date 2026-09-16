import json

import pytest

import analysis_origin as ao
import fallback_delivered as fd
import fallback_recap_runtime as runtime

URL = 'https://example.com/article?x=1&y=2'
PARA = '公司需求可能回升，但還沒有訂單證據。（媒體）'
HTML = ('<h2>八、科技板塊脈動</h2><p><b>公司需求可能回升</b>，但還沒有訂單證據。'
        '<a href="https://example.com/article?x=1&amp;y=2">（媒體）</a></p>')


def test_delivered_links_recover_legacy_prose_without_raw_markdown_urls(tmp_path):
    context = {'text': '## 八、科技板塊脈動\n' + PARA, 'news': [{'url': URL}],
               'date': '2026-09-16', 'origin': ao.LEGACY_AFTER_LUNA_FAILURE}
    writes, manifest = [], {}
    runtime.delivered(HTML, context, tmp_path / 'recap.json', manifest,
                      lambda _, text: writes.append(json.loads(text)))
    item = writes[0]['legacy_report']['items'][0]
    assert item == {'statement': PARA, 'source_urls': [URL]}
    assert writes[0]['legacy_report']['status'] == 'unvalidated_model_opinion'
    assert manifest['llm']['fallback_recap_saved'] == 'saved'


@pytest.mark.parametrize('html,news', [
    (HTML, []),
    (HTML.replace('八、科技板塊脈動', 'Podcast 重點'), [{'url': URL}]),
    (HTML.replace('公司需求', '持有股數'), [{'url': URL}]),
    (HTML.replace('公司需求', '長' * 801), [{'url': URL}]),
    (HTML.replace('<b>', '<a href="https://example.com/unknown">').replace('</b>', '</a>'), [{'url': URL}]),
])
def test_unknown_links_private_long_or_wrong_section_are_not_kept(html, news):
    assert not fd.extract(html, news, '2026-09-16', ao.LEGACY_PRIMARY)['items']


def test_section_boundary_and_origin_are_enforced():
    html = HTML + '<h2>操作思路</h2><p><a href="' + URL + '">不要保存</a></p>'
    assert len(fd.extract(html, [{'url': URL}], '2026-09-16', ao.LEGACY_PRIMARY)['items']) == 1
    assert fd.extract(html, [{'url': URL}], '2026-09-16', ao.EMERGENCY_FALLBACK) == {}
