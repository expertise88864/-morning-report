# -*- coding: utf-8 -*-
"""**標的的型別身分**(第二十四輪 P1-12 回歸)。

先前沒有「標的」這個型別:判斷散在四處各憑字串形狀猜(`_ASSET_LIKE` 的
正規式、一張手寫白名單、當日 universe、一張手寫概念黑名單)。兩個後果:

  * `AI` / `GPU` / `CHIP` 冒充過標的 —— 靠黑名單一個一個補;
  * 白名單裡的 `QQQ` / `SPY` / `TSM` **無條件繞過事件相關性檢查**,
    一則與它們無關的新聞可以宣稱「受影響標的:QQQ」而沒有人擋。

**兩個問題要分開問**:這是不是標的(型別,查表)、它跟這件事有沒有關係
(證據)。先前把「是已知標的」當成「與這件事有關」。

必補測試 16:無關新聞不得掛 QQQ／SPY 等 known assets。
"""
from __future__ import annotations

import analysis_validate as av
import instrument_registry as ir


def _packet():
    return {"tw_universe": [{"code": "2330"}, {"code": "2317"}]}


def _unrelated_news():
    """一則與美股 ETF 完全無關的新聞。"""
    return {"source_item_id": "n1", "title": "長榮海運運價指數連三週上漲",
            "summary": "SCFI 上揚", "entities": ["長榮"]}


# ── 型別:這是不是標的 ──

def test_canonical_ids_are_typed_by_market_and_class():
    pk = _packet()
    assert ir.resolve("2330", pk) == ("TW:EQUITY:2330", ir.EQUITY)
    assert ir.resolve("QQQ", pk) == ("US:ETF:QQQ", ir.ETF)
    assert ir.resolve("TSM", pk) == ("US:EQUITY:TSM", ir.EQUITY)
    assert ir.resolve("TAIEX", pk) == ("TW:INDEX:TAIEX", ir.INDEX)
    # 別名指到同一個 canonical id —— 「費半」與 `SOX` 不是兩個標的
    assert ir.resolve("費半", pk)[0] == ir.resolve("SOX", pk)[0]
    assert ir.resolve("加權指數", pk)[0] == ir.resolve("TAIEX", pk)[0]


def test_concepts_and_fake_codes_are_not_instruments():
    pk = _packet()
    for junk in ("AI", "GPU", "CHIP", "999999", "", None):
        assert ir.resolve(junk, pk) == (None, None), junk


def test_tw_code_must_exist_in_todays_universe():
    assert ir.resolve("2330", _packet())[1] == ir.EQUITY
    assert ir.resolve("9999", _packet()) == (None, None)
    # 拿不到 universe 時**不判定**(降級不誤擋)
    # **相容出口預設 fail-closed**(第二十八輪外審 P2-2):上一版直接丟掉
    # status,於是沒有 universe 的日子 `9999` 也回得出 canonical id ——
    # 任何殘留的呼叫端只要走這個出口,修掉的 bypass 就重新打開。
    assert ir.resolve("9999", {}) == (None, None)
    assert ir.resolve("9999", {}, allow_unverified=True)[0] == "TW:EQUITY:9999"


# ── 相關性:只有指數豁免 ──

def test_only_indices_are_exempt_from_event_evidence():
    assert ir.needs_event_evidence(ir.EQUITY) is True
    assert ir.needs_event_evidence(ir.ETF) is True
    assert ir.needs_event_evidence(ir.INDEX) is False


def test_unrelated_news_cannot_claim_qqq_or_tsm():
    """**必補測試 16**:白名單不再是繞過檢查的後門。"""
    news, pk = _unrelated_news(), _packet()
    for aid in ("QQQ", "SPY", "TSM"):
        assert av._asset_unknown_to_evidence(aid, news, pk), (
            f"{aid} 與這則新聞無關,卻通過了相關性檢查")


