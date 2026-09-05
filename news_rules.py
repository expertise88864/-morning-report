"""新聞分類 / 來源分級 / 降噪規則 + 台灣政策·醫界情報關鍵字(A5-B3 由 morning_report 抽出)。
純規則函式(關鍵字比對、來源評級、去重、重要性打分)與其關鍵字常數;無網路/狀態,
只依 stdlib(各函式內部自行 import re/difflib)。morning_report 以 re-export 保相容,
既有測試零修改。後續 news_events(B5)如需 NEWS_POSITIVE/NEGATIVE_TERMS 由此 import。
"""
import re
from typing import Optional


#: **期間詞的單一判準**(repo-wide 外審 2026-08-19 r3:先前
#: analysis_validate 與 news_events 各養一份,已經漂移 —— 1Q/1H/CY25/2Q26
#: 一邊擋得住、一邊放行)。只收「帶數字」的形狀:裸縮寫(MTD/TTM)可能
#: 是真代號,絕對黑名單會誤殺(analysis_validate 的教訓,誤殺比漏放危險)。
#: 兩個消費端:asset/entity 候選的 fullmatch 篩查 —— Q2、2Q、H1、1H、
#: FY25、CY25、2Q26、1H26、2026Q3、2026H1 都不是公司。
PERIOD_TOKEN = re.compile(
    r"Q[1-4]|[1-4]Q(?:[0-9]{2,4})?|H[12]|[12]H(?:[0-9]{2,4})?"
    r"|(?:FY|CY)[0-9]{2,4}|[0-9]{4}Q[1-4]|[0-9]{4}H[12]",
    re.IGNORECASE)


NEWS_POSITIVE_TERMS = [
    "上修", "優於預期", "創高", "成長", "增加", "擴產", "訂單", "得標",
    "獲利", "轉盈", "調升", "beat", "raise", "raised", "growth", "record",
    "order", "orders", "contract", "contracts", "expand", "expanded",
    "increase", "increased", "upgrade", "upgraded",
]


NEWS_NEGATIVE_TERMS = [
    "下修", "低於預期", "衰退", "減產", "砍單", "虧損", "轉虧", "調降",
    "禁令", "出口管制", "制裁", "召回", "訴訟", "miss", "cut", "lower",
    "decline", "declined", "loss", "losses", "ban", "banned", "sanction",
    "sanctions", "recall", "lawsuit", "downgrade", "downgraded",
]


TECH_NEWS_ANALYST_NOISE = [
    "目標價", "上看", "喊買", "喊到", "看好上", "評等", "重申", "調升評等", "調降評等",
    "投顧", "分析師看", "外資點名", "外資喊", "法人喊", "buy 評等",
    "target price", "price target", "overweight", "outperform", "reiterate", "initiate",
]


TECH_NEWS_CHIPFLOW_NOISE = [
    "買超", "賣超", "三大法人", "外資連", "投信連", "自營商連", "籌碼", "法人動向",
    "土洋對作", "權證", "融資增", "融券增",   # 「認購/認售」太廣(認購私募=實質公司動作)→ 只留權證
]


TECH_GATE_CATALYST = [
    # 訂單/接單/產能
    "訂單", "新訂單", "得標", "接單", "大單", "下單", "投片", "擴產", "產能", "良率",
    # 營運/財報事件(具體,非泛詞;不用裸「上修/下修」——會放行「上修目標價」這類喊價,
    # 真正的財測上修由「財測」涵蓋,另收「上修/下修展望」)
    "法說", "財報", "財測", "上修展望", "下修展望", "轉盈", "轉虧", "beat", "miss",
    # 製造/產品
    "量產", "出貨", "投產", "流片", "tape-out", "tapeout", "認證", "漲價", "報價",
    # 投資/設廠/併購
    "設廠", "建廠", "併購", "收購", "簽約", "簽訂", "合作",
    # 負面具體事件
    "砍單", "減產", "停產", "罷工", "火災", "資遣", "裁員", "召回", "訴訟",
    "出口管制", "禁令", "制裁", "sanction", "sanctions", "ban", "banned", "recall", "lawsuit",
]


