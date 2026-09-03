# -*- coding: utf-8 -*-
"""**2026-08-19 使用者第三批**(當天實信的八項回饋)。

分散的行為各自測在原檔;這裡放**跨檔的三件事**:
台灣政策段(schema v20)、條數 advisory、以及「刪掉的段落不得回來」。
"""
import io as _io
from pathlib import Path as _Path

import analysis_depth as ad
import analysis_render as ar
import analysis_schema as sch
import fixtures_analysis as fx

#: **路徑錨在這個檔案自己身上**,不靠 CWD。
_ROOT = _Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- 台灣政策段

def test_the_policy_section_renders_from_the_schema_field():
    """**legacy 的「台灣本地動態」在特化 schema 沒有對應欄位,那一段整個
    消失** —— 使用者連兩天反映(「原本台灣政策分析也都不見了」)。
    v20 的 `taiwan_policy` 是它的家:政策,不是行情。"""
    obj = fx.valid_analysis()
    obj["taiwan_policy"] = [
        {"source_item_id": "n1", "what": "政院拍板 2027 普發現金一萬元",
         "impact": "內需消費短多,對電子權值中性"}]
    out = ar.render(obj)
    assert ar.SECTION_POLICY in out
    # 2026-08-19 第四批:政策改成**深度解析的排法** —— 政策名當小標、
    # 分析當內文(legacy 十之二的樣子),不再是一行一項。
    assert "**政院拍板 2027 普發現金一萬元**" in out
    assert "內需消費短多" in out


def test_an_empty_policy_list_adds_no_section():
    """沒有政策新聞的日子不需要一個空段落。"""
    obj = fx.valid_analysis()
    obj["taiwan_policy"] = []
    assert ar.SECTION_POLICY not in ar.render(obj)


def test_the_policy_field_is_required_by_the_schema():
    """strict 模式全欄位必填 —— 欄位不在 `required` 裡,模型可以整個不填,
    而「沒填」與「當日沒有政策新聞」在下游長得一樣。"""
    assert "taiwan_policy" in sch.ANALYSIS_OUTPUT_SCHEMA["required"]
    props = sch.ANALYSIS_OUTPUT_SCHEMA["properties"]["taiwan_policy"]
    assert props["items"]["required"] == sorted(
        ["source_item_id", "what", "impact"])


def test_a_policy_item_missing_either_half_is_not_rendered():
    """「那件事是什麼」與「影響」缺一半就不排 —— 半句話比沒有更糟。"""
    obj = fx.valid_analysis()
    obj["taiwan_policy"] = [
        {"source_item_id": "n1", "what": "只有事件沒有影響", "impact": ""},
        {"source_item_id": "n1", "what": "", "impact": "只有影響沒有事件"}]
    out = ar.render(obj)
    assert ar.SECTION_POLICY not in out


# ---------------------------------------------------------------- 條數 advisory

def test_too_few_items_on_a_rich_day_is_shallow():
    """**條數也是深度**(使用者:「怎麼只有四篇新聞…我要的是更多新聞」)。

    素材充足(≥20 則)而只分析 4 則 → advisory → 走既有的加深迴圈。
    """
    obj = {"top_news_analysis": [{"materiality": "low"}] * 4}
    advs = ad.depth_advisories(obj, {"news": [{}] * 30})
    assert any("只有 4 則" in a for a in advs), advs


def test_a_thin_news_day_does_not_demand_padding():
    """**反向:素材貧乏的日子不硬湊** —— 湊出來的那幾則會是把同一件事
    寫兩遍。"""
    obj = {"top_news_analysis": [{"materiality": "low"}] * 4}
    advs = ad.depth_advisories(obj, {"news": [{}] * 10})
    assert not any("只有" in a and "則" in a for a in advs), advs


def test_enough_items_do_not_trigger_the_advisory():
    """六則以上就不再要求 —— 目標是涵蓋,不是無上限灌條數。"""
    obj = {"top_news_analysis": [{"materiality": "low"}] * 6}
    advs = ad.depth_advisories(obj, {"news": [{}] * 30})
    assert not any("目標 6" in a for a in advs), advs


