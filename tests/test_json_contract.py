# -*- coding: utf-8 -*-
"""**本地 strict schema 檢查自己要可靠**(第十三輪 P2-3 / r1 #2)。

這個驗證器的用途是替 fixture 與金絲雀背書:「這個物件 API 會接受」。
它若漏檢一種關鍵字,那句背書就是假的 —— 而假的背書比沒有背書更糟,
因為下游會停止懷疑。

r1(Codex,#2)抓到的正是這個:第一版沒實作 `minimum`/`maximum`,
**而且沒把它們列進不支援清單** —— 我在模組開頭寫下的規則被模組自己違反。
`stance.score=999`、`confidence=2` 因此會被判成合法。

修法不是補上那兩個關鍵字就算:黑名單是**手寫**的,漏了什麼不會有人發現。
改成從 schema 反推(白名單以外一律點名),新關鍵字進 schema 的當下就會
被指出來。**守衛不能自己決定要掃多大。**
"""
import analysis_schema as sch
import json_contract as jc


def test_the_production_schema_uses_only_implemented_keywords():
    """**這條是整個檔的地基。**

    生產 schema 若用到沒實作的關鍵字,所有「fixture 合法」的斷言都退化成
    「fixture 沒有違反我剛好有檢查的那幾條」。
    """
    missing = jc.unsupported_keywords(sch.ANALYSIS_OUTPUT_SCHEMA)
    assert missing == [], f"schema 用到沒實作的關鍵字:{missing}"


def test_an_unimplemented_keyword_raises_instead_of_passing():
    """沒實作就拋,不要靜默通過 —— 「沒檢查」不得被誤讀成「檢查過了」。"""
    import pytest
    with pytest.raises(NotImplementedError):
        jc.violations({"a": 1}, {"type": "object",
                                 "properties": {"a": {"type": "integer",
                                                      "anyOf": []}}})


def test_a_node_made_only_of_an_unsupported_keyword_is_caught():
    """**只有 `anyOf` 的節點也要被抓**(r1 Codex,pass 2)。

    我把黑名單改成白名單時加了一個「這看起來像不像 schema」的閘門
    (要有 `type`/`properties`/`enum`/`items` 才檢查),於是
    `{"anyOf": [...]}` 這種**最該擋**的形狀整個被跳過 ——
    **舊的黑名單反而擋得住它**。而我的測試把 `anyOf` 跟 `type` 寫在一起,
    正好滿足那個閘門,把這個情形遮住了。

    組合關鍵字構成的節點本來就常常沒有 `type`;而傳進來的每個 dict
    都已經是 schema 節點,沒有需要猜的餘地。
    """
    import pytest
    assert jc.unsupported_keywords({"anyOf": []}) == ["(root):anyOf"]
    with pytest.raises(NotImplementedError):
        jc.violations({"a": 1}, {"anyOf": []})
    # 巢狀的也要抓得到,而且要指得出位置
    nested = {"type": "object", "properties": {"a": {"oneOf": []}}}
    assert jc.unsupported_keywords(nested) == ["a:oneOf"]


def test_an_empty_schema_is_not_flagged():
    """反向:空 schema(什麼都接受)不是「用了沒實作的關鍵字」。"""
    assert jc.unsupported_keywords({}) == []
    assert jc.violations({"a": 1}, {}) == []


def test_numeric_bounds_are_checked():
    """r1(Codex,#2):範圍沒檢查,守衛就會替 API 必然拒絕的物件背書。"""
    s = {"type": "object", "additionalProperties": False,
         "required": ["x"], "properties": {
             "x": {"type": "number", "minimum": 0.0, "maximum": 1.0}}}
    assert jc.violations({"x": 0.5}, s) == []
    assert jc.violations({"x": 0.0}, s) == [], "邊界值本身合法(minimum 是閉區間)"
    assert jc.violations({"x": 1.0}, s) == []
    assert len(jc.violations({"x": -0.1}, s)) == 1
    assert len(jc.violations({"x": 1.1}, s)) == 1


def test_a_bool_is_not_a_number():
    """Python 的 `True` 是 `1`,而 JSON Schema 的布林不是數值。

    第一版只測「有 `type` 的欄位」—— 那條路徑由型別檢查擋下,範圍檢查裡
    那個布林保護怎麼改都不會紅(**重複的守衛測不出來**)。
    真正只有它作用的是「有範圍、沒有 `type`」:那時 `True` 不該被當成 1
    拿去比大小。
    """
    typed = {"type": "object", "properties": {
        "x": {"type": "number", "minimum": 0.0, "maximum": 1.0}}}
    assert jc.violations({"x": True}, typed), "布林值被當成合法的數值了"
    # 只有範圍、沒有 type:布林不是數值,所以不該產生「小於下限」
    assert jc.violations(True, {"minimum": 2}) == [],         "布林被當成 1 拿去比範圍了"
    assert jc.violations(1, {"minimum": 2}) != [], "真正的數值仍要被檢查"


def test_required_and_additional_properties():
    s = {"type": "object", "additionalProperties": False,
         "required": ["a"], "properties": {"a": {"type": "string"}}}
    assert jc.violations({"a": "x"}, s) == []
    assert len(jc.violations({}, s)) == 1
    assert len(jc.violations({"a": "x", "b": 1}, s)) == 1


def test_enum_and_nested_items():
    s = {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["k"], "properties": {"k": {"enum": ["a", "b"]}}}}
    assert jc.violations([{"k": "a"}, {"k": "b"}], s) == []
    bad = jc.violations([{"k": "a"}, {"k": "z"}], s)
    assert len(bad) == 1 and "[1]" in bad[0], f"位置沒指出來:{bad}"


def test_every_violation_says_where():
    """**訊息要指得出位置。** 只說「不合法」的驗證器,修的人得自己找。"""
    import sys
    sys.path.insert(0, "tests")
    import fixtures_analysis as fx
    obj = fx.valid_analysis()
    obj["stance"]["score"] = 999
    obj["key_drivers"][0]["materiality"] = "巨大"
    hits = jc.violations(obj, sch.ANALYSIS_OUTPUT_SCHEMA)
    assert len(hits) == 2
    assert any("stance.score" in h for h in hits)
    assert any("key_drivers[0].materiality" in h for h in hits)
