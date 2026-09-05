"""Git-backed queue for exact-commit Claude second opinions (stdlib only)."""
from __future__ import annotations

import json
import re
from pathlib import Path

STATUS = "Claude-Opus-5-Review"
EFFORT = "Claude-Opus-5-Review-Effort"
REVIEWED = "Claude-Opus-5-Reviewed-Commit"
SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def trailers(gate, message: str) -> dict[str, list[str]]:
    raw = gate._git_bytes("interpret-trailers", "--parse", stdin=message.encode())
    result: dict[str, list[str]] = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            result.setdefault(key.lower(), []).append(value.strip())
    return result


def message_for(gate, sha: str) -> str:
    return gate._git_bytes("show", "-s", "--format=%B", sha).decode("utf-8")


def is_pending(meta: dict) -> bool:
    return (meta.get(STATUS.lower()) == ["pending"]
            and meta.get(EFFORT.lower()) == ["high"])


def is_empty_commit(gate, sha: str) -> bool:
    parents = gate._git_bytes("rev-list", "--parents", "-n", "1", sha).decode().split()
    return (len(parents) == 2 and not gate._git_bytes(
        "diff", "--name-only", parents[1], sha, "--"))


def pending_commits(gate) -> list[str]:
    """Include topic and remote branches, not just the currently checked-out one."""
    raw = gate._git_bytes("log", "--reverse", "--topo-order", "-z", "--format=%H%n%B",
                         "--branches", "--remotes", "HEAD")
    pending: dict[str, None] = {}
    for block in raw.decode("utf-8").split("\0"):
        sha, sep, message = block.strip().partition("\n")
        if not sep:
            continue
        # Most repository commits do not participate in the queue.
        if STATUS.lower() not in message.lower():
            continue
        meta = trailers(gate, message)
        if is_pending(meta):
            pending[sha] = None
        else:
            target = audit_target(gate, sha, meta)
            if target:
                pending.pop(target, None)
    return list(pending)


def receipt_path(gate, sha: str) -> Path:
    if not SHA.fullmatch(sha):
        raise gate.ReviewError("reviewed commit must be a full hexadecimal SHA")
    return gate.STATE_DIR / "commits" / f"{sha}.json"


def has_approval(gate, sha: str) -> bool:
    path = receipt_path(gate, sha)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(record, dict) and record.get("commit") == sha
            and record.get("model") == gate.MODEL and record.get("effort") == gate.EFFORT
            and record.get("verdict") == "APPROVE"
            and bool(record.get("fingerprint")))


def save_approvals(gate, ranges, fingerprint: str) -> None:
    for ref in ranges:
        for sha in gate._outgoing_commits(ref.base, ref.tip):
            record = {"commit": sha, "model": gate.MODEL, "effort": gate.EFFORT,
                      "verdict": "APPROVE", "fingerprint": fingerprint}
            gate._atomic_text(receipt_path(gate, sha), json.dumps(record) + "\n")


def commit_message(gate, path: str) -> int:
    """Queue every content commit; allow empty audit commits only with real evidence."""
    message_path = Path(path)
    message = message_path.read_text(encoding="utf-8")
    meta = trailers(gate, message)
    targets = meta.get(REVIEWED.lower(), [])
    if targets:
        if (gate.staged_fingerprint() is not None or len(targets) != 1
                or meta.get(STATUS.lower()) != ["passed"]
                or meta.get(EFFORT.lower()) != ["high"]
                or not has_approval(gate, targets[0])
                or targets[0] not in pending_commits(gate)
                or gate._try_git_bytes("merge-base", "--is-ancestor", targets[0], "HEAD") is None):
            raise gate.ReviewError("audit commit requires an empty diff and exact pending-commit approval")
        return 0
    # The final commit, including its metadata, is queued even if staged review passed.
    # This includes message-only amends: their index equals HEAD even though
    # the resulting commit still has a nonempty diff against its real parent.
    # Git's --trailer replacement treats prefix-related keys as matches, so
    # adding Review-Effort with --if-exists=replace can erase Review itself.
    # Preserve unrelated trailers and append only absent, exact policy keys.
    additions = []
    for key, value in ((STATUS, "pending"), (EFFORT, "high")):
        existing = meta.get(key.lower(), [])
        if existing and existing != [value]:
            raise gate.ReviewError(f"content commit requires exactly {key}: {value}")
        if not existing:
            additions.append(f"{key}: {value}")
    if not additions:
        return 0
    separator = "\n" if meta else "\n\n"
    rewritten = message.rstrip() + separator + "\n".join(additions) + "\n"
    if not is_pending(trailers(gate, rewritten)):
        raise gate.ReviewError("cannot safely add pending commit trailers")
    gate._atomic_text(message_path, rewritten)
    return 0


def audit_target(gate, sha: str, meta: dict) -> str | None:
    """A durable, empty audit for an exact pending ancestor survives cache loss.

    commit-msg requires the machine-verified receipt when creating the audit;
    subsequent transport must not depend on that ignored, disposable cache.
    """
    targets = meta.get(REVIEWED.lower(), [])
    if (meta.get(STATUS.lower()) != ["passed"]
            or meta.get(EFFORT.lower()) != ["high"] or len(targets) != 1
            or not SHA.fullmatch(targets[0]) or not is_empty_commit(gate, sha)):
        return None
    target = targets[0]
    if gate._try_git_bytes("merge-base", "--is-ancestor", target, sha) is None:
        return None
    return target if is_pending(trailers(gate, message_for(gate, target))) else None


def _fast_forward(gate, ref) -> bool:
    return (ref.base == gate._empty_tree()
            or gate._try_git_bytes("merge-base", "--is-ancestor", ref.base, ref.tip) is not None)


def quota_push_allowed(gate, ranges) -> bool:
    """Quota deferral is allowed only for explicitly queued, fast-forward branches."""
    if not ranges:
        return False
    for ref in ranges:
        if (not ref.remote_ref.startswith("refs/heads/") or ref.local_ref == "(delete)"
                or not _fast_forward(gate, ref)):
            return False
        commits = gate._outgoing_commits(ref.base, ref.tip)
        if not commits:
            return False
        for sha in commits:
            meta = trailers(gate, message_for(gate, sha))
            if is_pending(meta):
                continue
            if audit_target(gate, sha, meta):
                continue
            return False
    return True


def validate_push(gate, ranges) -> None:
    """Policy applies even when Claude is available, before any paid review."""
    for ref in ranges:
        if ref.remote_ref.startswith("refs/tags/") and not ref.is_new_ref:
            raise gate.ReviewError("published tags are immutable; replacing them requires a forbidden force push")
        if ref.local_ref == "(delete)" or not _fast_forward(gate, ref):
            raise gate.ReviewError("push must be fast-forward; deletions and history rewrites are blocked")
        for sha in gate._outgoing_commits(ref.base, ref.tip):
            meta = trailers(gate, message_for(gate, sha))
            if not (is_pending(meta) or audit_target(gate, sha, meta)):
                raise gate.ReviewError("outgoing commits require pending/high trailers or verified empty audits")
    # Tags and ref creations with no outgoing commits may proceed normally.
    # Their quota-only restrictions belong in quota_push_allowed, not here.


def commit_range(gate, sha: str):
    receipt_path(gate, sha)  # validate before using it as a Git revision
    parents = gate._git_bytes("rev-list", "--parents", "-n", "1", sha).decode().split()
    if not parents or parents[0] != sha:
        raise gate.ReviewError("commit was not found")
    base = parents[1] if len(parents) > 1 else gate._empty_tree()
    return gate.PushRange("commit", "commit", base, sha)