def test_a_related_news_may_claim_them():
    """反向:真的被點名時當然可以掛 —— 判準是證據,不是白名單。"""
    news = {"source_item_id": "n2", "title": "QQQ 領跌 那斯達克 收黑",
            "summary": "科技股回檔", "entities": ["QQQ"]}
    assert not av._asset_unknown_to_evidence("QQQ", news, _packet())


def test_indices_stay_claimable_for_macro_transmission():
    """總經事件影響的就是整個市場 —— 要求「加權指數」出現在標題裡,
    等於禁止談總經傳導。"""
    news = {"source_item_id": "n3", "title": "美國 核心 PCE 年增 3.4% 創新高",
            "summary": "通膨黏著", "entities": ["美國"]}
    for aid in ("TAIEX", "加權指數", "market-wide"):
        assert not av._asset_unknown_to_evidence(aid, news, _packet()), aid


def test_tw_equity_still_needs_to_be_in_the_evidence():
    """個股沒有豁免:2330 要被點名(或別名命中)才算被影響。"""
    assert av._asset_unknown_to_evidence("2330", _unrelated_news(), _packet())
    named = {"source_item_id": "n4", "title": "台積電 上修 資本支出",
             "summary": "法說", "entities": ["台積電"]}
    assert not av._asset_unknown_to_evidence("2330", named, _packet())


# ── 第二十六輪 P1-6:「永遠不是標的」與「與這件事無關」是兩個問題 ──

def test_a_fiscal_period_is_never_an_instrument():
    """**`Q2` 出現在幾乎每一則財報新聞的標題裡。**

    於是「這個字有沒有出現在證據裡」對它永遠成立 —— 判準等於沒有判準,
    而 renderer 會把 `asset_id: "Q2"` 排得跟真的逐標的分析一模一樣。
    這與 `CEO`/`GPU` 是同一個形狀,只是先前沒有人把期間詞列進去。
    """
    news = {"source_item_id": "n9", "title": "台積電 Q2 每股盈餘優於預期",
            "summary": "FY25 展望上修", "entities": ["台積電"]}
    for aid in ("Q2", "FY25", "1H", "H1", "4Q", "2H", "CY2026"):
        assert av.never_an_instrument(aid), aid
        assert av._asset_unknown_to_evidence(aid, news, _packet()), (
            f"{aid} 只因為出現在標題裡就通過了")


def test_real_tickers_are_not_swept_up_by_the_period_rule():
    """**誤殺比漏放危險**(repo 記過):真代號不得被期間詞的規則掃到。

    `MTD` 是 Mettler-Toledo、`TTM` 也有人在用(外審第二輪 P2)——
    第一版把裸縮寫也列進絕對黑名單,會在那家公司真的上新聞的那天
    把合法分析判掉。規則因此收窄成**只擋帶數字的期間詞**:
    美股代號不含數字,台股代號是純數字加選擇性字尾,兩邊都不會
    長成 `Q2` / `FY25`。代價是裸 `FY`、`YTD` 仍可能混進來 ——
    那是刻意選的那一側。
    """
    for aid in ("2330", "TSM", "NVDA", "AMD", "QQQ", "TAIEX", "00662",
                "MTD", "TTM", "YTD", "QTD", "FY", "CY"):
        assert not av.never_an_instrument(aid), aid


def test_an_ambiguous_abbreviation_still_passes_when_the_news_is_about_it():
    """`MTD` 真的是那則新聞的主角時要放行 —— 判準退回證據,不是黑名單。"""
    news = {"source_item_id": "n5", "title": "MTD 上修全年財測",
            "summary": "實驗室儀器需求回溫", "entities": ["MTD"]}
    assert not av._asset_unknown_to_evidence("MTD", news, _packet())
    # 而與它無關的新聞照樣擋得住(黑名單拿掉不等於門開著)
    assert av._asset_unknown_to_evidence("MTD", _unrelated_news(), _packet())


