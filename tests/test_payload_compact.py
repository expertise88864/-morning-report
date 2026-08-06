# -*- coding: utf-8 -*-
"""**第二層壓縮**(第二十四輪 P1-2 回歸)。

2026-08-06 生產:背景區塊全裁光後仍有 910,312 字元(上限 600,000),
剩下的全在 `payload_budget` 的不可裁清單裡 —— 硬閘門每天正確地擋下請求,
而特化路徑沒有任何一天可能成功。

必補測試 4:**8 月 6 日尺寸等級的 fixture 經壓縮後必須低於上限。**
"""
from __future__ import annotations

import json

import payload_budget as pb
import payload_compact as pc


def _size(x):
    return len(json.dumps(x, ensure_ascii=False, default=str))


def _forecast(i):
    """實測形狀:每檔約 2,486 字元,六成是模型內部管線。"""
    quality = {"model_version": "tw-top100-decay-regime-ridge-platt-quantile-v4",
               "training_rows": 13150, "recent_direction_hit_pct": 83.8,
               "probability_calibrated": True, "fallback_enabled": False,
               "model_monitoring_status": "ok" * 90,
               "feature_drift_penalty_detail": "drift" * 60}
    horizon = {"label": "隔日開盤", "horizon_days": 1, "expected_price": 2456.77,
               "expected_return_pct": 0.69, "lower": 2350.24, "upper": 2571.27,
               "interval_pct": 4.53, "conformal_adj_pct": 0.69,
               "beat_market_probability": 0.487,
               "model_method": "time-decayed ridge + regime blend + Platt + quantile",
               "quality": quality}
    return {"method": "收縮動能 + 結構分數 + 已驗證新聞催化 + 歷史偏誤",
            "regime": "neutral", "confidence": "低",
            "1d_open": dict(horizon), "3d": dict(horizon), "5d": dict(horizon)}


def _universe(n=100):
    """生產實測:每檔 4,179 字元(其中 price_forecast 2,486)。"""
    return [{"code": f"{1000+i}", "name": f"股{i}", "industry": "半導體業",
             "close": 100.0 + i, "day_pct": 0.5, "pct_5d": 1.2,
             "market_cap": 1e11, "ranking_score": float(n - i),
             "eps": 5.0, "rev_yoy_pct": 30.0, "foreign_lot": 100,
             "attention_score": 42.0, "daily_vol_pct": 1.8,
             "ma20_dist_pct": 3.2, "major_holder_pct": 60.1,
             "tdcc_wow_pct": 0.02, "short_cover_ratio": 1.1,
             "inst_buy_vol_ratio": 0.3, "rev_surprise_pct": 5.0,
             "news_catalysts": [{"event_id": f"e{i}", "event_type": "orders",
                                 "direction": 1, "note": "催化" * 90}],
             "price_forecast": _forecast(i)} for i in range(n)]


def _news(n=220):
    """生產實測:220 則 ≈ 280K(每則約 1,270)。"""
    return [{"source_item_id": f"n{i:04d}", "title": f"新聞標題{i}" * 20,
             "summary": "摘要內容" * 150, "source": "Reuters",
             "source_name": "Reuters", "entities": ["台積電", "NVIDIA"],
             "numeric_facts": [{"key": "amount", "value": 80, "unit": "億美元",
                                "context": "訂單金額" * 32}],
             "url": "https://example.com/" + "a" * 40,
             "published": "2026-08-05T10:00:00Z"} for i in range(n)]


def _packet_2026_08_06():
    """重現生產尺寸等級(910,312):背景區塊已被 trim 裁光後的形狀。

    比例依實測:tw_universe ≈ 418K、news ≈ 280K、其餘(行情核心/張力)≈ 210K。
    """
    return {
        "schema_version": "v17",
        "tw_universe": _universe(),
        "news": _news(),
        "news_clusters": {"required_cluster_ids": ["cluster:n0000"],
                          "clusters": [{"cluster_id": "cluster:n0000",
                                        "member_source_ids": ["n0000", "n0001"]}]},
        "top_events": [{"cluster_id": "cluster:n0000",
                        "member_source_ids": ["n0000"]}],
        "signal_tensions": {"items": [
            {"tension_id": f"t{i}", "why": "張力說明" * 40,
             "evidence_refs": [f"market:X{i}"]} for i in range(40)]},
        # 行情核心是**不可裁**的(它們是分析原料),照生產規模給
        "market": {f"BLOCK_{i}": {"note": "行情核心" * 2600} for i in range(20)},
    }


