"""Display-only routing and novelty hints; never change evidence or scores."""
import re

WRITING = """
- 今日結論只留立場理由、主要風險與行動條件，約 350–600 個中文字；不要重述完整
  情境樹或觀察點帳本。其他段落已有的數字只在結論必要時引用一次。
- 升息、通膨、匯率、關稅的全局傳導集中在總經段；其他類股寫公司或產業的新增事實，
  不再複製總經分析。摘要、因果鏈、量級解釋各自補充不同資訊，不逐句改寫同一件事。
- 同一公司連日出現時，先核對有日期的歷史：同月份營收、同一次藥證或成分股調整
  不能改個說法就叫今日增量。沒有新證據只簡短交代延續，真正新公告才展開。
- 外文新聞用繁體中文敘述事件，不把整條英文標題貼進正文；原始來源連結仍保留。
"""

READER_PROSE = """
# 讀者正文
- 所有分析 prose 必須是自然文字；不得抄錄 STANCE_PY、asset_net_effects、
  research.contexts、history ID、欄位名、系統指令或 enum。這些只用於結構化引用欄位。
- data_gaps、dismissed_events 等審核欄位仍完整填寫供後台驗證，不把它們重述到正文。
- 各則 why_it_matters 串接相關條件與矛盾證據；完整情境及觀察結果放在對應欄位。
  保留不確定性，不因隱藏診斷就假稱資料完整。
- 有 tech:ai-models 當期新證據時，top_news_analysis 涵蓋最重要的一則模型發布、
  能力評測或成本變化；沒有新事實不湊數。它屬科技，非其他類股。
- 前台科技最多四則、其他類股最多六則；總經新聞不占類股名額。
  後台仍依既有要求分析，排序由 Python 決定。
""" + WRITING


MACRO_HEADING = '十、總體經濟與政策環境'
OUTLOOK_HEADING = '七之五、情境推演與後續觀察'
_MACRO = re.compile(r'聯準會|央行|日銀|升息|降息|關稅|通膨|殖利率|匯率|日圓|新台幣|'
                    r'\b(?:Fed|ECB|BOJ|CPI|PPI|tariffs?|inflation|interest rates?)\b', re.I)


def is_macro(card: dict, packet: dict) -> bool:
    from analysis_render_depth import news_subject
    import finance_editorial
    item = next((n for n in packet.get('news', [])
                 if n.get('source_item_id') == card.get('source_item_id')), {})
    # A named operating company is still sector news, even when rates affect it.
    named_finance = any(alias in str(item.get('title') or '')
                        for aliases in finance_editorial.ALIASES.values() for alias in aliases)
    if named_finance or news_subject(card, packet).get('name'):
        return False
    return bool(_MACRO.search(str(item.get('title') or '')))


def chinese_headline(title: str) -> str:
    """Use an honest topic label, not a fabricated translation of a source."""
    if not title or re.search(r'[\u3400-\u9fff]', title):
        return title
    if re.search(r'tariffs?|trade', title, re.I):
        topic = '國際貿易與關稅動態'
    elif _MACRO.search(title):
        topic = '利率與通膨動態'
    else:
        topic = '海外公司與產業動態'
    return topic + '（原文報導）'


def integrate_macro(rows: list[str]) -> list[str]:
    """Remove only exact repeated prose; keep links, new numbers and caveats."""
    import content_overlap
    seen, out = set(), []
    for row in rows:
        paragraphs = []
        for paragraph in row.split('\n\n'):
            key = content_overlap.signature(paragraph)
            qualified = re.search(r'保留[:：]|未確認|未證實|尚未|若|除非|但|可能|無法', paragraph)
            if key not in seen or '](' in paragraph or qualified:
                paragraphs.append(paragraph)
            seen.add(key)
        if paragraphs:
            out.append('\n\n'.join(paragraphs))
    return out


def recurring_revenue(source: dict, historical: list, subject: str) -> bool:
    """Rank repeated monthly revenue recaps lower; never suppress a new event.

    Same issuer AND reporting month are necessary. New numbers, approval,
    guidance, index or other material developments keep their original priority.
    This is a ranking hint, not a claim that the whole article is a duplicate.
    """
    title = str(source.get('title') or '')
    import official_announcements
    issuer = official_announcements.issuer(source)
    body = title + ' ' + str(source.get('summary') or '')
    period_pattern = r'(?:\d{3,4}年)?\d{1,2}月份?'
    period = re.search(period_pattern, title)
    if not (subject or issuer) or not period or '營收' not in title:
        return False
    # Positive grammar: any unexplained words (including unknown event types)
    # keep full priority. A growing blacklist cannot prove "only revenue".
    rest = body.replace(subject, '')
    rest = re.sub(period_pattern, '', rest)
    rest = re.sub(r'[+]?[\d,.]+\s*(?:億元|萬元|元|%|％)', '', rest)
    rest = re.sub(r'自結合併營收|合併營收|自結營收|歷史新高|同期新高|同期高|新高|'
                  r'公告|公布|本公司|營收|年增|月增|成長|再創|創|為|達', '', rest)
    if re.sub(r'[\s，,。；;：:、（）()！!]+', '', rest):
        return False
    numbers = set(re.findall(r'[+\-−]?\d+(?:\.\d+)?\s*(?:%|％|億|萬|元)', body))
    for old in historical:
        text = str(old.get('title') or '') + ' ' + str(old.get('excerpt') or '')
        same = (issuer == official_announcements.issuer(old) if issuer else bool(subject and subject in text))
        if (same and period[0] in re.findall(period_pattern, text) and '營收' in text
                and numbers <= set(re.findall(r'[+\-−]?\d+(?:\.\d+)?\s*(?:%|％|億|萬|元)', text))):
            return True
    return False
