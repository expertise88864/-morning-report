# -*- coding: utf-8 -*-
"""**橫向張力要由 Python 算好**(第十五輪 P2-1)。

2026-08-04 的信同時載著:QQQ +1.76%、外資台指期淨空 90,038 口、
上漲家數 59.7%、半導體中位數 +3.6% 而台積電 -2.3% —— 四組矛盾
全部散在信裡,沒有一句話把它們對起來。模型要在 97K token 裡自己
找出這些,它就會退化成逐條摘要。

fixture 用的就是**那一天的真實數字** —— 這四組矛盾每一組都要被抓到。
"""
import signal_tensions as st

#: 2026-08-04 實際數字(信件與 manifest 抄的)。
_REAL_QUOTES = {
    "QQQ": {"close": 700.07, "change_pct": 1.76},
    "TAIFEX_OI": {"foreign_oi_net": -90038},
    "TAIEX_PRED": {"pred_pct": 0.47, "pred_open": 43590},
    "BREADTH": {"advance_ratio": 59.7, "breadth_state": "narrow",
                "advance": 653, "decline": 360},
    "SECTOR_HEAT": {
        "ranked": ["半導體業", "電子零組件業"],
        "sectors": {
            "半導體業": {"median_pct": 3.6, "value_share_pct": 39.2,
                       "leaders": [{"code": "2330", "name": "台積電", "pct": -2.3},
                                   {"code": "2303", "name": "聯電", "pct": -2.5}]},
            "電子零組件業": {"median_pct": 3.6,
                        "leaders": [{"code": "2308", "name": "台達電", "pct": -3.7}]},
        }},
    "MACRO": {"10Y": {"close": 4.69, "prev_close": 4.745}},
}


def _topics(out, kind=None):
    return [i["topic"] for i in out["items"] if kind in (None, i["kind"])]


# ---------------------------------------------------------------- 四組真實矛盾

def test_us_risk_on_vs_taifex_net_short_is_detected():
    """**QQQ 大漲而外資台指期大額淨空** —— 當天信裡五句平行敘述的核心矛盾。"""
    out = st.detect(_REAL_QUOTES)
    hits = [i for i in out["items"]
            if i["topic"] == "外部定價 vs 本地籌碼" and i["kind"] == "tension"]
    assert len(hits) == 1, out["items"]
    assert hits[0]["left"]["value"] == 1.76 and hits[0]["left"]["unit"] == "%"
    assert hits[0]["right"]["value"] == -90038
    assert hits[0]["relationship"] == "opposite_sign"
    assert hits[0]["tension_id"] == "t_us_vs_taifex"


def test_predicted_open_up_vs_narrow_breadth_is_detected():
    """59.7% 對 60% 門檻 —— 當天的實際邊界案例,差 0.3 個百分點。"""
    out = st.detect(_REAL_QUOTES)
    hits = [i for i in out["items"] if i["topic"] == "開盤預測 vs 市場廣度"]
    assert len(hits) == 1
    assert hits[0]["right"]["value"] == 59.7
    assert "60%" in hits[0]["right"]["label"], "門檻要寫在觀測旁,讀者才知道憑什麼"


def test_sector_median_vs_falling_leaders_is_detected():
    """**半導體中位 +3.6% 而台積電 -2.3%** —— 外審點名、信裡沒人指出的結構分歧。"""
    out = st.detect(_REAL_QUOTES)
    hits = [i for i in out["items"] if i["topic"] == "產業內部分歧"]
    # **每個產業一筆**(P1-5B:先前每個 leader 各發一筆,半導體會出兩筆
    # 幾乎相同的張力,而 prompt 要求逐筆處理 → 在信裡重新製造資料堆疊)。
    # 這份 fixture 有兩個產業都分歧,所以是兩筆、不是一筆。
    assert len(hits) == 2, [h["tension_id"] for h in hits]
    assert len({h["tension_id"] for h in hits}) == 2, "同一產業出現重複"
    semi = [h for h in hits if "半導體" in h["tension_id"]][0]
    assert semi["left"]["value"] == 3.6
    # 挑**差距最大**的那一檔:聯電 3.6−(−2.5)=6.1 > 台積電 5.9。
    # 判準是確定性的「差距最大」,不是「挑最有名的」—— 後者沒有規則可言。
    assert "2303" in semi["right"]["label"], semi["right"]["label"]
    assert semi["relationship"] == "median_above_leader"


