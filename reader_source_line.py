"""Protect literal citations without confusing a following actor's fact."""
import re
from urllib.parse import urlsplit

_HEAD = re.compile(r"^\s*\[[^\]\r\n]+\]\((https?://)", re.I)


def citation_prefix_end(text: str) -> int:
    if not isinstance(text, str) or not (head := _HEAD.match(text)):
        return 0
    depth, escaped = 1, False
    for at, char in enumerate(text[head.end():], head.end()):
        if char.isspace():
            return 0
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                try:
                    host = urlsplit(text[head.start(1):at]).hostname
                except ValueError:
                    return 0
                return at + 1 + len(text[at + 1:]) - len(text[at + 1:].lstrip()) if host else 0
    return 0


def is_source_link_line(text: str) -> bool:
    end = citation_prefix_end(text)
    return bool(end and not text[end:].strip())


def actor_prefix(text: str) -> str:
    """Normalize only the factual-actor matching copy, never emitted text."""
    end = citation_prefix_end(text)
    return text[end:].lstrip() if end else re.sub(r"^\s*\[[^\]\r\n]+\]\s*", "", text)
