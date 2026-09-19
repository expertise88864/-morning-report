from copy import deepcopy
import html

import pytest

from podcast_quotes import validate, display_quote


@pytest.mark.parametrize('quote', ['利率不會立刻下降。', 'This is an exact quote.'])
def test_verbatim_quote_preserves_negation_and_language(quote):
    digest = {'notable_quote': quote, 'summary_points': ['主持人觀點']}
    before = deepcopy(digest)
    result = validate(digest, '前文。' + quote + '後文。')
    assert display_quote(result) == quote
    assert digest == before


@pytest.mark.parametrize('quote', ['利率會立刻下降。', ['不是字串'], {}, 7, ' ', '文' * 601])
def test_unsupported_quote_cannot_reuse_model_metadata(quote):
    result = validate({'notable_quote': quote, 'quote_evidence': {
        'version': 1, 'status': 'verbatim', 'quote': quote}}, '利率不會立刻下降。')
    assert display_quote(result) == ''
    assert result['quote_evidence']['status'] == 'unverified'


def test_changed_or_legacy_quote_is_not_displayable():
    assert display_quote({'notable_quote': '舊金句'}) == ''
    result = validate({'notable_quote': '不確定'}, '我不確定')
    result['notable_quote'] = '確定'
    assert display_quote(result) == ''


def test_render_uses_producer_evidence_without_mutating_legacy():
    from render_utils import _render_podcast_html
    quote = '利率不會立刻下降。'
    legacy = {'notable_quote': quote, 'summary_points': ['保留摘要']}
    before = deepcopy(legacy)
    assert quote not in _render_podcast_html([{'show': '節目', 'digest': legacy}], [], html)
    verified = validate(legacy, quote)
    assert quote in _render_podcast_html([{'show': '節目', 'digest': verified}], [], html)
    assert legacy == before


def test_real_producer_rejects_invented_quote_without_another_paid_call(monkeypatch):
    import json
    from unittest.mock import Mock
    import podcast_digest as producer
    response = Mock()
    response.json.return_value = {'choices': [{'message': {'content': json.dumps({
        'summary_points': ['主持人提醒利率仍不確定'],
        'notable_quote': '利率一定下降',
        'quote_evidence': {'version': 1, 'status': 'verbatim', 'quote': '利率一定下降'}
    })}}]}
    post = Mock(return_value=response)
    monkeypatch.setattr(producer.requests, 'post', post)
    result = producer.deepseek_digest('我不知道利率什麼時候下降。')
    assert post.call_count == 1
    assert result['summary_points'] == ['主持人提醒利率仍不確定']
    assert result['notable_quote'] == ''
    assert result['quote_evidence']['status'] == 'unverified'
