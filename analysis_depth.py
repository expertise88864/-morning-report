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
        # 第十七輪 P1-7:**兩步連續不等於走到終點。**
        # 「事件 → 市場關注提高 → 投資情緒改善」通得過先前的所有判準,
        # 卻沒有碰到訂單、稼動率、營收、估值或股價的任何一層。
        if n.get("materiality") == "high" and steps:
            import analysis_schema as _sch
            seen = {str(st.get("stage") or "") for st in steps}
            if not (seen & set(_sch.OPERATIONAL_STAGES)):
                out.append(f"{where} 的因果鏈沒有走到營運或產業供需層 —— "
                           "真的走不到就把最後一步標成 sentiment,"
                           "並在 why_this_magnitude 說明它停在敘事驗證")
            if not (seen & set(_sch.TERMINAL_STAGES)):
                out.append(f"{where} 的因果鏈沒有走到營收/毛利/獲利/估值/"
                           "籌碼/股價任何一層 —— **停在情緒不算分析**;"
                           "走不到就明說缺什麼才走得到")
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


def _identity(obj) -> dict:
    """第二版**必須保留**的東西(第十七輪 P1-8)。

    先前只比數量,於是第二版可以:刪掉台積電那則、換一則次要新聞;
    刪掉反證、補一筆重複的支持證據;把「缺訂單金額」換成另一個無關的
    缺口 —— **數量全部持平,實質全部退步**。所以改成比**身分集合**。
    """
    o = obj or {}
    news = [n for n in (o.get("top_news_analysis") or []) if isinstance(n, dict)]
    cms = o.get("cross_market_synthesis") or {}
    return {
        "分析過的新聞": {str(n.get("source_item_id") or "") for n in news},
        "處理過的張力": {str(r.get("tension_id") or "")
                   for r in (cms.get("tension_resolutions") or [])
                   if isinstance(r, dict)},
        "反面證據": {str(x) for c in (o.get("claim_audit") or [])
                 if isinstance(c, dict)
                 for x in (c.get("counterevidence_ids") or [])},
        "資料缺口": {str((g or {}).get("what_is_missing") or "")
                 for g in (o.get("data_gaps") or []) if isinstance(g, dict)},
    }


#: 加深**不得順手改掉**的判斷欄位。它們不是深度,改了就是換一份報告。
_PINNED = (("stance", "label"), ("stance", "time_horizon"),
           ("cross_market_synthesis", "dominant_driver"))


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
        "addressed_tensions": len(cms.get("tension_resolutions") or []),
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
    # **要傳完整 packet**(第十七輪 P1-8):只傳 ID 集合的話,
    # 「必須處理的張力」「有新聞卻沒分析」這些 packet-aware 規則
    # 在這裡整個不會跑 —— 而那正是第二版最可能退步的地方。
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
    # **身分保存**:數量持平但內容被換掉,是最難察覺的退步。
    ib, ia = _identity(before), _identity(after)
    for name in ib:
        lost = ib[name] - ia[name]
        if lost:
            return False, f"第二版弄丟了{name}:{sorted(lost)[:3]}"
    for block, field in _PINNED:
        vb = str(((before or {}).get(block) or {}).get(field) or "")
        va = str(((after or {}).get(block) or {}).get(field) or "")
        if vb and va and vb != va:
            return False, f"{block}.{field} 被改掉({vb} → {va}) —— 加深不該改判斷"
    # 立場分與信心可以微調,但**大幅漂移**代表它重寫了判斷而不是加深。
    sb = ((before or {}).get("stance") or {}).get("score")
    sa = ((after or {}).get("stance") or {}).get("score")
    if isinstance(sb, int) and isinstance(sa, int) and abs(sa - sb) > 2:
        return False, f"立場分大幅改變({sb:+d} → {sa:+d})"
    db, da = _dominance(before), _dominance(after)
    worse = [k for k in db if da[k] < db[k]]
    if worse:
        return False, "這些面向退步了:" + "、".join(
            f"{k} {db[k]}→{da[k]}" for k in worse)
    return True, f"深度提示 {len(adv_b)} → {len(adv_a)}"


def depth_metrics(obj, packet=None) -> dict:
    """**十配對要回答的是「深度有沒有真的改善」,而先前量不到**
    (第十七輪 P1-9)。

    全部是結構性計數,不是關鍵詞。刻意不合成總分 —— 合成之後
    「因果鏈變深 3、張力少處理 1」會看起來像進步。
    """
    import analysis_schema as _sch
    o = obj if isinstance(obj, dict) else {}
    news = [n for n in (o.get("top_news_analysis") or []) if isinstance(n, dict)]
    hi = [n for n in news if n.get("materiality") == "high"]
    cms = o.get("cross_market_synthesis") or {}
    res = [r for r in (cms.get("tension_resolutions") or []) if isinstance(r, dict)]

    def _stages(n):
        return {str(st.get("stage") or "")
                for st in (n.get("mechanism_steps") or []) if isinstance(st, dict)}

    need = 0
    if isinstance(packet, dict):
        import signal_tensions as _st
        need = len(_st.required_tension_ids(packet.get("signal_tensions")))
    chains = [len([st for st in (n.get("mechanism_steps") or [])
                   if isinstance(st, dict)]) for n in news]
    return {
        "news_analyzed": len(news),
        "high_materiality": len(hi),
        "chain_steps_median": (sorted(chains)[len(chains) // 2] if chains else 0),
        # **走到財務/估值/股價的比例** —— 停在「情緒改善」不算。
        "reaches_financial": sum(
            1 for n in hi if _stages(n) & set(_sch.TERMINAL_STAGES)),
        "reaches_operations": sum(
            1 for n in hi if _stages(n) & set(_sch.OPERATIONAL_STAGES)),
        "magnitude_explained": sum(
            1 for n in news if str(n.get("why_this_magnitude") or "").strip()),
        "magnitude_unknown": sum(
            1 for n in news if n.get("magnitude_band") == "unknown"),
        "with_confirmation": sum(
            1 for n in news if str(n.get("confirmation_signal") or "").strip()),
        "with_invalidation": sum(
            1 for n in news if str(n.get("invalidation_signal") or "").strip()),
        "relations": sum(len(n.get("relates_to") or []) for n in news),
        # 張力覆蓋:**分母來自 packet**,不是模型自報 —— 自報的話
        # 「沒處理」與「今天沒有張力」會長得一樣。
        "tensions_required": need,
        "tensions_resolved": len(res),
        "resolutions_with_rule": sum(
            1 for r in res if str(r.get("decision_rule") or "").strip()),
        "priced_in_items": len((o.get("priced_in") or {}).get("already_reflected") or [])
        + len((o.get("priced_in") or {}).get("not_yet_reflected") or []),
        "data_gaps": len(o.get("data_gaps") or []),
    }
