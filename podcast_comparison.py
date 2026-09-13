"""Attributed opinion/news comparisons with independently checked source excerpts."""
from __future__ import annotations

import re

from news_temporal_context import display_day, relation
from podcast_revision import _rows
from podcast_topic import mismatch

RELATIONS = ('agreement', 'disagreement', 'not_comparable')
RULES = """Podcast 比對規則：podcast_context 是主持人意見，不是市場事實。
僅在新聞與節目討論同一可辨識命題時填 podcast_comparisons，每則至多兩項；
只有同公司但不同事件不可硬比。opinion_id 只能填此區，禁止放入任何 evidence_ids。
opinion_excerpt 與 news_excerpt 各逐字摘錄對應來源 8–240 字，不可自行補字。
relation 僅描述觀點相同、分歧或不可比，不代表主持人預測已驗證；
comparison 說明同一命題的差異與適用条件，open_question 說明尚缺證據與後續驗證。
日期由程式呈現，不以發布日推定錄製日或因果。未知日期不推先後。
没有適用節目就用空陣列，不為湊數製造對照。
"""


def schema(obj, arr, string, enum):
    return arr(obj({
        'opinion_id': string('照抄 podcast_context.episodes 的 opinion_id'),
        'opinion_excerpt': string('主持人原文短摘，8–240 字'),
        'news_excerpt': string('本則新聞原文短摘，8–240 字'),
        'relation': enum(RELATIONS),
        'comparison': string('比較同一命題，不得宣稱意見已證實'),
        'open_question': string('尚缺證據與後續可驗證事項'),
    }), '有相關意見才填，至多兩項；不是市場事實佐證')


def _sources(row, packet):
    packet = packet if isinstance(packet, dict) else {}
    context = packet.get('podcast_context')
    context = context if isinstance(context, dict) else {}
    news = next((n for n in _rows(packet.get('news'))
                 if n.get('source_item_id') == row.get('source_item_id')), None)
    opinions = {p['opinion_id']: p for p in
                _rows(context.get('episodes')) if isinstance(p.get('opinion_id'), str)}
    return news, opinions


def _excerpt(value, parts):
    return (isinstance(value, str) and 8 <= len(value.strip()) <= 240
            and any(value in part for part in parts if isinstance(part, str)))


def row_problems(row, packet):
    if not isinstance(row, dict):
        return ['Podcast 比對新聞不是物件']
    rows = row.get('podcast_comparisons', [])
    if not isinstance(rows, list):
        return ['podcast_comparisons 必須是陣列']
    errors = ['podcast_comparisons 超過兩項'] if len(rows) > 2 else []
    news, opinions = _sources(row, packet)
    seen = set()
    for item in rows:
        if not isinstance(item, dict):
            errors.append('Podcast 比對不是物件')
            continue
        oid = item.get('opinion_id')
        if not isinstance(oid, str) or oid not in opinions or news is None:
            errors.append('Podcast 比對引用不存在的節目或新聞')
            continue
        if oid in seen:
            errors.append('Podcast 比對重複引用同一集')
        seen.add(oid)
        opinion = opinions[oid]
        points = opinion.get('summary_points')
        parts = (points if isinstance(points, list) else []) + [opinion.get('market_view', '')]
        parts += [t.get('reason', '') for t in _rows(opinion.get('tickers'))]
        if not _excerpt(item.get('opinion_excerpt'), parts):
            errors.append('Podcast 摘錄不在該集可用意見內')
        if not _excerpt(item.get('news_excerpt'), [news.get(k, '') for k in ('title', 'summary', 'fulltext')]):
            errors.append('Podcast 比對新聞摘錄不在本則來源內')
        if item.get('relation') not in RELATIONS:
            errors.append('Podcast 比對關係無效')
        if mismatch(item.get('opinion_excerpt'), item.get('news_excerpt')):
            errors.append('Podcast 摘錄主題不同；移除這項對照或選擇同一命題，不以不可比標籤保留無關內容')
        for key in ('comparison', 'open_question'):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f'Podcast 比對缺少 {key}')
    return errors


def validate(obj, packet):
    errors = []
    for row in _rows(obj.get('top_news_analysis')):
        if isinstance(row, dict):
            errors.extend(row_problems(row, packet))
    # Opinion IDs are deliberately never added to the fact registry.
    def walk(node, path=()):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, path + (key,))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, path + (index,))
        elif isinstance(node, str) and node.startswith('opinion:'):
            allowed = (len(path) == 5 and path[0] == 'top_news_analysis'
                       and path[2] == 'podcast_comparisons' and path[4] == 'opinion_id')
            if not allowed:
                errors.append('Podcast 意見 ID 不可用在一般證據欄位')
    walk(obj)
    return errors


def prose(row, packet):
    """Direct rendering also fails closed; labels and chronology are not model-owned."""
    if not isinstance(packet, dict) or row_problems(row, packet):
        return ''
    news, opinions = _sources(row, packet)
    def safe(value):
        return re.sub(r'[\[\]<>*_`#]', '', str(value)).strip()
    labels = {'agreement': '觀點相近', 'disagreement': '觀點分歧', 'not_comparable': '暫不可比'}
    dates = {'earlier_reporting': '節目較早發布', 'same_day_reporting': '同日發布',
             'later_reporting': '節目較晚發布', 'unknown': '日期不足，不排先後'}
    lines = []
    for item in row.get('podcast_comparisons', []):
        source = opinions[item['opinion_id']]
        lines.append(f"Podcast 觀點對照（非事實佐證）：{safe(source['show'])}「{safe(source['title'])}」"
                     f"，{display_day(source)}，{dates[relation(news, source)]}；"
                     f"{labels[item['relation']]}：{safe(item['comparison'])}。"
                     f"待驗證：{safe(item['open_question'])}。")
    return '\n\n'.join(lines)