def test_a_period_abbreviation_in_the_headline_is_not_a_company():
    """**把裸縮寫放行,原來那條冒充路徑就開回來了**(外審第二輪 P2)。

    「Revenue rises on a TTM basis」—— `TTM` 就在標題裡,token 比對因此
    放行,而它在那句話裡是**期間**。判準要看上下文:抽取器把公司名放進
    `entities`,標題裡當期間用的那個字不會進去。
    """
    head = {"source_item_id": "n6", "title": "Revenue rises on a TTM basis",
            "summary": "毛利同步改善", "entities": []}
    assert av._asset_unknown_to_evidence("TTM", head, _packet())
    assert av.period_word_not_an_entity("TTM", head)
    # 同一個字被點名成實體時,**期間那條規則**放行 ——
    # 但它仍然不是宣告過的標的,所以整體判準照樣擋(第二十八輪 P1-2:
    # 未知的大寫字串是「未知實體」,不是「可能是標的」)。
    named = dict(head, entities=["TTM"])
    assert not av.period_word_not_an_entity("TTM", named)
    assert av._asset_unknown_to_evidence("TTM", named, _packet()),         "沒有被宣告過的縮寫仍然不該進逐標的方向卡"
    # 交易所限定寫法才是自由文字裡的權威
    quoted = dict(head, title="TTM Technologies (NASDAQ: TTM) beats",
                  entities=["TTM Technologies"])
    assert not av._asset_unknown_to_evidence("TTM", quoted, _packet())
    # 而真代號不受這條規則影響
    for aid in ("NVDA", "AMD", "TSM", "2330"):
        assert not av.period_word_not_an_entity(aid, head), aid


def test_a_company_whose_ticker_collides_with_a_period_must_be_declared():
    """**權威是被宣告的,不是從版面推導的**(外審第三、四輪)。

    抽取器抓到的是公司全名「Mettler-Toledo」,不是括號裡的 `MTD` ——
    只比對 `entities` 的字面相等,那則新聞會被判成「這裡的 MTD 是期間」,
    而它明明是主角。**修法不是「公司名後面接括號就算代號」**:
    `Apple (TTM) valuation reaches…` 完全符合那個樣式,而 `TTM` 在那裡
    是估值期間。對應寫進 `entity_alias.ALIAS_GROUPS`,
    交易所限定寫法(`NYSE: MTD`)另外接受 —— 那是自由文字裡的權威寫法。
    """
    import entity_alias as _ea
    assert _ea.group_of("Mettler-Toledo") == _ea.group_of("MTD") >= 0,         "撞名的公司要被宣告,否則它上新聞那天會被判成期間"
    for title in ("Mettler-Toledo (MTD) raises guidance",
                  "Mettler-Toledo raises guidance (NYSE: MTD)",
                  "Mettler-Toledo lifts outlook, NYSE: MTD up 3%"):
        it = {"source_item_id": "n7", "title": title, "summary": "",
              "entities": ["Mettler-Toledo"]}
        assert not av.period_word_not_an_entity("MTD", it), title
        assert not av._asset_unknown_to_evidence("MTD", it, _packet()), title


def test_a_period_next_to_a_company_name_is_still_a_period():
    """**文字相鄰證明不了對應關係**(外審第四輪 P2)。

    `Apple (TTM) valuation reaches…` 與 `Mettler-Toledo (MTD)` 在版面上
    完全同構,而前者的 `TTM` 是估值期間 —— 蘋果的代號是 AAPL。
    上一版用「實體後面緊接括號」當判準,這一則會過關。
    """
    for aid, title, ents in (("TTM", "Apple (TTM) valuation reaches a record",
                              ["Apple"]),
                             ("TTM", "台積電營收(TTM)創高", ["台積電"]),
                             ("YTD", "Nvidia (YTD) gain tops 40%", ["Nvidia"])):
        it = {"source_item_id": "n8", "title": title, "summary": "",
              "entities": ents}
        assert av.period_word_not_an_entity(aid, it), title


