"""Git-backed queue for exact-commit Claude second opinions (stdlib only)."""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

STATUS = "Claude-Opus-5-Review"
EFFORT = "Claude-Opus-5-Review-Effort"
REVIEWED = "Claude-Opus-5-Reviewed-Commit"
# User decision 2026-10-14: a commit that Claude already reviewed is settled once
# a later, itself-reviewed descendant repaired the findings (status "resolved").
RESOLVED_BY = "Claude-Opus-5-Resolved-By"
SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def keys(gate=None) -> tuple[str, str, str]:
    """`(status, effort, reviewed)` trailer 名稱。

    2026-10-14 起強制 diff review 改由 gpt-6.1-sol 執行(`tools/gpt_diff_review.py`),
    它用自己的 trailer;沒有宣告的閘門(Claude 閘門、既有測試)沿用 Claude 的名稱。
    """
    return (getattr(gate, "TRAILER_STATUS", STATUS),
            getattr(gate, "TRAILER_EFFORT", EFFORT),
            getattr(gate, "TRAILER_REVIEWED", REVIEWED))


def resolved_key(gate=None) -> str:
    return getattr(gate, "TRAILER_RESOLVED_BY", RESOLVED_BY)


#: 已提交 commit 的 trailer:git 自己的解析器(與 `interpret-trailers --parse` 同一套,
#: 2026-10-14 在本 repo 218 個審查 commit 上逐筆比對一致),一次 `git log` 就取回全部。
_TRAILER_FORMAT = "%(trailers:only,unfold)"
_METAS: dict[str, dict[str, list[str]]] = {}


def _parse_trailers(raw: bytes | str) -> dict[str, list[str]]:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    result: dict[str, list[str]] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            result.setdefault(key.lower(), []).append(value.strip())
    return result


def trailers(gate, message: str) -> dict[str, list[str]]:
    """尚未提交的訊息(commit-msg hook)用 interpret-trailers 解析。"""
    return _parse_trailers(gate._git_bytes("interpret-trailers", "--parse", stdin=message.encode()))


def meta_for(gate, sha: str) -> dict[str, list[str]]:
    """已提交 commit 的 trailer;SHA 是內容定址的,同一行程內記住。"""
    if sha not in _METAS:
        _METAS[sha] = _parse_trailers(
            gate._git_bytes("show", "-s", f"--format={_TRAILER_FORMAT}", sha))
    return dict(_METAS[sha])


def message_for(gate, sha: str) -> str:
    return gate._git_bytes("show", "-s", "--format=%B", sha).decode("utf-8")


def is_pending(meta: dict, gate=None) -> bool:
    status, effort, _ = keys(gate)
    return (meta.get(status.lower()) == ["pending"]
            and meta.get(effort.lower()) == ["high"])


def is_empty_commit(gate, sha: str) -> bool:
    parents = gate._git_bytes("rev-list", "--parents", "-n", "1", sha).decode().split()
    return (len(parents) == 2 and not gate._git_bytes(
        "diff", "--name-only", parents[1], sha, "--"))


