# -*- coding: utf-8 -*-
"""**人工重跑不得把排程的失敗洗掉**(第十二輪 P1-4)。

## 實測復現的缺陷

    06:00 排程:Luna 逾時 → 落回 legacy → 記一列失敗
    09:00 人工改設定後重跑:兩邊都成功 → **蓋掉上面那列**
    → primary_ok_rate 從 0.0 變成 1.0

那次逾時、那次計費、那次落回,全部從帳本消失。而這個實驗要回答的是
**「排程跑起來可不可靠、要花多少錢」**;人工重跑答的是另一個問題
(「設定修好之後跑不跑得動」)。兩者混在一起,前者永遠看起來很漂亮。

最糟的是方向:**失敗越多、人越會去重跑,於是越不可靠的日子越容易被洗白。**
這種偏差不會有錯誤訊息,只會讓十天後的結論偏向「Luna 很穩」。

## 這個檔盯什麼

  1. 追加不覆蓋(原始紀錄補不回來);
  2. 一天的代表樣本**排程優先**;
  3. 成本橫跨所有嘗試 —— 配對可以只算一次,帳單不行;
  4. 而且以上都要**經由 `record_day`**(生產唯一入口)成立。

第 4 點是本 repo 反覆栽的地方:直接呼叫底層函式測得很漂亮,而生產那條
根本沒接上去。
"""
import experiment_ledger as xl
import llm_experiment as lx


def _rec(day, *, ok, run_id, attempt=1, kind=xl.SCHEDULED, reason="",
         at=""):
    return lx.build_record(
        today=day, experiment_id="e",
        primary={"profile": "luna", "ok": ok},
        shadow={"profile": "deepseek_legacy", "ok": ok},
        evidence_sha_primary="a", evidence_sha_shadow="a",
        core_sha_primary="c", core_sha_shadow="c", failure_reason=reason,
        review={"review_ok": ok, "review_expires": "2099-01-01"},
        run={"run_id": run_id, "run_attempt": attempt, "run_kind": kind,
             "started_at": at or f"{day}T06:00:00+08:00"})


# ------------------------------------------------------------ 執行身分

def test_the_run_kind_comes_from_the_actual_event():
    """排程與人工要分得出來,否則可靠度沒有意義。"""
    sched = xl.run_identity({"GITHUB_RUN_ID": "77", "GITHUB_RUN_ATTEMPT": "2",
                             "GITHUB_EVENT_NAME": "schedule",
                             "RUN_STARTED_AT": "2026-08-03T06:00:00+00:00"})
    assert sched["run_id"] == "77" and sched["run_attempt"] == 2
    assert sched["run_kind"] == xl.SCHEDULED
    assert sched["started_at"] == "2026-08-03T06:00:00+00:00"
    manual = xl.run_identity({"GITHUB_RUN_ID": "78",
                              "GITHUB_EVENT_NAME": "workflow_dispatch"})
    assert manual["run_kind"] == xl.MANUAL and manual["run_attempt"] == 1


def test_an_unknown_event_is_not_counted_as_scheduled():
    """**寧可低估排程樣本,不可高估。**

    認不得的事件若當成排程,可靠度的分母就混進了不是排程的東西 ——
    而那個數字正是要拿來做永久切換決定的。
    """
    assert xl.run_identity({"GITHUB_RUN_ID": "1",
                            "GITHUB_EVENT_NAME": "push"})["run_kind"] == xl.MANUAL


def test_a_local_run_is_neither():
    """本機跑不該進任何一邊的分母。"""
    assert xl.run_identity({})["run_kind"] == xl.LOCAL


# ------------------------------------------------------------ 代表樣本

def test_a_manual_rerun_does_not_replace_the_scheduled_failure():
    """**本檔最重要的一條。**

    人工那筆刻意給**更晚的 run_id 與更高的 attempt** —— 也就是說在
    「最後一筆贏」或「attempt 高的贏」之下它都會勝出,只有排程優先能救。
    第一版兩筆的 rank 相同,結果是靠「先來的贏」這個排序巧合過關:
    把優先序整個拿掉,測試照樣綠(突變當場抓到)。
    **靠巧合成立的測試,守不住它宣稱在守的規則。**
    """
    led = xl.append([], _rec("2026-08-03", ok=False, run_id="1",
                             reason="luna_failed:timeout"))
    led = xl.append(led, _rec("2026-08-03", ok=True, run_id="9", attempt=3,
                              kind=xl.MANUAL))
    day = xl.canonical(led)
    assert len(day) == 1, "一天只該有一個代表樣本"
    assert day[0]["primary_ok"] is False, (
        "人工重跑成了那天的代表樣本 —— 排程的失敗被洗掉,"
        "而越不可靠的日子越容易被重跑")
    assert "timeout" in day[0]["failure_reason"]


