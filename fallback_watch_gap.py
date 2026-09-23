"""Expose due watch reviews that a non-specialized analysis cannot validate.

This is a read-only projection of the existing ledger. A legacy paragraph is not
evidence of a structured review and must never advance ``last_reviewed``.
"""
from __future__ import annotations

import datetime as dt
import re

import analysis_origin as origin
import analysis_recap as recap


def _date(value: object) -> str:
    text = str(value or "")[:10]
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def due_cases(state: dict, report_date: str, target_session: str = "") -> dict:
    """Use the report day for expiry and the target session for model selection."""
    today = _date(report_date)
    if not today or not isinstance(state, dict):
        return {"count": 0, "cases": []}
    try:
        ledger = recap._watch_ledger(state)
        selected = {row["watch_id"] for row in recap.usable_watch(state, _date(target_session) or today)}
    except (TypeError, ValueError):
        return {"count": 0, "cases": [], "error": "invalid_ledger"}
    cases = []
    unselected_cases = []
    for watch in ledger:
        created, deadline = _date(watch.get("created")), _date(watch.get("deadline"))
        reviewed = _date(watch.get("last_reviewed"))
        if (watch.get("status") != recap.WATCH_OPEN or not created
                or created >= today or not deadline or deadline > today
                or (reviewed and reviewed >= deadline)):
            continue
        raw_id = str(watch.get("watch_id") or "")
        case = {"watch_id": raw_id if re.fullmatch(r"w\d+", raw_id) else "?",
                "created": created, "deadline": deadline}
        if raw_id not in selected:
            unselected_cases.append(case)
        cases.append(case)
    return {"count": len(cases), "unselected_count": len(unselected_cases),
            "cases": cases[:8], "unselected_cases": unselected_cases[:8]}


def _unreviewed(record: dict, analysis_origin: str) -> int:
    try:
        key = "unselected_count" if origin.normalize(analysis_origin) == origin.LUNA_SPECIALIZED else "count"
        return max(0, int(record.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def reader_notice(record: dict, analysis_origin: str) -> str:
    """Natural-language caveat, not an internal ID or a claim of assessment."""
    count = _unreviewed(record if isinstance(record, dict) else {}, analysis_origin)
    if not count:
        return ""
    return ("<p role='note' style='margin:10px 0;color:#92400e'>"
            f"跨日觀察提醒：有 {count} 項觀察條件今天或此前到期，"
            "但本日未完成可驗證的逐項回顧；不能據此認定條件未觸發。"
            "</p>")


def findings(record: dict, analysis_origin: str, *, digest: bool = False) -> list[tuple[str, str, str]]:
    """Surface a due-day gap now, rather than only after tomorrow's expiry."""
    if digest:
        return []
    record = record if isinstance(record, dict) else {}
    if record.get("error"):
        return [("watch_due_scan_failed", "defect", "觀察點帳本格式錯誤，本班無法檢查到期回顧")]
    count = _unreviewed(record, analysis_origin)
    if not count:
        return []
    key = "unselected_cases" if origin.normalize(analysis_origin) == origin.LUNA_SPECIALIZED else "cases"
    rows = record.get(key) if isinstance(record.get(key), list) else []
    examples = [f"{row['watch_id']}(期限 {row['deadline']})" for row in rows[:3]
                if isinstance(row, dict) and row.get("watch_id") and row.get("deadline")]
    detail = (f"{count} 條觀察點今天或此前到期，本班未完成已驗證的逐項回顧；"
              "不可視為未觸發，也不可事後改寫期限")
    if examples:
        detail += "：" + "、".join(examples)
    return [("watch_due_unreviewed", "degraded", detail)]
