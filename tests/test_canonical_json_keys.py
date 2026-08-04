# -*- coding: utf-8 -*-
"""**序列化器不得弄壞它要保護的那條路**(2026-08-04 實機故障)。

## 實機證據

Luna 特化路徑**連兩天**失敗。第一天完全查不出原因;第二天(補上診斷欄位後)
manifest 記到::

    llm:luna_path_failed:TypeError
    {"error": "TypeError: '<' not supported between instances of 'int' and 'str'",
     "stage": "analysis"}
    analysis_origin: legacy_fallback_after_luna_failure

`stage=analysis` 表示證據組裝成功了,而 `primary_bundle` 不存在 ——
`_luna_analysis` 的第二行就是寫那個欄位,所以掛在**第一行**
`build_luna_bundle()`,也就是它裡面的 `evidence_sha(packet)`。

## 為什麼 `build()` 沒事而 bundle 會炸

`build()` 只對 **news** 算 `core_evidence_sha`;整個 packet 的 `evidence_sha`
是 `_bundle()` 才算的。上游某個欄位塞了非字串鍵,只有後者會踩到。

## 這個缺陷的形狀

`canonical_json` 的 docstring 自己寫著:

> 寧可得到一個穩定的字串,也不要讓整個 packet 拋例外 ——
> 那會讓當天完全沒有 sha,而沒有 sha 的那天就是不可比的一天。

而 `default=str` 保護的是**值**,`sort_keys=True` 照樣在**鍵**上拋。
**宣稱與實作差一層,而差的那一層正好是宣稱要解決的問題。**
"""
import json

import evidence_packet as ep


def test_mixed_key_types_no_longer_raise():
    """**這一行就是連兩天讓實驗零產出的那一行。**"""
    packet = {"market": {2026: {"close": 1.0}, "QQQ": {"close": 2.0}}}
    # 修之前:TypeError: '<' not supported between instances of 'int' and 'str'
    out = ep.canonical_json(packet)
    assert json.loads(out) == {"market": {"2026": {"close": 1.0},
                                          "QQQ": {"close": 2.0}}}
    assert ep.evidence_sha(packet)          # sha 算得出來才有那一天的樣本


def test_the_old_behaviour_is_byte_identical_for_string_keys():
    """**修法不得改動任何既有指紋。**

    全部是字串鍵時 `str(k) == k`,所以型別感知的排序與 `sorted(keys)`
    結果相同 —— 這條測試是那個宣稱的證據,不是推理。
    """
    packet = {"z": 1, "a": {"n": [3, {"b": 2, "A": 4}]}, "m": "字串",
              "10": None, "2": True}
    old = json.dumps(packet, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), default=str)
    assert ep.canonical_json(packet) == old


def test_nested_and_in_list_dicts_are_ordered_too():
    """順序要**整棵樹**都定義,否則 sha 會隨 dict 插入順序抖動。"""
    a = {"x": [{"b": 1, "a": 2}], "y": {"d": 1, "c": 2}}
    b = {"y": {"c": 2, "d": 1}, "x": [{"a": 2, "b": 1}]}
    assert ep.canonical_json(a) == ep.canonical_json(b)
    assert ep.evidence_sha(a) == ep.evidence_sha(b)


def test_a_deterministic_order_across_types():
    """混型別時也要**每次都一樣**,否則同一份證據會有兩個 sha。"""
    a = {2026: "a", "QQQ": "b", 7: "c"}
    b = {"QQQ": "b", 7: "c", 2026: "a"}
    assert ep.canonical_json(a) == ep.canonical_json(b)


def test_a_stringified_collision_does_not_silently_drop_data():
    """`{1: 'a', '1': 'b'}` —— JSON 的鍵只有字串,兩者會撞在一起。

    這是**既有**行為(`json.dumps` 本來就會把 int 鍵轉成字串),修正沒有
    讓它變好也沒有讓它變壞;但要有一條測試把它釘住,而且要看得出來
    **有幾個鍵進去、剩幾個出來** —— 哪天真的踩到,不會以為資料好好的。
    """
    packet = {1: "a", "1": "b"}
    out = json.loads(ep.canonical_json(packet))
    assert len(out) == 1, "撞鍵的行為變了 —— 這條測試要跟著更新並說明"
    assert ep.nonstring_key_paths(packet) == ["(root):1(int)"], (
        "撞鍵的那一筆要能被診斷指出來")


def test_the_diagnostic_names_the_offending_field():
    """**知道「是鍵的型別」還不夠,要知道是哪個上游欄位。**

    否則下次換一個欄位又要從零查一次 —— 而這次查了兩天。
    """
    packet = {"market": {"TAIFEX_OI": {2026: 1}}, "news": [{"x": {7: "a"}}]}
    paths = ep.nonstring_key_paths(packet)
    assert "market.TAIFEX_OI:2026(int)" in paths
    assert any("news[0].x:7(int)" == p for p in paths), paths


def test_a_clean_packet_reports_nothing():
    """反向:全字串鍵時診斷是空的 —— 不得每天都在喊狼來了。"""
    assert ep.nonstring_key_paths({"a": {"b": [1, "c", {"d": None}]}}) == []


def test_build_then_sha_survives_a_dirty_upstream_field():
    """**生產那條路**:上游丟進來一個 int 鍵,整條仍然走得完。

    判準走 `build()` → `build_luna_bundle()`,也就是實機真正炸掉的那一段;
    直接測 `canonical_json` 會漏掉「bundle 有沒有接上」。
    """
    import prompt_profiles as pp
    packet = ep.build({"TAIFEX_OI": {2026: {"oi": 1}}}, {}, {},
                      [{"title": "標題", "summary": "內文", "source": "來源"}],
                      [], {}, as_of="2026-08-04T06:00:00+08:00",
                      target_session_date="2026-08-04", sanitize=str)
    bundle = pp.build_luna_bundle(packet)     # 這一行實機拋 TypeError
    assert bundle["evidence_sha"] and bundle["prompt_sha"]
    assert ep.nonstring_key_paths(packet), "診斷應該指得出那個 int 鍵"
