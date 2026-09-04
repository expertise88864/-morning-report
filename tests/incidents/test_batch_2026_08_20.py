# -*- coding: utf-8 -*-
"""2026-08-20 生產回饋批:第五段 2×2 版面 + 佐證等級代抄。

實信問題:(1) 第五段四格並排把信件左右拉長,iPhone 字被縮小;
(2) 特化輸出因「佐證等級誇大 ×2 + 鏈斷 ×1」被擋 5 條、修補沒收斂,
整份退回既有路徑 —— 佐證等級的真值本來就在 packet,叫模型猜再駁回
是白燒配額。
"""
import io
from pathlib import Path

import morning_report as mr

_SRC = io.open(Path(mr.__file__).resolve().parent / "morning_report.py",
               encoding="utf-8").read()


def test_taiex_kpi_cells_are_a_table_not_flex():
    """第五段資訊格是 2×2 表格:flex 在部分郵件客戶端不支援,
    min-width×4 會把版面撐到 600px+,手機字被縮小(2026-08-20 使用者)。

    第二批把 % 併進大字後,格子改成先組好(`_cell_close`/`_cell_cons`)再塞進
    模板 —— 探針窗因此從模板抬頭改成涵蓋「組格子 → 模板」整段,斷言不變。"""
    i = _SRC.index('night_row_html = ""')
    seg = _SRC[i:_SRC.index("{_pred_grid_rows}") + 200]
    assert "display:flex" not in seg, "第五段仍在用 flex 並排"
    assert "table-layout:fixed" in seg, "資訊格沒有走固定欄寬表格"
    assert seg.count('width:50%') >= 3, "不是兩欄(50%)的 2×2 排法"
    # 夜盤 cell 也要是 <td>(它插在表格列裡,div 會破表)
    j = _SRC.index("night_row_html = (")
    assert "<td" in _SRC[j:j + 400], "夜盤格不是 <td>"


def test_overstated_corroboration_is_rewritten_to_the_computed_value():
    """佐證等級「Python 算、模型抄」—— 誇大就代抄(canonicalize-not-
    reject),並補確定性 caveat;不再讓兩筆誇大燒掉修補配額、整份作廢。"""
    obj = {"top_news_analysis": [
        {"source_item_id": "n1", "corroboration_assessment": "multi_source",
         "source_caveat": ""}]}
    pk = {"news_clusters": {"clusters": [
        {"member_source_ids": ["n1"], "corroboration": "single_source"}]}}
    mr._align_corroboration(obj, pk)
    row = obj["top_news_analysis"][0]
    assert row["corroboration_assessment"] == "single_source", row
    assert row["source_caveat"].strip(), "降級成弱等級卻沒補 caveat(會被下一關擋)"


def test_conservative_corroboration_is_left_alone():
    """反向:模型比計算值保守時不動 —— validator 本來就放行保守,
    往上「校正」等於替模型誇大。"""
    obj = {"top_news_analysis": [
        {"source_item_id": "n2", "corroboration_assessment": "single_source",
         "source_caveat": "僅一家"}]}
    pk = {"news_clusters": {"clusters": [
        {"member_source_ids": ["n2"], "corroboration": "official"}]}}
    mr._align_corroboration(obj, pk)
    assert obj["top_news_analysis"][0]["corroboration_assessment"] == "single_source"


def test_alignment_is_wired_before_phantom_pruning():
    """接線:與證據 ID 正規化同一段(修剪之前)—— 沒接上等於不存在。"""
    i = _SRC.index("_align_corroboration(obj, packet)",
                   _SRC.index("def _align_corroboration") + 100)
    seg = _SRC[i:i + 200]
    assert "_prune_phantom_audit_ids" in seg, "改寫沒有排在修剪之前"


# --------------------------------------------- 2026-08-21 實信三項


def test_top3_items_render_as_separate_paragraphs():
    """08/21 實信:三大重點用單一換行 join,被 markdown 摺成同一段 ——
    三條黏成一坨。要空行分段。"""
    import sys
    sys.path.insert(0, "tests")
    import analysis_render as ar
    import fixtures_analysis as fx
    o = fx.valid_analysis()
    o["key_drivers"] = [fx._driver("Fed 訊號轉鷹"),
                        fx._driver("台積電資本支出上修"),
                        fx._driver("油價創月新高")]
    out = ar.render(o)
    i = out.find(ar.SECTION_TOP3)
    assert i >= 0
    seg = out[i:]
    nxt = seg.find("\n## ")
    seg = seg[:nxt] if nxt > 0 else seg
    paras = [x for x in seg.split("\n\n") if x.strip()]
    # 真正的不變式:三條重點**各在不同段**(標題與第一條同段無妨,
    # `## ` 行在 markdown 裡自成 h2)。
    homes = [next(i for i, x in enumerate(paras) if kw in x)
             for kw in ("Fed 訊號轉鷹", "台積電資本支出上修", "油價創月新高")]
    assert len(set(homes)) == 3, f"三條重點沒有各自成段:{homes}"