def test_the_prompt_asks_for_enough_items_and_non_tech_coverage():
    """prompt 要真的說出目標 —— advisory 只能事後補救,第一次就寫夠
    比修補一次便宜。2026-08-22 使用者回饋「科技與其他類股都偏少」後,
    目標從六到十改成十到十六,並改成**兩段各有下限**(八與九是獨立段落,
    不是一段的附屬)。"""
    text = _io.open(_ROOT / "prompt_profiles.py", encoding="utf-8").read()
    assert "十五到二十則為目標" in text   # 2026-08-24 使用者再次反映條數偏少
    assert "科技至少八則、科技之外至少七則" in text
    # 第四批的骨架欄位也要在 prompt 裡有寫法說明 —— 只在 schema 宣告而
    # prompt 不提,模型會全部給空(strict 允許空值,而空值合法)。
    for field in ("taiwan_policy", "world_events", "upcoming_event_scenarios",
                  "narrative_delta", "macro_environment", "taiwan_local"):
        assert f"`{field}`" in text, f"prompt 沒有 {field} 的寫法說明"


# ---------------------------------------------------------------- 不得回來

def test_the_deleted_sections_do_not_come_back():
    """2026-08-19 使用者刪掉的東西:市場合併段、七段的失效條件、
    ETF 進出參考欄。**偷偷回來與偷偷消失一樣要被看見。**"""
    out = ar.render(fx.valid_analysis())
    assert "今日市場關注與預測" not in out
    assert "什麼情況代表這個判斷錯了" not in out
    import morning_report as mr
    assert not hasattr(mr, "_etf_band_cell")
    src = _io.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    assert "今日進出參考</th>" not in src


# ------------------------------------------------- 外審 2026-08-19 的兩項

def test_a_fabricated_policy_source_id_is_rejected():
    """**政策段的引用也要真的存在**(外審 P1)。

    漏了這一關,一個捏造的 `source_item_id` 會讓政策與它宣稱的影響
    **看起來有根據地**進信 —— 那正是引用檢查存在的理由。
    """
    obj = fx.valid_analysis()
    obj["taiwan_policy"] = [{"source_item_id": "捏造的id",
                             "what": "某政策", "impact": "某影響"}]
    probs = [p for p in sch.validate(obj, fx.ids()) if "taiwan_policy" in p]
    assert probs, "捏造的政策引用通過了驗證"
    # 反向:真的 ID 不報
    obj["taiwan_policy"] = [{"source_item_id": "n1",
                             "what": "某政策", "impact": "某影響"}]
    assert not [p for p in sch.validate(obj, fx.ids()) if "taiwan_policy" in p]


def test_a_deepened_response_cannot_drop_the_policy_section():
    """**加深不得換掉或刪掉政策段**(外審 P2)。

    條數 advisory 觸發的加深可以「補了新聞、刪了政策段」而勝出 ——
    使用者才剛要回來的段落又靜默消失。內容也要保:換一句 impact
    就是換了一個結論。
    """
    before = {"taiwan_policy": [{"source_item_id": "n1",
                                 "what": "普發現金", "impact": "內需短多"}]}
    dropped = {"taiwan_policy": []}
    reworded = {"taiwan_policy": [{"source_item_id": "n1",
                                   "what": "普發現金", "impact": "改寫過的影響"}]}
    idb = ad._identity(before)
    assert idb["政策項"] - ad._identity(dropped)["政策項"], "刪掉政策段沒被看見"
    assert idb["政策項"] - ad._identity(reworded)["政策項"], "改寫 impact 沒被看見"


# ------------------------------------------------- 第四批:legacy 骨架

