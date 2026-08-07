# -*- coding: utf-8 -*-
"""**「有東西可以寄」不等於「新路徑成功」**(第十四輪 P0-1)。

## 實機證據(2026-08-03 06:43)

`run_manifest.json`:`degraded_steps: ["llm:luna_path_failed"]`、
`llm_experiment: null` —— 特化路徑沒跑成。
同一天的 `llm_shadow_ledger.json`::

    primary_model: gpt-5.6-luna   primary_effort: xhigh   primary_ok: true

兩份都沒說謊:特化確實失敗,而 Luna 這個模型確實產出了文字(跑的是
DeepSeek 的舊 prompt)。但十天後翻帳本的人會讀成「Luna xhigh 成功」——
而那正是這個實驗要回答的問題本身。

`primary_ok = bool(primary_text)` 在正常的日子裡剛好等於「特化成功」,
**只有在失敗的日子裡才分開** —— 也就是唯一需要它們分開的時候。
"""
import analysis_origin as ao
import analysis_validate as av
import fixtures_analysis as fx
import morning_report as mr


# ---------------------------------------------------------------- 判定本身

def test_only_the_specialized_path_counts_as_a_luna_success():
    assert ao.counts_as_primary_success(ao.LUNA_SPECIALIZED) is True
    for other in (ao.LEGACY_PRIMARY, ao.LEGACY_AFTER_LUNA_FAILURE,
                  ao.EMERGENCY_FALLBACK, ao.UNKNOWN):
        assert ao.counts_as_primary_success(other) is False, other


def test_no_text_is_not_a_success_either():
    """反向:走對了路但沒有輸出,仍然不是成功。"""
    assert ao.counts_as_primary_success(ao.LUNA_SPECIALIZED,
                                        has_text=False) is False


def test_an_unrecognised_value_never_becomes_a_success():
    """**打錯字不得變成 Luna 成功。**

    這個實驗的結論直接建立在這個計數上;寧可少算一天樣本,
    也不要讓一個拼錯的字串被計成成功。
    """
    for junk in ("luna", "LUNA_SPECIALIZED", "luna_specialised", "", None, 7,
                 " luna_specialized "):
        assert ao.normalize(junk) in (ao.UNKNOWN, ao.LUNA_SPECIALIZED)
    assert ao.counts_as_primary_success("luna_specialised") is False
    assert ao.counts_as_primary_success("LUNA_SPECIALIZED") is False
    # 前後空白是排版,不是另一個值
    assert ao.counts_as_primary_success(" luna_specialized ") is True


def test_the_emergency_fallback_marks_itself():
    """**四條路走到備援,標記在函式裡而不是四個 return 上。**

    逐個 return 補一行等於留四個會被漏掉的地方,而漏掉的症狀是
    「一封沒有模型判斷的信」被記成某條路徑的成功。
    """
    mr._set_analysis_origin(ao.LUNA_SPECIALIZED)
    mr._fallback_analysis_text([], RuntimeError("x"))
    assert mr._analysis_origin() == ao.EMERGENCY_FALLBACK
# -------------------------------------------- schema v2:新增的跨欄位不變式



def _v2_obj():
    return fx.valid_analysis()


def test_a_fact_step_without_evidence_is_rejected():
    """**沒有證據的因果步驟不得自稱 fact**(schema v2)。

    突變驗證第一輪抓到這條規則沒有測試 —— 把它從驗證器拿掉,全套照樣綠。
    它擋的正是「看起來有根據」:一條 fact→fact→fact 的鏈,讀起來像事實,
    而中間某一步其實是猜的。
    """
    import analysis_schema as sch
    obj = _v2_obj()
    obj["top_news_analysis"][0]["mechanism_steps"][1]["step_type"] = "fact"
    hits = sch.validate(obj, fx.ids())
    assert any("自稱 fact 卻沒有證據" in h for h in hits), hits
    # 反向:標成 inference 就合法
    obj["top_news_analysis"][0]["mechanism_steps"][1]["step_type"] = "inference"
    assert sch.validate(obj, fx.ids()) == []


def test_unknown_magnitude_must_say_what_is_missing():
    """`unknown` 是誠實不是逃生口 —— 選它就要說缺哪些資料。"""
    import analysis_schema as sch
    obj = _v2_obj()
    obj["top_news_analysis"][1]["why_this_magnitude"] = ""
    hits = sch.validate(obj, fx.ids())
    assert any("unknown,卻沒有說缺哪些資料" in h for h in hits), hits


