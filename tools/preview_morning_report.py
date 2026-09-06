"""Non-sending, isolated-state canary; full pipeline even on Sundays.

The production entry point and schedule are unchanged. Real current time,
configured providers and the production phase list are preserved. Call in a
fresh interpreter with DRY_RUN=1. Full canaries use assert_run_quality against
preview_manifest with the same SHA/run-id/nonce. Scheduled Sunday previews do
not prove the weekday specialized analysis and cannot pass that strict gate.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import smtplib
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def isolated_state(source: Path, parent: Path | None = None) -> Path:
    """Never reuse an old run's manifest or mutate the source checkout."""
    if not source.is_dir() or source.is_symlink() or any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("preview state source must be an existing non-symlink tree")
    target = Path(tempfile.mkdtemp(prefix="morning-preview-", dir=parent)) / "state"
    shutil.copytree(source, target, ignore=lambda path, names:
                    ["run_manifest.json"] if Path(path) == source else [])
    return target


def forbidden(*_args, **_kwargs):
    raise RuntimeError("preview must never send email or publish state")


def install_guards(mr, state: Path) -> None:
    """Fail closed if production ever falls through its DRY_RUN boundary."""
    smtplib.SMTP = smtplib.SMTP_SSL = forbidden
    for name in ("send_email", "_git_commit_and_push_state", "push_committed_state"):
        getattr(mr, name)  # Refuse invented interfaces instead of silently adding them.
        setattr(mr, name, forbidden)
    writer = mr._atomic_write_bytes
    def isolated_write(path, data):
        if not Path(path).resolve().is_relative_to(state.resolve()):
            raise RuntimeError("preview attempted a state write outside its isolated tree")
        return writer(path, data)
    mr._atomic_write_bytes = isolated_write


def full_pipeline(mr) -> int:
    """Use actual time and phases, bypass only the scheduled Sunday dispatch."""
    mr._RUN_DEADLINE = mr.time.monotonic() + mr.RUN_BUDGET_SECONDS
    mr._DEGRADED_STEPS.clear()
    now = mr.dt.datetime.now(mr.TPE)
    mr._set_run_stamp(now)
    mr._gha_output("run_outcome", "running")
    mr._gha_output("state_dirty", "false")
    ctx = mr._app.AppContext(mr._RECORDER)
    ctx.now_tpe = now
    ctx.mode = mr.determine_mode(now)
    ctx.report_date = now.strftime("%Y-%m-%d (%a)")
    ctx.target_session_date = mr._infer_target_session_date(now.strftime("%Y-%m-%d"))
    ctx.target_session_day = mr.dt.date.fromisoformat(ctx.target_session_date)
    ctx.recorder.data["marks"].clear()
    for phase in mr._PIPELINE:
        if phase is mr._phase_deliver:
            raise RuntimeError("preview render did not stop before delivery")
        code = phase(ctx)
        if code is not None:
            return mr._final_exit_code(code)
    raise RuntimeError("preview pipeline produced no terminal outcome")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("scheduled", "full"), default="scheduled")
    args = parser.parse_args(argv)
    if os.environ.get("DRY_RUN") != "1":
        raise RuntimeError("preview requires explicit DRY_RUN=1")
    if "morning_report" in sys.modules:
        raise RuntimeError("preview requires a fresh interpreter before state initialization")
    state = isolated_state(ROOT / "state")
    os.environ["STATE_ROOT"] = str(state)
    sys.path.insert(0, str(ROOT))
    import morning_report as mr
    install_guards(mr, state)
    mr._gha_output("preview_manifest", str(state / "run_manifest.json"))
    print(f"[preview] kind={args.kind}; isolated state={state}", flush=True)
    code = full_pipeline(mr) if args.kind == "full" else mr.main()
    return mr._final_exit_code(code)


if __name__ == "__main__":
    raise SystemExit(main())
