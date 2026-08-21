# -*- coding: utf-8 -*-
"""2026-08-21 使用者拍板:第九段類股工程(龍頭優先/能源桶/法人買賣超)。"""
import io
from pathlib import Path

import morning_report as mr

_SRC = io.open(Path(__file__).resolve().parents[1] / "morning_report.py",
               encoding="utf-8").read()


def test_energy_bucket_and_leaders_are_declared():
    assert "能源-台股" in mr.OTHER_SECTOR_QUERIES
    assert set(mr.SECTOR_LEADERS) >= {"金融", "航運", "生技", "能源", "重電"}
    assert any("台塑化" in x for x in mr.SECTOR_LEADERS["能源"])


def test_leader_priority_reaches_both_prompts():
    """選材原則要真的進 prompt(特化 profile + legacy)——沒接上等於不存在。"""
    import prompt_profiles as pp
    import inspect
    src = inspect.getsource(pp)
    assert "優先挑該類股龍頭公司的重大公告" in src
    assert "SECTOR_LEADERS.items()" in _SRC, "legacy prompt 沒接龍頭名單"


def test_sector_heat_aggregates_institutional_net(monkeypatch):
    """各類股法人買賣超:估值 = 淨買賣股數 × 收盤,聚到產業;
    法人資料壞掉只缺這一欄(其餘照常)。"""
    rows = [{"Code": "2330", "ClosingPrice": "1000", "Change": "10",
             "TradeValue": "6000000000"},
            {"Code": "2317", "ClosingPrice": "100", "Change": "-1",
             "TradeValue": "4000000000"}]
    basics = {"2330": {"industry": "半導體業", "name": "台積電"},
              "2317": {"industry": "半導體業", "name": "鴻海"}}
    monkeypatch.setattr(mr, "_fetch_twse_stock_day_all", lambda: rows)
    monkeypatch.setattr(mr, "_get_twse_listing_basics_cached", lambda: basics)
    monkeypatch.setattr(mr, "fetch_twse_institutional",
                        lambda: {"2330": {"total": 3_000_000},
                                 "2317": {"total": -1_000_000}})
    heat = mr.fetch_sector_heat(min_names=1)
    sec = heat["sectors"]["半導體業"]
    # 3e6×1000 − 1e6×100 = 29 億
    assert sec["inst_net_yi"] == 29.0, sec
    # 法人資料失敗 → 該欄 None,熱度表其他欄照常
    monkeypatch.setattr(mr, "fetch_twse_institutional",
                        lambda: (_ for _ in ()).throw(RuntimeError("down")))
    heat2 = mr.fetch_sector_heat(min_names=1)
    assert heat2["sectors"]["半導體業"]["inst_net_yi"] is None
    assert heat2["sectors"]["半導體業"]["value_yi"] > 0


def test_prompt_block_shows_institutional_column():
    blk = mr._format_sector_heat_block({
        "sectors": {"半導體業": {"n": 2, "up": 1, "down": 1,
                              "median_pct": 0.5, "value_yi": 100,
                              "value_share_pct": 40.0, "inst_net_yi": 29.0,
                              "leaders": []}},
        "ranked": ["半導體業"], "total_value_yi": 250})
    assert "法人 +29.0 億(估)" in blk, blk


# ---------------------- 外審 P2-2(範圍:預算三分;slim payload 另批)


def test_repair_round_has_its_own_budget():
    """08/20 生產:剩 651s 卻因「第一輪 546s + 保留 300s 裝不下」跳過
    修補、整份落 legacy —— 修補不該被假設與第一輪同價。成本估計 =
    min(上一輪, cap 300);放行後該輪預算 = min(cap, 剩餘−保留)。"""
    import time
    assert mr._repair_round_viable(651, 546) == (True, 300.0)
    assert mr._repair_round_viable(400, 546)[0] is False, "保留被吃掉了"
    assert mr._repair_round_viable(450, 100) == (True, 150.0)
    # r2:round 鉗制是**作用域制**(整個修補傳輸期間持續生效,含退避
    # 重試),由 _call_deepseek_responses 的 finally 清除;
    # call_llm_analysis 的 finally 是 gate 駁回等未送出路徑的安全網。
    mr._REPAIR_ROUND_DEADLINE = time.monotonic() + 50
    try:
        assert mr._llm_request_timeout() <= 50.5
        assert mr._llm_request_timeout() <= 50.5, "第二次就失效 = 重試吃得到保留"
        eff = mr._effective_llm_deadline()
        assert eff is not None and eff - time.monotonic() <= 50.5
    finally:
        mr._REPAIR_ROUND_DEADLINE = None
    assert mr._llm_request_timeout() > 100


