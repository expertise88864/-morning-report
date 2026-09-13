"""Source-backed *attributed* Podcast opinions, never market authority.

An exact quotation proves text provenance, not semantic entailment. The basis is
still a model classification and must be presented as a speaker's opinion.
Old records without this evidence remain readable but are not investment calls.
No state writes, provider requests, stock-specific exceptions or inferred stance.
"""
from __future__ import annotations

from copy import deepcopy

DIRECTIONS = frozenset({'bullish', 'bearish', 'neutral'})
BASES = frozenset({'investment_view', 'non_investment', 'unclear'})
MAX_QUOTE = 600


def validate_ticker(ticker: dict, transcript: str) -> dict:
    """Replace all claimed validation metadata using the supplied transcript.

    Quotes must be verbatim spans of the actual model input, not the full audio
    beyond a truncation boundary. Do not normalize away negation or punctuation.
    Unknown basis/direction and unsupported quotes fail closed without deleting
    the company comment or converting an unknown opinion to neutral.
    """
    result = deepcopy(ticker)
    result.pop('direction_evidence', None)
    basis = ticker.get('stance_basis')
    quote = ticker.get('stance_quote')
    direction = ticker.get('direction')
    if not isinstance(basis, str) or basis not in BASES:
        basis = 'unclear'
    grounded = (isinstance(quote, str) and 8 <= len(quote) <= MAX_QUOTE
                and isinstance(transcript, str) and quote in transcript)
    known = isinstance(direction, str) and direction in DIRECTIONS
    status = ('non_investment' if basis == 'non_investment'
              else 'attributed' if basis == 'investment_view' and grounded and known
              else 'unverified')
    result['direction_evidence'] = {
        'version': 1, 'status': status,
        'quote': quote if grounded else '',
        'direction': direction if status == 'attributed' else '',
    }
    # Do not leave a fabricated quotation available to downstream projections.
    result.pop('stance_quote', None)
    result['stance_basis'] = basis
    result['direction'] = direction if status == 'attributed' else 'unknown'
    return result


def direction(ticker: dict) -> str:
    """Single consumer gate; absence of evidence is not a neutral investment view."""
    evidence = ticker.get('direction_evidence')
    value = ticker.get('direction')
    if not isinstance(evidence, dict) or evidence.get('version') != 1:
        return 'unknown'
    quote = evidence.get('quote')
    if (evidence.get('status') != 'attributed'
            or ticker.get('stance_basis') != 'investment_view'
            or not isinstance(value, str) or value not in DIRECTIONS
            or evidence.get('direction') != value
            or not isinstance(quote, str) or not 8 <= len(quote) <= MAX_QUOTE):
        return 'unknown'
    return value


def validate_digest(digest: dict, transcript: str) -> dict:
    """Keep commentary, expose missing evidence, and never request regeneration."""
    result = deepcopy(digest)
    tickers = digest.get('tickers') or []
    counts = {'attributed': 0, 'non_investment': 0, 'unverified': 0, 'invalid': 0}
    if not isinstance(tickers, list):
        tickers = []
        counts['invalid'] += 1
    validated = []
    for ticker in tickers:
        if not isinstance(ticker, dict):
            counts['invalid'] += 1
            continue
        row = validate_ticker(ticker, transcript)
        counts[row['direction_evidence']['status']] += 1
        validated.append(row)
    result['tickers'] = validated
    result['direction_quality'] = counts
    return result
