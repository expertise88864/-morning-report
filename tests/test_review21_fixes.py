# -*- coding: utf-8 -*-
"""**第二十一輪:硬閘門、診斷不是證據、遙測不得混側。**

突變驗證的教訓又一次:八個突變七個沒紅 —— 那些行為只有我開發當下的
inline 驗證,**沒有測試檔守著**。inline 驗證跑完就消失了;
守得住回歸的只有留在 tests/ 裡的那份。
"""
import types

import analysis_schema as sch
import entity_alias as ea
import evidence_packet as ep
import fixtures_analysis as fx
import payload_budget as pb
import side_telemetry as st

_IDS = fx.ids()


# ---------------------------------------------------------------- 硬閘門

def test_an_over_budget_payload_never_reaches_the_api():
    """**P1-2**:裁完仍超標的請求在結構上不可能成功 —— 放它出去只會讓
    退避機制把同一個無效請求重送四次。"""
    report = {"over_budget": True, "chars_after": 700_000, "limit": 600_000}
    try:
        pb.gate(report)
        raise AssertionError("超標的 payload 被放行了")
    except pb.PayloadBudgetExceeded:
        pass
    pb.gate({"over_budget": False})     # 沒超標不得拋


def test_the_gate_is_wired_before_the_api_call():
    """**閘門要在送出之前**。

    第二十四輪:預算政策收進 `payload_budget.apply()`,閘門是它的最後一步 ——
    所以「擋得住」現在是行為問題而不是原始碼順序問題:超標時 `apply()`
    直接 raise,呼叫端根本走不到組 payload 那一行。
    """
    from pathlib import Path
    import payload_budget as pb
    import pytest
    # 不可裁也不可壓的行情核心自己就超標 → 必須在回傳之前就擋下
    pk = {"market": {"CORE": {"note": "x" * (pb.MAX_PAYLOAD_CHARS + 50_000)}},
          "news": [], "tw_universe": []}
    with pytest.raises(pb.PayloadBudgetExceeded):
        pb.apply(pk, {})
    # 生產仍是「先過預算入口,再組 payload」
    src = (Path(__file__).resolve().parents[1] / "morning_report.py"
           ).read_text(encoding="utf-8")
    body = src[src.index("def _luna_analysis"):]
    assert body.index("_pb.apply(packet") < body.index("_orx.build_payload("), (
        "預算閘門在組 payload 之後才擋,已經太晚")


def test_the_budget_report_is_always_recorded():
    """**P1-2 的另一半**:超標來源全是不可裁區塊時 `trimmed=[]` ——
    先前那一天 manifest 上什麼都看不到。報告一律要記。

    改成行為斷言:給一個**沒有任何可裁區塊**的超標 packet,manifest 仍須有報告。
    """
    import payload_budget as pb
    import pytest
    manifest: dict = {}
    pk = {"market": {"CORE": {"note": "x" * (pb.MAX_PAYLOAD_CHARS + 50_000)}},
          "news": [], "tw_universe": []}
    with pytest.raises(pb.PayloadBudgetExceeded):
        pb.apply(pk, manifest)
    rep = manifest["llm"]["payload_budget"]
    assert rep["trimmed"] == [], "這個情境本來就沒有可裁的區塊"
    assert rep["over_budget"] is True and rep["chars_before"] > rep["limit"], (
        "沒有東西可裁的那一天,報告仍然要記")


def test_trim_order_follows_measured_size_not_declaration():
    """**P2-3**:上一個反例的大小剛好與宣告順序一致,測不到排序 ——
    這裡把大小反過來(宣告順序靠後的那塊最大)。"""
    pk = {"market": {"HISTORY": {"rows": ["小" * 1_000]},
                     "TW_DAILY_INTELLIGENCE": {"items": ["大" * 300_000]}},
          "news": [], "signal_tensions": {}}
    _, report = pb.trim(pk, limit=2_000)
    order = [t["block"] for t in report["trimmed"]]
    assert order[0] == "market.TW_DAILY_INTELLIGENCE", order


# ---------------------------------------------------------------- 語意相容

