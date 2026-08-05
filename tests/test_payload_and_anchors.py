# -*- coding: utf-8 -*-
"""**2026-08-05 實機根因 + 第二十輪 P1-2/P1-3/P2-3/P2-4。**

今天的實機紀錄把兩天前的診斷推翻了一半:

    TypeError 不見了(packet 組得起來、sha 算得出來、請求送得出去)
    error = "429 Too Many Requests"、elapsed = 2.7s
    estimated_input_tokens = 1,110,589

**新的擋路石是請求本身太大**:約 2.0 MB、估 111 萬 token,2.7 秒就被拒。
新聞側有上限,而 market 的外部文字區塊一個都沒有 ——
**每一塊都有人負責,總和沒有人負責。**

同一天的另一半:LLM 呼叫**沒有 429/5xx 退避**,而這個 repo 的
`_http_get` 早就有。一次暫時性的失敗花掉了整天的特化分析。
"""
import analysis_depth as ad
import analysis_metrics as am
import analysis_stages as ast_
import evidence_packet as ep
import evidence_registry as er
import fixtures_analysis as fx
import payload_budget as pb
import quality_metrics as qm

_IDS = fx.ids()


def _packet(**over) -> dict:
    q = dict({"QQQ": {"change_pct": 1.76},
              "MARKET_REGIME": {"label": "risk-on"}}, **over.pop("quotes", {}))
    return ep.build(q, {}, {}, fx.news(), [], {}, as_of="2026-08-05T06:00",
                    target_session_date="2026-08-05", sanitize=str, **over)


# ---------------------------------------------------------------- payload 預算

def test_an_oversized_packet_is_trimmed_before_it_is_sent():
    """**2.0 MB 的請求連進佇列的資格都沒有。**"""
    big = "公報全文 " * 60_000                       # ≈ 0.4 MB
    pk = _packet(quotes={"HISTORY": {"rows": [big]},
                         "GAZETTE_RECORDS": {"docs": [big]},
                         "TW_DAILY_INTELLIGENCE": {"items": [big]}})
    trimmed, report = pb.trim(pk, limit=200_000)
    assert report["chars_before"] > 200_000
    assert report["chars_after"] <= 200_000, report
    assert [t["block"] for t in report["trimmed"]][0] == "market.HISTORY", \
        "要從最大的背景區塊開始裁"


def test_trimming_never_touches_the_analysis_raw_material():
    """行情數字、新聞、張力**不是背景** —— 裁掉它們是改變結論,不是縮小輸入。"""
    big = "背景 " * 80_000
    pk = _packet(quotes={"HISTORY": {"rows": [big]}})
    trimmed, _ = pb.trim(pk, limit=50_000)
    assert trimmed["market"]["QQQ"]["change_pct"] == 1.76
    assert len(trimmed["news"]) == len(pk["news"])
    assert trimmed["signal_tensions"] == pk["signal_tensions"]
    for name in ("QQQ", "MARKET_REGIME"):
        assert "omitted_for_size" not in str(trimmed["market"].get(name))


def test_a_trimmed_block_leaves_a_trace():
    """**靜默截斷會讓「今天沒有公報」與「公報被裁掉了」長得一模一樣。**"""
    big = "公報 " * 80_000
    pk = _packet(quotes={"GAZETTE_RECORDS": {"docs": [big]}})
    trimmed, report = pb.trim(pk, limit=50_000)
    assert trimmed["market"]["GAZETTE_RECORDS"] == {
        "omitted_for_size": report["trimmed"][0]["chars"]}


def test_a_packet_within_budget_is_returned_untouched():
    pk = _packet()
    trimmed, report = pb.trim(pk)
    assert report["trimmed"] == [] and report["over_budget"] is False
    assert trimmed["market"] == pk["market"]


def test_still_over_budget_after_trimming_is_reported():
    """**裁完仍超標要說出來** —— 靜默放行等於明天再被 429 一次。"""
    pk = _packet(quotes={"SECTOR_HEAT": {"blob": "無法裁的原料 " * 40_000}})
    _, report = pb.trim(pk, limit=1_000)
    assert report["over_budget"] is True


def test_block_sizes_are_measured_before_anything_is_trimmed():
    """**先量再裁** —— 這個 repo 的規矩。"""
    pk = _packet(quotes={"HISTORY": {"rows": ["x" * 5000]}})
    sizes = pb.block_sizes(pk)
    assert list(sizes)[0].startswith("market.HISTORY")
    assert sizes["market.HISTORY"] > sizes["market.QQQ"]


