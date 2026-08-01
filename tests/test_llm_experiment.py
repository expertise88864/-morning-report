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
        "primary_ok": True, "primary_evidence_sha": "abc123",
        "shadow_profile": "deepseek_legacy_v1", "shadow_profile_version": 1,
        "shadow_model": "deepseek-v4-pro", "shadow_effort": "max",
        "shadow_ok": True, "shadow_evidence_sha": "abc123",
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
                 _rec(date="2026-08-11", shadow_evidence_sha="zzz")])  # 證據不同
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
    assert not ex.is_comparable(_rec(shadow_evidence_sha="different"), cohort)
    assert ex.exclusion_reason(_rec(shadow_evidence_sha="d"), cohort) == \
        "evidence_mismatch"


def test_a_missing_evidence_hash_is_not_silently_accepted():
    """沒有指紋就無從證明可比。**「沒有證據說它不同」不等於「相同」。**"""
    cohort = ex.cohort_key(_rec())
    for missing in ({"primary_evidence_sha": ""}, {"shadow_evidence_sha": None}):
        assert ex.exclusion_reason(_rec(**missing), cohort) == \
            "missing_evidence_sha"


def test_the_most_fundamental_reason_is_the_one_reported():
    """判斷順序:同群 → 證據 → 成敗。

    設定不同的那天根本不屬於這個實驗,回報「影子失敗」會誤導人去查
    影子的穩定性,而真正的原因是那天在跑另一組設定。
    """
    cohort = ex.cohort_key(_rec())
    odd = _rec(primary_model="gpt-5.6-terra", shadow_ok=False,
               shadow_evidence_sha="different")
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
