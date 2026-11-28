"""11-28 實信的問題與「選後不再顯示台灣選舉賭盤」(使用者 11-28)。"""
import datetime as dt

import pytest
import requests

import local_news_routing as lnr
import morning_report as mr
import net_retry
import report_polish as rp
from news_display_quality import relevant


def test_sections_are_numbered_in_the_order_they_appear():
    """敘事變化缺席時,世界大事之後接六 → 改成依實際段落連續編號。"""
    html = ("<h2>二、總經指標</h2><h2>五、行情與開盤展望</h2><h2>七、昨夜三大重點</h2>"
            "<h2>七之二、世界大事速覽</h2><h2>八、科技板塊脈動</h2><h2>九、其他類股資訊</h2>")
    out = rp.display_pass(html)
    for want in ("一、總經指標", "四、世界大事速覽", "五、科技板塊脈動", "六、其他類股資訊"):
        assert want in out
    assert "A 級＝有具體數字" in out   # 科技段的 A/B 級說明仍接得上


def test_00662_pct_matches_the_displayed_fair_price(fake_yf):
    fake_yf({})
    res = mr.calc_00662_fair_value(515.0, 501.5, 32.0, 22.64, usdtwd_prev=32.0)
    assert res["implied_change_pct"] == round((res["fair_price"] / 22.64 - 1) * 100, 2)


def test_a_timeout_is_retried_on_the_same_day(monkeypatch):
    seen = []

    def call(t):
        seen.append(t)
        if t == 15:
            raise requests.Timeout("read timed out")
        return "ok"
    assert net_retry.retry_timeout(call) == "ok" and seen == [15, 45]
    with pytest.raises(requests.ConnectionError):
        net_retry.retry_timeout(lambda t: (_ for _ in ()).throw(requests.ConnectionError()))


def test_taifex_foreign_oi_tries_the_same_day_twice_before_falling_back(monkeypatch):
    calls = []

    def post(url, data=None, timeout=None, headers=None):
        calls.append((data["queryStartDate"], timeout))
        raise requests.Timeout("read timed out")
    monkeypatch.setattr(mr.requests, "post", post)
    assert mr.fetch_taifex_foreign_futures() == {}
    assert calls[0][1] == 15 and calls[1] == (calls[0][0], 45)


def test_local_news_drops_facebook_posts_and_routes_rallies_to_elections():
    assert not relevant("建商動態", "【盧秀燕壓軸登場！喊話台中一定要交棒江啟臣】 （#起啵） - Facebook")
    assert lnr.destination("建設", "陳素月選前之夜今晚登場～ 蕭美琴、卓榮泰壓軸站台！彰化隊合體催票 - 蕃新聞") == "選情"
    assert lnr.destination("建設", "台中捷運藍線動工 - 自由時報") == "建設"


def test_taiwan_election_party_market_stops_after_election_day(monkeypatch):
    slugs = []
    monkeypatch.setattr(mr, "_poly_search_events", lambda *a, **k: [])
    monkeypatch.setattr(mr, "_poly_events", lambda *a, **k: [])
    monkeypatch.setattr(mr, "_poly_outright", lambda slug, *a, **k: slugs.append(slug) or [])
    mr.fetch_polymarket_pulse(dt.datetime(2026, 11, 28, 6, 0, tzinfo=mr.TPE))
    assert "2026-taiwanese-local-elections-party-winner" in slugs
    slugs.clear()
    mr.fetch_polymarket_pulse(dt.datetime(2026, 11, 29, 6, 0, tzinfo=mr.TPE))
    assert "2026-taiwanese-local-elections-party-winner" not in slugs
    assert "which-party-wins-2028-us-presidential-election" in slugs


def test_a_long_facebook_title_is_dropped_even_after_truncation():
    """外審 PR #71:正式入口先把標題截成 90 字,「- Facebook」被截掉也要擋。"""
    raw = "【盧秀燕壓軸登場！喊話台中一定要交棒江啟臣：他只會聽市民的話】" + "台中" * 40 + " - Facebook"
    item = {"title": raw[:90], "link": "x", "raw": raw}
    out = lnr.select([("建商動態", item)], [("建商動態", "q", 3)], 3,
                     is_dup=lambda t, seen: False, seen_entry=lambda t: t)
    assert out == {}
    ok = {"title": "台中建商推出新案", "link": "y", "raw": "台中建商推出新案 - 自由時報"}
    kept = lnr.select([("建商動態", ok)], [("建商動態", "q", 3)], 3, is_dup=lambda t, seen: False, seen_entry=lambda t: t)
    assert kept == {"建商動態": [{"title": "台中建商推出新案", "link": "y"}]}   # raw 不外流


def test_taiwan_presidential_market_is_hidden_after_election_day(monkeypatch):
    """外審 PR #71:使用者要的是「台灣選舉賭盤」都不顯示,2028 總統盤開盤也不出現。"""
    search = [{"title": "Taiwan Presidential Election 2028", "slug": "tw-2028", "endDate": "2028-01-13T00:00:00Z"}]
    monkeypatch.setattr(mr, "_poly_search_events", lambda q, *a, **k: search if "presidential" in q else [])
    monkeypatch.setattr(mr, "_poly_events", lambda *a, **k: [])
    monkeypatch.setattr(mr, "_poly_outright", lambda slug, *a, **k: [{"name": "民進黨", "prob": 50.0}] if slug == "tw-2028" else [])
    monkeypatch.setattr(mr, "_poly_annotate_deltas", lambda *a, **k: None)
    monkeypatch.setattr(mr, "_poly_prob_line", lambda outs: "民進黨 50%")
    labels = lambda day: {r["label"] for r in mr.fetch_polymarket_pulse(dt.datetime(2026, 11, day, 6, 0, tzinfo=mr.TPE))}  # noqa: E731
    assert "台灣總統大選" in labels(28) and "台灣總統大選" not in labels(29)


def test_routing_sees_keywords_past_the_truncation_point():
    """外審 PR #71 第二輪:「造勢」落在第 90 字之後時,要歸到選情,不是在建設被丟掉。"""
    raw = "彰化市" + "地方建設說明" * 16 + "今晚造勢晚會登場 - 自由時報"
    out = lnr.select([("建設", {"title": raw[:90], "link": "x", "raw": raw})],
                     [("建設", "q", 3), ("選情", "q", 3)], 3, is_dup=lambda t, seen: False, seen_entry=lambda t: t)
    assert list(out) == ["選情"] and out["選情"][0]["title"] == raw[:90]
