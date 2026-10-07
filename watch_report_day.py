"""Date boundary shared by recap storage and its rendered watch admission."""


def report_day(packet) -> str:
    """Use the Taipei report day for watches; views retain their market session."""
    pk = packet if isinstance(packet, dict) else {}
    as_of = str(pk.get("as_of") or "")[:10]
    try:
        import datetime as dt
        return dt.date.fromisoformat(as_of).isoformat()
    except ValueError:
        return str(pk.get("target_session_date") or "")[:10]
