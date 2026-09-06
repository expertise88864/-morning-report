"""Weekly sources must stay traceable without unbounded prompt growth."""
import datetime as dt
import json

import news_memory as memory
import week_review
from test_news_research import observation, write_atomic


def test_weekly_source_url_is_not_truncated_like_display_text(tmp_path):
    url = "https://example.com/article/" + "a" * 532
    row = observation(5, link=url)
    memory.save(tmp_path, [row], row["observed_at"], atomic_write=write_atomic)
    material = week_review.memory_material(tmp_path, dt.datetime(2026, 9, 6, 7, tzinfo=memory.TPE), sanitize=str)
    themes = json.loads(material.split("\n", 1)[1])
    assert themes[0]["latest_source"]["url"] == url


def test_oversized_url_is_explicitly_missing_not_a_broken_link():
    row = observation(link="https://example.com/" + "b" * 2200)
    brief = week_review.source_brief(row, sanitize=str)
    assert brief["url"] == "" and brief["url_omitted"] is True
    assert brief["title"] == row["title"]


def test_weekly_budget_trims_sources_not_their_urls():
    selected, matches, refs = [], {}, {}
    for i in range(5):
        row = observation(5, title=f"主題{i}" + "字" * 295,
                          summary="摘錄" * 600, link=f"https://example.com/{i}/" + "c" * 1800)
        selected.append(row)
        ids = []
        for j in range(6):
            old = observation(1, title=f"主題{i}前情{j}" + "字" * 290,
                              summary="摘錄" * 600, link=f"https://example.com/{i}/{j}/" + "d" * 1800)
            refs[old["evidence_id"]] = old
            ids.append(old["evidence_id"])
        matches["n" + row["evidence_id"][-15:]] = {"evidence_ids": ids}
    themes = week_review.bounded_themes(selected, matches, refs, sanitize=str)
    assert len(themes) == 5
    assert len(json.dumps(themes, ensure_ascii=False)) <= week_review.MAX_SOURCE_CHARS
    assert all(t["preceding_sources"] for t in themes)  # No first-theme starvation.
    assert sum(t["omitted_for_budget"] for t in themes) > 0
    assert sum(len(t["preceding_sources"]) + t["omitted_for_budget"] for t in themes) == 30
    urls = {r["url"] for r in selected + list(refs.values())}
    for theme in themes:
        assert theme["latest_source"]["url"] in urls
        assert all(r["url"] in urls for r in theme["preceding_sources"])
