# -*- coding: utf-8 -*-
"""**第八段回到「哪間公司昨天發生什麼事」的寫法**(2026-08-18 使用者定案)。

使用者連續三封信反映同一件事:特化路徑跑成之後,第八段變成一整片
「偏多 / 偏空(量級中等、1-3 個月)」,而他要的是舊版那種
「公司(代號,簡介):昨天發生的那則新聞」當小標題、底下接一段敘述,
再底下才是傳導與什麼會推翻它。原話:

    「小標題先客觀敘述昨日發生什麼事情 發生什麼新聞 哪間公司發生什麼新聞
      … 只是在公司發生新聞的下方可以寫一段類似目前的傳導跟什麼會推翻它
      … 而不是整篇都是偏多什麼的 用成原本的科技類股以及其他類股的寫法」

這裡釘住的是**小標題的來源**:公司來自新聞自己的編輯標註實體與當日
universe 的宣告資料,事件來自新聞標題 —— 兩者都是客觀事實,不是模型的
判斷。模型的判斷在下面那一段敘述裡。編一個公司名比沒有小標題糟得多,
所以查不到主體時要退回沒有標題的排版。
"""
import analysis_render as ar
import analysis_render_depth as ard


def _packet(**over):
    pk = {
        "tw_universe": [
            {"code": "2330", "name": "台積電", "industry": "半導體業",
             "desc": "晶圓代工龍頭"},
            {"code": "2317", "name": "鴻海", "industry": "其他電子業",
             "desc": "鴻海 — 其他電子業"},
            {"code": "2882", "name": "國泰金", "industry": "金融保險業",
             "desc": "壽險與銀行雙引擎金控"},
        ],
        "news": [
            {"source_item_id": "n1", "title": "台積電 CoWoS 產能明年再擴一倍",
             "entities": ["2330"]},
            {"source_item_id": "n2", "title": "國泰金上半年獲利創同期新高",
             "entities": ["2882"]},
            {"source_item_id": "n3", "title": "輝達財測優於市場預期",
             "entities": ["NVDA"]},
            {"source_item_id": "n4", "title": "鴻海 AI 伺服器機櫃出貨上修",
             "entities": ["2317"]},
        ],
    }
    pk.update(over)
    return pk


def _news(sid, asset, **over):
    n = {"source_item_id": sid,
         "why_it_matters": "這件事之所以重要的一段敘述。",
         "mechanism_steps": [{"from_what": "起點", "to_what": "終點"}],
         "invalidation_signal": "什麼情況代表判斷錯了",
         "affected_assets": [{"asset_id": asset, "direction": "bullish",
                              "magnitude_band": "moderate", "horizon": "1-3 個月",
                              "first_order_effect": "一階影響",
                              "second_order_effect": "二階影響"}]}
    n.update(over)
    return n


# ------------------------------------------------------------ 小標題的內容

def test_the_heading_names_the_company_and_what_happened():
    """小標題 = **公司(代號,簡介):**,昨天發生什麼事寫在下面那一段。

    **2026-08-18 第三次校正**:使用者貼了舊信要求照做 —— 公司、昨天發生
    什麼事、分析在**同一段**裡,底下才接傳導。第二次校正時我把小標題拆成
    獨立一行,而他要的是舊信那種一段到底的寫法。
    """
    line = ard._news_line(_news("n1", "2330"), _packet()).splitlines()[0]
    assert line.startswith("**台積電（2330,晶圓代工龍頭）**:"), line
    assert "CoWoS 產能明年再擴一倍。" in line, line
    assert "這件事之所以重要的一段敘述。" in line, line


def test_the_company_name_is_not_printed_twice():
    """標題開頭與公司同名時只削開頭 —— 「台積電(2330,…):**台積電** CoWoS…」
    是同一個名字印兩次。"""
    md = ard._news_line(_news("n1", "2330"), _packet())
    assert md.count("台積電") == 1, md


