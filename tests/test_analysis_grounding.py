# -*- coding: utf-8 -*-
"""**每條「有話說就要說得出根據」的規則各自被測到**(第十二輪 P1-3)。

## 為什麼需要這個檔

路徑測試裡的反例(`_UNSUPPORTED`)同時違反了四條規則,所以拿掉任何**單一**
規則,其他三條照樣把它擋下來 —— 突變驗證全綠。也就是說四條規則裡只有
「至少還剩一條」被測到,個別規則其實可以被悄悄拿掉而沒有人發現。

這是本 repo 反覆出現的形狀:**重複的守衛測不出來,而測不出來的守衛在下一次
重構時會被拿掉。** 所以這裡每個案例只違反一條規則,其餘欄位都合格。

## 判準

`analysis_grounding` 擋的不是「格式不對」—— 那是 `analysis_schema` 的事。
它擋的是**語氣肯定但背後什麼都沒有**的那種輸出,而那種輸出會原樣進到信裡。
"""
import analysis_grounding as gr

_IDS = {"n1", "n2"}


def _sound() -> dict:
    """一份**每條規則都滿足**的報告。每個案例只從這裡破壞一個地方。"""
    return {
        "executive_summary": "今日偏多。",
        "key_drivers": [{"statement": "費半走強", "claim_type": "fact",
                         "materiality": "high", "evidence_ids": ["n1"]}],
        "market_regime": {"label": "偏多", "evidence_ids": ["n1"]},
        "taiwan_market": {"summary": "量能回升。", "evidence_ids": ["n2"]},
        "global_market": {"summary": "美股收紅。", "evidence_ids": ["n1"]},
        # schema v2:橫向綜合也是帶證據、會進信的段落。
        "cross_market_synthesis": {"dominant_driver": "美股外部定價",
                                   "evidence_ids": ["n1"]},
        # 第十六輪 P2-4:「已反映/未反映」是高推論性判斷,也要帶證據。
        "priced_in": {"already_reflected": ["費半漲幅"],
                      "not_yet_reflected": [], "evidence_ids": ["n1"]},
        "top_news_analysis": [{"source_item_id": "n1", "why_it_matters": "傳導"}],
        "claim_audit": [{"claim_id": "c1", "statement": "費半走強",
                         "claim_type": "fact", "materiality": "high",
                         "evidence_ids": ["n1"]}],
    }


def test_the_baseline_is_actually_clean():
    """先確立前提:基準本身要零問題,否則下面每一條都在測雜訊。"""
    assert gr.problems(_sound()) == []


def test_a_key_driver_without_evidence_is_caught():
    """`key_drivers` 會被排進「昨夜三大重點」—— 那是信裡語氣最肯定的一段。"""
    obj = _sound()
    obj["key_drivers"][0]["evidence_ids"] = []
    hits = gr.problems(obj)
    assert len(hits) == 1 and "key_drivers[0]" in hits[0], hits


def _sections_with_evidence_in_schema() -> set:
    """schema 裡**帶 `evidence_ids` 的物件段落**(清單型另外處理)。

    應檢查範圍從 schema 推導,**不從被測的清單推導**:
    第一版寫 `for sec in gr.EVIDENCE_BEARING`,於是把 `global_market`
    從那個常數裡刪掉之後,測試也跟著少跑一圈 —— 突變全綠。
    **守衛不能自己決定要掃多大。**
    """
    import analysis_schema as sch
    props = (sch.ANALYSIS_OUTPUT_SCHEMA.get("properties") or {})
    return {k for k, v in props.items()
            if isinstance(v, dict) and v.get("type") == "object"
            and "evidence_ids" in (v.get("properties") or {})}


def test_each_evidence_bearing_section_is_checked_separately():
    """帶證據的段落**每一個**都要被檢查,而且是各自被檢查。

    只寫一條「有段落沒證據」的測試,會讓其中幾個段落可以被悄悄移出清單。
    """
    expected = _sections_with_evidence_in_schema()
    assert expected, "從 schema 推不出任何帶證據的段落 —— 掃描器壞了"
    assert set(gr.EVIDENCE_BEARING) == expected, (
        f"檢查清單與 schema 分岔:漏檢 {expected - set(gr.EVIDENCE_BEARING)}、"
        f"多檢 {set(gr.EVIDENCE_BEARING) - expected}")
    for sec in expected:
        obj = _sound()
        obj[sec]["evidence_ids"] = []
        hits = gr.problems(obj)
        assert len(hits) == 1 and sec in hits[0], f"{sec} 沒有被單獨檢查:{hits}"


def test_a_news_item_without_a_source_is_caught():
    """指不出是哪一則新聞的「新聞分析」,讀的人無從查證。"""
    obj = _sound()
    obj["top_news_analysis"][0]["source_item_id"] = "  "
    hits = gr.problems(obj)
    assert len(hits) == 1 and "top_news_analysis[0]" in hits[0], hits


def test_an_empty_claim_audit_is_caught():
    """**最安靜的一種假通過。**

    `claim_audit` 空著時,所有逐項檢查都會因為「沒有東西可迭代」而通過。
    稽核軌跡本身必須被檢查,否則它一空,整套稽核就自動全過。
    """
    obj = _sound()
    obj["claim_audit"] = []
    hits = gr.problems(obj)
    assert len(hits) == 1 and "claim_audit" in hits[0], hits


def test_an_empty_report_is_not_penalised():
    """**沒有內容就沒有稽核義務。**

    「這天沒有國際盤可談」與「有話要說卻說不出根據」是兩回事,
    只有後者要擋 —— 把前者也擋掉會讓 Luna 在資料稀薄的日子無謂落回。
    """
    assert gr.problems({}) == []
    assert gr.problems({"data_gaps": ["來源不足"]}) == []


