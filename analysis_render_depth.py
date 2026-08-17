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


def _chain_line(chain: list) -> str:
    """因果鏈壓成一行:`A → B → C`。

    **鏈斷掉時不假裝連續**:下一步的起點不等於上一步的終點,就用
    `；` 分段。用一條箭頭串起兩件不相干的事,是這份報告最該避免的
    那種句子(通道與 fact/推論 標記拿掉了 —— 那些括號正是把一句話
    撐成三行的東西,欄位本身仍在 schema 裡被驗證)。
    """
    segs, nodes = [], []
    for st in (chain or []):
        a, b = _s(st.get("from_what")), _s(st.get("to_what"))
        if not a or not b:
            continue
        if not nodes:
            nodes = [a, b]
        elif nodes[-1] == a:
            nodes.append(b)
        else:
            segs.append(nodes)
            nodes = [a, b]
    if nodes:
        segs.append(nodes)
    return "；".join(" → ".join(seg) for seg in segs)


def _news_line(n: dict, packet=None) -> str:
    """一則新聞的分析。**schema v2 的深度要真的排進信裡。**

    v1 只印 `why_it_matters` —— 於是即使模型填好了因果鏈、量級與關係,
    收件人看到的仍然只有一句話。**渲染層丟資料時,schema 再深也沒用。**
    """
    body = _s(n.get("why_it_matters"))
    if not body:
        return ""
    # **2026-08-17 使用者定案:敘事為主,機制鏈與失效條件各留一行。**
    # 特化路徑第一次在生產成功那天,使用者的回饋是「敘述方式變成這樣,
    # 原本的還比較好」—— 一則新聞底下曾經排出五到六個標籤行(怎麼傳導 /
    # 量級 / 成立要看到 / 來源評註 / 逐標的影響 / 與另一則的關係),
    # 讀起來像表單不像文章。
    # 收起來的欄位**仍在 schema 裡被要求與驗證**(模型還是得寫、還是得
    # 通過引用檢查),只是不再排進讀者的視線:量級與理由、確認訊號、
    # 與另一則的關係(橫向綜合那一段已經在講關係了)。
    # 留下的三樣是舊版沒有、而且看得懂就能用的:傳導鏈、什麼會推翻它、
    # 逐標的影響。
    out = [body]
    chain = [st for st in (n.get("mechanism_steps") or []) if isinstance(st, dict)]
    line = _chain_line(chain)
    if line:
        out.append("  - 傳導:" + line)
    inval = _s(n.get("invalidation_signal"))
    if inval:
        out.append(f"  - 什麼會推翻它:{inval}")
    # 佐證等級收成一句話的尾巴(第二十輪 P2-7 的理由不變:「沒發生」與
    # 「只有一家說」不能長得一樣)—— 但不再自己佔一行。
    _CORR = {"single_source": "單一來源", "unverified": "未證實"}
    lvl = _CORR.get(_s(n.get("corroboration_assessment")))
    if lvl:
        out[0] = out[0] + f"（{lvl}）"
    out.extend(_assets(n, packet))
    return "\n".join(out)


#: 逐資產影響的印法。**方向詞單獨出現就是使用者抱怨的那種句子**,
#: 所以一定帶著幅度與時間一起排。
_DIR = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}
_BAND = {"negligible": "可忽略", "small": "小", "moderate": "中等",
         "large": "大", "unknown": "說不出量級"}


def _assets(n: dict, packet=None) -> list:
    rows = []
    for a in (n.get("affected_assets") or []):
        if not isinstance(a, dict) or not _s(a.get("asset_id")):
            continue
        # **兩層傳導要分得開**(第三十二輪 P1-3,選項 B):只靠當日
        # universe 放行的標的,讀者要能自行折價 —— universe 證明它是
        # 真股票,證明不了這件事真的會傳導到它。判準回 validator 問
        # (`speculative_transmission`),不在渲染層自己判一份。
        _spec = ""
        try:
            import analysis_validate as _av9
            if _av9.speculative_transmission(_s(a.get("asset_id")), n, packet):
                _spec = "〔推測性傳導,未宣告的供應鏈關係〕"
        except Exception:               # noqa: BLE001 - 標籤失敗不毀渲染
            _spec = ""
        # **2026-08-17:方向與幅度收進一個括號**,不再用「、」把
        # 方向/幅度/時間窗串成一串標籤(使用者:讀起來像表單)。
        tag = "、".join(x for x in (
            _BAND.get(_s(a.get("magnitude_band")), ""),
            _s(a.get("horizon"))) if x)
        head = (f"{_s(a.get('asset_id'))} "
                f"{_DIR.get(_s(a.get('direction')), '')}"
                + (f"（{tag}）" if tag else "") + _spec)
        # 兩段影響是**兩句話**,先前用「、」黏起來會接出「。、」
        # (2026-08-17 生產信裡看得到)。
        body = "".join(_join_sentence(x) for x in
                       (_s(a.get("first_order_effect")),
                        _s(a.get("second_order_effect"))) if x)
        rows.append(f"  - {head}:{body}" if body else f"  - {head}")
    return rows


def _join_sentence(text: str) -> str:
    """接成句子:自己有句末標點就不再補一個。"""
    t = str(text or "").strip()
    return t if (not t or t[-1] in "。！？;;") else t + "。"


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
    # Commit E:**共用底層驅動的說明要進信。** 「三個獨立訊號同向」與
    # 「同一件事的三個表現」對讀者是完全不同的訊息 —— schema 收了、
    # 驗證器擋了,而先前渲染層一個字都沒印。
    for x in (cms.get("shared_driver_notes") or []):
        if not isinstance(x, dict):
            continue
        why = _s(x.get("why_not_double_counted"))
        cids = [_s(c) for c in (x.get("cluster_ids") or []) if _s(c)]
        if why and len(cids) >= 2:
            rows.append(f"- **這 {len(cids)} 件事共用同一個驅動**,"
                        f"不重複計權:{why}")
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
    # 第十八輪 P1-7:同向訊號的解讀也要進信 —— 只印矛盾的話,
    # 「兩個訊號其實是同一個底層驅動」這種話讀者永遠看不到。
    for r in (cms.get("alignment_readings") or []):
        if not isinstance(r, dict) or not _s(r.get("interpretation")):
            continue
        head = _tension_head(_s(r.get("alignment_id")), packet)
        if head:
            rows.append(f"  - {head}")
        extra = _s(r.get("marginal_information"))
        risk = _s(r.get("double_count_risk"))
        rows.append(f"  - **同向訊號**:{_s(r.get('interpretation'))}"
                    + (f";增量資訊:{extra}" if extra else "")
                    + (f"。會不會重複計算:{risk}" if risk else ""))
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
