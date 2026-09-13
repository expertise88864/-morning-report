"""Weekly sources must stay traceable without unbounded prompt growth."""
import datetime as dt
import json
import copy
import pytest

import news_memory as memory
import week_review
from test_news_research import observation, write_atomic


def test_weekly_source_url_is_not_truncated_like_display_text(tmp_path):
    url = "https://example.com/article/" + "a" * 532
    row = observation(5, link=url)
    memory.save(tmp_path, [row], row["observed_at"], atomic_write=write_atomic)
    material = week_review.memory_material(tmp_path, dt.datetime(2026, 9, 6, 7, tzinfo=memory.TPE), sanitize=str)
    themes = json.loads(material.split("\n", 1)[1])
    assert themes[0]["representative_source"]["url"] == url


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
    assert all(t["related_sources"] for t in themes)  # No first-theme starvation.
    assert sum(t["omitted_for_budget"] for t in themes) > 0
    assert sum(len(t["related_sources"]) + t["omitted_for_budget"] for t in themes) == 30
    urls = {r["url"] for r in selected + list(refs.values())}
    for theme in themes:
        assert theme["representative_source"]["url"] in urls
        assert all(r["url"] in urls for r in theme["related_sources"])


@pytest.mark.parametrize('published,expected', [
    ('2026-09-04T18:00:00Z', 'same_day_reporting'),
    ('2026-09-04T00:00:00+08:00', 'earlier_reporting'),
    ('2026-09-06T00:00:00+08:00', 'later_reporting'),
    ('not-a-date', 'unknown'),
])
def test_weekly_sources_preserve_chronology_without_inventing_progress(published, expected):
    current = observation(5)
    source = dict(observation(1), published_at=published)
    before = copy.deepcopy(source)
    matches = {'n' + current['evidence_id'][-15:]: {'evidence_ids': ['h1']}}
    themes = week_review.bounded_themes([current], matches, {'h1': source}, sanitize=str)
    related = themes[0]['related_sources'][0]
    assert related['publication_relation'] == expected
    assert related['url'] == source['url']
    assert source == before
    assert 'preceding_sources' not in themes[0] and 'latest_source' not in themes[0]


def test_weekly_chronology_rules_are_outside_untrusted_data(tmp_path):
    prompt = week_review.build(dt.datetime(2026, 9, 6, 7, tzinfo=memory.TPE),
        load_history_state=lambda: [{'date': '2026-09-05', 'critical_news': ['測試新聞']}],
        EVENT_TIMELINE_FILE=tmp_path / 'missing.json', _external_text=lambda s, *a: s,
        _DEGRADED_STEPS=[], _register_state_corrupt=lambda *a: None)
    outside = prompt.split('<UNTRUSTED_SOURCE_DATA>', 1)[0]
    assert 'same_day_reporting=同日' in outside
    assert '較晚報導不可倒作前因' in outside
    assert '不為湊數編造演變' in prompt
    assert '跨週來源只用來說明本週主線的前因' not in prompt