_A_GRADE_EN = re.compile(r"\b(federal reserve|treasury|sec|mops|twse|taifex)\b")
_A_GRADE_ZH = ("中央銀行", "證交所", "公開資訊觀測站")
_B_GRADE_EN = re.compile(
    r"\b(cnbc|bloomberg|reuters|cnyes|udn|cna|scmp|nikkei|bbc"
    r"|moneydj|technews|digitimes|yahoo)\b")
_B_GRADE_ZH = ("鉅亨", "工商", "經濟日報", "聯合", "中央社", "南華",
               "科技新報")


def _grade_from_text(text: str, allow_a: bool = True) -> str:
    """從單一字串判斷來源等級;無法判斷回空字串。
    allow_a=False 用於「標題」欄位:標題提到 SEC/央行只代表事件主角是官方機構,
    不代表發布者是官方來源——A 級只能由 source/source_name(發布者身分)判定;
    標題僅用於辨識 Google News 尾綴的主流媒體名(B 級)(GPT-5.6 三審 P1)。"""
    text = (text or "").lower()
    if allow_a and (_A_GRADE_EN.search(text)
                    or any(token in text for token in _A_GRADE_ZH)):
        return "A"
    if _B_GRADE_EN.search(text) or any(token in text for token in _B_GRADE_ZH):
        return "B"
    return ""


def _news_source_grade(item: dict) -> str:
    """新聞來源分級：官方 A、主流媒體 B、聚合或未識別來源 C。
    Google News / 類股 feed 的 source 只是聚合器代號(如 Google:NVDA、類股-金融-台股),
    真正的發布媒體在 source_name、或 Google 標題結尾「- 經濟日報」。三者一起看,
    否則正版個股新聞會被誤判為 C → 去重時輸給舊版、且被當低可信度。
    標題只允許升到 B(見 _grade_from_text 的 allow_a 說明)。
    聚合器代號(Google:xxx / 類股-xxx)是內部查詢別名,不是發布者身分——
    「Google:SEC」這類別名不得升 A(四審 P1-2);別名整段跳過,只看
    source_name 與標題尾綴。"""
    source = str(item.get("source") or "")
    is_aggregator = source.lower().startswith("google:") or source.startswith("類股-")
    return ((_grade_from_text(source) if not is_aggregator else "")
            or _grade_from_text(item.get("source_name"))
            or _grade_from_text(item.get("title"), allow_a=False)
            or "C")


def _news_keep_score(item: dict) -> tuple[int, int]:
    """同事件去重時優先保留較可信、內容較完整的版本。"""
    grade_score = {"A": 3, "B": 2, "C": 1}.get(_news_source_grade(item), 0)
    content_len = len(item.get("summary") or "") + len(item.get("fulltext") or "")
    return grade_score, content_len


def _credibility_tag(item: dict) -> str:
    """G6:可信度確定性標記。獨立來源數(dedup 累計的 merged_n)> 1 或含官方來源時,
    回「〔獨立來源 N・含官方來源〕」供 prompt 顯示;否則回 ""。純確定性,不進計分。
    official 欄位由 dedup_news 累計;單筆未去重者退回以來源分級(A=官方)即時判定。"""
    n = item.get("merged_n", 1)
    n = n if isinstance(n, int) and n > 0 else 1
    official = item.get("official")
    if not isinstance(official, bool):
        official = _news_source_grade(item) == "A"
    bits = []
    if n > 1:
        bits.append(f"獨立來源 {n}")
    if official:
        bits.append("含官方來源")
    return f"〔{'・'.join(bits)}〕" if bits else ""


