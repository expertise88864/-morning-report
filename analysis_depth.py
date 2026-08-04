# -*- coding: utf-8 -*-
"""**合法但淺**的判準,以及加深那一次的取捨(第十五/十六輪)。

與 `analysis_validate` 刻意分開,因為兩者的**後果完全不同**:

  * 不合法 → 修補;修不好 → 落回 legacy。
  * **淺 → 什麼都不擋。** 淺而正確的分析落回 legacy 只會換來一封更淺的信,
    所以淺只用來決定「要不要把還沒用掉的那次呼叫拿去加深」。

判準全部是**結構性**的(數步數、查空欄、比集合),不是關鍵詞 ——
第十六輪 P1-5 說對了:關鍵詞當門檻,一句「兩者同向,預計明年影響 5%」
就能騙過。
"""
from __future__ import annotations

# `validate` 在函式內延遲取用 —— `analysis_validate` 的尾端會反向
# import 本模組(相容出口),頂層互相 import 會在載入順序上翻車。


# ------------------------------------------------------------ 深度(不擋信)

def depth_advisories(obj) -> list:
    """**合法但淺**的地方(空 = 夠深)。與 `validate()` 刻意分開:

    這裡的每一條都**不會**讓輸出被拒絕 —— 淺而正確的分析落回 legacy
    只會換來一封更淺的信。它們的用途是:第一次輸出合法但淺的時候,
    把**還沒用掉的那次修補額度**拿來加深(見 `deepen_input`),
    最壞情況仍是兩次呼叫,與修補相同 —— 多的是深度,不是新的失敗模式。

    判準全部是結構性的(數步數、查空欄),不是關鍵詞 —— 第十五輪 P1-5
    說對了:關鍵詞當門檻,一句「兩者同向,預計明年影響 5%」就能騙過。
    """
    out: list = []
    if not isinstance(obj, dict):
        return out
    news = [n for n in (obj.get("top_news_analysis") or []) if isinstance(n, dict)]
    for i, n in enumerate(news):
        where = f"top_news_analysis[{i}]"
        steps = [s for s in (n.get("mechanism_steps") or []) if isinstance(s, dict)]
        if n.get("materiality") == "high" and len(steps) < 2:
            out.append(f"{where} 是高重要性,因果鏈卻只有 {len(steps)} 步 —— "
                       "至少走到「事件 → 營運 → 財務或股價」兩步;"
                       "不確定的步驟標 inference/scenario,不要省略")
        if (n.get("magnitude_band") in ("negligible", "small", "moderate", "large")
                and not str(n.get("why_this_magnitude") or "").strip()):
            out.append(f"{where} 給了量級卻沒有說為什麼是這個量級")
    cms = obj.get("cross_market_synthesis")
    if isinstance(cms, dict):
        has_content = any(str(v or "").strip() if isinstance(v, str) else v
                          for k, v in cms.items() if k != "evidence_ids")
        if has_content:
            if not [x for x in (cms.get("conflicting_signals") or [])
                    if str(x).strip()]:
                out.append("cross_market_synthesis 沒有列任何互相抵銷的訊號 —— "
                           "確實沒有衝突時要寫一條「今日無明顯互相抵銷的訊號」明講,"
                           "不得留空")
            if not str(cms.get("dominant_driver") or "").strip():
                out.append("cross_market_synthesis 沒有指出今天的主導因子")
            if not str(cms.get("what_would_flip_it") or "").strip():
                out.append("cross_market_synthesis 沒有說什麼情況會讓主導因子失效")
    if len(news) >= 3 and not any((n.get("relates_to") or []) for n in news):
        out.append(f"{len(news)} 則新聞裡沒有任何一則指出與其他條目的關係 —— "
                   "確認它們是否真的全部獨立;**沒有根據的關係不要硬湊**,"
                   "但搶同一段產能或同一個底層驅動的要指出來")
    return out


