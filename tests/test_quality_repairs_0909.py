import copy
import datetime as dt
from zoneinfo import ZoneInfo

import analysis_recap as rc
import email_markup
import event_clock
import finance_editorial as finance
import news_display_quality as feeds
import reader_fact_labels as facts
import reader_prose
import fetch_plan
import json


def test_short_horizon_gets_review_before_long_backlog():
    ledger = [{"watch_id": f"w{i}", "trigger": f"事件{i}", "created": "2026-09-01",
               "deadline": "2026-09-29", "status": "open", "last_reviewed": "2026-09-05"}
              for i in range(1, 41)]
    ledger.append({"watch_id": "w41", "trigger": "明日數據", "created": "2026-09-08",
                   "deadline": "2026-09-09", "status": "open", "last_reviewed": ""})
    before = copy.deepcopy(ledger)
    selected = rc.usable_watch({"watch": ledger}, "2026-09-09")
    assert selected[0]["watch_id"] == "w41" and len(selected) == 8
    assert ledger == before


def test_storage_backstop_preserves_prior():
    ledger = [{"watch_id": f"w{i}", "trigger": f"事件{i}", "status": "open",
               "created": "2026-09-01", "deadline": "2026-09-29"} for i in range(1, 257)]
    out, _, dropped = rc.carry_watch({"watch": ledger},
                                    {"watch_triggers": [{"trigger": "新事件", "horizon": "1-4w"}]},
                                    "2026-09-09")
    assert len(out) == 256 and dropped == 1
    assert {w['watch_id'] for w in out} == {w['watch_id'] for w in ledger}


def test_reader_terms_scores_and_urls():
    source = "系統立場只有中性（-2）。系統把它折成-2，殖利率上升。ALERTS的解釋。ET（紅血球增多症）"
    cleaned = reader_prose.clean_text(source)
    assert "系統" not in cleaned and "-2" not in cleaned and "殖利率上升" in cleaned
    assert "ET（本態性血小板增多症）" in cleaned
    assert reader_prose.clean_text("https://example.org/ALERTS") == "https://example.org/ALERTS"
    assert facts.medical_terms("PV（真性紅血球增多症）") == "PV（真性紅血球增多症）"


def test_relevance_and_duplicates_keep_distinct_numbers_and_negation():
    assert not feeds.relevant("醫界追蹤", "泰山向食品業者求償10億元")
    assert feeds.relevant("醫界追蹤", "急診等床死亡後續調查")
    assert not feeds.relevant("學區/文教", "台中社宅獲建築獎")
    assert feeds.relevant("學區/文教", "台中社宅新設國小學區")
    assert not feeds.relevant("建設", "台中選戰造勢集結")
    rows = [{"title": s} for s in ("球員來台 私約 - A", "球員來台私約 - B",
                                   "球員不來台私約 - C", "球員來台2次 - D")]
    assert len(feeds.unique(rows)) == 3
    actual = [{"title": "NBA球星認了來台「私約小A辣」！ 小波特自爆：她是我的菜 - sports.ettoday.net"},
              {"title": "NBA球星認了來台 「私約小A辣」 本人自爆：她是我的菜 - facebook.com"}]
    assert feeds.unique(actual) == actual[:1]


def test_each_event_retains_its_own_time():
    packet = {"market": {"EVENT_CALENDAR": [
        {"title": "PPI", "date": "2026-09-10", "time": "20:30"},
        {"title": "[EUR] 主要再融資利率（Main Refinancing Rate）", "date": "2026-09-10", "time": "20:15"},
        {"title": "[EUR] ECB Press Conference", "date": "2026-09-10", "time": "20:45"}]}}
    text = facts.scenario_time("PPI 與 ECB", "20:30", packet)
    assert "PPI 2026-09-10 20:30" in text and "ECB 2026-09-10 20:15" in text
    assert facts.scenario_time("FOMC", "尚待確認", packet) == "尚待確認"
    assert "20:45" in facts.scenario_time("ECB 記者會", "未知", packet)


def test_elapsed_calendar_rows_removed_not_all_day():
    now = dt.datetime(2026, 9, 17, 6, tzinfo=ZoneInfo("Asia/Taipei"))
    past = {"date": now.date(), "time": "02:00"}
    future = {"date": now.date(), "time": "20:30"}
    unknown = {"date": now.date(), "time": "全天"}
    assert event_clock.future_rows([past, future, unknown], now) == [future, unknown]


