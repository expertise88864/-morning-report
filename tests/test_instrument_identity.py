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
