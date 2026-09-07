"""Reader-facing projection only; keep source analysis and audit data intact."""
from __future__ import annotations

import re


def clean_text(text: str) -> str:
    """Clean visible prose without changing source URL destinations."""
    return ''.join(part if re.match(r'https?://', part) else _clean_prose(part)
                   for part in re.split(r'(https?://[^\s<>)]+)', text))


def _clean_prose(text: str) -> str:
    """Remove schema echoes, not the economic explanation following them."""
    text = re.sub(r"(?:抄錄系統計分\s*)?`?STANCE_PY\.total`?\s*=\s*[+-]?\d+"
                  r"\s*[（(]\s*label\s*[:：]\s*[^）)]+[）)]\s*[。. ]*", "", text)
    text = re.sub(r"[（(]見\s*`?asset_net_effects`?[^）)]*[）)]", "", text)
    text = re.sub(r'`?(?<![A-Za-z0-9_])STANCE_PY(?:\.[A-Za-z_][A-Za-z0-9_]*)?`?', '整體立場', text)
    text = re.sub(r'`?(?<![A-Za-z0-9_])asset_net_effects(?![A-Za-z0-9_])`?', '各項消息合計影響', text)
    text = re.sub(r"[（(]fact[）)]", "", text)
    text = re.sub(r'(?<![A-Za-z_])moderate(?![A-Za-z_])', '中等', text).replace("本報看不出次級影響", "")
    text = re.sub(r"、(?=[。；;])", "", text)
    return text


def public_sections(markdown: str, obj=None, packet=None) -> str:
    """Merge supporting discussion into the conclusion, hide diagnostic sections.

    This acts on the rendered copy, never on validated JSON or persisted state.
    Keep every scenario and observation, including one-off tracking caveats.
    """
    hidden = {"資料缺口", "本段的保留事項"}
    integrated = {"情境與觸發條件", "證據衝突與調和", "昨日觀察點回顧", "觀察觸發點"}
    sections = re.split(r"(?m)^(?=#{2,3} )", markdown)
    kept, additions = [], []
    obj, packet = obj or {}, packet or {}
    cards, _ = select_cards(obj.get('top_news_analysis') or [], packet)
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
                return '八、科技板塊脈動' if article_is_tech(card, packet) else '九、其他類股資訊'
        for block in (obj.get('macro_environment') or {}).values():
            if isinstance(block, dict) and block.get('analysis') and refs.intersection(expanded(block.get('evidence_ids') or [])):
                return '十、總體經濟與政策環境'
        for field, heading in (('world_events', '七之二、世界大事速覽'),
                               ('taiwan_policy', '十之二、重大政策深度解析'),
                               ('taiwan_local', '十一、台灣本地動態')):
            for block in obj.get(field) or []:
                if block.get('what') and block.get('source_item_id') in refs:
                    return heading
        return '我的明確立場'
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
                target = destination(rows[index]) if index < len(rows) else '我的明確立場'
                additions.append((target, prose))
        else:
            kept.append(section)
    if additions:
        for target, prose in additions:
            index = next((i for i, section in enumerate(kept) if section.startswith('## ' + target + '\n')), None)
            if index is not None:
                kept[index] = kept[index].rstrip() + '\n\n' + prose + '\n\n'
    return clean_text("".join(kept))


def article_is_tech(card: dict, packet: dict) -> bool:
    from analysis_render_depth import news_subject, is_tech
    from industry_class import is_tech_headline
    import finance_editorial as finance

    item = next((n for n in packet.get("news", []) if isinstance(n, dict)
                 and n.get("source_item_id") == card.get("source_item_id")), {})
    subject = news_subject(card, packet)
    if is_tech(subject):
        return True
    # A known non-tech issuer outranks incidental AI vocabulary.
    if finance.groups(item) or subject.get("industry") or subject.get("name") in ("COST", "TMUS"):
        return False
    title = str(item.get("title") or "")
    return is_tech_headline(title) or bool(re.search(
        r"台積|美光|聯亞|廠務工程|矽光子|OpenAI|Anthropic|DeepSeek|Claude|Gemini|ChatGPT", title, re.I))


def select_cards(cards: list, packet: dict) -> tuple[list, list]:
    """Six per section, ranked by existing Python event order, with topic reserves."""
    import finance_editorial as finance
    items = {n.get("source_item_id"): n for n in packet.get("news", []) if isinstance(n, dict)}
    events = packet.get('top_events') or {}
    order = [r.get('cluster_id') for r in events.get('ranked', [])] or events.get('top_cluster_ids', [])
    ranks = {cid: i for i, cid in enumerate(order)}
    membership = {sid: str(c.get("cluster_id") or "") for c in
                  (packet.get("news_clusters") or {}).get("clusters", [])
                  for sid in c.get("member_source_ids", [])}
    ordered = sorted((c for c in cards if isinstance(c, dict)), key=lambda c:
                     ranks.get(membership.get(c.get("source_item_id")), 999))
    selected = []
    for tech in (True, False):
        group = [c for c in ordered if article_is_tech(c, packet) == tech]
        if tech:
            reserve = [c for c in group if "tech:ai-models" in
                       items.get(c.get("source_item_id"), {}).get("coverage_buckets", [])][:1]
        else:
            preferred = finance.balanced([items.get(c.get("source_item_id"), {}) for c in group], 1)
            reserve = [c for c in group if any(items.get(c.get("source_item_id")) is n for n in preferred)]
        selected.extend((reserve + [c for c in group if c not in reserve])[:6])
    return selected, [c for c in ordered if c not in selected]