def test_the_message_says_the_real_reason():
    """**訊息要說得出真正的理由。** `Q2` 先前拿到的是「不在這則新聞的
    實體或標題裡」—— 而它就在標題裡,那句話是假的,讀著訊息的人
    會去修錯的東西。"""
    import analysis_schema as sch
    import fixtures_analysis as fx
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = "Q2"
    hits = [p for p in sch.validate(obj, fx.ids()) if "Q2" in p]
    assert hits, "Q2 整個沒被擋"
    assert any("不是可交易標的" in h for h in hits), hits
    assert not any("不在這則" in h for h in hits), hits


# ===== 第二十七輪外審 P1-5:國家實體冒充可交易標的 =====

def test_a_country_entity_cannot_be_rendered_as_an_asset():
    """**`US` 精確出現在 entities 與標題裡,於是被當成標的**。

    確定性反例(外審原文):
    `{"title": "US sanctions China", "entities": ["US", "China"],
      "affected_assets": [{"asset_id": "US", ...}]}` ——
    `US` 不在商用縮寫黑名單、不在概念詞表、不是會計期間,而且**就在證據
    裡**,所以逐標的方向卡照渲染。可是它是國家,不是可交易標的。

    「出現在證據裡」這個判準對**事件主體**永遠成立 —— 與 `CEO`、`Q2`
    是同一個形狀,只是主體那一類先前完全沒被宣告。
    """
    news = {"source_item_id": "n9", "title": "US sanctions China",
            "summary": "", "entities": ["US", "China"]}
    for aid in ("US", "China", "UK", "美國", "中國", "台灣"):
        assert av.never_an_instrument(aid), aid
        assert av._asset_unknown_to_evidence(aid, news, _packet()), aid


def test_a_jurisdiction_that_collides_with_a_real_ticker_needs_context():
    """**`EU` 是歐盟,也是 enCore Energy 的美股代號**(外審第二輪)。

    一律擋會把合法的逐標的卡判掉,而那會讓整份特化分析進修補、
    修不好就降級。所以它不進絕對黑名單,改走看上下文的判準 ——
    而且**不認 `entities` 字面**:`EU` 出現在 entities 裡多半就是歐盟
    本身,拿它當「這是公司」的證據會讓每一則歐盟新聞變成一張方向卡。
    只認宣告過的別名組或交易所限定寫法。
    """
    assert not av.never_an_instrument("EU")
    eu = {"source_item_id": "n1", "title": "EU announces new tariffs on China",
          "summary": "", "entities": ["EU", "China"]}
    assert av._asset_unknown_to_evidence("EU", eu, _packet()), "歐盟被當成標的"
    enc = {"source_item_id": "n2", "summary": "",
           "title": "enCore Energy (NASDAQ: EU) reports results",
           "entities": ["enCore Energy"]}
    assert not av._asset_unknown_to_evidence("EU", enc, _packet()),         "交易所限定寫法仍被擋"
    # **訊息要說得出真正的理由**(外審第二輪):對 `EU` 說「是期間」
    # 「沒有出現在實體清單」兩句都是假的 —— 它就在實體清單裡,
    # 只是那裡的 `EU` 是歐盟。這句話會被原樣送進修補 prompt。
    import analysis_schema as sch
    import fixtures_analysis as fx
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["source_item_id"] = "n1"
    obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = "EU"
    pk = _packet()
    pk["news"] = [{"source_item_id": "n1", "title": eu["title"],
                   "summary": "", "entities": eu["entities"],
                   "source": "Reuters"}]
    hits = [p for p in sch.validate(obj, pk) if "EU" in p]
    assert hits, "EU 整個沒被擋"
    assert any("法域" in h for h in hits), hits
    assert not any("期間" in h for h in hits), hits
    # 期間那組仍然認 `entities`(兩組的確認方式不同,理由見判準)
    mtd = {"source_item_id": "n3", "title": "MTD 上修全年財測",
           "summary": "", "entities": ["MTD"]}
    assert not av._asset_unknown_to_evidence("MTD", mtd, _packet())


