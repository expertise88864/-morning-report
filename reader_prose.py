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
    text = medical_terms(text).replace("Python 模型", "模型").replace("把表合起來讀：", "")
    text = re.sub(r"(?:\*\*)?(?:所以會怎樣|後續可能影響)(?:\*\*)?\s*[:：]\s*", "", text)
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
    2026-10-19 user: specialized analysis, fallback-letter layout. Scenarios keep
    one sentence each and the top watch triggers join one short closing section;
    tensions and watch reviews stay in JSON/ledger (still validated), not in 八/九.
    """
    import legacy_layout
    from reader_editorial import OUTLOOK_HEADING
    obj = obj or {}   # legacy 路徑只傳 markdown(不可少:conclusion 要讀 obj)
    hidden = {"資料缺口", "本段的保留事項", "證據衝突與調和", "昨日觀察點回顧"}
    integrated = {"情境與觸發條件", "觀察觸發點"}
    sections = re.split(r"(?m)^(?=#{2,3} )", markdown)
    kept, additions = [], []
    for section in sections:
        head, _, body = section.partition("\n")
        title = head.lstrip("# ").strip()
        if title in hidden:
            continue
        if title in integrated:
            paragraphs = [" ".join(p.splitlines()).strip() for p in re.split(r"(?m)^- ", body) if p.strip()]
            if title == "情境與觸發條件":   # 基準/偏多/偏空各一句,觸發條件不另列
                paragraphs = [legacy_layout.first_sentence(re.sub(r"\s+- 什麼情況代表它成立:.*", "", p))
                              for p in paragraphs]
            else:
                paragraphs = paragraphs[:legacy_layout.OUTLOOK_WATCH_LIMIT]
            additions.extend((OUTLOOK_HEADING, p) for p in paragraphs if p)
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
    from reader_revision import conclusion
    return clean_text(conclusion(kept, obj))