def test_a_short_remainder_keeps_the_whole_headline():
    """削過頭比重複更糟:剩下的不成句就整條留著。"""
    pk = _packet(news=[{"source_item_id": "n1", "title": "台積電法說",
                        "entities": ["2330"]}])
    line = ard._news_line(_news("n1", "2330"), pk).splitlines()[0]
    assert "台積電法說" in line, line


def test_the_fallback_blurb_does_not_repeat_the_name():
    """`desc` 查不到時是「<名稱> — <產業別>」的退化字串 ——
    放進括號會排成「鴻海(2317,鴻海 — 其他電子業)」。"""
    head = ard._news_line(_news("n4", "2317"), _packet()).splitlines()[0]
    assert head.startswith("**鴻海（2317,其他電子業）**:"), head


def test_an_index_is_not_a_subject():
    """「費半」「加權指數」不是「哪間公司昨天發生什麼事」的答案。

    總經/指數新聞仍然有小標題 —— 使用者要的是「小標題先客觀敘述昨日
    發生什麼事情」,新聞標題自己就是那件事。這條擋的是**把指數當公司**:
    「費半:費半收漲 2.1%」那種寫法會讓讀者以為費半是一間公司。
    """
    pk = _packet(news=[{"source_item_id": "x1", "title": "費半收漲 2.1%",
                        "entities": ["費半"]}])
    md = ard._news_line(_news("x1", "費半"), pk)
    assert not md.startswith("**"), md
    assert md.startswith("費半收漲 2.1%。"), md
    assert ard.news_subject(_news("x1", "費半"), pk)["name"] == "", md


def test_no_subject_means_no_invented_heading():
    """查不到主體就退回沒有小標題的排版 —— 編一個公司名比沒有標題糟得多。"""
    line = ard._news_line(_news("zz", "NOPE"), _packet())
    assert not line.startswith("**"), line


def test_the_subject_comes_from_the_editorial_entities_first():
    """主體優先用新聞自己的**編輯標註實體**,而不是模型宣告的受影響標的
    —— 後者可能是傳導到的對象,不是這則新聞在講的那間公司。"""
    pk = _packet(news=[{"source_item_id": "n9", "title": "鴻海機櫃出貨上修",
                        "entities": ["2317"]}])
    head = ard._news_line(_news("n9", "2330"), pk).splitlines()[0]
    assert "鴻海" in head and "台積電" not in head, head
    assert head.startswith("**鴻海（2317"), head


# ------------------------------------------------------------ 敘述與兩行標籤

def test_the_narrative_sits_under_the_heading():
    """小標題底下是敘述,再底下才是傳導與什麼會推翻它。

    2026-08-18 第三次校正:公司與新聞同一段(舊信的寫法),所以第一行
    就是完整的那一段;傳導與什麼會推翻它仍各自成行。
    """
    lines = ard._news_line(_news("n1", "2330"), _packet()).splitlines()
    assert ("CoWoS 產能明年再擴一倍。這件事之所以重要的一段敘述。"
            in lines[0]), lines
    assert lines[1].strip().startswith("- 傳導:"), lines
    assert lines[2].strip().startswith("- 什麼會推翻它:"), lines


def test_no_direction_words_per_asset():
    """**使用者的原話:「不是整篇都是偏多什麼的」。**

    方向/幅度/時間窗整組不排進逐則新聞;它們在「各標的合計影響」那一段
    合計後出現一次。一階/二階影響留著 —— 那是使用者要的「後續影響」。
    """
    line = ard._news_line(_news("n1", "2330"), _packet())
    assert "偏多" not in line and "中等" not in line and "1-3 個月" not in line, line
    assert "一階影響" in line and "二階影響" in line, line


# ------------------------------------------------------------ 兩段的分法

def test_the_two_subsections_follow_the_declared_industry():
    """科技/其他的分法來自 `tw_universe` 的產業別與 registry 的宣告,
    不是模型貼的標籤。"""
    import industry_class as ic
    assert ic.is_tech_industry("半導體業")
    assert not ic.is_tech_industry("金融保險業")
    assert ard.is_tech({"industry": "其他電子業", "name": "鴻海"})
    assert not ard.is_tech({"industry": "金融保險業", "name": "國泰金"})


