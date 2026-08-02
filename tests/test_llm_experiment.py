# -*- coding: utf-8 -*-
"""**實驗身分與配對語意的契約**(Phase 5)。

指令書點名的兩個必須先修的問題,各由這裡的一半盯住:

  1. `code_version` 在同群鍵裡 → 任何無關的 commit 都會讓十筆樣本歸零。
     十天實驗不可能在完全不動程式碼的前提下跑完。
  2. 「十天」若被實作成日曆日,失敗的那天會被當成有效樣本 ——
     而失敗率本身正是要比的指標之一。
"""
import llm_experiment as ex


def _rec(**over):
    base = {
        "date": "2026-08-05",
        "experiment_id": "luna56-xhigh-vs-dsv4pro-v1",
        "comparison_mode": ex.COMPARISON_MODE,
        "primary_profile": "luna56_xhigh_v1", "primary_profile_version": 1,
        "primary_model": "gpt-5.6-luna", "primary_effort": "xhigh",
        # r2(Codex,#2):可比性看**核心證據集**(來源池 + 交易日),
        # 不是整個 packet 的指紋 —— 兩份 prompt 是各自組出來的。
        "primary_ok": True, "primary_core_sha": "abc123",
        # 深度差異被**記錄**而不是被隱藏:兩側涵蓋率不同是預期的。
        "primary_coverage": {"available": 200, "included": 200, "rate": 1.0},
        "shadow_profile": "deepseek_legacy_v1", "shadow_profile_version": 1,
        "shadow_model": "deepseek-v4-pro", "shadow_effort": "max",
        "shadow_ok": True, "shadow_core_sha": "abc123",
        "shadow_coverage": {"available": 200, "included": 180, "rate": 0.9},
        "evidence_schema_version": 1, "output_schema_version": 1,
        "postprocess_version": ex.POSTPROCESS_VERSION,
        "renderer_version": ex.RENDERER_VERSION,
        "code_version": "cd41fee",
    }
    base.update(over)
    return base


def test_an_unrelated_commit_does_not_reset_the_cohort():
    """**指令書點名的第一個問題。**

    改一行 README、修一個測試,`code_version` 就變了。若它在同群鍵裡,
    累積中的樣本會全部落到新的同群 —— 十天實驗不可能在完全不動程式碼的
    前提下跑完。
    """
    a = ex.cohort_key(_rec(code_version="cd41fee"))
    b = ex.cohort_key(_rec(code_version="9999abc"))
    assert a == b, "無關的 commit 改變了同群身分"
    assert "code_version" not in ex.COHORT_FIELDS
    assert "code_version" in ex.PROVENANCE_FIELDS, \
        "code_version 完全消失了 —— 它仍要留著回答「哪一版程式跑的」"


def test_changing_the_contract_does_reset_the_cohort():
    """反向:契約真的變了就必須換同群,否則會把兩種定義混進同一個平均。"""
    base = ex.cohort_key(_rec())
    for field, value in (
            ("primary_model", "gpt-5.6-terra"),
            ("primary_effort", "high"),
            ("primary_profile", "luna56_xhigh_v2"),
            ("primary_profile_version", 2),
            ("shadow_model", "deepseek-v4-flash"),
            ("shadow_effort", "high"),
            ("evidence_schema_version", 2),
            ("output_schema_version", 2),
            ("postprocess_version", 2),
            ("renderer_version", 2),
            ("experiment_id", "另一個實驗"),
            ("comparison_mode", "raw_prompt")):
        assert ex.cohort_key(_rec(**{field: value})) != base, \
            f"{field} 變了,同群身分卻沒變"


def test_ten_means_ten_comparable_pairs_not_ten_days():
    """**指令書點名的第二個問題。**

    失敗的那天不進有效分母 —— 但紀錄要留著,因為「誰比較常失敗」
    本身就是要比的指標。
    """
    cohort = ex.cohort_key(_rec())
    ledger = ([_rec(date=f"2026-08-{d:02d}") for d in range(1, 9)]      # 8 個好日
              + [_rec(date="2026-08-09", shadow_ok=False),             # 影子掛
                 _rec(date="2026-08-10", primary_ok=False),            # 主分析掛
                 _rec(date="2026-08-11", shadow_core_sha="zzz")])  # 證據不同
    p = ex.pair_progress(ledger, cohort, target=10)
    assert p["rows_seen"] == 11
    assert p["comparable_pairs"] == 8, p
    assert p["ready"] is False and p["remaining"] == 2
    assert p["excluded"] == {"evidence_mismatch": 1, "primary_failed": 1,
                             "shadow_failed": 1}, p
    assert "影子失敗" in p["excluded_labels"].values()


def test_evidence_mismatch_makes_a_day_incomparable():
    """兩邊看到的證據不同,那天就不可比 —— 這是公平性的全部依據。"""
    cohort = ex.cohort_key(_rec())
    assert ex.is_comparable(_rec(), cohort)
    assert not ex.is_comparable(_rec(shadow_core_sha="different"), cohort)
    assert ex.exclusion_reason(_rec(shadow_core_sha="d"), cohort) == \
        "evidence_mismatch"