def test_a_manual_run_represents_a_day_with_no_scheduled_run():
    """反向:那天**沒有**排程紀錄時,人工那筆總比沒有好。"""
    led = xl.append([], _rec("2026-08-04", ok=True, run_id="9", kind=xl.MANUAL))
    day = xl.canonical(led)
    assert len(day) == 1 and day[0]["run_kind"] == xl.MANUAL


def test_a_github_retry_of_the_same_scheduled_run_wins():
    """同為排程時取最後一次 attempt —— GitHub 自動重試是同一次排程的續跑。"""
    led = xl.append([], _rec("2026-08-05", ok=False, run_id="5", attempt=1))
    led = xl.append(led, _rec("2026-08-05", ok=True, run_id="5", attempt=2))
    day = xl.canonical(led)
    assert len(day) == 1 and day[0]["primary_ok"] is True
    assert len(led) == 2, "兩次 attempt 的原始紀錄都要留著"


# ------------------------------------------------------------ 嘗試層級

def test_the_attempt_view_shows_what_the_daily_view_hides():
    """代表樣本看不到的那一面要另外報得出來。"""
    led = xl.append([], _rec("2026-08-03", ok=False, run_id="1",
                             at="2026-08-03T06:00:00+08:00"))
    led = xl.append(led, _rec("2026-08-03", ok=True, run_id="2", kind=xl.MANUAL,
                              at="2026-08-03T09:00:00+08:00"))
    led = xl.append(led, _rec("2026-08-04", ok=True, run_id="3"))
    st = xl.attempt_stats(led)
    assert st["recorded_runs"] == 3 and st["days_seen"] == 2
    assert st["manual_reruns_after_a_scheduled_run"] == 1
    # 第十三輪 P1-4:**列數不是呼叫數。** 沒有逐列的呼叫數就回 None,
    # 不要拿列數頂替 —— 那是編造,而且方向偏向「這個實驗很便宜」。
    assert st["provider_calls"] is None
    assert st["scheduled_attempts"] == 2


def test_a_manual_run_alone_is_not_counted_as_a_rerun():
    """沒有排程紀錄的人工執行不是「重跑」,別把它算進偏差指標。"""
    led = xl.append([], _rec("2026-08-04", ok=True, run_id="9", kind=xl.MANUAL))
    assert xl.attempt_stats(led)["manual_reruns_after_a_scheduled_run"] == 0


# ------------------------------------------------------------ 生產入口

def test_record_day_uses_the_canonical_sample(tmp_path):
    """**經由 `record_day` 也要成立。**

    直接呼叫 `canonical()` 測得很漂亮、而生產那條沒接上去 ——
    那是本 repo 反覆栽的地方,所以判準走真正的入口。
    """
    store = {"led": xl.append([], _rec("2026-08-03", ok=False, run_id="1",
                                       reason="luna_failed:timeout"))}
    prog = lx.record_day(
        record=_rec("2026-08-03", ok=True, run_id="9", attempt=3,
                    kind=xl.MANUAL, at="2026-08-03T09:00:00+08:00"),
        today="2026-08-03", ledger_path=tmp_path / "l.json",
        read_ledger=lambda p: store["led"],
        write_ledger=lambda p, v: store.update(led=v),
        target=10, log=lambda m: None)
    assert prog["reliability"]["primary_ok_rate"] == 0.0, (
        "經由 record_day 時人工重跑仍然洗掉了排程的失敗")
    assert prog["attempts"]["manual_reruns_after_a_scheduled_run"] == 1
    assert len(store["led"]) == 2, "原始紀錄被覆蓋了"


def test_record_day_keeps_both_attempts_on_disk(tmp_path):
    """寫回去的是**全部嘗試**,不是收斂後的結果。"""
    store = {"led": []}
    for i, (ok, kind) in enumerate([(False, xl.SCHEDULED), (True, xl.MANUAL)]):
        lx.record_day(record=_rec("2026-08-03", ok=ok, run_id=str(i), kind=kind),
                      today="2026-08-03", ledger_path=tmp_path / "l.json",
                      read_ledger=lambda p: store["led"],
                      write_ledger=lambda p, v: store.update(led=v),
                      target=10, log=lambda m: None)
    assert len(store["led"]) == 2
    assert {r["run_kind"] for r in store["led"]} == {xl.SCHEDULED, xl.MANUAL}