def test_a_declared_foreign_equity_counts_as_tech():
    """`instrument_registry` 收的外國個股就是半導體鏈與大型科技股
    (那是它自己寫下的收錄範圍),所以宣告本身就是科技的依據 ——
    不需要第二張名單。指數與 ETF 不算(它們不是公司新聞的主體)。"""
    assert ard.is_tech({"industry": "", "name": "NVDA"})
    assert not ard.is_tech({"industry": "", "name": "QQQ"})
    assert not ard.is_tech({"industry": "", "name": "沒宣告過的名字"})


def test_the_tech_industry_set_has_exactly_one_copy():
    """**判準只有一份。** 兩份集合分歧會讓同一檔股票在「補非科技類股
    新聞」與「第八段排版」兩處被分到不同類股。"""
    import morning_report as mr
    import industry_class as ic
    assert mr._TECH_INDUSTRIES_FOR_SECTOR_NEWS is ic.TECH_INDUSTRIES


def test_the_reservations_are_not_filed_under_a_sector():
    """「傳導未完成 / 看過但未展開」講的是整段的取捨 ——
    掛進「其他類股」會讓讀者以為只有那一類有缺。"""
    import fixtures_analysis as fx
    obj = fx.valid_analysis()
    obj["dismissed_events"] = [{"cluster_id": "c9", "why_not_material": "重複報導"}]
    text = ar.render(obj)
    i = text.index(ar.SUBSECTION_NOTES)
    assert "今日看過但未展開" in text[i:], text[i:i + 200]
    assert text.index(ar.SUBSECTION_OTHER) < i, text


# ------------------------------------------------- 外審 2026-08-18 的兩個 P2

def test_the_heading_and_the_narrative_are_separate_html_blocks():
    """**信裡看到的才算數。**

    `_md_to_html` 是逐行的:相鄰的非空行會被併成同一個 `<p>`,於是
    「**小標題** 敘述接在後面」—— 而使用者要的是「在公司發生新聞的
    **下方**寫一段」。這條穿過真正的 HTML 轉譯器,不只看中間的 Markdown。
    """
    import render_utils as ru
    html = ru._md_to_html(ard._news_line(_news("n1", "2330"), _packet()))
    assert ("<p><strong>台積電（2330,晶圓代工龍頭）</strong>:"
            "CoWoS 產能明年再擴一倍。這件事之所以重要的一段敘述。") in html, html
    assert "</p>" in html.split("這件事之所以重要的一段敘述。")[1][:40], html
    # 傳導那幾行仍要是自己的清單,不得被吸進同一個 <p>
    assert "<ul>" in html and "<li>傳導:起點 → 終點</li>" in html, html


def test_the_editorial_entity_wins_even_when_it_is_a_foreign_equity():
    """**候選順序不能被「哪張表查得到」壓過。**

    先前是「先拿所有候選掃 universe,掃不到再問 registry」——
    `entities=["ASML"]` 而 `affected_assets` 是 2330 時,ASML 不在當日
    台股 universe 裡,於是小標題寫成台積電,而標題與編輯標註講的都是 ASML。
    """
    pk = _packet(news=[{"source_item_id": "n5", "title": "ASML 上修全年 EUV 出貨",
                        "entities": ["ASML"]}])
    sub = ard.news_subject(_news("n5", "2330"), pk)
    assert sub["name"] == "ASML", sub
    head = ard._news_line(_news("n5", "2330"), pk).splitlines()[0]
    assert "台積電" not in head and "ASML" in head, head


# ------------------------------------------- 九、今日市場關注與預測(合併段)

