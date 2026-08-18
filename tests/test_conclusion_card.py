# -*- coding: utf-8 -*-
"""**結論卡不得變成一句空話**(2026-08-18 使用者回報)。

信頂端那張「今日結論」連著好幾天是:

    依系統計分:中性。LLM 摘要與系統立場不一致,已略過其方向性建議;
    價位區間見下方預測表。

沒有任何可行動的內容。兩個原因,兩件事各修各的:

1. **自己產生 markdown、再用正則把立場讀回來。** 2026-08-18 的
   `run_manifest.stance_dual` 記著 `llm=1 / llm_label=null` —— 分數讀到、
   標籤沒有。特化路徑手上本來就有 `stance.label`(schema 的 enum),
   繞一圈解析是自己給自己製造的失效模式:排版一動就讀不到,而症狀出現
   在結論卡、原因在渲染層,沒有人看得出關聯。
2. **「一句話總結裡沒有立場詞」被當成立場衝突。** 2026-08-17 的生產:
   LLM 與 Python 都判「中性」,只因總結寫成「今日以觀望為主」就把整張
   結論卡抹掉。沒寫立場詞不是矛盾,寫出**不同的**立場詞才是。
"""
import io as _io
from pathlib import Path as _Path

import morning_report as mr

#: **路徑錨在這個檔案自己身上**,不靠 CWD —— 從別的目錄跑 pytest 時
#: 相對路徑會讀不到,而「讀不到」在這裡會變成靜默跳過。
_SRC = _Path(__file__).resolve().parent.parent / "morning_report.py"


def _clear():
    mr._RUN_MANIFEST.setdefault("llm", {}).pop("stance_structured", None)


def test_the_structured_stance_survives_the_render_round_trip():
    """立場從 JSON 留下來 —— **不經過 markdown**。

    這是 2026-08-18 那個缺陷的直接反例:渲染層怎麼排版,都不影響
    結論卡拿到的立場。
    """
    _clear()
    obj = {"stance": {"label": "偏空", "score": -4, "rationale": "利率壓過題材。"}}
    mr._RUN_MANIFEST.setdefault("llm", {})["stance_structured"] = {
        "label": str(obj["stance"]["label"]), "score": obj["stance"]["score"],
        "rationale": obj["stance"]["rationale"]}
    got = mr._structured_stance()
    assert got["label"] == "偏空" and got["score"] == -4, got
    _clear()


def test_no_structured_stance_means_no_claim():
    """沒有結構化立場時回空 dict —— 呼叫端會退回解析 markdown。
    **不得回一個看起來有值的空殼**(那會讓「解析失敗」變成「立場是空字串」)。"""
    _clear()
    assert mr._structured_stance() == {}
    mr._RUN_MANIFEST["llm"]["stance_structured"] = {"label": "", "score": None}
    assert mr._structured_stance() == {}
    _clear()


def test_accept_luna_records_the_stance_from_the_json():
    """`_accept_luna` 是特化路徑的唯一出口 —— 立場要在那裡留下來。"""
    import ast
    src = _io.open(_SRC, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_accept_luna")
    body = ast.dump(fn)
    assert "stance_structured" in body, (
        "特化路徑沒有把結構化立場留下來 —— 結論卡又要回去解析自己的 markdown")


# ---------------------------------------------------------------- 判準本身

def _conflict(llm_label: str, sum_word: str, py_label: str) -> bool:
    """把生產那條判準抄成可測的形狀 —— **並由下面那條測試釘住它沒有漂移**。"""
    return bool((llm_label and llm_label != py_label)
                or (sum_word and sum_word != py_label))


def test_a_summary_without_a_stance_word_is_not_a_contradiction():
    """2026-08-17 生產:兩邊都判「中性」,只因總結寫成「今日以觀望為主」
    (不含四個立場詞)就整張結論卡被抹掉。**沒寫不是寫反。**"""
    assert not _conflict("中性", "", "中性")


def test_a_different_stance_word_is_a_contradiction():
    """寫出**不同的**立場詞才是矛盾 —— 那正是 PR-2 要防的
    「同一封信兩個相反立場」。"""
    assert _conflict("偏多", "", "偏空")
    assert _conflict("", "偏多", "偏空")
    assert _conflict("中性", "偏多", "中性")


def test_the_production_rule_matches_this_one():
    """**判準只有一份。** 生產那一行改了而這裡沒改,上面兩條就變成
    在測一個不存在的規則(這個 repo 踩過同一形狀好幾次)。"""
    src = _io.open(_SRC, encoding="utf-8").read()
    assert ('_conflict = ((_llm_label and _llm_label != _py_label)'
            in src), "生產的衝突判準換了寫法,這個檔的測試已經不代表它"
    assert ('or (_sum_word and _sum_word != _py_label))' in src)


def test_the_degraded_card_still_says_something_actionable():
    """真的矛盾時**不再只留一句空話**:系統立場與分析師觀點並列、
    寫明以何者為準。一封信裡出現兩個立場而**沒有說哪個算數**才是問題,
    說清楚了就不是。"""
    src = _io.open(_SRC, encoding="utf-8").read()
    assert "分析師觀點為" in src and "本報以系統計分為準" in src
    # 舊的空話不得留著
    assert "LLM 摘要與系統立場不一致" not in src, (
        "那句話沒有任何可行動的內容,使用者連著幾天收到的就是它")
