"""User 2026-09-07: offline CI remains required; live acceptance waits a day."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("script", ["preview_morning_report.py", "deepseek_live_canary.py",
                                    "validate_llm_config.py"])
def test_paid_cli_refuses_before_network_or_state(script, tmp_path):
    # Fake credentials must not make an explicitly disabled entry point run.
    env = dict(os.environ, DRY_RUN="1", DEEPSEEK_API_KEY="not-a-real-key",
               OPENAI_API_KEY="not-a-real-key", STATE_ROOT=str(tmp_path))
    result = subprocess.run([sys.executable, str(ROOT / "tools" / script)],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "disabled by user policy" in result.stderr
    assert not list(tmp_path.iterdir())


def test_paid_jobs_disabled_and_ordinary_ci_still_required():
    policy = json.loads((ROOT / "_delivery_policy.json").read_text(encoding="utf-8"))
    assert policy["morning_dry_run"] is False
    required = next(w for w in policy["workflows"] if w["path"].endswith("/ci.yml"))
    assert required["jobs"] == ["test"]
    assert {"Syntax check", "Lint", "Type check (boundary modules)", "Run unit tests"} <= set(
        required["steps"]["test"]["required"])
    for name, job in [("ci.yml", "dry-run-preview"), ("deepseek-canary.yml", "contract"),
                      ("validate-llm-config.yml", "canary"), ("validate-llm-config.yml", "probe")]:
        workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
        assert workflow["jobs"][job]["if"] == "${{ false }}"
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    assert "if" not in ci["jobs"]["test"]
