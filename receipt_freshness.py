"""Keep older workflow reruns from replacing newer delivery evidence.

This is a stdlib-only boundary shared by the standalone and batch publishers.
It compares the report day first, then terminal delivery and its event time;
run identity is a fallback for legacy receipts without a delivery timestamp.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from delivery_contract import OUTCOME_DELIVERED, OUTCOME_SKIPPED, delivery_outcome


def _day(receipt: dict[str, object]) -> date:
    raw = receipt.get("date")
    if not isinstance(raw, str) or len(raw) < 10:
        raise ValueError("寄送收據缺少可比較的日期")
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise ValueError("寄送收據日期無效") from exc


def _delivered_at(receipt: dict[str, object]) -> datetime | None:
    delivery = receipt.get("delivery")
    raw = delivery.get("delivered_at") if isinstance(delivery, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def _run_order(receipt: dict[str, object]) -> tuple[int, int]:
    run_id = str(receipt.get("github_run_id") or "")
    attempt = str(receipt.get("github_run_attempt") or "1")
    if not (re.fullmatch(r"[1-9][0-9]*", run_id)
            and re.fullmatch(r"[1-9][0-9]*", attempt)):
        raise ValueError("寄送收據缺少可比較的 run ID/attempt")
    return int(run_id), int(attempt)


def may_replace_receipt(candidate: object, incumbent: object) -> bool:
    """Preserve newer terminal evidence; replace a corrupt remote with a valid receipt."""
    if not isinstance(candidate, dict):
        raise ValueError("本班寄送收據不是物件")
    candidate_outcome = delivery_outcome(candidate.get("delivery"))
    if candidate_outcome not in (OUTCOME_DELIVERED, OUTCOME_SKIPPED):
        raise ValueError("本班寄送收據沒有終局結果")
    candidate_day = _day(candidate)
    try:
        if not isinstance(incumbent, dict):
            raise ValueError("遠端收據不是物件")
        incumbent_outcome = delivery_outcome(incumbent.get("delivery"))
        if incumbent_outcome not in (OUTCOME_DELIVERED, OUTCOME_SKIPPED):
            raise ValueError("遠端收據沒有終局結果")
        incumbent_day = _day(incumbent)
    except ValueError:
        print("::warning title=invalid-remote-receipt::遠端寄送收據無法比較，以本班已驗證終局收據修復", file=sys.stderr)
        return True
    if candidate == incumbent:
        return True
    if candidate_day != incumbent_day:
        return candidate_day > incumbent_day
    if (candidate_outcome, incumbent_outcome) == (OUTCOME_DELIVERED, OUTCOME_SKIPPED):
        return True
    if (candidate_outcome, incumbent_outcome) == (OUTCOME_SKIPPED, OUTCOME_DELIVERED):
        return False
    if (candidate_outcome, incumbent_outcome) == (OUTCOME_DELIVERED, OUTCOME_DELIVERED):
        candidate_time, incumbent_time = (_delivered_at(candidate),
                                          _delivered_at(incumbent))
        if candidate_time is not None and incumbent_time is not None:
            return candidate_time > incumbent_time
    try:
        return _run_order(candidate) > _run_order(incumbent)
    except ValueError:
        _run_order(candidate)  # A malformed local receipt must not repair anything.
        print("::warning title=invalid-remote-receipt::遠端寄送收據缺少可比較輪次，以本班終局收據修復", file=sys.stderr)
        return True


def may_replace_receipt_file(candidate_file: str | Path,
                             incumbent_json: str) -> bool:
    """Parse the fetched remote receipt before a privileged standalone push."""
    candidate = json.loads(Path(candidate_file).read_text(encoding="utf-8"))
    try:
        incumbent = json.loads(incumbent_json)
    except ValueError:
        incumbent = None
    return may_replace_receipt(candidate, incumbent)
