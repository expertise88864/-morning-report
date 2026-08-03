# -*- coding: utf-8 -*-
"""**分側成本與延遲**(第十四輪 P1-4)。

## 這條 finding 的實測依據

2026-08-03 使用者問「今天跑 Luna 的成本是多少」,答案在 `run_manifest.json`
裡查得到:Luna 主分析 $0.062598 + 抽取器 $0.004347、DeepSeek 影子 $0.047866。
**隔天早上那份 manifest 就被下一班覆蓋了。** 而實驗帳本裡只有整班總和,
十配對達標時比得出「這一班多少錢」,比不出兩套系統各自的成本效益 ——
那正是「要不要永久換成 Luna」要回答的問題。

判準刻意用**真實 manifest 的形狀**(`tests/fixtures/` 沒有的話就用這裡的
常數,但欄位名一律照生產)。本 repo 反覆栽在「測試用自己捏的形狀」。
"""
import json

import llm_experiment as lx
import side_telemetry as st

#: 2026-08-03 06:43 那一班的真實數字(從 `state/run_manifest.json` 抄的)。
_REAL = {
    "primary": {"provider": "openai", "model": "gpt-5.6-luna", "calls": 1,
                "prompt_tokens": 97278, "cached_tokens": 0,
                "cache_write_tokens": 97275, "completion_tokens": 31899,
                "reasoning_tokens": 26894, "estimated_cost_usd": 0.062598,
                "pricing_tier": "standard", "elapsed_seconds": 204.5},
    "shadow": {"provider": "deepseek", "model": "deepseek-v4-pro", "calls": 1,
               "prompt_tokens": 88546, "completion_tokens": 10745,
               "reasoning_tokens": 6308, "estimated_cost_usd": 0.047866,
               "pricing_tier": "standard", "elapsed_seconds": 138.7},
    "extractor": {"provider": "openai", "model": "gpt-5.6-luna", "calls": 1,
                  "prompt_tokens": 12456, "completion_tokens": 1028,
                  "estimated_cost_usd": 0.004347, "elapsed_seconds": 7.3},
}


def _row(manifest_llm):
    return {f"{k}_telemetry": v
            for k, v in st.from_manifest(manifest_llm).items()}


# ---------------------------------------------------------------- 逐側擷取

def test_each_side_keeps_its_own_cost_and_latency():
    """**整班總和回答不了「哪一側比較貴」。**"""
    out = st.from_manifest(_REAL)
    assert out["primary"]["measured_cost_usd"] == 0.062598
    assert out["shadow"]["measured_cost_usd"] == 0.047866
    assert out["primary"]["elapsed_seconds"] == 204.5
    assert out["shadow"]["elapsed_seconds"] == 138.7
    assert out["primary"]["reasoning_tokens"] == 26894, (
        "推理 token 沒留下來 —— 那是這次成本差異的唯一解釋")


def test_the_extractor_is_shared_and_not_attributed_to_either_side():
    """**抽取器不屬於任何一側。**

    它在 EvidencePacket 組裝**之前**跑一次,兩側吃同一份產物。
    按比例拆給兩邊是編造(拆法本身就是結論),所以只標 shared、分開報。
    """
    out = st.from_manifest(_REAL)
    assert out["extractor"]["attribution"] == "shared"
    assert out["primary"]["attribution"] == "primary"
    totals = st.side_costs([_row(_REAL)])
    assert totals["extractor"]["attribution"] == "shared"
    # 兩側的數字裡都**不含**抽取器
    assert totals["primary"]["cost_usd"] == 0.062598
    assert totals["shadow"]["cost_usd"] == 0.047866


def test_a_missing_side_says_it_does_not_know():
    """影子被預算擋掉的那天**不得記成「花了 0 元」**。

    記成 0 的話,十天平均會把那天當成一次免費的成功 ——
    方向又是偏向「這個實驗很便宜」。
    """
    out = st.from_manifest({"primary": _REAL["primary"]})
    assert out["shadow"]["available"] is None
    assert "basis" in out["shadow"], "說不知道也要說得出為什麼"
    assert "measured_cost_usd" not in out["shadow"]
    assert st.from_manifest(None)["primary"]["available"] is None


