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

import analysis_grounding as _gr
import analysis_origin as _ao
import blind_review as _br
import experiment_ledger as _xl
import side_telemetry as _sc

#: 實驗帳本的 schema 版本。
EXPERIMENT_SCHEMA_VERSION = 1

#: 後處理與渲染的契約版本。**動了它們就等於換了一個系統**,
#: 樣本因此不可比 —— 所以它們在同群鍵裡。
#:
#: 這兩個數字要手動維護。自動從程式碼推導(檔案雜湊、git SHA)正是本模組
#: 要避免的東西:那會讓「改一個註解」變成「換一個系統」。
POSTPROCESS_VERSION = 1
#: v2:段落語意修正+補回四欄位;v3:schema v2 深度渲染;
#: v4(第十七輪 P1-3):逐筆張力調和進信 —— 只印「訊號互有矛盾」等於沒處理。
#: v10(Commit C):`key_drivers` 多了 `cluster_id`,渲染的欄位集合
#: 因此改變(指紋會動的是欄位,不是版面)。
#: v11(Commit E):三大重點改事件卡(帶這件事的來歷:官方/幾個獨立
#: 來源/連續追蹤第幾天)、新增「各標的合計影響」、共用驅動的說明進信。
RENDERER_VERSION = 11

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
    "grounding_version",   # P1-3:換一套接受規則等於換一個系統
)