def dedup_news(news: list[dict], similarity: float = 0.85) -> list[dict]:
    """
    去除重複 / 近似重複的新聞（同一事件常被多個 RSS 來源重貼）。
    規則：標題正規化（去空白、去標點、小寫）後完全相同 → 重複；
         或與已保留標題的 difflib 相似度 > similarity → 重複。
    重複時保留來源品質較高、摘要較完整者。
    """
    import difflib
    import re as _re
    import news_coverage as _coverage

    def _norm(t: str) -> str:
        t = (t or "").lower().strip()
        t = _re.sub(r"[\s　]+", "", t)
        # 只保留中英數，去掉所有標點符號
        t = _re.sub(r"[^\w一-鿿]", "", t)
        return t

    def _pub_key(item: dict) -> str:
        """發布者身分(用於算「獨立來源數」)。優先用 source_name(真正媒體,如「鉅亨」),
        其次 source(常是聚合器代號)。目的:同一媒體經多條查詢路徑(類股 feed vs 個股 Google)
        重貼,source 不同但 source_name 相同 → 視為同一來源、不灌水 merged_n(Codex review)。"""
        return _norm(str(item.get("source_name") or item.get("source") or ""))

    def _pub_set(item: dict) -> set:
        """該項已知的發布者集合。dedup_news 會被 pipeline 多次呼叫(逐步併入新聞群組),
        故把集合持久化在項目的 _pub_keys 上;後續呼叫從中還原,避免 merged_n 被重置縮水
        (Codex review 第二輪)。無 _pub_keys 者退回單一發布者。"""
        existing = item.get("_pub_keys")
        if isinstance(existing, (list, set)) and existing:
            return {str(x) for x in existing}
        return {_pub_key(item)}

    kept: list[dict] = []
    kept_norms: list[str] = []
    # 每個保留項「已合併的發布者身分集合」,與 kept/kept_norms 同步索引;merged_n = 其基數。
    kept_pubs: list[set] = []
    dropped = 0
    for n in news:
        nt = _norm(n.get("title", ""))
        if not nt:
            # 無標題者不參與比對,但三個平行陣列仍同步 append(空 norm 永不匹配),
            # 維持 kept / kept_norms / kept_pubs 索引一致(否則 dup_index 會錯位)。
            kept.append(n)
            kept_norms.append("")
            kept_pubs.append(_pub_set(n))
            continue
        dup_index = None
        for index, kn in enumerate(kept_norms):
            if not kn:
                continue
            if nt == kn:
                dup_index = index
                break
            # 近似比對：兩者較短長度 >= 8 才比，避免短標題誤殺
            if (min(len(nt), len(kn)) >= 8
                    and difflib.SequenceMatcher(None, nt, kn).ratio() > similarity):
                dup_index = index
                break
        if dup_index is not None:
            # 不論保留哪一版,都把 company_label 補到留下來的那筆,
            # 避免個股新聞因去重而失去標籤、從「科技板塊脈動」消失(rank 5)。
            # world_cat 同理:同一事件常同時出現在一般來源(如 Google-地緣)與世界來源,
            # 若被一般來源那版吃掉、世界標記跟著消失,「世界大事速覽」取材段就漏事件
            # (Codex review)。
            label = n.get("company_label") or kept[dup_index].get("company_label")
            wcat = n.get("world_cat") or kept[dup_index].get("world_cat")
            coverage = sorted(set(_coverage.buckets(n)) |
                              set(_coverage.buckets(kept[dup_index])))
            # 混源重複(一版來自市場來源、一版來自世界來源)→ 標 world_and_market:
            # 該事件同屬兩個版面,市場配額桶與世界取材段都要收,不可因帶 world_cat
            # 就被市場桶排除(Codex review 第二輪:否則跨源大事件從市場桶消失)。
            mixed = bool(n.get("world_cat")) != bool(kept[dup_index].get("world_cat"))
            # G6 可信度確定性欄位:merged_n=去重後「不同發布者」數(非則數),含官方=任一版 grade A。
            # 集合存 kept_pubs(平行陣列,替換保留版本也不遺失累計)並持久化到 _pub_keys
            # (dedup 會被多次呼叫,持久化才不會在下一輪縮水);官方旗標取 OR。
            kept_pubs[dup_index] |= _pub_set(n)
            combined_official = (bool(kept[dup_index].get("official"))
                                 or bool(n.get("official"))
                                 or _news_source_grade(kept[dup_index]) == "A"
                                 or _news_source_grade(n) == "A")
            if _news_keep_score(n) > _news_keep_score(kept[dup_index]):
                kept[dup_index] = n
                kept_norms[dup_index] = nt
            kept[dup_index]["merged_n"] = len(kept_pubs[dup_index])
            kept[dup_index]["_pub_keys"] = sorted(kept_pubs[dup_index])
            kept[dup_index]["official"] = combined_official
            if coverage:
                kept[dup_index]["coverage_buckets"] = coverage
            if label and not kept[dup_index].get("company_label"):
                kept[dup_index]["company_label"] = label
            if wcat and not kept[dup_index].get("world_cat"):
                kept[dup_index]["world_cat"] = wcat
            if mixed:
                kept[dup_index]["world_and_market"] = True
            dropped += 1
            continue
        kept.append(n)
        kept_norms.append(nt)
        kept_pubs.append(_pub_set(n))
    print(f"[news] 去重：{len(news)} → {len(kept)} 則（移除 {dropped} 則重複）")
    return kept