def test_repair_deadline_is_cleared_by_the_transport_scope(monkeypatch):
    """退避重試收到的是 round deadline(不是總預算);傳輸結束(含例外)
    一定清;call_llm_analysis 收尾是未送出路徑的安全網。"""
    import time
    cap = {}

    def fake_post(url, body, headers, timeout=None, manifest=None,
                  deadline_at=None):
        cap["deadline_at"] = deadline_at
        return None

    monkeypatch.setattr(mr._lh, "post_with_backoff", fake_post)
    monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "x")
    mr._REPAIR_ROUND_DEADLINE = time.monotonic() + 50
    try:
        try:
            mr._call_deepseek_responses({"model": "m"})
        except TimeoutError:
            pass
        assert cap["deadline_at"] is not None
        assert cap["deadline_at"] - time.monotonic() <= 50.5,             "重試 deadline 沒被 round 預算夾住"
        assert mr._REPAIR_ROUND_DEADLINE is None, "傳輸結束沒清"
    finally:
        mr._REPAIR_ROUND_DEADLINE = None
    # 安全網:gate 駁回(沒送出)也不得外溢
    i = _SRC.index("# r2 安全網:修補獲准後若 request_gate 駁回")
    assert "_REPAIR_ROUND_DEADLINE = None" in _SRC[i:i + 300]


def test_legacy_fallback_clears_the_repair_clamp_before_running(monkeypatch):
    """r3(外審):gate 駁回後 legacy 在 impl 內就開跑,外層 finally 來
    不及 —— legacy 落回的路口要先清,否則 legacy 被 stale 修補預算夾住。
    功能面驗證:預設一個 50s 的 stale 鉗制,走 legacy 路徑(特化關閉),
    legacy 的請求 timeout 不得被夾在 50s。"""
    import time
    seen = {}

    def fake_call(prompt):
        seen["timeout"] = mr._llm_request_timeout()
        return ("## 今日重點\n盤面偏多。\n## 我的明確立場\n偏多(淨分 +3)\n"
                "## 一句話總結\n偏多。\n")

    monkeypatch.setattr(mr, "_call_llm_text", fake_call)
    monkeypatch.setattr(mr, "LLM_PROVIDER", "deepseek")
    mr._REPAIR_ROUND_DEADLINE = time.monotonic() + 50   # stale 鉗制
    try:
        out = mr.call_llm_analysis({}, {}, {}, [])
    finally:
        mr._REPAIR_ROUND_DEADLINE = None
    assert out
    assert seen.get("timeout", 0) > 100,         f"legacy 被 stale 修補預算夾住:{seen.get('timeout')}"


def test_leader_rule_sits_outside_the_untrusted_fence():
    """r2(外審):選材規則是**本報規則**,放進圍欄會被安全前言自己廢掉;
    要在 </UNTRUSTED_SOURCE_DATA> 之後、且無條件(不掛 company_news)。"""
    i = _SRC.index('news_block + "\\n</UNTRUSTED_SOURCE_DATA>")')
    j = _SRC.index("九、其他類股的選材原則(本報規則)")
    assert j > i, "選材規則仍在圍欄內"
    assert j - i < 900, "規則沒有緊跟在圍欄關閉之後"


def test_missing_institutional_data_registers_degradation(monkeypatch):
    """r2(外審):T86 端點耗盡時 fetch 自己吸收回 {} —— 空結果與例外
    走同一個降級標記,不得靜默消失。"""
    rows = [{"Code": "2330", "ClosingPrice": "1000", "Change": "10",
             "TradeValue": "6000000000"}]
    basics = {"2330": {"industry": "半導體業", "name": "台積電"}}
    monkeypatch.setattr(mr, "_fetch_twse_stock_day_all", lambda: rows)
    monkeypatch.setattr(mr, "_get_twse_listing_basics_cached", lambda: basics)
    monkeypatch.setattr(mr, "fetch_twse_institutional", lambda: {})
    before = len(mr._DEGRADED_STEPS)
    mr.fetch_sector_heat(min_names=1)
    assert "sector:institutional_missing" in mr._DEGRADED_STEPS[before:]
    # 有資料的日子不記
    monkeypatch.setattr(mr, "fetch_twse_institutional",
                        lambda: {"2330": {"total": 1000}})
    before2 = len(mr._DEGRADED_STEPS)
    mr.fetch_sector_heat(min_names=1)
    assert "sector:institutional_missing" not in mr._DEGRADED_STEPS[before2:]


def test_the_gate_uses_the_viability_helper():
    """接線:_consume_repair 的時間閘走 _repair_round_viable(單一判準),
    並在放行時設定該輪的一次性鉗制。"""
    i = _SRC.index("_ok, _round_budget = _repair_round_viable(_remaining, elapsed)")
    seg = _SRC[i:i + 1100]
    assert "_REPAIR_ROUND_DEADLINE = time.monotonic() + _round_budget" in seg
