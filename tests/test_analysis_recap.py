# -*- coding: utf-8 -*-
"""**昨日觀點閉環**(分析面縱深)。

prompt 第 150 行起早就要求「延續事件寫增量」—— 但模型沒有 diff 的對象,
那是一句無法執行的要求;也沒有任何檢查量得到「今天只是把昨天再說一次」。
閉環:分析成功 → 存 key_drivers 觀點 → 明天掛在同一件事的事件群上
(`yesterday_view`)→ 重述度過高退回加深。
"""
import analysis_depth as ad
import analysis_recap as rc
import evidence_packet as ep
import fixtures_analysis as fx


def _packet(news=None, recap=None, date="2026-08-08"):
    q = {"QQQ": {"change_pct": 1.0}}
    if recap is not None:
        q["ANALYSIS_RECAP"] = recap
    return ep.build(q, {}, {}, news if news is not None else fx.news(),
                    [], {}, as_of="x", target_session_date=date,
                    sanitize=lambda s: s)


def _saved(statement="台積電先進封裝產能瓶頸外溢,對封測廠是結構性利多"):
    """昨天存下來的形狀 —— 由 `extract` 從真的分析物件產生,
    不是測試自己發明的整齊形狀。fixture 裡 n2 的實體才是台積電
    (n1 是費半 —— 選錯群的話,別名比對測的是空集合)。"""
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0],
                               statement=statement, cluster_id="cluster:n2")]
    pk = _packet(date="2026-08-07")
    rec = rc.extract(obj, pk)
    assert rec["items"][0]["entities"] == ["台積電"], rec
    return rec


# ------------------------------------------------------------ 抽取與存取

def test_extract_keys_the_view_by_entities_not_cluster_id():
    """cluster_id 是「群裡最小的 source_item_id」,明天必然換號 ——
    觀點要以實體為身分,否則存了也接不回來。"""
    rec = _saved()
    assert rec["date"] == "2026-08-07"
    assert rec["items"] and rec["items"][0]["entities"], rec
    assert rec["items"][0]["direction"]


def test_save_and_load_round_trip(tmp_path):
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0], cluster_id="cluster:n1")]
    f = tmp_path / "analysis_recap.json"
    assert rc.save(f, obj, _packet(date="2026-08-07")) is True
    assert rc.load(f)["date"] == "2026-08-07"


def test_save_to_an_unwritable_path_degrades_without_raising(tmp_path):
    """加深不可斷晨報:存不進去回 False,不拋。"""
    assert rc.save(tmp_path, fx.valid_analysis(), _packet()) is False


def test_statement_is_stored_as_judgment_not_essay():
    rec = _saved(statement="長" * 500)
    assert len(rec["items"][0]["statement"]) == rc.STATEMENT_CHARS


# ------------------------------------------------------------ 同日重跑守衛

def test_same_day_rerun_must_not_diff_against_itself():
    """手動 dispatch 會把「今天早上」存進 state —— 拿它比就是今天比今天,
    會產生假的強化/推翻(legacy 的 `_format_narrative_delta` 踩過)。"""
    rec = _saved()
    assert rc.usable(rec, "2026-08-08"), "昨天的觀點要可用"
    assert rc.usable(rec, "2026-08-07") == [], "同日不得自比"
    assert rc.usable(rec, "2026-08-06") == [], "未來的觀點是資料錯亂"


# ------------------------------------------------------------ 掛上事件群

def test_yesterday_view_attaches_across_alias():
    """昨天寫「台積電」,今天的群寫「TSMC」—— 同一件事要接得上
    (與 continuing_days 同一套身分哲學)。"""
    pk = _packet(
        news=[{"source_item_id": "n1", "title": "TSMC CoWoS orders spill",
               "entities": ["TSMC"], "source": "CNBC", "source_name": "CNBC"}],
        recap=_saved())
    v = pk["news_clusters"]["clusters"][0]["yesterday_view"]
    assert "2026-08-07本報" in v and "封測廠" in v, v
    assert "偏多" in v


