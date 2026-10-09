"""Read-only expiry diagnostics using the packet's known trading session."""
from datetime import date


def expiry_diagnostic(rows: list[dict], today: str, *, previous_session: str = '') -> tuple[int, list[dict]]:
    """Count unreviewed expiries; expose only IDs and dates, never trigger text."""
    expired = [w for w in rows if not w.get('last_reviewed') and w.get('deadline')
               and today and today > str(w['deadline'])]
    try:
        known_session = date.fromisoformat(previous_session) < date.fromisoformat(today)
    except ValueError:
        known_session = False
    if known_session:
        expired = [w for w in expired if str(w.get('created') or '') != previous_session]
    cases = [{'watch_id': str(w.get('watch_id') or ''),
              'created': str(w.get('created') or ''),
              'deadline': str(w.get('deadline') or '')} for w in expired[:8]]
    return len(expired), cases


def expiry_report(rows: list[dict], today: str, previous_session: str) -> dict:
    """Separate a proven no-session window without changing stored deadlines."""
    total, _ = expiry_diagnostic(rows, today)
    count, cases = expiry_diagnostic(rows, today, previous_session=previous_session)
    return {'watch_expired_unreviewed': count, 'watch_expired_unreviewed_cases': cases,
            'watch_expired_no_review_session': total - count}
