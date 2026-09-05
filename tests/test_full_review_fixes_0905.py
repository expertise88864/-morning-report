"""CR-01..04: use synthetic state and mocked external boundaries only."""
import datetime as dt
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import alpha_factors as af
import factor_ic as fi
import model_history_store as mh
import morning_report as mr
import overfit_check as oc
from app_context import AppContext


def _row(date, i=0):
    return {"session_date": date, "taiex_close": 20000, "model_version": "test",
            "stocks": {f"synthetic-{j}": {
                "close": 100 + j * (i + 1), "open": 100 + j * i,
                "volume": 1000 + j, **{f: j for f in fi.FACTORS}}
                for j in range(1, 17)}}


def _partition(directory, name, rows):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(gzip.compress(json.dumps(rows).encode()))


@pytest.mark.parametrize("rewritten", [None, set(), {"2026-09.json.gz"}])
def test_missing_month_survives_manifest_rewrite(tmp_path, rewritten, capsys):
    _partition(tmp_path, "2026-08.json.gz", [_row("2026-08-03")])
    _partition(tmp_path, "2026-09.json.gz", [_row("2026-09-01")])
    original = mh.write_partition_manifest(tmp_path)
    (tmp_path / "2026-08.json.gz").unlink()  # Synthetic fixture only.
    result = mh.write_partition_manifest(tmp_path, rewritten=rewritten)
    assert result["partitions"]["2026-08.json.gz"] == original["partitions"]["2026-08.json.gz"]
    assert "2026-08.json.gz" in capsys.readouterr().err
    with pytest.raises(mh.HistoryIntegrityError, match="missing_partition"):
        mh.verify_history_integrity(tmp_path, strict=True)


def test_daily_save_cannot_erase_missing_month_outside_merge_view(tmp_path, monkeypatch):
    _partition(tmp_path, "2026-08.json.gz", [_row("2026-08-03")])
    mh.write_partition_manifest(tmp_path)
    (tmp_path / "2026-08.json.gz").unlink()
    monkeypatch.setattr(mr, "MODEL_HISTORY_DIR", tmp_path)
    monkeypatch.setattr(mr, "load_model_history", lambda: [])
    mr.save_model_history_records([_row("2026-09-01")])
    assert (tmp_path / "2026-09.json.gz").exists()
    with pytest.raises(mh.HistoryIntegrityError, match="missing_partition"):
        mh.verify_history_integrity(tmp_path, strict=True)


def _llm_context(monkeypatch):
    import llm_config
    monkeypatch.setattr(llm_config, "config_snapshot", lambda **kw: ({}, []))
    monkeypatch.setattr(mr, "call_llm_analysis", lambda *a: "synthetic analysis")
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    return SimpleNamespace(quotes={}, fair={}, predictions={}, news=[], tw0050=[],
                           calibration="", now_tpe=dt.datetime(2026, 9, 5),
                           mark_phase=lambda *a: None)


def test_attribution_failure_keeps_valid_python_score(monkeypatch):
    ctx = _llm_context(monkeypatch)
    dim = mr._STANCE_DIM_ZH[0][0]
    ctx.quotes["HISTORY"] = [{"date": "2026-09-04", "stance_score_py": 3,
                              "stance_components_py": {dim: float("nan")}}]
    expected = {"total": 4, "label": "偏多", "components": {dim: 1},
                "missing": [], "stale_us": False}
    monkeypatch.setattr(mr, "_compute_stance_score", lambda q: expected)
    mr._phase_llm_analysis(ctx)
    assert ctx.quotes["STANCE_PY"] == expected
    assert ctx.quotes["STANCE_ATTRIB"] == {}
    assert "stance:attribution_failed" in mr._DEGRADED_STEPS
    assert ctx.analysis == "synthetic analysis"


def test_score_failure_is_visible_and_does_not_block_analysis(monkeypatch):
    ctx = _llm_context(monkeypatch)

    def fail(q):
        raise ValueError("synthetic failure")

    monkeypatch.setattr(mr, "_compute_stance_score", fail)
    mr._phase_llm_analysis(ctx)
    assert ctx.quotes["STANCE_PY"] == {}
    assert "stance:score_failed" in mr._DEGRADED_STEPS
    assert ctx.analysis == "synthetic analysis"


def _render_context(py):
    ctx = AppContext(SimpleNamespace(mark_phase=lambda *a: None))
    for field in ctx.__slots__:
        if getattr(ctx, field) is None:
            setattr(ctx, field, {})
    ctx.now_tpe = dt.datetime(2026, 9, 5, tzinfo=mr.TPE)
    ctx.target_session_date = "2026-09-07"
    ctx.report_date, ctx.mode, ctx.analysis = "2026-09-05", "daily", "synthetic"
    ctx.quotes = {"QQQ": {}, "TSM": {}, "SPY": {}, "STANCE_PY": py}
    ctx.news, ctx.tw0050, ctx.trading_sessions = [], [], []
    return ctx


