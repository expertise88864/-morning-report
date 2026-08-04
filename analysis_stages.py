# -*- coding: utf-8 -*-
"""**因果鏈的階段:走到哪一層、順序對不對、有沒有走完**(第十八輪拆出)。

從 `analysis_depth` 拆出來的理由不是行數,是**兩種後果不同**:

  * `analysis_depth` 決定**要不要再跑一次**(深度提示)與**採不採用第二版**
    (選優)—— 它的錯誤代價是多花一次呼叫,或留下較差的版本。
  * 這個模組回答**這條鏈本身長什麼樣**,而答案會直接寫進信裡
    (傳導未完成的揭露)與帳本(十配對要用的深度指標)——
    它的錯誤代價是收件人讀到一個假的完整度。

第十八輪 P1-10 就是後者的例子:先前只看 stage **集合**,於是
「事件→股價上漲(price)」接「股價上漲→稼動率提升(operations)」
被算成「營運與財務都到了」—— 文字連續,因果倒著走。
"""
from __future__ import annotations



def _stage_seq(n) -> list:
    """這條鏈依序走過的階段索引(沒標 stage 的步驟不參與順序判斷)。"""
    import analysis_schema as _sch
    order = {name: i for i, name in enumerate(_sch.CHAIN_STAGES)}
    return [order[str(st.get("stage") or "")]
            for st in (n.get("mechanism_steps") or [])
            if isinstance(st, dict) and str(st.get("stage") or "") in order]


def _terminal_index(n):
    """第一個走到財務/估值/股價的位置;沒走到回 None。"""
    import analysis_schema as _sch
    term = {i for i, name in enumerate(_sch.CHAIN_STAGES)
            if name in _sch.TERMINAL_STAGES}
    for pos, idx in enumerate(_stage_seq(n)):
        if idx in term:
            return pos
    return None


def _ordered_chain(n) -> bool:
    """**營運層出現在財務層之前**才算真的把傳導講完。"""
    import analysis_schema as _sch
    ops = {i for i, name in enumerate(_sch.CHAIN_STAGES)
           if name in _sch.OPERATIONAL_STAGES}
    seq = _stage_seq(n)
    t = _terminal_index(n)
    return t is not None and any(idx in ops for idx in seq[:t])


def _stage_order_broken(n) -> bool:
    """階段索引倒退 —— 「股價上漲 → 稼動率提升」這種倒著走的鏈。

    **只回報,不擋**:偶發的回頭(價格反饋到情緒)是真實存在的,
    而把它做成硬性失敗會逼模型改標 stage 來過關。
    """
    seq = _stage_seq(n)
    return any(b < a for a, b in zip(seq, seq[1:]))


def _required_ids(packet):
    if not isinstance(packet, dict):
        return set()
    import signal_tensions as _st
    return _st.required_tension_ids(packet.get("signal_tensions"))


def both_sides_cited(r, packet) -> bool:
    """這筆調和有沒有引用**該張力本身或它的兩側**(第十八輪 P1-5)。"""
    if not isinstance(packet, dict) or not isinstance(r, dict):
        return False
    import signal_tensions as _st
    tid = str(r.get("tension_id") or "")
    sides = _st.sides_evidence(packet.get("signal_tensions")).get(tid)
    cited = {str(x) for x in (r.get("evidence_ids") or [])}
    if sides is None:
        return False
    left, right = sides
    return tid in cited or bool((cited & left) and (cited & right))


def incomplete_chains(obj) -> list:
    """**高重要性事件的傳導沒走完** —— 回 `[(新聞ID, 缺什麼)]`。

    第十八輪 P1-9:走到財務層先前**只是 advisory**。維持不擋信是對的
    (淺而正確落回 legacy 只會更淺),但「加深失敗之後照樣寄出、
    而收件人不知道那條鏈停在情緒」不是 resilience,是隱瞞。
    折衷:不擋,但**渲染時說出來**。
    """
    import analysis_schema as _sch
    out = []
    for n in ((obj or {}).get("top_news_analysis") or []):
        if not isinstance(n, dict) or n.get("materiality") != "high":
            continue
        stages = {str(st.get("stage") or "")
                  for st in (n.get("mechanism_steps") or [])
                  if isinstance(st, dict)}
        sid = str(n.get("source_item_id") or "")
        if not stages & set(_sch.TERMINAL_STAGES):
            out.append((sid, "沒有推到營收、獲利、估值或股價"))
        elif not stages & set(_sch.OPERATIONAL_STAGES):
            out.append((sid, "沒有經過營運或產業供需這一層"))
        # 第十九輪 P1-10:**兩個判準先前不一致。** `depth_metrics` 用順序
        # (營運要在財務之前),而揭露只看 stage **集合** —— 於是
        # 「事件 → 股價上漲 → 稼動率提升 → 營收」兩層都出現、指標記為
        # 順序不成立、而信裡什麼都不說。把股價反應當成原因再倒推營運,
        # 是因果方向錯置,不是深度。同一個 evaluator,同一個答案。
        elif not _ordered_chain(n):
            out.append((sid, "因果順序不成立:營運層出現在財務/股價之後"))
    return out


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
        # 第十八輪 P1-10:先前只看 stage **集合**,於是
        # 「事件→股價上漲(price)」接「股價上漲→稼動率提升(operations)」
        # 會被算成「營運與財務都到了」—— 文字連續,因果倒著走。
        # 現在要求順序:營運層要出現在財務層**之前**。
        "reaches_financial": sum(1 for n in hi if _terminal_index(n) is not None),
        "reaches_operations": sum(
            1 for n in hi if _stages(n) & set(_sch.OPERATIONAL_STAGES)),
        "operations_then_financial": sum(1 for n in hi if _ordered_chain(n)),
        # **比例才跨得了日** —— 1/1 與 1/5 的計數相同而品質天差地遠。
        "reaches_financial_rate": (
            round(sum(1 for n in hi if _terminal_index(n) is not None) / len(hi), 3)
            if hi else None),
        "operations_then_financial_rate": (
            round(sum(1 for n in hi if _ordered_chain(n)) / len(hi), 3)
            if hi else None),
        "chains_out_of_order": sum(1 for n in hi if _stage_order_broken(n)),
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
        # 第十八輪 P1-6:先前是 `len(res)` —— 同一筆重複填三次會顯示
        # 「處理了 3 筆」而實際只處理 1 筆,覆蓋率因此**大於 100%**。
        "tensions_resolved": len({str(r.get("tension_id") or "") for r in res}
                                 & (_required_ids(packet) or set())),
        "duplicate_resolutions": len(res) - len(
            {str(r.get("tension_id") or "") for r in res}),
        "tension_coverage_rate": (
            round(len({str(r.get("tension_id") or "") for r in res}
                      & (_required_ids(packet) or set())) / need, 3)
            if need else None),
        "resolutions_grounded_both_sides": sum(
            1 for r in res if both_sides_cited(r, packet)),
        "resolutions_with_rule": sum(
            1 for r in res if str(r.get("decision_rule") or "").strip()),
        "priced_in_items": len((o.get("priced_in") or {}).get("already_reflected") or [])
        + len((o.get("priced_in") or {}).get("not_yet_reflected") or []),
        "data_gaps": len(o.get("data_gaps") or []),
    }