def test_the_overwrite_gun_is_gone():
    """**把覆蓋槍留在桌上就是讓缺陷復發。**

    `upsert()` 依 `(date, experiment_id)` 覆蓋 —— 它在生產已經沒有呼叫端,
    留著只是等下一個人接回去。
    """
    assert not hasattr(lx, "upsert"), \
        "llm_experiment.upsert 又出現了 —— 它會覆蓋掉同日的原始紀錄"


# ------------------------------------------------ r1(Codex):宣稱要對得上

def test_a_local_run_never_becomes_the_daily_sample():
    """**本機跑不得推進十配對,也不得抬高可靠度**(r1 Codex,P1)。

    `run_identity` 的說明寫著「本機跑不該進任何一邊的分母」,而
    `canonical()` 原本在沒有更高階紀錄時照樣把它留下來 —— 於是我在本機
    測一次就可能推進門檻。**不符的那一邊是我自己寫下的合約。**
    """
    led = xl.append([], _rec("2026-08-06", ok=True, run_id="", kind=xl.LOCAL))
    assert xl.canonical(led) == [], "本機那筆成了那天的代表樣本"
    # 但花費仍然看得見 —— 排除不等於假裝沒發生
    assert xl.attempt_stats(led)["recorded_runs"] == 1


def test_a_local_run_does_not_shadow_a_scheduled_one():
    """反向:本機那筆不該把同一天的排程紀錄擠掉。"""
    led = xl.append([], _rec("2026-08-06", ok=True, run_id="", kind=xl.LOCAL))
    led = xl.append(led, _rec("2026-08-06", ok=False, run_id="7"))
    day = xl.canonical(led)
    assert len(day) == 1 and day[0]["run_kind"] == xl.SCHEDULED
    assert day[0]["primary_ok"] is False


def test_a_manual_run_before_the_scheduled_one_is_not_a_rerun():
    """**先手動、後排程不是重跑**(r1 Codex,P2)。

    這個指標存在的理由是抓「**失敗之後才去重跑**」那個偏差;
    把時序相反的也算進去,等於在製造它要偵測的假象。
    """
    led = xl.append([], _rec("2026-08-07", ok=True, run_id="1", kind=xl.MANUAL,
                             at="2026-08-07T04:00:00+08:00"))
    led = xl.append(led, _rec("2026-08-07", ok=True, run_id="2",
                              at="2026-08-07T06:00:00+08:00"))
    st = xl.attempt_stats(led)
    assert st["manual_reruns_after_a_scheduled_run"] == 0,         "排程之前跑的人工執行被算成重跑"


def test_a_manual_run_after_the_scheduled_one_is_a_rerun():
    """正向:時序對的那個才算。"""
    led = xl.append([], _rec("2026-08-07", ok=False, run_id="1",
                             at="2026-08-07T06:00:00+08:00"))
    led = xl.append(led, _rec("2026-08-07", ok=True, run_id="2", kind=xl.MANUAL,
                              at="2026-08-07T09:00:00+08:00"))
    assert xl.attempt_stats(led)["manual_reruns_after_a_scheduled_run"] == 1


def test_rows_without_timestamps_are_counted_separately():
    """**排不出先後就別猜。**

    當成「否」會低估偏差、當成「是」會高估 —— 兩個都是編造。
    分開報,讓看的人知道有多少列答不出這個問題。
    """
    led = xl.append([], _rec("2026-08-08", ok=False, run_id="1", at=" "))
    led = xl.append(led, _rec("2026-08-08", ok=True, run_id="2", kind=xl.MANUAL,
                              at=" "))
    st = xl.attempt_stats(led)
    assert st["manual_reruns_after_a_scheduled_run"] == 0
    assert st["manual_attempts_of_unknown_order"] == 1


def _cohort_rec(day, model, run_id, ok=True):
    return lx.build_record(
        today=day, experiment_id="e",
        primary={"profile": "luna", "ok": ok, "model": model, "effort": "xhigh"},
        shadow={"profile": "deepseek_legacy", "ok": ok},
        evidence_sha_primary="a", evidence_sha_shadow="a",
        core_sha_primary="c", core_sha_shadow="c",
        run={"run_id": run_id, "run_attempt": 1, "run_kind": xl.SCHEDULED,
             "started_at": f"{day}T06:00:00+08:00"})