def deepen_input(user_payload: str, advisories: list, previous=None) -> str:
    """加深那一次呼叫的 user 輸入。

    **要把上一版附上去**(第十六輪 P1-8)。先前只說「上一版深度不足」,
    模型看不到自己寫過什麼,只能整份重生 —— 於是可能修好了深度、卻少分析
    一則重要新聞,或把立場改掉。附上前一版並要求**保留已成立的內容**,
    把「重寫」變成「加深」。

    **加深是把已有的證據走完因果鏈,不是編內容** —— 這句話要留在指令裡,
    否則「至少兩步」這種要求本身就會誘發編造。
    """
    prev = ""
    if previous is not None:
        import json as _json
        prev = ("\n<PREVIOUS_OUTPUT>\n"
                + _json.dumps(previous, ensure_ascii=False)
                + "\n</PREVIOUS_OUTPUT>\n")
    return (user_payload + "\n\nDEEPEN\n上一版輸出合法,但深度不足。" + prev
            + "請**保留上一版所有已經成立的內容**(同一批新聞、同一個立場、"
            "同樣的資料缺口),只針對下列各點加深,再輸出**完整** JSON。"
            "沒有根據的關係與證據**不得硬湊** —— 加深是把已有的證據"
            "走完因果鏈與量級判斷,不是編造新內容;真的判斷不出量級就選 "
            "unknown 並寫缺哪些資料:\n"
            + "\n".join(f"- {a}" for a in advisories[:6]))


def _dominance(obj) -> dict:
    """比較兩版用的**可數面向**。只數結構,不評文字品質。

    刻意回一組數字而不是一個總分:合成之後,「深度 +3、證據 -2」
    會看起來像進步。
    """
    news = [n for n in ((obj or {}).get("top_news_analysis") or [])
            if isinstance(n, dict)]
    cms = (obj or {}).get("cross_market_synthesis") or {}
    ev = sum(len((st or {}).get("evidence_ids") or [])
             for n in news for st in (n.get("mechanism_steps") or []))
    return {
        "news_items": len(news),
        "high_materiality": sum(1 for n in news if n.get("materiality") == "high"),
        "data_gaps": len((obj or {}).get("data_gaps") or []),
        "step_evidence": ev,
        "addressed_tensions": len(cms.get("addressed_tension_ids") or []),
        "counterevidence": sum(len((c or {}).get("counterevidence_ids") or [])
                               for c in ((obj or {}).get("claim_audit") or [])),
    }


def deepen_is_an_improvement(before, after, *, evidence_ids) -> tuple:
    """第二版**是不是真的比較好**(第十六輪 P1-8)。回 `(bool, 理由)`。

    先前只要第二版合法就採用 —— 而加深那次是**整份重生**,可能修好深度
    卻少分析一則新聞、改掉立場、刪掉資料缺口。
    **「一個修正可能比原本的缺陷更糟」正是這個 repo 反覆栽的形狀**,
    而這一次是我自己寫進去的。

    判準逐項檢查、任一條不成立就留第一版:合法、深度提示要**減少**、
    立場不得漂移、每個可數面向都不得退步。
    """
    if not isinstance(after, dict):
        return False, "第二版不是物件"
    from analysis_validate import validate   # 延遲:避免循環
    problems = validate(after, evidence_ids)
    if problems:
        return False, f"第二版不合法({problems[0][:40]})"
    adv_b, adv_a = depth_advisories(before), depth_advisories(after)
    if len(adv_a) >= len(adv_b):
        return False, f"深度提示沒有減少({len(adv_b)} → {len(adv_a)})"
    sb = str(((before or {}).get("stance") or {}).get("label") or "")
    sa = str(((after or {}).get("stance") or {}).get("label") or "")
    if sb and sa and sb != sa:
        return False, f"立場漂移({sb} → {sa}) —— 加深不該改變判斷"
    db, da = _dominance(before), _dominance(after)
    worse = [k for k in db if da[k] < db[k]]
    if worse:
        return False, "這些面向退步了:" + "、".join(
            f"{k} {db[k]}→{da[k]}" for k in worse)
    return True, f"深度提示 {len(adv_b)} → {len(adv_a)}"