def _full(**over):
    obj = fx.valid_analysis()
    obj["world_events"] = [{"source_item_id": "n1", "what": "美沙簽署核能合作協議",
                            "why_it_matters": "中東勢力格局重組"}]
    obj["upcoming_event_scenarios"] = [
        {"when": "08/20 02:00", "event": "FOMC 會議紀要",
         "base_expectation": "按兵不動基調", "bull_case": "偏鴿利多 00662",
         "bear_case": "偏鷹利空成長股", "most_affected": "00662",
         "invalidation": "油價暴漲蓋過紀要", "evidence_ids": ["n1"]}]
    obj["narrative_delta"] = [{"prior_view_id": "pv1",
                               "prior_view": "美伊戰局逼近十字路口",
                               "change": "升溫",
                               "evidence_today": "油價單日 +3.79%",
                               "evidence_ids": ["n1"]}]
    obj["macro_environment"] = {
        "us_rates_fx_vix": {"analysis": "10Y 4.657% 高檔",
                            "evidence_ids": ["n1"]},
        "fed_policy": {"analysis": "Warsh 鷹派發酵", "evidence_ids": ["n1"]},
        "geopolitics": {"analysis": "三線地緣升溫", "evidence_ids": ["n1"]}}
    obj["taiwan_local"] = [{"source_item_id": "n1",
                            "what": "中經院估 GDP 破 10%",
                            "impact": "支撐高本益比"}]
    obj.update(over)
    return obj


def test_the_legacy_skeleton_renders_in_order():
    """**七之二 → 七之三 → 七之四 → 十 → 十之二 → 十一。**(七之五已刪,2026-09-03)

    使用者貼了幾個禮拜前的完整實信要求照做 —— 這些段落在 legacy 信都
    存在、在特化 schema 先前沒有對應欄位,於是整段消失。
    """
    obj = _full()
    obj["taiwan_policy"] = [{"source_item_id": "n1", "what": "太陽光電標準第9條",
                             "impact": "需求透過模組與工程訂單傳導。"}]
    out = ar.render(obj)
    order = [ar.SECTION_WORLD, ar.SECTION_48H, ar.SECTION_DELTA,
             ar.SECTION_MACRO, ar.SECTION_POLICY, ar.SECTION_LOCAL]
    idx = [out.index(t) for t in order]
    assert idx == sorted(idx), [out.index(t) for t in order]
    # 2026-08-29 使用者:七之二改一氣呵成段落(標題句。解讀句。)
    assert "美沙簽署核能合作協議。中東勢力格局重組。" in out
    assert "基準預期:按兵不動基調" in out
    assert "「美伊戰局逼近十字路口」→ **升溫**" in out
    assert "**(A)** 10Y 4.657% 高檔" in out
    assert "中經院估 GDP 破 10%:支撐高本益比" in out


def test_empty_skeleton_fields_add_no_sections():
    """空欄位不出段 —— legacy 骨架不是每天每段都有內容。"""
    out = ar.render(fx.valid_analysis())
    for t in (ar.SECTION_WORLD, ar.SECTION_48H, ar.SECTION_DELTA,
              ar.SECTION_MACRO, ar.SECTION_LOCAL):
        assert t not in out, t




def test_fabricated_ids_in_the_new_sections_are_rejected():
    """world_events / taiwan_local 的引用也要真的存在 —— 與 taiwan_policy
    同一關(捏造的引用會讓內容「看起來有根據地」進信)。"""
    obj = _full()
    obj["world_events"][0]["source_item_id"] = "捏造的id"
    probs = [p for p in sch.validate(obj, fx.ids()) if "world_events" in p]
    assert probs, "捏造的世界大事引用通過了驗證"
    obj2 = _full()
    obj2["taiwan_local"][0]["source_item_id"] = "捏造的id"
    assert [p for p in sch.validate(obj2, fx.ids()) if "taiwan_local" in p]


