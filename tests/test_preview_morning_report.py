"""Canary isolation and complete phase execution without sending or time travel."""
import datetime as dt
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import preview_morning_report as preview


def test_isolation_keeps_history_but_never_inherits_old_manifest(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "history.json").write_text('[{"date":"2026-09-01"}]')
    (source / "run_manifest.json").write_text('{"delivery":"old"}')
    target = preview.isolated_state(source, tmp_path)
    assert target != source and (target / "history.json").read_bytes() == (source / "history.json").read_bytes()
    assert not (target / "run_manifest.json").exists()
    assert (source / "run_manifest.json").exists()
    (target / "history.json").write_text("[]")
    assert (source / "history.json").read_text() != "[]"


def test_missing_state_is_not_a_silent_cold_start(tmp_path):
    with pytest.raises(ValueError):
        preview.isolated_state(tmp_path / "absent", tmp_path)


def test_cli_refuses_to_run_without_dry_run(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    result = subprocess.run([sys.executable, str(Path(preview.__file__)), "--kind", "full"],
                            capture_output=True, text=True)
    assert result.returncode != 0 and "DRY_RUN=1" in result.stderr


def test_cli_rejects_late_state_initialization(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    with pytest.raises(RuntimeError, match="fresh interpreter"):
        preview.main(["--kind", "full"])


def fake_report(phases):
    import app_context
    import run_manifest
    now = dt.datetime(2026, 9, 6, 7, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    class Clock:
        @staticmethod
        def now(_tz):
            return now
    return SimpleNamespace(time=SimpleNamespace(monotonic=lambda: 1), RUN_BUDGET_SECONDS=300,
        _DEGRADED_STEPS=[], dt=SimpleNamespace(datetime=Clock, date=dt.date), TPE=now.tzinfo,
        _set_run_stamp=lambda n: None, _gha_output=lambda *a: None,
        _app=app_context, _RECORDER=run_manifest.ManifestRecorder(),
        determine_mode=lambda n: "週日測試", _infer_target_session_date=lambda d: "2026-09-07",
        _PIPELINE=phases, _phase_deliver=preview.forbidden, _final_exit_code=int)


def test_full_preview_runs_actual_phases_on_sunday_without_changing_date():
    seen = []
    def market(ctx):
        seen.append(ctx.now_tpe)
    def render(ctx):
        seen.append(ctx.target_session_date)
        return 0
    report = fake_report([market, render, preview.forbidden])
    assert preview.full_pipeline(report) == 0
    assert seen[0].isoformat() == "2026-09-06T07:00:00+08:00"
    assert seen[1] == "2026-09-07"


def test_full_preview_fails_if_render_falls_through_to_delivery():
    with pytest.raises(RuntimeError, match="before delivery"):
        preview.full_pipeline(fake_report([lambda ctx: None, preview.forbidden]))


def test_full_preview_preserves_failure_exit_code():
    assert preview.full_pipeline(fake_report([lambda ctx: 7])) == 7


def test_guards_block_smtp_publishing_and_outside_state_writes(tmp_path, monkeypatch):
    import morning_report as report
    # Record the real globals for restoration: the installer mutates them.
    monkeypatch.setattr(preview.smtplib, "SMTP", preview.smtplib.SMTP)
    monkeypatch.setattr(preview.smtplib, "SMTP_SSL", preview.smtplib.SMTP_SSL)
    writes = []
    monkeypatch.setattr(report, "_atomic_write_bytes", lambda *args: writes.append(args))
    for name in ("send_email", "_git_commit_and_push_state", "push_committed_state"):
        monkeypatch.setattr(report, name, getattr(report, name))
    state = tmp_path / "isolated"
    state.mkdir()
    preview.install_guards(report, state)
    for action in (preview.smtplib.SMTP, preview.smtplib.SMTP_SSL,
                   report.send_email, report._git_commit_and_push_state, report.push_committed_state):
        assert action is preview.forbidden  # Never execute a live capability on regression.
        with pytest.raises(RuntimeError, match="never send"):
            action([], "preview") if action is report._git_commit_and_push_state else action()
    with pytest.raises(RuntimeError, match="outside"):
        report._atomic_write_bytes(state / ".." / "official.json", b"bad")
    assert not writes
    report._atomic_write_bytes(state / "history.json", b"safe")
    assert writes == [(state / "history.json", b"safe")]
    report.persist_delivered_report_state(None, [], mark_podcasts=False, push=False)


def test_render_dry_run_explicitly_disables_publication():
    import ast
    import inspect
    import morning_report as report
    tree = ast.parse(inspect.getsource(report._phase_render))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "persist_delivered_report_state"]
    assert len(calls) == 1
    assert any(k.arg == "push" and isinstance(k.value, ast.Constant) and k.value.value is False
               for k in calls[0].keywords)


def test_guard_installer_fails_on_a_missing_production_interface(tmp_path, monkeypatch):
    monkeypatch.setattr(preview.smtplib, "SMTP", preview.smtplib.SMTP)
    monkeypatch.setattr(preview.smtplib, "SMTP_SSL", preview.smtplib.SMTP_SSL)
    with pytest.raises(AttributeError):
        preview.install_guards(SimpleNamespace(_atomic_write_bytes=lambda *a: None), tmp_path)


def test_manual_ci_uses_full_isolation_and_exact_fresh_manifest():
    import yaml
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["dry-run-preview"]
    assert job["if"] == "github.event_name == 'workflow_dispatch'"
    run = next(s for s in job["steps"] if s.get("id") == "run")
    assert run["run"] == "python tools/preview_morning_report.py --kind full"
    assert run["env"]["DRY_RUN"] == "1"
    quality = next(s for s in job["steps"] if "assert_run_quality.py" in s.get("run", ""))
    assert quality["if"] == "always()"
    assert '--mode strict --manifest "$PREVIEW_MANIFEST"' in quality["run"]
    assert quality["env"]["PREVIEW_MANIFEST"] == "${{ steps.run.outputs.preview_manifest }}"
    assert quality["env"]["RUN_NONCE"] == run["env"]["RUN_NONCE"]
    assert quality["env"]["GITHUB_SHA"] == "${{ github.sha }}"
    assert quality["env"]["GITHUB_RUN_ID"] == "${{ github.run_id }}"
    assert not any(s.get("continue-on-error") for s in job["steps"])