def test_intergovernmental_bodies_and_product_brands_are_not_instruments():
    """`UN`/`NATO` 是機構、`AWS`/`CUDA` 是產品線 —— 要談那家公司
    請寫 `AMZN` / `NVDA`。"""
    for aid in ("UN", "NATO", "OECD", "AWS", "CUDA", "AZURE"):
        assert av.never_an_instrument(aid), aid


def test_real_tickers_survive_the_jurisdiction_rule():
    """**誤殺比漏放危險**:這條規則不得掃到真代號或指數。"""
    for aid in ("NVDA", "AMD", "TSM", "QQQ", "SPY", "TAIEX", "加權指數",
                "market-wide", "2330", "00662", "SOX", "費半"):
        assert not av.never_an_instrument(aid), aid


def test_the_jurisdiction_list_is_declared_not_guessed():
    """判準走**宣告過的表**(`event_actions`),不是「兩個大寫字母」
    那種開放式猜測 —— 猜會掃到 `GM`、`BP` 這種真代號。"""
    import event_actions as ea
    assert ea.is_jurisdiction("US") and ea.is_jurisdiction("英國")
    assert not ea.is_jurisdiction("GM") and not ea.is_jurisdiction("BP")


# ===== 第二十八輪外審 P1-2:開放字彙 =====

def _named(aid: str) -> dict:
    return {"source_item_id": "n1", "summary": "",
            "title": f"{aid} discusses regional trade restrictions",
            "entities": [aid]}


def test_an_international_body_cannot_become_an_asset():
    """**`ASEAN` 符合 `[A-Z]{2,6}`、不在任何黑名單、就在標題與 entities 裡**
    —— 於是它被渲染成逐標的方向卡。黑名單追不完開放字彙。"""
    for aid in ("ASEAN", "BRICS", "OPEC", "G7", "G20"):
        assert av._asset_unknown_to_evidence(aid, _named(aid), _packet()), aid


def test_an_arbitrary_uppercase_token_is_not_an_instrument():
    """**未知的大寫字串是「未知實體」,不是「可能是標的」。**"""
    for aid in ("XYZAB", "ZZZ", "QWERTY", "FOO"):
        assert av._asset_unknown_to_evidence(aid, _named(aid), _packet()), aid


def test_declared_instruments_still_pass():
    """**誤殺比漏放危險**:宣告過的標的不得被這條規則掃掉。

    宣告有兩個來源 —— `instrument_registry._KNOWN` 與 `entity_alias`
    的別名組(「輝達/NVIDIA/NVDA」本來就是同一個主體的不同寫法)。
    """
    import instrument_registry as ir
    for aid in ("NVDA", "AMD", "TSM", "QQQ", "SPY", "AAPL", "MSFT",
                "AVGO", "ASML", "SOX", "TAIEX"):
        assert ir.is_declared(aid), aid
        assert not av._asset_unknown_to_evidence(aid, _named(aid), _packet()), aid


def test_an_exchange_qualified_symbol_is_authoritative_even_if_undeclared():
    """沒被宣告過、但新聞用交易所限定寫法點名 —— 那是自由文字裡的權威。"""
    it = {"source_item_id": "n2", "summary": "",
          "title": "Foo Industries (NASDAQ: FOOO) raises guidance",
          "entities": ["Foo Industries"]}
    assert not av._asset_unknown_to_evidence("FOOO", it, _packet())


def test_the_declaration_is_the_gate_not_the_evidence():
    """**「出現在證據裡」不再足夠** —— 那正是開放字彙的破口:
    事件主體(國家、組織)必然出現在自己的新聞裡。"""
    import instrument_registry as ir
    assert not ir.is_declared("ASEAN")
    it = _named("ASEAN")
    assert "ASEAN" in it["title"] and "ASEAN" in it["entities"]
    assert av._asset_unknown_to_evidence("ASEAN", it, _packet())


