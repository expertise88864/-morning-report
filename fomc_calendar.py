"""Convert official US meeting-end dates to actual Taipei decision timestamps."""
import datetime as dt
from zoneinfo import ZoneInfo


def events(meetings, start: dt.date, end: dt.date) -> list[dict]:
    result = []
    for meeting in meetings:
        local = dt.datetime.combine(meeting, dt.time(14), ZoneInfo('America/New_York'))
        taipei = local.astimezone(ZoneInfo('Asia/Taipei'))
        if start <= taipei.date() <= end:
            result.append({'date': taipei.date(), 'time': taipei.strftime('%H:%M'),
                           'title': 'FOMC 利率決策（台北時間）',
                           'note': '決策日前後美股波動放大', 'impact': 'high'})
    return result
