"""來源連結移到每一段的最後面(使用者 2026-11-12:「鑲嵌在內文裡面會干擾閱讀」)。

備援分析把 `[媒體][N12]` 換成 `[媒體](網址)` 之後,連結散在句子中間。這裡逐段處理:
把該段所有來源連結抽出來,依出現順序接在段尾(同一個網址只留一次),內文不留痕跡。
一段 = 連續的非空行;清單項目與引言每行各自成段。標題與表格不動;連結的文字與網址一字不改。純函式。
"""
from __future__ import annotations

import re

_LINK = re.compile(r"[ \t　]*\[([^\[\]\n]{1,24})\]\((https?://[^)\s]+)\)")
_SKIP = re.compile(r"^\s*(?:#{1,6}\s|\|)")
_STARTS = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|>)")


def _move(lines: list[str]) -> list[str]:
    """一段(一行或多行)的連結移到最後一行行尾。"""
    links = [pair for line in lines for pair in _LINK.findall(line)]
    if not links:
        return lines
    first = {}
    for label, url in links:
        first.setdefault(url, label)   # 同一網址留第一次出現的名稱
    body = [re.sub(r"[ \t]+(?=[，。、；：,.;:！？!?）)])", "", _LINK.sub("", line)).rstrip() for line in lines]
    body = [line for line in body if line.strip()] or [""]   # 整行只有連結的行併掉
    body[-1] += "　" + " ".join(f"[{label}]({url})" for url, label in first.items())
    return body


def last(text: str) -> str:
    """每一段的來源連結移到段尾;沒有連結的段落原樣保留。"""
    if not isinstance(text, str) or "](" not in text:
        return text
    out, paragraph = [], []
    for line in text.split("\n"):
        if not line.strip() or _SKIP.match(line):
            out += _move(paragraph) + [line]
            paragraph = []
        elif _STARTS.match(line):
            out += _move(paragraph)
            paragraph = [line]
        else:
            paragraph.append(line)
    return "\n".join(out + _move(paragraph))