@pytest.mark.parametrize("py,expected", [({}, None), ({"total": 4, "label": "偏多"}, 4)])
def test_persisted_authority_never_falls_back_to_llm(monkeypatch, py, expected):
    ctx = _render_context(py)
    monkeypatch.setenv("DRY_RUN", "0")
    monkeypatch.setattr(mr, "render_html", lambda *a: "<html>test</html>")
    monkeypatch.setattr(mr, "_latest_completed_session", lambda *a: None)
    monkeypatch.setattr(mr, "_structured_stance", lambda: {"score": -6, "label": "偏空"})
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    mr._phase_render(ctx)
    assert ctx.pending_state_entry is not None
    assert ctx.pending_state_entry["stance_score"] == expected
    assert ctx.pending_state_entry["stance_label"] == ("偏多" if expected else None)
    assert ctx.pending_state_entry["stance_score_llm"] == -6  # Diagnostic only.
    if expected is None:
        recap = mr._format_narrative_delta([ctx.pending_state_entry], today="2026-09-06")
        assert "偏空" not in recap and "-6" not in recap


@pytest.fixture
def research_history(tmp_path, monkeypatch):
    rows = [_row((dt.date(2026, 7, 1) + dt.timedelta(days=i)).isoformat(), i)
            for i in range(40)]
    legacy, parts = tmp_path / "legacy.json", tmp_path / "parts"
    legacy.write_text(json.dumps(rows[:31]), encoding="utf-8")
    _partition(parts, "2026-08.json.gz", rows[31:])
    mh.write_partition_manifest(parts)
    for module in (af, fi, oc):
        # Redirect paths but execute the real strict loader, not a canned result.
        monkeypatch.setattr(module, "load_model_history",
                            lambda **kw: mh.load_model_history(legacy, parts, **kw))
    return rows, parts


def test_all_research_entrypoints_include_partition_samples(research_history, capsys):
    rows, _ = research_history
    assert list(af._build_panels()["close"].index) == [r["session_date"] for r in rows]
    assert fi.main() == 0
    output = capsys.readouterr().out
    assert "40 個交易日" in output and rows[-1]["session_date"] in output
    matrix, factors = oc._build_factor_return_matrix()
    assert matrix.shape == (40 - oc.HORIZON, len(oc.FACTORS))
    assert factors == oc.FACTORS


@pytest.mark.parametrize("entry", [af._build_panels, fi.main, oc._build_factor_return_matrix])
def test_every_research_entry_rejects_corrupt_partitions(research_history, entry):
    _, parts = research_history
    (parts / "2026-08.json.gz").write_bytes(b"synthetic corruption")
    with pytest.raises(mh.HistoryIntegrityError):
        entry()


def test_missing_expected_artifact_fails_publish_and_has_own_alert():
    root = Path(mr.__file__).resolve().parent
    wf = yaml.safe_load((root / ".github/workflows/morning-report-b.yml").read_text(encoding="utf-8"))
    jobs = wf["jobs"]
    download = next(s for s in jobs["publish-state"]["steps"] if s.get("id") == "statedl")
    assert not download.get("continue-on-error", False)
    assert "state_dirty == 'true'" in download["if"]
    assert "contract_outcome == 'success'" in download["if"]
    alert = jobs["alert-on-state-failure"]
    assert set(alert["needs"]) == {"send-report", "publish-state"}
    assert "always()" in alert["if"]
    assert "needs.publish-state.result == 'failure'" in alert["if"]
    assert alert["permissions"] == {"contents": "read"}
    assert not any(s.get("continue-on-error", False) for s in alert["steps"])
    mail = next(s for s in alert["steps"] if "state_publish_alert.py" in s.get("run", ""))
    assert mail["env"]["DELIVERED"] == "${{ needs.send-report.outputs.delivered }}"


@pytest.mark.parametrize("minimal", [False, True])
def test_renderers_do_not_promote_llm_when_python_is_missing(minimal, monkeypatch):
    from test_render_smoke import _fixture
    quotes, fair, predictions, _ = _fixture()
    quotes["STANCE_PY"] = {}
    analysis = ("## 昨夜三大重點\n合成新聞仍保留。\n\n"
                "## 十二、我的明確立場\n立場：偏空 (淨分 -6)\n錯誤方向建議。\n\n"
                "## 十三、一句話總結\n偏空,錯誤方向建議。")
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    render = mr._render_minimal_html if minimal else mr.render_html
    html = render(quotes, fair, predictions, analysis, "2026-09-05", "daily")
    assert "立場未知" in html
    assert "錯誤方向建議" not in html
    assert "合成新聞仍保留" in html