def test_falling_yield_with_rising_tech_is_an_alignment():
    """10Y 從 4.745 → 4.69(-5.5bps,未達 -8bps 門檻)→ 不報;
    改成 -10bps 就是 alignment。**門檻要真的有作用**,不是擺著好看。"""
    out = st.detect(_REAL_QUOTES)
    assert not [i for i in out["items"] if i["topic"] == "利率 vs 科技股"]
    q = dict(_REAL_QUOTES, MACRO={"10Y": {"close": 4.64, "prev_close": 4.745}})
    out2 = st.detect(q)
    hits = [i for i in out2["items"] if i["topic"] == "利率 vs 科技股"]
    assert hits and hits[0]["kind"] == "alignment"


# ---------------------------------------------------------------- 誠實性

def test_missing_inputs_are_declared_not_silently_skipped():
    """**守衛不得靜默 no-op**:空清單要分得出「沒有張力」與「沒有資料」。"""
    out = st.detect({})
    assert out["items"] == []
    assert out["checks_run"] == []
    assert set(out["unavailable"]) == {"us_vs_taifex", "prediction_vs_breadth",
                                       "sector_internal_divergence",
                                       "rates_vs_tech"}
    # 部分缺:只缺的那幾項進 unavailable
    out2 = st.detect({"QQQ": {"change_pct": 1.76},
                      "TAIFEX_OI": {"foreign_oi_net": -90038}})
    assert "us_vs_taifex" in out2["checks_run"]
    assert "prediction_vs_breadth" in out2["unavailable"]


def test_it_states_facts_never_conclusions():
    """**只陳述事實,不下結論** —— 結論是模型與立場計分的工作。"""
    out = st.detect(_REAL_QUOTES)
    # 第十六輪 P1-3:**禁用詞清單抓不到經驗法則。**「兩者不可能同時說對
    # 今天的方向」「其中一邊撐不久」沒有任何禁用詞,卻是未經驗證的推論。
    # 改成結構性判準:Python 只能吐**數值觀測 + 幾何關係**,不得有自由散文。
    allowed = {"tension_id", "kind", "topic", "left", "right",
               "relationship", "evidence_refs", "usable_for_inference",
               "caveat"}
    for item in out["items"]:
        assert set(item) == allowed, f"多出自由欄位:{set(item) - allowed}"
        # **引用模組自己的白名單**,不要手抄一份 —— 手抄的那份會漂,
        # 而漂掉的症狀是「新的關係詞混進去了,測試還是綠的」。
        assert item["relationship"] in st.RELATIONSHIPS, item["relationship"]
        for side in ("left", "right"):
            assert isinstance(item[side]["value"], (int, float))
            assert set(item[side]) == {"label", "value", "unit", "evidence_ref"}
    banned = ("偏多", "偏空", "建議", "看好", "看空", "應該", "加碼", "減碼",
              "撐不久", "不可能", "要靠")
    blob = "".join(i["left"]["label"] + i["right"]["label"] + i["caveat"]
                   for i in out["items"])
    for w in banned:
        assert w not in blob, f"張力描述下了結論「{w}」"


def test_a_quiet_day_reports_nothing():
    """反向:訊號都不顯著的日子,不得為了有東西可報而硬湊。"""
    quiet = {"QQQ": {"change_pct": 0.2}, "TAIFEX_OI": {"foreign_oi_net": -1200},
             "TAIEX_PRED": {"pred_pct": 0.1}, "BREADTH": {"advance_ratio": 55.0},
             "SECTOR_HEAT": {"ranked": ["半導體業"], "sectors": {
                 "半導體業": {"median_pct": 0.4,
                            "leaders": [{"code": "2330", "name": "台積電",
                                         "pct": 0.3}]}}},
             "MACRO": {"10Y": {"close": 4.70, "prev_close": 4.69}}}
    out = st.detect(quiet)
    assert out["items"] == [], out["items"]
    assert len(out["checks_run"]) == 4, "檢查都跑了,只是沒有張力"


def test_a_bool_is_never_a_number():
    assert st.detect({"QQQ": {"change_pct": True},
                      "TAIFEX_OI": {"foreign_oi_net": True}})["items"] == []


# ---------------------------------------------------------------- 生產接線

def test_the_packet_carries_the_tensions_and_they_are_sanitized():
    """**進 packet、而且外部字串(產業名/股名)要過消毒器。**

    直接測 detect() 測得很漂亮、packet 沒帶就等於沒做 —— 老地方。
    """
    import evidence_packet as ep
    seen = []

    def _spy(text):
        seen.append(text)
        return text

    packet = ep.build(_REAL_QUOTES, {}, {}, [], [], {},
                      as_of="2026-08-04T06:00", target_session_date="2026-08-04",
                      sanitize=_spy)
    ts = packet.get("signal_tensions")
    assert isinstance(ts, dict) and ts["items"], "packet 沒帶 signal_tensions"
    assert any("台積電" in s for s in seen), "張力裡的股名沒有經過消毒器"
    assert packet["schema_version"] >= 2, "證據形狀變了,schema 版本卻沒進"
    # 進了 canonical_json → 進 prompt、進 sha
    assert "外部定價" in ep.canonical_json(packet)

