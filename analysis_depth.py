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

def _registry_of(packet):
    """**沒有 packet 時退回寬鬆判準**(舊呼叫端仍要能用),而且說得出來:
    那時 `is_numeric_anchor` 查不到 metadata,只能看命名空間。"""
    if not isinstance(packet, dict):
        return None
    import evidence_registry as _reg
    return _reg.registry(packet)


def depth_advisories(obj, packet=None) -> list:
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
        # 深度加強(縱向,2026-08-05):**沒有量化錨點的鏈是散文。**
        # 「費半收漲 → 台股電子開盤定價」每一步都合法,而整條鏈沒有
        # 引用任何一個行情數字 —— 讀者無從判斷這個傳導是 0.3% 還是 3%。
        # 高重要性事件的鏈至少要有一步錨在 `market:` / `derived:` /
        # `valuation:` / `prediction:` 的數字上。判準是結構性的
        # (查引用的命名空間),不是關鍵詞。
        if n.get("materiality") == "high" and steps:
            import analysis_stages as _ast
            _reg = _registry_of(packet)
            # 第二十二輪 P2-1:**帶主體的錨點要在這一段的範圍裡** ——
            # 講台積電的鏈不能靠鴻海的漲跌當錨點。
            _subj = {str(a.get("asset_id")) for a in
                     (n.get("affected_assets") or []) if isinstance(a, dict)}
            anchored = any(
                _ast.is_numeric_anchor(e, n.get("source_item_id"), _reg,
                                       subjects=_subj)
                for st in steps for e in (st.get("evidence_ids") or []))
            if not anchored:
                out.append(
                    f"{where} 的因果鏈沒有任何一步引用行情或衍生數字 —— "
                    "至少把一步錨在具體數字上 —— 行情用 market:,"
                    "新聞裡的數字用 fact:(逐則列在 numeric_facts)")
    cms = obj.get("cross_market_synthesis")
    if isinstance(cms, dict):
        # 深度加強(橫向,2026-08-05):**只靠新聞的橫向綜合是轉述,
        # 不是綜合。** 橫向的原料是行情之間的張力與同向(Python 已經
        # 算好放在 packet 裡),綜合段的證據若一個 `market:` / `tension:` /
        # `derived:` 都沒有,它大概率只是把幾則新聞再說一次。
        cited = ([str(e) for e in (cms.get("evidence_ids") or [])]
                 + [str(e) for r in (cms.get("tension_resolutions") or [])
                    if isinstance(r, dict)
                    for e in (r.get("evidence_ids") or [])]
                 + [str(e) for r in (cms.get("alignment_readings") or [])
                    if isinstance(r, dict)
                    for e in (r.get("evidence_ids") or [])])
        if cited and not any(e.startswith(("market:", "tension:", "derived:"))
                             for e in cited):
            out.append(
                "cross_market_synthesis 的證據全是新聞 —— 橫向綜合的原料"
                "是行情之間的張力與同向(EVIDENCE 的 signal_tensions),"
                "沒有接上任何一個行情數字的綜合只是新聞轉述")
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


def _claim_fingerprint(c) -> str:
    """一條主張的**全部判斷內容**。ID 不變而內容換掉,是換一份報告。"""
    return ":".join(str(c.get(k) or "") for k in (
        "claim_id", "statement", "claim_type", "direction", "materiality",
        # 第二十一輪 P1-8:`confidence` 先前不在身分裡 ——
        # 0.9 → 0.2 而其餘不變時,第二版照樣可以勝出。
        "confidence", "horizon", "falsification_trigger")) + ":" + ",".join(
        sorted(map(str, c.get("evidence_ids") or []))) + ":" + ",".join(
        sorted(map(str, c.get("asset_scope") or [])))


