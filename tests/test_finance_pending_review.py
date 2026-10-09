"""Generic financial vocabulary must not force a filler analysis card."""
import pytest

import finance_editorial as finance


@pytest.mark.parametrize("title", [
    "國泰投信00878受益人創新高 投資人關注配息",
    "中信金出席資本市場論壇",
    "國泰投信分享投資理財觀念",
    "國泰投信投資講座開放報名", "國泰金監理論壇交流",
])
def test_routine_words_do_not_create_mandatory_finance_coverage(title):
    news = [{"source_item_id": "n1", "title": title, "published": "2026-10-08"}]
    assert finance.analysis_candidates(news) == []
    from news_coverage import select
    group = '中信金' if '中信金' in title else '國泰金'
    routine = {"source_item_id": "n0", "title": group + "數位帳戶新功能", "published": "2026-10-08"}
    kept, _ = select([routine, *news], set(), 1)
    assert kept == [routine]
    assert finance.analysis_candidates(kept) == []


@pytest.mark.parametrize("title", [
    "中信金宣布投資新公司", "中信金資本適足率下滑", "中信金資本不足遭裁罰",
    "國泰金公布資本支出計畫", "中信金公布財報獲利", "國泰金股利公告",
])
def test_substantive_finance_events_still_require_analysis(title):
    news = [{"source_item_id": "n1", "title": title, "published": "2026-10-08"}]
    assert finance.analysis_candidates(news) == [["n1"]]