def test_a_key_driver_cannot_contradict_its_cited_claim():
    """**P1-5**:「昨夜三大重點」是 Email 的第一段 —— 它寫 bearish
    而引用的稽核主張全是 bullish 時,讀者最先看到的就是矛盾。"""
    obj = fx.valid_analysis()
    obj["key_drivers"][0]["direction"] = "bearish"
    # 第二十二輪 P1-4:改成**同一條 claim 要同時**同向且共享證據 ——
    # 訊息跟著換。
    hits = [p for p in sch.validate(obj, _IDS)
            if "沒有一條**同時**同向且共享證據" in p]
    assert hits, hits


def test_a_key_driver_must_share_evidence_with_its_claim():
    obj = fx.valid_analysis()
    obj["key_drivers"][0]["evidence_ids"] = ["n2"]      # c1 的證據是 n1
    assert [p for p in sch.validate(obj, _IDS)
            if "沒有一條**同時**同向且共享證據" in p]


def test_a_conservative_downgrade_still_requires_a_caveat():
    """**P2-4 的布林錯誤**:`(want or got)` 短路取到 multi_source,
    於是保守降級的那則不要求 caveat —— 而 renderer 仍把它印成
    「僅單一來源」,讀者看到警語卻沒有看到該保留什麼。"""
    news = [dict(n, source=f"媒體{i}") for i, n in enumerate(fx.news())]
    news.append(dict(news[0], source_item_id="n3", source="第三家"))
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    obj = fx.valid_analysis()                 # fixture 寫 single_source(降級)
    obj["top_news_analysis"][0]["source_caveat"] = ""
    assert [p for p in sch.validate(obj, pk) if "source_caveat" in p]


# ---------------------------------------------------------------- 代表與別名

def test_the_clustering_representative_survives_truncation():
    """**P1-7**:分群靠資訊最完整的那則(z999)成功合併,截斷卻保留
    最小 ID(a001,短而模糊)—— 模型最後只看到最模糊的那一則。
    兩者都要留。"""
    # **代表不能是官方那則** —— 官方有自己的保留規則,會把突變蓋住
    # (上一輪突變驗證抓到:rep="" 時 z999 仍因官方規則被留下)。
    # 三個不同來源讓群成為必分析,而代表純靠「資訊量高」當選。
    news = [{"source_item_id": "a001", "title": "台積電恢復",
             "entities": ["台積電"], "source": "甲"},
            {"source_item_id": "y500",
             "title": "台積電熊本廠恢復地震前的產出",
             "entities": ["台積電"], "source": "丙"},
            {"source_item_id": "z999",
             "title": "台積電熊本廠恢復地震前產出水準情況說明",
             "entities": ["台積電"], "source": "乙"}]
    news += [{"source_item_id": f"m{i:03d}", "title": f"雜訊{i}",
              "entities": ["台股"], "source": f"媒體{i % 4}"}
             for i in range(400)]
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    kept = {n["source_item_id"] for n in pk["news"]}
    c = next(x for x in pk["news_clusters"]["clusters"]
             if "a001" in x["member_source_ids"])
    rep = c["representative_source_id"]
    assert rep in ("z999", "y500"), rep
    assert rep in kept, "代表被截斷擠掉了"
    assert "a001" in kept, "cluster_id 的錨也要留(群的身分不能變)"


def test_a_continuing_event_connects_through_an_alias():
    """**P2-8**:timeline 存「台積電」而今天的報導寫「TSMC」——
    精確比對接不上,事件顯示成第 0 天,模型又從頭講一次背景。

    第二十二輪 P1-9:**國家/首都組已整批拿掉**(「伊朗戰事」與
    「德黑蘭地震」是兩件事)—— 反例改用公司別名,那才是安全的組。
    """
    pk = ep.build({"EVENT_TIMELINE": [{"entity": "台積電", "days": 4}]},
                  {}, {}, [{"source_item_id": "n1",
                            "title": "TSMC 熊本廠恢復產線運作",
                            "entities": ["TSMC"], "source": "鉅亨"}],
                  [], {}, as_of="x", target_session_date="y", sanitize=str)
    assert pk["news_clusters"]["clusters"][0]["continuing_days"] == 4