def test_unrelated_cluster_gets_no_view():
    pk = _packet(
        news=[{"source_item_id": "n1", "title": "長榮7月營收年增43%",
               "entities": ["長榮"], "source": "X", "source_name": "X"}],
        recap=_saved())
    assert pk["news_clusters"]["clusters"][0]["yesterday_view"] == ""


def test_no_recap_state_degrades_to_empty_view():
    pk = _packet(recap=None)
    assert all(c["yesterday_view"] == ""
               for c in pk["news_clusters"]["clusters"])


def test_the_view_goes_through_the_sanitizer():
    """recap 的敘述是回流的模型輸出(內含外部素材的轉述)——
    與其他字串葉節點一樣要過整樹消毒。"""
    q = {"ANALYSIS_RECAP": _saved()}
    pk = ep.build(q, {}, {},
                  [{"source_item_id": "n1", "title": "TSMC news",
                    "entities": ["TSMC"], "source": "X", "source_name": "X"}],
                  [], {}, as_of="x", target_session_date="2026-08-08",
                  sanitize=lambda s: s.replace("封測", "封測▲"))
    assert "封測▲" in pk["news_clusters"]["clusters"][0]["yesterday_view"]


def test_yesterday_view_is_not_citable_evidence():
    """**拿自己昨天的判斷當今天的證據是循環引用。** ANALYSIS_RECAP
    不進 registry —— 模型引用 `market:ANALYSIS_RECAP.*` 必須被判不存在。"""
    pk = _packet(recap=_saved())
    assert not [i for i in ep.evidence_ids(pk)
                if "ANALYSIS_RECAP" in str(i)]


# ------------------------------------------------------------ 重述檢查

def _analysis_with(statement):
    obj = fx.valid_analysis()
    obj["key_drivers"] = [dict(obj["key_drivers"][0],
                               statement=statement, cluster_id="cluster:n1")]
    return obj


def _pk_with_view():
    return _packet(
        news=[{"source_item_id": "n1", "title": "TSMC CoWoS orders spill",
               "entities": ["TSMC"], "source": "CNBC", "source_name": "CNBC"}],
        recap=_saved())


def test_restating_yesterday_is_flagged_for_deepening():
    """把昨天的判斷換兩個字再說一次 —— 要被退回加深。"""
    hits = [a for a in ad.depth_advisories(
        _analysis_with("台積電先進封裝產能瓶頸外溢,對封測廠是結構性利多"),
        _pk_with_view()) if "昨日觀點" in a]
    assert hits, "整句重述沒有被看見"


def test_a_real_delta_is_not_flagged():
    """真的寫了增量(新的量、新的方向理由)不得誤傷 —— 誤傷會訓練出
    「看到 advisory 就改寫措辭」的模型行為,而不是更深的分析。"""
    hits = [a for a in ad.depth_advisories(
        _analysis_with("日月光今日證實追加設備採購,瓶頸外溢從傳聞升級為事實"),
        _pk_with_view()) if "昨日觀點" in a]
    assert hits == [], hits


def test_no_view_means_no_restatement_check():
    """第一天出現的事件沒有 diff 基準 —— 不得拿空字串亂比。"""
    hits = [a for a in ad.depth_advisories(
        _analysis_with("台積電先進封裝產能瓶頸外溢,對封測廠是結構性利多"),
        _packet()) if "昨日觀點" in a]
    assert hits == []


# ------------------------------------------------------------ 生產接線

def test_production_wires_both_ends_of_the_loop():
    """**守衛不得靠遺忘失效**:存與讀都只在生產有接線時存在 ——
    上面每一條在生產斷線時照樣綠,而閉環整段 no-op。"""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "morning_report.py").read_text(encoding="utf-8")
    # 寫入端:成功點存檔,而且結果進 manifest(靜默失敗明天才發現就晚了)
    assert '_RUN_MANIFEST["llm"]["recap_saved"] = _arc.save(' in src
    # 讀取端:與 EVENT_TIMELINE 同一段接進 quotes
    assert 'quotes["ANALYSIS_RECAP"] = _arc.load(' in src
