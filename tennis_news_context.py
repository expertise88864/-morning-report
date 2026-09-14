"""Reject same-final previews only when an already-fetched result establishes identity."""
import datetime as dt
import re


def completed_preview(title, results, now):
    from render_utils import _TENNIS_EVENT_ZH, _TENNIS_PLAYER_ZH
    if not re.search(r"爭奪|爭冠|挑戰|力拚|力拼|決賽.*(?:前瞻|預告|將)|final preview", title, re.I):
        return False
    if re.search(r"擊敗|奪冠|贏得|回顧|重溫|賽後|歷史上的今天|^\s*(?:昔日|當年)|defeated|won|recap", title, re.I):
        return False
    for result in results or []:
        if not isinstance(result, dict) or result.get('round') != 'Final':
            continue
        try:
            played = dt.datetime.fromisoformat(str(result.get('played_at') or '').replace('Z', '+00:00'))
            if played.tzinfo is None or not dt.timedelta(0) <= now - played <= dt.timedelta(days=7):
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        event = str(result.get('event_key') or result.get('event') or '')
        event_zh = _TENNIS_EVENT_ZH.get(event)
        if not event_zh or not (event.casefold() in title.casefold() or event_zh in title):
            continue
        years = re.findall(r'20\d{2}', title)
        if years and str(played.astimezone(now.tzinfo).year) not in years:
            continue
        def mentions(name):
            surname = str(name or '').split()[-1:] or ['']
            surname = surname[0]
            # Shelton's spelling is present in the supplied September14 report.
            zh = _TENNIS_PLAYER_ZH.get(surname) or {'Shelton': '舒爾頓'}.get(surname)
            return bool(surname and (re.search(r'(?<![A-Za-z])' + re.escape(surname)
                        + r'(?![A-Za-z])', title, re.I) or (zh and zh in title)))
        if mentions(result.get('winner')) and mentions(result.get('loser')):
            return True
    return False
