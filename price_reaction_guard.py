"""Neutralize unsupported price-as-causal-response wording in reader prose.

A price move is an observation, not evidence of what caused it. Keep the move
and its adjacent source citation while removing only an explicit causal verb.
"""

import re

from reader_price_causality_guard import _QUOTE, _RENDERED_HEADLINE, _SOURCE_HEADLINE


_PRICE_RESPONSE = re.compile(
    r"以(?P<move>漲停放量|放量漲停|漲停|跌停|大漲|重挫)回應"
    r"(?=[ \t]*(?:[（(\[，。；;]|$))"
)
_SOURCE_SPAN = re.compile(rf"(?:{_QUOTE.pattern})|\[[^\]\n]+\]\(https?://[^)\n]+\)", re.M)


def neutralize(text: str, manifest: dict) -> str:
    """Preserve price facts; avoid claiming a cause absent transaction evidence."""
    if not isinstance(text, str) or not text:
        return text

    def _correct(prose: str) -> tuple[str, int]:
        return _PRICE_RESPONSE.subn(
            lambda match: f"出現{match.group('move')}，惟無法僅憑股價確認原因",
            prose,
        )

    revised_lines: list[str] = []
    count = 0
    for line in text.splitlines(keepends=True):
        body = line.lstrip(" >\t")
        if _SOURCE_HEADLINE.match(body) or _RENDERED_HEADLINE.match(body):
            revised_lines.append(line)
            continue
        parts: list[str] = []
        start = 0
        for source in _SOURCE_SPAN.finditer(line):
            prose, found = _correct(line[start:source.start()])
            parts.extend((prose, source.group(0)))
            count += found
            start = source.end()
        prose, found = _correct(line[start:])
        parts.append(prose)
        count += found
        revised_lines.append("".join(parts))
    revised = "".join(revised_lines)
    if count:
        manifest.setdefault("llm", {})["price_reaction_claims_neutralized"] = count
    return revised
