# -*- coding: utf-8 -*-
"""**第二十二輪:名稱正確、實際沒測到的那些測試,這次要真的測到。**

上一輪的教訓再進一層:P2-4 點名了六條「名稱正確、實際沒有測到」——
`never reaches API` 沒有 mock API、`not counted` 只檢查旗子、
deadline 測試不驗經過時間。**測試的名字是承諾,內容要兌現它。**
"""
import analysis_schema as sch
import entity_alias as ea
import evidence_packet as ep
import fixtures_analysis as fx
import news_clusters as nc
import payload_budget as pb
import side_telemetry as st

_IDS = fx.ids()


# ---------------------------------------------------------------- 硬閘門(真的)

def test_over_budget_means_zero_api_calls_through_production(monkeypatch):
    """**這次真的 mock API。** 不可裁的區塊超標 → gate 在生產路徑上
    擋下 → `_call_openai_responses` 呼叫數必須是 0,信落回 legacy。"""
    import morning_report as mr
    calls = []
    monkeypatch.setattr(mr, "_call_openai_responses",
                        lambda p: calls.append(p) or {})
    monkeypatch.setattr(mr, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(mr, "OPENAI_API_MODE", "responses")
    monkeypatch.setattr(mr, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(mr, "OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setattr(mr, "LLM_PRIMARY_PROMPT_PROFILE", "")
    monkeypatch.setattr(mr, "_PRIMARY_EFFORT", "xhigh")
    monkeypatch.setattr(mr, "LLM_SHADOW_PROVIDER", "")
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "")
    legacy = ("## 我的明確立場\n立場:偏多\n既有路徑寫的分析。\n"
              "## 一句話總結\n維持核心部位。")
    monkeypatch.setattr(mr, "_call_llm_text", lambda p: legacy)
    # SECTOR_HEAT 不可裁 —— 塞爆它讓 trim 救不回來
    huge = {"SECTOR_HEAT": {"blob": "原料 " * 500_000}}
    text = mr._call_llm_analysis_impl(huge, {"fair_value": 100.0},
                                      {"model1": 1000.0}, fx.news(), [], "")
    assert calls == [], "超標的 payload 還是打到了 API"
    assert text == legacy, "沒有落回 legacy"


def test_chars_after_includes_the_disclosures():
    """**P1-2 問題 A**:599,950 + 300 字缺口 = 600,250 —— 上一版在加
    disclosure 之前量,over_budget=False 錯誤放行。"""
    import json
    pk = {"market": {"HISTORY": {"rows": ["x" * 100_000]}},
          "news": [], "signal_tensions": {}}
    trimmed, rep = pb.trim(pk, limit=50_000)
    actual = len(json.dumps(trimmed, ensure_ascii=False, default=str))
    assert rep["chars_after"] == actual, (rep["chars_after"], actual)


def test_the_final_request_gate_measures_the_bundle():
    """**P1-2 問題 B**:packet 沒超不代表加上指令與 schema 之後沒超。"""
    # 第二十三輪 P1-2:**測試把實作錯誤鎖住了** —— 上一版用
    # `structured_output`(布林旗標)當 schema 鍵,測試也餵同一個錯鍵,
    # 於是兩邊一起錯、一起綠。真正的 schema 在 `response_schema`。
    ok = {"developer_instructions": "x" * 1000, "user_payload": "y" * 1000,
          "response_schema": {}}
    pb.request_gate(ok)                     # 不拋
    big = {"developer_instructions": "x" * 350_000,
           "user_payload": "y" * 300_000,
           "response_schema": {"pad": "z" * 60_000}}
    try:
        pb.request_gate(big)
        raise AssertionError("最終 request 超標仍被放行")
    except pb.PayloadBudgetExceeded:
        pass
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "morning_report.py"
           ).read_text(encoding="utf-8")
    assert "_pb.request_gate(bundle" in src, "最終 gate 沒接進生產"


# ---------------------------------------------------------------- 遙測(真的)

def test_side_costs_excludes_fallback_from_primary():
    """**P1-3**:旗子掛了而統計沒看 —— `side_costs` 照樣把 fallback 的
    成本累進 primary。這次驗的是**總和**,不是旗子。"""
    rows = [{"primary_telemetry": {"available": True,
                                   "role_is_specialized": False,
                                   "measured_cost_usd": 0.05}},
            {"primary_telemetry": {"available": True,
                                   "role_is_specialized": True,
                                   "measured_cost_usd": 0.07}},
            # 舊 row 沒旗子 —— 用 analysis_origin 事後歸類
            {"analysis_origin": "legacy_fallback_after_luna_failure",
             "primary_telemetry": {"available": True,
                                   "measured_cost_usd": 0.04}}]
    out = st.side_costs(rows)
    assert out["primary"]["cost_usd"] == 0.07, out["primary"]
    assert out["fallback_writer"]["days"] == 2
    assert out["fallback_writer"]["cost_usd"] == 0.09


# ---------------------------------------------------------------- 語意(同一條)

def test_direction_and_evidence_must_come_from_the_same_claim():
    """**P1-4 split-quantifier**:方向靠 c1、證據靠 c2 —— 沒有任何一條
    claim 真的支持這條重點,而兩個分開的檢查都綠。"""
    o = fx.valid_analysis()
    o["key_drivers"][0].update(direction="bullish", evidence_ids=["n2"],
                               claim_ids=["c1", "c2"])
    o["claim_audit"][1].update(direction="bearish", evidence_ids=["n2"])
    assert [p for p in sch.validate(o, _IDS)
            if "沒有一條**同時**同向且共享證據" in p]
    # 同一條同時滿足就過
    o2 = fx.valid_analysis()
    o2["key_drivers"][0].update(direction="bullish", evidence_ids=["n1"],
                                claim_ids=["c1"])
    assert not [p for p in sch.validate(o2, _IDS) if "同時" in p]


# ---------------------------------------------------------------- 標的(語意)

def _packet_gpu():
    return ep.build({}, {}, {},
                    [{"source_item_id": "n1",
                      "title": "Taiwan GPU demand accelerates",
                      "entities": ["台積電"], "source": "X"},
                     {"source_item_id": "n2", "title": "b",
                      "entities": ["c"], "source": "d"}],
                    [], {}, as_of="x", target_session_date="y", sanitize=str)


def test_ai_hidden_inside_taiwan_is_not_an_asset():
    """**P1-6**:`"ai" in "taiwan"` —— 裸子字串讓 `Ai` 借道 `Taiwan`。"""
    pk = _packet_gpu()
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = "Ai"
    assert [p for p in sch.validate(obj, pk) if "概念" in p or "不在這則" in p]


def test_a_fragment_of_a_real_ticker_is_not_an_asset():
    """**子字串規則要自己被測到** —— `Ai` 被概念清單先擋,測不到邊界。
    `MD` 不在概念清單、不在已知標的,而它藏在 `AMD` 裡:
    裸子字串會放行,token 邊界才擋得住。"""
    pk = ep.build({}, {}, {},
                  [{"source_item_id": "n1", "title": "AMD 資料中心營收年增 107%",
                    "entities": ["AMD"], "source": "CNBC"},
                   {"source_item_id": "n2", "title": "b", "entities": ["c"],
                    "source": "d"}],
                  [], {}, as_of="x", target_session_date="y", sanitize=str)
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = "MD"
    assert [p for p in sch.validate(obj, pk) if "不在這則" in p], \
        "MD 借道 AMD 的子字串被放行了"
    obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = "AMD"
    assert not [p for p in sch.validate(obj, pk) if "不在這則" in p]


def test_gpu_in_the_title_is_still_a_concept_not_an_asset():
    """標題就叫「GPU demand」時,「在證據裡」對產品概念永遠成立 ——
    等於沒有判準。概念詞一律不是可交易標的。"""
    pk = _packet_gpu()
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = "GPU"
    assert [p for p in sch.validate(obj, pk) if "概念" in p or "不在這則" in p]


def test_an_unrelated_chinese_company_is_not_an_asset_either():
    """**上一版對非 ASCII 一律放行** —— AMD 新聞可以掛「華碩」。"""
    pk = _packet_gpu()
    obj = fx.valid_analysis()
    obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = "華碩"
    assert [p for p in sch.validate(obj, pk) if "不在這則" in p]
    # 反向:這則新聞真的在講的中文主體要放行
    obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = "台積電"
    assert not [p for p in sch.validate(obj, pk) if "不在這則" in p]


# ---------------------------------------------------------------- 延續與分群

def test_us_does_not_hit_asus():
    """**P1-9**:裸子字串讓 `US` 命中 `ASUS` —— 美國事件的第 4 天
    接到華碩財報上。token 邊界 + 國家組整批拿掉。"""
    pk = ep.build({"EVENT_TIMELINE": [{"entity": "US", "days": 4}]},
                  {}, {}, [{"source_item_id": "n1",
                            "title": "ASUS 財報優於預期",
                            "entities": ["華碩"], "source": "X"}],
                  [], {}, as_of="x", target_session_date="y", sanitize=str)
    assert pk["news_clusters"]["clusters"][0]["continuing_days"] == 0


def test_tehran_earthquake_does_not_inherit_the_war():
    """「伊朗戰事」與「德黑蘭地震」是兩件事 —— 城市不是國家的別名,
    是不同實體。國家/首都組已整批拿掉(漏併安全,誤併危險)。"""
    assert ea.group_of("伊朗") == -1
    assert ea.group_of("德黑蘭") == -1
    pk = ep.build({"EVENT_TIMELINE": [{"entity": "伊朗", "days": 4}]},
                  {}, {}, [{"source_item_id": "n1", "title": "德黑蘭發生地震",
                            "entities": ["德黑蘭"], "source": "X"}],
                  [], {}, as_of="x", target_session_date="y", sanitize=str)
    assert pk["news_clusters"]["clusters"][0]["continuing_days"] == 0


def test_tsmc_and_taijidian_cluster_together():
    """**P2-3**:同一個主體的兩種寫法先前拆成兩群 —— 橫向重複計權。"""
    groups = nc.clusters(
        [{"source_item_id": "a", "title": "台積電熊本廠恢復產線",
          "entities": ["台積電"]},
         {"source_item_id": "b", "title": "TSMC 熊本廠恢復產線運作",
          "entities": ["TSMC"]}])
    assert [g["member_source_ids"] for g in groups] == [["a", "b"]]
