# -*- coding: utf-8 -*-
"""2026-08-25 生產:信寄出了,但 Luna 被自己的驗證擋下 14 條而落 legacy。

manifest 逐條看下來,其中三條**不是模型的內容問題**:
  * 「不是合法 JSON」—— 回覆是「散文 + ```json 圍欄」,解析器在第 0 個
    字元就死,那一輪被判成語法輪(而語法本來就是對的),
    `repair_modes` 變成 semantic→syntax→semantic 而 semantic 額度是 2。
  * 兩條 `shared_driver_notes 宣稱的驅動是 X,而…被歸類為 Y` —— 而 X 與 Y
    是同一個東西(`label（code）` vs `code`)。
"""
import io
import json
from pathlib import Path

import analysis_contracts as ac
import deepseek_responses as dsr
import event_graph as eg
import morning_report as mr

_FENCE = chr(96) * 3


def test_prose_wrapped_json_is_not_a_syntax_failure():
    """生產原文的形狀:標題、說明、然後才是圍欄裡的 JSON。"""
    text = (chr(10).join([
        "## 修正說明", "",
        "上一輪輸出經逐項檢查,確認以下七項問題須修正;其餘部分照抄。", "",
        "## 修正後的完整輸出", "",
        _FENCE + "json",
        json.dumps({"executive_summary": "輝達財報前美股半導體先跌為敬",
                    "top_news_analysis": []}, ensure_ascii=False),
        _FENCE, "",
        "以上已修正 `n5431e`(top_news_analysis 與 macro_environment)。"]))
    obj, how = dsr.json_object_from_text(text)
    assert how == "fence" and obj["executive_summary"].startswith("輝達"), how
    # 正常情況照舊(裸 JSON 不繞路)
    assert dsr.json_object_from_text('{"a": 1}') == ({"a": 1}, "raw")
    # 連圍欄都沒有的散文夾帶也撿得回來
    assert dsr.json_object_from_text('說明:{"a": 1} 以上')[1] == "braces"
    # **截斷仍然是語法問題**:三種候選都失敗 → 分類是對的,不得硬救
    assert dsr.json_object_from_text("## 說明" + chr(10) + _FENCE + "json"
                                     + chr(10) + '{"a": [1,') == (None, "")
    # 根不是物件的不收(那是結構問題,有它自己的處置)
    assert dsr.json_object_from_text("[1, 2, 3]") == (None, "")


def test_the_primary_parser_uses_the_recovery_and_leaves_a_trace():
    """接線:helper 對了但主解析沒用它,生產照樣落 legacy。
    而且救回來**要留痕** —— 模型正在偏離「只回 JSON」的契約,
    信卻會看起來完全正常。"""
    src = io.open(Path(mr.__file__), encoding="utf-8").read()
    i = src.index('obj = json.loads(out["text"])')
    seg = src[i:i + 1800]
    assert "_dsr.json_object_from_text(out.get(\"text\"))" in seg, seg[:400]
    assert "_parse_exc = obj is None" in seg, seg[:400]
    assert '"recovered_by"' in seg, seg[:600]


def _groups():
    """用 producer 真的算一次(不是手捏 `shared_driver_groups`)。"""
    clusters = [{"cluster_id": f"cluster:{c}", "member_source_ids": [str(i)]}
                for i, c in enumerate("abcd")]
    news = [{"source_item_id": "0", "title": "AI 伺服器資本支出再上修",
             "summary": "資料中心 capex"},
            {"source_item_id": "1", "title": "GPU 需求推升資本支出",
             "summary": "AI server"},
            {"source_item_id": "2", "title": "Fed 官員鴿派發言",
             "summary": "降息預期"},
            {"source_item_id": "3", "title": "美債殖利率回落",
             "summary": "10年期公債"}]
    return eg.build(clusters, news)


def test_the_driver_name_the_model_was_given_is_not_a_rejection():
    """packet 給模型的是 `driver`(代號)**與** `label`(中文名)兩欄,
    而判準只拿代號做字串相等 —— 模型把兩欄接起來寫,就被駁回。
    命名失誤只要指得回唯一一個對象,這個 repo 的作法是正規化收下。"""
    graph = _groups()
    grp = {g["driver"]: g for g in graph["shared_driver_groups"]}
    assert set(grp) == {"ai_capex", "us_monetary"}, sorted(grp)
    assert grp["ai_capex"]["label"] == "AI 資本支出循環"

    packet = {"event_graph": graph,
              "news_clusters": {"clusters": [
                  {"cluster_id": c} for c in
                  ("cluster:a", "cluster:b", "cluster:c", "cluster:d")]}}

    def _problems(driver_text, ids):
        obj = {"cross_market_synthesis": {"shared_driver_notes": [
            {"driver": driver_text, "cluster_ids": ids,
             "why_not_double_counting": "同一條傳導鏈"}]}}
        return [p for p in ac.reference_problems(obj, packet)
                if "宣稱的驅動" in p]

    ab = ["cluster:a", "cluster:b"]
    # 生產那天實際寫的兩種形狀
    assert _problems("AI 資本支出循環（ai_capex）", ab) == []
    assert _problems("聯準會政策路徑、美債殖利率、美國就業、美國通膨"
                     "（us_monetary）", ["cluster:c", "cluster:d"]) == []
    # 代號本身、標籤本身都照樣算對
    assert _problems("ai_capex", ab) == []
    assert _problems("AI 資本支出循環", ab) == []
    # 代號帶標籤(反過來的順序)也算對
    assert _problems("ai_capex(AI 資本支出循環)", ab) == []

    # **真的寫錯還是要擋。** r1 外審:第一版用無錨點的詞法搜尋,於是
    # 「代號有出現」就算過 —— 判準比它自己的 docstring 寬,一個指錯或
    # 含糊的驅動名會把 Luna 判成合格而不要求修補。
    for bad in ("us_monetary",                    # 指到別組
                "ai_capex_extra",                 # 只是前綴相同
                "美國就業（ai_capex）",             # 標籤是錯的,代號對
                "不是 ai_capex",                   # 散文夾帶
                "fed_policy / ai_capex"):         # 兩個代號,含糊
        assert _problems(bad, ab), f"{bad!r} 被放行了"