def test_two_cohorts_on_the_same_day_do_not_evict_each_other():
    """**同一天換過模型時,兩個 cohort 不得互相擠掉**(第十三輪 P1-4)。

    `canonical()` 只依 `(date, experiment_id)` 分組、不看同群鍵 —— 於是
    換模型那天只有一筆存活,被擠掉的那群憑空少一個樣本,而可靠度還會
    跟著挑選順序跑。**收斂只在同一個可比範圍內才有意義。**
    """
    led = xl.append([], _cohort_rec("2026-08-03", "gpt-5.6-luna", "1"))
    led = xl.append(led, _cohort_rec("2026-08-03", "gpt-5.6-terra", "2"))
    luna = lx.cohort_key(_cohort_rec("2026-08-03", "gpt-5.6-luna", "1"))
    terra = lx.cohort_key(_cohort_rec("2026-08-03", "gpt-5.6-terra", "2"))
    assert luna != terra, "前提:換模型要換 cohort"
    for want, cohort in (("gpt-5.6-luna", luna), ("gpt-5.6-terra", terra)):
        day = xl.canonical(xl.scoped(led, lambda r, c=cohort:
                                     lx.cohort_key(r) == c))
        assert len(day) == 1 and day[0]["primary_model"] == want, (
            f"{want} 那天的樣本被另一個 cohort 擠掉了")


def test_record_day_scopes_before_it_converges(tmp_path):
    """**經由生產入口也要成立。**"""
    store = {"led": xl.append([], _cohort_rec("2026-08-03", "gpt-5.6-terra", "1"))}
    prog = lx.record_day(
        record=_cohort_rec("2026-08-03", "gpt-5.6-luna", "2"),
        today="2026-08-03", ledger_path=tmp_path / "l.json",
        read_ledger=lambda p: store["led"],
        write_ledger=lambda p, v: store.update(led=v),
        target=10, log=lambda m: None)
    assert prog["comparable_pairs"] == 1, (
        "terra 那筆被算進 luna 的 cohort,或反之")
    assert prog["attempts"]["recorded_runs"] == 1, (
        "嘗試統計混進了別的 cohort")


def test_attempt_stats_does_not_mix_other_experiments(tmp_path):
    """一個實驗的進度不得顯示另一個實驗的嘗試數。"""
    other = lx.build_record(
        today="2026-08-03", experiment_id="另一個實驗",
        primary={"profile": "x", "ok": True}, shadow={"profile": "y", "ok": True},
        evidence_sha_primary="a", evidence_sha_shadow="a",
        core_sha_primary="c", core_sha_shadow="c",
        run={"run_id": "9", "run_attempt": 1, "run_kind": xl.SCHEDULED,
             "started_at": "2026-08-03T06:00:00+08:00"})
    store = {"led": xl.append([], other)}
    prog = lx.record_day(
        record=_cohort_rec("2026-08-03", "gpt-5.6-luna", "2"),
        today="2026-08-03", ledger_path=tmp_path / "l.json",
        read_ledger=lambda p: store["led"],
        write_ledger=lambda p, v: store.update(led=v),
        target=10, log=lambda m: None)
    assert prog["attempts"]["recorded_runs"] == 1
    assert len(store["led"]) == 2, "別的實驗的原始紀錄不該被動到"


def test_provider_calls_are_counted_not_inferred_from_rows():
    """**一列不等於一次計費呼叫**(第十三輪 P1-4)。

    一份報告可能是「Luna 不合格 + 修補 + 影子」= 三次;用列數冒充呼叫數
    會低估帳單,而低估的方向正好偏向「這個實驗很便宜」。
    """
    rec = dict(_cohort_rec("2026-08-03", "gpt-5.6-luna", "1"),
               provider_calls=3, billable_unmeasured_calls=1)
    st = xl.attempt_stats([rec])
    assert st["recorded_runs"] == 1
    assert st["provider_calls"] == 3, "呼叫數被列數頂替了"
    assert st["billable_unmeasured_calls"] == 1