def test_low_volume_marker_has_no_warning_emoji():
    """08/21 實信:「機率 50%(量低⚠)」的 ⚠ 漏拆(聚合行已拆,單一
    市場那條漏了)—— 使用者已要求全部移除。"""
    import io
    from pathlib import Path
    src = io.open(Path(mr.__file__).resolve().parent / "morning_report.py",
                  encoding="utf-8").read()
    assert "量低⚠" not in src


def test_empty_macro_and_policy_sections_get_deepen_advisories():
    """08/21 實信:特化成功的第一天,十、總經與十之二、政策深析整段
    消失(素材在、欄位空)。合法但淺 —— 加深要點名;填了就不吵。"""
    import sys
    sys.path.insert(0, "tests")
    import analysis_depth as ad
    import fixtures_analysis as fx
    # 2026-08-22 外審 P3:觸發改看**真的有沒有素材**,不是新聞則數/
    # 公報清單非空。這裡給利率素材與**關注分類**(520 金融)的公報。
    pk = {"news": [{}] * 12,
          "market": {"MACRO": {"10Y": {"close": 4.74}},
                     "GAZETTE_RECORDS": [
                         {"title": "銀行法修正", "category_codes": ["520"]},
                         {"title": "產業條例", "category_codes": ["550"]}]}}
    advs = ad.depth_advisories(fx.valid_analysis(), pk)
    assert any("macro_environment" in a for a in advs), advs
    assert any("taiwan_policy" in a for a in advs), advs
    filled = fx.valid_analysis()
    filled["macro_environment"] = {
        "us_rates_fx_vix": {"analysis": "10Y 高檔", "evidence_ids": ["n1"]},
        "fed_policy": {"analysis": "", "evidence_ids": []},
        "geopolitics": {"analysis": "", "evidence_ids": []}}
    filled["taiwan_policy"] = [{"source_item_id": "n1", "what": "x",
                                "impact": "y"}]
    advs2 = ad.depth_advisories(filled, pk)
    assert not any("macro_environment" in a for a in advs2), advs2
    assert not any("taiwan_policy" in a for a in advs2), advs2


def test_deepen_advisories_do_not_fire_without_the_material():
    """外審 P3 點名的兩個誤觸情境 —— 素材不在就不該催,否則是在誘導
    模型寫它手上沒有的東西(而那正是幽靈引用的來源)。"""
    import sys
    sys.path.insert(0, "tests")
    import analysis_depth as ad
    import fixtures_analysis as fx
    obj = fx.valid_analysis()
    # (1) 12 則全是公司新聞、利率/匯率/地緣素材都不在 → 不催總經
    quiet = {"news": [{}] * 12, "market": {"QQQ": {"close": 700}}}
    assert not any("macro_environment" in a
                   for a in ad.depth_advisories(obj, quiet)), "沒素材還催總經"
    # (2) 公報有東西但**全部不在關注分類** → 不催政策深析
    offtopic = {"news": [{}] * 12,
                "market": {"GAZETTE_RECORDS": [
                    {"title": "某機關人事令", "category_codes": ["999"]}]}}
    assert not any("taiwan_policy" in a
                   for a in ad.depth_advisories(obj, offtopic)), "非關注分類也催"
    # (3) 地緣事件在 → 總經該催(判準不是整個失效)
    geo = {"news": [], "market": {"STRUCTURED_NEWS_EVENTS": [
        {"event_type": "geopolitical", "title": "制裁"}]}}
    assert any("macro_environment" in a for a in ad.depth_advisories(obj, geo))


def test_error_only_macro_is_not_evidence():
    """r1 外審:`fetch_macro_indicators` 全線失敗時仍寫出
    `{"10Y": {"error": ...}}` —— 整個 MACRO 是 truthy。只問「在不在」
    等於斷料那天照樣催,而那正是這批要消掉的行為。"""
    import sys
    sys.path.insert(0, "tests")
    import analysis_depth as ad
    import fixtures_analysis as fx
    obj = fx.valid_analysis()
    broken = {"news": [{}] * 12, "market": {"MACRO": {
        "10Y": {"error": "資料不足"}, "VIX": {"error": "timeout"}}}}
    assert not any("macro_environment" in a
                   for a in ad.depth_advisories(obj, broken)), "只有錯誤紀錄也算素材"
    # 一格壞、一格好 → 仍算有素材(判準不得因防護而整個失效)
    partial = {"news": [], "market": {"MACRO": {
        "10Y": {"error": "資料不足"}, "VIX": {"close": 15.1}}}}
    assert any("macro_environment" in a
               for a in ad.depth_advisories(obj, partial))