def test_finance_evidence_coverage_not_routine_or_invented():
    news = [{"source_item_id": "n1", "title": "國泰人壽處分投資", "published": "2026-09-09"},
            {"source_item_id": "n2", "title": "中信金獲利創高", "published": "2026-09-09"},
            {"source_item_id": "n3", "title": "國泰卡優惠", "published": "2026-09-09"}]
    candidates = finance.analysis_candidates(news)
    packet = {"finance_analysis_candidates": candidates}
    assert len(candidates) == 2 and all("n3" not in ids for ids in candidates)
    assert len(finance.coverage_problems({"top_news_analysis": [{"source_item_id": "n2"}]}, packet)) == 1
    assert not finance.coverage_problems({"top_news_analysis": [
        {"source_item_id": "n1"}, {"source_item_id": "n2"}]}, packet)


def test_html_compaction_keeps_inline_spacing_preformatted_and_links():
    html = '<p><a href="https://example.org">all news</a> <b>kept</b></p>\n  <p>尾段</p><pre><p>A</p>\n <p>B</p></pre>'
    compact = email_markup.compact(html)
    assert '</p><p>尾段' in compact
    assert '</a> <b>' in compact and '<pre><p>A</p>\n <p>B</p></pre>' in compact
    assert 'href="https://example.org"' in compact
    assert email_markup.compact(compact) == compact


def test_fulltext_budget_does_not_bury_high_importance_under_low_official():
    news = [{"source_item_id": "high", "importance": "high", "link": "https://example.org/a"},
            {"source_item_id": "low", "importance": "low", "link": "https://example.org/b"}]
    clusters = [{"cluster_id": "a", "member_source_ids": ["low"], "official": True},
                {"cluster_id": "b", "member_source_ids": ["high"], "official": False}]
    assert fetch_plan.plan(news, clusters, budget=1)["targets"] == ["high"]


def test_unreviewed_expiry_has_telemetry(tmp_path):
    path = tmp_path / "recap.json"
    path.write_text(json.dumps({"watch": [{"watch_id": "w1", "trigger": "昨日到期",
                                          "created": "2026-09-07", "deadline": "2026-09-08",
                                          "last_reviewed": "", "status": "open"}]}), encoding="utf-8")
    manifest = {}
    assert rc.save(path, {}, {"target_session_date": "2026-09-09"}, manifest) == rc.SAVED
    assert manifest["llm"]["watch_expired_unreviewed"] == 1
    import run_quality
    assert any(f["code"] == "watch_expired_unreviewed" for f in run_quality.assess(manifest))


def test_finance_obligations_reach_packet_and_crosscheck():
    import evidence_packet as ep
    from analysis_crosscheck import _coverage_problems
    news = [{"source_item_id": "n1", "title": "國泰人壽處分投資", "published": "2026-09-09"}]
    packet = ep.build({}, {}, {}, news, [], {}, sanitize=lambda x: x)
    assert packet["finance_analysis_candidates"] == [["n1"]]
    assert _coverage_problems({}, packet, set())
    assert not finance.coverage_problems({"top_news_analysis": [{"source_item_id": "n1"}]}, packet)


def test_finance_materiality_checks_dated_originals_and_earnings_synonyms():
    for title in ("國泰金前八月稅後盈餘創高", "國泰金每股盈餘成長", "國泰金EPS增加"):
        assert finance.analysis_candidates([{"source_item_id": "n1", "title": title,
                                             "published": "2026-09-09"}]) == [["n1"]]
    merged = {"source_item_id": "n1", "title": "金融業公布八月成績",
              "published": "2026-09-09", "finance_headlines": [
                  {"title": "國泰金每股盈餘成長", "published": "2026-09-09"}]}
    assert finance.analysis_candidates([merged]) == [["n1"]]
    merged["finance_headlines"][0].pop("published")
    assert finance.analysis_candidates([merged]) == []
    assert finance.analysis_candidates([{"source_item_id": "n2", "title": "國泰金STEPS介紹",
                                         "published": "2026-09-09"}]) == []
