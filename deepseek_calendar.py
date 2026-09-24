"""Verified Beijing-calendar exclusions from DeepSeek peak pricing."""

from datetime import datetime

# DeepSeek's peak hours apply Monday–Friday, excluding Chinese public holidays.
# These are the full 2026 State Council holiday periods, including observed
# weekdays. Make-up working weekends remain off-peak under DeepSeek's wording.
# Sources: https://api-docs.deepseek.com/quick_start/pricing/
# https://www.beijing.gov.cn/cs/gncs/zcwj/202603/t20260327_4568275.html
_HOLIDAY_RANGES_2026 = (
    ((1, 1), (1, 3)),
    ((2, 15), (2, 23)),
    ((4, 4), (4, 6)),
    ((5, 1), (5, 5)),
    ((6, 19), (6, 21)),
    ((9, 25), (9, 27)),
    ((10, 1), (10, 7)),
)


class UnknownPricingCalendar(ValueError):
    """The provider's holiday exception cannot be priced for this year."""


def is_offpeak_day(beijing: datetime) -> bool:
    """True for a weekend or verified 2026 public holiday in Beijing."""
    if beijing.weekday() >= 5:
        return True
    if beijing.year != 2026:
        raise UnknownPricingCalendar(f"unverified DeepSeek holidays: {beijing.year}")
    return any(
        start <= (beijing.month, beijing.day) <= end
        for start, end in _HOLIDAY_RANGES_2026
    )
