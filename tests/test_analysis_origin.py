# -*- coding: utf-8 -*-
"""**「有東西可以寄」不等於「新路徑成功」**(第十四輪 P0-1)。

## 實機證據(2026-08-03 06:43)

`run_manifest.json`:`degraded_steps: ["llm:luna_path_failed"]`、
`llm_experiment: null` —— 特化路徑沒跑成。
同一天的 `llm_shadow_ledger.json`::

    primary_model: gpt-5.6-luna   primary_effort: xhigh   primary_ok: true

兩份都沒說謊:特化確實失敗,而 Luna 這個模型確實產出了文字(跑的是
DeepSeek 的舊 prompt)。但十天後翻帳本的人會讀成「Luna xhigh 成功」——
而那正是這個實驗要回答的問題本身。

`primary_ok = bool(primary_text)` 在正常的日子裡剛好等於「特化成功」,
**只有在失敗的日子裡才分開** —— 也就是唯一需要它們分開的時候。
"""
import analysis_origin as ao
import llm_experiment as lx
import llm_shadow as ls
import morning_report as mr


# ---------------------------------------------------------------- 判定本身

def test_only_the_specialized_path_counts_as_a_luna_success():
    assert ao.counts_as_primary_success(ao.LUNA_SPECIALIZED) is True
    for other in (ao.LEGACY_PRIMARY, ao.LEGACY_AFTER_LUNA_FAILURE,
                  ao.EMERGENCY_FALLBACK, ao.UNKNOWN):
        assert ao.counts_as_primary_success(other) is False, other


def test_no_text_is_not_a_success_either():
    """反向:走對了路但沒有輸出,仍然不是成功。"""
    assert ao.counts_as_primary_success(ao.LUNA_SPECIALIZED,
                                        has_text=False) is False


def test_an_unrecognised_value_never_becomes_a_success():
    """**打錯字不得變成 Luna 成功。**

    這個實驗的結論直接建立在這個計數上;寧可少算一天樣本,
    也不要讓一個拼錯的字串被計成成功。
    """
    for junk in ("luna", "LUNA_SPECIALIZED", "luna_specialised", "", None, 7,
                 " luna_specialized "):
        assert ao.normalize(junk) in (ao.UNKNOWN, ao.LUNA_SPECIALIZED)
    assert ao.counts_as_primary_success("luna_specialised") is False
    assert ao.counts_as_primary_success("LUNA_SPECIALIZED") is False
    # 前後空白是排版,不是另一個值
    assert ao.counts_as_primary_success(" luna_specialized ") is True


# ------------------------------------------------ 生產入口:`_experiment_row`

def _row(origin, *, primary_ok=True):
    mr._set_analysis_origin(origin)
    return mr._experiment_row({"core_sha": "c", "schema_version": 1},
                              primary_ok=primary_ok, shadow_ok=True,
                              today="2026-08-03")


def test_the_authoritative_row_refuses_to_call_a_fallback_a_success():
    """**判準要在生產那條路上成立。**

    呼叫端傳進來的 `primary_ok` 是 `bool(primary_text)` —— 直接相信它,
    落回 legacy 的那天就會被記成 Luna 成功。收斂到單一判準,
    而不是要求每個呼叫端自己記得。
    """
    assert _row(ao.LUNA_SPECIALIZED)["primary_ok"] is True
    fell_back = _row(ao.LEGACY_AFTER_LUNA_FAILURE)
    assert fell_back["primary_ok"] is False, (
        "特化失敗後由 legacy 補上的那天被記成 Luna 成功")
    assert fell_back["analysis_origin"] == ao.LEGACY_AFTER_LUNA_FAILURE, (
        "記了失敗卻說不出失敗在哪一段")


def test_a_failed_fallback_day_still_counts_in_the_denominator():
    """**排除不等於消失。**

    `analysis_origin` 刻意不進同群鍵:進去的話,落回 legacy 的日子會被切成
    另一個群、從可靠度分母消失 —— 那正好是「失敗越多看起來越可靠」
    那個偏差的另一種寫法。
    """
    assert "analysis_origin" not in lx.COHORT_FIELDS
    assert "analysis_origin" in lx.PROVENANCE_FIELDS
    a = lx.cohort_key(_row(ao.LUNA_SPECIALIZED))
    b = lx.cohort_key(_row(ao.LEGACY_AFTER_LUNA_FAILURE))
    assert a == b, "落回 legacy 的那天被切進另一個同群,可靠度分母會少一天"


def test_the_reliability_rate_actually_drops_on_a_fallback_day():
    """行為判準:兩天一成一敗 → 0.5,而不是 1.0。

    只驗欄位不驗數字的話,`primary_ok` 被誰在下游覆寫回 True 也看不出來。
    """
    rows = [dict(_row(ao.LUNA_SPECIALIZED), date="2026-08-03"),
            dict(_row(ao.LEGACY_AFTER_LUNA_FAILURE), date="2026-08-04")]
    assert lx.reliability(rows)["primary_ok_rate"] == 0.5


