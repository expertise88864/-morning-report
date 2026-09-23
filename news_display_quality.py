"""Conservative feed relevance and duplicate filtering, with no model calls."""
import re
import unicodedata
from difflib import SequenceMatcher


def relevant(label: str, title: str) -> bool:
    # A stock discussion repost is not local reporting, even with a ticker prefix.
    if re.search(r"股市爆料同學會\s*[-－–]\s*CMoney\s*$", title, re.I):
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
