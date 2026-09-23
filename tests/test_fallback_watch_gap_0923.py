"""Offline regression for the due-day gap in legacy analysis fallback."""
import copy

import analysis_origin as origin
import fallback_watch_gap as gap
import finding_domains
import morning_report as mr
import run_quality


def _watch(watch_id, *, created="2026-09-21", deadline="2026-09-22",
           last_reviewed="", status="open"):
    return {"watch_id": watch_id, "created": created, "deadline": deadline,
            "last_reviewed": last_reviewed, "status": status,
            "trigger": "私人原文不得寫進告警"}


def test_due_day_fallback_is_reported_without_changing_watch_ledger():
    state = {"watch": [_watch("w49"), _watch("w50", deadline="2026-09-23"),
                       _watch("w51", last_reviewed="2026-09-22"),
                       _watch("w52", created="2026-09-22"),
                       _watch("w53", status="closed")]}
    before = copy.deepcopy(state)
    record = gap.due_cases(state, "2026-09-22")
    assert record == {"count": 1, "unselected_count": 0, "cases": [
        {"watch_id": "w49", "created": "2026-09-21", "deadline": "2026-09-22"}],
        "unselected_cases": []}
    assert state == before
    assert "私人原文" not in str(record)
    manifest = {"llm": {"analysis_origin": origin.LEGACY_AFTER_LUNA_FAILURE,
                        "watch_due_at_start": record}}
    finding = next(f for f in run_quality.assess(manifest)
                   if f["code"] == "watch_due_unreviewed")
    assert finding["severity"] == "degraded"
    assert "w49(期限 2026-09-22)" in finding["detail"]
    assert finding["domain"] == finding_domains.DOMAIN_CONTENT
    assert "私人原文" not in finding["detail"]
    assert "未完成可驗證的逐項回顧" in gap.reader_notice(record, origin.LEGACY_PRIMARY)
    assert "w49" not in gap.reader_notice(record, origin.LEGACY_PRIMARY)
    assert gap.reader_notice(record, origin.LUNA_SPECIALIZED) == ""
    assert gap.findings(record, origin.LUNA_SPECIALIZED) == []
    assert gap.findings(record, origin.LEGACY_PRIMARY, digest=True) == []


def test_specialized_path_reports_due_watches_beyond_review_capacity():
    watches = [_watch(f"w{i}") for i in range(1, 11)]
    record = gap.due_cases({"watch": watches}, "2026-09-22")
    assert record["count"] == 10 and record["unselected_count"] == 2
    finding = gap.findings(record, origin.LUNA_SPECIALIZED)[0]
    assert finding[0] == "watch_due_unreviewed"
    assert "w9(期限 2026-09-22)" in finding[2]
    assert "w1(期限 2026-09-22)" not in finding[2]
    assert "2 項" in gap.reader_notice(record, origin.LUNA_SPECIALIZED)


def test_weekend_report_date_does_not_prematurely_expire_monday_watch():
    state = {"watch": [_watch("w49", created="2026-09-25", deadline="2026-09-28")]}
    record = gap.due_cases(state, "2026-09-26", "2026-09-28")
    assert record["count"] == 0
    assert gap.findings(record, origin.LEGACY_AFTER_LUNA_FAILURE) == []
    state["watch"].append(_watch("w50", created="2026-09-25", deadline="2026-09-26"))
    record = gap.due_cases(state, "2026-09-26", "2026-09-28")
    assert record["count"] == 1 and record["unselected_count"] == 1
    assert "w50(期限 2026-09-26)" in gap.findings(record, origin.LUNA_SPECIALIZED)[0][2]


def test_due_scan_bad_state_cannot_block_the_report_or_hide_diagnostic():
    record = gap.due_cases({"watch_seq": "bad", "watch": [_watch("w1")]}, "2026-09-22")
    assert record["error"] == "invalid_ledger"
    manifest = {"llm": {"analysis_origin": origin.LEGACY_PRIMARY,
                        "watch_due_at_start": record}}
    assert any(f["code"] == "watch_due_scan_failed" and f["severity"] == "defect"
               for f in run_quality.assess(manifest))
    assert gap.due_cases({"watch": [_watch("w1", deadline="2026-09-20")]},
                         "2026-09-22")["count"] == 1


def test_reviewed_on_deadline_is_not_falsely_marked_overdue_next_day():
    for reviewed in ("2026-09-22", "2026-09-23"):
        state = {"watch": [_watch("w49", last_reviewed=reviewed)]}
        assert gap.due_cases(state, "2026-09-23")["count"] == 0
    before_deadline = {"watch": [_watch("w49", last_reviewed="2026-09-21")]}
    assert gap.due_cases(before_deadline, "2026-09-22")["count"] == 1
    assert gap.due_cases(before_deadline, "2026-09-23")["count"] == 1


def test_actual_email_renderer_labels_fallback_gap_without_dropping_analysis(monkeypatch):
    from test_golden_faults import _golden_quotes

    def no_network(*_args, **_kwargs):
        raise AssertionError("offline rendering must not fetch")

    monkeypatch.setattr(mr, "_http_get", no_network)
    monkeypatch.setattr(mr.requests, "get", no_network)
    monkeypatch.setattr(mr, "_DEGRADED_STEPS", [])
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {"llm": {
        "analysis_origin": origin.LEGACY_AFTER_LUNA_FAILURE,
        "watch_due_at_start": gap.due_cases({"watch": [_watch("w49")]}, "2026-09-22")}})
    analysis = "## 一、今日結論\n原本分析正文保留。"
    html = mr.render_html(_golden_quotes(), {"error": "fixture"}, {"error": "fixture"},
                          analysis, "2026-09-22", "每日報")
    assert "跨日觀察提醒" in html and "原本分析正文保留" in html
    assert "私人原文" not in html and "w49" not in html
    minimal = mr._render_minimal_html(_golden_quotes(), {"error": "fixture"},
                                      {"error": "fixture"}, analysis,
                                      "2026-09-22", "每日報")
    assert "跨日觀察提醒" in minimal and "原本分析正文保留" in minimal
    assert "私人原文" not in minimal and "w49" not in minimal
