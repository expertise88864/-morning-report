# -*- coding: utf-8 -*-
"""**證據圖:覆蓋整個 packet,而且每個 ID 說得出自己是什麼時候的。**

第十八輪 P1-1/P1-2/P1-8。先前 registry 回答得了「這個名字存不存在」,
回答不了三件更重要的事:

  1. 模型想談 00662 估值、2330 開盤預測、模型校準 —— **沒有 ID 可引用**,
     只能不引用(被擋)或拿一則新聞去頂(形式合法、語意錯誤)。
  2. 引用的是**今天的**資料嗎?美股休市那天,QQQ 是上一個交易日的延續值。
  3. 今天哪幾項根本沒查成?先前只要 `data_gaps` 非空就過關 ——
     一筆「缺某公司的資本支出金額」能替三項跑不成的橫向檢查背書。
"""
import analysis_schema as sch
import evidence_packet as ep
import evidence_registry as er
import fixtures_analysis as fx
import tension_refs as tr

_QUOTES = {
    "QQQ": {"change_pct": 1.76, "close": 500.0},
    "TAIFEX_OI": {"foreign_oi_net": -90038},
    "MACRO": {"10Y": {"close": 4.32, "prev_close": 4.20}},
    "SECTOR_HEAT": {"ranked": ["半導體業"],
                    "sectors": {"半導體業": {"median_pct": 3.6,
                                          "leaders": [{"code": "2330",
                                                       "name": "台積電",
                                                       "pct": -2.3}]}}},
}


def _packet(**over) -> dict:
    q = dict(_QUOTES, **over.pop("quotes", {}))
    return ep.build(q, {"fair_value": 55.2, "premium_pct": 3.1},
                    {"model1": {"pred_pct": 0.42}}, fx.news(),
                    [{"code": "2330", "pct": 1.2}], {"brier": 0.21},
                    as_of="2026-08-05T06:00", target_session_date="2026-08-05",
                    sanitize=str, **over)


# ---------------------------------------------------------------- 覆蓋範圍

def test_the_blocks_that_used_to_have_no_ids_now_have_them():
    """**P1-1**:這五塊先前一個 ID 都沒有,而它們正是最需要根據的判斷。"""
    reg = er.registry(_packet())
    assert "valuation:fair_value" in reg, "00662 估值沒有引用對象"
    assert "prediction:model1.pred_pct" in reg, "2330 開盤預測沒有引用對象"
    assert "calibration:brier" in reg, "模型校準沒有引用對象"
    assert "universe:2330.pct" in reg, "台股個股沒有引用對象"
    assert any(k.startswith("quality:") for k in reg), "資料涵蓋度沒有引用對象"


def test_a_list_is_keyed_by_identity_not_by_index():
    """索引會隨當日排序漂移 —— 昨天的引用會指到今天的另一檔。"""
    reg = er.registry(_packet())
    assert "market:SECTOR_HEAT.sectors.半導體業.leaders.2330.pct" in reg
    assert not any(".0." in k for k in reg), "出現了陣列索引"


def test_a_whole_dict_is_not_citable():
    """只收純量葉子。讓「引用一整個 dict」合法,引用檢查就失去作用。"""
    reg = er.registry(_packet())
    assert reg["market:SECTOR_HEAT.sectors.半導體業.median_pct"]["value"] == 3.6
    assert "market:SECTOR_HEAT.sectors.半導體業.leaders" not in reg


def test_the_registry_does_not_swallow_the_whole_packet():
    """**太寬與太窄一樣糟。** 什麼都引得到時,引用檢查等於沒有。"""
    reg = er.registry(_packet())
    assert len(reg) < 400, f"registry 膨脹到 {len(reg)} 個 ID"
    assert not any(k.startswith(("market:DATA_QUALITY", "market:HISTORY",
                                 "market:ALERTS")) for k in reg), \
        "診斷區塊被當成市場證據"


def test_a_label_is_citable_but_prose_is_not():
    """**標籤是證據,散文不是。**

    `MARKET_REGIME.label = "risk-on"` 是一個具體事實,引用得到才對;
    而公報全文那種幾百字的區塊,引用它說明不了任何**具體**的事 ——
    讓它合法只會讓引用檢查變成橡皮圖章。
    突變驗證抓到的:把長度上限拿掉之後全套照樣綠。
    """
    prose = "行政院會通過修正草案,並請主管機關研議配套措施後另行公告。" * 4
    pk = _packet(quotes={"MARKET_REGIME": {"label": "risk-on", "note": prose}})
    reg = er.registry(pk)
    assert reg["market:MARKET_REGIME.label"]["value"] == "risk-on"
    assert reg["market:MARKET_REGIME.label"]["unit"] == "", "字串不該被安上單位"
    assert "market:MARKET_REGIME.note" not in reg, "整段散文變成合法引用對象"
    # 區塊本身仍然引用得到 —— 談「今天有這塊資料」時需要
    assert "market:MARKET_REGIME" in reg


def test_the_old_id_set_is_still_a_subset():
    """**舊呼叫端不得被打壞** —— 這是擴充,不是換一套命名。"""
    pk = _packet()
    assert set(ep.evidence_ids(pk)) == set(er.registry(pk))


# ---------------------------------------------------------------- 新鮮度