# ------------------- 第十六輪:我上一批自己寫進去的三個缺陷

def test_a_defensive_leader_in_a_falling_sector_is_detected():
    """**P1-5A 符號錯誤**:第一版寫 `pct >= -LEADER_DROP_PCT`,
    而 `-(-1.0)` = `+1.0` —— 實際要求權值股**上漲 1%** 才算「抗跌」。

    於是「中位 −2.5% 而權值只跌 0.2%」這種**最典型的抗跌**完全抓不到。
    註解寫著抗跌、程式要求上漲,兩者差一個負號(實測確認過)。
    """
    q = {"SECTOR_HEAT": {"ranked": ["塑膠工業"], "sectors": {
        "塑膠工業": {"median_pct": -2.5,
                   "leaders": [{"code": "1301", "name": "台塑", "pct": -0.2}]}}}}
    hits = [i for i in st.detect(q)["items"] if i["topic"] == "產業內部分歧"]
    assert hits, "中位 -2.5%、權值 -0.2% 的抗跌沒被抓到"
    assert hits[0]["relationship"] == "median_below_leader"


def test_all_four_rate_tech_quadrants_are_covered():
    """**P1-5C 象限不全**:第一版只處理「利率升+科技漲」與「利率降+科技漲」,
    科技下跌的兩個象限完全沒有涵蓋。"""
    cases = {(12, 1.5): "tension", (-12, 1.5): "alignment",
             (12, -1.5): "alignment", (-12, -1.5): "tension"}
    for (bps, qqq), want in cases.items():
        q = {"QQQ": {"change_pct": qqq},
             "MACRO": {"10Y": {"close": 4.50 + bps / 100.0, "prev_close": 4.50}}}
        hits = [i for i in st.detect(q)["items"] if i["topic"] == "利率 vs 科技股"]
        assert len(hits) == 1, f"{bps}bps / {qqq}% 沒有產出"
        assert hits[0]["kind"] == want, f"{bps}bps / {qqq}% → {hits[0]['kind']}"


def test_a_us_holiday_marks_the_us_side_unusable():
    """**P1-4 新鮮度**:美股休市那天 QQQ 是上一交易日的延續值,
    拿它與今天的本地籌碼對照沒有意義。

    **不丟掉、只標不可用** —— 丟掉的話「今天沒有張力」與
    「今天的張力不可用」在下游長得一模一樣。
    """
    q = dict(_REAL_QUOTES, US_HOLIDAY={"detected": True})
    out = st.detect(q)
    us = [i for i in out["items"] if i["tension_id"] == "t_us_vs_taifex"][0]
    assert us["usable_for_inference"] is False
    assert "休市" in us["caveat"]
    local = [i for i in out["items"] if i["tension_id"] == "t_pred_vs_breadth"][0]
    assert local["usable_for_inference"] is True, "本地訊號不該被美股休市影響"
    # **不可用的不列入「必須處理」** —— 否則模型會被逼著解釋一個假矛盾
    assert "tension:t_us_vs_taifex" not in st.required_tension_ids(out)
    # 平日的 dict 也有 detected 欄位,用 truthiness 判斷會天天誤判休市
    ok = st.detect(dict(_REAL_QUOTES, US_HOLIDAY={"detected": False}))
    assert all(i["usable_for_inference"] for i in ok["items"])


def test_the_tension_ids_are_citable():
    """**張力要有可引用的 ID** —— 沒有的話,模型談那筆矛盾時只能引新聞,
    而那正是 typed registry 要解決的問題(P1-1)。"""
    refs = st.evidence_refs(st.detect(_REAL_QUOTES))
    assert "tension:t_us_vs_taifex" in refs
    assert "market:QQQ.change_pct" in refs
    assert "market:TAIFEX_OI.foreign_oi_net" in refs


def test_the_packet_registry_accepts_tension_and_market_ids():
    """**生產入口**:packet 的 registry 要收得下這三類,否則引用一律被判偽造。"""
    import evidence_packet as ep
    packet = ep.build(_REAL_QUOTES, {}, {},
                      [{"source_item_id": "n1", "title": "t", "summary": "s",
                        "source": "x"}], [], {},
                      as_of="2026-08-04T06:00", target_session_date="2026-08-04",
                      sanitize=str)
    ids = ep.evidence_ids(packet)
    assert "n1" in ids
    assert "tension:t_us_vs_taifex" in ids, "張力 ID 不在 registry 裡"
    assert "market:QQQ.change_pct" in ids, "行情 ID 不在 registry 裡"

