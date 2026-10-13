import copy
import datetime as dt
import json

import pytest

import polymarket_pulse_history as history


URL = "https://polymarket.com/event/example"
TPE = dt.timezone(dt.timedelta(hours=8))


def now(day, hour=6):
    return dt.datetime(2026, 10, day, hour, tzinfo=TPE)


def rows(prob=50, name="甲", key="id-1"):
    return [{"label": "Market", "source_url": URL, "detail": "old badges",
             "outcomes": [{"hist_key": key, "name": name, "prob_raw": prob,
                           "prob": round(prob), "low_vol": True, "wide": True}]}]


def write(path, text):
    assert isinstance(text, str)
    path.write_text(text, encoding="utf-8")


def test_persistence_copy_exact_yesterday_zero_and_fractional(tmp_path):
    path = tmp_path / "history.json"
    original = rows(50.25)
    untouched = copy.deepcopy(original)
    first = history.prepare(original, now(12), path, write)
    assert original == untouched and first is not original
    assert first[0]["detail"] == "甲 50.25%(昨日無快照)"
    assert history.prepare(rows(50.25), now(13), path, write)[0]["detail"] == "甲 50.25%(0pp)"
    assert history.prepare(rows(50.5), now(13, 8), path, write)[0]["detail"] == "甲 50.5%(↑0.25pp)"
    assert history.prepare(rows(50.1), now(14), path, write)[0]["detail"] == "甲 50.1%(↓0.4pp)"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["days"]["2026-10-13"][URL]["id-1"] == 50.5


def test_gaps_never_compare_last_available_or_another_market(tmp_path):
    path = tmp_path / "history.json"
    history.prepare(rows(), now(12), path, write)
    assert "昨日無快照" in history.prepare(rows(60), now(14), path, write)[0]["detail"]
    other = rows(60)
    other[0]["source_url"] += "-other"
    assert "昨日無快照" in history.prepare(other, now(15), path, write)[0]["detail"]


def test_sunday_compares_same_week_monday_and_missing_baselines(tmp_path):
    path = tmp_path / "history.json"
    history.prepare(rows(40), now(12), path, write)  # Monday
    history.prepare(rows(50), now(17), path, write)
    result = history.prepare(rows(50.5), now(18), path, write)[0]
    assert result["detail"] == "甲 50.5%(↑0.5pp)"
    assert result["weekly_detail"] == "甲 50.5%(↑10.5pp)"
    next_week = history.prepare(rows(60), now(25), path, write)[0]
    assert "昨日無快照" in next_week["detail"]
    assert next_week["weekly_detail"] == "甲 60%(↑9.5pp)"
    assert next_week["weekly_baseline_label"] == "較上週日 2026-10-18"
    assert "weekly_detail" not in history.prepare(rows(), now(26), path, write)[0]


def test_stable_ids_display_limit_and_store_hidden_outcomes(tmp_path):
    path = tmp_path / "history.json"
    initial = rows(80)
    initial[0]["display_limit"] = 1
    initial[0]["outcomes"].append({"hist_key": "id-2", "name": "乙", "prob": 20})
    result = history.prepare(initial, now(12), path, write)
    assert "乙" not in result[0]["detail"]
    renamed = rows(21, name="新譯名", key="id-2")
    assert history.prepare(renamed, now(13), path, write)[0]["detail"] == "新譯名 21%(↑1pp)"
    assert len(json.loads(path.read_text(encoding="utf-8"))["days"]["2026-10-12"][URL]) == 2


def test_name_fallback_and_taipei_date(tmp_path):
    path = tmp_path / "history.json"
    data = rows()
    del data[0]["outcomes"][0]["hist_key"]
    utc = dt.datetime(2026, 10, 11, 22, tzinfo=dt.timezone.utc)
    history.prepare(data, utc, path, write)
    assert history.prepare(data, now(13), path, write)[0]["detail"] == "甲 50%(0pp)"