def test_fixture_reproduces_the_production_scale():
    """fixture 本身要真的是生產尺寸等級,否則這份測試證明不了任何事。"""
    pk = _packet_2026_08_06()
    assert _size(pk) > pb.MAX_PAYLOAD_CHARS, "fixture 必須超標(否則沒在測壓縮)"


def test_production_sized_packet_comes_under_limit_after_compaction():
    """**必補測試 4**:8/6 尺寸等級經壓縮後必須低於 packet 上限。"""
    pk = _packet_2026_08_06()
    out, rep = pc.compact(pk, limit=pb.MAX_PAYLOAD_CHARS)
    assert rep["applied"], "超標卻什麼都沒壓"
    assert rep["chars_after"] <= pb.MAX_PAYLOAD_CHARS, (
        f"壓縮後仍 {rep['chars_after']:,} > {pb.MAX_PAYLOAD_CHARS:,}")
    assert rep["over_budget"] is False


def test_compaction_never_drops_stock_codes():
    """**代號一律保留** —— `analysis_validate` 用它當「這個代號今天存在」的
    白名單,刪列會讓合法的個股分析被判成捏造代號。"""
    pk = _packet_2026_08_06()
    before = {r["code"] for r in pk["tw_universe"]}
    out, _rep = pc.compact(pk, limit=pb.MAX_PAYLOAD_CHARS)
    after = {r["code"] for r in out["tw_universe"]}
    assert after == before, f"掉了代號:{before - after}"
    assert len(out["tw_universe"]) == len(pk["tw_universe"])


def test_compaction_never_drops_news_items():
    """**不刪則數** —— 刪則會讓 news_clusters / top_events 的成員 ID 指空。"""
    pk = _packet_2026_08_06()
    before = [n["source_item_id"] for n in pk["news"]]
    out, _rep = pc.compact(pk, limit=pb.MAX_PAYLOAD_CHARS)
    assert [n["source_item_id"] for n in out["news"]] == before


def test_required_and_top_event_news_keep_full_summaries():
    """必分析與三大重點的新聞**不縮摘要** —— 那正是要深入分析的材料。"""
    pk = _packet_2026_08_06()
    full = {n["source_item_id"]: n["summary"] for n in pk["news"]}
    out, _rep = pc.compact(pk, limit=pb.MAX_PAYLOAD_CHARS)
    by_id = {n["source_item_id"]: n for n in out["news"]}
    for sid in ("n0000", "n0001"):          # required cluster 的成員
        assert by_id[sid]["summary"] == full[sid], f"{sid} 的摘要不該被縮"


def test_forecast_keeps_headline_numbers_drops_plumbing():
    """留標頭數字、丟模型內部管線。"""
    pk = _packet_2026_08_06()
    out, _rep = pc.compact(pk, limit=pb.MAX_PAYLOAD_CHARS)
    fc = out["tw_universe"][0]["price_forecast"]
    assert fc["1d_open"]["expected_return_pct"] == 0.69     # 標頭數字留著
    assert fc["1d_open"]["lower"] and fc["1d_open"]["upper"]
    blob = json.dumps(fc, ensure_ascii=False)
    for junk in ("model_version", "training_rows", "model_method",
                 "probability_calibrated", "fallback_enabled"):
        assert junk not in blob, f"{junk} 是模型內部管線,不該進 payload"


def test_analyzed_rows_keep_full_detail():
    """觀察名單前段(ranking_score 高)要保留完整欄位。"""
    pk = _packet_2026_08_06()
    out, _rep = pc.compact(pk, limit=pb.MAX_PAYLOAD_CHARS)
    top = out["tw_universe"][0]           # ranking_score 最高
    assert "price_forecast" in top and "eps" in top
    tail = out["tw_universe"][-1]         # 最低
    assert "code" in tail and "close" in tail       # 骨架仍有可辨識的證據


def test_tier2_skeleton_fires_under_tighter_limit_and_keeps_all_codes():
    """第二級(骨架化)在更緊的上限下必須啟動,**而且代號一個都不能少**。

    生產 910K 時第一級之後仍超標,第二、三級一定會跑 —— 只測第一級
    等於沒測到真正會發生的路徑。
    """
    pk = _packet_2026_08_06()
    out, rep = pc.compact(pk, limit=400_000)
    tiers = [a["tier"] for a in rep["applied"]]
    assert "universe.non_analyzed_to_skeleton" in tiers
    assert {r["code"] for r in out["tw_universe"]} == {
        r["code"] for r in pk["tw_universe"]}, "骨架化不得掉代號"
    # 骨架列仍帶得走可辨識的證據
    assert all("code" in r and "name" in r for r in out["tw_universe"])


