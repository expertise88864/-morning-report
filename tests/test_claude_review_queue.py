"""Exercise deferred-review policy against real isolated Git repositories."""
import json
import subprocess

import pytest

from tools import claude_diff_review as gate
from tools import claude_review_queue as queue


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "STATE_DIR", tmp_path / ".claude-review")
    monkeypatch.setattr(gate, "PENDING_PATH", tmp_path / ".claude-review/pending_review.json")
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "-b", "main")
    git("config", "user.name", "Review Test")
    git("config", "user.email", "test@example.invalid")
    git("config", "core.hooksPath", ".test-no-hooks")
    (tmp_path / ".gitignore").write_text(".claude-review/\nmessage.txt\n", encoding="utf-8")
    git("add", ".gitignore")
    git("commit", "-m", "baseline")
    return tmp_path, git


def pending_commit(repo, content="first"):
    root, git = repo
    (root / "sample.txt").write_text(content, encoding="utf-8")
    git("add", "sample.txt")
    path = root / "message.txt"
    path.write_text("Change a file\n", encoding="utf-8")
    queue.commit_message(gate, str(path))
    git("commit", "-F", str(path))
    return git("rev-parse", "HEAD")


def test_content_commit_gets_pending_trailers_and_queue_survives_local_state_loss(repo):
    sha = pending_commit(repo)
    meta = queue.trailers(gate, queue.message_for(gate, sha))
    assert queue.is_pending(meta)
    assert queue.pending_commits(gate) == [sha]
    assert not gate.STATE_DIR.exists()  # The queue is entirely recoverable from Git.


def test_commit_message_body_divider_does_not_hide_review_trailers(repo):
    root, git = repo
    (root / "sample.txt").write_text("divider-case", encoding="utf-8")
    git("add", "sample.txt")
    path = root / "message.txt"
    path.write_text("Change a file\n\n---\n\nBody details\n", encoding="utf-8")

    assert queue.commit_message(gate, str(path)) == 0
    git("commit", "-F", str(path))
    assert queue.is_pending(queue.trailers(gate, queue.message_for(gate, git("rev-parse", "HEAD"))))


@pytest.mark.parametrize("comment_char", ["#", ";"])
def test_verbose_scissors_keep_review_trailers_in_commit_message(repo, comment_char):
    root, git = repo
    git("config", "core.commentChar", comment_char)
    (root / "sample.txt").write_text("verbose-case", encoding="utf-8")
    git("add", "sample.txt")
    path = root / "message.txt"
    marker = f"{comment_char} ------------------------ >8 ------------------------"
    path.write_text(
        f"Change a file\n\n{comment_char} Please enter the commit message.\n"
        f"{marker}\ndiff --git a/sample.txt b/sample.txt\n",
        encoding="utf-8",
    )

    assert queue.commit_message(gate, str(path)) == 0
    rewritten = path.read_text(encoding="utf-8")
    assert rewritten.index("Claude-Opus-5-Review: pending") < rewritten.index(marker)
    git("commit", "--cleanup=scissors", "-F", str(path))
    assert queue.is_pending(queue.trailers(gate, queue.message_for(gate, git("rev-parse", "HEAD"))))


def test_only_empty_verified_audit_resolves_its_exact_target(repo):
    root, git = repo
    first = pending_commit(repo)
    second = pending_commit(repo, "second")
    message = ("Record review\n\nClaude-Opus-5-Review: passed\n"
               "Claude-Opus-5-Review-Effort: high\n"
               f"Claude-Opus-5-Reviewed-Commit: {first}\n")
    path = root / "message.txt"
    path.write_text(message, encoding="utf-8")
    with pytest.raises(gate.ReviewError, match="exact pending-commit approval"):
        queue.commit_message(gate, str(path))
    span = queue.commit_range(gate, first)
    queue.save_approvals(gate, [span], gate.push_fingerprint([span]))
    assert queue.commit_message(gate, str(path)) == 0
    (root / "sample.txt").write_text("unrelated user edit", encoding="utf-8")
    git("add", "sample.txt")
    with pytest.raises(gate.ReviewError, match="empty diff"):
        queue.commit_message(gate, str(path))
    git("restore", "--staged", "sample.txt")  # Only the isolated test repository.
    git("commit", "--allow-empty", "-F", str(path))
    assert queue.pending_commits(gate) == [second]
    audit = git("rev-parse", "HEAD")
    queue.receipt_path(gate, first).unlink()  # Disposable isolated-test cache only.
    assert queue.quota_push_allowed(gate, [gate.PushRange(
        "refs/heads/main", "refs/heads/main", second, audit)])