def test_a_deepened_response_cannot_drop_the_skeleton():
    """加深不得刪掉 legacy 骨架的任何一段(與政策段同一條理由)。"""
    full = _full()
    empty = fx.valid_analysis()
    idf = ad._identity(full)
    ide = ad._identity(empty)
    for key in ("世界大事", "在地動態", "情境事件", "敘事變化"):
        assert idf[key] - ide[key], f"{key} 沒有進加深身分"


def test_a_scenario_without_evidence_is_rejected():
    """**虛構的未來事件要擋得住**(外審第二輪):情境要引用 EVIDENCE 裡
    真的存在的 ID;沒有來源的未來事件與編的沒有分別。"""
    obj = _full()
    obj["upcoming_event_scenarios"][0]["evidence_ids"] = []
    probs = [p for p in sch.validate(obj, fx.ids())
             if "upcoming_event_scenarios" in p]
    assert probs, "沒有來源的情境通過了驗證"
    obj["upcoming_event_scenarios"][0]["evidence_ids"] = ["捏造的id"]
    assert [p for p in sch.validate(obj, fx.ids())
            if "upcoming_event_scenarios" in p]




def test_deepen_cannot_blank_a_render_critical_field():
    """**身分要含所有會改變渲染內容或可見性的欄位**(外審第二輪):
    加深版本保留 ID/標題、清空 impact/evidence_today,整段會靜默消失。"""
    full = _full()
    blanked_local = _full()
    blanked_local["taiwan_local"] = [dict(full["taiwan_local"][0], impact="")]
    blanked_delta = _full()
    blanked_delta["narrative_delta"] = [dict(full["narrative_delta"][0],
                                             evidence_today="")]
    blanked_scen = _full()
    blanked_scen["upcoming_event_scenarios"] = [
        dict(full["upcoming_event_scenarios"][0], base_expectation="")]
    idf = ad._identity(full)
    assert idf["在地動態"] - ad._identity(blanked_local)["在地動態"], \
        "清空 impact 沒被看見"
    assert idf["敘事變化"] - ad._identity(blanked_delta)["敘事變化"], \
        "清空 evidence_today 沒被看見"
    assert idf["情境事件"] - ad._identity(blanked_scen)["情境事件"], \
        "清空情境內文沒被看見"


def test_bull_bear_and_target_are_python_authority():
    """**「最強/最相關」是排名,不變式是 Python 算、模型抄**
    (外審 2026-08-19 三輪定案 —— 模型自選的版本被駁回)。

    schema **不得**有這兩個欄位(模型沒有決定權);段落與標記改由
    Python 權威值渲染:多空交鋒來自 11 維立場分的逐維貢獻極值
    (packet 的 `stance_extremes`),最相關標記由事件群的編輯標註實體
    推導(2330 / NDX 名單)。
    """
    props = sch.ANALYSIS_OUTPUT_SCHEMA["properties"]
    assert "bull_bear" not in props
    assert "primary_target" not in props["key_drivers"]["items"]["properties"]

    # 2026-09-03 使用者刪掉「七之五、多空交鋒」:權威值仍進 packet 給模型當
    # 證據,但**不再渲染成一段** —— 有極值也不排。
    pk = {"stance_extremes": {"bull": {"dim": "sox", "text": "費半 SOX +2.00%"},
                              "bear": {"dim": "wti", "text": "WTI 油價 +3.79%"}}}
    out = ar.render(fx.valid_analysis(), pk)
    assert "多空交鋒" not in out and "多方最強" not in out
    assert not hasattr(ar, "SECTION_BULLBEAR")


