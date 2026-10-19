"""寄出前把簡體專用字換成繁體;繁簡共用字一律不動。

使用者 2026-10-16/19:信裡不得有簡體字(10/19 實信「資金撤离」)。opencc 的
s2t/s2tw 會把「台」改「臺」、「發布」改「發佈」,s2twp 甚至把「發布會」改成
「釋出會」—— 整封信直接轉會改壞正確的繁體。所以只換**不在 cp950(Big5)字集**
的字,外加少數在 Big5 內、但現代繁中幾乎只當簡體出現的字;換成什麼取自整段
s2tw(台灣字形、詞組層級,例:头发→頭髮),但只採用被標記字的位置。
"""
from __future__ import annotations

import re
import sys

#: 在 Big5 內、但現代繁中幾乎只以簡體身分出現的字。「准」台灣常用(准許),不列。
BIG5_SIMPLIFIED = frozenset("离与价复万网")
#: 台灣正體慣用、opencc 卻會改寫的共用字:即使緊鄰簡體字也保留。
KEEP = frozenset("台准")
_CJK = re.compile(r"[㐀-鿿]")
_CC = None


def _simplified_only(ch: str) -> bool:
    if ch in BIG5_SIMPLIFIED:
        return True
    try:
        ch.encode("cp950")
    except UnicodeEncodeError:
        return True
    return False


def simplified_free(text):
    """回傳只把簡體專用字換掉的同一段文字;opencc 不可用時原樣返回(晨報不可斷)。"""
    if not isinstance(text, str):
        return text
    flagged = {i for i, ch in enumerate(text) if _CJK.match(ch) and _simplified_only(ch)}
    if not flagged:
        return text
    global _CC
    try:
        if _CC is None:
            from opencc import OpenCC
            _CC = OpenCC("s2tw")
        whole = _CC.convert(text)
        if len(whole) != len(text):          # 詞組轉換改了長度:退回逐字,仍只動標記字
            return "".join(_CC.convert(ch) if i in flagged else ch for i, ch in enumerate(text))
        # 緊鄰簡體字的共用字屬於同一個簡體詞(后续→後續、这里→這裡);台/准不動。
        near = {j for i in flagged for j in (i - 1, i + 1)} - flagged
        take = flagged | {j for j in near if 0 <= j < len(text) and text[j] not in KEEP}
        return "".join(whole[i] if i in take else ch for i, ch in enumerate(text))
    except Exception as exc:                 # noqa: BLE001 - 轉換失敗不可擋寄信
        print(f"[traditional_guard] 簡轉繁略過({type(exc).__name__})", file=sys.stderr)
        return text
