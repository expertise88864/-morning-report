# -*- coding: utf-8 -*-
"""**指稱明確、路徑少一層的證據 ID**(2026-08-10 生產缺陷)。

那天的信少了事件卡、淨效果與橫向綜合:主分析被自己的驗證擋下 10 條,
全部是同一種 —— 模型寫 `market:SOX.change_pct`,而合法的是
`market:MACRO.SOX.change_pct`。整份特化分析因此作廢、退回既有路徑。

**這不是放寬驗證。** 不存在的 ID 仍然不存在;這裡處理的是「同一個東西
的另一種寫法」,而且只在**唯一命中**時解。所以這裡的判準有兩半:
救得回真正的近似(不然沒解決問題)、以及**歧義與捏造一律不救**
(不然就是把沒根據的主張洗成合法 —— 那正是 `_prune_phantom_audit_ids`
的 docstring 記著的教訓)。
"""
from __future__ import annotations

import evidence_registry as er

_IDS = {
    "market:MACRO.SOX.change_pct", "market:MACRO.SOX.close",
    "market:MACRO.VIX.close", "market:MACRO.WTI.close",
    "market:QQQ.change_pct", "market:TAIFEX_OI.foreign_oi_net",
    "n1", "n2", "fact:n1.0",
}


# ---------------------------------------------------------------- 解得出來

def test_the_real_production_misses_resolve():
    """2026-08-10 實信的三個 —— 少的都是中間那一層。"""
    for cited, want in (
            ("market:SOX.change_pct", "market:MACRO.SOX.change_pct"),
            ("market:WTI.close", "market:MACRO.WTI.close"),
            ("market:VIX.close", "market:MACRO.VIX.close")):
        assert er.resolve_near_miss(cited, _IDS) == want, cited


def test_a_legal_id_is_returned_unchanged():
    """已經對的不要動。"""
    assert er.resolve_near_miss("market:QQQ.change_pct",
                                _IDS) == "market:QQQ.change_pct"


# ---------------------------------------------------------------- 不能解

def test_ambiguity_is_never_resolved():
    """命中兩個以上**不解** —— 那不是筆誤是歧義,兩個都可能不是它要的。"""
    ambiguous = _IDS | {"market:OTHER.SOX.change_pct"}
    assert er.resolve_near_miss("market:SOX.change_pct", ambiguous) == ""


def test_missing_two_levels_is_not_a_typo():
    """`market:close` 指的是哪一個 close,少的那兩層才知道 ——
    那不是筆誤,是根本沒說完。"""
    assert er.resolve_near_miss("market:close", _IDS) == ""
    assert er.resolve_near_miss("market:change_pct", _IDS) == ""


def test_only_one_missing_level_resolves_even_when_unique():
    """**跳兩層不是漏寫外殼,是自創簡寫**(這條反例只靠深度規則分勝負:
    命中唯一、命名空間相同、路徑兩段 —— 差別只在少了兩層)。
    樹越深,明天另一個區塊長出同名葉節點的機會越大 —— 那時今天的
    「唯一命中」會安靜地指到別人身上。"""
    deep = {"market:SECTOR_HEAT.sectors.半導體業.turnover_pct"}
    assert er.resolve_near_miss("market:半導體業.turnover_pct", deep) == ""
    # 少一層的同一棵樹解得出來(證明不是整條路都不解)
    assert er.resolve_near_miss("market:sectors.半導體業.turnover_pct",
                                deep) ==         "market:SECTOR_HEAT.sectors.半導體業.turnover_pct"


def test_an_invented_id_stays_invented():
    """**不存在的仍然不存在** —— 這一層不放寬任何東西。"""
    assert er.resolve_near_miss("market:BOGUS.close", _IDS) == ""
    assert er.resolve_near_miss("market:MACRO.FAKE.close", _IDS) == ""
    assert er.resolve_near_miss("n9", _IDS) == ""
    assert er.resolve_near_miss("", _IDS) == ""
    assert er.resolve_near_miss("沒有命名空間", _IDS) == ""


def test_the_namespace_is_a_wall():
    """`market:` 的近似只在 `market:` 裡找 —— 跨命名空間的「相似」
    不是同一個東西(命名空間就是「這是哪一種證據」)。"""
    ids = {"market:MACRO.SOX.change_pct"}
    assert er.resolve_near_miss("fact:MACRO.SOX.change_pct", ids) == ""
    assert er.resolve_near_miss("tension:SOX.change_pct", ids) == ""