def test_a_subject_alias_group_is_not_an_instrument_declaration():
    """**別名表是「主體」的身分表,不是標的表**(外審第二輪)。

    它含「聯準會 / Fed / FOMC / 美聯儲」—— 那是一個機構。
    把整張表當成標的宣告的話,`asset_id: "Fed"` 會被渲染成方向卡
    (`FOMC` 剛好被縮寫黑名單擋下,`Fed` 沒有 —— 所以反例要用 `Fed`)。
    """
    import instrument_registry as ir
    for aid in ("Fed", "FOMC", "聯準會", "美聯儲"):
        assert not ir.is_declared(aid), aid
    it = {"source_item_id": "n1", "summary": "",
          "title": "Fed holds rates steady", "entities": ["Fed"]}
    assert av._asset_unknown_to_evidence("Fed", it, _packet())
    # 而**組裡有一個真代號**的那些仍然算宣告過
    for aid in ("台積電", "TSMC", "2330", "輝達", "NVDA"):
        assert ir.is_declared(aid), aid


def test_every_alias_group_has_taken_a_side():
    """**每一組別名都要表態:它是標的,還是主體?**(外審第二輪)

    只靠「組裡有沒有一個代號」推導的話,`SK海力士`、`三星電子` 這種
    真的上市、但別名組裡沒有代號的公司會被一律拒絕 ——
    而那會把合法的半導體分析送進修補。
    這條守衛讓「新增一組別名卻沒想過它是哪一種」當場紅,
    而不是等某天在生產裡變成一張假的方向卡(或一次誤殺)。
    """
    import entity_alias as ea
    import instrument_registry as ir
    undecided = [g[0] for g in ea.ALIAS_GROUPS
                 if not ir.is_declared(g[0])
                 and g[0] not in ir.NON_INSTRUMENT_ALIAS_GROUPS]
    assert not undecided, f"這些別名組沒有表態:{undecided}"
    # 兩邊都要有東西,否則這條守衛可能是靠空集合過關的
    assert ir.NON_INSTRUMENT_ALIAS_GROUPS
    assert any(ir.is_declared(g[0]) for g in ea.ALIAS_GROUPS)


def test_listed_companies_without_a_ticker_in_their_group_are_declared():
    """`SK海力士` / `三星電子` 的別名組裡沒有代號,但它們是真的上市公司。"""
    import instrument_registry as ir
    for aid in ("SK海力士", "SK Hynix", "海力士",
                "三星電子", "Samsung Electronics", "三星"):
        assert ir.is_declared(aid), aid


# ===== 第二十九輪外審 P1-2:三條殘餘 bypass =====

def test_a_chinese_institution_cannot_become_an_asset():
    """**非 ASCII 分支先前只問「有沒有出現在證據裡」**(P1-2A)——
    而「聯準會」精確出現在自己的新聞裡,它是機構不是可交易標的。
    同一道門在 ASCII 側已經是正面條件,只關一半等於沒關。"""
    for aid, title in (("聯準會", "聯準會宣布維持利率不變"),
                       ("美聯儲", "美聯儲官員談話"),
                       ("東協", "東協峰會討論區域貿易"),
                       ("金磚國家", "金磚國家擴大成員"),
                       ("美國財政部", "美國財政部標售公債")):
        it = {"source_item_id": "n1", "title": title, "summary": "",
              "entities": [aid]}
        assert av._asset_unknown_to_evidence(aid, it, _packet()), aid
    # 宣告過的中文名稱不受影響(誤殺比漏放危險)
    for aid in ("台積電", "聯發科", "鴻海", "SK海力士", "三星電子"):
        it = {"source_item_id": "n2", "title": f"{aid} 最新公告",
              "summary": "", "entities": [aid]}
        assert not av._asset_unknown_to_evidence(aid, it, _packet()), aid