FED_OFFICIALS = [
    "Powell", "Williams", "Jefferson", "Bowman", "Cook", "Kugler", "Waller",
    "Barr", "Brainard", "Daly", "Bostic", "Mester", "Kashkari", "Goolsbee",
    "Schmid", "Logan", "Musalem", "Hammack", "鮑爾", "鮑威爾",
    "Warsh",   # 新任聯準會主席
]


FED_EVENTS = [
    "FOMC", "聯準會", "Federal Reserve", "Fed minutes", "Fed Funds",
    "rate decision", "升息", "降息", "利率決議", "點陣圖", "dot plot",
    "Jackson Hole",
]


ECON_DATA = [
    "CPI", "PPI", "PCE", "核心通膨", "core inflation",
    "Nonfarm Payrolls", "非農", "就業數據", "失業率", "Initial Jobless Claims",
    "ADP", "JOLTS",
    "GDP", "ISM", "PMI", "零售銷售", "Retail Sales", "Consumer Confidence",
    "Durable Goods", "Industrial Production",
]


GEOPOLITICAL = [
    "出口管制", "晶片禁令", "對中制裁", "Entity List", "EAR",
    "川習會", "Trump Xi", "貿易戰", "tariff", "關稅",
    "台海", "Taiwan Strait", "封鎖", "demilitarized",
    "伊朗", "以色列", "烏克蘭", "戰爭", "war",
    # 中國政策/對台 深度
    "中共", "中國商務部", "China MOFCOM", "中國國台辦",
    "解放軍", "PLA", "海警", "軍演", "drill",
    "稀土", "rare earth", "中國新晶片", "華為", "SMIC", "Huawei",
    "禁止出口", "ban", "黑名單", "blacklist",
    "晶片補貼", "CHIPS Act",
    "央行降準", "RRR", "China stimulus", "人民幣",
]


GEOPOLITICAL_CRITICAL = [
    "川習會", "川習", "Trump Xi", "拜習", "習拜",
    "台海", "Taiwan Strait", "對台", "台灣問題", "一個中國", "侵台", "封島",
    "軍演", "對台軍售", "解放軍", "PLA", "封鎖", "blockade",
    "出口管制", "晶片禁令", "Entity List", "對中制裁", "EAR",
    "戰爭", "war",
]


TW_POLICY = [
    "金管會", "央行", "升息", "降息", "外資匯入", "外匯存底",
    "產創條例", "新青安", "科專",
    "TSMC", "台積電", "艾司摩爾", "ASML",
]


def _matches_any(text: str, keywords: list[str]) -> Optional[str]:
    """文本是否包含任一關鍵字，回傳命中的那個。"""
    if not text:
        return None
    import re as _re
    lower = text.lower()
    for kw in keywords:
        needle = kw.lower()
        # 英文關鍵字用 word boundary，避免 war 誤中 Warren / software / hardware。
        # 中文與混合中文詞維持 substring，才能命中「台海軍演」等自然語句。
        if _re.fullmatch(r"[a-z0-9][a-z0-9 ._/-]*", needle):
            pattern = rf"(?<![a-z0-9]){_re.escape(needle)}(?![a-z0-9])"
            matched = _re.search(pattern, lower) is not None
        else:
            matched = needle in lower
        if matched:
            return kw
    return None


def _strip_html(html: str) -> str:
    """簡單去 HTML tag，不依賴 BeautifulSoup。"""
    import re as _re
    # 移除 <script>...</script> 與 <style>...</style>
    html = _re.sub(r"<script[^>]*>.*?</script>", "", html, flags=_re.DOTALL | _re.IGNORECASE)
    html = _re.sub(r"<style[^>]*>.*?</style>", "", html, flags=_re.DOTALL | _re.IGNORECASE)
    # 移除其他 tag
    html = _re.sub(r"<[^>]+>", " ", html)
    # HTML entities
    html = html.replace("&nbsp;", " ").replace("&amp;", "&")
    html = html.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    # 壓縮空白
    html = _re.sub(r"\s+", " ", html).strip()
    return html


