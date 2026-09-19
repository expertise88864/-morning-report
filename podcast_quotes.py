"""Verbatim provenance for notable quotes; never proof of factual accuracy."""
from copy import deepcopy

MAX_QUOTE_CHARS = 600


def validate(digest: dict, transcript: str) -> dict:
    """Validate against the actual supplied model input, without regeneration."""
    result = deepcopy(digest)
    quote = digest.get('notable_quote')
    present = quote not in (None, '')
    valid = (isinstance(quote, str) and bool(quote.strip())
             and len(quote) <= MAX_QUOTE_CHARS
             and isinstance(transcript, str) and quote in transcript)
    result['notable_quote'] = quote if valid else ''
    # Replace model-provided metadata rather than trusting it.
    result['quote_evidence'] = {
        'version': 1, 'status': 'verbatim' if valid else 'unverified' if present else 'absent',
        'quote': quote if valid else '',
    }
    return result


def display_quote(digest: dict) -> str:
    """Legacy/changed quotes without producer evidence must not look verified."""
    quote = digest.get('notable_quote')
    evidence = digest.get('quote_evidence')
    if (not isinstance(quote, str) or not quote.strip() or len(quote) > MAX_QUOTE_CHARS
            or not isinstance(evidence, dict) or evidence.get('version') != 1
            or evidence.get('status') != 'verbatim' or evidence.get('quote') != quote):
        return ''
    return quote
