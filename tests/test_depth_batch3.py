# -*- coding: utf-8 -*-
"""**深度優化第三批:表要自己說話、標的要是證據裡的人、延續事件寫增量。**

三項的來源都是 2026-08-05 的實信:

  * 類股熱度表:半導體佔 40.5%、中位 +2.5%,而台積電 -2.1%、聯發科 -1.1%
    —— 四個數字都在,合起來的那句話(買盤在中小型不在龍頭)沒有人說;
  * 「延燒中事件(第 4 天)」與八段的分析是兩個互不知道對方的系統;
  * `AI`/`GPU` 仍能冒充 asset_id(外審 P2-4 的收尾)。
"""
import analysis_schema as sch
import evidence_packet as ep
import fixtures_analysis as fx
import sector_readout as sr

_HEAT_0805 = {
    "ranked": ["半導體業", "電子零組件業"],
    "sectors": {"半導體業": {
        "value_share_pct": 40.5, "median_pct": 2.5,
        "leaders": [{"code": "2330", "name": "台積電", "pct": -2.1},
                    {"code": "2454", "name": "聯發科", "pct": -1.1}]}},
}


# ---------------------------------------------------------------- 熱度表解讀

def test_the_real_0805_divergence_is_called_out():
    """**衝突不講出來就會被當成一致。** 用實信的真實數字釘住。"""
    out = sr.readout(_HEAT_0805)
    assert "買盤在中小型、不在龍頭" in out, out
    assert "2330台積電" in out
    assert "40%集中在半導體業" in out, "集中度也要講"


def test_agreement_means_silence():
    """**沒話說就沉默** —— 寧可少一句,不要生一句空話。"""
    assert sr.readout({"ranked": ["半導體業"], "sectors": {"半導體業": {
        "value_share_pct": 28.0, "median_pct": 2.0,
        "leaders": [{"code": "2330", "name": "台積電", "pct": 1.8}]}}}) == ""
    assert sr.readout({}) == "" and sr.readout(None) == ""


def test_the_readout_never_recommends():
    """只描述,不建議 —— 判準是禁用詞掃描,不是我讀過覺得還好。"""
    for case in (_HEAT_0805,
                 {"ranked": ["金融保險"], "sectors": {"金融保險": {
                     "value_share_pct": 52.0, "median_pct": -1.5,
                     "leaders": [{"code": "2881", "name": "富邦金",
                                  "pct": 2.0}]}}}):
        out = sr.readout(case)
        for w in sr.BANNED:
            assert w not in out, f"出現建議語氣「{w}」:{out}"


def test_a_bool_is_never_a_number():
    """`True` 是 1 —— 不擋的話會被當成中位數拿去比大小。"""
    assert sr.readout({"ranked": ["X"], "sectors": {"X": {
        "value_share_pct": True, "median_pct": True,
        "leaders": [{"code": "1", "name": "Y", "pct": True}]}}}) == ""


def test_the_card_actually_renders_the_line():
    """**生產那條路要接上** —— 直接測 `readout()` 測得很漂亮、
    卡片沒有印出來,是本 repo 反覆栽的地方。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "morning_report.py"
           ).read_text(encoding="utf-8")
    assert "_sr.readout(quotes.get(\"SECTOR_HEAT\"))" in src
    assert "_htmllib.escape(_heat_line)" in src, "解讀沒有跳脫就進 HTML"


# ---------------------------------------------------------------- 標的與證據

def _packet(news):
    return ep.build({}, {}, {}, news, [], {}, as_of="2026-08-05T06:00",
                    target_session_date="2026-08-05", sanitize=str)


def test_a_concept_cannot_impersonate_a_ticker():
    """**字串格式分不出「代號」與「概念」,證據分得出。**

    `_ASSET_LIKE` 放行任何 2–6 個大寫字母 —— `AI`、`GPU`、`CHIP` 都能
    冒充標的;可是 `AMD`、`TSM` 又是真的。判準:大寫字母的標的要在
    **該則新聞的實體或標題**裡。
    """
    pk = _packet([{"source_item_id": "n1", "title": "AMD 資料中心營收年增 107%",
                   "entities": ["AMD"], "source": "CNBC"},
                  {"source_item_id": "n2", "title": "台積電法說會下週登場",
                   "entities": ["台積電"], "source": "經濟日報"}])
    for aid, blocked in (("GPU", True), ("AI", True), ("CHIP", True),
                         ("AMD", False), ("2330", False), ("TAIEX", False)):
        obj = fx.valid_analysis()
        obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = aid
        hit = [p for p in sch.validate(obj, pk)
               if "不在這則" in p or "泛稱" in p]
        assert bool(hit) == blocked, f"{aid}: {hit}"


def test_a_ticker_in_the_title_but_not_the_entities_still_passes():
    """實體抽取會漏,標題不會說謊 —— NVDA 在標題裡就放行。"""
    pk = _packet([{"source_item_id": "n1",
                   "title": "NVDA 財報後供應鏈同步走高",
                   "entities": ["輝達"], "source": "工商時報"},
                  {"source_item_id": "n2", "title": "台積電法說會下週登場",
                   "entities": ["台積電"], "source": "經濟日報"}])
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = "NVDA"
    assert not [p for p in sch.validate(obj, pk) if "不在這則" in p]


def test_without_a_packet_the_rule_stays_quiet():
    """舊呼叫端傳 ID 集合 —— 查不到新聞實體就不判,**不誤擋**。"""
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = "GPU"
    assert not [p for p in sch.validate(obj, fx.ids()) if "不在這則" in p]


# ---------------------------------------------------------------- 延續事件

def test_a_continuing_event_carries_its_day_count():
    """**「延燒中事件(第 4 天)」與八段的分析先前是兩個互不知道對方的
    系統。** timeline 的天數要接到事件群上,模型才知道哪些事昨天分析過。"""
    pk = ep.build({"EVENT_TIMELINE": [
                       {"entity": "伊朗", "days": 4, "latest_title": "戰事"}]},
                  {}, {}, [{"source_item_id": "n1",
                            "title": "美伊戰事第四天,談判傳進展",
                            "entities": ["伊朗"], "source": "鉅亨"}],
                  [], {}, as_of="x", target_session_date="y", sanitize=str)
    assert pk["news_clusters"]["clusters"][0]["continuing_days"] == 4


def test_a_fresh_event_is_day_zero():
    pk = ep.build({"EVENT_TIMELINE": [
                       {"entity": "伊朗", "days": 4, "latest_title": "戰事"}]},
                  {}, {}, [{"source_item_id": "n1", "title": "聯發科法說會",
                            "entities": ["聯發科"], "source": "經濟日報"}],
                  [], {}, as_of="x", target_session_date="y", sanitize=str)
    assert pk["news_clusters"]["clusters"][0]["continuing_days"] == 0


def test_the_prompt_demands_increments_for_continuing_events():
    import prompt_profiles as pp
    assert "寫增量,不是重述" in pp.LUNA_DEVELOPER_INSTRUCTIONS
    assert "continuing_days" in pp.LUNA_DEVELOPER_INSTRUCTIONS
