"""Move repeated noncritical inline email styling into a final head stylesheet.

Inline type, line spacing, numeric alignment and contrast-sensitive colors stay
available when a mail client strips the head. The pass is optional and never
removes reader content or a source link.
"""
from __future__ import annotations

from collections import defaultdict
from html.parser import HTMLParser
import re


_NAME = re.compile(r"<[A-Za-z][A-Za-z0-9:-]*")
_DARK_INK = {"#94a3b8", "#334155", "#0369a1", "#0f172a", "#64748b", "#475569"}
_KEEP = {"font-size", "line-height", "text-align", "display", "visibility",
         "opacity", "white-space", "height", "max-height", "overflow",
         "width", "vertical-align"}
_AttrSpan = tuple[int, int, int, int, str]


def _attrs(tag: str) -> dict[str, _AttrSpan] | None:
    """Return raw attribute/value spans, declining malformed or duplicate tags."""
    head = _NAME.match(tag)
    if head is None:
        return None
    end = len(tag) - 1
    pos = head.end()
    result = {}
    while pos < end:
        space = pos
        while pos < end and tag[pos].isspace():
            pos += 1
        if pos >= end or tag[pos] == "/" and pos + 1 == end:
            break
        start = pos
        while pos < end and not tag[pos].isspace() and tag[pos] not in "=/>":
            pos += 1
        if pos == start:
            return None
        name = tag[start:pos].lower()
        if name in result:
            return None
        while pos < end and tag[pos].isspace():
            pos += 1
        if pos >= end or tag[pos] != "=":
            result[name] = (space, pos, pos, pos, "")
            continue
        pos += 1
        while pos < end and tag[pos].isspace():
            pos += 1
        if pos >= end:
            return None
        quote = tag[pos]
        if quote in "\"'":
            value_start = pos + 1
            value_end = tag.find(quote, value_start)
            if value_end < 0 or value_end >= end:
                return None
            pos = value_end + 1
        else:
            value_start = pos
            while pos < end and not tag[pos].isspace() and tag[pos] != ">":
                pos += 1
            value_end = pos
            quote = ""
        result[name] = (space, pos, value_start, value_end, quote)
    return result


def _split_style(value: str, *, protected_table: bool) -> tuple[str, str]:
    # Skip syntax whose declaration boundaries are not unambiguous.
    if protected_table:
        return value, ""
    if any(x in value.lower() for x in ("url(", "content:", "var(", "!important")):
        return value, ""
    if any(x in value for x in ("&", "\\", "\"", "'")):
        return value, ""
    keep: list[str] = []
    move: list[str] = []
    seen: set[str] = set()
    paired_background = bool(re.search(r"(?:^|;)\s*background(?:-color)?\s*:",
                                       value, re.I))
    light_ink = bool(re.search(r"(?:^|;)\s*color\s*:\s*(?:#fff|#ffffff|white)(?:;|$)",
                               value, re.I))
    for part in value.split(";"):
        if not part.strip():
            continue
        if ":" not in part:
            return value, ""
        name, _, raw = part.partition(":")
        name = name.strip().lower()
        if name in seen:
            return value, ""
        seen.add(name)
        color = raw.strip().lower()
        required = (name in _KEEP or (name == "color" and
                    (color not in _DARK_INK or paired_background))
                    or (light_ink and name.startswith("background")))
        (keep if required else move).append(part.strip() + ";")
    return "".join(keep), "".join(move)


class _Collector(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=False)
        self.html = html
        self.lines = [0]
        for match in re.finditer("\n", html):
            self.lines.append(match.end())
        self.protected_tables: list[bool] = []
        self.groups: dict[str, list[tuple[int, str, dict[str, _AttrSpan], str]]] = (
            defaultdict(list))
        self.class_names: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = self.get_starttag_text()
        if raw is None:
            return
        offset = self.lines[self.getpos()[0] - 1] + self.getpos()[1]
        if self.html[offset:offset + len(raw)] != raw:
            return
        parsed = _attrs(raw)
        values = dict(attrs)
        protected = bool(self.protected_tables and self.protected_tables[-1])
        if tag == "table":
            protected = protected or values.get("data-mobile-layout") == "table"
            self.protected_tables.append(protected)
        if parsed is None:
            return
        class_value = values.get("class")
        if class_value:
            self.class_names.update(class_value.split())
        style, cls = parsed.get("style"), parsed.get("class")
        if style is None or not style[4] or cls is not None and not cls[4]:
            return
        raw_style = raw[style[2]:style[3]]
        if values.get("style") != raw_style:
            return
        retained, moved = _split_style(raw_style, protected_table=protected)
        if moved:
            self.groups[moved].append((offset, raw, parsed, retained))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag == "table" and self.protected_tables:
            self.protected_tables.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.protected_tables:
            self.protected_tables.pop()


def _rewrite(raw: str, attrs: dict[str, _AttrSpan], kept: str, name: str) -> str:
    style = attrs["style"]
    changes = [(style[0], style[1],
                f' style={style[4]}{kept}{style[4]}' if kept else "")]
    cls = attrs.get("class")
    if cls:
        changes.append((cls[3], cls[3], " " + name))
    else:
        end = len(raw) - (2 if raw.endswith("/>") else 1)
        changes.append((end, end, f' class="{name}"'))
    for start, end, value in sorted(changes, reverse=True):
        raw = raw[:start] + value + raw[end:]
    return raw


def compact(html: str) -> str:
    """Use a class only when exact UTF-8 savings beat its rule and wrapper."""
    if not html or not re.search(r"</head\s*>", html, re.I):
        return html
    parser = _Collector(html)
    parser.feed(html)
    parser.class_names.update(re.findall(r"\.([zZ]\d+)\b", html))
    replacements = []
    rules = []
    index = 0
    for moved, nodes in sorted(parser.groups.items(), key=lambda item: (-len(item[1]), item[0])):
        while f"z{index}" in parser.class_names:
            index += 1
        name = f"z{index}"
        changed = [(offset, raw, _rewrite(raw, attrs, kept, name))
                   for offset, raw, attrs, kept in nodes]
        rule = f".{name}{{{moved}}}"
        gain = sum(len(raw.encode()) - len(new.encode()) for _, raw, new in changed)
        if gain <= len(rule.encode()):
            continue
        replacements.extend(changed)
        rules.append(rule)
        index += 1
    if not rules:
        return html
    out = html
    for offset, raw, new in sorted(replacements, reverse=True):
        out = out[:offset] + new + out[offset + len(raw):]
    out = re.sub(r"</head\s*>", lambda m: "<style>" + "".join(rules) + "</style>" + m.group(),
                 out, count=1, flags=re.I)
    return out if len(out.encode()) < len(html.encode()) else html