def test_a_missing_evidence_hash_is_not_silently_accepted():
    """沒有指紋就無從證明可比。**「沒有證據說它不同」不等於「相同」。**"""
    cohort = ex.cohort_key(_rec())
    for missing in ({"primary_core_sha": ""}, {"shadow_core_sha": None}):
        assert ex.exclusion_reason(_rec(**missing), cohort) == \
            "missing_evidence_sha"


def test_the_most_fundamental_reason_is_the_one_reported():
    """判斷順序:同群 → 證據 → 成敗。

    設定不同的那天根本不屬於這個實驗,回報「影子失敗」會誤導人去查
    影子的穩定性,而真正的原因是那天在跑另一組設定。
    """
    cohort = ex.cohort_key(_rec())
    odd = _rec(primary_model="gpt-5.6-terra", shadow_ok=False,
               shadow_core_sha="different")
    assert ex.exclusion_reason(odd, cohort) == "other_cohort"


def test_failures_still_feed_the_reliability_metric():
    """被排除的天數在可靠度這裡才有價值 —— 丟掉它們等於丟掉一個指標。"""
    cohort = ex.cohort_key(_rec())
    ledger = [_rec(date="1"), _rec(date="2"),
              _rec(date="3", shadow_ok=False),
              _rec(date="4", shadow_ok=False),
              _rec(date="5", primary_ok=False)]
    r = ex.reliability(ledger, cohort)
    assert r["days"] == 5
    assert r["primary_ok_rate"] == 0.8
    assert r["shadow_ok_rate"] == 0.6
    assert ex.reliability([], cohort) == {"days": 0}


def test_the_verdict_refuses_to_conclude_on_thin_evidence():
    """樣本不足時要明說「還不知道」,而且要說出被排除了什麼。

    只給「有效樣本 6 筆」而不說排除了什麼,會讓人以為實驗停滯,
    而實際可能是影子一直逾時 —— 那本身就是結論。
    """
    cohort = ex.cohort_key(_rec())
    few = ex.pair_progress([_rec(date=str(i)) for i in range(3)]
                           + [_rec(date="x", shadow_ok=False)], cohort)
    v = ex.verdict(few)
    assert "樣本不足" in v and "3/10" in v
    assert "影子失敗 1 天" in v, v
    assert "尚不得下結論" in v

    enough = ex.pair_progress([_rec(date=str(i)) for i in range(10)], cohort)
    assert enough["ready"] is True
    assert "可以做判讀" in ex.verdict(enough)
    assert "人工盲評" in ex.verdict(enough), \
        "達標的判讀不得暗示可以只看綜合分數"


def test_the_record_carries_both_cohort_and_provenance_fields():
    """帳本一列要同時有同群欄位與溯源欄位,否則事後補不回來。"""
    rec = ex.build_record(
        today="2026-08-05", experiment_id="e1",
        primary={"profile": "luna56_xhigh_v1", "profile_version": 1,
                 "model": "gpt-5.6-luna", "effort": "xhigh", "ok": True,
                 "prompt_sha": "p1", "evidence_schema_version": 1,
                 "output_schema_version": 1},
        shadow={"profile": "deepseek_legacy_v1", "profile_version": 1,
                "model": "deepseek-v4-pro", "effort": "max", "ok": True,
                "prompt_sha": "s1"},
        evidence_sha_primary="ev", evidence_sha_shadow="ev",
        core_sha_primary="core", core_sha_shadow="core",
        code_version="abcdef1234567890")
    for f in ex.COHORT_FIELDS:
        assert rec.get(f) is not None, f"同群欄位 {f} 沒有被寫進紀錄"
    assert rec["code_version"] == "abcdef123456", "溯源欄位沒有被截短或遺失"
    assert rec["primary_prompt_sha"] != rec["shadow_prompt_sha"], \
        "兩邊的 prompt 指紋相同 —— 特化沒有發生"
    assert ex.is_comparable(rec, ex.cohort_key(rec))


def test_progress_never_raises_on_a_messy_ledger():
    """帳本裡混進舊格式或垃圾時,進度計算不得炸。

    它一炸就沒有進度可看,而那時最需要知道的正是「還差幾筆」。
    """
    for junk in (None, [], [None, 7, "字串"], [{"date": "x"}]):
        p = ex.pair_progress(junk, None, target=10)
        assert p["comparable_pairs"] >= 0 and p["ready"] is False
        assert isinstance(ex.verdict(p), str)


# ---------------------------------------------------------------- 帳本(r2 #3)

def _mem_ledger():
    """記憶體帳本(不碰檔案系統,本模組刻意保持純函式可測)。"""
    store = {}

    def read(path):
        return list(store.get(str(path), []))

    def write(path, ledger):
        store[str(path)] = list(ledger)

    return store, read, write