def test_a_repeated_sentence_is_written_once_in_the_market_section():
    """**內文重複的就不用一直寫了**(2026-08-18 使用者原話)。

    五段併成一段之後,同一句「費半走強帶動台股電子」會在綜合、連動、
    台股三個小段各出現一次 —— 那正是使用者要拿掉的東西。逐字重複才濾,
    不做語意判斷(語意去重會把兩件不同的事誤刪)。
    """
    import fixtures_analysis as fx
    obj = fx.valid_analysis()
    dup = "費半走強帶動台股電子開盤。"
    obj["global_market"]["summary"] = dup
    obj["taiwan_market"]["summary"] = dup
    text = ar.render(obj)
    assert text.count(dup) == 1, text[text.index(ar.SECTION_MARKET):]


def test_a_different_sentence_is_not_swallowed_by_the_dedup():
    """反向:去重不得把**不同的**句子吃掉(誤刪比重複糟)。"""
    import fixtures_analysis as fx
    obj = fx.valid_analysis()
    obj["global_market"]["summary"] = "美股收紅,費半領漲。"
    obj["taiwan_market"]["summary"] = "台股量能回升,外資轉買。"
    text = ar.render(obj)
    assert "美股收紅,費半領漲。" in text
    assert "台股量能回升,外資轉買。" in text


def test_each_market_subsection_is_one_html_list():
    """小標題與它底下那幾行是**一個區塊**。

    `_md_to_html` 逐行處理,遇到空行就收掉 `<ul>` —— 逐條之間空一行會讓
    每一條各自變成一個清單(2026-08-18 自測看得到)。
    """
    import fixtures_analysis as fx
    import render_utils as ru
    text = ar.render(fx.valid_analysis())
    seg = text[text.index("### " + ar.SUBSECTION_TW):]
    seg = seg[:seg.index("### " + ar.SUBSECTION_PRICED)]
    html = ru._md_to_html(seg)
    assert html.count("<ul>") == 1, html
    assert html.count("<li>") == 3, html


# ------------------------------- 第三次校正:側寫 / 來源 / 佐證等級與信心

def test_the_hand_written_company_profile_survives():
    """**五十檔手寫的業務簡介不得被丟掉。**

    `tw_universe` 的 `desc` 長成「台積電 — 全球晶圓代工龍頭,先進製程…」,
    開頭就是公司名。上一版用「開頭是公司名就丟掉」擋退化字串,結果把
    整批手寫側寫一起丟了,信裡只剩「半導體業」——而使用者要的正是
    「全球最大晶圓代工廠,先進製程 N3/N2 與 CoWoS 封裝主導 AI 晶片供給」
    這種一句話。
    """
    row = {"name": "台積電", "industry": "半導體業",
           "desc": "台積電 — 全球晶圓代工龍頭,先進製程 (3nm/5nm) 市佔超過 90%"}
    assert ard._blurb(row) == "全球晶圓代工龍頭,先進製程 (3nm/5nm) 市佔超過 90%"
    # 反向:退化字串「<名稱> — <產業別>」仍然只留產業別,不重複公司名。
    assert ard._blurb({"name": "鴻海", "industry": "其他電子業",
                       "desc": "鴻海 — 其他電子業"}) == "其他電子業"


def test_a_foreign_company_gets_its_declared_profile():
    """外國個股沒有 universe 那種資料源 —— 側寫是**宣告**(`company_profiles`)。
    沒宣告就只寫代號,不編造這家公司在做什麼。"""
    pk = {"news": [{"source_item_id": "f1", "title": "Q4 財報優於預期",
                    "entities": ["MSFT"]}]}
    sub = ard.news_subject(_news("f1", "MSFT"), pk)
    assert sub["label"].startswith("Microsoft（MSFT,"), sub
    assert "Azure" in sub["label"], sub
    # 宣告過是標的、但沒有側寫的名字:只寫代號
    assert ard.news_subject(_news("f2", "MTD"),
                            {"news": [{"source_item_id": "f2",
                                       "entities": ["MTD"]}]})["label"] == "MTD"


