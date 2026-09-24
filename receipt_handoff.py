"""Validate delivery-receipt ownership before crossing the publish boundary.

This module and its delivery contract dependency use only the standard library,
so the credential-bearing publish job does not install third-party packages.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import delivery_contract as _delivery_contract
from receipt_freshness import may_replace_receipt_file

RECEIPT_REPO_PATH = "state/delivery_receipt.json"


def receipt_attempt_allowed(producer_attempt: str, current_attempt: str) -> bool:
    """A prior producer attempt may be consumed by a rerun, never a future one."""
    canonical = re.compile(r"[1-9][0-9]*\Z")
    return bool(canonical.fullmatch(producer_attempt)
                and canonical.fullmatch(current_attempt)
                and int(producer_attempt) <= int(current_attempt))


def receipt_belongs_to_run(local_file: str | Path, run_id: str,
                           run_attempt: str | None = None) -> bool:
    """Only this run attempt's terminal receipt may overwrite remote evidence.

    A no-op backup still checks out an older receipt. GitHub reruns also retain
    the run ID, so the attempt number and artifact name must match as well.
    """
    if not run_id:
        return False
    try:
        data = json.loads(Path(local_file).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    if not isinstance(data, dict) or str(data.get("github_run_id") or "") != run_id:
        return False
    if run_attempt is not None and str(data.get("github_run_attempt") or "") != run_attempt:
        return False
    return _delivery_contract.delivery_outcome(data.get("delivery")) in (
        _delivery_contract.OUTCOME_DELIVERED,
        _delivery_contract.OUTCOME_SKIPPED,
    )


def filter_batch_allowlist(allow: list[str], *, run_id: str,
                           run_attempt: str,
                           receipt_file: str | Path = RECEIPT_REPO_PATH,
                           remote_receipt_file: str | Path | None = None) -> list[str]:
    """Do not let a state batch re-add a stale receipt after artifact filtering.

    ``allow`` has already passed the publisher's lexical path validation.
    A broad parent path cannot be safely filtered, so reject it altogether.
    """
    if not run_id or not run_attempt:
        raise ValueError("缺少本班 run ID 或 attempt，無法驗證收據")
    if any(RECEIPT_REPO_PATH.startswith(p + "/") for p in allow):
        raise ValueError("交棒清單包含覆蓋寄送收據的上層目錄")
    if RECEIPT_REPO_PATH in allow:
        keep = receipt_belongs_to_run(receipt_file, run_id, run_attempt)
        if keep and remote_receipt_file is not None:
            keep = may_replace_receipt_file(
                receipt_file, Path(remote_receipt_file).read_text(encoding="utf-8"))
        if not keep:
            print("::warning title=stale-delivery-receipt::交棒清單的收據不屬於寄信輪次或已落後遠端，已排除",
                  file=sys.stderr)
            allow = [p for p in allow if p != RECEIPT_REPO_PATH]
    if not allow:
        raise ValueError("本班沒有可發佈的 state 路徑")
    return allow
