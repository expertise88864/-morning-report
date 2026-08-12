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
    # calibration 用**生產形狀**(`build_historical_calibration` 的回傳)。
    # 先前餵 `{"brier": 0.21}` —— 生產從來不會產生的形狀 —— 於是
    # 「`calibration:brier` 在 registry 裡」自第十八輪起一路綠著,
    # 而生產傳的是 markdown 字串、那個命名空間**始終一個 ID 都沒有**
    # (2026-08-08 才在信裡看見)。測試要用生產的呼叫形狀。
    return ep.build(q, {"fair_value": 55.2, "premium_pct": 3.1},
                    {"model1": {"pred_pct": 0.42}}, fx.news(),
                    [{"code": "2330", "pct": 1.2}],
                    {"n_days": 7, "mean_abs_delta_pct": 0.83,
                     "by_date": {"08/07": {"delta_pct": -0.33}}, "note": ""},
                    as_of="2026-08-05T06:00", target_session_date="2026-08-05",
                    sanitize=str, **over)


# ---------------------------------------------------------------- 覆蓋範圍

def test_the_blocks_that_used_to_have_no_ids_now_have_them():
    """**P1-1**:這五塊先前一個 ID 都沒有,而它們正是最需要根據的判斷。"""
    reg = er.registry(_packet())
    assert "valuation:fair_value" in reg, "00662 估值沒有引用對象"
    assert "prediction:model1.pred_pct" in reg, "2330 開盤預測沒有引用對象"
    assert "calibration:mean_abs_delta_pct" in reg, "模型校準沒有引用對象"
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


def test_last_trading_session_as_plain_string_does_not_crash():
    """**生產的 `LAST_TRADING_SESSION` 是純字串,不是 dict。**

    main 用 `max(trading_sessions)` 塞進 quotes 的是 "2026-08-06" 這種字串;
    registry 原本寫 `(... or {}).get("date")`,非空字串會 AttributeError ——
    2026-08-07 flash E2E 實測整條特化路徑因此天天落回 legacy,而 fixture
    沒這個 key,測試全綠。兩種形狀都要收,而且台股區塊要拿得到那個日期。
    """
    pk = _packet(quotes={"LAST_TRADING_SESSION": "2026-08-04"})
    reg = er.registry(pk)          # 不得拋
    assert reg, "registry 不得因字串形狀而空手而回"
    oi = reg.get("market:TAIFEX_OI.foreign_oi_net") or {}
    assert oi.get("observed_session") == "2026-08-04", (
        "台股區塊要標上實際交易日(字串形狀也要讀得到)")
    # dict 形狀(若未來 producer 改型)同樣要通
    pk2 = _packet(quotes={"LAST_TRADING_SESSION": {"date": "2026-08-04"}})
    reg2 = er.registry(pk2)
    oi2 = reg2.get("market:TAIFEX_OI.foreign_oi_net") or {}
    assert oi2.get("observed_session") == "2026-08-04"


# ------------------------------------------------- 2026-08-12 生產(CI #500)

