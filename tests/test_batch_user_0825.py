# -*- coding: utf-8 -*-
"""2026-08-25 使用者:「世界大事速覽也要有解析這條新聞的後續可能影響」。

08/25 那封信的七之二寫的是「中印關係破冰,金磚擴員後首次峰會,地緣政治
緩和有指標意義,但美中對抗結構未變」—— 那回答的是「現在為什麼重要」,
不是「接下來會怎樣」。兩件不同的事壓成一句,讀者拿不到可以拿去做判斷的
東西。做成**欄位**而不是多一句叮嚀,因為 strict schema 會強制它出現。
"""
import io
from pathlib import Path

import analysis_render as ar
import analysis_schema as sch
import prompt_profiles as pp
import writing_rules as wr


def test_world_events_schema_asks_for_the_follow_on_impact():
    spec = sch.ANALYSIS_OUTPUT_SCHEMA["properties"]["world_events"]
    props = spec["items"]["properties"]
    assert "what_next" in props, sorted(props)
    # strict schema:每個欄位都必填,所以它一定會出現(不是「有空就寫」)
    req = spec["items"].get("required") or []
    assert "what_next" in req, req
    # 三個欄位問的是三件事 —— 說明文字要分得開
    assert "後續" in props["what_next"]["description"]
    assert "戰略意涵" in props["why_it_matters"]["description"]


def test_both_prompts_ask_for_it():
    """**legacy 那條路也要改** —— 08/25 那班的七之二正是 legacy 產生的
    (`analysis_origin = legacy_fallback_after_luna_failure`),只改特化路徑
    的話使用者在 Luna 恢復之前一天都看不到。"""
    luna = io.open(Path(pp.__file__), encoding="utf-8").read()
    assert "what_next" in luna and "後續可能影響" in luna
    legacy = io.open(Path(wr.__file__), encoding="utf-8").read()
    i = legacy.index("七之二、世界大事速覽")
    seg = legacy[i:i + 1400]
    # **每條的組成**那一行要真的列出三段(只在段落裡別處提到不算 ——
    # 模型照的是「每條=…」那條規格)
    j = seg.index("1. 每條=")
    # 只看**組成**那一句(到「字。」為止)—— 底下的說明段落也會提到
    # 同樣的詞,拿整段比對的話,把組成拆掉也照樣「通過」。
    rule = seg[j:seg.index("字。", j)]
    assert "發生什麼" in rule and "所以會怎樣" in rule, rule
    assert "後續可能影響" in rule, rule
    # 兩段問的是不同的問題,規則本身要說出來(否則模型會寫成同一句)
    assert "不同的問題" in seg, seg[:400]


def test_the_length_retry_does_not_flatten_it_away():
    """長度重試是**常態不是例外**:那一段先前寫「世界大事速覽最多 4 條、
    每條一行」,一行裝不下「所以會怎樣 + 後續可能影響」。限制條數,
    不要限制那一條的完整性。"""
    import morning_report as mr
    src = io.open(Path(mr.__file__), encoding="utf-8").read()
    i = src.index("【長度控制追加規則】")
    seg = src[i:i + 900]
    # 只看**送給模型的字串**,不看旁邊的註解(註解會引述舊文字)
    sent = "".join(ln for ln in seg.splitlines()
                   if ln.strip().startswith('"') or ln.strip().startswith("'"))
    assert "每條一行" not in sent, sent[:400]
    assert "後續可能影響" in sent, sent[:400]


def test_the_renderer_shows_it_on_its_own_line():
    """併在同一句尾巴會被讀成同一件事。沒有那一欄的舊資料要照舊能渲染。"""
    import sys
    sys.path.insert(0, "tests")
    import fixtures_analysis as fx
    obj = fx.valid_analysis()
    obj["world_events"] = [
        {"what": "川普家族獲准設立國家銀行 (Bloomberg)",
         "why_it_matters": "銀行監管鬆綁,穩定幣與傳統銀行界線模糊",
         "what_next": "若其他家族企業跟進申請,監管標準會被反覆測試;"
                      "要盯的是首張執照的附帶條件"},
        {"what": "沒有後續欄位的舊資料", "why_it_matters": "仍要渲染"}]
    md = ar.render(obj)
    assert md, "fixture 渲染不出東西,量不到這條規則"
    assert "後續可能影響:若其他家族企業跟進申請" in md, md
    assert "沒有後續欄位的舊資料:仍要渲染" in md, md
    # 舊資料不得長出一個空的「後續可能影響:」
    assert md.count("後續可能影響") == 1, md


def test_an_empty_follow_on_is_rejected_not_silently_dropped():
    """外審:strict schema 的 `required` 只保證**欄位在**,`type: string`
    收下 `""` —— 而渲染端沒有那一欄就整行省略,於是使用者要的那件事會
    **靜默消失**,信看起來完全正常。(我在 context 裡寫「strict schema
    保證非空」,那句話本身就是錯的。)"""
    import sys

    import analysis_schema as _sch
    sys.path.insert(0, "tests")
    import fixtures_analysis as fx

    def _problems(rows):
        # **生產的呼叫形狀**是 `_sch.validate(obj, packet)`(morning_report
        # 那一行)—— `analysis_validate.validate(obj, evidence_ids)` 是另一
        # 個介面,拿它來測會量到別的東西。
        obj = fx.valid_analysis()
        obj["world_events"] = rows
        pk = {"news": [{"source_item_id": "n1", "title": "t"}], "market": {}}
        return [p for p in _sch.validate(obj, pk) if "what_next" in p]

    ok = [{"source_item_id": "n1", "what": "川普家族獲准設立國家銀行",
           "why_it_matters": "銀行監管鬆綁",
           "what_next": "若其他家族企業跟進,監管標準會被反覆測試"}]
    assert _problems(ok) == [], _problems(ok)

    for bad in ("", "   "):
        rows = [dict(ok[0], what_next=bad)]
        assert _problems(rows), f"空的 what_next({bad!r})被放行了"
    # 欄位整個缺席也一樣(舊模型/舊快取)
    rows = [{k: v for k, v in ok[0].items() if k != "what_next"}]
    assert _problems(rows), rows

    # `what` 是空的那一列本來就不會進信 —— 不得為它多報一條
    assert _problems([{"source_item_id": "n1", "what": "", "what_next": ""}]) == []