def test_the_display_name_is_not_printed_twice():
    """主體是代號(MSFT)而標題寫「Microsoft Q4 財報」——
    只比對代號的話會排成「Microsoft（MSFT,…）:Microsoft Q4 財報…」。"""
    pk = {"news": [{"source_item_id": "f1", "title": "Microsoft Q4 財報優於預期",
                    "entities": ["MSFT"]}]}
    line = ard._news_line(_news("f1", "MSFT"), pk).splitlines()[0]
    assert line.count("Microsoft") == 1, line


def test_the_publisher_is_named():
    """`（鉅亨網）` —— 發布者是**新聞自己帶的欄位**,不是模型寫的。"""
    pk = {"news": [{"source_item_id": "p1", "title": "熊本廠測得強震",
                    "source_name": "鉅亨網", "entities": ["2330"]}],
          "tw_universe": [{"code": "2330", "name": "台積電",
                           "industry": "半導體業", "desc": "晶圓代工龍頭"}]}
    assert "（鉅亨網）" in ard._news_line(_news("p1", "2330"), pk)


def test_an_aggregator_alias_is_not_printed_as_a_publisher():
    """`Google:2330`、`類股-金融-台股` 是內部標籤,印給讀者看沒有意義。"""
    for alias in ("Google:2330", "類股-金融-台股"):
        pk = {"news": [{"source_item_id": "p2", "title": "某公司發布財報",
                        "source": alias, "entities": ["2330"]}],
              "tw_universe": [{"code": "2330", "name": "台積電",
                               "industry": "半導體業", "desc": "晶圓代工龍頭"}]}
        line = ard._news_line(_news("p2", "2330"), pk)
        assert alias not in line, line
        assert "（" not in line.split("**:")[1][:40], line


def _pk_conf(**cluster):
    """一群一則新聞的 packet;`cluster` 覆寫那一群的確定性欄位。"""
    c = {"cluster_id": "cluster:c1", "member_source_ids": ["c1"],
         "official": False, "independent_sources": 1}
    c.update(cluster)
    return {"news": [{"source_item_id": "c1", "title": "某事發生",
                      "source_grade": "A", "entities": ["2330"]}],
            "news_clusters": {"clusters": [c]},
            "tw_universe": [{"code": "2330", "name": "台積電",
                             "industry": "半導體業", "desc": "晶圓代工龍頭"}]}


def test_the_tag_says_the_grade_and_how_many_independent_sources():
    """`[A 級・2 家獨立報導]` —— **兩件事不同軸**:

    等級講「最好的那個來源有多可靠」,第二格講「有幾家互相獨立地說同一件事」。
    一家 A 級媒體獨家 = `[A 級・僅單一來源]`,那正是讀者需要看見的組合。
    """
    n = _news("c1", "2330")
    assert "[A 級・3 家獨立報導]" in ard._news_line(n, _pk_conf(independent_sources=3))
    assert "[A 級・2 家獨立報導]" in ard._news_line(n, _pk_conf(independent_sources=2))
    assert "[A 級・僅單一來源]" in ard._news_line(n, _pk_conf(independent_sources=1))
    # **0 不是 1**(外審 2026-08-18 第四輪):查不到發布者、或全是聚合器
    # 轉載時 `independent_sources` 是 0,而那一群可能有好幾則 ——
    # 寫成「僅單一來源」是說了一件沒發生的事。
    assert "[A 級・來源獨立性未驗證]" in ard._news_line(
        n, _pk_conf(independent_sources=0, size=3,
                    aggregator_only_sources=3)), "0 被當成 1"
    assert "[A 級・官方公告]" in ard._news_line(n, _pk_conf(official=True))