#: 不進同群鍵、但要留在紀錄裡的溯源欄位。**`analysis_origin` 進同群的話,
#: 落回 legacy 的日子會從可靠度分母消失**(見 `test_analysis_origin.py`)。
PROVENANCE_FIELDS = ("code_version", "date", "primary_prompt_sha",
                     "shadow_prompt_sha", "evidence_sha", "analysis_origin",
                     "primary_telemetry", "shadow_telemetry",
                     "extractor_telemetry")

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
    # r2(Codex,#2):判準是**核心證據集**(來源池 + 交易日),不是整個 packet
    # 的指紋 —— 後者只證明「同一個 packet 物件」,而兩份 prompt 是各自組的。
    ev_p = str(r.get("primary_core_sha") or "")
    ev_s = str(r.get("shadow_core_sha") or "")
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
                  target: int = DEFAULT_TARGET_PAIRS, *, as_of: str) -> dict:
    """實驗進度。**分母是配對數,不是天數。**

    回傳同時包含被排除的天數與原因 —— 沒有它,「跑了 14 天只有 6 筆」
    看起來會像實驗停滯,而實際上可能是影子一直逾時(那本身就是結論)。

    `as_of` 是**必填**的(r7 Codex)。它原本預設空字串,而空字串讓
    `material_live` 永遠不判到期 —— 於是我把到期判定寫好、接線時漏傳,
    生產路徑上它從第一天起就是個 no-op,`pairs_review_expired` 恆為零。
    測試也沒抓到:那些測試直接呼叫本函式並自己傳 `as_of`,繞過了真正的
    呼叫端。**預設值是「關閉」的選用參數,就是一個等著被忘記的開關;
    改成必填之後,忘記傳會當場拋,不會靜靜地失效。**
    不做到期篩選是合法用途(例如事後分析),但那要 `as_of=""` 明講。
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
        # 有幾個配對**現在**還留著可取回的盲評材料。刻意不併進
        # `comparable_pairs`:配對是否可比與盲評材料在不在是兩件事,
        # 混成一個數字就再也分不出是哪一個缺。
        "pairs_with_review": sum(1 for r in pairs if _br.material_live(r, as_of)),
        # 曾經有、但 artifact 已經過期的那些。分開報才看得出「材料不足」
        # 是從來沒產生,還是產生了卻放到過期。
        "pairs_review_expired": sum(
            1 for r in pairs if r.get("review_ok") and not _br.material_live(r, as_of)),
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
    with_review = p.get("pairs_with_review", 0)
    tail = ("判讀本身仍需人工盲評與逐日 stance flip 裁決,不得只看綜合分數。")
    if with_review < got:
        # r5(Codex,#1):要求一件做不到的事,等於沒有要求。
        expired = p.get("pairs_review_expired", 0)
        tail += (f"**但只有 {with_review}/{got} 個配對還留著可取回的盲評卡** ——"
                 "其餘的卡片寫失敗、落在 job 結束即消失的 sink"
                 + (f",或 artifact 已經過期({expired} 天)" if expired else "")
                 + ",那幾天的盲評無法補做。")
    return f"已達 {got}/{target} 個可比較配對 —— 可以做判讀。{tail}"


def build_record(*, today: str, experiment_id: str,
                 primary: dict, shadow: dict,
                 evidence_sha_primary: str, evidence_sha_shadow: str,
                 core_sha_primary: str = "", core_sha_shadow: str = "",
                 code_version: str = "", failure_reason: str = "",
                 review: Optional[dict] = None, run: Optional[dict] = None,
                 metrics: Optional[dict] = None, analysis_origin: str = "",
                 telemetry: Optional[dict] = None) -> dict:
    """組出一列實驗帳本。**同群欄位與溯源欄位都要在。**

    `primary` / `shadow` 各自帶 `profile` / `profile_version` / `model` /
    `effort` / `ok` / `prompt_sha` 等。刻意用兩個 dict 而不是十幾個關鍵字參數
    —— 參數一多就會有人傳錯位置,而傳錯的症狀是同群鍵指向一個不存在的設定。
    """
    p, s = primary or {}, shadow or {}
    return {
        "date": today,
        "experiment_id": experiment_id,
        # 第十二輪 P1-4:**哪一次執行寫的。** 少了這個,同日重跑會覆蓋掉
        # 排程那次的失敗 —— 而越不可靠的日子越容易被人重跑洗白。
        **{k: (run or {}).get(k, v) for k, v in
           (("run_id", ""), ("run_attempt", 0), ("run_kind", _xl.LOCAL),
            # P2-1:沒 telemetry 的預設是 `None` 不是 `0`(「沒量到」≠「確定零次」)
            ("started_at", ""), ("recorded_at", ""), ("provider_calls", None),
            ("billable_unmeasured_calls", None))},
        # r5 #1/#3、r6 #1/#3:這一天有沒有**還取得回來的**盲評材料。
        # 判讀明文要求人工盲評 —— 卡片寫失敗、落在 job 結束就消失的 sink、
        # 或 artifact 已經過期,都讓那個要求做不成,而「達標」會變成空話。
        "review_ok": bool((review or {}).get("review_ok")),
        "review_expires": str((review or {}).get("review_expires") or ""),
        "comparison_mode": COMPARISON_MODE,
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "primary_profile": p.get("profile"),
        "primary_profile_version": p.get("profile_version"),
        "primary_model": p.get("model"),
        "primary_effort": p.get("effort"),
        "primary_ok": bool(p.get("ok")),
        "primary_prompt_sha": p.get("prompt_sha"),
        "primary_evidence_sha": evidence_sha_primary,
        "primary_core_sha": core_sha_primary,
        # **深度差異要被記錄。** 兩側涵蓋率不同是預期的(各自最佳化),
        # 但十配對的結論必須說得出「這是模型差異還是餵進去的東西不同」。
        "primary_coverage": dict(primary.get("coverage") or {}),
        "shadow_profile": s.get("profile"),
        "shadow_profile_version": s.get("profile_version"),
        "shadow_model": s.get("model"),
        "shadow_effort": s.get("effort"),
        "shadow_ok": bool(s.get("ok")),
        "shadow_prompt_sha": s.get("prompt_sha"),
        "shadow_evidence_sha": evidence_sha_shadow,
        "shadow_core_sha": core_sha_shadow,
        "shadow_coverage": dict(shadow.get("coverage") or {}),
        "evidence_schema_version": p.get("evidence_schema_version"),
        "output_schema_version": p.get("output_schema_version"),
        "postprocess_version": POSTPROCESS_VERSION,
        "renderer_version": RENDERER_VERSION,
        "grounding_version": _gr.GROUNDING_VERSION,
        # **溯源,不進同群鍵**(見 PROVENANCE_FIELDS)。
        "code_version": (code_version or "unknown")[:12],
        # P1-4:**逐側的成本與延遲。** manifest 隔天就被下一班覆蓋,
        # 只留整班總和的話,十配對達標時比得出「這一班多少錢」,
        # 比不出兩套系統各自的成本效益 —— 而那是「要不要換」的核心問題。
        # 抽取器標 `shared`、不歸任何一側(理由見 `side_telemetry` 模組)。
        **{f"{k}_telemetry": dict(v) for k, v in (telemetry or {}).items()
           if isinstance(v, dict)},
        "analysis_origin": _ao.normalize(analysis_origin),
        # 失敗的那天要說得出**為什麼** —— 只記「失敗」的帳本回答不了
        # 「誰比較常失敗、失敗在哪裡」,而那正是十配對要比的指標之一。
        "failure_reason": failure_reason or "",
        # r3(Codex,#2):**兩側都算得出來的指標**。十配對達標時要有東西可以
        # 判讀 —— 只有立場、字數與 body overlap 的話,正是指令書明說不可當
        # 判準的那幾樣。結構化指標只有 Luna 有,刻意不放進來(那會變成
        # 「有結構 vs 沒結構」的比較)。
        "metrics": dict(metrics or {}),
    }


# ---------------------------------------------------------------- 帳本

def load_ledger(path) -> list:
    """讀實驗帳本。**讀不出來就拋** —— 呼叫端不得代它把檔案清掉。

    這是本 repo 反覆出現的病灶(讀檔失敗被當成沒有資料,再被原子覆寫),
    而實驗帳本的全部價值就是跨日累積:一次誤覆寫等於十配對重新開始。
    """
    from pathlib import Path as _P
    import json as _json

    p = _P(path)
    if not p.exists():
        return []
    data = _json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"實驗帳本格式非預期:{type(data).__name__}")
    for i, r in enumerate(data):
        if not isinstance(r, dict):
            raise ValueError(f"實驗帳本第 {i} 列不是物件")
    return data


def record_day(*, record: dict, today: str, ledger_path, read_ledger,
               write_ledger, target: int = DEFAULT_TARGET_PAIRS,
               log=print) -> dict:
    """把今天這一列寫進帳本,並回報**跨日累積**的進度。

    這是「十配對」真正會計數的地方。先前只把紀錄寫進當日 manifest ——
    而 manifest 每天覆寫,所以那個門檻永遠不會被觸及:
    `pair_progress()` 從來沒有被呼叫過,`LLM_EXPERIMENT_TARGET_PAIRS` 也從來
    沒有被使用過。機制存在但不會計數,比沒有機制更糟(它看起來在運作)。

    **失敗只是今天沒有累積**:讀不出帳本就不寫(不得覆蓋),
    寫不進去也不得讓晨報中斷 —— 由呼叫端吞例外。
    """
    ledger = read_ledger(ledger_path)          # 讀不出來讓它拋,呼叫端決定
    # 第十二輪 P1-4:**追加,不覆蓋。** 原始紀錄一旦被蓋掉就補不回來,
    # 而收斂成「一天一筆」是判讀時的事(`canonical`)。
    ledger = _xl.append(ledger, record)
    write_ledger(ledger_path, ledger)
    cohort = cohort_key(record)
    # 第十三輪 P1-4:**先劃範圍再收斂。** 同一天若換過模型/強度/profile,
    # 不分同群就收斂會讓兩個 cohort 互相擠掉,而被擠掉的那群憑空少一天。
    # 嘗試層級同理:不分實驗的話,這個實驗的進度會顯示別的實驗的嘗試數。
    same = _xl.scoped(ledger, lambda r: cohort_key(r) == cohort)
    daily = _xl.canonical(same)
    progress = pair_progress(daily, cohort, target, as_of=today)
    progress["attempts"] = _xl.attempt_stats(same)
    # P1-4:**逐側成本橫跨所有嘗試,不是代表樣本。** 配對一天只算一次,
    # 帳單不行 —— 重跑那筆錢真的花掉了。所以傳 `same` 而不是 `daily`。
    progress["side_costs"] = _sc.side_costs(same)
    progress["cohort_fields"] = dict(zip(COHORT_FIELDS, cohort))
    progress["reliability"] = reliability(daily, cohort)
    progress["verdict"] = verdict(progress)
    log(f"[llm-experiment] {progress['verdict']}")
    return progress

