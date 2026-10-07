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


def test_readme_local_testing_does_not_recommend_paid_preview():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    local_testing = readme.split("## 七、本地測試", 1)[1].split("\n---", 1)[0]
    commands = local_testing.split("```", 2)[1]
    assert "DRY_RUN=" not in commands
    assert "DEEPSEEK_API_KEY=" not in commands
    assert "python morning_report.py" not in commands
    assert "不得用於測試" in local_testing


def test_claude_instructions_do_not_require_paid_pipeline_preview():
    instructions = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "另做不寄信 dry-run" not in instructions
    assert "只能靠 CI dry-run artifact" not in instructions
    assert "用封存案例與離線 fixture 驗證" in instructions
    assert "實際外部內容與手機閱讀只待正常排程信驗收" in instructions


def test_delivery_guide_keeps_full_local_gate_before_every_push():
    guide = (ROOT / "REMOTE_CI_DELIVERY.md").read_text(encoding="utf-8")
    assert "replaced mandatory full local CI" not in guide
    assert "before **every** branch push" in guide
    assert "complete tests" in guide
    assert "Neither the hook nor hosted CI replaces the local prerequisite" in guide


def test_agent_rules_do_not_waive_full_local_ci_or_restore_paid_dry_run():
    for name in ("AGENTS.md", "CLAUDE.md"):
        rules = (ROOT / name).read_text(encoding="utf-8")
        assert "候選 push 不要求先有完整本機 CI" not in rules
        assert "每次候選 push 前完整本機 CI」及" not in rules
        assert "候選與正式 push 前均須重驗實際待推版本" in rules
        assert "任何分支 push 前" in rules
        assert "另做不寄信 dry-run" not in rules


def test_effort_cap_warning_does_not_suggest_paid_manual_probe():
    import llm_config

    issues = llm_config.validate_llm_config(
        provider="deepseek", extractor_provider="deepseek", shadow_provider="",
        has_key=lambda _: True, efforts={"extractor": "max"}, models={})
    warning = next(str(issue) for issue in issues if "超過實測過的上限" in issue)
    assert "維持現行上限" in warning
    assert "不可為驗證手動重跑或影子付費測試" in warning
    assert "要提高請先用手動執行" not in warning


def test_missing_mail_credentials_do_not_recommend_paid_preview(monkeypatch):
    import morning_report as report

    monkeypatch.setattr(report, "GMAIL_USER", "")
    monkeypatch.setattr(report, "GMAIL_APP_PASSWORD", "")
    with pytest.raises(RuntimeError) as error:
        report.send_email("<p>離線案例</p>", "測試")
    assert "離線 fixture／pytest" in str(error.value)
    assert "不得手動產報" in str(error.value)
    assert "DRY_RUN=1" not in str(error.value)


def test_radar_instructions_do_not_offer_paid_preview():
    header = (ROOT / "gooaye_radar.py").read_text(encoding="utf-8").split(
        '"""', 2)[1]
    assert "不得作本機／CI 測試" in header
    assert "設 DRY_RUN=1 只輸出預覽檔" not in header