def test_a_common_word_does_not_support_its_homonym_ticker():
    """**「宣告過」回答不了「這個句子裡的 now 是不是 ServiceNow」**
    (P1-2B)。撞名的 ticker 不得靠標題裸字命中 —— 要 entities 大小寫
    一致,或交易所限定寫法。"""
    cases = (("NOW", "Investors now expect the Fed to cut rates"),
             ("NET", "Company reports higher net income this quarter"),
             ("ARM", "Arm architecture adoption is accelerating"),
             ("SNOW", "Heavy snow disrupts logistics networks"),
             ("COIN", "The other side of the coin for investors"))
    for aid, title in cases:
        it = {"source_item_id": "n1", "title": title, "summary": "",
              "entities": ["Fed"]}
        assert av._asset_unknown_to_evidence(aid, it, _packet()), aid
    # 真的在講那家公司:entities 大寫一致、或交易所限定寫法
    assert not av._asset_unknown_to_evidence(
        "NOW", {"source_item_id": "n2", "title": "ServiceNow beats estimates",
                "summary": "", "entities": ["NOW"]}, _packet())
    assert not av._asset_unknown_to_evidence(
        "NOW", {"source_item_id": "n3",
                "title": "ServiceNow (NYSE: NOW) raises guidance",
                "summary": "", "entities": ["ServiceNow"]}, _packet())


def test_a_fake_tw_code_is_rejected_even_when_the_universe_is_empty():
    """**資料斷供的日子正是假代號最不會被抓到的日子**(P1-2C)。

    走到 universe 那一關代表代號不在證據裡 —— 那不論 universe 在不在
    都該擋。上一版在 universe 空的那天放行。
    """
    it = {"source_item_id": "n1", "title": "市場震盪", "summary": "",
          "entities": []}
    assert av._asset_unknown_to_evidence("9999", it, {"tw_universe": []})
    # `packet is None` 的舊呼叫端先前是**全放行** —— 同樣關掉:
    # 證據判準不需要 packet。
    assert av._asset_unknown_to_evidence("9999", it, None)
    assert av._asset_unknown_to_evidence("999999", it, {"tw_universe": []})
    # 而**在證據裡**的代號不受 universe 缺席影響
    named = {"source_item_id": "n2", "title": "2330 法說會登場",
             "summary": "", "entities": []}
    assert not av._asset_unknown_to_evidence("2330", named,
                                             {"tw_universe": []})


def test_the_id_set_entry_point_also_validates_assets():
    """**相容路徑不得跳過標的驗證**(第二十九輪外審第二輪 F1)。

    整段掛在 `packet is not None` 底下的話,`validate(obj, ids)` 這條入口
    連 `ASEAN` 都放行 —— 之前每一輪關掉的 bypass 在這條路上全部無效,
    而 `analysis_depth` 與大量測試就是用這條入口。
    沒有 packet = 沒有證據可看:指數(豁免相關性)照過,其餘 fail-closed。
    """
    import sys
    sys.path.insert(0, "tests")
    import fixtures_analysis as fx
    import analysis_schema as sch
    for bad in ("ASEAN", "聯準會", "9999", "NOW"):
        obj = fx.valid_analysis()
        obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = bad
        assert [p for p in sch.validate(obj, fx.ids()) if bad in p], bad
    # 指數與 market-wide 在 ID-set 路徑照過(fixture 本身就是)
    assert not [p for p in sch.validate(fx.valid_analysis(), fx.ids())
                if "affected_assets" in p]


def test_a_year_shaped_code_gets_no_credit_from_the_body():
    """**`asset_id="2026"` 配 "2026 market outlook" 命中的是年份**
    (第二輪 F2)。年份形狀的代號要 entities 精確命中或別名組;
    其餘數字的正文命中要 token 邊界(裸子字串會藏在長數字裡)。"""
    it = {"source_item_id": "n1", "title": "2026 market outlook",
          "summary": "", "entities": []}
    assert av._asset_unknown_to_evidence("2026", it, {"tw_universe": []})
    # entities 精確命中才算(真的有 2026 這檔且被點名時)
    named = dict(it, entities=["2026"])
    assert not av._asset_unknown_to_evidence("2026", named, {"tw_universe": []})
    # token 邊界:2330 不得藏在 123300 裡
    hidden = {"source_item_id": "n2", "title": "指數上漲 123300 點次",
              "summary": "", "entities": []}
    assert av._asset_unknown_to_evidence("2330", hidden, {"tw_universe": []})


