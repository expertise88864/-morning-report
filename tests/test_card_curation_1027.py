"""使用者 2026-10-27 看了當天晨報的兩個問題:新聞標題錯置、題材都是那幾間科技公司。

案例取自 10/27 實際寄出的信(state/emails/2026-10-27、state/news_memory/2026-10-27)。
"""
import analysis_render as ar
import analysis_render_depth as ard
import card_curation as cc
import fixtures_analysis as fx
import reader_selection as rs

UNIVERSE = [
    {"code": "2330", "name": "台積電", "industry": "半導體業", "desc": "台積電 — 全球最大晶圓代工廠"},
    {"code": "2891", "name": "中信金", "industry": "金融保險業", "desc": "中信金 — 金控"},
    {"code": "2618", "name": "長榮航", "industry": "航運業", "desc": "長榮航 — 航空公司"},
    {"code": "4958", "name": "臻鼎-KY", "industry": "電子零組件業", "desc": "臻鼎-KY — PCB 大廠"},
    {"code": "2882", "name": "國泰金", "industry": "金融保險業", "desc": "國泰金 — 金控"},
    {"code": "2609", "name": "陽明", "industry": "航運業", "desc": "陽明 — 貨櫃航商"},
    {"code": "2002", "name": "中鋼", "industry": "鋼鐵工業", "desc": "中鋼 — 鋼鐵廠"},
]
LONG = "，這是卡片自己的解讀，長度足以判斷它到底在寫哪一件事。"


def card(sid, why, assets=()):
    return {"source_item_id": sid, "why_it_matters": why,
            "affected_assets": [{"asset_id": a, "direction": "bullish", "first_order_effect": e}
                                for a, e in assets]}


def packet(*news):
    return {"news": [{"source_item_id": s, "title": t, "entities": list(e)} for s, t, *e in news],
            "tw_universe": UNIVERSE}


def test_roundup_headline_does_not_lend_its_first_company_to_another_story():
    """實信:〈台股盤前要聞〉點名美光,卡片寫的卻是公股投信整併 → 標成「美光(MU…)」還進了科技段。"""
    pk = packet(("r", "〈台股盤前要聞〉美光罷工危機解除、四大公股投信整併案拍板", "美光", "US-MICR"))
    c = card("r", "第一金、兆豐、合庫、華南四家公股投信整併案拍板，由第一金投信存續" + LONG,
             [("2891", "公股投信規模化會提高市場競爭"), ("TAIEX", "工業生產年增是實體面證據")])
    line = ard._news_line(c, pk)
    assert "美光" not in line.split("本報解讀")[0], line[:80]
    assert not rs.article_is_tech(c, pk), "沒有主角時看影響標的的產業(金融),不看標題的「美光」"


def test_single_story_headline_still_names_its_company():
    pk = packet(("z", "臻鼎-KY代子公司禮鼎公告購入機器設備"))
    c = card("z", "臻鼎-KY 代子公司禮鼎以合計約 25 億元購入兩批設備" + LONG, [("4958", "擴產進入執行")])
    assert ard._news_line(c, pk).startswith("**臻鼎-KY")
    short = card("z", "設備採購。", [("4958", "擴產")])   # 解讀太短無從判斷 → 沿用標題主角
    assert ard._news_line(short, pk).startswith("**臻鼎-KY")


def test_ai_vocabulary_in_the_headline_does_not_make_freight_news_tech():
    """實信:DHL「AI 產品運輸推高國際運價」進了科技段,影響的其實是長榮航。"""
    pk = packet(("d", "DHL大中華區執行長：AI產品運輸增長推高國際運價"))
    c = card("d", "空運與貨運需求面證據，與海運復航壓低成本的方向相反" + LONG,
             [("2618", "航空貨運需求與運價同步走揚")])
    assert not rs.article_is_tech(c, pk)


def test_one_theme_takes_at_most_two_cards_and_the_rest_become_one_line():
    """實信:AI 資本支出與融資一條主線佔了四五張(Anthropic×2、Meta、微軟)。"""
    titles = ["Meta擬增資950億美元支應AI基建", "Anthropic揭露十年逾5000億美元支出承諾",
              "微軟以自願離職對沖AI資本支出", "Alphabet上調資料中心資本支出"]
    pk = packet(*[(f"a{i}", t) for i, t in enumerate(titles)], ("s", "陽明11月兩條航線恢復紅海"))
    cards = [card(f"a{i}", t + LONG) for i, t in enumerate(titles)] + [card("s", "陽明復航紅海" + LONG)]
    selected, limited = rs.select_cards(cards, pk)
    themed = [c for c in selected if cc.theme_of(c, pk) == "ai_capex"]
    assert len(themed) == cc.THEME_CAP and cards[4] in selected
    extra = cc.overflow(selected, limited, pk)["ai_capex"]
    assert len(extra) == 2
    obj = fx.valid_analysis()
    obj["top_news_analysis"] = cards
    md = ar.render(obj, pk)
    assert "同主題（AI 資本支出循環）還有：" in md and "Alphabet上調資料中心資本支出" in md