# ------------------------------------------------ 舊帳本:不得再長得一模一樣

def test_the_legacy_ledger_row_records_the_origin_and_its_own_status():
    """舊 `llm_shadow` 帳本是**十天後最可能被翻的那一份**。

    它的 `primary_ok` 語意(有沒有產出文字)不改 —— 那個量測本身是真的。
    改的是讓它**說得出走的是哪條路**,並在正式實驗在跑時自報只供觀測。
    """
    written = {}
    out = ls.run_comparison(
        primary_model="gpt-5.6-luna", primary_text="主分析內容", prompt="p",
        shadow_model="deepseek-v4-pro", call_shadow=lambda p: "影子內容",
        today="2026-08-03", ledger_path="l.json", read_ledger=lambda p: [],
        write_ledger=lambda p, v: written.update(rows=v),
        extract_stance=lambda t: {"label": "中性", "score": 0},
        extract_summary=lambda t: t, elapsed_timer=lambda: 0.0,
        primary_effort="xhigh", shadow_effort="max", code_version="v",
        analysis_origin=ao.LEGACY_AFTER_LUNA_FAILURE,
        experiment_running=True, log=lambda m: None)
    assert out
    row = written["rows"][-1]
    assert row["analysis_origin"] == ao.LEGACY_AFTER_LUNA_FAILURE
    assert row["legacy_observability_only"] is True, (
        "正式實驗在跑時,這份帳本沒有自報只供觀測 —— "
        "兩份帳本對同一天給出相反結論時,沒有東西說得出該信哪一份")


def test_the_legacy_ledger_keeps_both_attempts_of_a_day():
    """同一天先失敗、重跑後成功 → **兩列都要在**。

    `analysis_origin` 進了 `LEDGER_KEY_FIELDS`,所以後者不會蓋掉前者。
    """
    assert "analysis_origin" in ls.LEDGER_KEY_FIELDS
    base = {"primary_model": "gpt-5.6-luna", "primary_effort": "xhigh",
            "shadow_model": "d", "shadow_effort": "max", "code_version": "v"}
    led = ls.upsert([], dict(base, analysis_origin=ao.LEGACY_AFTER_LUNA_FAILURE),
                    "2026-08-03")
    led = ls.upsert(led, dict(base, analysis_origin=ao.LUNA_SPECIALIZED),
                    "2026-08-03")
    assert len(led) == 2, "重跑成功那列把同一天的失敗蓋掉了"


# ------------------------------------------------ 出處**真的會被設**

def test_the_emergency_fallback_marks_itself():
    """**四條路走到備援,標記在函式裡而不是四個 return 上。**

    逐個 return 補一行等於留四個會被漏掉的地方,而漏掉的症狀是
    「一封沒有模型判斷的信」被記成某條路徑的成功。
    """
    mr._set_analysis_origin(ao.LUNA_SPECIALIZED)
    mr._fallback_analysis_text([], RuntimeError("x"))
    assert mr._analysis_origin() == ao.EMERGENCY_FALLBACK


# ------------------------------------------------ P2-1:`0` 不是「不知道」

def test_a_measured_zero_is_reported_as_zero():
    """**確定沒有呼叫,與沒有量到呼叫數,是兩件事。**

    原本寫 `calls or None`,於是「影子天天被預算擋掉、零修補」那種
    **真的量到的零**會被報成「沒有 telemetry」—— 一個事實被說成量不到。
    這會直接影響「這個實驗花了多少錢」的完整性判斷。
    """
    import experiment_ledger as xl
    rows = [{"date": "2026-08-03", "provider_calls": 0,
             "billable_unmeasured_calls": 0},
            {"date": "2026-08-04", "provider_calls": 0,
             "billable_unmeasured_calls": 0}]
    st = xl.attempt_stats(rows)
    assert st["provider_calls"] == 0, "量到的零被報成「不知道」"
    assert st["billable_unmeasured_calls"] == 0


def test_rows_without_telemetry_are_still_unknown():
    """反向:一列都沒帶數字時仍然是 `None` —— 不得拿列數頂替。"""
    import experiment_ledger as xl
    st = xl.attempt_stats([{"date": "2026-08-03"}, {"date": "2026-08-04"}])
    assert st["provider_calls"] is None
    assert st["billable_unmeasured_calls"] is None


def test_a_mix_sums_only_the_rows_that_measured():
    """部分有、部分沒有 → 報有的那些的總和(而不是把缺的當成零)。"""
    import experiment_ledger as xl
    st = xl.attempt_stats([{"date": "2026-08-03", "provider_calls": 3},
                           {"date": "2026-08-04"}])
    assert st["provider_calls"] == 3


