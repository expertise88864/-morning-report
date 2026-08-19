# -*- coding: utf-8 -*-
"""**逐標的影響、同向訊號、閉合的 claim 圖**(第十八輪最後三項)。

三者的共同形狀是**「看起來有做」**:

  * 每則新聞只有一個 direction/magnitude/horizon —— 於是「對台積電中期
    中度正面、對指數即日可忽略、對成熟製程可能是負面」被壓成一個「偏多」。
    那正是使用者說的泛論。
  * 橫向只嚴格處理矛盾。同向訊號放在自由文字裡,於是**有沒有把同一個
    底層驅動重複計權**驗不了 —— 而重複計權正是立場分虛高的來源。
  * `claim_audit` 非空且合法,而信裡真正寫出來的立場、已反映/未反映、
    投資組合影響**沒有任何東西回指它**。可以「今日偏多,主因半導體
    需求強勁」而稽核裡只有一條「QQQ 昨日上漲」。
"""
import analysis_render as ar
import analysis_schema as sch
import evidence_packet as ep
import fixtures_analysis as fx
import tension_refs as tr

_IDS = fx.ids()


def _packet() -> dict:
    """一份**同時有矛盾與同向**的 packet(生產形狀)。"""
    return ep.build({"QQQ": {"change_pct": 1.76},
                     "TAIFEX_OI": {"foreign_oi_net": 90038},
                     "TAIEX_PRED": {"pred_pct": 0.5},
                     "BREADTH": {"advance_ratio": 72.0}},
                    {}, {}, fx.news(), [], {},
                    as_of="2026-08-05T06:00", target_session_date="2026-08-05",
                    sanitize=str)


# ---------------------------------------------------------------- 逐標的影響

def test_a_high_materiality_event_must_name_the_assets_it_moves():
    """**「這則新聞對股市偏多」是泛論,不是分析。**"""
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["affected_assets"] = []
    hits = [p for p in sch.validate(obj, _IDS) if "沒有拆出任何受影響標的" in p]
    assert hits, "高重要性事件不拆標的也通過了"


def test_an_asset_without_a_first_order_effect_is_rejected():
    """只給方向與幅度等於沒有拆 —— 那只是把同一個標籤複製到三個代號上。"""
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["affected_assets"][0]["first_order_effect"] = ""
    assert [p for p in sch.validate(obj, _IDS) if "沒有寫直接影響" in p]


def test_opposite_directions_for_two_assets_are_legal():
    """**同一件事對不同標的可以相反** —— 這正是拆開的目的,不得被擋。"""
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["affected_assets"][1].update(
        direction="bearish", first_order_effect="成熟製程的排擠效應")
    assert sch.validate(obj, _IDS) == []


def test_the_letter_shows_each_asset_with_its_downstream_effects():
    """**逐標的的一階/二階影響要進信** —— 那是使用者要的「後續影響、脈絡」。

    2026-08-19 改散文之後它在逐則段落的句尾(`費半:權值股開盤定價…`),
    不再是縮排清單。判準不變:影響本身一定要在、方向詞不得出現在逐則裡。
    """
    text = ar.render(fx.valid_analysis())
    i = text.index(ar.SECTION_NEWS)
    # 下一個 h2(`### ` 是第八段自己的子段,不是邊界)
    j = text.index(chr(10) + "## ", i)
    section = text[i:j]
    assert "偏多" not in section and "偏空" not in section, section
    assert "權值股開盤定價直接跟隨費半" in section, section


# ---------------------------------------------------------------- 同向訊號

def test_an_alignment_must_be_read_not_just_listed():
    """**P1-7**:橫向先前只嚴格處理矛盾,同向訊號連 ID 對應都沒有。"""
    pk = _packet()
    need = sorted(tr.required_alignment_ids(pk["signal_tensions"]))
    assert need, "這份行情本來就該產生同向訊號"
    obj = _resolved(pk)
    obj["cross_market_synthesis"]["alignment_readings"] = []
    assert [p for p in sch.validate(obj, pk) if "沒有解讀" in p]


