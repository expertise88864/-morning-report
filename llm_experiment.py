# -*- coding: utf-8 -*-
"""**端到端 profile 比較實驗**的身分與配對語意(Phase 5)。

## 這個模組修的兩個設計問題

既有 `llm_shadow` 的同群鍵含 `code_version`,而且假設兩邊送**同一份 raw prompt**。
對「換個模型看看」那種比較夠用,對這次的實驗會壞掉:

  1. **任何無關的 commit 都會讓十筆樣本歸零。** 改一行 README、修一個測試,
     `code_version` 就變了,累積中的樣本全部落到新的同群。十天實驗
     不可能在完全不動程式碼的前提下跑完。
  2. **強迫兩邊共用 prompt,Luna 的特化就做不出來** —— 而特化正是要測的東西。

所以身分改成**語意契約版本**:證據 schema、輸出 schema、兩邊的 profile 與版本、
模型與強度、後處理與渲染版本。這些之中任何一個變了,樣本確實不可比;
而 git SHA 變了不一定 —— 它仍然記進紀錄當 provenance,只是不進同群鍵。

## 「十天」是什麼

**10 個成功且可比較的配對**,不是 10 個日曆日。任一邊失敗、證據 sha 不同、
或 profile 中途改版的那一天,不進有效分母 —— 但**紀錄要留著**,
因為「誰比較常失敗」本身就是要比的指標之一。
"""
from __future__ import annotations

from typing import Optional

#: 實驗帳本的 schema 版本。
EXPERIMENT_SCHEMA_VERSION = 1

#: 後處理與渲染的契約版本。**動了它們就等於換了一個系統**,
#: 樣本因此不可比 —— 所以它們在同群鍵裡。
#:
#: 這兩個數字要手動維護。自動從程式碼推導(檔案雜湊、git SHA)正是本模組
#: 要避免的東西:那會讓「改一個註解」變成「換一個系統」。
POSTPROCESS_VERSION = 1
RENDERER_VERSION = 1

#: 比較模式。`end_to_end_profiles` = 同一份證據、各自最佳化的問法,
#: 比的是**整套系統**而不是裸模型。
COMPARISON_MODE = "end_to_end_profiles"

#: **同群鍵:語意契約,不含 git SHA。**
#:
#: 判準是「這個東西變了,兩批樣本還能不能放進同一個平均」。
#: 模型、強度、問法、證據格式、輸出格式、後處理、渲染 —— 會;
#: 「修了一個錯字」—— 不會。
COHORT_FIELDS = (
    "experiment_id",
    "comparison_mode",
    "primary_profile", "primary_profile_version", "primary_model", "primary_effort",
    "shadow_profile", "shadow_profile_version", "shadow_model", "shadow_effort",
    "evidence_schema_version", "output_schema_version",
    "postprocess_version", "renderer_version",
)

#: 不進同群鍵、但要留在紀錄裡的溯源欄位。
#: `code_version` 在這裡 —— 它回答「這是哪一版程式跑的」,
#: 那個問題很有用,只是不該決定樣本能不能相加。
PROVENANCE_FIELDS = ("code_version", "date", "primary_prompt_sha",
                     "shadow_prompt_sha", "evidence_sha")

#: 有效樣本的目標數。**單位是配對,不是日曆日。**
DEFAULT_TARGET_PAIRS = 10


def cohort_key(rec: Optional[dict]) -> tuple:
    """一列紀錄屬於哪個同群。缺欄位的舊資料自成 `legacy/unknown` 群。"""
    r = rec or {}
    return tuple(str(r.get(f) if r.get(f) is not None else "legacy/unknown")
                 for f in COHORT_FIELDS)


#: 一天**不能**計入有效分母的理由。這些理由要被記錄與回報 ——
#: 只給「有效樣本數」而不說被排除了什麼,等於讓人以為那些天不存在。
EXCLUSION_REASONS = {
    "primary_failed": "主分析失敗",
    "shadow_failed": "影子失敗",
    "evidence_mismatch": "兩邊看到的證據不同",
    "missing_evidence_sha": "沒有證據指紋,無從證明可比",
    "other_cohort": "設定與本次實驗不同",
}


def exclusion_reason(rec: Optional[dict], cohort: Optional[tuple] = None) -> str:
    """這列**為什麼**不算有效配對;算數就回空字串。

    順序有意義:先判同群(設定不同的那天根本不屬於這個實驗),
    再判證據,最後才判成敗 —— 這樣回報出來的原因是最根本的那一個。
    """
    r = rec or {}
    if cohort is not None and cohort_key(r) != cohort:
        return "other_cohort"
    ev_p = str(r.get("primary_evidence_sha") or "")
    ev_s = str(r.get("shadow_evidence_sha") or "")
    if not ev_p or not ev_s:
        return "missing_evidence_sha"
    if ev_p != ev_s:
        return "evidence_mismatch"
    if not r.get("primary_ok"):
        return "primary_failed"
    if not r.get("shadow_ok"):
        return "shadow_failed"
    return ""


def is_comparable(rec: Optional[dict], cohort: Optional[tuple] = None) -> bool:
    """這一天算不算一個**成功且可比較的配對**。"""
    return exclusion_reason(rec, cohort) == ""


