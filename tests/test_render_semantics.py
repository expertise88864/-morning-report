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
    """有反面證據的判斷,讀起來不得與一面倒的判斷一模一樣。

    2026-08-17:標記從機械括號(推論、信心…)搬到**來歷**括號 ——
    留在讀者眼前的都是誠實性訊號,機械欄位收起來。判準不變。"""
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
    assert "傳導:" in out
    assert "費半收漲 → 台股電子開盤定價" in out
    # 2026-08-17:通道與 fact/推論 標記收起來(使用者:讀起來像表單)。
    # **鏈本身還在**,而且節點串成一行 —— 那正是這條測試在保護的東西。


def test_the_invalidation_signal_reaches_the_reader():
    """**留下的是「什麼會推翻它」。**

    2026-08-17 使用者定案:量級/為什麼是這個量級/確認訊號收起來
    (它們仍在 schema 裡被要求與驗證)。失效條件不能一起收 ——
    說不出什麼情況自己會錯的判斷,事後無法評分。"""
    out = ar.render(fx.valid_analysis())
    assert "什麼會推翻它" in out
    assert "量級中等" not in out and "成立要看到" not in out


def test_the_relationship_line_is_folded_away():
    """2026-08-17 使用者定案:逐則新聞底下的「與另一則的關係:…」收起來。

    關係本身由**橫向綜合**那一段負責(互相強化/互相抵銷/共用驅動不
    重複計權),而逐則再講一次正是使用者說的「像表單」。
    **誠實記下代價**:綜合段講的是全局關係,不保證逐則的配對
    (「這則與那則方向相反」)會被提到 —— 那個細節確實不再進信裡。
    欄位仍在 schema 裡被要求與驗證。
    """
    out = ar.render(fx.valid_analysis())
    assert "與另一則的關係" not in out
    assert ar.SECTION_SYNTHESIS in out, "橫向綜合那一段本身要還在"


def test_the_synthesis_section_renders_and_leads():
    """橫向綜合要在,而且**排在逐條分析之前** —— 使用者要的是
    「合起來說什麼」,不是自己拼。"""
    out = ar.render(fx.valid_analysis())
    assert ar.SECTION_SYNTHESIS in out
    assert out.index(ar.SECTION_SYNTHESIS) < out.index(ar.SECTION_NEWS)
    assert "互相強化" in out and "互相抵銷" in out
    assert "今天的主導因子" in out and "什麼會讓它翻盤" in out


def test_a_broken_chain_is_not_rendered_as_one_arrow_run():
    """**鏈斷掉時不假裝連續**(2026-08-17 壓成一行的代價要先擋住)。

    下一步的起點不等於上一步的終點,就用「;」分段 —— 用一條箭頭把
    兩件不相干的事串起來,是這份報告最該避免的那種句子。
    """
    import analysis_render_depth as ard
    joined = ard._chain_line([{"from_what": "A", "to_what": "B"},
                              {"from_what": "B", "to_what": "C"}])
    assert joined == "A → B → C"
    broken = ard._chain_line([{"from_what": "A", "to_what": "B"},
                              {"from_what": "X", "to_what": "Y"}])
    assert broken == "A → B；X → Y", broken


def test_two_effect_sentences_do_not_collide():
    """兩段影響是**兩句話**:先前用「、」黏起來,接出「。、」
    (2026-08-17 生產信裡看得到)。"""
    import analysis_render_depth as ard
    rows = ard._assets({"affected_assets": [
        {"asset_id": "2330", "direction": "bearish", "magnitude_band": "small",
         "horizon": "1-5d", "first_order_effect": "折現率上升。",
         "second_order_effect": "折價可能擴大"}]})
    assert rows and "。、" not in rows[0], rows
    assert rows[0].endswith("折價可能擴大。"), rows


def test_the_counterevidence_flag_survives_without_a_cluster():
    """反面證據是 claim 自己的欄位 —— 沒有 cluster_id、或查不到那一群時
    仍要出現(先前兩個早退會把整個括號跳過)。"""
    import analysis_render as ar
    c = {"statement": "判斷一句話。", "counterevidence_ids": ["n2"]}
    assert "有反面證據" in ar._event_card(c, {})


def test_the_provenance_paren_sits_on_the_claim_sentence():
    """來歷括號要接在**判斷那一句**後面,不是接在失效條件那一行後面。

    `_claim_line` 回的是兩行(判斷 \n 失效條件)—— 直接 `line + 括號`
    會把「(官方公告)」黏在「什麼情況代表這個判斷錯了:夜盤翻黑」的
    尾巴,讀起來像是那個條件出自官方公告。
    """
    import analysis_render as ar
    c = {"statement": "判斷一句話。", "falsification_trigger": "夜盤翻黑",
         "cluster_id": "cluster:x"}
    pk = {"news_clusters": {"clusters": [{"cluster_id": "cluster:x",
                                          "official": True}]}}
    lines = ar._event_card(c, pk).splitlines()
    assert len(lines) == 2, lines
    assert lines[0].endswith("（官方公告）"), lines
    assert "官方公告" not in lines[1], lines