def test_an_alignment_reading_must_say_whether_it_double_counts():
    """**重複計權是立場分虛高的來源** —— 兩個「同向訊號」可能是同一件事。"""
    pk = _packet()
    obj = _resolved(pk)
    obj["cross_market_synthesis"]["alignment_readings"][0]["double_count_risk"] = ""
    hits = [p for p in sch.validate(obj, pk) if "double_count_risk" in p]
    assert hits, "沒有回答會不會重複計算也通過了"


def test_a_fabricated_alignment_id_is_rejected():
    """反向:回填不存在的 ID 讓「都解讀過了」看起來成立。"""
    pk = _packet()
    obj = _resolved(pk)
    obj["cross_market_synthesis"]["alignment_readings"].append(
        {"alignment_id": "tension:t_bogus", "interpretation": "x",
         "marginal_information": "y", "double_count_risk": "z",
         "evidence_ids": []})
    assert [p for p in sch.validate(obj, pk) if "今天沒有這筆同向訊號" in p]


def test_the_alignment_reading_reaches_the_letter_is_retired():
    """同向解讀那一段隨「今日市場關注與預測」被使用者整段刪掉
    (2026-08-19)。欄位仍在 schema 裡被要求與驗證;這條釘住它**不再**
    出現在信裡 —— 段落偷偷回來與偷偷消失一樣要被看見。"""
    text = ar.render(fx.valid_analysis())
    assert "同向訊號" not in text
    assert "增量資訊" not in text

def _resolved(pk: dict) -> dict:
    """把今天的矛盾與同向**都**處理過的一份分析。"""
    obj = fx.valid_analysis()
    obj["data_gaps"] = [{"gap_id": g, "what_is_missing": "行情欄位",
                         "impact_on_conclusions": "沒有答案"}
                        for g in tr.required_gap_ids(pk["signal_tensions"])]
    obj["cross_market_synthesis"]["tension_resolutions"] = [
        {"tension_id": t, "resolution": "外部定價先反映在權值開盤",
         "dominant_side": "left", "why": "開盤前只有美股已定價",
         "decision_rule": "現貨量能與期貨空單是否回補", "evidence_ids": [t]}
        for t in sorted(tr.required_tension_ids(pk["signal_tensions"]))]
    obj["cross_market_synthesis"]["alignment_readings"] = [
        {"alignment_id": a, "interpretation": "外部定價與本地籌碼同方向",
         "marginal_information": "籌碼確認了美股的方向不只是隔夜情緒",
         "double_count_risk": "兩者都受同一批權值股帶動,不宜各算一分",
         "evidence_ids": [a]}
        for a in sorted(tr.required_alignment_ids(pk["signal_tensions"]))]
    return obj


# ---------------------------------------------------------------- claim 圖

def test_a_section_must_say_which_claims_it_rests_on():
    """**說不出這一段靠哪幾條主張,稽核就只是裝飾。**"""
    obj = fx.valid_analysis()
    obj["stance"]["claim_ids"] = []
    hits = [p for p in sch.validate(obj, _IDS) if "stance 沒有回指任何 claim" in p]
    assert hits, hits


def test_a_section_cannot_point_at_a_claim_that_does_not_exist():
    obj = fx.valid_analysis()
    obj["priced_in"]["claim_ids"] = ["c99"]
    assert [p for p in sch.validate(obj, _IDS) if "指向不存在的主張" in p]


def test_a_duplicate_claim_id_is_rejected():
    """回指會指向兩條 —— 那時「有根據」是哪一條就說不清了。"""
    obj = fx.valid_analysis()
    obj["claim_audit"] = obj["claim_audit"] * 2
    assert [p for p in sch.validate(obj, _IDS) if "重複的 claim_id" in p]


