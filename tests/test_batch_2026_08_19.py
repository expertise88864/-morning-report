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
    assert "普發現金一萬元:內需消費短多" in out


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


def test_the_prompt_asks_for_six_to_ten_and_non_tech_coverage():
    """prompt 要真的說出目標 —— advisory 只能事後補救,第一次就寫夠
    比修補一次便宜。"""
    text = _io.open(_ROOT / "prompt_profiles.py", encoding="utf-8").read()
    assert "六到十則為目標" in text
    assert "至少一到兩則" in text
    assert "taiwan_policy" in text


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
