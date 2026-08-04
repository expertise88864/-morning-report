# -*- coding: utf-8 -*-
"""**schema v2 深度欄位的渲染**(因果鏈/量級/關係/橫向綜合)。

從 `analysis_render` 拆出:那個檔管**整封信的組裝順序與段落語意**,
這裡管**單一條目要長什麼樣**。拆的直接原因是 schema v2 讓渲染多了
一百多行,而兩塊的變更理由不同 —— 段落順序跟著信件結構走,
條目寫法跟著 schema 版本走。

**渲染層丟資料時,schema 再深也沒用**(第十五輪 P1-2)—— 這裡的每個
函式都對應一組「模型填了、讀者要看得到」的欄位。
"""
from __future__ import annotations


def _s(v) -> str:
    return v.strip() if isinstance(v, str) else ""


#: 量級的中文。**`unknown` 不寫成「影響有限」** —— 那正好是使用者抱怨的
#: 那種形容詞;誠實的說法是「量級判斷不出來」,後面接缺什麼資料。
_BANDS = {"negligible": "量級可忽略", "small": "量級小", "moderate": "量級中等",
          "large": "量級大", "unknown": "量級判斷不出來"}
_RELS = {"reinforcing": "互相強化", "conflicting": "方向相反",
         "competing_for_same_capacity": "互相排擠(搶同一段產能)",
         "same_underlying_driver": "同一個底層驅動"}
#: 因果步驟的可信度。**沒有證據的推論要看得出來**,否則整條鏈讀起來像事實。
_STEP = {"fact": "", "inference": "(推論)", "scenario": "(情境)",
         "unknown": "(資料不足)"}


def _news_line(n: dict) -> str:
    """一則新聞的分析。**schema v2 的深度要真的排進信裡。**

    v1 只印 `why_it_matters` —— 於是即使模型填好了因果鏈、量級與關係,
    收件人看到的仍然只有一句話。**渲染層丟資料時,schema 再深也沒用。**
    """
    body = _s(n.get("why_it_matters"))
    if not body:
        return ""
    # `_lines` 會替第一行加 `- `,這裡不重複(否則變成「- - 」)。
    out = [body]
    chain = [st for st in (n.get("mechanism_steps") or []) if isinstance(st, dict)]
    hops = [f"{_s(st.get('from_what'))} → {_s(st.get('to_what'))}"
            f"（{_s(st.get('channel'))}{_STEP.get(_s(st.get('step_type')), '')}）"
            for st in chain if _s(st.get("from_what")) and _s(st.get("to_what"))]
    if hops:
        out.append("  - 怎麼傳導:" + " ／ ".join(hops))
    band = _BANDS.get(_s(n.get("magnitude_band")))
    why = _s(n.get("why_this_magnitude"))
    if band:
        out.append(f"  - {band}"
                   + (f"（{why}）" if why else "")
                   + (f",最快 {_s(n.get('horizon'))} 看得到"
                      if _s(n.get("horizon")) else ""))
    conf, inval = _s(n.get("confirmation_signal")), _s(n.get("invalidation_signal"))
    if conf or inval:
        bits = []
        if conf:
            bits.append(f"成立要看到:{conf}")
        if inval:
            bits.append(f"什麼會推翻它:{inval}")
        out.append("  - " + ";".join(bits))
    for rel in (n.get("relates_to") or []):
        if isinstance(rel, dict) and _RELS.get(_s(rel.get("relationship"))):
            out.append(f"  - 與另一則的關係:{_RELS[_s(rel.get('relationship'))]}"
                       + (f" —— {_s(rel.get('explanation'))}"
                          if _s(rel.get("explanation")) else ""))
    return "\n".join(out)


def _tension_head(tid: str, packet) -> str:
    """張力本身長什麼樣 —— **由 renderer 從 packet 回查,不讓模型重述數字**。

    第十八輪:信裡連著三個「矛盾調和:…(偏向前者)」,而讀者無從知道
    「前者」是 QQQ、是開盤預測、還是產業中位數。調和說得再好,
    看不出在調和什麼就等於沒說。
    """
    if not isinstance(packet, dict):
        return ""
    for it in ((packet.get("signal_tensions") or {}).get("items") or []):
        if not isinstance(it, dict) or f"tension:{it.get('tension_id')}" != tid:
            continue

        def _one(side):
            side = side if isinstance(side, dict) else {}
            v, u = side.get("value"), _s(side.get("unit"))
            num = (f"{v:+.2f}".rstrip("0").rstrip(".") if isinstance(v, float)
                   else f"{v:+}" if isinstance(v, int) else "")
            return f"{_s(side.get('label'))} {num}{'%' if u == '%' else ' ' + u}".strip()
        return (f"【{_s(it.get('topic'))}】{_one(it.get('left'))} ↔ "
                f"{_one(it.get('right'))}")
    return ""


def _synthesis(cms: dict, packet=None) -> str:
    """橫向綜合。**這是這次改版要的東西** —— 訊號之間的關係,
    而不是把各市場各寫一句。"""
    if not isinstance(cms, dict):
        return ""
    rows = []
    for key, name in (("reinforcing_signals", "互相強化"),
                      ("conflicting_signals", "互相抵銷")):
        vals = [_s(x) for x in (cms.get(key) or []) if _s(x)]
        if vals:
            rows.append(f"- **{name}**:" + "、".join(vals[:5]))
    if _s(cms.get("dominant_driver")):
        rows.append(f"- **今天的主導因子**:{_s(cms.get('dominant_driver'))}"
                    + (f" —— {_s(cms.get('why_it_dominates'))}"
                       if _s(cms.get("why_it_dominates")) else ""))
    for key, name in (("net_effect_intraday", "即日"),
                      ("net_effect_next_days", "未來 1–5 日")):
        if _s(cms.get(key)):
            rows.append(f"- **{name}**:{_s(cms.get(key))}")
    src = [_s(x) for x in (cms.get("funds_moving_from") or []) if _s(x)]
    dst = [_s(x) for x in (cms.get("funds_moving_to") or []) if _s(x)]
    if src or dst:
        rows.append("- **資金流向**:"
                    + ("、".join(src[:4]) if src else "(來源不明)")
                    + " → " + ("、".join(dst[:4]) if dst else "(去向不明)"))
    # 第十七輪 P1-3:**逐筆張力的調和要看得到。** 只印一句「訊號互有矛盾」
    # 等於沒有處理 —— 而那正是這個結構要取代的東西。
    for r in (cms.get("tension_resolutions") or []):
        if not isinstance(r, dict) or not _s(r.get("resolution")):
            continue
        side = {"left": "偏向前者", "right": "偏向後者",
                "neither": "兩邊都不夠強"}.get(_s(r.get("dominant_side")), "")
        head = _tension_head(_s(r.get("tension_id")), packet)
        if head:
            rows.append(f"  - {head}")
        rows.append(f"  - **矛盾調和**:{_s(r.get('resolution'))}"
                    + (f"({side})" if side else "")
                    + (f";{_s(r.get('why'))}" if _s(r.get("why")) else "")
                    + (f"。什麼情況分出勝負:{_s(r.get('decision_rule'))}"
                       if _s(r.get("decision_rule")) else ""))
    if _s(cms.get("what_would_flip_it")):
        rows.append(f"- **什麼會讓它翻盤**:{_s(cms.get('what_would_flip_it'))}")
    return "\n".join(rows)