def test_a_relation_must_point_at_a_real_item():
    """關係要指向今天真的存在的另一則,不能指向自己或不存在的東西。"""
    import analysis_schema as sch
    obj = _v2_obj()
    rel = obj["top_news_analysis"][0]["relates_to"][0]
    rel["other_source_item_id"] = "n_ghost"
    hits = sch.validate(obj, {"n1", "n2", "n_ghost"})
    assert any("沒有分析那一則" in h for h in hits), hits
    rel["other_source_item_id"] = "n1"          # 指向自己
    hits = sch.validate(obj, fx.ids())
    assert any("指向自己" in h for h in hits), hits


# ---------------------------------------- 第十五輪:深度加深(不擋信)

def test_depth_advisories_and_the_deepen_pass_are_wired():
    """**加深要真的接在生產迴圈上**,而且用的是剩餘的修補額度。

    判準掃 `_luna_analysis` 的原始碼:
      (a) 成功分支要呼叫 `depth_advisories`;
      (b) 加深走 `deepen_input`(裡面帶著「不得硬湊」);
      (c) 加深失敗要用留著的第一版(`_kept`),**不落回 legacy** ——
          淺而正確的分析落回只會換來一封更淺的信。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "morning_report.py"
           ).read_text(encoding="utf-8")
    body = src[src.index("def _luna_analysis"):src.index("def _luna_analysis")
               + 8000]
    assert "_av.depth_advisories(obj, packet)" in body, "成功分支沒有查深度"
    assert "_av.deepen_input(" in body, "加深沒有走統一的 deepen_input"
    assert "_kept" in body and "deepen_failed" in body, (
        "加深失敗沒有回退到留著的第一版")


def test_deepen_input_forbids_fabrication():
    """加深指令必須明說「不得硬湊」—— 否則加深會誘發編造。"""
    txt = av.deepen_input("PAYLOAD", ["a", "b"])
    assert "不得硬湊" in txt and "DEEPEN" in txt
    assert txt.startswith("PAYLOAD"), "user payload 不見了 —— 模型會沒有證據可用"


def test_fixture_depth_is_the_reference_answer():
    """fixture 是「夠深」的參考答案:零 advisory。

    它一旦退化(有人把 mechanism_steps 刪掉),先紅的是這裡。
    """
    import fixtures_analysis as fx
    assert av.depth_advisories(fx.valid_analysis()) == []


def test_each_shallow_shape_gets_its_own_advisory():
    import fixtures_analysis as fx
    base = fx.valid_analysis()
    # 高重要性只有一步
    o = fx.valid_analysis()
    o["top_news_analysis"][0]["mechanism_steps"] = \
        o["top_news_analysis"][0]["mechanism_steps"][:1]
    assert any("因果鏈卻只有 1 步" in a for a in av.depth_advisories(o))
    # 有量級沒理由
    o = fx.valid_analysis()
    o["top_news_analysis"][0]["why_this_magnitude"] = ""
    assert any("沒有說為什麼是這個量級" in a for a in av.depth_advisories(o))
    # 橫向綜合沒有衝突欄
    o = fx.valid_analysis()
    o["cross_market_synthesis"]["conflicting_signals"] = []
    assert any("互相抵銷" in a for a in av.depth_advisories(o))
    # 主導因子空白
    o = fx.valid_analysis()
    o["cross_market_synthesis"]["dominant_driver"] = ""
    assert any("主導因子" in a for a in av.depth_advisories(o))
    _ = base


def test_advisories_never_reject():
    """**淺不是不合格。** depth_advisories 的每一種形狀都要通得過 validate
    —— 兩者混在一起,淺的那天就會落回 legacy,換來一封更淺的信。"""
    import fixtures_analysis as fx
    o = fx.valid_analysis()
    o["top_news_analysis"][0]["mechanism_steps"] = []
    o["cross_market_synthesis"]["conflicting_signals"] = []
    o["cross_market_synthesis"]["dominant_driver"] = ""
    assert av.depth_advisories(o), "淺的形狀要被點名"
    import analysis_schema as sch
    assert sch.validate(o, fx.ids()) == [], "淺被當成了不合格 —— 會落回 legacy"