def test_push_receipt_cannot_authorize_exact_commit_audit(repo):
    _, git = repo
    base = git("rev-parse", "HEAD")
    sha = pending_commit(repo)
    push = gate.PushRange("refs/heads/main", "refs/heads/main", base, sha)
    queue.save_approvals(gate, [push], gate.push_fingerprint([push]))
    assert not queue.has_approval(gate, sha)

    exact = queue.commit_range(gate, sha)
    queue.save_approvals(gate, [exact], gate.push_fingerprint([exact]))
    assert queue.has_approval(gate, sha)


def test_later_exact_request_changes_revokes_older_approval(repo):
    sha = pending_commit(repo)
    exact = queue.commit_range(gate, sha)
    fingerprint = gate.push_fingerprint([exact])
    queue.save_approvals(gate, [exact], fingerprint)
    assert queue.has_approval(gate, sha)

    queue.save_rejections(gate, [exact], fingerprint)
    assert not queue.has_approval(gate, sha)


def test_exact_merge_review_preserves_individual_merged_commit_receipt(repo):
    root, git = repo
    git("checkout", "-b", "side")
    side = pending_commit(repo, "side")
    side_range = queue.commit_range(gate, side)
    queue.save_approvals(gate, [side_range], gate.push_fingerprint([side_range]))
    assert queue.has_approval(gate, side)

    git("checkout", "main")
    (root / "main.txt").write_text("main", encoding="utf-8")
    git("add", "main.txt")
    git("commit", "-m", "Main change")
    git("merge", "--no-ff", "side", "-m", "Merge side")
    merged = git("rev-parse", "HEAD")
    merge_range = queue.commit_range(gate, merged)
    assert side in gate._outgoing_commits(merge_range.base, merge_range.tip)

    merge_fingerprint = gate.push_fingerprint([merge_range])
    queue.save_approvals(gate, [merge_range], merge_fingerprint)
    assert queue.has_approval(gate, side)
    assert queue.has_approval(gate, merged)

    queue.save_rejections(gate, [merge_range], merge_fingerprint)
    assert queue.has_approval(gate, side)
    assert not queue.has_approval(gate, merged)


def test_review_entry_point_never_promotes_push_approval_to_exact_receipt(repo, monkeypatch):
    import io
    _, git = repo
    base = git("rev-parse", "HEAD")
    sha = pending_commit(repo)
    monkeypatch.setattr(gate.sys, "stdin", io.StringIO(
        f"refs/heads/main {sha} refs/heads/main {base}\n"))
    monkeypatch.setattr(gate, "run_review", lambda *args: 0)
    assert gate.main(["push"]) == 0
    assert not queue.receipt_path(gate, sha).exists()

    assert gate.main(["commit", "--commit", sha]) == 0
    assert queue.has_approval(gate, sha)
    monkeypatch.setattr(gate, "run_review", lambda *args: 2)
    assert gate.main(["commit", "--commit", sha]) == 2
    assert not queue.has_approval(gate, sha)


def test_quota_push_requires_every_outgoing_commit_to_be_marked(repo):
    _, git = repo
    base = git("rev-parse", "HEAD")
    marked = pending_commit(repo)
    span = gate.PushRange("refs/heads/main", "refs/heads/main", base, marked)
    assert queue.quota_push_allowed(gate, [span])
    git("commit", "--allow-empty", "-m", "not reviewed or marked")
    unmarked = git("rev-parse", "HEAD")
    assert not queue.quota_push_allowed(gate, [span._replace(tip=unmarked)])
    assert not queue.quota_push_allowed(gate, [span._replace(local_ref="(delete)")])
    assert not queue.quota_push_allowed(gate, [span._replace(remote_ref="refs/tags/v1")])


