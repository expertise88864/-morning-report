"""Offline contracts for the isolated Windows review launcher."""
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_windows_sandbox_is_explicit_for_initial_and_resume():
    source = (ROOT / "tools/codex_review.ps1").read_text(encoding="utf-8")
    flags = source.split("function Build-Flags", 1)[1].split("function Get-SessionId", 1)[0]
    assert "'-c', 'windows.sandbox=unelevated'" in flags
    assert flags.index("windows.sandbox=unelevated") < flags.index("if ($Kind")
    for required in ("--ignore-user-config", "sandbox_mode=read-only",
                     "'--sandbox', 'read-only'", "--strict-config",
                     "web_search=disabled", "features.apps=false"):
        assert required in flags


def test_incomplete_review_resumes_original_scope_before_clearing_result():
    source = (ROOT / "tools/codex_review.ps1").read_text(encoding="utf-8")
    recovery = source.index("^REVIEW_NOT_COMPLETED")
    clear = source.index("[System.IO.File]::WriteAllText($LastMsg, '')")
    assert recovery < clear
    assert "Resume the original requested review in this same session" in source
    assert "Do not approve only the" in source
    assert "Inspect only the corrections made for CONFIRMED" in source


def test_resume_context_is_checked_before_dispatch_without_changing_session():
    source = (ROOT / "tools/codex_review.ps1").read_text(encoding="utf-8")
    resume = source.split("# ================= resume", 1)[1].split("# =================", 1)[0]
    assert "Get-Content -LiteralPath $TaskContextFile -Raw -Encoding UTF8" in resume
    assert "resume task-context must not contain a diff" in resume
    assert resume.index("resume task-context must not contain a diff") < resume.index("$args2 =")
    assert "All original read-only restrictions and scope limits still apply" in resume
    assert "elseif ($Sid -cne $RecordedSid)" in resume


@pytest.mark.parametrize("text", ["  diff --git a/a b/a", "--- a/file\n+++ b/file",
                                  "\t@@ -1 +1 @@", "  index ab12..cd34", "  +++ /dev/null"])
def test_all_context_gates_reject_diff_formats(text):
    source = (ROOT / "tools/codex_review.ps1").read_text(encoding="utf-8")
    for variable in ("$ResumeContext", "$ctx", "$env:CODEX_REVIEW_VERIFICATION"):
        pattern = source.split(variable + " -match '", 1)[1].split("'", 1)[0]
        assert re.search(pattern, text)
        assert not re.search(pattern, "Task: repair JSON output.\nTests: 69 passed.")


@pytest.mark.parametrize("separator", ["---", "---   ", "\t---\t", "+++", "+++  "])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_context_separator_lines_are_not_file_headers(separator, newline):
    source = (ROOT / "tools/codex_review.ps1").read_text(encoding="utf-8")
    for variable in ("$ResumeContext", "$ctx", "$env:CODEX_REVIEW_VERIFICATION"):
        pattern = source.split(variable + " -match '", 1)[1].split("'", 1)[0]
        assert not re.search(pattern, newline.join(["Task summary", separator, "Tests passed"]))
