"""Conservative guards before fuzzy headline merging; no event inference."""
import re
import unicodedata

from headline_amounts import amount_markers


def _headline(title: str) -> str:
    return unicodedata.normalize('NFKC', re.split(r'\s+-\s+', title)[0]).strip()


def static_stock_page(title: str) -> bool:
    """Match navigation-page titles, not stories discussing such pages."""
    title = _headline(title)
    if re.search(r'新增|推出|改版|更新|發布|公布|公開|改善|揭露|介紹|教學|分析|打造|調整|上線|整合', title):
        return False
    return bool(re.fullmatch(
        r'[\w .&()\-]{1,45}\s*個股(?:概覽|總覽)'
        r'(?:\s*[|｜]\s*個股)?', title))


def merge_compatible(left: str, right: str) -> bool:
    """Different amounts, negations, or confirmation states are different news.

    This veto supplements similarity; it never establishes event identity.
    Preserve multiplicity/order rather than treating matching numbers as a set.
    """
    guard = (r'不|未|無|非|否認|駁斥|澄清|'
             r'傳出|傳聞|盛傳|確認|證實|擬|可能|確定|取消|延後|暫停|'
             r'\b(?:not|no|denies|denied|confirmed|reportedly|may|cancelled)\b')
    def markers(title):
        text = re.sub(r'\(\d{4,6}\)', '', _headline(title).casefold())
        return re.findall(guard, text), amount_markers(text)
    lwords, lnums = markers(left)
    rwords, rnums = markers(right)
    # One-sided detail (e.g. an ordinal/date) does not by itself contradict the
    # other headline. Different explicit quantities on both sides veto merging.
    amounts_agree = (not lnums or not rnums or (len(lnums) == len(rnums) and all(
        lv == rv and (lu == ru or (not lu and ru != '%') or (not ru and lu != '%'))
        for (lv, lu), (rv, ru) in zip(lnums, rnums))))
    return lwords == rwords and amounts_agree