def _claim_sections(o):
    import claim_map as _cm
    return _cm.section_claim_mappings(o)


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
        # **反證要綁在自己那條 claim 上。** 先前是全域集合,於是
        # 反證可以在 claim A、B 之間互換而集合完全相同。
        "反面證據": {f"{c.get('claim_id')}:{x}"
                 for c in (o.get("claim_audit") or []) if isinstance(c, dict)
                 for x in (c.get("counterevidence_ids") or [])},
        "資料缺口": {str((g or {}).get("what_is_missing") or "")
                 for g in (o.get("data_gaps") or []) if isinstance(g, dict)},
        # 第十九輪 P1-11:**第二版可以「更深」而同時刪掉橫向與逐標的。**
        # 先前只保護新聞、張力、反證、缺口四個集合,於是「多一個財務層
        # 步驟、刪掉台積電與指數的差異分析、刪掉全部同向解讀、刪掉
        # claim 回指」會因為鏈變長而勝出 —— 加深反而讓信變淺。
        # 第二十輪 P1-4:**只保名字保不住結論。** `n1:2330` 不變而方向
        # bullish→bearish、量級 moderate→negligible —— ID 集合完全相同,
        # 第二版照樣勝出。**加深是把同一個判斷說得更清楚,不是換判斷**,
        # 所以身分要含方向/量級/時間 —— 改任何一格都是換了一個結論。
        # 第二十一輪 P1-8:`second_order_effect` 也會渲染進信 ——
        # 改成相反方向而其餘不變時,先前完全看不出來。
        "拆過的標的": {f"{n.get('source_item_id')}:{a.get('asset_id')}:"
                  f"{a.get('direction')}:{a.get('magnitude_band')}:"
                  f"{a.get('horizon')}:{a.get('first_order_effect')}:"
                  f"{a.get('second_order_effect')}:"
                  f"{','.join(sorted(map(str, a.get('evidence_ids') or [])))}"
                  for n in news for a in (n.get("affected_assets") or [])
                  if isinstance(a, dict) and a.get("asset_id")},
        "解讀過的同向訊號": {f"{r.get('alignment_id')}:{r.get('interpretation')}:"
                     f"{r.get('marginal_information')}:"
                     f"{r.get('double_count_risk')}:"
                     f"{','.join(sorted(map(str, r.get('evidence_ids') or [])))}"
                     for r in (cms.get("alignment_readings") or [])
                     if isinstance(r, dict)},
        # 矛盾那側同理:哪一側可信、憑什麼分出勝負、靠什麼證據 ——
        # 全部換掉而 tension_id 不變時,先前完全看不出來。
        "調和過的張力": {f"{r.get('tension_id')}:{r.get('dominant_side')}:"
                   f"{r.get('resolution')}:{r.get('decision_rule')}:"
                   f"{','.join(sorted(map(str, r.get('evidence_ids') or [])))}"
                   for r in (cms.get("tension_resolutions") or [])
                   if isinstance(r, dict)},
        # 同理:claim 的身分含**內容**。第二十輪 P1-4:先前只含
        # 本文/尺度/範圍 —— 於是 `direction` bullish→bearish、
        # `evidence_ids` 換成另一個合法但不相關的 ID、反證整組搬到
        # 另一條 claim 上,身分集合完全不變。**加深是把同一個判斷說得
        # 更清楚,不是換一個判斷。**
        "稽核過的主張": {_claim_fingerprint(c)
                   for c in (o.get("claim_audit") or [])
                   if isinstance(c, dict) and c.get("claim_id")},
        # 第二十輪 P2-5:**從 `claim_map` 長出來。** 先前寫死三段 + 手動補
        # 總結,於是 schema 新增的 scenario / watch / key_driver 回指
        # 可以被整批換掉而不被發現 —— 而寫死的清單漂移一次就再也對不回來。
        "各段的回指": {f"{sec}:{cid}" for sec, ids in
                  _claim_sections(o).items() for cid in ids},
        "因果步驟的證據": {f"{n.get('source_item_id')}:{e}"
                    for n in news for st in (n.get("mechanism_steps") or [])
                    if isinstance(st, dict)
                    for e in (st.get("evidence_ids") or [])},
        "條目之間的關係": {f"{n.get('source_item_id')}→"
                    f"{r.get('other_source_item_id')}"
                    for n in news for r in (n.get("relates_to") or [])
                    if isinstance(r, dict)},
    }


#: **每則新聞自己的身分**(第十八輪 P1-11)。上一版只比新聞 ID 的集合,
#: 於是這個交換完全合法:
#:
#:     第一版:n1 = high 但鏈很淺、n2 = medium 但鏈很深
#:     第二版:n1 = medium 仍然很淺、n2 = high 已經很深
#:
#: 新聞 ID 集合不變、high 的**數量**不變、深度提示還會變少 ——
#: 而真正被要求加深的 n1 是**靠降級逃掉的**。深度要求不能用重新分類繞過。
_MATERIALITY_RANK = {"low": 0, "medium": 1, "high": 2}

#: 說得出來的東西**不得在加深後說不出來**。這些欄位由有變無,是實質退步,
#: 而它會讓報告看起來更乾淨(少了一堆但書)—— 最難察覺的那種。
#: 第二十一輪 P1-8:`source_caveat`(單一來源的保留事項)與
#: `why_it_matters` 都會渲染進信,先前不在保護範圍內。
_NEWS_KEPT = ("horizon", "confirmation_signal", "invalidation_signal",
              "why_this_magnitude", "source_caveat", "why_it_matters")

