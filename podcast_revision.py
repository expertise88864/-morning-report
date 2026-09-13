"""Preserve attributed comparisons and constrain stateless repair additions."""
import json


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def signatures(obj):
    return {json.dumps([row.get('source_item_id'), item], sort_keys=True, ensure_ascii=False)
            for row in _rows((obj or {}).get('top_news_analysis'))
            for item in _rows(row.get('podcast_comparisons'))}


def repair_problems(obj, visible_news, full_context):
    if visible_news is None:
        return []
    errors = []
    for row in _rows((obj or {}).get('top_news_analysis')):
        if row.get('source_item_id') in visible_news:
            continue
        if signatures({'top_news_analysis': [row]}) - full_context:
            errors.append('新增或改寫 Podcast 對照，但本輪看不到該新聞；只能保留完整脈絡下的原對照')
    return errors


def retained(row):
    return {'podcast:' + str(item.get('opinion_id'))
            for item in _rows(row.get('podcast_comparisons')) if item.get('opinion_id')}