def test_rejected_attempts_are_counted_and_charged():
    """**被拒絕的呼叫也送出去了,也計費。** 它們是「修補」的直接量測。"""
    llm = dict(_REAL, attempts=[
        {"role": "primary", "model": "gpt-5.6-luna",
         "estimated_cost_usd": 0.01, "error": "schema 不合"},
        {"role": "primary", "model": "gpt-5.6-luna", "billable_unmeasured": True},
        {"role": "shadow", "model": "deepseek-v4-pro", "estimated_cost_usd": 0.02},
    ])
    out = st.from_manifest(llm)
    assert out["primary"]["rejected_calls"] == 2
    assert out["primary"]["failed_attempt_cost_usd"] == 0.01
    assert out["primary"]["billable_unmeasured_calls"] == 1, (
        "計費卻量不到的那次沒被數到 —— 帳單會低估")
    assert out["shadow"]["rejected_calls"] == 1


def test_a_clean_run_reports_zero_failures_not_unknown():
    """**沒有失敗嘗試 → 失敗花費是 `0`,不是「不知道」。**

    這是同一批 P2-1 的規約:`rejected_calls` 出自同一份資料,
    說得出那個零是真的零。第一版寫成 `sum(...) or (0.0 if tried else None)`,
    又把已知的零報成不知道。
    """
    out = st.from_manifest(_REAL)
    assert out["primary"]["rejected_calls"] == 0
    assert out["primary"]["failed_attempt_cost_usd"] == 0.0
    assert out["primary"]["billable_unmeasured_calls"] == 0


def test_a_zero_call_count_is_not_replaced_by_one():
    """`calls: 0` 不得被 `or 1` 換掉 —— 那是被禁掉的那個形狀。"""
    out = st.from_manifest({"primary": dict(_REAL["primary"], calls=0)})
    assert out["primary"]["accepted_calls"] == 0
    # 反向:**舊紀錄沒有 `calls` 欄位**時才退回 1
    no_calls = {k: v for k, v in _REAL["primary"].items() if k != "calls"}
    assert st.from_manifest({"primary": no_calls})["primary"]["accepted_calls"] == 1


def test_a_bool_is_never_a_number():
    """`True` 是 1 —— 不擋的話會被加進帳單。"""
    out = st.from_manifest({"primary": dict(_REAL["primary"],
                                            estimated_cost_usd=True)})
    assert out["primary"]["measured_cost_usd"] is None


# ---------------------------------------------------------------- 跨日彙總

def test_costs_span_every_attempt_not_the_daily_sample():
    """**配對一天只算一次,帳單不行。** 重跑那筆錢真的花掉了。"""
    rows = [_row(_REAL), _row(_REAL), _row(_REAL)]
    out = st.side_costs(rows)
    assert out["rows_seen"] == 3 and out["primary"]["days_measured"] == 3
    assert out["primary"]["cost_usd"] == round(0.062598 * 3, 6)


def test_rows_without_telemetry_do_not_become_zeros():
    """沒有 telemetry 的列不得被當成「那天花了 0」。"""
    out = st.side_costs([{"date": "2026-08-03"}, {"date": "2026-08-04"}])
    assert out["rows_seen"] == 2
    assert out["primary"]["days_measured"] == 0
    assert out["primary"]["cost_usd"] is None
    assert out["primary"]["latency_samples"] == 0


def test_partial_coverage_is_visible():
    """量到的天數與總列數差很多時,總和不能被當成「這幾天的帳單」。"""
    out = st.side_costs([_row(_REAL), {"date": "x"}, {"date": "y"}])
    assert out["rows_seen"] == 3 and out["primary"]["days_measured"] == 1