#: 立場信心的單次漂移上限。加深是把同一個判斷說得更清楚,不是換一個判斷 ——
#: 0.35 → 0.95 不可能是「補了幾條因果鏈」帶來的。**本模組自訂。**
_CONFIDENCE_DRIFT = 0.25

#: 佐證等級由弱到強。加深**不得往上調** —— 讓讀者高估可信度。
_CORROBORATION_RANK = {"unverified": 0, "single_source": 1,
                       "multi_source": 2, "official": 3}


def _news_identity(obj) -> dict:
    """`{source_item_id: {重要性, 量級已知, 說得出來的欄位}}`。"""
    out = {}
    for n in ((obj or {}).get("top_news_analysis") or []):
        if not isinstance(n, dict):
            continue
        out[str(n.get("source_item_id") or "")] = {
            "materiality": str(n.get("materiality") or ""),
            "direction": str(n.get("direction") or ""),
            "corroboration": str(n.get("corroboration_assessment") or ""),
            "magnitude_known": n.get("magnitude_band") not in (None, "", "unknown"),
            "said": {k for k in _NEWS_KEPT if str(n.get(k) or "").strip()},
        }
    return out


def news_regressions(before, after) -> list:
    """第二版在**個別新聞**上的退步(集合層看不見的那種)。"""
    ib, ia = _news_identity(before), _news_identity(after)
    bad = []
    for sid, b in ib.items():
        a = ia.get(sid)
        if a is None:            # 整則不見 —— 由集合層的「弄丟新聞」負責
            continue
        # 佐證等級不得被往上調(那會讓讀者高估可信度)。
        if b.get("corroboration") and a.get("corroboration") and                 _CORROBORATION_RANK.get(a["corroboration"], 0) >                 _CORROBORATION_RANK.get(b["corroboration"], 0):
            bad.append(f"{sid} 的佐證等級被調高("
                       f"{b['corroboration']} → {a['corroboration']})")
        if b.get("direction") and a.get("direction")                 and b["direction"] != a["direction"]:
            bad.append(f"{sid} 的方向被改掉({b['direction']} → {a['direction']})"
                       " —— 加深不該改判斷")
        rb = _MATERIALITY_RANK.get(b["materiality"], -1)
        ra = _MATERIALITY_RANK.get(a["materiality"], -1)
        if rb >= 0 and ra >= 0 and ra < rb:
            bad.append(f"{sid} 的重要性被降級({b['materiality']} → "
                       f"{a['materiality']})—— 深度要求不得用重新分類繞過")
        if b["magnitude_known"] and not a["magnitude_known"]:
            bad.append(f"{sid} 的量級從說得出來變成 unknown")
        lost = b["said"] - a["said"]
        if lost:
            bad.append(f"{sid} 不再說得出 {sorted(lost)}")
    return bad


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
    # **選優的判準要與觸發加深的判準是同一套**(第二十二輪 P2-1 順帶抓到)。
    # 上面三行剛講完「傳 packet 不是 ids」,而下面這一行自己沒傳 ——
    # 於是觸發加深的是 `depth_advisories(obj, packet)`(含錨點、橫向這些
    # packet-aware 提示),而選優數的是不含它們的那一套。第二版**剛好把
    # packet-aware 的那幾條修好**時,盲測的數量沒有變少 → 判定沒有改善 →
    # 把真正的改善丟掉,沿用第一版。
    adv_b = depth_advisories(before, evidence_ids)
    adv_a = depth_advisories(after, evidence_ids)
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
    for msg in news_regressions(before, after):
        return False, msg
    cb = ((before or {}).get("stance") or {}).get("confidence")
    ca = ((after or {}).get("stance") or {}).get("confidence")
    if isinstance(cb, (int, float)) and isinstance(ca, (int, float))             and abs(float(ca) - float(cb)) > _CONFIDENCE_DRIFT:
        return False, (f"立場信心漂移過大({cb} → {ca})—— 加深是把同一個"
                       "判斷說得更清楚,不是換一個判斷")
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


# ---------------------------------------------------------------- 相容出口
#
# 階段/指標搬到 `analysis_stages`(見該檔:**後果不同**)。呼叫端仍可從
# 這裡取用,一次只改一件事。
from analysis_stages import (                     # noqa: E402,F401
    both_sides_cited, depth_metrics, incomplete_chains)