def test_retention_includes_exactly_35_calendar_dates(tmp_path):
    path = tmp_path / "history.json"
    start = now(1)
    for offset in range(40):
        history.prepare(rows(), start + dt.timedelta(days=offset), path, write)
    dates = sorted(json.loads(path.read_text(encoding="utf-8"))["days"])
    assert len(dates) == 35 and dates[0] == "2026-10-06" and dates[-1] == "2026-11-09"


@pytest.mark.parametrize("content", [
    "not json", "[]", '{}', '{"version":true,"days":{}}',
    '{"version":1,"version":1,"days":{}}',
    '{"version":1,"days":{"2026-10-99":{}}}',
    '{"version":1,"days":{"2026-10-20":{}}}',
    json.dumps({"version": 1, "days": {"2026-10-12": {URL: {"id-1": True}}}}),
    json.dumps({"version": 1, "days": {"2026-10-12": {URL: {"id-1": 101}}}}),
    json.dumps({"version": 1, "days": {"2026-10-12": {URL: {"id-1": float("nan")}}}}),
    json.dumps({"version": 1, "days": {"2026-10-12": {URL: []}}}),
])
def test_corrupt_history_raises_without_overwrite(tmp_path, content):
    path = tmp_path / "history.json"
    path.write_text(content, encoding="utf-8")
    calls = []
    with pytest.raises(ValueError):
        history.prepare(rows(), now(13), path, lambda *args: calls.append(args))
    assert not calls and path.read_text(encoding="utf-8") == content