# ---------------------------------------------------------------- 走訪與範圍

def _obj():
    return {
        "claim_audit": [{"claim_id": "c1",
                         "evidence_ids": ["market:SOX.change_pct", "n1"],
                         "counterevidence_ids": ["market:VIX.close"]}],
        "contradictions": [{"supporting_ids": ["market:WTI.close"],
                            "opposing_ids": ["market:BOGUS.x"]}],
        "dismissed_events": [{"cluster_id": "cluster:n1",
                              "supporting_evidence_ids": ["n2"]}],
        "executive_summary_claim_ids": ["c1"],
        # `relates_to` 是**物件**陣列(schema),每個物件自己帶 evidence_ids
        # —— 第一版 fixture 寫成字串清單,於是這一格的近似 ID 根本沒被
        # 走訪到(外審 r1:測試把形狀寫錯,量到的是別的東西)。
        "top_news_analysis": [{
            "source_item_id": "n1",
            "relates_to": [{"other_source_item_id": "n2",
                            "relationship": "same_driver",
                            "evidence_ids": ["market:SOX.close"],
                            "explanation": "同一個驅動"}]}],
    }


def test_every_evidence_field_is_covered():
    """證據欄位由 schema 推導 —— 抄一份清單的話,新欄位會靜靜漏掉,
    而漏掉的症狀是「修了一半」。"""
    import analysis_schema as sch
    obj = _obj()
    changed = er.canonicalize_evidence_ids(obj, _IDS,
                                           sch.evidence_id_fields())
    assert obj["claim_audit"][0]["evidence_ids"][0] == \
        "market:MACRO.SOX.change_pct"
    assert obj["claim_audit"][0]["counterevidence_ids"] == \
        ["market:MACRO.VIX.close"]
    assert obj["contradictions"][0]["supporting_ids"] == \
        ["market:MACRO.WTI.close"]
    assert obj["top_news_analysis"][0]["relates_to"][0]["evidence_ids"] ==         ["market:MACRO.SOX.close"]
    assert len(changed) == 4, changed
    # 合法的、捏造的都原封不動
    assert obj["claim_audit"][0]["evidence_ids"][1] == "n1"
    assert obj["contradictions"][0]["opposing_ids"] == ["market:BOGUS.x"]


def test_non_evidence_fields_are_left_alone():
    """`claim_ids` / `cluster_id` / `relates_to` 指的是主張、事件群與
    裝飾層 —— 拿證據的規則去改它們會把命名空間混成一個。"""
    import analysis_schema as sch
    obj = _obj()
    er.canonicalize_evidence_ids(obj, _IDS, sch.evidence_id_fields())
    assert obj["executive_summary_claim_ids"] == ["c1"]
    assert obj["dismissed_events"][0]["cluster_id"] == "cluster:n1"
    # `relates_to` 物件裡的 `evidence_ids` **是**證據欄位,會被正規化;
    # 而它的 `other_source_item_id` / `relationship` 不是。
    rel = obj["top_news_analysis"][0]["relates_to"][0]
    assert rel["other_source_item_id"] == "n2"
    assert rel["relationship"] == "same_driver"
    fields = sch.evidence_id_fields()
    assert "evidence_ids" in fields and "supporting_evidence_ids" in fields
    assert "claim_ids" not in fields and "relates_to" not in fields


def test_junk_shapes_do_not_raise():
    """**晨報不可斷**:模型回的東西形狀怪異時退化成不改,不是例外。"""
    import analysis_schema as sch
    f = sch.evidence_id_fields()
    assert er.canonicalize_evidence_ids(None, _IDS, f) == []
    assert er.canonicalize_evidence_ids({"evidence_ids": "不是清單"},
                                        _IDS, f) == []
    assert er.canonicalize_evidence_ids({"evidence_ids": [None, 3, {}]},
                                        _IDS, f) == []
    assert er.canonicalize_evidence_ids(_obj(), set(), f) == []


# ---------------------------------------------------------------- 接上生產

