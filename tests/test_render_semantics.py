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

def test_the_retired_sections_stay_retired():
    """**退休的段落名不得再出現。**

    「世界大事速覽」是 legacy 掛錯招牌的段名(schema 沒有那個概念);
    「今日市場關注與預測」是 2026-08-18 併出來、隔天被使用者整段刪掉的
    (「直接刪除整段今日市場關注與預測」)。名字再出現代表有人把段落
    加回去了,而那兩個決定都有明確的使用者原話。
    """
    out = _rendered()
    assert "世界大事速覽" not in out
    assert "今日市場關注與預測" not in out
    assert "訊號的橫向綜合" not in out


def test_the_news_section_does_not_claim_to_be_tech_only():
    """**科技類股那一段裡不得出現非科技的主體。**

    2026-08-18 之前這條的寫法是「沒過濾就不得叫科技板塊脈動」——
    因為當時 `top_news_analysis` 整包無條件進第八段。使用者定案要回到
    舊版的「科技類股 / 其他類股」兩段寫法之後,**這一段真的有過濾了**,
    所以判準跟著換成過濾本身:金融股不得落在科技類股底下。
    (舊標題「科技板塊脈動」是既有路徑的段名,仍然不該出現在這裡。)
    """
    out = _rendered()
    assert ar.SECTION_NEWS in out
    # **這裡不能再寫「科技板塊脈動 不得出現」** —— 2026-08-18 子段名回到
    # 舊版用字之後那正是合法的段名,而 `_rendered()` 沒有 packet、分不出
    # 產業,所以那句斷言是**靠巧合過關**的(沒有科技條目就不會有那個子段)。
    # 判準換成過濾本身,見下面的雙主體案例。

    pk = {"tw_universe": [
        {"code": "2330", "name": "台積電", "industry": "半導體業"},
        {"code": "2882", "name": "國泰金", "industry": "金融保險業"}],
        "news": [{"source_item_id": "t1", "title": "台積電擴產", "entities": ["2330"]},
                 {"source_item_id": "t2", "title": "國泰金獲利創高", "entities": ["2882"]}]}
    obj = fx.valid_analysis()
    base = obj["top_news_analysis"][0]
    obj["top_news_analysis"] = [
        dict(base, source_item_id="t1",
             affected_assets=[dict(base["affected_assets"][0], asset_id="2330")]),
        dict(base, source_item_id="t2",
             affected_assets=[dict(base["affected_assets"][0], asset_id="2882")])]
    text = ar.render(obj, pk)
    i = text.index(ar.SUBSECTION_TECH)
    j = text.index(ar.SUBSECTION_OTHER)
    assert i < j, text
    assert "台積電" in text[i:j] and "國泰金" not in text[i:j], text[i:j]
    assert "國泰金" in text[j:], text[j:]


def test_the_market_fields_are_deliberately_unrendered():
    """`taiwan_market` / `global_market` / `priced_in` / `asset_net_effects` /
    `cross_market_synthesis` **刻意不渲染**(2026-08-19 使用者刪掉整段)。

    欄位仍在 schema 裡被要求與驗證 —— 模型先想清楚全局才寫得好逐則,
    拿掉要求會讓第八段的品質跟著掉。這條釘住「不渲染」是決策不是遺漏:
    這幾個欄位的內容不得出現在信裡。
    """
    out = _rendered(
        priced_in={"already_reflected": ["獨特的已反映句"],
                   "not_yet_reflected": ["獨特的未反映句"]})
    assert "獨特的已反映句" not in out and "獨特的未反映句" not in out
    assert "已被市場反映" not in out
    assert "各標的合計影響" not in out


# ---------------------------------------------------------------- 不得丟資料

def test_the_falsification_trigger_is_folded_from_the_top_three():
    """**七段只留三大重點本身**(2026-08-19 使用者:「我只要三大消息重點
    即可」)。失效條件仍在 schema 裡被要求與驗證(評分用),且第八段的
    逐則分析裡「若…,此判斷不成立」仍在 —— 失效條件沒有從信裡整個消失。
    """
    out = _rendered()
    assert "什麼情況代表這個判斷錯了" not in out
    assert "此判斷不成立" in out, "第八段的失效條件也一起消失了"


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
    # 2026-08-19:字樣從「什麼會推翻它:X」改成散文「若X,此判斷不成立」
    # (使用者:整合成一小段落語句敘述)。**判準不變**:失效條件要在。
    out = ar.render(fx.valid_analysis())
    assert "此判斷不成立" in out
    assert "量級中等" not in out and "成立要看到" not in out


def test_the_relationship_line_is_folded_away():
    """2026-08-17 使用者定案:逐則新聞底下的「與另一則的關係:…」收起來。

    關係本身由**橫向綜合**那一段負責(互相強化/互相抵銷/共用驅動不
    重複計權),而逐則再講一次正是使用者說的「像表單」。
    **誠實記下代價**:綜合段講的是全局關係,不保證逐則的配對
    (「這則與那則方向相反」)會被提到 —— 那個細節確實不再進信裡。
    欄位仍在 schema 裡被要求與驗證。
    """
    # 2026-08-19:橫向綜合那一段也被使用者刪掉了(整段「今日市場關注與
    # 預測」)—— 這條只剩前半:逐則的關係行不得再出現。
    out = ar.render(fx.valid_analysis())
    assert "與另一則的關係" not in out


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


def test_a_chain_that_the_validator_accepts_is_not_drawn_as_broken():
    """**連續性的判準要與驗證器同一個**(外審 2026-08-17)。

    schema 明說「照抄再補充」是合法接法,`analysis_validate._same_node`
    用包含判準放行 —— 渲染層若改用逐字相等,一條**驗證過的連續鏈**會被
    畫成斷鏈,而讀者只會看到一條莫名其妙分成兩段的因果鏈。
    """
    import analysis_render_depth as ard
    import analysis_validate as av
    steps = [{"from_what": "AI 需求", "to_what": "先進封裝產能擴充"},
             {"from_what": "先進封裝產能擴充（CoWoS 量產）",
              "to_what": "台積電營收上修"}]
    assert av._same_node(steps[0]["to_what"], steps[1]["from_what"]), (
        "前提:驗證器認為這條鏈是連續的")
    assert ard._chain_line(steps) == "AI 需求 → 先進封裝產能擴充 → 台積電營收上修"


def test_two_effect_sentences_do_not_collide():
    """兩段影響接進散文時不得接出「。、」(2026-08-17 生產信裡看得到)。
    2026-08-19 改散文之後由 `_assets_prose` 負責:效果去尾再用「、」接。"""
    import analysis_render_depth as ard
    prose = ard._assets_prose({"affected_assets": [
        {"asset_id": "2330", "first_order_effect": "折現率上升。",
         "second_order_effect": "折價可能擴大"}]})
    assert prose and "。、" not in prose, prose
    assert prose == "2330:折現率上升、折價可能擴大。", prose


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
    # 2026-08-19:失效條件從七段收起來之後 `_event_card` 只回一行,
    # 來歷括號接在句尾。**判準的核心不變**:括號要跟著判斷那一句。
    line = ar._event_card(c, pk)
    assert chr(10) not in line, line
    assert line.endswith("（官方公告）"), line
