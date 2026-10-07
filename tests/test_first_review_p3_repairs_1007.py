"""Actual writer/last-fallback and Podcast projection regressions from full review."""
import copy
import html

import pytest

import analysis_origin as ao
import morning_report as mr
from legacy_actor_guard import correct_reader_claims
from podcast_entity_identity import conflicting_spoken_name
from podcast_evidence import project
from render_utils import _render_podcast_html
from test_podcast_entity_identity_0927 import _ticker


@pytest.mark.parametrize('heading', ['一句話總結', '我的明確立場'])
@pytest.mark.parametrize('marker', ['', '- ', '> '])
def test_unregistered_link_label_cannot_exempt_model_advice(heading, marker):
    raw = f'## {heading}\n{marker}[偏多，建議減碼公开範例](https://example.test/unknown)\n'
    manifest = {}
    result = correct_reader_claims(raw, [], origin=ao.LEGACY_PRIMARY, manifest=manifest)
    assert '建議減碼' not in result
    assert manifest.get('llm')


def test_actual_last_fallback_removes_unregistered_summary_link_advice(monkeypatch):
    monkeypatch.setattr(mr, '_RUN_MANIFEST', {})
    raw = '## 十二、一句話總結\n[偏多，建議減碼公開範例](https://example.test/unknown)\n'
    result = mr._render_minimal_html({'STANCE_PY': {'total': 50}}, {}, {}, raw, '2026-10-07', 'test')
    assert '建議減碼' not in result
    assert mr._RUN_MANIFEST['llm']['conclusion_trade_claims_neutralized'] == 1


def test_actual_full_renderer_removes_advice_from_body_and_conclusion(monkeypatch):
    monkeypatch.setattr(mr, '_RUN_MANIFEST', {})
    raw = ('## 十二、我的明確立場\n> **立場：偏多**\n'
           '## 十三、一句話總結\n[偏多，建議減碼公開範例](https://example.test/unknown)\n')
    quotes = {'STANCE_PY': {'total': 6, 'label': '偏多', 'components': {}},
              'MACRO': {}, 'TAIEX_PRED': {}, 'NIGHT_TXF': {}}
    result = mr.render_html(quotes, {'error': 'offline'}, {'error': 'offline'},
                            raw, '2026-10-07', '離線合成')
    assert '建議減碼' not in result and 'example.test/unknown' not in result
    assert mr._RUN_MANIFEST['llm']['conclusion_trade_claims_neutralized'] == 1


def test_registered_source_title_is_not_edited_as_model_advice():
    url = 'https://example.test/source'
    raw = '## 一句話總結\n[法人建議減碼公開範例](' + url + ')\n'
    manifest = {}
    assert correct_reader_claims(raw, [{'link': url, 'title': '法人建議減碼公開範例'}], origin=ao.LEGACY_PRIMARY, manifest=manifest) == raw
    assert not manifest


@pytest.mark.parametrize('quote', ['Bloom Energy 是 clean energy 的龍頭',
                                  'Department of Energy 提及供電，Bloom Energy 的設備受關注',
                                  'nuclear energy 需要持續追蹤', 'new energy 的供電方案有討論',
                                  'the Energy 資料是公開統計'])
def test_generic_energy_phrase_does_not_drop_actual_opinion_episode(quote):
    ticker = _ticker('Bloom Energy', quote)
    episode = {'show': '公開測試節目', 'title': '供電話題', 'published': '2026-10-06',
               'digest': {'tickers': [ticker]}}
    before = copy.deepcopy(episode)
    assert conflicting_spoken_name(ticker) == ''
    packet = project([episode], as_of='2026-10-07', sanitize=lambda text: text)
    assert len(packet['episodes']) == 1 and packet['omitted_episodes'] == 0
    output = _render_podcast_html([episode], [], html)
    assert 'Bloom Energy（BE）' in output and '公司對應待核實' not in output
    assert episode == before


@pytest.mark.parametrize('quote', ['Blue Energy 就是供電題材', 'blue energy就是供電題材',
                                  'Clean Energy 這家公司正在擴張'])
def test_company_conflict_still_excludes_the_actual_opinion_episode(quote):
    ticker = _ticker('Bloom Energy', quote)
    assert conflicting_spoken_name(ticker)
    packet = project([{'show': '測試', 'title': '測試', 'published': '2026-10-06',
                       'digest': {'tickers': [ticker]}}], as_of='2026-10-07', sanitize=lambda text: text)
    assert not packet['episodes'] and packet['identity_conflict_episodes'] == 1