def test_the_target_label_is_derived_from_editorial_entities():
    """最相關標記由**編輯標註實體**推導(Python),模型不參與。"""
    def _pk(entities):
        return {"news": [{"source_item_id": "n1", "title": "某事件",
                          "entities": entities}],
                "news_clusters": {"clusters": [
                    {"cluster_id": "c1", "member_source_ids": ["n1"]}]}}
    assert ar._derived_target(_pk(["2330"]), "c1") == "2330"
    assert ar._derived_target(_pk(["NVDA"]), "c1") == "00662"
    # 編輯標註同一家公司會用不同寫法 —— 先過 entity_alias 正規化再比
    # (外審 2026-08-19 第四輪:逐字比對 TSMC/台積 會漏)
    assert ar._derived_target(_pk(["TSMC"]), "c1") == "2330"
    assert ar._derived_target(_pk(["台積"]), "c1") == "2330"
    assert ar._derived_target(_pk(["輝達"]), "c1") == "00662"
    # 2330 優先於 NDX(台積電新聞常同時帶美系客戶)
    assert ar._derived_target(_pk(["2330", "NVDA"]), "c1") == "2330"
    # 推不出來就不掛 —— 硬掛「市場最相關」是廢話
    assert ar._derived_target(_pk(["2882"]), "c1") == ""
    assert ar._derived_target({}, "c1") == ""


def test_the_extremes_need_both_signs():
    """全空方的日子挑不出「多方最強」→ 回空 —— 硬湊一個正貢獻最小的
    當多方,是把排名變成謊言。

    fixture 用**生產形狀**:QQQ/TSM 在 quotes 頂層、SOX/WTI 在 MACRO
    (計分器就是這樣讀的;外審 2026-08-19 第四輪抓到第一版把 QQQ
    塞進 MACRO —— 測了一個生產不會發生的情境)。"""
    import morning_report as mr
    all_bear = {"QQQ": {"change_pct": -1.69},
                "MACRO": {"SOX": {"change_pct": -4.98},
                          "WTI": {"change_pct": 3.79}}}
    assert mr._stance_extremes(all_bear) == {}
    mixed = {"QQQ": {"change_pct": 0.6},
             "MACRO": {"SOX": {"change_pct": 5.0},
                       "WTI": {"change_pct": 3.79}}}
    out = mr._stance_extremes(mixed)
    assert out["bull"]["dim"] == "sox" and out["bear"]["dim"] == "wti", out
    # 顯示的是市場事實(漲跌幅),不是 ±分數 —— 計分內部不外露(批#26)
    assert "%" in out["bull"]["text"] and "分" not in out["bull"]["text"]


def test_the_extremes_tie_break_is_threshold_excess():
    """components 都是 ±1,同分是常態 —— 「最強」不能是欄位插入順序。
    tie-break 比**超越門檻的幅度**(|值|/該維門檻),讀值器與計分器
    走同一條資料路徑(外審 2026-08-19 第四輪定案)。"""
    import morning_report as mr
    # qqq +0.6(0.6/0.5=1.2 倍門檻)vs sox +5.0(5 倍)→ sox 最強
    q = {"QQQ": {"change_pct": 0.6},
         "MACRO": {"SOX": {"change_pct": 5.0},
                   "WTI": {"change_pct": 3.79}}}
    assert mr._stance_extremes(q)["bull"]["dim"] == "sox"
    # 反向:qqq +2.0(4 倍)vs sox +1.2(1.2 倍)→ qqq 最強;
    # 且顯示值從**頂層** quotes["QQQ"] 讀到(路徑錯了就是光禿禿的名字)
    q2 = {"QQQ": {"change_pct": 2.0},
          "MACRO": {"SOX": {"change_pct": 1.2},
                    "WTI": {"change_pct": 3.79}}}
    out2 = mr._stance_extremes(q2)
    assert out2["bull"]["dim"] == "qqq"
    assert out2["bull"]["text"] == "QQQ +2.00%", out2


