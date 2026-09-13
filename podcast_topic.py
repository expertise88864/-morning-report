"""Narrow mismatch detector, not a semantic agreement classifier."""
import re

# Only concrete subject matter, never company names, sentiment or market direction.
TOPICS = {
    'demand': r'訂單|需求|接單|拉貨|\borders?\b|\bdemand\b',
    'dividend': r'股利|配息|除息|\bdividends?\b',
    'management': r'董事長|總經理|執行長|人事異動|\bCEO\b',
    'litigation': r'訴訟|提告|判決|\blawsuit\b|\blitigation\b',
    'capacity': r'產能|量產|擴廠|\bcapacity\b',
    'acquisition': r'併購|收購|\bacquisition\b|\bmerger\b',
}


def topics(text):
    return {key for key, pattern in TOPICS.items()
            if isinstance(text, str) and re.search(pattern, text, re.I)}


def mismatch(left, right):
    a, b = topics(left), topics(right)
    # Unknown vocabulary is inconclusive, not an automatic rejection or approval.
    return bool(a and b and a.isdisjoint(b))
