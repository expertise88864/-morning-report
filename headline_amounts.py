"""Normalize explicit headline amounts; bare dates/ordinals are not amounts."""
import re
from decimal import Decimal

_DIGITS = dict(zip('零〇一二三四五六七八九兩', (0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2)))
_UNITS = {'十': 10, '百': 100, '千': 1000, '萬': 10000,
          '億': 100000000, '兆': 1000000000000}
_CN = '零〇一二三四五六七八九十百千萬億兆兩'
_PATTERN = re.compile(
    rf'(?P<prefix>[$€£¥]?)(?P<number>\d[\d,]*(?:\.\d+)?|[{_CN}]+(?:點[零〇一二三四五六七八九]+)?)'
    r'(?P<scale>兆|億|萬|千|百)?(?P<unit>美元|日圓|歐元|元|%)?')


def _chinese_integer(text: str) -> int:
    # Split at the largest unit, so 一兆二千億 and 一億兩千萬 compose correctly.
    for unit in ('兆', '億', '萬'):
        if unit in text:
            left, right = text.split(unit, 1)
            return (_chinese_integer(left) if left else 1) * _UNITS[unit] + _chinese_integer(right)
    total = number = 0
    for char in text:
        if char in _DIGITS:
            number = number * 10 + _DIGITS[char]
        else:
            total += (number or 1) * _UNITS[char]
            number = 0
    return total + number


def amount_markers(text: str) -> list[tuple[Decimal, str]]:
    """Veto contradictory explicit amounts, not 第3季 versus 第三季 notation."""
    text = text.replace('美金', '美元')
    text = re.sub(r'(?:新台幣|台幣)(\d[\d,.]*[兆億萬千百]?)(?![\d])', r'\1元', text)
    result = []
    for match in _PATTERN.finditer(text):
        raw, scale, unit, prefix = (match.group(k) or '' for k in ('number', 'scale', 'unit', 'prefix'))
        # Chinese magnitudes are consumed greedily as part of the number.
        if not (scale or unit or prefix or any(c in raw for c in '萬億兆')):
            continue
        if raw[0].isdigit():
            value = Decimal(raw.replace(',', ''))
        else:
            integer, _, fraction = raw.partition('點')
            value = Decimal(_chinese_integer(integer))
            if fraction:
                value += Decimal('0.' + ''.join(str(_DIGITS[c]) for c in fraction))
        value *= _UNITS.get(scale, 1)
        result.append((value, prefix + unit))
    return result
