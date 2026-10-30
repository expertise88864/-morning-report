# -*- coding: utf-8 -*-
"""2026-10-30 使用者:新聞條數、正確性、排序 —— 四項修正的回歸測試。

1. 全文不再抓媒體首頁:Google News 轉址沒夾原文網址時,不拿 RSS `<source>` 的首頁頂替
   (10/30 實害:兩則 UDN 新聞的「全文」都是 OPPO 發表會)。規劃器的「可抓」判準同步。
2. 全文改並行抓、額度 26 → 48;順序仍是優先序,時間地板仍擋新的一篇。
3. 備援 prompt 各重要性層先排再截(獨立媒體數 → 官方 → 分級 → 全文 → 新鮮度)。
4. 市值前百大當日漲跌 ≥7% 補查自家新聞;寫作規則 R21 同主題正反並陳。
"""
from __future__ import annotations

import threading

import fetch_plan as fp
import fulltext_fetch as ftf
import morning_report as mr
import mover_news
import news_rules
import writing_rules

_GNEWS = "https://news.google.com/rss/articles/CBMiVkFVX3lxTE1vTUNTb1BBaVJZNXVGR?oc=5"


# ---------- 1. 全文不抓首頁 ----------

def test_google_news_without_embedded_url_never_falls_back_to_publisher_homepage():
    item = {"link": _GNEWS, "source_url": "https://udn.com",
            "publisher_url": "https://udn.com/news/index"}
    assert mr._fulltext_target_link(item) == ""


def test_google_news_with_embedded_url_uses_that_article():
    link = "https://news.google.com/articles/x?url=https://money.udn.com/a/1"
    assert mr._fulltext_target_link({"link": link}) == "https://money.udn.com/a/1"


def test_fetch_one_does_not_request_anything_for_an_undecodable_google_link(monkeypatch):
    calls = []
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: calls.append(a) or None)
    item = {"link": _GNEWS, "source_url": "https://udn.com"}
    assert mr._fetch_one_fulltext(item, 8, 2000) is False
    assert calls == [] and "fulltext" not in item


def test_planner_fetchable_rule_matches_the_fetcher():
    assert fp._fetchable({"link": "https://news.cnyes.com/news/id/1"})
    assert not fp._fetchable({"link": _GNEWS, "source_url": "https://udn.com"})
    assert fp._fetchable({"link": "https://news.google.com/a?url=https://cna.com.tw/x"})
    # 沒有 link、只有 source_url(首頁)→ 抓取端不會抓,規劃器也不能排
    assert not fp._fetchable({"source_url": "https://udn.com"})
    assert not fp._fetchable({"link": "https://cna.com.tw/x", "fulltext": "已有"})


def test_planner_spends_the_slot_on_the_fetchable_member_of_the_same_event():
    news = [{"source_item_id": "g", "link": _GNEWS, "importance": "high"},
            {"source_item_id": "d", "link": "https://news.cnyes.com/news/id/9",
             "importance": "high"}]
    clusters = [{"cluster_id": "c1", "member_source_ids": ["g", "d"],
                 "representative_source_id": "g", "independent_sources": 2}]
    plan = fp.plan(news, clusters)
    assert plan["targets"] == ["d"]
    assert plan["fetchable_candidates"] == 1 and plan["no_fetch_link"] == 1


def test_budget_was_raised_for_parallel_fetching():
    assert fp.BUDGET == 48
    assert ftf.WORKERS >= 2


# ---------- 2. 並行抓取 ----------

def _items(n):
    return [{"source_item_id": f"s{i}", "importance": "critical" if i == 0 else "high"}
            for i in range(n)]


def test_parallel_fetch_counts_successes_and_dedupes_ids():
    news, seen, lock = _items(5), [], threading.Lock()

    def fetch_one(item, timeout, limit):
        with lock:
            seen.append((item["source_item_id"], timeout, limit))
        item["fulltext"] = "x"
        return item["source_item_id"] != "s3"

    got = ftf.fetch_targets(news, ["s0", "s1", "s1", "s3", "missing"], fetch_one,
                            lambda: 999.0, 120.0)
    assert got == 2
    assert sorted(s for s, _, _ in seen) == ["s0", "s1", "s3"]
    assert ("s0", 10, 2500) in seen and ("s1", 8, 2000) in seen


def test_parallel_fetch_starts_nothing_below_the_time_floor():
    calls = []
    got = ftf.fetch_targets(_items(4), ["s0", "s1"], lambda *a: calls.append(a) or True,
                            lambda: 50.0, 120.0)
    assert got == 0 and calls == []


def test_one_failing_fetch_does_not_sink_the_batch():
    def fetch_one(item, *_):
        if item["source_item_id"] == "s1":
            raise RuntimeError("boom")
        return True

    assert ftf.fetch_targets(_items(3), ["s0", "s1", "s2"], fetch_one,
                             lambda: 999.0, 120.0, workers=3) == 2