def test_tier3_summary_fires_under_tightest_limit_without_dropping_news():
    """第三級(縮摘要)啟動時,**則數與 ID 完全不變**。"""
    pk = _packet_2026_08_06()
    out, rep = pc.compact(pk, limit=260_000)
    tiers = [a["tier"] for a in rep["applied"]]
    assert "news.low_materiality_summary" in tiers
    assert [n["source_item_id"] for n in out["news"]] == [
        n["source_item_id"] for n in pk["news"]]
    # 必分析新聞的摘要仍是完整的
    by_id = {n["source_item_id"]: n for n in out["news"]}
    assert len(by_id["n0000"]["summary"]) > pc.COMPACT_SUMMARY_CHARS
    # 一般新聞已被縮短
    assert len(by_id["n0219"]["summary"]) <= pc.COMPACT_SUMMARY_CHARS


def test_still_over_budget_is_reported_honestly():
    """**壓不下去就要說壓不下去** —— 硬閘門靠這個旗標擋住必敗的請求。

    壓縮讓 gate 有機會通過,但不得把「還是太大」粉飾成通過。
    """
    pk = _packet_2026_08_06()
    out, rep = pc.compact(pk, limit=1_000)
    assert rep["applied"], "極端上限下仍應盡力壓縮"
    assert rep["over_budget"] is True
    assert rep["chars_after"] > 1_000
    # gate 必須據此擋下
    import pytest
    with pytest.raises(pb.PayloadBudgetExceeded):
        pb.gate({"over_budget": True, "chars_after": rep["chars_after"],
                 "limit": 1_000})


def test_under_limit_packet_is_untouched():
    """沒超標就什麼都不做(壓縮是例外路徑,不是常態)。"""
    small = {"news": [{"source_item_id": "n1", "summary": "x"}],
             "tw_universe": [{"code": "2330", "price_forecast": _forecast(0)}]}
    out, rep = pc.compact(small, limit=pb.MAX_PAYLOAD_CHARS)
    assert rep["applied"] == [] and rep["over_budget"] is False
    assert out["tw_universe"][0]["price_forecast"] == small[
        "tw_universe"][0]["price_forecast"]


def test_compaction_does_not_mutate_input():
    """與 `trim()` 同一個契約:不改變輸入。"""
    pk = _packet_2026_08_06()
    snapshot = json.dumps(pk, ensure_ascii=False, default=str)
    pc.compact(pk, limit=pb.MAX_PAYLOAD_CHARS)
    assert json.dumps(pk, ensure_ascii=False, default=str) == snapshot


def test_compaction_is_disclosed():
    """**沒有靜默的壓縮**:壓過就要留下必須揭露的缺口。"""
    pk = _packet_2026_08_06()
    out, rep = pc.compact(pk, limit=pb.MAX_PAYLOAD_CHARS)
    disc = out.get("required_disclosures") or {}
    assert "gap:payload_compacted" in disc
    assert rep["applied"] and all(a["chars_saved"] > 0 for a in rep["applied"])


def test_budget_apply_records_everything_and_gates_last():
    """`payload_budget.apply()` 是預算政策的單一入口:裁 → 壓 → 記錄 → 閘門。

    量測不得是死程式碼(`block_sizes()` 先前從未被呼叫,所以沒有人知道
    那 910K 是什麼),而閘門必須量**壓縮之後**的大小。
    """
    manifest: dict = {}
    pk = _packet_2026_08_06()
    out = pb.apply(pk, manifest)
    llm = manifest["llm"]
    assert llm["payload_compact"]["applied"], "第二層壓縮沒有被執行/記錄"
    assert llm["block_sizes"], "block_sizes 沒有進 manifest(量測仍是死的)"
    assert llm["payload_budget"]["over_budget"] is False
    assert llm["payload_budget"]["chars_after"] == llm[
        "payload_compact"]["chars_after"], "閘門量的必須是壓縮後的大小"
    assert _size(out) <= pb.MAX_PAYLOAD_CHARS


def test_budget_apply_still_raises_when_impossible():
    """壓完仍超標 → 硬閘門照樣擋(呼叫端落回 legacy,信不斷)。

    構造:**不可裁也不可壓**的行情核心自己就超過上限 —— 壓縮讓 gate 有機會
    通過,但不得把「還是太大」粉飾成通過。
    """
    import pytest
    pk = {"market": {"CORE": {"note": "x" * (pb.MAX_PAYLOAD_CHARS + 50_000)}},
          "tw_universe": [], "news": []}
    manifest: dict = {}
    with pytest.raises(pb.PayloadBudgetExceeded):
        pb.apply(pk, manifest)
    assert manifest["llm"]["payload_budget"]["over_budget"] is True