@pytest.mark.parametrize("label,severity", [("stance:score_failed", "defect"),
                                          ("stance:attribution_failed", "degraded")])
def test_stance_degradation_reaches_quality_alert(label, severity):
    import finding_domains as fd
    import run_quality as rq
    from test_run_quality import _ok_manifest
    findings = rq.assess(_ok_manifest(degraded_steps=[label]))
    match = next(f for f in findings if f["code"] == label)
    assert match["severity"] == severity
    assert fd.finding_domain(label) == fd.DOMAIN_CONTENT
    assert not any(f["code"] == "unknown_degradation" for f in findings)


@pytest.mark.parametrize("delivered", [True, False])
def test_state_alert_uses_delivery_evidence_not_job_success(delivered):
    from tools.state_publish_alert import build_message
    msg = build_message(delivered, "failure", "https://example.test/run")
    assert ("晨報已寄出" in msg["Subject"]) == delivered
    assert ("寄送狀態未確認" in msg["Subject"]) != delivered
    assert "不要因這封告警直接重寄晨報" in msg.get_content()
    assert "https://example.test/run" in msg.get_content()


def test_state_alert_missing_credentials_is_a_failure(monkeypatch):
    from tools import state_publish_alert as alert
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    assert alert.main() == 1


def test_state_alert_smtp_failure_propagates(monkeypatch):
    from tools import state_publish_alert as alert
    monkeypatch.setenv("GMAIL_USER", "synthetic@example.test")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "synthetic")

    def fail(*a, **kw):
        raise OSError("synthetic SMTP failure")

    monkeypatch.setattr(alert.smtplib, "SMTP_SSL", fail)
    with pytest.raises(OSError, match="synthetic SMTP failure"):
        alert.main()


def test_state_alert_sends_only_notification_without_logging_address(monkeypatch, capsys):
    from tools import state_publish_alert as alert
    monkeypatch.setenv("GMAIL_USER", "synthetic@example.test")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "synthetic")
    monkeypatch.setenv("DELIVERED", "true")
    sent = []

    class SMTP:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr(alert.smtplib, "SMTP_SSL", lambda *a, **kw: SMTP())
    assert alert.main() == 0
    assert len(sent) == 1 and "state 發佈未完成" in sent[0]["Subject"]
    assert sent[0]["To"] == "synthetic@example.test"
    assert "synthetic@example.test" not in capsys.readouterr().out


def test_dry_run_writes_unknown_stance_preview_without_sending(monkeypatch, tmp_path):
    import builtins
    from test_render_smoke import _fixture
    quotes, fair, predictions, _ = _fixture()
    quotes["STANCE_PY"] = {}
    ctx = _render_context({})
    ctx.quotes, ctx.fair, ctx.predictions = quotes, fair, predictions
    ctx.analysis = ("## 昨夜三大重點\n合成資料預覽。\n\n"
                    "## 我的明確立場\n偏空,錯誤方向建議。\n\n"
                    "## 一句話總結\n偏空,錯誤方向建議。")
    preview = tmp_path / "unknown-stance-preview.html"
    original_open = builtins.open

    def preview_open(path, *a, **kw):
        assert path == "/tmp/morning_report_preview.html"
        return original_open(preview, *a, **kw)

    saved = []
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setattr(mr, "open", preview_open, raising=False)
    monkeypatch.setattr(mr, "_latest_completed_session", lambda *a: None)
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    monkeypatch.setattr(mr, "_write_run_manifest", lambda *a, **kw: None)
    monkeypatch.setattr(mr, "persist_delivered_report_state", lambda *a, **kw: saved.append((a, kw)))
    assert mr._phase_render(ctx) == 0
    html = preview.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html and "立場未知" in html
    assert "合成資料預覽" in html and "錯誤方向建議" not in html
    assert "渲染-主體(改寄極簡版)" not in mr._DEGRADED_STEPS
    assert saved[0][0][0]["stance_score"] is None
    assert saved[0][1]["mark_podcasts"] is False


@pytest.mark.parametrize("entry", [af._build_panels, fi.main, oc._build_factor_return_matrix])
def test_missing_history_preserves_insufficient_sample_behavior(entry, tmp_path, monkeypatch):
    for module in (af, fi, oc):
        monkeypatch.setattr(module, "load_model_history", lambda **kw: mh.load_model_history(
            tmp_path / "missing.json", tmp_path / "parts", **kw))
    result = entry()
    if entry is af._build_panels:
        assert result == {}
    elif entry is fi.main:
        assert result == 1
    else:
        assert result[0].shape == (0, 0) and result[1] == []