def test_an_orphan_high_materiality_claim_is_rejected():
    """**寫進稽核卻沒有任何一段用到的高重要性主張,不是根據,是配菜。**

    這是「一份信寫『今日偏多,主因半導體需求強勁』而稽核裡只有一條
    『QQQ 昨日上漲』」的可機械化版本。
    """
    obj = fx.valid_analysis()
    obj["claim_audit"].append(dict(obj["claim_audit"][0], claim_id="c9",
                                   statement="半導體需求強勁"))
    hits = [p for p in sch.validate(obj, _IDS) if "沒有被任何段落引用" in p]
    assert hits and "c9" in hits[0], hits
    # 被引用之後就合格 —— **規則要的是連上,不是少寫**
    # (c2 是 fixture 裡撐住立場時間尺度的那一條,一併留著)
    obj["stance"]["claim_ids"] = ["c1", "c2", "c9"]
    assert not [p for p in sch.validate(obj, _IDS) if "沒有被任何段落引用" in p]


def test_the_summary_line_cannot_float_free_of_the_audit():
    """**最可能被單獨閱讀的那一段先前完全脫離稽核。**

    「今日偏多,主因半導體需求強勁」而稽核裡只有「QQQ 昨日上漲」——
    形式上完全合法,因為總結根本沒有回指的欄位。
    """
    obj = fx.valid_analysis()
    obj["executive_summary_claim_ids"] = []
    assert [p for p in sch.validate(obj, _IDS)
            if "executive_summary 沒有回指" in p]
    obj["executive_summary_claim_ids"] = ["c99"]
    assert [p for p in sch.validate(obj, _IDS)
            if "executive_summary 的 claim_ids 指向不存在" in p]


def test_a_reference_must_connect_to_the_right_horizon():
    """**回指只證明「有連上」,不證明「連對了」。**

    立場寫 1-5 天,而它唯一靠的主張只談今日盤前 —— 那個引用是形式的,
    而讀者看到的是一個有根據的一週判斷。
    """
    obj = fx.valid_analysis()
    obj["stance"]["claim_ids"] = ["c1"]          # c1 是 intraday
    obj["stance"]["time_horizon"] = "1-5d"
    # 第二十二輪 P1-5:判準改宣告式矩陣,訊息跟著改 ——「全都比它更短」
    # 對「更長兩階」是錯的,而矩陣會擋它。
    hits = [p for p in sch.validate(obj, _IDS) if "撐得起" in p]
    assert hits and "1-5d" in hits[0], hits
    # 把尺度改成一致就合格 —— **規則要的是連對,不是少寫**
    obj["stance"]["time_horizon"] = "intraday"
    assert not [p for p in sch.validate(obj, _IDS) if "撐得起" in p]
    # 相鄰一階仍然相容(1-5d 的主張撐得起當日的段落)
    obj["stance"]["claim_ids"] = ["c2"]          # c2 是 1-5d
    assert not [p for p in sch.validate(obj, _IDS) if "撐得起" in p]


def test_a_claim_must_say_who_it_is_about():
    """`asset_scope` 留空的主張,**回指到任何一段都成立** ——
    那讓「連對了嗎」這個問題失去意義。"""
    obj = fx.valid_analysis()
    obj["claim_audit"][0]["asset_scope"] = []
    assert [p for p in sch.validate(obj, _IDS) if "沒有 asset_scope" in p]
    # 泛稱也不算範圍;整體市場級別有自己的寫法
    obj["claim_audit"][0]["asset_scope"] = ["市場"]
    assert [p for p in sch.validate(obj, _IDS) if "是泛稱" in p]
    obj["claim_audit"][0]["asset_scope"] = ["market-wide"]
    assert not [p for p in sch.validate(obj, _IDS) if "asset_scope" in p]


def test_a_claim_without_an_id_cannot_be_pointed_at():
    obj = fx.valid_analysis()
    obj["claim_audit"][0].pop("claim_id")
    assert [p for p in sch.validate(obj, _IDS) if "沒有 claim_id" in p]


def test_the_prompt_explains_all_three():
    """規則不寫進 prompt,模型不會憑空做到。"""
    import prompt_profiles as pp
    dev = pp.build_luna_bundle(_packet())["developer_instructions"]
    assert "affected_assets" in dev and "本報看不出次級影響" in dev
    assert "alignment_readings" in dev and "重複計權" in dev
    assert "claim_ids" in dev and "配菜" in dev
