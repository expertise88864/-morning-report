"""User decision 2026-10-15: hide medical journals from the daily report only.

The daily email regularly exceeds Gmail's ~102KB clipping line. Hiding must not
spend the PubMed/translation calls for content no one sees, and the weekend
digest (a separate, smaller email) keeps the section.
"""
import inspect

import morning_report as mr


def test_daily_report_neither_fetches_nor_translates_journals():
    assert mr.DAILY_MEDICAL_JOURNALS is False
    src = inspect.getsource(mr)
    call = 'quotes["MEDICAL_JOURNALS"] = (translate_journal_titles('
    assert src.count(call) == 1
    i = src.index(call)
    # The fetch is evaluated only when the flag is on; otherwise an empty list.
    assert "fetch_medical_journal_articles()) if DAILY_MEDICAL_JOURNALS else [])" in src[i:i + 200]


def test_empty_journals_render_no_section():
    import html
    assert mr._render_journals_html([], html) == ""


def test_weekend_digest_still_fetches_journals():
    src = inspect.getsource(mr)
    i = src.index("journals = translate_journal_titles(fetch_medical_journal_articles())")
    assert "DAILY_MEDICAL_JOURNALS" not in src[i - 200:i + 200]