def test_the_luna_path_canonicalizes_before_validating():
    """**沒接上等於不存在**:走生產的呼叫形狀 —— 正規化之後,
    那條原本被擋下的引用要通得過 `validate`,而且改寫要進 manifest。"""
    import sys
    sys.path.insert(0, "tests")
    import analysis_validate as av
    import evidence_packet as ep
    import fixtures_analysis as fx
    import morning_report as mr
    pk = ep.build({"MACRO": {"SOX": {"close": 12356.79, "change_pct": 2.56}}},
                  {}, {}, [{"source_item_id": "n1", "title": "台積電法說",
                            "entities": ["台積電"], "source": "經濟日報"}],
                  [], {}, as_of="2026-08-10 06:00",
                  target_session_date="2026-08-10", sanitize=lambda s, *a: s)
    obj = fx.valid_analysis()
    obj["claim_audit"][0]["evidence_ids"] = ["market:SOX.change_pct"]
    before = [p for p in av.validate(obj, pk)
              if "market:SOX.change_pct" in p]
    assert before, "這條本來就該被擋(前提沒成立,後面量不到東西)"
    mr._RUN_MANIFEST.setdefault("llm", {}).pop("evidence_id_rewrites", None)
    mr._canonicalize_evidence_ids(obj, pk)
    after = [p for p in av.validate(obj, pk) if "market:SOX.change_pct" in p]
    assert not after, after
    # **改寫要看得見**:不記的話,「模型引用得對不對」會被這一層美化
    slot = mr._RUN_MANIFEST["llm"]
    assert slot["evidence_ids_canonicalized"] == 1
    assert slot["evidence_id_rewrites"] == [
        "market:SOX.change_pct→market:MACRO.SOX.change_pct"]


def test_the_wiring_is_in_the_luna_loop():
    """接線在**驗證之前**;接在後面等於沒接。"""
    import io
    from pathlib import Path
    src = io.open(Path(__file__).resolve().parents[1] / "morning_report.py",
                  encoding="utf-8").read()
    i = src.index("_canonicalize_evidence_ids(obj, packet)\n")
    j = src.index("problems = (_sch.validate(obj, packet)")
    assert i < j, "正規化跑在驗證後面 = 沒接上"


# ===== 外審第一輪 =====

def test_a_resolvable_near_miss_survives_the_pruner():
    """**正規化要在修剪之前**(外審 r1):`relates_to[].evidence_ids` 也是
    證據欄位 —— 修剪先跑的話,近似 ID 被當成幽靈剪掉,整條合法的跨新聞
    關聯消失,還被記成「幽靈修剪」。真的假引用仍然要被剪掉。"""
    import sys
    sys.path.insert(0, "tests")
    import evidence_packet as ep
    import fixtures_analysis as fx
    import morning_report as mr
    pk = ep.build({"MACRO": {"SOX": {"close": 12356.79, "change_pct": 2.56}}},
                  {}, {}, [{"source_item_id": "n1", "title": "台積電法說",
                            "entities": ["台積電"], "source": "經濟日報"},
                           {"source_item_id": "n2", "title": "費半大漲",
                            "entities": ["費半"], "source": "鉅亨"}],
                  [], {}, as_of="2026-08-10 06:00",
                  target_session_date="2026-08-10", sanitize=lambda s, *a: s)

    def _obj_with(rel_ids):
        obj = fx.valid_analysis()
        obj["top_news_analysis"][0]["source_item_id"] = "n1"
        obj["top_news_analysis"][0]["relates_to"] = [{
            "other_source_item_id": "n2", "relationship": "same_driver",
            "evidence_ids": list(rel_ids), "explanation": "同一個驅動"}]
        return obj

    # 生產順序:正規化 → 修剪
    obj = _obj_with(["market:SOX.change_pct"])
    mr._canonicalize_evidence_ids(obj, pk)
    obj = mr._prune_phantom_audit_ids(obj, pk)
    rel = obj["top_news_analysis"][0]["relates_to"]
    assert rel and rel[0]["evidence_ids"] == ["market:MACRO.SOX.change_pct"], rel

    # 真的捏造的仍然被剪掉(修正不得把修剪關掉)
    obj2 = _obj_with(["market:BOGUS.close"])
    mr._canonicalize_evidence_ids(obj2, pk)
    obj2 = mr._prune_phantom_audit_ids(obj2, pk)
    assert obj2["top_news_analysis"][0]["relates_to"] == []


def test_the_wiring_order_is_canonicalize_then_prune():
    """順序寫在原始碼裡 —— 反過來就是上面那個缺陷。"""
    import io as _io
    from pathlib import Path
    src = _io.open(Path(__file__).resolve().parents[1] / "morning_report.py",
                   encoding="utf-8").read()
    i = src.index("_canonicalize_evidence_ids(obj, packet)" + chr(10))
    j = src.index("obj = _prune_phantom_audit_ids(obj, packet)")
    assert i < j, "修剪跑在正規化前面 = 近似 ID 會被當成幽靈剪掉"

