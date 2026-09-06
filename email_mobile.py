"""Progressive enhancement for narrow email readers; original content retained.

Gmail supports class selectors and width media queries. Do not rely on script,
pseudo-content, dark-mode overrides or horizontal scrolling. Only rectangular,
leaf data tables are stacked; nested layout and merged-cell tables stay intact.
Apply after inline-style compaction so existing classes are never duplicated.
"""
from html import escape
from html.parser import HTMLParser
import re

_CSS = """<style id="morning-mobile">
@media screen and (max-width:600px){
.mail-reading{width:100%!important;min-width:0!important;-webkit-text-size-adjust:100%;}
.mail-reading p,.mail-reading li{font-size:14px!important;line-height:1.7!important;word-wrap:break-word;}
.mail-reading h2{font-size:18px!important;line-height:1.45!important;}
.mail-reading h3{font-size:16px!important;line-height:1.45!important;}
.mail-reading a{word-wrap:break-word;}
.mail-data{width:100%!important;table-layout:fixed!important;}
.mail-data td,.mail-data th{font-size:14px!important;padding:8px 5px!important;white-space:normal!important;word-wrap:break-word;}
.mail-stack,.mail-stack tbody,.mail-stack thead,.mail-stack tr,.mail-stack td{display:block!important;width:auto!important;}
.mail-stack .mail-head{display:none!important;}
.mail-stack .mail-row{margin:0 0 12px!important;border:1px solid #cbd5e1;border-radius:6px;}
.mail-stack td{text-align:left!important;border-bottom:1px solid #e2e8f0!important;}
.mail-stack .mail-label{display:block!important;font-size:12px!important;line-height:1.5!important;font-weight:600;color:#475569;}
}
</style>"""
_CLASS = re.compile(r'\bclass\s*=\s*([\'"])(.*?)\1', re.I | re.S)


def _add_class(tag: str, names: str) -> str:
    match = _CLASS.search(tag)
    if match:
        return tag[:match.start(2)] + match[2] + " " + names + tag[match.end(2):]
    return tag[:-1] + ' class="' + names + '">'


class _Tables(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.offsets = [0]
        self.offsets.extend(m.end() for m in re.finditer("\n", html))
        self.stack: list[dict] = []
        self.tables: list[dict] = []
        self.body = None

    def _tag(self) -> dict:
        line, col = self.getpos()
        return {"offset": self.offsets[line - 1] + col, "raw": self.get_starttag_text()}

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.body = self._tag()
        if tag == "table":
            if self.stack:
                self.stack[-1]["nested"] = True
            table = dict(self._tag(), rows=[], nested=False, merged=False,
                         root=not self.stack,
                         preserve=dict(attrs).get("data-mobile-layout") == "table",
                         presentation=dict(attrs).get("role") == "presentation")
            self.tables.append(table)
            self.stack.append(table)
        elif self.stack and tag == "tr":
            self.stack[-1]["rows"].append(dict(self._tag(), cells=[]))
        elif self.stack and tag in ("td", "th") and self.stack[-1]["rows"]:
            if any(k in ("colspan", "rowspan") and v != "1" for k, v in attrs):
                self.stack[-1]["merged"] = True
            self.stack[-1]["rows"][-1]["cells"].append(
                dict(self._tag(), kind=tag, text=[], open=True))

    def handle_endtag(self, tag):
        if tag == "table" and self.stack:
            self.stack.pop()
        elif tag in ("td", "th") and self.stack and self.stack[-1]["rows"]:
            cells = self.stack[-1]["rows"][-1]["cells"]
            if cells:
                cells[-1]["open"] = False

    def handle_data(self, data):
        if self.stack and self.stack[-1]["rows"]:
            cells = self.stack[-1]["rows"][-1]["cells"]
            if cells and cells[-1]["open"]:
                cells[-1]["text"].append(data)


def enhance(html: str) -> str:
    """Pure/idempotent: no network, removed rows, rewritten URLs or scripts."""
    if "</head>" not in html or 'id="morning-mobile"' in html:
        return html
    parser = _Tables(html)
    parser.feed(html)
    edits = []

    def decorate(node, classes, suffix=""):
        edits.append((node["offset"], len(node["raw"]),
                      _add_class(node["raw"], classes) + suffix))

    if parser.body:
        decorate(parser.body, "mail-reading")
    for table in parser.tables:
        rows = table["rows"]
        # Email clients may discard BODY attributes. Anchor the reading rules
        # to the actual outer presentation table, without resizing inner cards.
        if table["root"] and table["presentation"]:
            decorate(table, "mail-reading")
        if table["nested"] or table["presentation"] or table["preserve"] or not rows:
            continue
        header = rows[0]["cells"]
        # Only genuine data headers; never guess what layout cells mean.
        if not header or any(c["kind"] != "th" for c in header):
            continue
        labels = [" ".join("".join(c["text"]).split()) for c in header]
        stack = (len(header) >= 4 and len(rows) > 1 and all(labels)
                 and not table["merged"]
                 and all(len(r["cells"]) == len(header) and
                         all(c["kind"] == "td" for c in r["cells"])
                         for r in rows[1:]))
        decorate(table, "mail-data" + (" mail-stack" if stack else ""))
        if not stack:
            continue
        decorate(rows[0], "mail-head")
        for row in rows[1:]:
            decorate(row, "mail-row")
            for cell, label in zip(row["cells"], labels):
                suffix = ('<span class="mail-label" style="display:none">'
                          + escape(label) + "</span>")
                decorate(cell, "mail-cell", suffix)
    for offset, size, value in sorted(edits, reverse=True):
        html = html[:offset] + value + html[offset + size:]
    return html.replace("</head>", _CSS + "</head>", 1)
