"""Archived CBC report claims must retain the period and the original numbers."""

import html

import morning_report as report
from run_manifest import ManifestRecorder
from policy_scope_guard import correct_cbc_scope
from render_utils import _md_to_html, analysis_source_urls
from reader_market_language_guard import neutralize_report


def test_delivered_weekday_cbc_claim_is_scoped_to_annual_maximum():
    original = (
        "央行報告：台商投資結構翻轉：對中國累計直接投資比重從 2017 年底的 58.3% "
        "降至 2018 至 2025 年的 18.8%，美國以 18.7% 躍居最大投資地 （自由財經）。\n"
    )
    manifest = {}
    corrected = neutralize_report(original, manifest)
    assert "美國以 18.7% 躍居最大投資地" not in corrected
    assert "2018至2025年合計占18.7%，略低於中國大陸18.8%" in corrected
    assert "央行明確指出2023、2024年美國為當年最大對外直接投資地" in corrected
    assert "央行原報告](https://www.cbc.gov.tw/" in corrected
    assert manifest["llm"]["policy_scope_guard_rules"] == [
        "cbc_2018_2025_vs_2023_2024_scope"
    ]
    rendered = _md_to_html(corrected, allowed_urls=analysis_source_urls(
        {}, cbc_correction=bool(manifest["llm"]["policy_scope_guard_rules"])))
    assert 'href="https://www.cbc.gov.tw/' in rendered
    assert "](https://www.cbc.gov.tw/" not in rendered
    assert correct_cbc_scope(corrected)[0] == corrected


def test_delivered_sunday_cbc_claim_repairs_both_errors(monkeypatch):
    original = (
        "台商投資版圖與生產模式轉折（央行報告）：對中國累計直接投資比重降至"
        "2018-2025年的18.8%，美國躍居台商海外最大投資地；2025年外銷訂單"
        "在台生產比重達52.9%，中國生產比重降至26.5%。\n"
    )
    corrected, rules = correct_cbc_scope(original)
    assert "26.5%" not in corrected and "26.2%" in corrected
    assert "央行明確指出2023、2024年" in corrected
    assert rules == ("cbc_2018_2025_vs_2023_2024_scope",
                     "cbc_2025_china_production_share")
    assert corrected.count("央行原報告](https://") == 1
    manifest = {}
    monkeypatch.setattr(report, "_RUN_MANIFEST", manifest)
    delivered = report._render_week_review_html(original, html)
    assert "26.2%" in delivered and "26.5%" not in delivered
    assert "央行明確指出2023、2024年" in delivered
    assert 'href="https://www.cbc.gov.tw/' in delivered
    assert manifest["llm"]["policy_scope_guard_rules"] == list(rules)
    persisted = ManifestRecorder(data=manifest).build(
        date="2026-09-27", report_kind="weekend_digest",
        budget_seconds=0, news_workers=0, degraded_steps=[])
    assert persisted["llm"]["policy_scope_guard_rules"] == list(rules)


def test_unrelated_statistics_and_annual_claims_remain_unchanged():
    examples = [
        "其他研究：中國生產比重26.5%，美國最大投資地。",
        "央行報告：2023年美國為最大投資地，投資占比36.4%。",
        "央行報告：某年美國最大投資地，但未提供2018至2025年比重。",
        "央行報告：中國18.8%，美國躍居最大投資地，但這是另一份調查。",
        "「央行報告：2018至2025年中國18.8%，美國躍居最大投資地。」",
    ]
    for original in examples:
        assert correct_cbc_scope(original) == (original, ())


def test_corrected_line_does_not_attach_source_to_later_unchanged_line():
    original = ("央行報告：2018至2025年中國18.8%，美國躍居最大投資地。\n"
                "央行報告：另一項獨立調查，數值不明。\n")
    corrected, _ = correct_cbc_scope(original)
    assert corrected.count("央行原報告](https://") == 1


def test_rendered_source_headline_is_preserved_while_own_prose_is_corrected():
    title = ("**央行報告**｜[2018至2025年中國18.8%，美國躍居最大投資地]"
             "(https://example.org/news)（自由財經）\n")
    own = "本報解讀：央行報告顯示2018至2025年中國18.8%，美國躍居最大投資地。\n"
    corrected, rules = correct_cbc_scope(title + own)
    assert corrected.startswith(title)
    assert corrected.count("央行原報告](https://") == 1
    assert "央行明確指出2023、2024年" in corrected[len(title):]
    assert rules == ("cbc_2018_2025_vs_2023_2024_scope",)


def test_emergency_source_list_title_is_preserved_not_rewritten_as_own_prose(monkeypatch):
    title = "- [fixture-source] 央行報告：2018至2025年中國18.8%，美國躍居最大投資地。\n"
    own = "本報解讀：央行報告顯示2018至2025年中國18.8%，美國躍居最大投資地。\n"
    manifest = {}
    monkeypatch.setattr(report, "_RUN_MANIFEST", manifest)
    report._fallback_analysis_text([{"source": "fixture-source", "title": title.split("] ", 1)[1].rstrip("\n")}],
                                   RuntimeError("offline fixture"))
    corrected, rules = correct_cbc_scope(title + own, manifest)
    assert corrected.startswith(title)
    assert corrected.count("央行原報告](https://") == 1
    assert rules == ("cbc_2018_2025_vs_2023_2024_scope",)
