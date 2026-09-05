# -*- coding: utf-8 -*-
"""Claude Opus 5 diff gate: exact model, read-only, and fail-closed wiring."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "claude_diff_review.py"
SPEC = importlib.util.spec_from_file_location("claude_diff_review", SCRIPT)
assert SPEC and SPEC.loader
GATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GATE
SPEC.loader.exec_module(GATE)


def _result(message: str, *, model: str = "claude-opus-5", error: bool = False) -> str:
    return json.dumps(
        {
            "type": "result",
            "is_error": error,
            "result": message,
            "modelUsage": {model: {"inputTokens": 1, "outputTokens": 1}},
        }
    )


def test_command_pins_exact_opus5_and_exposes_no_write_or_fallback_tools():
    command = GATE.build_command("claude")
    assert "review" not in command
    assert command[command.index("--model") + 1] == "claude-opus-5"
    assert command[command.index("--effort") + 1] == "high"
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert "--restricted" in command
    assert "--strict-mcp-config" in command
    assert "--setting-sources" not in command and "--settings" not in command
    assert "--fallback-model" not in command
    assert "Edit" not in command and "Write" not in command
    assert "Bash" not in command
    assert GATE.ALLOWED_TOOLS == ("Read(/**)", "Glob(/**)", "Grep(/**)")
    assert command.index("--output-format") < command.index("--allowedTools")
    assert command[-len(GATE.ALLOWED_TOOLS) :] == list(GATE.ALLOWED_TOOLS)


def test_result_requires_model_evidence_and_an_exact_terminal_verdict():
    message, verdict, _ = GATE.parse_review_output(
        _result("NO_ACTIONABLE_FINDINGS\n\nAPPROVE")
    )
    assert verdict == "APPROVE" and "NO_ACTIONABLE" in message

    with pytest.raises(GATE.ReviewError, match="cannot prove"):
        GATE.parse_review_output(_result("APPROVE", model="claude-opus-4-8"))
    with pytest.raises(GATE.ReviewError, match="exact APPROVE"):
        GATE.parse_review_output(_result("Looks good"))
    with pytest.raises(GATE.ReviewError, match="Not logged in"):
        GATE.parse_review_output(_result("Not logged in", error=True))


def test_plain_text_cli_auth_failure_is_reported_and_blocks(monkeypatch, tmp_path):
    monkeypatch.setattr(GATE, "STATE_DIR", tmp_path)
    monkeypatch.setattr(GATE, "find_claude", lambda: "claude")
    monkeypatch.setattr(GATE, "_snapshot_text", lambda *args: "review input\n")
    monkeypatch.setattr(
        GATE.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="Not logged in · Please run /login"
        ),
    )
    with pytest.raises(GATE.ReviewError, match="authentication"):
        GATE.run_review("worktree", "abc", [])


def test_overlapping_reviews_have_independent_snapshot_files(monkeypatch, tmp_path):
    monkeypatch.setattr(GATE, "STATE_DIR", tmp_path)
    monkeypatch.setattr(GATE, "find_claude", lambda: "claude")
    monkeypatch.setattr(GATE, "_snapshot_text", lambda scope, _: f"snapshot for {scope}\n")
    prompts = []
    def invoke(*args, **kwargs):
        prompts.append(kwargs["input"])
        if len(prompts) == 1:
            outer = next(tmp_path.glob("review-*.patch"))
            assert outer.read_text() == "snapshot for worktree\n"
            assert GATE.run_review("staged", "inner", []) == 0
            assert outer.exists() and outer.read_text() == "snapshot for worktree\n"
        else:
            snapshots = list(tmp_path.glob("review-*.patch"))
            assert len(snapshots) == 2
            inner = next(p for p in snapshots if p.read_text() == "snapshot for staged\n")
            assert inner.as_posix() in kwargs["input"]
        return SimpleNamespace(returncode=0, stdout=_result("APPROVE"), stderr="")
    monkeypatch.setattr(GATE.subprocess, "run", invoke)
    assert GATE.run_review("worktree", "outer", []) == 0
    assert prompts[0] != prompts[1]
    assert not list(tmp_path.glob("review-*.patch"))
    record = json.loads((tmp_path / "last_review.json").read_text())
    assert len(record["snapshot_sha256"]) == 64


def test_pending_marker_is_atomic_sanitized_and_preserves_reset_hint(
    monkeypatch, tmp_path
):
    pending = tmp_path / "pending_review.json"
    monkeypatch.setattr(GATE, "PENDING_PATH", pending)
    secret = "do-not-persist-this-output"
    GATE.record_pending_review(
        "worktree",
        "abc123",
        GATE.ReviewError(
            f"{secret}: You've hit your usage limit; resets 7:50 PM (Asia/Taipei)"
        ),
    )

    record = json.loads(pending.read_text(encoding="utf-8"))
    assert record["status"] == "PENDING"
    assert record["fingerprint"] == "abc123"
    assert record["failure_kind"] == "quota"
    assert record["retry_after"] == "7:50 PM (Asia/Taipei)"
    assert secret not in pending.read_text(encoding="utf-8")

    GATE.record_pending_review(
        "worktree",
        "def456",
        GATE.ReviewError(
            '"api_error_status":429,"result":"You have hit your session limit '
            'resets 7:50pm (Asia/Taipei)"'
        ),
    )
    actual_cli_record = json.loads(pending.read_text(encoding="utf-8"))
    assert actual_cli_record["failure_kind"] == "quota"
    assert actual_cli_record["retry_after"] == "7:50pm (Asia/Taipei)"


def test_successful_review_clears_pending_marker(monkeypatch, tmp_path):
    pending = tmp_path / "pending_review.json"
    monkeypatch.setattr(GATE, "STATE_DIR", tmp_path)
    monkeypatch.setattr(GATE, "PENDING_PATH", pending)
    GATE.record_pending_review("worktree", "abc", GATE.ReviewError("quota"))
    monkeypatch.setattr(GATE, "worktree_fingerprint", lambda: "abc")
    monkeypatch.setattr(GATE, "find_claude", lambda: "claude")
    monkeypatch.setattr(GATE, "_snapshot_text", lambda *args: "review input\n")
    monkeypatch.setattr(
        GATE.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=_result("NO_ACTIONABLE_FINDINGS\n\nAPPROVE"),
            stderr="",
        ),
    )

    assert GATE.main(["worktree"]) == 0
    assert not pending.exists()


def test_empty_staged_review_does_not_erase_pending_worktree(monkeypatch, tmp_path):
    pending = tmp_path / "pending_review.json"
    monkeypatch.setattr(GATE, "PENDING_PATH", pending)
    GATE.record_pending_review("worktree", "worktree-diff", GATE.ReviewError("quota"))
    original = pending.read_bytes()
    monkeypatch.setattr(GATE, "staged_fingerprint", lambda: None)

    assert GATE.main(["staged"]) == 0
    assert pending.read_bytes() == original


def test_pending_resolution_requires_matching_scope_and_fingerprint(monkeypatch, tmp_path):
    pending = tmp_path / "pending_review.json"
    monkeypatch.setattr(GATE, "PENDING_PATH", pending)
    GATE.record_pending_review("push", "abc", GATE.ReviewError("quota"))
    original = pending.read_bytes()
    GATE.clear_pending_review("worktree", "abc")
    GATE.clear_pending_review("push", "different-diff")
    assert pending.read_bytes() == original
    GATE.clear_pending_review("push", "abc")
    assert not pending.exists()


def test_diff_changed_during_review_records_current_fingerprint(monkeypatch, tmp_path):
    pending = tmp_path / "pending_review.json"
    monkeypatch.setattr(GATE, "STATE_DIR", tmp_path)
    monkeypatch.setattr(GATE, "PENDING_PATH", pending)
    fingerprints = iter(("old-diff", "new-diff"))
    monkeypatch.setattr(GATE, "worktree_fingerprint", lambda: next(fingerprints))
    monkeypatch.setattr(GATE, "run_review", lambda *args: 0)
    assert GATE.main(["worktree"]) == 4
    record = json.loads((tmp_path / "last_blocked.json").read_text(encoding="utf-8"))
    assert record["status"] == "BLOCKED" and not pending.exists()
    assert record["fingerprint"] == "new-diff"
    assert record["failure_kind"] == "diff_changed"


def test_quota_retry_changes_requested_then_corrected_diff_can_resolve(monkeypatch, tmp_path):
    pending = tmp_path / "pending_review.json"
    monkeypatch.setattr(GATE, "PENDING_PATH", pending)
    GATE.record_pending_review("worktree", "F", GATE.ReviewError("quota"))
    fingerprints = iter(("F", "F", "G", "G"))
    verdict_codes = iter((2, 0))
    monkeypatch.setattr(GATE, "worktree_fingerprint", lambda: next(fingerprints))
    monkeypatch.setattr(GATE, "run_review", lambda *args: next(verdict_codes))
    assert GATE.main(["worktree"]) == 2  # REQUEST_CHANGES still blocks delivery.
    assert not pending.exists()  # The quota-pending review of F did finish.
    assert GATE.main(["worktree"]) == 0
    assert not pending.exists()


def test_failed_review_does_not_persist_full_response(monkeypatch, tmp_path):
    monkeypatch.setattr(GATE, "STATE_DIR", tmp_path)
    monkeypatch.setattr(GATE, "find_claude", lambda: "claude")
    monkeypatch.setattr(GATE, "_snapshot_text", lambda *args: "review input\n")
    secret = "sensitive-source-excerpt-do-not-persist"
    monkeypatch.setattr(
        GATE.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=_result(f"{secret}; session limit resets 12:50am (Asia/Taipei)", error=True),
            stderr=secret,
        ),
    )
    with pytest.raises(GATE.ReviewError, match="quota") as error:
        GATE.run_review("worktree", "abc", [])
    assert secret not in str(error.value)
    for path in tmp_path.iterdir():
        assert secret not in path.read_text(encoding="utf-8")
    record = json.loads((tmp_path / "last_raw.json").read_text(encoding="utf-8"))
    assert record["failure_kind"] == "quota"
    assert record["retry_after"] == "12:50am (Asia/Taipei)"


def test_hooks_cover_commit_and_push_and_installer_is_used_by_batch_entrypoint():
    pre_commit = (ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    pre_push = (ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")
    batch = (ROOT / "commit_push.bat").read_text(encoding="utf-8", errors="replace")
    installer = (ROOT / "tools" / "install_claude_review_hooks.ps1").read_text(
        encoding="utf-8"
    )

    assert "claude_diff_review.py staged" in pre_commit
    assert "claude_diff_review.py push" in pre_push
    assert "install_claude_review_hooks.ps1" in batch
    assert "config --local core.hooksPath .githooks" in installer
    assert "100755" in installer
    assert "python3" in pre_commit and "python3" in pre_push
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert ".githooks/* text eol=lf" in attributes
    modes = subprocess.run(
        ["git", "ls-files", "--stage", "--", ".githooks/pre-commit", ".githooks/pre-push"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert len(modes) == 2 and all(line.startswith("100755 ") for line in modes)


def test_remote_ref_deletion_is_still_a_reviewed_diff(monkeypatch):
    empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    monkeypatch.setattr(GATE, "_empty_tree", lambda: empty_tree)
    remote = "a" * 40
    ranges = GATE.push_ranges(
        [f"(delete) {'0' * 40} refs/heads/old {remote}\n"], "origin"
    )
    assert ranges == [
        GATE.PushRange("(delete)", "refs/heads/old", remote, empty_tree)
    ]


def test_zero_net_tree_push_still_hashes_every_outgoing_commit(monkeypatch):
    base, tip = "a" * 40, "b" * 40
    responses = {
        ("rev-parse", "--verify", f"{tip}^{{commit}}"): f"{tip}\n".encode(),
        ("rev-parse", "--verify", f"{base}^{{commit}}"): f"{base}\n".encode(),
        ("cat-file", "-t", tip): b"commit\n",
    }
    monkeypatch.setattr(
        GATE, "_try_git_bytes", lambda *args: responses.get(tuple(args))
    )

    def fake_git(*args, stdin=None):
        if args[:2] == ("rev-list", "--reverse"):
            return b"c1\nc2\n"
        if args[0] == "show":
            return f"patch:{args[-1]}".encode()
        if args[0] == "diff":
            return b""
        raise AssertionError(args)

    monkeypatch.setattr(GATE, "_git_bytes", fake_git)
    update = GATE.PushRange("refs/heads/topic", "refs/heads/topic", base, tip)
    assert GATE.push_fingerprint([update]) is not None


def test_annotated_tag_name_and_annotation_are_in_hash_and_snapshot(monkeypatch):
    base, tag = "a" * 40, "b" * 40
    responses = {
        ("cat-file", "-t", tag): b"tag\n",
        ("rev-parse", "--verify", f"{tag}^{{commit}}"): f"{base}\n".encode(),
        ("rev-parse", "--verify", f"{base}^{{commit}}"): f"{base}\n".encode(),
    }
    monkeypatch.setattr(
        GATE, "_try_git_bytes", lambda *args: responses.get(tuple(args))
    )

    def fake_git(*args, stdin=None):
        if args[:2] == ("rev-list", "--reverse") or args[0] == "diff":
            return b""
        if args[:2] == ("cat-file", "-p"):
            return b"object aaaa\ntype commit\ntag confidential-release\n\nprivate note\n"
        raise AssertionError(args)

    monkeypatch.setattr(GATE, "_git_bytes", fake_git)
    update = GATE.PushRange(
        "refs/tags/confidential-release",
        "refs/tags/confidential-release",
        base,
        tag,
    )
    assert GATE.push_fingerprint([update]) is not None
    snapshot = GATE._snapshot_text("push", [update])
    assert "refs/tags/confidential-release" in snapshot
    assert "private note" in snapshot


def test_orphan_branch_falls_back_to_empty_tree_when_merge_base_is_absent(
    monkeypatch,
):
    monkeypatch.setattr(
        GATE.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )
    monkeypatch.setattr(GATE, "_try_git_bytes", lambda *args: None)
    monkeypatch.setattr(GATE, "_empty_tree", lambda: "empty-tree")
    assert GATE._fallback_new_branch_base("tip", "origin") == "empty-tree"


def test_agent_policies_require_every_diff_and_forbid_bypass_or_model_fallback():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for text in (agents, claude):
        assert "claude-opus-5" in text
        assert "所有 diff" in text
        assert "不得 fallback" in text
        assert "--no-verify" in text
        assert "PENDING" in text
        assert "自動" in text
    assert "git fetch && git reset --hard origin/main" not in claude


def test_powershell_codex_wrapper_keeps_one_session_until_approval():
    wrapper = (ROOT / "tools" / "codex_review.ps1").read_text(encoding="utf-8")
    gate = (ROOT / "tools" / "codex_gate_push.sh").read_text(encoding="utf-8")
    assert "Second and final review pass" not in wrapper
    assert "pass 2/2" not in wrapper
    assert "最多兩輪" not in wrapper and "每 task 最多兩輪" not in gate
    assert "$ThisPass = $ParsedPass + 1" in wrapper
    assert "session id 與本 repo 第一輪紀錄不符" in wrapper
    assert "[0-9a-f]{8}-[0-9a-f]{4}" in wrapper
