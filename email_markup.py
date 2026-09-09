"""Remove formatting-only whitespace; never shorten content or change styles."""
import re


def compact(html: str) -> str:
    protected = re.split(r"(<(?:pre|textarea|script|style)\b[^>]*>.*?</(?:pre|textarea|script|style)\s*>)",
                         html, flags=re.I | re.S)
    for i in range(0, len(protected), 2):
        protected[i] = re.sub(
            r"(</(?:tr|table|div|p|ul|ol|li|h[1-6])>)[ \t\r\n]+(?=<(?:tr|table|div|p|ul|ol|li|h[1-6])\b)",
            r"\1", protected[i], flags=re.I)
    return "".join(protected)
