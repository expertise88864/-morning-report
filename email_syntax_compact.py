"""Conservative final-pass email HTML syntax compaction.

No visible text, source URL, typography declaration, or table node is removed.
The generated short classes remain CSS-equivalent if quoted or unquoted.
"""
from __future__ import annotations

import re


_PROTECTED = re.compile(
    r"<!--.*?-->|<(?P<tag>pre|textarea|script|style)\b[^>]*>.*?</(?P=tag)\s*>",
    re.I | re.S,
)
_TAG = re.compile(
    r"</?[A-Za-z][A-Za-z0-9:-]*(?:\"[^\"]*\"|'[^']*'|[^'\">])*>"
)
_TAG_NAME = re.compile(r"<[A-Za-z][A-Za-z0-9:-]*")
_TABLE_TAG = re.compile(r"</?(?:table|thead|tbody|tr|td|th)\b", re.I)
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9_-]+\Z")
_SAFE_STYLE_TOKEN = re.compile(r"[^\s\"'=<>`&]+\Z")
_UNQUOTE_NAMES = {"class", "target", "role", "data-mobile-layout", "lang", "charset", "align"}
_ENTITY_END = re.compile(r"&(?:#[0-9]+|#x[0-9a-f]+|[a-z][a-z0-9]+);\Z", re.I)
_EDITOR_COMMENTS = re.compile(
    r"<!--\s*(?:HERO|KPI STRIP|TODAY'S TAKEAWAY|BODY\(|FOOTER:)[^>]*-->", re.I,
)


def _compact_start_tag(tag: str) -> str:
    """Edit actual quoted attributes, never syntax-looking text in their values."""
    start = _TAG_NAME.match(tag)
    if start is None:
        return tag
    end = len(tag) - 1
    pos = start.end()
    replacements: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    while pos < end:
        while pos < end and tag[pos].isspace():
            pos += 1
        if pos >= end:
            break
        if tag[pos] == "/" and pos + 1 == end:
            break
        name_start = pos
        while pos < end and not tag[pos].isspace() and tag[pos] not in "=/>:":
            pos += 1
        # A colon is valid in an attribute name (e.g. xmlns:x); accept it.
        while pos < end and tag[pos] == ":":
            pos += 1
            while pos < end and not tag[pos].isspace() and tag[pos] not in "=/>":
                pos += 1
        if name_start == pos:
            return tag
        name = tag[name_start:pos].lower()
        while pos < end and tag[pos].isspace():
            pos += 1
        if pos >= end or tag[pos] != "=":
            continue
        pos += 1
        while pos < end and tag[pos].isspace():
            pos += 1
        if pos >= end:
            return tag
        quote = tag[pos]
        if quote in "\"'":
            open_quote = pos
            value_start = pos + 1
            close_quote = tag.find(quote, value_start)
            if close_quote < 0 or close_quote >= end:
                return tag
            value = tag[value_start:close_quote]
            pos = close_quote + 1
            if name in seen and name in {"class", "style"}:
                return tag
            seen.add(name)
            if name in _UNQUOTE_NAMES and quote == '"' and _SAFE_TOKEN.fullmatch(value):
                replacements.append((open_quote, pos, value))
            elif name == "style":
                shortened = (value[:-1] if value.endswith(";")
                             and not _ENTITY_END.search(value) else value)
                if quote == '"' and _SAFE_STYLE_TOKEN.fullmatch(shortened):
                    replacements.append((open_quote, pos, shortened))
                elif shortened != value:
                    replacements.append((value_start, close_quote, shortened))
        else:
            while pos < end and not tag[pos].isspace() and tag[pos] != ">":
                pos += 1
    for start_at, end_at, value in reversed(replacements):
        tag = tag[:start_at] + value + tag[end_at:]
    return tag


def compact(html: str) -> str:
    """Remove optional syntax only, preserving protected content and text nodes."""
    if not html:
        return html
    html = _EDITOR_COMMENTS.sub("", html)

    def compact_segment(segment: str) -> str:
        pieces: list[str] = []
        cursor = 0
        previous_table = False
        for match in _TAG.finditer(segment):
            gap = segment[cursor:match.start()]
            current_table = _TABLE_TAG.match(match.group(0)) is not None
            pieces.append("" if gap.isspace() and previous_table and current_table else gap)
            pieces.append(_compact_start_tag(match.group(0)))
            cursor = match.end()
            previous_table = current_table
        pieces.append(segment[cursor:])
        return "".join(pieces)

    chunks = []
    cursor = 0
    for protected in _PROTECTED.finditer(html):
        chunks.append(compact_segment(html[cursor:protected.start()]))
        chunks.append(protected.group(0))
        cursor = protected.end()
    chunks.append(compact_segment(html[cursor:]))
    result = "".join(chunks)
    return result if len(result.encode("utf-8")) < len(html.encode("utf-8")) else html