def test_latency_reports_median_and_max_but_never_p95():
    """**十個樣本算不出 p95** —— 那個數字會完全由單一極端值決定,
    而它看起來像統計量。給三個誠實的數字,讓人自己判斷夠不夠。
    """
    rows = [_row({"primary": dict(_REAL["primary"], elapsed_seconds=s)})
            for s in (100.0, 200.0, 900.0)]
    out = st.side_costs(rows)["primary"]
    assert out["latency_median_seconds"] == 200.0
    assert out["latency_max_seconds"] == 900.0
    assert out["latency_samples"] == 3
    assert not any("p95" in k or "p50" in k for k in out), (
        f"出現了樣本數撐不起來的分位數:{sorted(out)}")


# ---------------------------------------------------------------- 生產入口

def test_the_ledger_row_actually_carries_the_three_sides():
    """**經由 `build_record` 也要成立。**

    直接呼叫 `from_manifest()` 測得很漂亮、而生產那條沒接上去 ——
    那是本 repo 反覆栽的地方。
    """
    rec = lx.build_record(
        today="2026-08-03", experiment_id="e",
        primary={"profile": "luna", "ok": True},
        shadow={"profile": "deepseek_legacy", "ok": True},
        evidence_sha_primary="a", evidence_sha_shadow="a",
        core_sha_primary="c", core_sha_shadow="c",
        telemetry=st.from_manifest(_REAL))
    for f in ("primary_telemetry", "shadow_telemetry", "extractor_telemetry"):
        assert f in rec, f
        assert f in lx.PROVENANCE_FIELDS
        assert f not in lx.COHORT_FIELDS, "成本不得決定樣本能不能相加"
    assert rec["primary_telemetry"]["measured_cost_usd"] == 0.062598
    # 帳本是 JSON 檔:整列必須序列化得出來
    json.dumps(rec, ensure_ascii=False)


def test_record_day_reports_side_costs(tmp_path):
    """**十配對進度那份報表要看得到分側成本**,否則它只存在於帳本檔裡。"""
    store = {"led": []}
    rec = lx.build_record(
        today="2026-08-03", experiment_id="e",
        primary={"profile": "luna", "ok": True},
        shadow={"profile": "deepseek_legacy", "ok": True},
        evidence_sha_primary="a", evidence_sha_shadow="a",
        core_sha_primary="c", core_sha_shadow="c",
        run={"run_id": "1", "run_kind": "scheduled"},
        telemetry=st.from_manifest(_REAL))
    prog = lx.record_day(record=rec, today="2026-08-03",
                         ledger_path=tmp_path / "l.json",
                         read_ledger=lambda p: store["led"],
                         write_ledger=lambda p, v: store.update(led=v),
                         target=10, log=lambda m: None)
    sc = prog["side_costs"]
    assert sc["primary"]["cost_usd"] == 0.062598
    assert sc["shadow"]["cost_usd"] == 0.047866
    assert sc["extractor"]["attribution"] == "shared"


def test_the_production_row_reads_the_live_manifest():
    """**`_experiment_row` 真的把當班 manifest 的分側數字帶進去。**

    第一版沒有這條:把生產呼叫端的 `telemetry=` 整個拿掉,上面那些測試
    照樣全綠 —— 因為它們都自己傳 telemetry 進 `build_record`。
    「底層測得很漂亮、生產那條沒接上去」是本 repo 反覆栽的地方,
    而這一條是唯一會紅的那個判準。
    """
    import morning_report as mr
    saved = dict(mr._RUN_MANIFEST.get("llm") or {})
    try:
        mr._RUN_MANIFEST["llm"] = dict(_REAL)
        row = mr._experiment_row({"core_sha": "c", "schema_version": 1},
                                 primary_ok=True, shadow_ok=True,
                                 today="2026-08-03")
    finally:
        mr._RUN_MANIFEST["llm"] = saved
    assert row["primary_telemetry"]["measured_cost_usd"] == 0.062598, (
        "帳本那一列沒有帶到當班 manifest 的主分析成本 —— "
        "manifest 明早就被覆蓋,這個數字之後再也查不到")
    assert row["shadow_telemetry"]["measured_cost_usd"] == 0.047866
    assert row["extractor_telemetry"]["attribution"] == "shared"