def test_the_sector_gap_threshold_has_teeth():
    """反向:差距不到門檻就不報 —— 否則每天每個產業都會冒出一筆。

    **門檻是我自訂的**(±5,000 口與 60% 有 repo 出處,這個沒有),
    所以正反兩面都要釘住,調它的人才看得見代價。
    """
    small = {"SECTOR_HEAT": {"ranked": ["水泥工業"], "sectors": {
        "水泥工業": {"median_pct": 1.0,
                   "leaders": [{"code": "1101", "name": "台泥", "pct": -0.5}]}}}}
    assert [i for i in st.detect(small)["items"]
            if i["topic"] == "產業內部分歧"] == [], "1.5pp 的差距不該報"
    assert st.SECTOR_GAP_PP == 2.0

# ------------------- 第十七輪:59.7% 不是「方向相反」

def test_positive_but_narrow_breadth_is_not_called_opposite():
    """**P1-4:先前 59.7%(正向)與 38%(真的偏空)拿到同一個標籤。**

    判準寫成 `same = (pred > 0) == (ratio >= 60)` —— 60% 是**強度**門檻,
    不是方向分界。於是模型收到一個「方向相反」的張力並被要求正面處理,
    就會寫出「市場廣度偏空」,而 653 檔上漲、360 檔下跌不是偏空。
    **而且我的測試把這個錯誤語意鎖住了。**
    """
    def _rel(ratio):
        q = {"TAIEX_PRED": {"pred_pct": 0.47}, "BREADTH": {"advance_ratio": ratio}}
        return st.detect(q)["items"][0]
    narrow = _rel(59.7)
    assert narrow["relationship"] == "aligned_but_narrow"
    assert narrow["kind"] == "tension", "正向但不夠廣仍是值得處理的張力"
    opposed = _rel(38.0)
    assert opposed["relationship"] == "opposite_direction"
    assert narrow["relationship"] != opposed["relationship"], (
        "正向但不夠廣、與真的偏空,不得共用同一個標籤")
    assert _rel(65.0)["kind"] == "alignment"


def test_the_relationship_vocabulary_carries_no_economics():
    """**P1-6**:`supportive_for_growth` 已經是經濟解釋 ——
    利率升未必壓抑科技股,利率降可能是通膨改善也可能是衰退擔憂。
    Python 只說符號關係,解釋是模型的工作(而且要標成 inference)。"""
    banned = ("growth", "bull", "bear", "supportive", "risk_on", "risk_off")
    for rel in st.RELATIONSHIPS:
        for w in banned:
            assert w not in rel, f"關係詞帶了經濟解釋:{rel}"


def test_nested_market_leaves_are_citable():
    """**P1-1**:先前只走一層,`market:MACRO.10Y.close` 這種**真正會被分析
    的數字**沒有合法引用對象 —— 模型只能留空或去引一則新聞替它背書。"""
    import evidence_packet as ep
    ids = ep.evidence_ids(ep.build(
        {"MACRO": {"10Y": {"close": 4.69, "prev_close": 4.745}},
         "SECTOR_HEAT": {"ranked": ["半導體業"], "sectors": {
             "半導體業": {"median_pct": 3.6,
                        "leaders": [{"code": "2330", "name": "台積電",
                                     "pct": -2.3}]}}},
         "DATA_QUALITY": {"anything": 1}},
        {}, {}, [], [], {}, as_of="x", target_session_date="y", sanitize=str))
    assert "market:MACRO.10Y.close" in ids
    assert "market:SECTOR_HEAT.sectors.半導體業.median_pct" in ids
    # 清單用**識別欄位**當路徑,不用索引 —— 索引會隨當日資料量漂移
    assert "market:SECTOR_HEAT.sectors.半導體業.leaders.2330.pct" in ids
    # 診斷區塊**不註冊**:讓模型引用它只會製造「看起來有根據」
    assert not [i for i in ids if "DATA_QUALITY" in i]


def test_the_tension_refs_match_the_registry_paths():
    """張力給的引用路徑要與 registry 的正規路徑**同名** ——
    否則模型引用張力提供的那個,會落在一個別名上而不是真正的欄位。"""
    import evidence_packet as ep
    packet = ep.build(_REAL_QUOTES, {}, {}, [], [], {},
                      as_of="x", target_session_date="y", sanitize=str)
    market = ep.market_refs(packet.get("market"))
    for item in (packet.get("signal_tensions") or {})["items"]:
        for ref in item["evidence_refs"]:
            assert ref in market, f"張力引用了 registry 沒有的路徑:{ref}"
    # **registry 不得膨脹到「什麼都引得到」** —— 那時引用檢查就失去作用。
    # 這份 fixture 是一天行情的典型規模;數量爆掉要先紅在這裡。
    assert len(market) < 60, f"registry 膨脹到 {len(market)} 個 ID"