def test_morning_report_routes_planned_targets_through_the_parallel_fetcher(monkeypatch):
    news = _items(3)
    monkeypatch.setattr(mr, "_run_seconds_left", lambda: 999.0)
    monkeypatch.setattr(mr, "_fetch_one_fulltext",
                        lambda item, *_: item.__setitem__("fulltext", "ok") or True)
    out = mr.fetch_news_fulltext(news, targets=["s2", "s0"])
    assert out is news
    assert [n.get("fulltext") for n in news] == ["ok", None, "ok"]


# ---------- 3. 先排再截 ----------

def test_prompt_order_ranks_by_coverage_then_source_quality_then_freshness():
    items = [
        {"title": "單一來源早", "merged_n": 1, "published_dt": "2026-10-29T01:00:00+00:00"},
        {"title": "三家報導", "merged_n": 3},
        {"title": "單一來源晚", "merged_n": 1, "published_dt": "2026-10-29T20:00:00+00:00"},
        {"title": "官方", "merged_n": 1, "official": True},
        {"title": "壞時間", "merged_n": 1, "published_dt": "not-a-date"},
    ]
    got = [n["title"] for n in news_rules.prompt_order(items)]
    assert got[:2] == ["三家報導", "官方"]
    assert got.index("單一來源晚") < got.index("單一來源早")


def test_prompt_order_is_stable_and_does_not_mutate():
    items = [{"title": t, "merged_n": 1} for t in "abc"]
    assert [n["title"] for n in news_rules.prompt_order(items)] == ["a", "b", "c"]
    assert [n["title"] for n in items] == ["a", "b", "c"]


def test_a_widely_covered_normal_item_survives_the_30_item_cut():
    from test_deepseek_legacy_golden import legacy_prompt_inputs
    quotes, fair, preds, _news, tw0050, calib = legacy_prompt_inputs()
    filler = [{"title": f"一般小新聞{i:02d}", "summary": "", "source": "某站",
               "importance": "normal", "category": "general", "link": f"https://x.tw/{i}"}
              for i in range(35)]
    late = {"title": "微軟Azure成長48%盤後漲4%", "summary": "", "source": "鉅亨網",
            "importance": "normal", "category": "general", "merged_n": 4,
            "link": "https://news.cnyes.com/news/id/7"}
    prompt = mr._build_prompt(quotes, fair, preds, filler + [late], tw0050, calib)
    assert "微軟Azure成長48%盤後漲4%" in prompt
    assert "一般小新聞34" not in prompt     # 截掉的是單一來源的尾巴


# ---------- 4. 大漲大跌補查 + R21 ----------

def _row(code, pct, score=0.0):
    return {"code": code, "name": f"N{code}", "day_pct": pct, "breakout": {"score": score}}


def test_movers_are_picked_by_absolute_move_and_skip_already_queried_codes():
    snap = [_row("3008", -8.2), _row("2303", -9.8), _row("2330", 7.5),
            _row("1101", 3.0), _row("2454", 9.0, score=5.0), _row("9999", float("nan"))]
    got = mover_news.picks(snap, exclude={"2330"})
    # 2330 在固定清單、2454 是爆發力候選(已查過);1101 未達門檻;NaN 不算
    assert [r["code"] for r in got] == ["2303", "3008"]
    assert got[0]["breakout"] == {"score": 9.8}
    assert snap[1]["breakout"] == {"score": 0.0}       # 淺拷貝,不改原資料


def test_movers_respect_the_limit():
    snap = [_row(str(1000 + i), -7.0 - i * 0.1) for i in range(12)]
    assert len(mover_news.picks(snap)) == mover_news.LIMIT


def test_mover_fetch_hands_rows_to_the_existing_company_query():
    seen = {}

    def fetcher(rows, top_n, exclude_codes):
        seen.update(codes=[r["code"] for r in rows], top_n=top_n, exclude=exclude_codes)
        return [{"title": "大立光法說", "company_label": "3008"}]

    out = mover_news.fetch([_row("3008", -8.2)], fetcher, {"2330"})
    assert out == [{"title": "大立光法說", "company_label": "3008"}]
    assert seen == {"codes": ["3008"], "top_n": 1, "exclude": {"2330"}}


def test_mover_fetch_never_raises_and_skips_quiet_days():
    def boom(*a, **k):
        raise RuntimeError("Google 503")

    assert mover_news.fetch([_row("3008", -8.2)], boom) == []
    assert mover_news.fetch([_row("3008", -1.0)], boom) == []


def test_mover_news_is_wired_into_the_candidate_query_step():
    import inspect
    src = inspect.getsource(mr)
    assert "_mover_news.fetch(tw0050, fetch_candidate_company_news, _fixed)" in src


def test_r21_opposing_signals_rule_is_in_the_legacy_prompt_rules():
    assert "R21. **同主題正反並陳**" in writing_rules.LEGACY_RULES
    assert "微軟 Azure" in writing_rules.LEGACY_RULES
