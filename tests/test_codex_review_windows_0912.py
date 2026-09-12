"""Offline contracts for the isolated Windows review launcher."""
from pathlib import Path


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
