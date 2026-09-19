"""Keep date-only calendar entries visible without inventing an event time."""
import datetime as dt
import re

from scenario_window import clock, event_clock


def nearby_undated_times(rows, now):
    """Potentially overlaps the next 48h; never asserts exact-window membership."""
    start = clock(now)
    if start is None:
        return []
    last_day = (start + dt.timedelta(hours=48)).date()
    out = []
    for row in rows or []:
        if not isinstance(row, dict) or event_clock(row) is not None:
            continue
        if re.fullmatch(r'\d{1,2}:\d{2}', str(row.get('time') or '').strip()):
            continue  # A malformed clock is not a date-only calendar entry.
        try:
            value = row.get('date')
            day = value.date() if isinstance(value, dt.datetime) else dt.date.fromisoformat(str(value))
        except (TypeError, ValueError):
            continue
        if start.date() <= day <= last_day and str(row.get('title') or '').strip():
            out.append(row)
    return out


def format_uncertain(rows, now, annotate):
    selected = nearby_undated_times(rows, now)
    if not selected:
        return ''
    heading = '\n日期已知、時間待確認（未確認是否落在未來 48 小時，不得自行補時刻）：'
    lines = []
    for row in selected[:3]:
        hint = str(row.get('time') or '').strip()
        note = str(row.get('note') or '').strip()
        lines.append(f"- {str(row['date'])[:10]}｜{annotate(str(row['title']))}（時間待確認）"
                     + (f'〔原始時間註記：{annotate(hint)}〕' if hint else '')
                     + (f'〔{annotate(note)}〕' if note else ''))
    if len(selected) > 3:
        lines.append(f'另有 {len(selected) - 3} 項時間待確認，詳見完整日曆。')
    return heading + '\n' + '\n'.join(lines)
