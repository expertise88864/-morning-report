"""寄出前把簡體字換成繁體;繁簡共用字、標籤屬性與網址一律不動。

使用者 2026-10-16/19:信裡不得有簡體字(10/19 實信「資金撤离」)。opencc 的
s2t/s2tw 會把「台」改「臺」、「發布」改「發佈」,s2twp 甚至把「發布會」改成
「釋出會」—— 整封信直接轉會改壞正確的繁體。所以只標記兩種「明確簡體」:
不在 cp950(Big5)字集的字,以及 Big5 次常用區裡 s2tw 會改寫的字(厂、价、网…,
PR #22 外審;合法繁體/常見姓氏除外)。緊鄰明確簡體的共用字視為同一個簡體詞
(后续→後續、听说→聽說),台/准不動。換成什麼取自 s2tw 的詞組結果,只採用被
標記的位置。HTML 標籤內(含 href)與文字中的網址不轉,連結目的地不變。
"""
from __future__ import annotations

import re
import sys

#: Big5 次常用區(C940–F9D5)內、s2tw 會改寫的字,扣掉合法繁體與常見姓氏
#: (涂 姓氏、拮据、瀟洒、人蔘、覈實、鍼灸、跖骨)。
BIG5_SIMPLIFIED = frozenset(
    "厂万与气圣宁优价庄异忏机网体吨怀扰沄皁岭怜构极泞炖肮虮厘复昵柜洼洁瓮种胜苹党"
    "凄栖茧蚕蚝赶适惊淀痒离棱确羡腊痹蒏触熏睾蜡踊蔂蝎篱膻齶裏恒嫺")
#: 台灣正體慣用、opencc 卻會改寫的共用字:即使緊鄰簡體字也保留。
KEEP = frozenset("台准")
_CJK = re.compile(r"[㐀-鿿]")
_PROTECTED = re.compile(r"(<[^>]*>|https?://[^\s<>\"']+)")
_CC = None


def _simplified_only(ch: str) -> bool:
    if ch in BIG5_SIMPLIFIED:
        return True
    try:
        ch.encode("cp950")
    except UnicodeEncodeError:
        return True
    return False


def _convert(text: str) -> str:
    flagged = {i for i, ch in enumerate(text) if _CJK.match(ch) and _simplified_only(ch)}
    if not flagged:
        return text
    whole = _CC.convert(text)
    if len(whole) != len(text):              # 詞組轉換改了長度:退回逐字,仍只動標記字
        return "".join(_CC.convert(ch) if i in flagged else ch for i, ch in enumerate(text))
    near = {j for i in flagged for j in (i - 1, i + 1)} - flagged
    take = flagged | {j for j in near if 0 <= j < len(text) and text[j] not in KEEP}
    return "".join(whole[i] if i in take else ch for i, ch in enumerate(text))


def simplified_free(text):
    """回傳只把簡體字換掉的同一段文字;opencc 不可用時原樣返回(晨報不可斷)。"""
    if not isinstance(text, str) or not any(map(_simplified_only, _CJK.findall(text))):
        return text
    global _CC
    try:
        if _CC is None:
            from opencc import OpenCC
            _CC = OpenCC("s2tw")
        return "".join(part if _PROTECTED.fullmatch(part) else _convert(part)
                       for part in _PROTECTED.split(text))
    except Exception as exc:                 # noqa: BLE001 - 轉換失敗不可擋寄信
        print(f"[traditional_guard] 簡轉繁略過({type(exc).__name__})", file=sys.stderr)
        return text