def pending_commits(gate) -> list[str]:
    """Include topic and remote branches, not just the currently checked-out one."""
    raw = gate._git_bytes("log", "--reverse", "--topo-order", "-z",
                         f"--format=%H%n{_TRAILER_FORMAT}", "--branches", "--remotes", "HEAD")
    pending: dict[str, None] = {}
    for block in raw.decode("utf-8").split("\0"):
        sha, sep, text = block.strip().partition("\n")
        if not sep:
            continue
        # Most repository commits do not participate in the queue.
        if keys(gate)[0].lower() not in text.lower():
            continue
        meta = _parse_trailers(text)
        _METAS.setdefault(sha, meta)
        if is_pending(meta, gate):
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
    status_key, effort_key, reviewed_key = keys(gate)
    targets = meta.get(reviewed_key.lower(), [])
    if targets:
        status = meta.get(status_key.lower())
        pending = pending_commits(gate) if len(targets) == 1 else []
        if status == ["resolved"]:
            evidence = resolution_valid(gate, targets[0], meta, "HEAD")
        else:
            evidence = status == ["passed"] and has_approval(gate, targets[0])
        if (gate.staged_fingerprint() is not None or len(targets) != 1
                or meta.get(effort_key.lower()) != ["high"]
                or not evidence
                or targets[0] not in pending
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
    for key, value in ((status_key, "pending"), (effort_key, "high")):
        existing = meta.get(key.lower(), [])
        if existing and existing != [value]:
            raise gate.ReviewError(f"content commit requires exactly {key}: {value}")
        if not existing:
            additions.append(f"{key}: {value}")
    if not additions:
        return 0
    separator = "\n" if meta else "\n\n"
    rewritten = message.rstrip() + separator + "\n".join(additions) + "\n"
    if not is_pending(trailers(gate, rewritten), gate):
        raise gate.ReviewError("cannot safely add pending commit trailers")
    gate._atomic_text(message_path, rewritten)
    return 0


def _is_ancestor(gate, older: str, newer: str) -> bool:
    return gate._try_git_bytes("merge-base", "--is-ancestor", older, newer) is not None


def resolution_valid(gate, target: str, meta: dict, tip: str) -> bool:
    """A "resolved" audit names one repair that is itself a closed review item.

    The repair must descend from the target and precede the audit, and it must
    have been queued (pending trailers) and later closed; an unreviewed commit,
    such as an automated data update without trailers, cannot settle a finding.
    """
    resolvers = meta.get(resolved_key(gate).lower(), [])
    if len(resolvers) != 1 or not SHA.fullmatch(resolvers[0]):
        return False
    fix = resolvers[0]
    if (fix == target or not _is_ancestor(gate, target, fix)
            or not _is_ancestor(gate, fix, tip)):
        return False
    meta_fix = meta_for(gate, fix)
    return any(is_pending(meta_fix, repair_gate) and _closed_within(repair_gate, fix, tip)
               for repair_gate in _repair_gates(gate))


def _closed_within(gate, sha: str, tip: str) -> bool:
    """`sha` 的有效結案 audit 在 `tip` 的歷史裡。

    只看 tip 可達的 commit:結案 audit 若只在另一條分支(或只在本機),推送 tip 之後
    別的 clone 看不到它,這筆 resolved 就不成立(gpt-6.1-sol 推送審查 2026-10-14)。
    不用全域 pending 清單 —— 那份清單掃所有分支,答的是另一個問題。
    """
    raw = gate._git_bytes("log", "-z", "--fixed-strings", f"--grep={sha}",
                          f"--format=%H%n{_TRAILER_FORMAT}", tip)
    reviewed = keys(gate)[2].lower()
    for block in raw.decode("utf-8").split("\0"):
        audit, sep, text = block.strip().partition("\n")
        if not sep or audit == sha:
            continue
        meta = _parse_trailers(text)
        # 只看審的就是 sha 的 audit。在 Resolved-By 提到 sha 的 resolved audit(包括正在
        # 驗證的那一筆)不是 sha 的結案;拿它們去驗證會繞回這裡無限遞迴。resolved 的修正
        # 必須是嚴格後代,所以「sha 本身被 resolved」那條鏈一定會走到底。
        if meta.get(reviewed) == [sha] and audit_target(gate, audit, meta) == sha:
            return True
    return False


def _repair_gates(gate) -> list:
    """修正 commit 可以在哪些佇列結案:自己的,加上宣告的後繼閘門。

    2026-10-14 換成 gpt-6.1-sol 後,舊 Claude 佇列裡 REQUEST_CHANGES 的項目會由排進
    GPT 佇列的新修正修好;那個修正在 GPT 佇列結案,就能結掉 Claude 的舊項目。
    """
    successor = getattr(gate, "SUCCESSOR_GATE", None)
    return [gate] + ([importlib.import_module(successor)] if successor else [])


_AUDITS: dict[tuple, str | None] = {}


def audit_target(gate, sha: str, meta: dict) -> str | None:
    """A durable, empty audit for an exact pending ancestor survives cache loss.

    commit-msg requires the machine-verified receipt when creating the audit;
    subsequent transport must not depend on that ignored, disposable cache.

    結果只取決於 `sha` 這個 commit 與它的祖先 —— SHA 是內容定址的,同一個 SHA 就是同一段
    歷史 —— 所以同一行程內記住它(結案鏈 21 筆 audit 共用一條鏈,不記住要跑一分多鐘)。
    """
    key = (getattr(gate, "__name__", repr(gate)), sha, repr(sorted(meta.items())))
    if key not in _AUDITS:
        _AUDITS[key] = _audit_target(gate, sha, meta)
    return _AUDITS[key]


def _audit_target(gate, sha: str, meta: dict) -> str | None:
    status_key, effort_key, reviewed_key = keys(gate)
    targets = meta.get(reviewed_key.lower(), [])
    status = meta.get(status_key.lower())
    if (status not in (["passed"], ["resolved"])
            or meta.get(effort_key.lower()) != ["high"] or len(targets) != 1
            or not SHA.fullmatch(targets[0]) or not is_empty_commit(gate, sha)):
        return None
    target = targets[0]
    if not _is_ancestor(gate, target, sha):
        return None
    if status == ["resolved"] and not resolution_valid(gate, target, meta, sha):
        return None
    return target if is_pending(meta_for(gate, target), gate) else None


def accepted_outgoing(gate, sha: str, meta: dict) -> bool:
    """這個 outgoing commit 是否已在佇列中(pending)或是有效的 audit。

    閘門可以宣告 `LEGACY_GATE`(2026-10-14 起 GPT 閘門宣告 Claude 閘門):切換前已排進
    舊佇列的歷史 commit,其**空的**補審 audit 不是新 diff,不另排進新佇列,但 push 時
    仍要以舊佇列的規則驗證通過(收據不需要,`audit_target` 只看 Git 上的事實)。
    """
    if is_pending(meta, gate) or audit_target(gate, sha, meta):
        return True
    legacy = getattr(gate, "LEGACY_GATE", None)
    # 切換前排進舊佇列的內容 commit 本身也接受:它仍留在舊佇列等補審(`pending` 列得到)
    return legacy is not None and (is_pending(meta, legacy)
                                   or audit_target(legacy, sha, meta) is not None)


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
            meta = meta_for(gate, sha)
            if not accepted_outgoing(gate, sha, meta):
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
            meta = meta_for(gate, sha)
            if not accepted_outgoing(gate, sha, meta):
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