def test_a_us_holiday_marks_the_us_side_unusable_everywhere():
    """**兩個真相來源不一致是這裡真正的缺陷。**

    先前 `signal_tensions` 讀原始 quotes 看得到美股休市,而 registry 只看
    packet —— 而 `US_HOLIDAY` 沒被帶進 packet。於是同一天,張力標成
    不可用、`market:QQQ.change_pct` 標成可用,下游信哪一個是隨機的。
    """
    pk = _packet(quotes={"US_HOLIDAY": {"detected": True}})
    reg = er.registry(pk)
    assert reg["market:QQQ.change_pct"]["usable_for_inference"] is False
    assert "休市" in reg["market:QQQ.change_pct"]["why_unusable"]
    # 本地側**不受影響** —— 一竿子打翻全部就等於沒有分辨力
    assert reg["market:TAIFEX_OI.foreign_oi_net"]["usable_for_inference"]


def test_metadata_says_what_it_knows_and_stays_quiet_otherwise():
    """**猜一個單位比留空更糟** —— 下游會拿它去格式化。"""
    reg = er.registry(_packet())
    assert reg["market:QQQ.change_pct"]["unit"] == "%"
    assert reg["market:TAIFEX_OI.foreign_oi_net"]["unit"] == "lots"
    assert reg["market:QQQ.close"]["unit"] == "", "推不出來的單位被猜了一個"
    assert reg["market:QQQ.change_pct"]["as_of"] == "2026-08-05T06:00"
    assert reg["market:QQQ.change_pct"]["source"] == "quotes.QQQ"


def test_a_high_claim_cannot_rest_only_on_stale_evidence():
    """**P1-2 的用途**:有 metadata 才問得出「引用的是今天的資料嗎」。"""
    pk = _packet(quotes={"US_HOLIDAY": {"detected": True}})
    obj = fx.valid_analysis()
    obj["data_gaps"] = [{"gap_id": g, "what_is_missing": "行情欄位",
                         "impact_on_conclusions": "今天沒有答案"}
                        for g in tr.required_gap_ids(pk["signal_tensions"])]
    obj["claim_audit"][0].update(materiality="high", claim_type="fact",
                                 evidence_ids=["market:QQQ.change_pct"])
    assert [p for p in sch.validate(obj, pk) if "全部不同步" in p]
    # 同時引用一個今天的證據就可以 —— **禁止的是「只」靠它**
    obj["claim_audit"][0]["evidence_ids"] = ["market:QQQ.change_pct",
                                             "market:TAIFEX_OI.foreign_oi_net"]
    assert not [p for p in sch.validate(obj, pk) if "全部不同步" in p]


# ---------------------------------------------------------------- 逐項揭露

def test_every_missing_check_must_be_disclosed_by_its_own_id():
    """**P1-8**:先前一筆無關的缺口能替三項跑不成的檢查過關。"""
    pk = ep.build({"QQQ": {"close": 500.0}}, {}, {}, fx.news(), [], {},
                  as_of="2026-08-05T06:00", target_session_date="2026-08-05",
                  sanitize=str)
    need = tr.required_gap_ids(pk["signal_tensions"])
    assert len(need) >= 3, "這份行情本來就該讓多項檢查跑不成"
    obj = fx.valid_analysis()
    obj["data_gaps"] = [{"gap_id": "gap:other",
                         "what_is_missing": "某公司的資本支出金額",
                         "impact_on_conclusions": "量級判斷不出來"}]
    missed = [p for p in sch.validate(obj, pk) if "沒有揭露它" in p]
    assert len(missed) == len(need), f"只抓到 {len(missed)}/{len(need)} 項"
    obj["data_gaps"] += [{"gap_id": g, "what_is_missing": "行情欄位",
                          "impact_on_conclusions": "沒有答案"} for g in need]
    assert not [p for p in sch.validate(obj, pk) if "沒有揭露它" in p]


def test_disclosing_a_gap_that_does_not_exist_is_also_wrong():
    """反向:**回填不存在的缺口**會讓「揭露過」看起來成立。"""
    pk = _packet()
    obj = fx.valid_analysis()
    obj["data_gaps"] = [{"gap_id": g, "what_is_missing": "x",
                         "impact_on_conclusions": "y"}
                        for g in tr.required_gap_ids(pk["signal_tensions"])]
    obj["data_gaps"].append({"gap_id": "gap:made_up",
                             "what_is_missing": "x", "impact_on_conclusions": "y"})
    assert [p for p in sch.validate(obj, pk) if "而今天沒有這一項" in p]
    # `gap:other` 是給模型自己發現的缺口用的,**不算回填**
    obj["data_gaps"][-1]["gap_id"] = "gap:other"
    assert not [p for p in sch.validate(obj, pk) if "而今天沒有這一項" in p]


def test_the_model_is_told_which_gaps_it_must_disclose():
    """**不給清單而要求逐項揭露,等於要它猜驗證器在想什麼。**

    那種規則只會逼出「什麼都寫一點」的自保式輸出 —— 而那正是
    使用者三次反映的「堆疊」的另一種形態。
    """
    pk = _packet()
    assert pk["required_disclosures"] == tr.required_gap_ids(pk["signal_tensions"])
    import prompt_profiles as pp
    dev = pp.build_luna_bundle(pk)["developer_instructions"]
    assert "required_disclosures" in dev, "prompt 沒有告訴模型這個欄位"
    assert "gap:other" in dev, "沒說自己發現的缺口要怎麼填"


def test_the_namespaces_are_documented_in_the_prompt():
    """新增的命名空間要在 prompt 裡說得出來 —— 不說,模型就不會用。"""
    import prompt_profiles as pp
    dev = pp.build_luna_bundle(_packet())["developer_instructions"]
    for ns in ("valuation:", "prediction:", "calibration:", "universe:",
               "portfolio:", "quality:", "derived:"):
        assert ns in dev, f"{ns} 沒有寫進 prompt"
