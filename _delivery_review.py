"""Whole-PR review gate for main promotion (user decision 2026-10-15).

tools/pr_review.py reviews each PR once and records the verdict as the owner's
commit status on the exact head; _delivery.py requires it before main.
Kept separate so the frozen _delivery.py does not absorb the gate.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
REVIEW_CONTEXT = "codex-review/gpt-6.1-sol"
REVIEW_DESCRIPTION = re.compile(r"(APPROVE|quota-pending) base=([0-9a-f]{40})")
_SHA = re.compile(r"^[0-9a-f]{40}$")


class Blocked(RuntimeError):
    """Missing or unsuccessful evidence; do not publish. Shared with _delivery.py."""


def requires_pr_review(cfg: dict, base_cfg: dict | None) -> bool:
    """A candidate cannot switch off the whole-PR review its base policy requires."""
    return bool(cfg.get("pr_review")) or bool((base_cfg or {}).get("pr_review"))


def pr_review(api: Any, cfg: dict, sha: str, previous: str) -> dict:
    """The owner's newest review status on the exact head must be APPROVE/success
    (or quota-pending/pending: publishable, reported, never approval), and its
    reviewed base must be an ancestor of the previous main so every promoted
    commit was in the reviewed range."""
    if not _SHA.fullmatch(sha) or not _SHA.fullmatch(previous):
        raise Blocked("A full commit SHA is required")
    owner = cfg["repository"].split("/")[0].lower()
    rows = [s for s in api.pages(f"/commits/{sha}/statuses")
            if s.get("context") == REVIEW_CONTEXT
            and str((s.get("creator") or {}).get("login", "")).lower() == owner]
    if not rows:
        raise Blocked("No PR review for this exact head: run python tools/pr_review.py")
    latest = max(rows, key=lambda s: s.get("id", 0))
    match = REVIEW_DESCRIPTION.fullmatch(str(latest.get("description", "")))
    state = latest.get("state")
    if not match or (match.group(1), state) not in (("APPROVE", "success"), ("quota-pending", "pending")):
        raise Blocked(f"PR review not approved: {state} {latest.get('description')!r}")
    reviewed_base = match.group(2)
    if subprocess.run(["git", "merge-base", "--is-ancestor", reviewed_base, previous],
                      cwd=ROOT).returncode != 0:
        raise Blocked("The reviewed range does not cover main..head; review the PR again")
    if match.group(1) == "quota-pending":
        print("WARNING: PR review is quota-pending (not approved); backfill with "
              "python tools/pr_review.py --pending", file=sys.stderr)
    return {"sha": sha, "pr_review": match.group(1), "reviewed_base": reviewed_base,
            "url": latest.get("target_url", "")}
