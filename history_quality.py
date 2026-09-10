"""Conservative research-only exclusions; archives and event scores are untouched."""
import re


def eligible(row: dict) -> bool:
    title = re.split(r'\s+-\s+', str(row.get('title') or ''))[0].strip()
    # Static quote/forum landing pages, not every report from the same publisher.
    if re.search(r'股票股價[,，].*即時行情.*走勢圖|討論區\s*-\s*股票評論\s*-\s*股吧', title):
        return False
    return not bool(re.fullmatch(r'\$[^$]{1,100}\([A-Z0-9.]+\)\$', title))


def revenue_periods_match(left: dict, right: dict) -> bool:
    def period(row):
        title = str(row.get('title') or '')
        found = re.search(r'(?:(20\d{2}|1\d{2})\s*年\s*)?(?<!\d)(1[0-2]|0?[1-9])\s*月(?:份)?\s*(?:自結)?(?:合併)?營收', title)
        if not found:
            return None
        year = int(found.group(1)) if found.group(1) else None
        return (year + 1911 if year and year < 200 else year, int(found.group(2)))
    a, b = period(left), period(right)
    return not (a and b and (a[1] != b[1] or a[0] and b[0] and a[0] != b[0]))