def test_the_tag_does_not_claim_confidence_in_the_analysis():
    """**佐證數不是分析的可信度**(外審 2026-08-18,第三次指正)。

    三家獨立報導同一件事,不代表底下那段推論成立 —— 把它寫成「信心:高」
    會讓一段推測性的分析看起來被驗證過。這份報告沒有 Python 算得出來的
    「分析信心」,所以就不寫一個;分析可不可信由「傳導」與
    「什麼會推翻它」自己說。
    """
    line = ard._news_line(_news("c1", "2330"), _pk_conf(independent_sources=3))
    assert "信心" not in line, line
    assert "傳導:" in line and "什麼會推翻它:" in line, line


def test_the_tag_comes_only_from_the_packet():
    """**模型的欄位不得影響這一格。**

    三版都被駁回過:模型自填 0–1、由鏈的欄位推導、由 packet 推導但仍叫
    「信心」。這條釘住第一件事 —— 輸入只有 packet。
    """
    import analysis_schema as sch
    props = sch.ANALYSIS_OUTPUT_SCHEMA["properties"]["top_news_analysis"][
        "items"]["properties"]
    assert "confidence" not in props, "模型又拿回這一格的決定權"
    pk = _pk_conf(independent_sources=2)
    for over in ({"confidence": 0.99}, {"magnitude_band": "unknown"},
                 {"corroboration_assessment": "single_source"},
                 {"mechanism_steps": [{"from_what": "A", "to_what": "B",
                                       "step_type": "unknown"}]},
                 {"mechanism_steps": []}):
        line = ard._news_line(_news("c1", "2330", **over), pk)
        assert "[A 級・2 家獨立報導]" in line, (over, line)


def test_the_model_no_longer_writes_the_corroboration_into_the_sentence():
    """句尾那個「(單一來源)」來自模型的 `corroboration_assessment`,
    而 packet 分群時就算好同一件事(schema 自己寫著「以 EVIDENCE 為準」)。
    **兩處寫同一件事、其中一處是模型抄的** —— 留 packet 那份。"""
    line = ard._news_line(
        _news("c1", "2330", corroboration_assessment="single_source"),
        _pk_conf(independent_sources=3))
    assert "（單一來源）" not in line, line
    assert "3 家獨立報導" in line, line


def test_no_cluster_evidence_means_no_claim():
    """查不到這則屬於哪一群就**不寫第二格** —— 沒有依據時給一個標籤
    是最糟的那種假精確。"""
    # 兩種「查不到」都要測:**沒有任何群**、以及**有群但不含這一則**。
    # 只測前者的話,反例改的是迴圈內的程式碼而迴圈根本沒跑 —— 突變驗證
    # 當場抓到那條測試分不出勝負。
    empty = _pk_conf()
    empty["news_clusters"] = {"clusters": []}
    other = _pk_conf(member_source_ids=["zz"], independent_sources=9)
    for pk in (empty, other):
        line = ard._news_line(_news("c1", "2330"), pk)
        assert "獨立報導" not in line and "單一來源" not in line, line
        assert "[A 級]" in line, line


def test_each_half_of_the_tag_can_be_missing_on_its_own():
    """**缺一半就只寫另一半**,不用一半頂替另一半。"""
    pk = _pk_conf(independent_sources=3)
    pk["news"][0].pop("source_grade")
    line = ard._news_line(_news("c1", "2330"), pk)
    assert "[3 家獨立報導]" in line, line


def test_the_publisher_sits_inside_the_sentence():
    """標題自帶句末標點時,出處要接在標點**之前**(外審 2026-08-18 P3)——
    否則排成「公司公布財報。(鉅亨網)。」。**全形半形都要收**。"""
    tw = [{"code": "2330", "name": "台積電", "industry": "半導體業",
           "desc": "晶圓代工龍頭"}]
    for mark in ("。", "！", "？", "!", "?"):
        pk = {"news": [{"source_item_id": "d1", "title": "台積電公布財報" + mark,
                        "source_name": "鉅亨網", "entities": ["2330"]}],
              "tw_universe": tw}
        line = ard._news_line(_news("d1", "2330"), pk).splitlines()[0]
        assert "公布財報（鉅亨網）。" in line, (mark, line)
        assert mark + "（" not in line, (mark, line)
