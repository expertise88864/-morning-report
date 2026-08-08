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
    assert ir.resolve("9999", {})[0] == "TW:EQUITY:9999"


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
    # 同一個字被點名成實體時就是公司 —— 規則不是黑名單
    named = dict(head, entities=["TTM"])
    assert not av.period_word_not_an_entity("TTM", named)
    assert not av._asset_unknown_to_evidence("TTM", named, _packet())
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

