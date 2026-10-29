"""Conservative feed relevance and duplicate filtering, with no model calls."""
import re
import unicodedata
from difflib import SequenceMatcher


#: 個人討論貼文不是新聞(2026-10-28:「2891 中信金 - 富邦現金股利… - 股市爆料同學會 - CMoney」成了中信金新聞卡)。
_FORUM = re.compile(r"股市爆料同學會|[-－–]\s*(?:PTT|批踢踢|Dcard|Mobile01)[^-－–]*$", re.I)


def forum_post(title: str) -> bool:
    return bool(_FORUM.search(str(title or "")))


def without_forum_posts(news: list | None, today=None) -> list:
    """討論區貼文、與標題日期已過期的舊聞都不進分析(兩條生成路徑共用的入口)。"""
    return [n for n in news or [] if not (isinstance(n, dict) and (
        forum_post(n.get("title")) or (today is not None and stale_title_date(n.get("title"), today))))]


#: 標題自己寫的日期早於報告日這麼多天,就是被重新收錄的舊聞(週五→週一是 3 天,留餘裕)。
STALE_TITLE_DAYS = 5
_TITLE_DAY = re.compile(r"(?:(?<!\d)(\d{1,2})月|(?<![\d/]))(\d{1,2})日(?!均|線|內|來|以來|新高|新低|起|前|後|K)")
_FUTURE = re.compile(r"將|預計|預定|訂於|即將|起跑|登場")


def stale_title_date(title, today) -> bool:
    """「薔琵颱風24日颱風假資訊…」(10-29 收到、RSS 發布時間是 10-28):標題日期 5 天前 → 舊聞。"""
    import datetime as dt
    text = str(title or "")
    m = _TITLE_DAY.search(text)
    if not m or _FUTURE.search(text) or re.search(r"[近連前後第每逾約]\s*\d{1,2}日", text):
        return False
    try:   # 只認同年內「已經過去」的日期;比今天晚的一律當作還沒發生(「聯發科30日法說」)
        event = dt.date(today.year, int(m[1] or today.month), int(m[2]))
    except ValueError:
        return False
    # 寫了月份但遠在兩個月前:可能是明年的公告(「1月3日開始交易」),不猜
    return STALE_TITLE_DAYS <= (today - event).days <= 60


def relevant(label: str, title: str) -> bool:
    # A stock discussion repost is not local reporting, even with a ticker prefix.
    if forum_post(title):
        return False
    if re.match(r"^\s*討論牆\s*[|｜]", title):
        return False
    if label == "建商動態" and re.search(r"[【\[].*分享[】\]]", title):
        return False
    if label in {"建商動態", "房市", "建設"} and re.search(r"裝修案例|裝修.*效果圖|室內設計.*案例", title):
        return False
    required = {
        "醫界追蹤": r"醫院|醫療|急診|醫師|護理|衛福|健保|病患|病床|等床",
        "學區/文教": r"學校|學區|中學|高中|國中|國小|教育|招生|入學|校園|明道|葳格",
    }
    if label in required and not re.search(required[label], title):
        return False
    if label == "建設" and re.search(r"選戰|造勢|競選|後援會", title):
        return False
    if label == "產業/科技" and not re.search(
            r"產業|科技|半導體|晶圓|園區|中科|工業|企業|廠|投資|招商|製造|研發|供應鏈|產線|(?<![A-Za-z])AI(?![A-Za-z])",
            title, re.I):
        return False
    return True


def title_key(title: str) -> str:
    # Strip RSS publisher suffix only; retain all words, numbers and negations.
    title = re.split(r"\s+-\s+", title)[0]
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", title))


def unique(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for row in rows:
        # A discussion wrapper is not a second independent news source.
        if re.match(r"^\s*討論牆\s*[|｜]", str(row.get("title") or "")):
            continue
        key = title_key(str(row.get("title") or ""))
        if key and not any(duplicate(key, old) for old in seen):
            out.append(row)
            seen.add(key)
    return out


def duplicate(left: str, right: str) -> bool:
    if left == right:
        return True
    # Near matches require a shared distinctive quotation and stable qualifiers.
    quotes = set(re.findall(r"「([^」]{5,})」", left))
    if not quotes.intersection(re.findall(r"「([^」]{5,})」", right)):
        return False
    guard = r"\d+(?:\.\d+)?|不|未|無|否認|駁斥|澄清|傳出|證實|擬|可能|確定"
    return (re.findall(guard, left) == re.findall(guard, right)
            and SequenceMatcher(None, left, right, autojunk=False).ratio() >= .88)