def pair_progress(ledger: Optional[list], cohort: Optional[tuple] = None,
                  target: int = DEFAULT_TARGET_PAIRS) -> dict:
    """實驗進度。**分母是配對數,不是天數。**

    回傳同時包含被排除的天數與原因 —— 沒有它,「跑了 14 天只有 6 筆」
    看起來會像實驗停滯,而實際上可能是影子一直逾時(那本身就是結論)。
    """
    rows = [r for r in (ledger or []) if isinstance(r, dict)]
    pairs, excluded = [], {}
    for r in rows:
        why = exclusion_reason(r, cohort)
        if why:
            excluded[why] = excluded.get(why, 0) + 1
        else:
            pairs.append(r)
    return {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "comparable_pairs": len(pairs),
        "target_pairs": int(target),
        "remaining": max(0, int(target) - len(pairs)),
        "ready": len(pairs) >= int(target),
        "rows_seen": len(rows),
        "excluded": dict(sorted(excluded.items())),
        "excluded_labels": {k: EXCLUSION_REASONS.get(k, k)
                            for k in sorted(excluded)},
    }


def reliability(ledger: Optional[list], cohort: Optional[tuple] = None) -> dict:
    """兩邊各自的成功率。**被排除的天數在這裡才有價值。**

    「誰比較常失敗」是十天實驗要回答的問題之一,而它只能從**沒有成為配對**
    的那些天算出來 —— 把它們丟掉等於把可靠度這個指標一起丟掉。
    """
    rows = [r for r in (ledger or []) if isinstance(r, dict)
            and (cohort is None or cohort_key(r) == cohort)]
    if not rows:
        return {"days": 0}
    p_ok = sum(1 for r in rows if r.get("primary_ok"))
    s_ok = sum(1 for r in rows if r.get("shadow_ok"))
    mismatch = sum(1 for r in rows
                   if exclusion_reason(r, cohort) == "evidence_mismatch")
    return {
        "days": len(rows),
        "primary_ok_rate": round(p_ok / len(rows), 3),
        "shadow_ok_rate": round(s_ok / len(rows), 3),
        "evidence_mismatch_days": mismatch,
    }


def verdict(progress: dict) -> str:
    """**樣本不足時明說「還不知道」**,不給一個看起來像結論的數字。

    這條規約沿用既有 `llm_shadow._verdict` 的精神:這個 repo 已經有太多次
    「機制存在但沒有證據」被當成有結論。
    """
    p = progress or {}
    got, target = p.get("comparable_pairs", 0), p.get("target_pairs", 0)
    if not p.get("ready"):
        bits = [f"樣本不足({got}/{target} 個可比較配對)"]
        ex = p.get("excluded") or {}
        if ex:
            bits.append("已排除:" + "、".join(
                f"{EXCLUSION_REASONS.get(k, k)} {v} 天" for k, v in ex.items()))
        bits.append("**尚不得下結論**")
        return ";".join(bits)
    return (f"已達 {got}/{target} 個可比較配對 —— 可以做判讀。"
            "判讀本身仍需人工盲評與逐日 stance flip 裁決,不得只看綜合分數。")


def build_record(*, today: str, experiment_id: str,
                 primary: dict, shadow: dict,
                 evidence_sha_primary: str, evidence_sha_shadow: str,
                 code_version: str = "") -> dict:
    """組出一列實驗帳本。**同群欄位與溯源欄位都要在。**

    `primary` / `shadow` 各自帶 `profile` / `profile_version` / `model` /
    `effort` / `ok` / `prompt_sha` 等。刻意用兩個 dict 而不是十幾個關鍵字參數
    —— 參數一多就會有人傳錯位置,而傳錯的症狀是同群鍵指向一個不存在的設定。
    """
    p, s = primary or {}, shadow or {}
    return {
        "date": today,
        "experiment_id": experiment_id,
        "comparison_mode": COMPARISON_MODE,
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "primary_profile": p.get("profile"),
        "primary_profile_version": p.get("profile_version"),
        "primary_model": p.get("model"),
        "primary_effort": p.get("effort"),
        "primary_ok": bool(p.get("ok")),
        "primary_prompt_sha": p.get("prompt_sha"),
        "primary_evidence_sha": evidence_sha_primary,
        "shadow_profile": s.get("profile"),
        "shadow_profile_version": s.get("profile_version"),
        "shadow_model": s.get("model"),
        "shadow_effort": s.get("effort"),
        "shadow_ok": bool(s.get("ok")),
        "shadow_prompt_sha": s.get("prompt_sha"),
        "shadow_evidence_sha": evidence_sha_shadow,
        "evidence_schema_version": p.get("evidence_schema_version"),
        "output_schema_version": p.get("output_schema_version"),
        "postprocess_version": POSTPROCESS_VERSION,
        "renderer_version": RENDERER_VERSION,
        # **溯源,不進同群鍵。** 它回答「哪一版程式跑的」,
        # 但不該決定樣本能不能相加。
        "code_version": (code_version or "unknown")[:12],
    }
