"""Preserve actual content and escaping without changing agreed typography."""
import html

import morning_report as mr
from render_utils import _render_podcast_html


def test_journal_heading_does_not_claim_incomplete_source_list():
    articles = [{'journal': journal, 'pmid': str(index),
                 'title': f'{journal} offline article <title>'}
                for index, journal in enumerate(('BJD', 'JAMA Derm'), 1)]
    rendered = mr._render_journals_html(articles, html)
    assert '醫學文獻速報（近 7 天）</h2>' in rendered
    for article in articles:
        assert html.escape(article['title']) in rendered
        assert f"https://pubmed.ncbi.nlm.nih.gov/{article['pmid']}/" in rendered
    assert 'font-size:20px' in rendered
    assert 'font-size:13px' in rendered


def test_long_podcast_title_remains_complete_and_escaped():
    title = 'All-In episode: ' + 'important context ' * 6 + '<closing topic> & final point'
    rendered = _render_podcast_html([{
        'show': 'Offline show', 'title': title,
        'digest': {'summary_points': ['本集的完整摘要。'], 'tickers': []}}], [], html)
    assert html.escape(title) in rendered
    assert '<closing topic>' not in rendered
    assert '本集的完整摘要。' in rendered
    assert "font-weight:400;color:#64748b;font-size:12px;" in rendered