@pytest.mark.parametrize("is_error,status,result,expected", [
    (True, 429, "temporarily unavailable", True),
    (True, None, "You've hit your session limit", True),
    (True, None, "You've hit your limit · resets 7pm (Asia/Taipei)", True),
    (True, 401, "Not logged in", False),
    (False, None, "Check quota handling\nAPPROVE", False),
    (False, None, "quota\nREQUEST_CHANGES", False),
])
def test_only_provider_quota_failures_can_defer(is_error, status, result, expected):
    raw = json.dumps({"type": "result", "is_error": is_error,
                      "api_error_status": status, "result": result})
    assert gate._is_quota_error(raw) is expected


def test_hook_exit_policy_distinguishes_quota_from_defects(repo, monkeypatch):
    pending_commit(repo)
    monkeypatch.setattr(gate, "worktree_fingerprint", lambda: "same")
    def fail_quota(*args):
        raise gate.QuotaUnavailable("quota resets 12:50am (Asia/Taipei)")
    monkeypatch.setattr(gate, "run_review", fail_quota)
    assert gate.main(["worktree"]) == 3
    monkeypatch.setattr(gate, "run_review", lambda *args: 2)
    assert gate.main(["worktree"]) == 2


def test_mixed_model_evidence_is_not_accepted():
    payload = {"type": "result", "is_error": False, "result": "APPROVE",
               "modelUsage": {"claude-opus-5-5": {}, "claude-sonnet-5": {}}}
    with pytest.raises(gate.ReviewError, match="cannot prove"):
        gate.parse_review_output(json.dumps(payload))


def test_replace_refs_cannot_change_exact_sha_review_evidence(repo):
    _, git = repo
    base = git("rev-parse", "HEAD")
    original = pending_commit(repo, "original-content-must-be-reviewed")
    span = queue.commit_range(gate, original)
    before = gate.push_fingerprint([span])
    substitute = git("commit-tree", git("rev-parse", f"{base}^{{tree}}"),
                     "-p", base, "-m", "benign substitute")
    git("replace", original, substitute)  # Only the isolated test object database.
    assert git("show", "-s", "--format=%s", original) == "benign substitute"
    assert gate.push_fingerprint([span]) == before
    assert "original-content-must-be-reviewed" in gate._snapshot_text("commit", [span])
    assert queue.is_pending(queue.trailers(gate, queue.message_for(gate, original)))


def test_exact_commit_snapshot_includes_state_text_hidden_by_gitattributes(repo):
    root, git = repo
    (root / ".gitattributes").write_text(
        "state/*.json -diff\nstate/emails/** -diff binary\n", encoding="utf-8"
    )
    state = root / "state" / "podcast_digest.json"
    state.parent.mkdir()
    state.write_text('{"episode": "before"}\n', encoding="utf-8")
    private_mail = root / "state" / "emails" / "private.json"
    private_mail.parent.mkdir()
    private_mail.write_text('{"mail": "before"}\n', encoding="utf-8")
    git("add", ".gitattributes", "state/podcast_digest.json", "state/emails/private.json")
    git("commit", "-m", "baseline state")

    state.write_text('{"episode": "after-review-this-value"}\n', encoding="utf-8")
    private_mail.write_text('{"mail": "private-mail-value"}\n', encoding="utf-8")
    git("add", "state/podcast_digest.json", "state/emails/private.json")
    message = root / "message.txt"
    message.write_text("Update podcast state\n", encoding="utf-8")
    queue.commit_message(gate, str(message))
    git("commit", "-F", str(message))
    sha = git("rev-parse", "HEAD")

    snapshot = gate._snapshot_text("commit", [queue.commit_range(gate, sha)])
    assert '+{"episode": "after-review-this-value"}' in snapshot
    assert '-{"episode": "before"}' in snapshot
    assert "private-mail-value" not in snapshot


def test_queue_follows_topic_branches_after_switching_away(repo):
    _, git = repo
    git("switch", "-c", "topic")
    sha = pending_commit(repo)
    git("switch", "main")
    assert queue.pending_commits(gate) == [sha]
    git("update-ref", "refs/remotes/origin/topic", sha)
    assert queue.pending_commits(gate) == [sha]  # Never double count shared objects.


def test_divergent_branch_updates_are_blocked_even_with_pending_markers(repo):
    _, git = repo
    base = git("rev-parse", "HEAD")
    published = pending_commit(repo)
    git("switch", "-c", "divergent", base)
    rewritten = pending_commit(repo, "replacement")
    span = gate.PushRange("refs/heads/divergent", "refs/heads/main", published, rewritten)
    assert not queue.quota_push_allowed(gate, [span])
    with pytest.raises(gate.ReviewError, match="fast-forward"):
        queue.validate_push(gate, [span])