def classify_news_importance(news: list[dict]) -> list[dict]:
    """
    對每則新聞自動分類與評重要性：
      importance: "critical" (★★★) / "high" (★★) / "normal"
      category:   "fed" / "econ_data" / "geo" / "tw_policy" / "general"

    Critical 事件會在 prompt 中被特別標記，並可選擇抓全文（Task A）。
    """
    for n in news:
        text = f"{n.get('title','')} {n.get('summary','')}"
        n["source_grade"] = _news_source_grade(n)

        fed_hit = _matches_any(text, FED_OFFICIALS) or _matches_any(text, FED_EVENTS)
        econ_hit = _matches_any(text, ECON_DATA)
        geo_crit_hit = _matches_any(text, GEOPOLITICAL_CRITICAL)
        geo_hit = geo_crit_hit or _matches_any(text, GEOPOLITICAL)
        tw_hit = _matches_any(text, TW_POLICY)

        # 評分邏輯：Fed/數據/重大地緣 → critical；一般地緣/台灣政策 → high
        if fed_hit and econ_hit:
            # Fed + 經濟數據同時出現 = 政策轉向訊號
            n["importance"] = "critical"
            n["category"] = "fed_econ"
            n["keyword"] = f"{fed_hit} + {econ_hit}"
        elif fed_hit:
            n["importance"] = "critical"
            n["category"] = "fed"
            n["keyword"] = fed_hit
        elif econ_hit:
            n["importance"] = "critical"
            n["category"] = "econ_data"
            n["keyword"] = econ_hit
        elif geo_crit_hit:
            # 直接牽動台股的重大地緣事件（川習會、台海、出口管制…）→ critical
            n["importance"] = "critical"
            n["category"] = "geo_critical"
            n["keyword"] = geo_crit_hit
        elif geo_hit:
            n["importance"] = "high"
            n["category"] = "geo"
            n["keyword"] = geo_hit
        elif tw_hit:
            n["importance"] = "high"
            n["category"] = "tw_policy"
            n["keyword"] = tw_hit
        elif n.get("company_label") and (
                _matches_any(text, NEWS_POSITIVE_TERMS)
                or _matches_any(text, NEWS_NEGATIVE_TERMS)):
            # 重點公司 + 具體催化(訂單/上修/財報/砍單/出口管制…)→ 升級為 high。
            # 讓它抓全文並進入高權重區,避免「科技板塊脈動」退化成只報股價+B級低信心(rank 7)。
            n["importance"] = "high"
            n["category"] = "company_catalyst"
            n["keyword"] = (_matches_any(text, NEWS_POSITIVE_TERMS)
                            or _matches_any(text, NEWS_NEGATIVE_TERMS))
        else:
            n["importance"] = "normal"
            n["category"] = "general"
            n["keyword"] = ""

    # 統計
    crit = sum(1 for n in news if n.get("importance") == "critical")
    high = sum(1 for n in news if n.get("importance") == "high")
    print(f"[news] 重要性分類完成：critical={crit}, high={high}, normal={len(news)-crit-high}")
    return news


def _is_low_value_tech_headline(n: dict) -> bool:
    """純分析師喊價或純籌碼流向、且不含具體催化的非 A 級新聞 → 視為科技脈動雜訊。
    僅用於過濾「重點公司新聞」餵 LLM 的取材(這類內容股價表/法人表已涵蓋),
    不更動 importance/ranking 等任何計分。"""
    text = f"{n.get('title', '')} {n.get('summary', '')}"
    grade = n.get("source_grade") or _news_source_grade(n)
    if grade == "A":                       # 官方來源(SEC/MOPS/TWSE…)一律保留
        return False
    if _matches_any(text, TECH_GATE_CATALYST):
        return False
    return bool(_matches_any(text, TECH_NEWS_ANALYST_NOISE)
                or _matches_any(text, TECH_NEWS_CHIPFLOW_NOISE))
