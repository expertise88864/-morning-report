# -*- coding: utf-8 -*-
"""2026-08-26 儲值後重跑:Luna 仍落 legacy,十條駁回裡有兩條是
`gap:payload_omitted:HISTORY` / `…:STRUCTURED_NEWS_EVENTS` 沒揭露。

那兩件事**是 Python 算出來的**(`payload_budget.trim` 知道裁了哪個區塊、
多少字元,連要顯示的句子都寫好放在 `required_disclosures`),卻要求模型
逐字抄回 `data_gaps`,抄漏就整份作廢 —— 讀者因此拿到完全沒有揭露的
legacy 版本,比補上去更糟。
"""
import io
from pathlib import Path

import morning_report as mr
import payload_budget as pb


def test_python_backfills_only_what_it_actually_knows():
    need = {"gap:payload_omitted:HISTORY": "這塊資料今天太大(1,046,782 字元),"
                                           "沒有進到分析輸入",
            "gap:payload_omitted:STRUCTURED_NEWS_EVENTS": "這塊資料今天太大",
            "gap:other:t_rates_vs_tech": "張力查不成"}
    obj = {"data_gaps": [{"gap_id": "gap:other:y", "what_is_missing": "a",
                          "impact_on_conclusions": "b"}]}
    mr._backfill_machine_known_gaps(obj, {"required_disclosures": need})
    ids = [g["gap_id"] for g in obj["data_gaps"]]
    assert "gap:payload_omitted:HISTORY" in ids
    assert "gap:payload_omitted:STRUCTURED_NEWS_EVENTS" in ids
    # **張力類的不代抄**:那要說出「所以今天少了什麼判斷」,Python 沒有
    # 那個答案 —— 代抄等於幫模型編一句它沒想過的話。
    assert "gap:other:t_rates_vs_tech" not in ids, ids
    # 模型自己寫過的不重複
    assert ids.count("gap:other:y") == 1
    # 補上去的那一列要有 Python 算出來的**實際內容**(不是佔位字串)
    row = next(g for g in obj["data_gaps"]
               if g["gap_id"] == "gap:payload_omitted:HISTORY")
    assert "1,046,782" in row["what_is_missing"], row


def test_it_is_idempotent_and_survives_a_missing_field():
    need = {"gap:payload_omitted:HISTORY": "太大"}
    obj = {}
    mr._backfill_machine_known_gaps(obj, {"required_disclosures": need})
    mr._backfill_machine_known_gaps(obj, {"required_disclosures": need})
    assert [g["gap_id"] for g in obj["data_gaps"]] == [
        "gap:payload_omitted:HISTORY"]
    # packet 沒有那一格時不得炸(降級路徑也走這裡)
    mr._backfill_machine_known_gaps(obj, {})
    mr._backfill_machine_known_gaps(None, {"required_disclosures": need})


def test_the_disclosure_the_validator_demands_is_the_one_python_writes():
    """接線:兩邊要讀**同一格**。`payload_budget` 寫進
    `required_disclosures`,驗證器也讀那一格 —— 代抄用的鍵若不同,
    補了還是照樣被駁回。"""
    pk = {"market": {"HISTORY": ["x" * 200_000]}, "news": []}
    trimmed, report = pb.trim(pk, limit=50_000)
    need = trimmed.get("required_disclosures") or {}
    assert any(k.startswith("gap:payload_omitted:") for k in need), need
    obj = {"data_gaps": []}
    mr._backfill_machine_known_gaps(obj, trimmed)
    told = {g["gap_id"] for g in obj["data_gaps"]}
    machine = {k for k in need if k.startswith("gap:payload_omitted:")}
    assert machine <= told, (machine - told)


def test_the_hook_runs_on_the_production_path():
    """沒有呼叫端的函式等於那個 docstring 是假的。"""
    src = io.open(Path(mr.__file__), encoding="utf-8").read()
    i = src.index("_canonicalize_gap_ids(obj, packet)\n            #")
    assert "_backfill_machine_known_gaps(obj, packet)" in src[i:i + 500]


def test_a_blank_machine_gap_row_is_filled_not_skipped():
    """2026-08-26 外審 P2:第一版只看 `gap_id` 在不在,而 schema 對內容
    沒有非空約束 —— 模型填對 ID、兩個內容欄留空,代抄跳過、驗證器放行,
    而渲染端 `_lines` 把整列丟掉:使用者**完全看不到**那個缺口。
    那正是這批要避免的失效,只是換了個形狀。"""
    import analysis_render as ar
    need = {"gap:payload_omitted:HISTORY": "HISTORY 太大,未進入分析輸入"}
    obj = {"data_gaps": [{"gap_id": "gap:payload_omitted:HISTORY",
                          "what_is_missing": "  ",
                          "impact_on_conclusions": ""}]}
    mr._backfill_machine_known_gaps(obj, {"required_disclosures": need})
    row = obj["data_gaps"][0]
    assert row["what_is_missing"] == "HISTORY 太大,未進入分析輸入", row
    assert row["impact_on_conclusions"].strip(), row
    # **走到渲染**:helper 的輸出對了不算,要真的出現在信裡
    lines = ar._lines(obj["data_gaps"],
                      lambda g: f"{g.get('what_is_missing') or ''}")
    assert lines and "HISTORY" in lines[0], lines
    # 模型自己寫了內容就不覆寫(它可能寫得更具體)
    obj2 = {"data_gaps": [{"gap_id": "gap:payload_omitted:HISTORY",
                           "what_is_missing": "模型自己的描述",
                           "impact_on_conclusions": "模型自己的影響"}]}
    mr._backfill_machine_known_gaps(obj2, {"required_disclosures": need})
    assert obj2["data_gaps"][0]["what_is_missing"] == "模型自己的描述"
    # 不得長出重複的同 ID 列
    assert len(obj2["data_gaps"]) == 1, obj2["data_gaps"]


def test_duplicate_machine_gap_rows_are_merged():
    """r1 外審:schema 沒有唯一性約束,而 `setdefault` 只修到第一列 ——
    另一列照樣被渲染,信裡出現兩條同樣的資料缺口。"""
    need = {"gap:payload_omitted:HISTORY": "HISTORY 太大,未進入分析輸入"}
    obj = {"data_gaps": [
        {"gap_id": "gap:payload_omitted:HISTORY", "what_is_missing": "",
         "impact_on_conclusions": ""},
        {"gap_id": "gap:payload_omitted:HISTORY",
         "what_is_missing": "模型後來寫的描述", "impact_on_conclusions": ""},
        {"gap_id": "gap:other:y", "what_is_missing": "a",
         "impact_on_conclusions": "b"}]}
    mr._backfill_machine_known_gaps(obj, {"required_disclosures": need})
    ids = [g["gap_id"] for g in obj["data_gaps"]]
    assert ids.count("gap:payload_omitted:HISTORY") == 1, obj["data_gaps"]
    assert "gap:other:y" in ids, ids
    row = next(g for g in obj["data_gaps"]
               if g["gap_id"] == "gap:payload_omitted:HISTORY")
    # 合併時保留較具體的非空模型內容
    assert row["what_is_missing"] == "模型後來寫的描述", row
    assert row["impact_on_conclusions"].strip(), row