def test_message_only_amend_gets_marked_and_unmarked_commits_never_reach_review(repo, monkeypatch):
    import io
    root, git = repo
    base = git("rev-parse", "HEAD")
    pending_commit(repo)
    path = root / "message.txt"
    path.write_text("New subject\n", encoding="utf-8")
    assert gate.staged_fingerprint() is None
    queue.commit_message(gate, str(path))
    git("commit", "--amend", "-F", str(path))  # Isolated test repo, never published.
    marked = git("rev-parse", "HEAD")
    assert queue.is_pending(queue.trailers(gate, queue.message_for(gate, marked)))
    git("commit", "--amend", "-m", "unmarked")
    unmarked = git("rev-parse", "HEAD")
    monkeypatch.setattr(gate.sys, "stdin", io.StringIO(
        f"refs/heads/main {unmarked} refs/heads/main {base}\n"))
    def must_not_run(*args):
        pytest.fail("policy violation must be blocked before spending review quota")
    monkeypatch.setattr(gate, "run_review", must_not_run)
    assert gate.main(["push"]) == 4


def test_non_quota_errors_preserve_the_existing_quota_reset(repo, monkeypatch):
    gate.record_pending_review("commit", "original", gate.QuotaUnavailable(
        "You've hit your limit · resets 7pm (Asia/Taipei)"))
    before = gate.PENDING_PATH.read_bytes()
    assert json.loads(before)["retry_after"] == "7pm (Asia/Taipei)"
    monkeypatch.setattr(gate, "worktree_fingerprint", lambda: "new-diff")
    def authentication_error(*args):
        raise gate.ReviewError("Not logged in")
    monkeypatch.setattr(gate, "run_review", authentication_error)
    assert gate.main(["worktree"]) == 4
    assert gate.PENDING_PATH.read_bytes() == before
    blocked = json.loads((gate.STATE_DIR / "last_blocked.json").read_text())
    assert blocked["status"] == "BLOCKED"


def test_reviewed_tag_and_zero_commit_branch_creation_are_not_quota_deferrals(repo, monkeypatch):
    import io
    _, git = repo
    base = git("rev-parse", "HEAD")
    branch = gate.PushRange("refs/heads/new", "refs/heads/new", base, base, True)
    queue.validate_push(gate, [branch])
    assert not queue.quota_push_allowed(gate, [branch])
    git("tag", "-a", "release", "-m", "review this annotation")
    tag = git("rev-parse", "refs/tags/release")
    span = gate.PushRange("refs/tags/release", "refs/tags/release", base, tag, True)
    queue.validate_push(gate, [span])
    assert not queue.quota_push_allowed(gate, [span])
    monkeypatch.setattr(gate.sys, "stdin", io.StringIO(
        f"refs/tags/release {tag} refs/tags/release {'0' * 40}\n"))
    monkeypatch.setattr(gate, "_fallback_new_branch_base", lambda *args: base)
    calls = []
    def approve(scope, fingerprint, ranges):
        calls.append(scope)
        return 0
    monkeypatch.setattr(gate, "run_review", approve)
    assert gate.main(["push"]) == 0
    assert calls == ["push"]  # Tags still receive the normal live review.


@pytest.mark.parametrize("annotated", [False, True])
def test_existing_tag_replacement_is_blocked_even_with_commit_ancestry(repo, annotated):
    _, git = repo
    base = git("rev-parse", "HEAD")
    if annotated:
        git("tag", "-a", "published", "-m", "old annotation")
        git("tag", "-a", "replacement", "-m", "new annotation")
        old = git("rev-parse", "published")
        tip = git("rev-parse", "replacement")
    else:
        old = base
        tip = pending_commit(repo, "descendant")
    update = gate.push_ranges([
        f"refs/tags/replacement {tip} refs/tags/published {old}\n"], "origin")[0]
    assert not update.is_new_ref
    assert queue._fast_forward(gate, update)  # Commit ancestry alone misses tag rewrites.
    with pytest.raises(gate.ReviewError, match="published tags are immutable"):
        queue.validate_push(gate, [update])
