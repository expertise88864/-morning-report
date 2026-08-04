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
    assert "+1.76%" in hits[0]["a"]
    assert "90,038" in hits[0]["b"], "口數要保留千分位原值,不得改寫"


def test_predicted_open_up_vs_narrow_breadth_is_detected():
    """59.7% 對 60% 門檻 —— 當天的實際邊界案例,差 0.3 個百分點。"""
    out = st.detect(_REAL_QUOTES)
    hits = [i for i in out["items"] if i["topic"] == "開盤預測 vs 市場廣度"]
    assert len(hits) == 1
    assert "59.7%" in hits[0]["b"] and "60%" in hits[0]["b"]


def test_sector_median_vs_falling_leaders_is_detected():
    """**半導體中位 +3.6% 而台積電 -2.3%** —— 外審點名、信裡沒人指出的結構分歧。"""
    out = st.detect(_REAL_QUOTES)
    hits = [i for i in out["items"] if i["topic"] == "產業內部分歧"]
    assert hits, "產業內部分歧沒被抓到"
    assert any("2330" in h["b"] and "+3.6" in h["a"] for h in hits), hits


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
    banned = ("偏多", "偏空", "建議", "看好", "看空", "應該", "加碼", "減碼")
    for item in out["items"]:
        blob = item["a"] + item["b"] + item["note"]
        for w in banned:
            assert w not in blob, f"張力描述下了結論「{w}」:{blob}"


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
