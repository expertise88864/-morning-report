"""Source-title exemptions require Python-bound URL AND literal title."""
import re
from reader_citation_registry import matches, url_variants
from reader_source_line import citation_prefix_end


def guarded_parts(text: str, allowed_urls, source_titles=()) -> tuple[str, str, str]:
    """Return literal source prefix, original remainder and its guard copy."""
    end = citation_prefix_end(text)
    if not end:
        return '', text, text
    label = re.match(r'^\s*\[([^\]\r\n]+)\]\(', text)
    assert label is not None  # The shared balanced-link parser accepted it.
    link_end = len(text[:end].rstrip())
    url = text[label.end():link_end - 1]
    if matches(url, label.group(1), source_titles) and any(url in url_variants(u) for u in allowed_urls if isinstance(u, str)):
        return text[:end], text[end:], text[end:]
    # Only the matching copy drops an unregistered destination; safe text
    # retains its exact original representation unless a correction is needed.
    return '', text, label.group(1) + text[end:]