def test_the_extremes_excess_is_directional():
    """有基準點的維度(廣度基準 50、VIX 基準 18/22)不能用 |值|/尺度 ——
    那會反向排序:廣度 40%(剛觸發)算出 0.8 「強」、極端的 10% 反而
    只有 0.2(外審 2026-08-19 第五輪)。強度沿**觸發方向**量,零點在
    該維自己的門檻上。"""
    import morning_report as mr
    # 廣度 10%(超門檻 1.5 單位)vs WTI +3.5(0.17 單位)→ 廣度是空方最強
    q1 = {"QQQ": {"change_pct": 0.6}, "BREADTH": {"advance_ratio": 10},
          "MACRO": {"WTI": {"change_pct": 3.5}}}
    assert mr._stance_extremes(q1)["bear"]["dim"] == "breadth"
    # 廣度 40%(剛觸發,0 單位)vs WTI +6(1.0 單位)→ WTI 才是最強
    q2 = {"QQQ": {"change_pct": 0.6}, "BREADTH": {"advance_ratio": 40},
          "MACRO": {"WTI": {"change_pct": 6.0}}}
    assert mr._stance_extremes(q2)["bear"]["dim"] == "wti"
    # VIX 10(低於門檻 18 兩個帶寬)vs QQQ +0.6 → VIX 是多方最強;
    # VIX 17(0.25 單位)vs QQQ +2.0(3 單位)→ QQQ 才是
    q3 = {"QQQ": {"change_pct": 0.6},
          "MACRO": {"VIX": {"close": 10}, "WTI": {"change_pct": 3.5}}}
    assert mr._stance_extremes(q3)["bull"]["dim"] == "vix"
    q4 = {"QQQ": {"change_pct": 2.0},
          "MACRO": {"VIX": {"close": 17}, "WTI": {"change_pct": 3.5}}}
    assert mr._stance_extremes(q4)["bull"]["dim"] == "qqq"


def test_the_extremes_units_match_the_data():
    """foreign_top10 是 `foreign_lot` 加總,單位「張」(prompt 同樣格式)
    —— 第一版標「億」會把 17,131 張寄成 +17131 億(外審 2026-08-19
    第五輪)。逐維單位是宣告不是猜測。"""
    import morning_report as mr
    q = {"FOREIGN_TOP10_TOTAL": 17131.0,
         "MACRO": {"WTI": {"change_pct": 3.5}}}
    out = mr._stance_extremes(q)
    assert out["bull"]["text"] == "外資十大買賣超 +17,131 張", out
    assert "億" not in out["bull"]["text"]


def test_the_vix_display_shows_the_triggering_evidence():
    """VIX 計分是雙條件(close 18/22 或一年百分位 30/70)——percentile
    觸發、close 中性或缺值時只顯示 close,會寄出看似中性的「VIX 20.00」
    或光禿禿的名字(外審 2026-08-19 第六輪)。顯示要選**實際觸發該方向**
    的條件,兩者都觸發就並列。"""
    import morning_report as mr

    def _q(vix):
        return {"QQQ": {"change_pct": 0.6},
                "MACRO": {"VIX": vix, "WTI": {"change_pct": 3.5}}}
    # percentile 單獨觸發(close 缺值)
    out = mr._stance_extremes(_q({"pct_rank_252d": 10}))
    assert out["bull"]["text"] == "VIX 恐慌指數 一年百分位 10%", out
    # close 中性(20)+ percentile 觸發 → 證據是 percentile,不是 20.00
    out = mr._stance_extremes(_q({"close": 20, "pct_rank_252d": 10}))
    assert out["bull"]["dim"] == "vix"
    assert out["bull"]["text"] == "VIX 恐慌指數 一年百分位 10%", out
    # 兩者都觸發 → 並列
    out = mr._stance_extremes(_q({"close": 10, "pct_rank_252d": 5}))
    assert out["bull"]["text"] == "VIX 恐慌指數 10.00(一年百分位 5%)", out
    # close 單獨觸發(原行為不變)
    out = mr._stance_extremes(_q({"close": 10}))
    assert out["bull"]["text"] == "VIX 恐慌指數 10.00", out


def test_the_packet_carries_the_extremes():
    """**沒有接進 packet 的權威值等於沒有**(渲染端只看 packet)。"""
    import io as _io2
    src = _io2.open(_ROOT / "morning_report.py", encoding="utf-8").read()
    assert '_packet["stance_extremes"] = _stance_extremes(quotes)' in src
