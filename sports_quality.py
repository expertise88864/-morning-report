"""Source-status and numeric display helpers; no result inference or network."""
import re


def mlb_summary(stat: dict, group: str) -> str:
    fields = (('inningsPitched', 'IP'), ('earnedRuns', 'ER'), ('strikeOuts', 'K'), ('baseOnBalls', 'BB'), ('era', 'ERA'))
    if group == 'hitting':
        fields = (('hits', 'H'), ('atBats', 'AB'), ('homeRuns', 'HR'), ('rbi', 'RBI'),
                  ('baseOnBalls', 'BB'), ('strikeOuts', 'K'), ('runs', 'R'))
    parts = [f'{stat[k]} {label}' for k, label in fields if stat.get(k) is not None]
    return ', '.join(parts) if parts else str(stat.get('summary') or '')


def tennis_note(comp: dict) -> str:
    status = (comp.get('status') or {}).get('type') or {}
    text = ' '.join(str(status.get(k) or '') for k in ('name', 'description', 'detail', 'shortDetail'))
    if re.search(r'retir', text, re.I):
        return '退賽結束'
    if re.search(r'walkover', text, re.I):
        return '不戰而勝'
    if re.search(r'default|disqualif', text, re.I):
        return '判決勝負'
    players = comp.get('competitors') or []
    if len(players) != 2:
        return ''
    left, right = [p.get('linescores') or [] for p in players]
    if not left and not right:
        return ''
    if len(left) != len(right):
        return '比分資料不完整'
    try:
        for a, b in zip(left, right):
            hi, lo = sorted((int(a['value']), int(b['value'])), reverse=True)
            if not (hi >= 6 and hi - lo >= 2 or hi == 7 and lo == 6):
                return '非完整盤數，結束方式待確認'
    except (ValueError, TypeError, KeyError):
        return '比分資料不完整'
    return ''


def news_allowed(entry: dict) -> bool:
    title = str(entry.get('title') or '')
    # Promotional programme schedules are not result/news reports, even if freshly reposted.
    return not re.search(r'轉播預告|轉播表|直播連結|無廣告.*體育|免費直播', title)


def tennis_score(win: dict, lose: dict) -> str:
    """Same provider set scores, with explicit finish status handled separately."""
    try:
        wls, lls = win.get('linescores') or [], lose.get('linescores') or []
        if not wls or len(wls) != len(lls):
            return ''
        sets = []
        for a, b in zip(wls, lls):
            av, bv = int(a['value']), int(b['value'])
            segment = f'{av}-{bv}'
            tb = b if av > bv else a
            if tb.get('tiebreak') is not None:
                segment += f"({int(tb['tiebreak'])})"
            sets.append(segment)
        return ' '.join(sets)
    except (ValueError, TypeError, KeyError):
        return ''