def test_production_records_the_call_count():
    """生產要真的把呼叫數記進去,否則上面那條永遠是 None。"""
    import run_manifest as rm
    got = rm.call_counts({"primary": {"model": "x"},
                          "attempts": [{"role": "primary"},
                                       {"role": "shadow",
                                        "billable_unmeasured": True}]})
    assert got == {"provider_calls": 3, "billable_unmeasured_calls": 1}


def test_an_accepted_role_can_be_more_than_one_call():
    """**accepted 那一格可能已經是多次呼叫的累加**(第十三輪 r1,#2)。

    `merge_same_role` 維護 `calls`(抽取器重試、短版重試都會讓它 >1),
    而原本一律算 1 —— 呼叫數又被低估,方向還是偏向「這個實驗很便宜」。
    先前的測試給的角色紀錄沒有 `calls`,所以只驗到單次那個情形。
    """
    import llm_telemetry as lt
    import run_manifest as rm
    one = lt.merge_same_role(None, lt.build_record(
        "openai", "gpt-5.6-luna",
        usage={"prompt_tokens": 10, "completion_tokens": 1}))
    two = lt.merge_same_role(one, lt.build_record(
        "openai", "gpt-5.6-luna",
        usage={"prompt_tokens": 10, "completion_tokens": 1}))
    assert two["calls"] == 2, "前提:merge 會累加 calls"
    assert rm.call_counts({"primary": two})["provider_calls"] == 2
    # 缺 `calls` 的舊紀錄退回 1,不要當成 0
    assert rm.call_counts({"primary": {"model": "x"}})["provider_calls"] == 1


def test_pricing_metadata_reaches_the_telemetry_record():
    """**生效費率要真的進到紀錄裡**(第十三輪 r1,#3)。

    `estimate_cost()` 回傳了 `pricing_tier` 與三個生效費率,而
    `build_record()` 只留總額與 basis —— 我在上一個 commit 寫下
    「只記總額的話,對不上帳單時分不出原因」,然後就讓那些欄位停在
    回傳值裡沒有帶出來。**宣稱與實作差的那一層,正好是宣稱要解決的問題。**
    """
    import llm_telemetry as lt
    for pt, want in ((100_000, "standard"), (300_000, "long_context")):
        rec = lt.build_record("openai", "gpt-5.6-luna",
                              usage={"prompt_tokens": pt,
                                     "completion_tokens": 10_000})
        assert rec["pricing_tier"] == want
        assert rec["pricing_schema"] == lt.PRICING_SCHEMA
        assert rec["effective_input_rate"] > 0
        assert rec["pricing_source"].startswith("developers.openai.com")


def test_started_at_comes_from_the_workflow_not_from_row_creation():
    """**先後要用執行開始時間判,不是用這一列被寫下的時間**(第十三輪 P2-5)。

    先前兩者混為一談,而寫下的時間是 LLM 分析跑完之後 —— 兩個 workflow
    重疊時「先開始、後完成」與「後開始、先完成」的順序會相反,
    而重跑偏差正是靠先後判的。
    """
    r = xl.run_identity({"GITHUB_RUN_ID": "7", "GITHUB_EVENT_NAME": "schedule",
                         "RUN_STARTED_AT": "2026-08-03T06:00:00+00:00"},
                        recorded_at="2026-08-03T06:12:00+08:00")
    assert r["started_at"] == "2026-08-03T06:00:00+00:00"
    assert r["recorded_at"] == "2026-08-03T06:12:00+08:00",         "記錄時間仍然要留著(對照 log 有用),只是不能拿來判先後"


def test_the_workflow_actually_exports_the_start_time():
    """workflow 要真的產生它,否則上面那條在生產永遠是空的。"""
    from pathlib import Path
    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
          / "morning-report.yml").read_text(encoding="utf-8")
    assert "RUN_STARTED_AT=" in wf, "workflow 沒有記下執行開始時間"
    assert wf.index("RUN_STARTED_AT=") < wf.index("Run morning report"),         "開始時間記在晨報**之後** —— 那就不是開始時間了"


def test_production_passes_recorded_at_not_started_at():
    """生產不得再把記錄時間當成開始時間交進去。"""
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "morning_report.py"
           ).read_text(encoding="utf-8")
    call = [n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call)
            and getattr(n.func, "attr", "") == "run_identity"]
    assert call, "生產沒有呼叫 run_identity"
    kw = {k.arg for k in call[0].keywords}
    assert kw == {"recorded_at"}, f"生產交進去的是 {kw},不該再有 started_at"
