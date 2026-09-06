"""Independent-review regressions: inference boundaries and archive continuity."""
import copy
import datetime as dt
from types import SimpleNamespace

import pytest

import analysis_schema
import fixtures_analysis as fx
import morning_report as mr
import news_memory as memory
import news_research_context as context
import news_research_runtime as runtime
import run_manifest
from test_news_research import article, observation, packet, write_atomic
from tools.evaluate_news_research import evaluate


@pytest.mark.parametrize("path", [
    ("top_news_analysis", 0, "affected_assets", 0, "evidence_ids"),
    ("top_news_analysis", 0, "mechanism_steps", 0, "evidence_ids"),
    ("key_drivers", 0, "evidence_ids"),
    ("claim_audit", 0, "evidence_ids"),
    ("claim_audit", 0, "counterevidence_ids"),
])
def test_production_validator_rejects_history_outside_dated_context(path):
    pk, obj = packet(), fx.valid_analysis()
    eid = pk["historical_sources"][0]["evidence_id"]
    assert not any("歷史 ID 僅限" in p for p in analysis_schema.validate(obj, pk))
    target = obj
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = [eid]
    assert any("歷史 ID 僅限" in p for p in analysis_schema.validate(obj, pk))


@pytest.mark.parametrize("field", sorted(analysis_schema.evidence_id_fields()))
def test_every_schema_evidence_array_enforces_the_history_boundary(field):
    # Schema-derived names cover present and future evidence fields, including
    # nested current-effect/watch/narrative structures, not just claim_audit.
    pk = packet()
    eid = pk["historical_sources"][0]["evidence_id"]
    valid_rows = [{"source_item_id": "n1", "why_it_matters": "尚缺投產證據，無法量化。"}]
    assert any("歷史 ID 僅限" in p for p in context.validate(
        {"top_news_analysis": valid_rows, "nested": [{"nested": {field: [eid, "n1"]}}]}, pk))
    assert not context.validate(
        {"top_news_analysis": valid_rows, "nested": [{"nested": {field: ["n1"]}}]}, pk)


def test_updated_only_feed_keeps_canonical_time_in_memory_and_replay(tmp_path):
    item = article(published="", updated="2026-09-01T06:00:00+08:00")
    mr._mark_news_date_quality(item, mr._entry_published_dt(item))
    assert not item["date_missing"] and item["published_dt"]
    original = copy.deepcopy(item)
    rows, skipped = memory.observations([item], "2026-09-01T07:00+08:00", sanitize=str)
    assert len(rows) == 1 and skipped["undated"] == 0
    assert rows[0]["published_at"] == item["published_dt"]
    assert context.snapshot([item])["undated"] == 0
    assert evaluate(tmp_path, [item], "2026-09-02T07:00+08:00")["news_available"] == 1
    assert evaluate(tmp_path, [item], "2026-09-01T05:00+08:00")["news_available"] == 0
    assert item == original


def test_old_corruption_does_not_stop_today_but_stays_visible(tmp_path):
    now = dt.datetime.now(memory.TPE)
    old = tmp_path / f"{now - dt.timedelta(days=2):%Y-%m-%d}.json.gz"
    old.write_bytes(b"broken old history")
    healthy = observation()
    healthy["observed_at"] = (now - dt.timedelta(days=1)).isoformat()
    memory.save(tmp_path, [healthy], healthy["observed_at"], atomic_write=write_atomic)
    current = article(published=(now - dt.timedelta(hours=1)).isoformat())
    ctx = SimpleNamespace(news=[current], quotes={}, recorder=run_manifest.ManifestRecorder())
    calls = []
    runtime.enrich(ctx, tmp_path, sanitize=str, atomic_write=write_atomic,
                   fetch_feed=lambda *a, **kw: calls.append(a) or {"entries": []},
                   make_url=mr._gnews_rss, entry_time=mr._entry_published_dt,
                   entry_source=mr._tw_entry_source, time_left=lambda: 1000, reserve=120)
    assert calls  # Independent discovery still runs.
    assert memory.read_partition(tmp_path / f"{now:%Y-%m-%d}.json.gz")
    assert healthy in ctx.quotes["NEWS_MEMORY"]
    assert old.read_bytes() == b"broken old history"
    diag = ctx.recorder.data["news"]["research"]
    assert diag["memory_status"] == "partial_history"
    assert diag["unreadable_partitions"] == [old.name]
    assert "news_memory" in ctx.recorder.degraded
    # Audits/state publication remain strict: no corruption is labelled healthy.
    with pytest.raises(OSError):
        memory.load(tmp_path, now.isoformat())
