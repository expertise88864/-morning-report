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

_SRC = io.open(Path(__file__).resolve().parents[1] / "morning_report.py",
               encoding="utf-8").read()


def test_taiex_kpi_cells_are_a_table_not_flex():
    """第五段資訊格是 2×2 表格:flex 在部分郵件客戶端不支援,
    min-width×4 會把版面撐到 600px+,手機字被縮小(2026-08-20 使用者)。"""
    i = _SRC.index(">五、加權指數開盤預測</h2>")
    seg = _SRC[i:i + 4000]
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