#: strict schema **所有欄位必填**,所以資料不足那天的合法空段落不是 `{}`,
#: 而是「欄位都在、值都空」。用 `{}` 測等於在測一個生產不會出現的形狀。
_EMPTY_SHAPED = {
    "market_regime": {"label": "", "evidence_ids": []},
    "taiwan_market": {"summary": "", "taiex_view": "", "tsmc_view": "",
                      "evidence_ids": []},
    "global_market": {"summary": "", "us_to_tw_linkage": "",
                      "evidence_ids": []},
}


def test_an_empty_section_object_is_not_penalised():
    """空的段落物件不算「有話說」。"""
    obj = _sound()
    obj["taiwan_market"] = {}
    assert gr.problems(obj) == []


def test_a_schema_shaped_empty_section_is_not_penalised():
    """**strict 輸出的空段落是 truthy 的**(r1 Codex,P2)。

    所有欄位必填,所以資料不足那天的合法空段落長成「欄位都在、值都空」。
    用 dict 的 truthiness 判斷會誤報「有內容卻沒有證據」,Luna 白白修補
    一次再落回 legacy —— **而那一段根本沒有任何文字會進信**。

    誤判的代價不是漏擋,是讓 Luna 在資料稀薄的日子看起來比較不可靠,
    而那正是這個實驗要量的東西。
    """
    for sec, empty in _EMPTY_SHAPED.items():
        obj = _sound()
        obj[sec] = dict(empty)
        assert gr.problems(obj) == [], f"{sec} 的空段落被誤判:{gr.problems(obj)}"


def test_evidence_ids_alone_are_not_content():
    """`has_content` 的宣稱是「`evidence_ids` 不算」—— 那句話要有測試。

    只有一串證據 ID、沒有任何文字的段落,**沒有東西會被寄出去**。
    把它算成「有內容」會讓一份全空的報告被要求交出稽核軌跡,
    而那份報告根本沒有話說。

    這條補在突變驗證之後:把「跳過 evidence_ids」拿掉時,grounding 的
    主路徑行為不變(所以其他測試全綠),但這個宣稱就成了空話。
    """
    assert gr.has_content({"summary": "", "evidence_ids": ["n1"]}) is False
    assert gr.has_content({"summary": "有話說", "evidence_ids": []}) is True
    # 連帶:全空但帶著證據 ID 的報告不該被要求稽核軌跡
    obj = {k: dict(v, evidence_ids=["n1"]) for k, v in _EMPTY_SHAPED.items()}
    obj.update({"executive_summary": "", "key_drivers": [],
                "top_news_analysis": [], "claim_audit": []})
    assert gr.problems(obj) == []


def test_a_section_with_text_but_no_evidence_is_still_caught():
    """反向:**別為了消誤報而把真的該擋的放過。**

    只要有一個欄位真的有字,那段就會進信,就要說得出根據。
    """
    obj = _sound()
    obj["taiwan_market"] = dict(_EMPTY_SHAPED["taiwan_market"],
                                summary="量能回升。")
    hits = gr.problems(obj)
    assert len(hits) == 1 and "taiwan_market" in hits[0], hits


def test_an_all_empty_report_needs_no_claim_audit():
    """整份都是 schema 形狀的空段落 → 沒有東西會被寄出 → 不必稽核。"""
    obj = {k: dict(v) for k, v in _EMPTY_SHAPED.items()}
    obj.update({"executive_summary": "", "key_drivers": [],
                "top_news_analysis": [], "claim_audit": []})
    assert gr.problems(obj) == []


def test_fabricated_ids_are_caught_in_every_managed_section():
    """**編造的 ID 比沒有 ID 更危險** —— 三個新段落也要驗存不存在。

    r1(Codex,P1):新守衛只要求 `evidence_ids` 非空,沒驗它存不存在 ——
    等於在鼓勵模型「隨便填一個」,而那正是本 repo 既有合約點名的那種風險。
    判準走 `schema.validate()`(生產真正呼叫的入口),不是只呼叫 grounding。
    """
    import analysis_schema as sch
    for sec in gr.EVIDENCE_BEARING:
        obj = _sound()
        obj[sec] = dict(obj[sec], evidence_ids=["n_fabricated"])
        hits = sch.validate(obj, _IDS)
        assert any("n_fabricated" in h and sec in h for h in hits),             f"{sec} 引用了不存在的證據卻通過:{hits}"


def test_the_rendered_list_covers_what_the_renderer_emits():
    """**判準要跟著 renderer 走。**

    這條規則的意義是「會被寄出去的都要有根據」;`RENDERED` 漏掉一個
    renderer 真的會排出來的段落,那個段落就能無根據地進信。
    """
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "analysis_render.py"
    emitted = {n.args[0].value
               for n in ast.walk(ast.parse(src.read_text(encoding="utf-8")))
               if isinstance(n, ast.Call)
               and getattr(n.func, "attr", "") == "get"
               and n.args and isinstance(n.args[0], ast.Constant)
               and isinstance(n.args[0].value, str)}
    # renderer 也會讀不進信的欄位(例如 stance 的子欄位),所以只驗
    # 「有帶 evidence_ids 的段落都在清單裡」這個方向。
    missing = [s for s in gr.EVIDENCE_BEARING if s in emitted
               and s not in gr.RENDERED and s != "market_regime"]
    assert not missing, f"這些段落會被寄出卻不在 RENDERED 裡:{missing}"