def test_alerts_is_market_evidence_not_diagnostics():
    """**payload 給模型看的市場觀測,要引用得到。**

    `ALERTS`(昨日過熱/恐慌訊號)跟著 packet 序列化進 Luna payload,
    而它被誤放在 `_NON_EVIDENCE` —— claim 引用 `market:ALERTS` 被判
    不存在、整份特化分析作廢。這是 `pred_open`/`fact:`/`valuation:`/
    `derived:` 之後**第五次**「prompt 給模型看它引用不到的東西」。
    """
    pk = ep.build({"QQQ": {"close": 500.0},
                   "ALERTS": [{"level": "warn", "title": "VIX 跳升",
                               "detail": "單日 +18%"}]},
                  {}, {}, [], [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    assert "market:ALERTS" in ep.evidence_ids(pk)
    # phantom 檢查的另一半要同步 —— 兩邊判準不同 = 同一個事實兩個名字。
    assert "market:ALERTS" in ep.market_refs(pk["market"])


def test_alerts_go_stale_with_the_us_market(monkeypatch):
    """**休市日的警訊是延續值**(外審 r1,P1)。

    過熱/恐慌訊號主要由 VIX/SOX 算出 —— `ALERTS` 若不在 `_US_BLOCKS`,
    高重要性 claim 只引用 `market:ALERTS` 就繞過「證據今天全部不同步」
    檢查,而它的內容全是上一個交易日的美股資料。
    """
    pk = ep.build({"QQQ": {"close": 500.0},
                   "US_HOLIDAY": {"detected": True, "actual_date": "2026-07-03"},
                   "ALERTS": [{"level": "red", "title": "VIX 跳升",
                               "detail": "單日 +18%"}]},
                  {}, {}, fx.news(), [], {}, as_of="x",
                  target_session_date="y", sanitize=str)
    meta = ep.evidence_meta(pk)
    assert meta["market:ALERTS"]["usable_for_inference"] is False
    # 生產的後果:高重要性 claim **只**靠它要被擋,搭配新鮮證據則放行。
    obj = fx.valid_analysis()
    obj["claim_audit"] = [dict(obj["claim_audit"][0], materiality="high",
                               evidence_ids=["market:ALERTS"])]
    assert [p for p in sch.validate(obj, pk) if "全部不同步" in p]
    obj["claim_audit"][0]["evidence_ids"] = ["market:ALERTS", "n1"]
    assert not [p for p in sch.validate(obj, pk) if "全部不同步" in p]


def test_the_deliberately_uncitable_blocks_stay_uncitable():
    """收窄的證明:ALERTS 放行**沒有**帶著整份清單一起放行。

    留在 `_NON_EVIDENCE` 裡的各有理由 —— ANALYSIS_RECAP 是循環引用、
    DATA_QUALITY 是管線診斷 —— 整類放行會把那兩條已釘住的規則靜默拆掉。
    """
    pk = ep.build({"ALERTS": [{"level": "warn", "title": "t", "detail": "d"}],
                   "DATA_QUALITY": {"missing_fields": 3},
                   "HEALTH_WARNINGS": {"n": 1}},
                  {}, {}, [], [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    ids = ep.evidence_ids(pk)
    bad = [i for i in ids if "DATA_QUALITY" in i or "HEALTH_WARNINGS" in i]
    assert not bad, bad


def test_a_self_found_gap_may_carry_a_label():
    """`gap:other:cpi_pending` 視同 `gap:other`(2026-08-12 生產:標籤被
    判成回填不存在的缺口)。`gap:otherX` 沒有冒號 —— 那是另一個名字,
    照樣擋;守的是回填**宣告過的**缺口 ID 那種假揭露。"""
    pk = _packet()
    base = [{"gap_id": g, "what_is_missing": "x", "impact_on_conclusions": "y"}
            for g in tr.required_gap_ids(pk["signal_tensions"])]
    for extra, blocked in (("gap:other:cpi_pending", False),
                           ("gap:other:news_truncation", False),
                           ("gap:otherX", True), ("gap:made_up", True)):
        obj = fx.valid_analysis()
        obj["data_gaps"] = base + [{"gap_id": extra, "what_is_missing": "x",
                                    "impact_on_conclusions": "y"}]
        got = bool([p for p in sch.validate(obj, pk)
                    if "而今天沒有這一項" in p])
        assert got == blocked, (extra, got)


# ------------------------------------------------- 2026-08-12 CI #502

def _trimmed_packet():
    """走**生產的裁切路徑**產生 `gap:payload_omitted:*`(不是手捏)。"""
    import payload_budget as pb
    pk = ep.build({"QQQ": {"close": 500.0},
                   "HISTORY": {"rows": ["x" * 400] * 40}},
                  {}, {}, fx.news(), [], {}, as_of="x",
                  target_session_date="y", sanitize=str)
    pk["market"]["HISTORY"] = {"rows": ["x" * 400] * 40}
    trimmed, report = pb.trim(pk, limit=len(pb._json(pk)) - 1
                              if hasattr(pb, "_json") else 8_000)
    assert any("HISTORY" in t["block"] for t in report["trimmed"]), report
    return trimmed


def test_a_disclosed_payload_omission_is_not_a_fabricated_gap():
    """**need 集合要讀模型看到的那一格**(CI #502)。

    `payload_budget` 把 `gap:payload_omitted:HISTORY` 寫進
    `required_disclosures` 給模型;模型照做揭露,而驗證器先前重新從
    signal_tensions 推導 —— 兩個真相來源,模型聽了其中一個、
    被另一個判成「回填不存在的缺口」,整份特化分析作廢。
    """
    pk = _trimmed_packet()
    assert "gap:payload_omitted:HISTORY" in pk["required_disclosures"]
    obj = fx.valid_analysis()
    obj["data_gaps"] = [{"gap_id": g, "what_is_missing": "x",
                         "impact_on_conclusions": "y"}
                        for g in pk["required_disclosures"]]
    assert not [p for p in sch.validate(obj, pk)
                if "而今天沒有這一項" in p]


def test_an_omitted_block_must_actually_be_disclosed():
    """反向也要接上線:「被裁掉的區塊必須揭露」先前只寫在 packet 裡,
    **沒有任何檢查執行它** —— 沒有呼叫端的宣稱是假的。"""
    pk = _trimmed_packet()
    obj = fx.valid_analysis()
    obj["data_gaps"] = [{"gap_id": g, "what_is_missing": "x",
                         "impact_on_conclusions": "y"}
                        for g in pk["required_disclosures"]
                        if not g.startswith("gap:payload_omitted:")]
    missing = [p for p in sch.validate(obj, pk)
               if "沒有揭露它" in p and "payload_omitted" in p]
    assert missing, "裁掉的區塊沒揭露卻通過了"


def test_a_fabricated_gap_is_still_fabricated():
    """收窄的證明:讀 `required_disclosures` 沒有把「回填宣告過的缺口」
    那道守衛一起放掉。"""
    pk = _trimmed_packet()
    obj = fx.valid_analysis()
    obj["data_gaps"] = ([{"gap_id": g, "what_is_missing": "x",
                          "impact_on_conclusions": "y"}
                         for g in pk["required_disclosures"]]
                        + [{"gap_id": "gap:payload_omitted:捏造的區塊",
                            "what_is_missing": "x",
                            "impact_on_conclusions": "y"}])
    assert [p for p in sch.validate(obj, pk) if "而今天沒有這一項" in p]