def test_the_production_path_trims_and_records():
    """**只在測試裡裁得動等於沒有。**"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "morning_report.py"
           ).read_text(encoding="utf-8")
    body = src[src.index("def _luna_analysis"):]
    assert "_pb.trim(packet)" in body, "生產沒有裁"
    assert '"payload_budget"' in body, "裁了什麼沒有進 manifest"


# ---------------------------------------------------------------- 退避重試

def test_a_rate_limit_is_retried_not_fatal():
    """**暫時性的失敗不該花掉一整天的分析。**

    2026-08-05:429 在 2.7 秒到達,而 `_call_openai_responses` 只在
    400 時重試(為了移除選配欄位)—— 429 直接 `raise_for_status()`,
    整條特化路徑落回 legacy。
    """
    import llm_http as lh

    class _R:
        def __init__(self, code):
            self.status_code = code
            self.headers = {"Retry-After": "0"}
    seen = []

    def _fake_post(url, json=None, headers=None, timeout=None):
        seen.append(1)
        return _R(429 if len(seen) < 3 else 200)
    import types
    lh.requests = types.SimpleNamespace(post=_fake_post)
    lh.time = types.SimpleNamespace(sleep=lambda _s: None)
    r = lh.post_with_backoff("u", {}, {}, timeout=10)
    assert r.status_code == 200 and len(seen) == 3, seen


def test_retries_are_bounded():
    """晨報有時間預算 —— 不能無限等。"""
    import types

    import llm_http as lh

    class _R:
        status_code = 503
        headers = {"Retry-After": "9999"}
    waits = []
    lh.requests = types.SimpleNamespace(post=lambda *a, **k: _R())
    lh.time = types.SimpleNamespace(sleep=waits.append)
    r = lh.post_with_backoff("u", {}, {}, timeout=10)
    assert r.status_code == 503
    assert len(waits) == lh._LLM_RETRIES
    assert max(waits) <= 45.0, "尊重 Retry-After 也要有上限"


# ---------------------------------------------------------------- P1-2

def test_eight_billion_copied_from_eighty_billion_is_caught():
    """**commit 主打的反例先前剛好抓不到。**

    `_TRIVIAL` 排除 0–10 是為了忽略年份與序數,而它連**帶單位**的
    小數字一起吃掉 —— evidence 是 80 億、信裡寫 8 億時,結果不是
    unmatched,是 `checked=0`:整條檢查靜靜地沒有跑。
    """
    news = [{"source_item_id": "n9", "title": "Broadcom 獲 80 億美元訂單",
             "entities": ["Broadcom"], "source": "Reuters"}]
    pk = ep.build({}, {}, {}, news, [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    wrong = am.numeric_consistency("該訂單約 8 億美元。", pk)
    assert wrong["checked"] == 1 and wrong["unmatched"] == ["8"], wrong
    right = am.numeric_consistency("該訂單約 80 億美元。", pk)
    assert right["rate"] == 1.0
    # 沒有單位的小數字仍然忽略 —— 年份與序數本來就是噪音
    assert am.numeric_consistency("排名第 8。", pk)["checked"] == 0


# ---------------------------------------------------------------- P1-3

def test_an_anchor_must_be_a_number_from_this_very_news_item():
    """**先前只看命名空間前綴** —— 三種假錨點都通過。"""
    pk = _packet()
    reg = er.registry(pk)
    assert ast_.is_numeric_anchor("market:QQQ.change_pct", "n1", reg)
    assert not ast_.is_numeric_anchor("market:QQQ", "n1", reg), \
        "block 殼的 value 是 None"
    assert not ast_.is_numeric_anchor("market:MARKET_REGIME.label", "n1", reg), \
        "字串標籤不是量級"
    assert ast_.is_numeric_anchor("fact:n1.0", "n1", reg)
    assert not ast_.is_numeric_anchor("fact:n1.0", "n2", reg), \
        "別則新聞的數字不是這一則的錨點"


def test_an_unusable_value_is_not_an_anchor():
    """今天不同步的資料不能當今天的量級依據。"""
    pk = _packet(quotes={"US_HOLIDAY": {"detected": True}})
    reg = er.registry(pk)
    assert reg["market:QQQ.change_pct"]["usable_for_inference"] is False
    assert not ast_.is_numeric_anchor("market:QQQ.change_pct", "n1", reg)


def test_the_advisory_uses_the_same_judgment_as_the_metric():
    """**指標與驗證器要用同一個判準** —— 否則 dashboard 與加深迴圈
    對同一份輸出給出不同答案。"""
    pk = _packet()
    obj = fx.valid_analysis()
    steps = obj["top_news_analysis"][0]["mechanism_steps"]
    for st in steps:
        st["evidence_ids"] = ["market:QQQ"]      # 殼,不是數字
        st["step_type"] = "inference"
    assert [a for a in ad.depth_advisories(obj, pk) if "錨" in a]
    assert qm.fact_anchor_usage(obj, pk)["unanchored"] == 1
    steps[0]["evidence_ids"] = ["market:QQQ.change_pct"]
    assert not [a for a in ad.depth_advisories(obj, pk) if "錨" in a]
    assert qm.fact_anchor_usage(obj, pk)["anchored_market"] == 1


# ---------------------------------------------------------------- P2-3 / P2-4

def test_deduping_does_not_look_like_missing_coverage():
    """一家媒體重發十次時,packet 正確地只留一篇 —— 而 `included/available`
    先前會暴跌,讀指標的人會以為證據抓不夠。"""
    dup = [{"source_item_id": f"d{i}", "title": "台積電熊本廠恢復至地震前水準",
            "entities": ["台積電"], "source": "經濟日報"} for i in range(10)]
    pk = ep.build({}, {}, {}, dup, [], {}, as_of="x", target_session_date="y",
                  sanitize=str)
    cov = pk["coverage"]
    assert cov["raw_available"] == 10 and cov["near_duplicates_dropped"] == 9
    assert cov["available"] == 1 and cov["included"] == 1
    assert cov["rate"] == 1.0, cov


def test_the_metric_and_the_validator_agree_on_generic_assets():
    """**「台灣市場」先前被 validator 擋、被指標放行。**"""
    import analysis_schema as sch
    for aid in ("台灣市場", "半導體產業", "相關電子族群"):
        obj = fx.valid_analysis()
        obj["top_news_analysis"][0]["affected_assets"][0]["asset_id"] = aid
        blocked = bool([p for p in sch.validate(obj, _IDS) if "泛稱" in p])
        counted = qm.asset_breakdown_quality(obj)["generic"] >= 1
        assert blocked and counted, f"{aid}:validator={blocked} metric={counted}"