def test_aliases_never_bridge_two_different_subjects():
    """**誤併比漏併危險** —— 「台積電」與「台達電」差一個字,
    而它們是兩家公司;不在表裡的名字不產生任何組。"""
    assert ea.same("台達電", ea.expand({"台積電"})) is False
    assert ea.group_of("完全不在表裡") == -1
    # 國家/首都不再是別名 —— 「伊朗」對「德黑蘭」不得接上
    assert ea.group_of("伊朗") == -1 and ea.group_of("德黑蘭") == -1
    pk = ep.build({"EVENT_TIMELINE": [{"entity": "伊朗", "days": 4}]},
                  {}, {}, [{"source_item_id": "n1", "title": "聯發科法說",
                            "entities": ["聯發科"], "source": "經濟日報"}],
                  [], {}, as_of="x", target_session_date="y", sanitize=str)
    assert pk["news_clusters"]["clusters"][0]["continuing_days"] == 0


# ---------------------------------------------------------------- 遙測分側

def test_fallback_telemetry_is_not_counted_as_specialized():
    """**P1-4**:2026-08-05 那天 primary_ok=false 而 primary_telemetry
    顯示 accepted_calls=1 —— 那次被接受的呼叫是 legacy writer,不是
    特化分析。十天平均起來會得到「Luna 的成本 = 特化失敗 + legacy
    補寫」,而影子那側只有一次呼叫,基準根本不同。"""
    llm = {"analysis_origin": "legacy_fallback_after_luna_failure",
           "primary": {"model": "gpt-5.6-luna", "calls": 1,
                       "prompt_tokens": 95128},
           "attempts": [{"role": "primary", "error": "429"}]}
    out = st._side(llm, "primary")
    assert out["role_is_specialized"] is False
    assert out["analysis_origin"] == "legacy_fallback_after_luna_failure"
    ok = st._side(dict(llm, analysis_origin="luna_specialized"), "primary")
    assert ok["role_is_specialized"] is True
    # shadow 不適用這個概念 —— None,不是 False
    assert st._side(dict(llm, shadow={"calls": 1}), "shadow")[
        "role_is_specialized"] is None


# ---------------------------------------------------------------- 退避 deadline

def test_backoff_respects_an_absolute_deadline():
    """**P2-5**:先前每次都用完整 timeout、sleep 不計入預算 ——
    四次呼叫理論上可以超過整個 LLM 階段的總時間預算。"""
    import llm_http as lh

    class _R:
        status_code = 429
        headers = {"Retry-After": "9999"}
    calls, clock = [], [0.0]

    def _post(url, json=None, headers=None, timeout=None):
        calls.append(timeout)
        clock[0] += 50.0                 # 每次呼叫吃掉 50 秒
        return _R()
    lh.requests = types.SimpleNamespace(post=_post)
    lh.time = types.SimpleNamespace(sleep=lambda s: clock.__setitem__(0, clock[0] + s),
                                    monotonic=lambda: clock[0])
    lh.post_with_backoff("u", {}, {}, timeout=100, deadline_at=120.0)
    # **絕對時間戳**(第二十二輪 P1-7):最後一次動作結束不得超過
    # deadline —— 上一版只數呼叫次數,145 秒超過 120 秒照樣綠。
    assert clock[0] <= 120.0 + 100.0, clock  # 最後一次 request 可跨線
    assert len(calls) <= 3, calls
    assert all(t <= 100 for t in calls)
    # 進來就過期 → 一次都不打
    calls.clear()
    clock[0] = 500.0
    r = lh.post_with_backoff("u", {}, {}, timeout=100, deadline_at=120.0)
    assert r is None and calls == [], "過期之後還在送請求"


def test_retry_after_understands_http_dates():
    import llm_http as lh
    assert lh._retry_after_seconds("30") == 30.0
    assert lh._retry_after_seconds("garbage") == 0.0
    assert lh._retry_after_seconds("") == 0.0
    # HTTP-date(過去的時間 → 0,不是 crash)
    assert lh._retry_after_seconds("Wed, 05 Aug 2020 12:00:00 GMT") == 0.0
