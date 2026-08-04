# -*- coding: utf-8 -*-
"""**渲染層不得改變意思,也不得把分析丟掉**(第十五輪 P1-2/P1-3)。

## 兩個實際的缺陷

1. **段落名說了謊。** `global_market`(美股→台股連動)被放進
   「七之二、世界大事速覽」—— 那一段在既有契約裡的定義是**股市之外的世界**;
   `taiwan_market.tsmc_view` 被放進「九、**其他**類股資訊」——
   台積電是最不「其他」的那一檔;整個 `top_news_analysis` 無條件進
   「八、科技板塊脈動」,即使那則是金融、航運或生技。
2. **最像分析的欄位整段沒有被渲染。** `priced_in`(哪些已在價格裡、
   哪些還沒)、`falsification_trigger`(什麼情況代表判斷錯了)、
   `counterevidence_ids`、`actions_to_consider` —— 模型產出了、驗證器
   檢查了,收件人從來沒看到。

兩者都只有在 Luna 特化路徑跑成的那天才會顯現,而**信看起來仍然完整**,
沒有任何錯誤訊息。這正是使用者反映「只有數據沒有分析」的一部分。
"""
import fixtures_analysis as fx

import analysis_render as ar


def _rendered(**over):
    obj = fx.valid_analysis()
    obj.update(over)
    return ar.render(obj)


# ---------------------------------------------------------------- 段落要說實話

def test_the_world_events_heading_is_gone():
    """**Luna 的 schema 沒有「股市之外的世界」這種欄位。**

    沒有就不要宣稱有 —— 找一個欄位塞進去,收件人會以為那是世界大事。
    """
    out = _rendered()
    assert "世界大事速覽" not in out, "美股連動又被標成世界大事了"
    assert ar.SECTION_GLOBAL in out


def test_tsmc_does_not_land_in_the_other_sectors_section():
    """台積電進「其他類股資訊」是語意錯誤,不是排版問題。"""
    out = _rendered()
    assert "其他類股資訊" not in out
    assert ar.SECTION_TW in out
    i = out.index(ar.SECTION_TW)
    assert "守月線" in out[i:i + 200], "tsmc_view 沒有進台股那一段"


def test_the_news_section_does_not_claim_to_be_tech_only():
    """`top_news_analysis` 沒有被依產業過濾,就不得叫「科技板塊脈動」。"""
    out = _rendered()
    assert "科技板塊脈動" not in out
    assert ar.SECTION_NEWS in out


def test_the_taiwan_summary_is_not_filed_as_local_news():
    """`taiwan_market.summary` 是台股整體,不是「台灣本地動態」
    (那一段講的是證交所新制、勞動基金這類在地消息)。"""
    out = _rendered()
    assert "台灣本地動態" not in out
    i = out.index(ar.SECTION_TW)
    assert "量能回升" in out[i:i + 200]


# ---------------------------------------------------------------- 不得丟資料

def test_priced_in_is_rendered():
    """**這是整份 schema 裡最像分析的欄位**,先前整段沒有被渲染。"""
    out = _rendered(priced_in={"already_reflected": ["美股漲幅"],
                               "not_yet_reflected": ["台積電法說"]})
    assert ar.SECTION_PRICED in out
    assert "美股漲幅" in out and "台積電法說" in out


def test_the_falsification_trigger_reaches_the_reader():
    """schema 把它列為必填,理由是「說不出什麼情況我就錯了的判斷,
    事後無法評分」。**要求了卻不顯示,那個必填只保護了 JSON。**"""
    out = _rendered()
    assert "什麼情況代表這個判斷錯了" in out
    assert "夜盤翻黑" in out


def test_counterevidence_is_flagged():
    """有反面證據的判斷,讀起來不得與一面倒的判斷一模一樣。"""
    obj = fx.valid_analysis()
    obj["key_drivers"][0]["counterevidence_ids"] = ["n2"]
    assert "有反面證據" in ar.render(obj)
    obj["key_drivers"][0]["counterevidence_ids"] = []
    assert "有反面證據" not in ar.render(obj)


def test_actions_to_consider_are_rendered():
    """模型寫了、驗證了、沒人看得到 —— 那等於沒有產出。"""
    obj = fx.valid_analysis()
    obj["portfolio_implications"]["actions_to_consider"] = ["分批降低槓桿"]
    out = ar.render(obj)
    assert "分批降低槓桿" in out


# ---------------------------------------------------------------- 不得反向壞掉

def test_the_stance_anchors_still_parse():
    """**後處理靠這兩個標題抓立場與總結。** 改段落名不得動到它們 ——
    動到的症狀是頂部 KPI 變成「—」,而信照樣寄出。"""
    import llm_postprocess as lp
    out = _rendered()
    assert ar.SECTION_STANCE in out and ar.SECTION_SUMMARY in out
    assert lp._extract_stance(out).get("label") == "偏多"
    assert lp._extract_summary(out)


def test_an_empty_analysis_still_renders_nothing():
    """反向:缺立場或總結時回空字串,由呼叫端落回既有路徑。
    **回半份比不回更糟** —— 信寄出去了但少了一半,而且沒有錯誤訊息。"""
    assert ar.render({}) == ""
    assert ar.render(None) == ""
    assert ar.render({"stance": {"label": "偏多"}}) == "", "缺總結卻回了東西"


# ---------------------------------------------------- schema v2:深度要進信

def test_the_mechanism_chain_reaches_the_reader():
    """**模型填了因果鏈,讀者要看得到** —— 突變驗證第一輪抓到這裡沒測試:
    把渲染那兩行拿掉,全套照樣綠。schema 再深,渲染丟掉就等於沒有。"""
    out = ar.render(fx.valid_analysis())
    assert "怎麼傳導" in out
    assert "費半收漲 → 台股電子開盤定價" in out
    assert "(推論)" in out, "推論步驟沒有被標出來 —— 整條鏈讀起來像事實"


def test_the_magnitude_and_signals_reach_the_reader():
    out = ar.render(fx.valid_analysis())
    assert "量級中等" in out
    assert "量級判斷不出來" in out, "unknown 的誠實版本沒有被渲染"
    assert "缺資本支出區間" in out, "「為什麼判斷不出來」沒有跟著出現"
    assert "成立要看到" in out and "什麼會推翻它" in out


def test_the_relationship_reaches_the_reader():
    out = ar.render(fx.valid_analysis())
    assert "與另一則的關係" in out
    assert "同一個底層驅動" in out


def test_the_synthesis_section_renders_and_leads():
    """橫向綜合要在,而且**排在逐條分析之前** —— 使用者要的是
    「合起來說什麼」,不是自己拼。"""
    out = ar.render(fx.valid_analysis())
    assert ar.SECTION_SYNTHESIS in out
    assert out.index(ar.SECTION_SYNTHESIS) < out.index(ar.SECTION_NEWS)
    assert "互相強化" in out and "互相抵銷" in out
    assert "今天的主導因子" in out and "什麼會讓它翻盤" in out