def test_the_record_defaults_to_unknown_not_zero():
    """**生產入口也要成立**:`build_record` 沒拿到 telemetry 時寫 `None`。

    預設 0 的話,上面那條判準永遠看到「每一列都有數字」,
    於是「不知道」這個狀態再也表達不出來。
    """
    rec = lx.build_record(
        today="2026-08-03", experiment_id="e",
        primary={"profile": "luna", "ok": True},
        shadow={"profile": "deepseek_legacy", "ok": True},
        evidence_sha_primary="a", evidence_sha_shadow="a",
        core_sha_primary="c", core_sha_shadow="c",
        run={"run_id": "1", "run_kind": "scheduled"})
    assert rec["provider_calls"] is None
    assert rec["billable_unmeasured_calls"] is None


# ------------------------------ r1(Codex):旗標要真的讓它不參與判讀

def _obs_row(i):
    """一列「正式實驗在跑時寫下的」觀測列 —— 兩側都成功、立場一致。"""
    return {"date": f"2026-08-{i:02d}", "primary_model": "gpt-5.6-luna",
            "primary_effort": "xhigh", "shadow_model": "deepseek-v4-pro",
            "shadow_effort": "max", "code_version": "v",
            "analysis_origin": ao.LUNA_SPECIALIZED,
            "legacy_observability_only": True,
            "primary_ok": True, "shadow_ok": True, "stance_agree": True,
            "stance_flipped": False, "score_gap": 0, "body_overlap": 0.5}


def test_observability_rows_never_produce_a_verdict():
    """**旗標不是註解,要真的擋住判讀。**

    我在寫入端加了 `legacy_observability_only` 並在旁邊寫「不得參與判讀或
    配對計數」,而 `summarize()` 當時只依模型與同群過濾 —— 十筆之後這份
    帳本照樣吐出「可依品質偏好決定」,與權威帳本並列甚至相反。
    **宣稱與實作不符時,贏的是實作。**
    """
    led = [_obs_row(i) for i in range(1, 15)]     # 遠超過十筆門檻
    out = ls.summarize(led, shadow_model="deepseek-v4-pro")
    assert out["samples"] == 0, "觀測列被算進判讀樣本"
    assert out["both_ok"] == 0
    assert "樣本不足" in out.get("verdict", ""), (
        f"觀測列撐出了一個判讀:{out.get('verdict')!r}")


def test_observability_rows_are_still_visible():
    """反向:**排除不等於假裝沒發生。**

    不報的話,「樣本不足」會看起來像從來沒跑過,而實際上是跑了、
    只是不歸這份帳本判讀。
    """
    out = ls.summarize([_obs_row(1), _obs_row(2)],
                       shadow_model="deepseek-v4-pro")
    assert out["observability_only_rows"] == 2


def test_ordinary_rows_still_count():
    """反向的反向:沒有旗標的舊資料照舊參與判讀 —— 不得順手把它們也排除掉。"""
    plain = [dict(_obs_row(i), legacy_observability_only=False)
             for i in range(1, 4)]
    out = ls.summarize(plain, shadow_model="deepseek-v4-pro")
    assert out["samples"] == 3 and out["both_ok"] == 3
    assert "observability_only_rows" not in out


# -------------------------------------------- schema v2:新增的跨欄位不變式

def _v2_obj():
    import fixtures_analysis as fx
    return fx.valid_analysis()


def test_a_fact_step_without_evidence_is_rejected():
    """**沒有證據的因果步驟不得自稱 fact**(schema v2)。

    突變驗證第一輪抓到這條規則沒有測試 —— 把它從驗證器拿掉,全套照樣綠。
    它擋的正是「看起來有根據」:一條 fact→fact→fact 的鏈,讀起來像事實,
    而中間某一步其實是猜的。
    """
    import analysis_schema as sch
    obj = _v2_obj()
    obj["top_news_analysis"][0]["mechanism_steps"][1]["step_type"] = "fact"
    hits = sch.validate(obj, {"n1", "n2"})
    assert any("自稱 fact 卻沒有證據" in h for h in hits), hits
    # 反向:標成 inference 就合法
    obj["top_news_analysis"][0]["mechanism_steps"][1]["step_type"] = "inference"
    assert sch.validate(obj, {"n1", "n2"}) == []


def test_unknown_magnitude_must_say_what_is_missing():
    """`unknown` 是誠實不是逃生口 —— 選它就要說缺哪些資料。"""
    import analysis_schema as sch
    obj = _v2_obj()
    obj["top_news_analysis"][1]["why_this_magnitude"] = ""
    hits = sch.validate(obj, {"n1", "n2"})
    assert any("unknown,卻沒有說缺哪些資料" in h for h in hits), hits


def test_a_relation_must_point_at_a_real_item():
    """關係要指向今天真的存在的另一則,不能指向自己或不存在的東西。"""
    import analysis_schema as sch
    obj = _v2_obj()
    rel = obj["top_news_analysis"][0]["relates_to"][0]
    rel["other_source_item_id"] = "n_ghost"
    hits = sch.validate(obj, {"n1", "n2", "n_ghost"})
    assert any("沒有分析那一則" in h for h in hits), hits
    rel["other_source_item_id"] = "n1"          # 指向自己
    hits = sch.validate(obj, {"n1", "n2"})
    assert any("指向自己" in h for h in hits), hits
