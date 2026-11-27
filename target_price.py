"""目標價金額在原文的位置(11-27 實信:日月光 908 元寫成 2132 元,卻被行情裡 2330 預測 2132.35 背書;外審 PR #70)。

目標價只能由新聞佐證;以**位置**判定,同一條裡同值的昨收、漲幅照原規則核對。
"""
import re

#: 已插入的「（未核實）」(legacy_evidence.MARK)要跳過:同一條標第二處時,前一處的標記不能截斷辨識(外審 PR #70)
_BEFORE = re.compile(r"(\d[\d,.]*\s*(?:美元|元))(?=(?:（未核實）)?\s*的?目標價)")
_LINK = re.compile(r"(?:（未核實）|[\s:：由從自至到為達調高低降上下修升原先前的較]){0,9}?(\d[\d,.]*\s*(?:美元|元))")


def spans(text: str) -> list[tuple[int, int]]:
    """緊接在「目標價」前的金額,與接在它後面的一到兩個金額(「由 A 調高至 B」「較原先的 A 調高至 B」「：A」)。"""
    out = [m.span(1) for m in _BEFORE.finditer(text)]
    for m in re.finditer("目標價", text):
        at = m.end()
        for _ in range(2):
            n = _LINK.match(text, at)
            if not n:
                break
            out.append(n.span(1))
            at = n.end()
    return out


def split(text: str, quantities) -> tuple[str, list[tuple]]:
    """(遮掉目標價的文字, 全部數字;第 6 欄為真表示是目標價)。"""
    sp = spans(text)
    plain = "".join(" " if any(a <= i < b for a, b in sp) else c for i, c in enumerate(text))
    return plain, ([q + (False,) for q in quantities(plain)]
                   + [q + (True,) for a, b in sp for q in quantities(text[a:b])])


def pick(hits: list, block: str, target: bool):
    """同一寫法出現多次時要標哪一處:目標價標在目標價的位置,其他數字標不在那裡的一處;都沒有就第一處。"""
    sp = spans(block)
    inside = [m for m in hits if any(a <= m.start() < b for a, b in sp)]
    return next(iter(inside if target else [m for m in hits if m not in inside]), None) or next(iter(hits), None)