def test_a_standalone_number_in_the_body_is_not_ticker_evidence():
    """**正文出現一個數字證明不了它是代號**(第三輪外審)。

    「指數上漲 9999 點」的 9999 是點位 —— 正文命中只給**驗過的代號**
    (宣告過、或在當日 universe 裡)加分;沒驗過的數字要 entities
    精確命中。
    """
    it = {"source_item_id": "n1", "title": "指數上漲 9999 點",
          "summary": "", "entities": []}
    assert av._asset_unknown_to_evidence("9999", it, {"tw_universe": []})
    # 同一個數字,在 universe 裡(真的上市)→ 正文命中就算相關
    assert not av._asset_unknown_to_evidence(
        "9999", {"source_item_id": "n2", "title": "9999 盤中大漲",
                 "summary": "", "entities": []},
        {"tw_universe": [{"code": "9999"}]})


# ===== 第三十輪外審 P2-4:歧義代號的公司名要有橋 =====

def _blocked(aid, ents, title):
    import analysis_validate as av
    return av._asset_unknown_to_evidence(
        aid, {"title": title, "summary": "", "entities": list(ents)}, None)


def test_the_company_name_supports_its_ambiguous_ticker():
    """**fail-closed 但誤殺**(外審 P2-4):extractor 抽出的是公司名、
    模型寫的是代號 —— 中間沒有橋的話,`ServiceNow` 的真新聞與副詞 `now`
    一起被擋。補上別名組之後兩件事同時成立。"""
    for aid, name, title in (
            ("NOW", "ServiceNow", "ServiceNow raises full-year guidance"),
            ("NET", "Cloudflare", "Cloudflare reports record revenue"),
            ("ARM", "Arm Holdings", "Arm Holdings beats estimates"),
            ("SNOW", "Snowflake", "Snowflake lifts product revenue outlook"),
            ("COIN", "Coinbase", "Coinbase volumes surge")):
        assert not _blocked(aid, [name], title), (aid, name)


def test_the_common_word_itself_still_does_not_support_it():
    """裸字命中仍然不算 —— 這是上一輪的修正,不得被這一輪打開。"""
    for aid, title in (("NOW", "Apple is now the biggest company"),
                       ("NET", "Apple net income rises 12%"),
                       ("ARM", "Apple moves to Arm architecture"),
                       ("SNOW", "Snow disrupts flights in Chicago"),
                       ("COIN", "A coin shortage hits retailers")):
        assert _blocked(aid, ["Apple"], title), aid


def test_the_bare_ambiguous_word_as_an_entity_is_not_the_company():
    """**橋是公司名,不是代號自己**(這條反例只靠那一行分勝負):
    `Arm architecture` 的 entities 會是 `Arm` —— 別名組裡有它,
    把整組都當橋的話裸字又從 entities 那條路回來了。"""
    assert _blocked("ARM", ["Arm"], "Apple moves to Arm architecture")
    assert not _blocked("ARM", ["Arm Holdings"], "Arm Holdings beats")


def test_the_ambiguity_does_not_leak_into_the_global_alias_table():
    """**歧義只該影響問這個問題的人**(外審 r2):`canonical("Arm")` 一旦
    回 `Arm Holdings`,「Arm 架構」的新聞在 event identity 那邊就會與
    Arm Holdings 的線索併成同一條 —— 那張表是全域的主體等價關係。"""
    import entity_alias as ea
    import event_identity as eid
    for word in ("Arm", "now", "NOW", "net", "coin", "snow"):
        assert ea.canonical(word) == word, word
    assert not ea.expand({"Arm"}) & ea.expand({"Arm Holdings"})
    # 主體正規化也不得把它們併起來
    assert eid.canonical_subject("Arm") != eid.canonical_subject("Arm Holdings")