def test_collect_preserves_prices_warns_and_never_overwrites_corrupt_state(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("broken", encoding="utf-8")
    warnings, writes, fetches = [], [], []
    def fetch():
        fetches.append(True)
        return rows(50.5)
    result = history.collect(fetch, now(18), path, lambda *a: writes.append(a), warnings.append)
    assert len(fetches) == 1 and len(warnings) == 1 and not writes
    assert result[0]["detail"] == "甲 50.5%(昨日無快照)"
    assert result[0]["weekly_detail"] == "甲 50.5%(基準日無快照)"
    assert result[0]["weekly_baseline_label"] == "無可用基準"
    assert path.read_text(encoding="utf-8") == "broken"


def test_write_failure_propagates_and_fetch_failure_is_not_swallowed(tmp_path):
    path = tmp_path / "history.json"
    def fail(*args):
        raise OSError("fixture")
    with pytest.raises(OSError):
        history.prepare(rows(), now(13), path, fail)
    with pytest.raises(OSError):
        history.collect(fail, now(13), path, write, lambda _: None)


@pytest.mark.parametrize("bad", [True, -1, 101, float("inf"), "50", None])
def test_invalid_current_probability_never_writes(tmp_path, bad):
    data = rows()
    data[0]["outcomes"][0]["prob_raw"] = bad
    calls = []
    with pytest.raises(ValueError):
        history.prepare(data, now(13), tmp_path / "history.json", lambda *a: calls.append(a))
    assert not calls


def test_same_day_partial_retry_preserves_available_outcomes(tmp_path):
    path = tmp_path / "history.json"
    initial = rows(80)
    initial[0]["outcomes"].append({"hist_key": "id-2", "name": "乙", "prob": 20})
    history.prepare(initial, now(12), path, write)
    history.prepare(rows(81), now(12, 8), path, write)
    result = history.prepare(rows(21, name="乙", key="id-2"), now(13), path, write)
    assert result[0]["detail"] == "乙 21%(↑1pp)"


@pytest.mark.parametrize("case", ["market", "outcome", "limit", "url", "empty", "name"])
def test_invalid_row_identity_or_shape_never_writes(tmp_path, case):
    data = rows()
    if case == "market":
        data.append(copy.deepcopy(data[0]))
    elif case == "outcome":
        data[0]["outcomes"].append(copy.deepcopy(data[0]["outcomes"][0]))
    elif case == "limit":
        data[0]["display_limit"] = True
    elif case == "url":
        data[0]["source_url"] = "javascript:alert(1)"
    elif case == "empty":
        data[0]["outcomes"] = []
    else:
        data[0]["outcomes"][0]["name"] = ""
    writes = []
    with pytest.raises(ValueError):
        history.prepare(data, now(13), tmp_path / "history.json", lambda *a: writes.append(a))
    assert not writes


def test_collect_write_failure_does_not_retry_or_reuse_old_baseline(tmp_path):
    path = tmp_path / "history.json"
    history.prepare(rows(40), now(12), path, write)
    original = path.read_bytes()
    attempts, warnings = [], []
    def fail(*args):
        attempts.append(True)
        raise OSError("fixture")
    result = history.collect(lambda: rows(50), now(13), path, fail, warnings.append)
    assert result[0]["detail"] == "甲 50%(昨日無快照)"
    assert len(attempts) == len(warnings) == 1
    assert path.read_bytes() == original


def test_sunday_zero_and_per_outcome_missing_baselines(tmp_path):
    path = tmp_path / "history.json"
    history.prepare(rows(50), now(12), path, write)
    history.prepare(rows(50), now(17), path, write)
    current = rows(50)
    current[0]["outcomes"].append({"name": "新選項", "prob": 0})
    result = history.prepare(current, now(18), path, write)[0]
    assert result["detail"] == "甲 50%(0pp)・新選項 0%(昨日無快照)"
    assert result["weekly_detail"] == "甲 50%(0pp)・新選項 0%(基準日無快照)"


@pytest.mark.parametrize("dates,expected_label,expected_change", [
    ([11, 12, 13], "較週一 2026-10-12", "↑8pp"),
    ([11, 13], "較週二 2026-10-13", "↑7pp"),
    ([11], "較上週日 2026-10-11", "↑9pp"),
    ([10, 14], "無可用基準", "基準日無快照"),
])
def test_weekly_baseline_priority_and_no_arbitrary_substitution(tmp_path, dates, expected_label, expected_change):
    path = tmp_path / "history.json"
    for day in dates:
        history.prepare(rows(day), now(day), path, write)
    result = history.prepare(rows(20), now(18), path, write)[0]
    assert result["weekly_baseline_label"] == expected_label
    assert result["weekly_detail"] == f"甲 20%({expected_change})"
    assert result["detail"] == "甲 20%(昨日無快照)"


def test_fallback_is_market_specific_and_does_not_use_unrelated_options(tmp_path):
    path = tmp_path / "history.json"
    history.prepare(rows(10, key="other"), now(12), path, write)
    history.prepare(rows(20), now(13), path, write)
    result = history.prepare(rows(30), now(18), path, write)[0]
    assert result["weekly_baseline_label"] == "較週二 2026-10-13"
    assert result["weekly_detail"] == "甲 30%(↑10pp)"


@pytest.mark.parametrize("previous,current,expected", [
    (10, .146 * 100, "甲 14.6%(↑4.6pp)"),
    (.146 * 100, 10, "甲 10%(↓4.6pp)"),
    (10, 14.56789, "甲 14.57%(↑4.57pp)"),
    (10, 10.004, "甲 10%(↑<0.01pp)"),
    (10, 9.996, "甲 10%(↓<0.01pp)"),
    (10, 10.009, "甲 10.01%(↑<0.01pp)"),
    (10, 10.01, "甲 10.01%(↑0.01pp)"),
    (10, 10, "甲 10%(0pp)"),
])
def test_precision_and_small_changes_in_daily_and_weekly_detail(tmp_path, previous, current, expected):
    path = tmp_path / "history.json"
    history.prepare(rows(previous), now(12), path, write)
    history.prepare(rows(previous), now(17), path, write)
    result = history.prepare(rows(current), now(18), path, write)[0]
    assert result["detail"] == result["weekly_detail"] == expected
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["days"]["2026-10-18"][URL]["id-1"] == current


def test_missing_baseline_also_formats_probability_to_two_places(tmp_path):
    result = history.prepare(rows(.146 * 100), now(13), tmp_path / "history.json", write)
    assert result[0]["detail"] == "甲 14.6%(昨日無快照)"