def test_the_ledger_actually_accumulates_across_days():
    """**r2(Codex,#3)點名的問題。**

    先前紀錄只寫進當日 manifest,而 manifest 每天覆寫 —— `pair_progress()`
    從來沒有被呼叫、`LLM_EXPERIMENT_TARGET_PAIRS` 從來沒有被使用。
    十配對的計數機制**存在但不會計數**,而那比沒有機制更糟:它看起來在運作。
    """
    store, read, write = _mem_ledger()
    progress = None
    for d in range(1, 13):
        rec = _rec(date=f"2026-08-{d:02d}")
        if d in (3, 7):
            rec["shadow_ok"] = False
        progress = ex.record_day(record=rec, today=rec["date"],
                                 ledger_path="L", read_ledger=read,
                                 write_ledger=write, target=10,
                                 log=lambda m: None)
    assert len(store["L"]) == 12, "帳本沒有跨日累積"
    assert progress["comparable_pairs"] == 10, progress
    assert progress["ready"] is True
    assert progress["excluded"] == {"shadow_failed": 2}
    # 失敗的兩天仍要進可靠度分母 —— 那是它們的價值所在
    assert progress["reliability"]["days"] == 12
    assert progress["reliability"]["shadow_ok_rate"] < 1.0
    assert "cohort_fields" in progress, "沒有記下這批樣本屬於哪個設定"


def test_rerunning_the_same_day_replaces_not_duplicates():
    """同一天重跑不得變成兩筆 —— 那會讓十配對提早達標。"""
    store, read, write = _mem_ledger()
    for _ in range(3):
        ex.record_day(record=_rec(date="2026-08-05"), today="2026-08-05",
                      ledger_path="L", read_ledger=read, write_ledger=write,
                      log=lambda m: None)
    assert len(store["L"]) == 1, f"同一天被記了 {len(store['L'])} 筆"


def test_an_unreadable_ledger_is_never_overwritten():
    """讀不出來就拋 —— 覆蓋等於把累積中的十配對清零。

    這是本 repo 反覆出現的病灶(讀檔失敗被當成沒有資料,再被原子覆寫)。
    """
    import pytest as _pytest

    written = []

    def _boom(path):
        raise ValueError("帳本壞了")

    with _pytest.raises(ValueError):
        ex.record_day(record=_rec(), today="2026-08-05", ledger_path="L",
                      read_ledger=_boom,
                      write_ledger=lambda _p, rows: written.append(rows),
                      log=lambda m: None)
    assert not written, "讀不出來卻仍然寫了 —— 累積被清零"


def test_the_target_comes_from_the_caller_not_a_hardcoded_ten():
    """`LLM_EXPERIMENT_TARGET_PAIRS` 要真的被使用。"""
    store, read, write = _mem_ledger()
    p = ex.record_day(record=_rec(), today="2026-08-05", ledger_path="L",
                      read_ledger=read, write_ledger=write, target=3,
                      log=lambda m: None)
    assert p["target_pairs"] == 3 and p["remaining"] == 2


def test_the_depth_difference_is_disclosed_not_hidden():
    """r2(Codex,#2)的折衷:兩側涵蓋率不同是**預期**的,但要被記錄。

    可比性只保證「同一批新聞、同一個交易日」;深度差異由 coverage 揭露,
    最終報告才說得出「這是模型差異,還是餵進去的東西不同」。
    """
    rec = _rec()
    assert rec["primary_coverage"]["rate"] != rec["shadow_coverage"]["rate"], \
        "測試資料沒有反映真實的深度差異"
    # 深度不同**不影響**可比性 —— 那正是折衷的內容
    assert ex.is_comparable(rec, ex.cohort_key(rec))
    # 但核心證據集不同就不可比
    assert not ex.is_comparable(_rec(shadow_core_sha="別批新聞"),
                                ex.cohort_key(rec))


def test_the_ledger_row_carries_both_sides_coverage_and_metrics():
    """r3(Codex,#3 #2):帳本要帶得動深度揭露與可比指標。

    先前 `build_record` 有這兩個欄位、生產卻沒有傳進來 —— 帳本永遠得到
    兩個空物件,折衷 (b) 依賴的揭露無法隨十配對累積,
    而十配對達標時也沒有東西可以判讀。
    """
    rec = ex.build_record(
        today="2026-08-05", experiment_id="e1",
        primary={"profile": "luna56_xhigh_v1", "profile_version": 1,
                 "model": "gpt-5.6-luna", "effort": "xhigh", "ok": True,
                 "coverage": {"available": 200, "included": 200, "rate": 1.0}},
        shadow={"profile": "deepseek_legacy_v1", "profile_version": 1,
                "model": "deepseek-v4-pro", "effort": "max", "ok": True,
                "coverage": {"available": None, "basis": "legacy 無逐則統計"}},
        evidence_sha_primary="ev", evidence_sha_shadow="ev",
        core_sha_primary="c", core_sha_shadow="c",
        metrics={"primary": {"chars": 3000}, "shadow": {"chars": 2800}})
    assert rec["primary_coverage"]["rate"] == 1.0
    # DeepSeek 側沒有對應統計時要**明說不可得**,不得用空物件冒充已記錄
    assert rec["shadow_coverage"]["available"] is None
    assert rec["shadow_coverage"].get("basis"), "沒有說明為什麼不可得"
    assert rec["metrics"]["primary"]["chars"] == 3000
    assert rec["metrics"]["shadow"]["chars"] == 2800
