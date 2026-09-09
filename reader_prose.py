"""Reader-facing projection only; keep source analysis and audit data intact."""
from __future__ import annotations

import re
from reader_selection import article_is_tech, select_cards, importance_order  # noqa: F401


def clean_text(text: str) -> str:
    """Clean visible prose without changing source URL destinations."""
    return ''.join(part if re.match(r'https?://', part) else _clean_prose(part)
                   for part in re.split(r'(https?://[^\s<>)]+)', text))


def _clean_prose(text: str) -> str:
    """Remove schema echoes, not the economic explanation following them."""
    from reader_fact_labels import medical_terms
    text = medical_terms(text)
    text = re.sub(r"(?:抄錄系統計分\s*)?`?STANCE_PY\.total`?\s*=\s*[+-]?\d+"
                  r"\s*[（(]\s*label\s*[:：]\s*[^）)]+[）)]\s*[。. ]*", "", text)
    text = re.sub(r"[（(]見\s*`?asset_net_effects`?[^）)]*[）)]", "", text)
    text = re.sub(r'`?(?<![A-Za-z0-9_])STANCE_PY(?:\.[A-Za-z_][A-Za-z0-9_]*)?`?', '整體立場', text)
    text = re.sub(r'`?(?<![A-Za-z0-9_])asset_net_effects(?![A-Za-z0-9_])`?', '各項消息合計影響', text)
    text = re.sub(r"[（(]fact[）)]", "", text)
    text = re.sub(r'系統立場(?:只有|為|是)?', '整體立場為', text)
    text = re.sub(r'系統把它折成\s*[+-]?\d+(?:分)?[，,。]?', '', text)
    text = re.sub(r'ALERTS(?:的解釋)?', '風險訊號', text)
    text = re.sub(r'(整體立場為[^。\n（）]{0,8})[（(][+-]?\d+[）)]', r'\1', text)
    text = re.sub(r'(?<![A-Za-z_])moderate(?![A-Za-z_])', '中等', text).replace("本報看不出次級影響", "")
    text = re.sub(r"、(?=[。；;])", "", text)
    text = re.sub(r'\*\*(基準|偏多|偏空)\*\*[:：]',
                  lambda m: {'基準': '', '偏多': '若有利條件成立，',
                             '偏空': '但若風險擴大，'}[m[1]], text)
    return text


