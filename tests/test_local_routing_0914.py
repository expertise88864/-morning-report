"""Offline end-to-end regressions for local-news category routing."""
import datetime as dt
from types import SimpleNamespace

import morning_report as mr


def fetch(monkeypatch, feeds, limit=2):
    now = dt.datetime.now(dt.timezone.utc).timetuple()
    monkeypatch.setattr(mr, "LOCAL_NEWS_QUERIES", [(key, key) for key in feeds])
    monkeypatch.setattr(mr, "_gnews_rss", lambda query, **kw: query)
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout", lambda query:
                        SimpleNamespace(entries=[dict(title=t, link=f"https://example.invalid/{i}",
                                                      published_parsed=now)
                                                 for i, t in enumerate(feeds[query])]))
    monkeypatch.setattr(mr.requests, "get", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("network forbidden")))
    return mr.fetch_local_news(per_label=limit)


def test_actual_incident_titles_move_not_disappear(monkeypatch):
    rally = "陳文賓競總湧5千人相挺 謝龍介力挺：彰化需要能立即上手的市長"
    housing = "台中14期928推案 上看200億"
    out = fetch(monkeypatch, {"建設": [rally], "產業/科技": [housing], "選情": [], "房市": []})
    assert set(out) == {"選情", "房市"}
    assert out["選情"][0]["title"] == rally
    assert out["房市"][0]["title"] == housing


def test_election_vocabulary_is_not_dropped_or_left_in_construction(monkeypatch):
    titles = ["彰化縣長競選活動今晚登場", "台中市長選舉登記開跑"]
    out = fetch(monkeypatch, {"建設": titles, "選情": []})
    assert list(out) == ["選情"]
    assert [row["title"] for row in out["選情"]] == titles


def test_misrouted_items_do_not_consume_source_capacity(monkeypatch):
    rally = "彰化競總成立大會"
    construction = "台中捷運延伸線正式動工"
    out = fetch(monkeypatch, {"建設": [rally, construction], "選情": []}, limit=1)
    assert out["建設"][0]["title"] == construction
    assert out["選情"][0]["title"] == rally


def test_genuine_infrastructure_and_factory_news_stay(monkeypatch):
    a, b = "台中市長宣布捷運動工", "台中晶圓廠新廠房動工"
    out = fetch(monkeypatch, {"建設": [a], "產業/科技": [b], "選情": [], "房市": []})
    assert out["建設"][0]["title"] == a
    assert out["產業/科技"][0]["title"] == b


def test_destination_limit_and_duplicate_are_preserved(monkeypatch):
    a, b = "彰化競總成立大會", "台中市長選舉最新民調"
    out = fetch(monkeypatch, {"建設": [a], "選情": [a, b]}, limit=1)
    assert list(out) == ["選情"]
    assert len(out["選情"]) == 1


def test_specialist_scope_and_input_are_preserved():
    import copy
    from local_news_routing import select
    candidates = [("彰基/中國醫", {"title": "彰基住宅照護新方案", "link": "https://example.invalid/a"})]
    before = copy.deepcopy(candidates)
    result = select(candidates, [("彰基/中國醫", "x", 3)], 1,
                    is_dup=mr._local_title_is_dup, seen_entry=mr._local_seen_entry)
    assert result["彰基/中國醫"] == [candidates[0][1]]
    assert candidates == before


def test_full_bucket_does_not_hide_a_later_category():
    from local_news_routing import select
    a = {"title": "彰化鐵路高架通車", "link": "https://example.invalid/a"}
    b = {"title": "台中捷運藍線施工公告", "link": "https://example.invalid/b"}
    result = select([("彰化重點追蹤", a), ("彰化重點追蹤", b), ("建設", b)],
                    [("彰化重點追蹤", "x", 1), ("建設", "y", 2)], 2,
                    is_dup=mr._local_title_is_dup, seen_entry=mr._local_seen_entry)
    assert result == {"彰化重點追蹤": [a], "建設": [b]}
