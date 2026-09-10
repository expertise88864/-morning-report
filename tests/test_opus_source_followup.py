"""Offline regressions for independently confirmed source-quality findings."""
import copy

import journal_selection
from news_memory_selection import select


def test_truncated_archive_twin_prefers_readable_without_mutation():
    old = dict(title='Revenue update', excerpt='<a href="https://example.org/' + 'x' * 800,
               document_id='doc', published_at='2026-09-09T00:00:00Z', evidence_id='old')
    new = dict(old, excerpt='Revenue update', evidence_id='new')
    before = copy.deepcopy([old, new])
    assert select([old, new], 5) == [new]
    assert select([new, old], 5) == [new]
    assert [old, new] == before
    assert select([old], 5) == [old]


def test_readable_revisions_keep_changed_facts_and_unknown_identity():
    base = dict(title='Revenue update', document_id='doc',
                published_at='2026-09-09T00:00:00Z', evidence_id='a', excerpt='Revenue +2%')
    changed = dict(base, evidence_id='b', excerpt='Revenue -2%, not growth')
    assert select([base, changed], 5) == [base, changed]
    for overrides in ({'document_id': 'other'}, {'published_at': '2026-09-08T00:00:00Z'},
                      {'title': 'Different update'}, {'document_id': None}):
        empty = dict(base, excerpt='', evidence_id='empty', **overrides)
        assert len(select([empty, base], 5)) == 2


def test_plural_and_derived_comment_titles_do_not_displace_research():
    titles = ['Commentary: trial', 'Comments on trial', 'Corrections to trial',
              'Original trial', 'Commentary: trial. Reply']
    records = {str(i): {'title': title, 'pubtype': ['Journal Article']}
               for i, title in enumerate(titles)}
    assert [r['title'] for r in journal_selection.articles(list(records), records, 'NEJM', 3)] == ['Original trial']


def test_research_uses_of_correction_and_comments_remain_eligible():
    titles = ['Correction of myopia after surgery', 'Corrections of refractive errors',
              'Comments from patients in a randomized trial']
    records = {str(i): {'title': title, 'pubtype': ['Journal Article']}
               for i, title in enumerate(titles)}
    assert [r['title'] for r in journal_selection.articles(list(records), records, 'AJO', 3)] == titles