def public_sections(markdown: str, obj=None, packet=None) -> str:
    """Route supporting discussion out of the executive conclusion.

    This acts on the rendered copy, never on validated JSON or persisted state.
    Keep every scenario and observation, including one-off tracking caveats.
    """
    hidden = {"資料缺口", "本段的保留事項"}
    integrated = {"情境與觸發條件", "證據衝突與調和", "昨日觀察點回顧", "觀察觸發點"}
    sections = re.split(r"(?m)^(?=#{2,3} )", markdown)
    kept, additions = [], []
    obj, packet = obj or {}, packet or {}
    cards, _ = select_cards(obj.get('top_news_analysis') or [], packet)
    from reader_editorial import is_macro, MACRO_HEADING, OUTLOOK_HEADING
    def expanded(refs):
        result = set(refs)
        for item in packet.get('news') or []:
            sid = item.get('source_item_id')
            if any(ref in result for ref in [str(f.get('evidence_id') or f'fact:{sid}.{i}')
                   for i, f in enumerate(item.get('numeric_facts') or [])]):
                result.add(sid)
        result.update(ref[5:].rsplit('.', 1)[0] for ref in list(result)
                      if isinstance(ref, str) and ref.startswith('fact:'))
        for cluster in (packet.get('news_clusters') or {}).get('clusters', []):
            if cluster.get('cluster_id') in result:
                result.update(cluster.get('member_source_ids') or [])
        return result

    def destination(row):
        refs = set(row.get('evidence_ids') or []) | set(row.get('supporting_ids') or []) | set(row.get('opposing_ids') or [])
        claims = set(row.get('claim_ids') or [])
        for claim in obj.get('claim_audit') or []:
            if claim.get('claim_id') in claims:
                refs.update(claim.get('evidence_ids') or [])
        refs = expanded(refs)
        for card in cards:
            if card.get('source_item_id') in refs:
                if is_macro(card, packet):
                    return MACRO_HEADING
                return '八、科技板塊脈動' if article_is_tech(card, packet) else '九、其他類股資訊'
        for block in (obj.get('macro_environment') or {}).values():
            if isinstance(block, dict) and block.get('analysis') and refs.intersection(expanded(block.get('evidence_ids') or [])):
                return '十、總體經濟與政策環境'
        for field, heading in (('world_events', '七之二、世界大事速覽'),
                               ('taiwan_policy', '十之二、重大政策深度解析'),
                               ('taiwan_local', '十一、台灣本地動態')):
            for block in obj.get(field) or []:
                if block.get('what') and block.get('source_item_id') in refs:
                    if field == 'taiwan_local':
                        return ('八、科技板塊脈動' if article_is_tech(block, packet)
                                else '九、其他類股資訊')
                    return heading
        return OUTLOOK_HEADING
    rows_by_title = {'證據衝突與調和': obj.get('contradictions') or [],
                     '昨日觀察點回顧': obj.get('watch_review') or [],
                     '觀察觸發點': obj.get('watch_triggers') or []}
    for section in sections:
        head, _, body = section.partition("\n")
        title = head.lstrip("# ").strip()
        if title in hidden:
            continue
        if title in integrated:
            # Merge each item with its nested trigger into one prose paragraph.
            body = re.sub(r"(?m)^\s+- 什麼情況代表它成立:", "觀察條件：", body)
            paragraphs = (body.strip().splitlines() if title == '昨日觀察點回顧'
                          else re.split(r"(?m)^- ", body))
            paragraphs = [" ".join(p.splitlines()).strip() for p in paragraphs if p.strip()]
            rows = rows_by_title.get(title, [])
            for index, prose in enumerate(paragraphs):
                target = (OUTLOOK_HEADING if title == '情境與觸發條件' else
                          destination(rows[index]) if index < len(rows) else OUTLOOK_HEADING)
                additions.append((target, prose))
        elif title == '十一、台灣本地動態':
            local_rows = [row for row in obj.get('taiwan_local') or []
                          if isinstance(row, dict) and row.get('what') and row.get('impact')]
            for row in local_rows:
                target = ('八、科技板塊脈動' if article_is_tech(row, packet)
                          else '九、其他類股資訊')
                if is_macro(row, packet):
                    target = MACRO_HEADING
                prose = str(row['what']).rstrip('。:：') + '。' + str(row['impact'])
                additions.append((target, prose))
            if not local_rows and body.strip():
                additions.append(('九、其他類股資訊', body.strip()))
        else:
            kept.append(section)
    if additions:
        for target, prose in additions:
            index = next((i for i, section in enumerate(kept) if section.startswith('## ' + target + '\n')), None)
            if index is not None:
                kept[index] = kept[index].rstrip() + '\n\n' + prose + '\n\n'
            else:
                insert_at = next((i for i, section in enumerate(kept)
                                  if section.startswith('## 我的明確立場\n')), len(kept))
                kept.insert(insert_at, '## ' + target + '\n' + prose + '\n\n')
    # Keep short paragraphs; supporting scenarios and observations live elsewhere.
    for i, section in enumerate(kept):
        if section.startswith('## 我的明確立場\n'):
            head, _, body = section.partition('\n')
            body = re.sub(r'系統計分[^。\n]*。', '', body)
            lines = [re.sub(r'^(?:可考慮的做法|風險)[:：]', '', line.strip())
                     for line in body.splitlines() if line.strip()]
            contract = [line for line in lines if line.startswith(('立場：', '淨分 '))]
            prose = [line for line in lines if line not in contract]
            kept[i] = head + '\n' + '\n'.join(contract) + '\n\n' + '\n\n'.join(prose) + '\n\n'
    return clean_text("".join(kept))
