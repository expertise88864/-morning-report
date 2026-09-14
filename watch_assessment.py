"""Shared conservative projection for incomplete watch reviews; no state mutation.

Legacy negative results without evidence mean unknown, not disproven. Text
patterns only downgrade certainty; they cannot prove a trigger or close a watch.
Evidence existence/freshness remains the responsibility of analysis_validate.
"""
import re

LABELS = {'triggered': '已觸發', 'not_triggered': '未觸發',
          'partially_triggered': '部分成立，續追蹤',
          'insufficient_evidence': '資料不足，續追蹤',
          'no_longer_relevant': '不再相關'}
_PARTIAL = re.compile(
    r'(?<![不非是])(?:只觸發.{0,35}部分|僅觸發.{0,35}部分|只(?:有|完成)部分|'
    r'僅部分(?:成立|完成|觸發)|尚未全部(?:成立|完成|觸發)|'
    r'(?<!全部)(?<!不是)(?<!並非)(?<!僅)部分(?:成立|完成|觸發))')
_MISSING = re.compile(
    r'不在今日證據|沒有.{0,12}(?:數字|數據|資訊|證據)|'
    r'缺少.{0,16}(?:數字|數據|實際值|證據)|仍待驗證|尚待驗證')


def status(row: dict) -> str:
    """Never upgrade uncertainty to success, even if supporting prose says so."""
    declared = str(row.get('status') or '')
    text = str(row.get('what_happened') or '')
    if declared == 'insufficient_evidence':
        return declared
    if declared in ('triggered', 'not_triggered', 'partially_triggered'):
        if _PARTIAL.search(text):
            return 'partially_triggered' if row.get('evidence_ids') else 'insufficient_evidence'
        if _MISSING.search(text) or (declared == 'not_triggered' and not row.get('evidence_ids')):
            return 'insufficient_evidence'
    return declared
