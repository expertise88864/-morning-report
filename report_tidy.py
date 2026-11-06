"""模型照不照規則不一定,這幾件事由 Python 收尾(2026-11-06 使用者:「全部做」)。

寫稿規則寫了,11-06 實信仍有:金融與生技的小消息、一行孤立的「這條與第 1 條…」、
大跌日的台灣本地動態第一條不是大盤。純文字處理,不做網路呼叫;呼叫端 try,失敗回原文。
"""
from __future__ import annotations

import re

_HEADING = re.compile(r"(?m)^(?=#{1,3}\s*[一二三四五六七八九十]+(?:之[一二三四五六七八九十]+)?、)")
_FIN_BIO = re.compile(r"^\s*(?:[-*]\s+)?\**【(?:金融|生技)[｜|]")
_WEAK = re.compile(r"\[B 級|信心[:：]\s*低(?![-－~～])")
_SKIN_EYE = re.compile(r"皮膚|眼科|異位性|乾癬|痤瘡|圓禿|白斑|黃斑|青光眼|近視|乾眼|白內障")
_ITEM = re.compile(r"^\s*(?:[-*]\s+)?\*\*|\[N\d{1,4}\]|\[[AB] 級")
#: 要真的講指數漲跌與幅度:「重點關注台股資金外流」的「點」不算(外審 PR #44)。
_INDEX_MOVE = re.compile(r"(?:台股|加權|大盤)[^。\n]{0,40}?(?:跌|漲|挫|收|回落|反彈)[^。\n]{0,12}?\d[\d,.]*\s*(?:點|%)")


def _paragraphs(body: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", body.strip("\n")) if p.strip()]


def _merge_orphans(paragraphs: list[str]) -> list[str]:
    """沒有標題、來源或分級的短句是上一條的補充,併回去。第一段可能是段落引言,不動。"""
    out: list[str] = []
    for para in paragraphs:
        if out and len(para.strip()) <= 80 and not _ITEM.search(para):
            out[-1] = out[-1].rstrip() + " " + para.strip()
        else:
            out.append(para)
    return out


def _drop_minor(paragraphs: list[str]) -> list[str]:
    """金融與生技只留有料的:B 級或低信心就是模型自己承認沒有具體事實。皮膚科、眼科例外(使用者優先看)。"""
    kept = [p for p in paragraphs
            if not (_FIN_BIO.search(p) and _WEAK.search(p) and not _SKIN_EYE.search(p))]
    if len(kept) != len(paragraphs):
        print(f"[report-tidy] 金融/生技小消息略過 {len(paragraphs) - len(kept)} 則", flush=True)
    return kept


def _market_first(paragraphs: list[str]) -> list[str]:
    for i, para in enumerate(paragraphs):
        if _INDEX_MOVE.search(para):
            return [para] + paragraphs[:i] + paragraphs[i + 1:]
    return paragraphs


def tidy(text: str, *, big_move: bool = False) -> str:
    """只動七段以外的 八、九、十;其餘段落原樣保留。"""
    out = []
    for section in _HEADING.split(str(text or "")):
        head, newline, body = section.partition("\n")
        title = re.sub(r"^#{1,3}\s*", "", head)
        paragraphs = _paragraphs(body)
        if title.startswith("八、"):
            paragraphs = _merge_orphans(paragraphs)
        elif title.startswith("九、"):
            paragraphs = _drop_minor(_merge_orphans(paragraphs))
        elif title.startswith("十、") and big_move:
            paragraphs = _market_first(paragraphs)
        else:
            out.append(section)
            continue
        out.append(head + newline + "\n" + "\n\n".join(paragraphs) + "\n\n" if paragraphs else head + "\n\n")
    return "".join(out)


def safe(text: str, quotes: dict | None) -> str:
    """呼叫端用這個:判斷是不是大波動日,失敗就回原文(晨報不可斷)。"""
    import report_polish
    quotes = quotes or {}
    try:
        big = bool(report_polish.big_move_rule(quotes.get("TAIEX_SESSION_PCT"), quotes.get("BREADTH")))
        return tidy(text, big_move=big)
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::report_tidy_failed:{type(exc).__name__}", flush=True)
        return text