def test_other_sectors_rotate_before_a_third_card_from_the_same_sector():
    titles = [("f0", "國泰金獲利上修"), ("f1", "中信金海外布局"), ("f2", "國泰金壽險配息"),
              ("f3", "中信金數位帳戶"), ("y", "陽明復航紅海"), ("c", "中鋼熱鐵磚投資")]
    assets = {"f0": "2882", "f1": "2891", "f2": "2882", "f3": "2891", "y": "2609", "c": "2002"}
    pk = packet(*[(s, t) for s, t in titles])
    cards = [card(s, t + LONG, [(assets[s], "營運面影響")]) for s, t in titles]
    order = [c["source_item_id"] for c in cc.sector_balanced(cards, pk)]
    assert order.index("y") < order.index("f2") and order.index("c") < order.index("f2")
    picked = [c["source_item_id"] for c in rs.select_cards(cards, pk)[0]]   # 選卡真的有接上
    assert picked.index("y") < picked.index("f2") and picked.index("c") < picked.index("f2")


def test_core_asset_is_printed_on_at_most_three_cards_a_day():
    """實信:一週 98 張卡有 29 張寫「對台積電(2330)而言」。被標題點名的不算額度。"""
    pk = packet(*[(f"n{i}", f"第{i}則與晶圓代工無直接關係的新聞") for i in range(5)],
                ("t", "台積電CoWoS產能再擴一倍"))
    budget = __import__("collections").Counter()
    cards = [card(f"n{i}", f"第{i}則新聞" + LONG, [("2330", "先進製程需求能見度延長")]) for i in range(5)]
    printed = sum("對台積電(2330)而言" in ard._news_line(c, pk, budget) for c in cards)
    assert printed == cc.CORE_REPEAT
    own = card("t", "台積電CoWoS產能再擴" + LONG, [("2330", "封裝營收上修")])
    assert "對台積電(2330)而言" in ard._news_line(own, pk, budget), "標題點名台積電的新聞不受額度限制"


def test_an_effect_that_admits_no_link_is_not_printed():
    """實信:「對中信金(2891)而言,可能整併案與中信金無關…」—— 那不是分析,是湊數。"""
    pk = packet(("g", "四大公股投信整併案拍板"))
    c = card("g", "公股投信整併" + LONG, [("2891", "可能整併案與中信金無關，但會提高市場競爭")])
    assert "對中信金" not in ard._news_line(c, pk)
    ok = card("g", "公股投信整併" + LONG, [("2891", "與財報無關的一次性收益會墊高本季獲利")])
    assert "對中信金(2891)而言" in ard._news_line(ok, pk), "句中別處的「無關」不算"
    # 外審 PR #29:否認後接了受詞、或句中有轉折,那一行仍有實質影響
    for eff in ("裁罰沒有直接影響產能，但一次性費用將降低本季獲利", "沒有直接影響產能，獲利率下滑兩個百分點",
                "一次性裁罰費用將降低本季獲利，對產能沒有直接影響。", "對獲利不利但對產能沒有直接影響。",
                "看不出獲利有下滑但對產能的直接影響"):
        mixed = card("g", "公股投信整併" + LONG, [("2891", eff)])
        assert "對中信金(2891)而言" in ard._news_line(mixed, pk), eff
    for eff in ("對中信金沒有直接影響", "本報看不出次級影響。", "可能對中信金無直接關聯。", "看不出直接影響"):
        assert not cc.keep_asset(card("g", "x"), {"asset_id": "2891", "first_order_effect": eff}, pk, None)


def test_declared_alias_counts_as_the_card_naming_its_company():
    """外審 PR #29:標題寫 Apple、卡片寫蘋果 —— 已宣告的別名組要承認是同一家。"""
    c = card("a", "蘋果上修本季營收展望，iPhone 拉貨力道比預期強" + LONG)
    assert cc.referenced(c, "", ("Apple",))
    assert cc.referenced(card("m", "美光罷工危機解除，記憶體供給不再受威脅" + LONG), "", ("Micron",))
    assert not cc.referenced(card("x", "MUST 這個英文字不是那家公司的代號" + LONG), "MU", ())
