# -*- coding: utf-8 -*-
"""**Luna 的 strict JSON → 晨報 Markdown**(確定性 renderer)。

## 為什麼需要它

主分析的介面契約是「回一段 Markdown」,而下游有一整套既有的後處理靠**段落
標題**辨識內容(`_extract_stance` 找「我的明確立場」、`_extract_summary` 找
「一句話總結」、`_strip_llm_sections` 移除已上移到結論卡的段落)。

Luna 走 strict Structured Outputs,拿到的是 JSON。**模型不直接控制信件排版**
—— 那是刻意的:排版由程式決定,模型只負責判斷內容。這個模組就是那一刀。

## 驗收條件不是「看起來像」

`morning_report._analysis_complete_enough` 是既有的截斷偵測器:少了立場或
總結、或立場解析不出來,頂部 KPI 與結論卡會變成「—」。所以本模組的測試
直接拿那個函式當斷言 —— 那才是「這份 Markdown 真的能用」的定義,
而不是我自己覺得格式對。

## 段落標題必須沿用既有詞彙

`八、科技板塊脈動` / `九、其他類股資訊` / `七之二、世界大事速覽` 等字串在
主模組裡是常數,後處理靠它們切段。自創標題不會有錯誤訊息,只會讓那些段落
在信裡消失。
"""
from __future__ import annotations

from typing import Optional

from analysis_render_depth import _news_line, _synthesis

RENDER_SCHEMA_VERSION = 1

#: 這些標題**必須與主模組的常數一致**。改一個字,對應段落就會在信裡消失
#: 而且沒有任何錯誤 —— 由測試比對主模組的 `_SECTION_*`。
SECTION_TOP3 = "七、昨夜三大重點"
#: 第十五輪 P1-3:**段落名要說實話。**
#:
#: 舊的映射把 `global_market`(美股→台股連動)放進「世界大事速覽」——
#: 那一段在 legacy 契約裡的定義是**股市之外的世界**;把整個
#: `top_news_analysis` 無條件放進「科技板塊脈動」,即使那則新聞是金融、
#: 航運或生技;把 `taiwan_market.taiex_view` / `tsmc_view` 放進
#: 「**其他**類股資訊」——台積電是最不「其他」的那一檔。
#:
#: 這不是排版問題。Luna 特化路徑跑成的那一天,收件人會看到
#: 「世界大事速覽」裡寫美股連動、「其他類股資訊」裡只有台積電 ——
#: 而信看起來仍然完整,沒有任何錯誤訊息。
#: Luna 的 schema **沒有**「股市之外的世界」這種欄位,所以正確的做法是
#: **不要宣稱有**,而不是找一個欄位塞進去。
SECTION_GLOBAL = "七之二、全球市場與美股台股連動"
SECTION_NEWS = "八、重點新聞分析"
SECTION_TW = "九、台股與台積電"
SECTION_PRICED = "已被市場反映 vs 尚未反映"
#: schema v2:橫向綜合。**排在最前面** —— 使用者要的是「這些訊號合起來
#: 說什麼」,逐條看完再自己拼是他反映了三次的那個問題。
SECTION_SYNTHESIS = "七之一、今日訊號的橫向綜合"
SECTION_STANCE = "我的明確立場"
SECTION_SUMMARY = "一句話總結"


def _s(v) -> str:
    return str(v or "").strip()


def _lines(items, fmt) -> list:
    out = []
    for it in (items or []):
        if isinstance(it, dict):
            text = fmt(it)
            if text:
                out.append(f"- {text}")
    return out


def _claim_line(c: dict) -> str:
    """一條 claim 的行文。**型別與信心一起寫出來** —— 那是這份報告與
    「看起來很確定的散文」的唯一差別。"""
    body = _s(c.get("statement"))
    if not body:
        return ""
    bits = []
    kind = _s(c.get("claim_type"))
    if kind and kind != "fact":
        bits.append({"inference": "推論", "scenario": "情境",
                     "unknown": "資料不足"}.get(kind, kind))
    conf = c.get("confidence")
    if isinstance(conf, (int, float)):
        bits.append(f"信心 {round(float(conf) * 100)}%")
    horizon = _s(c.get("horizon"))
    if horizon:
        bits.append(horizon)
    # 第十五輪 P1-2:**有反面證據要看得見。** schema 收了 `counterevidence_ids`,
    # 而渲染層先前整個丟掉 —— 於是一條「有人持相反看法」的判斷,
    # 讀起來與一面倒的判斷一模一樣。
    if [x for x in (c.get("counterevidence_ids") or []) if _s(x)]:
        bits.append("有反面證據")
    line = body + (f"（{'、'.join(bits)}）" if bits else "")
    # **失效條件先前也被丟掉。** schema 把它列為必填,理由寫在測試裡:
    # 「說不出什麼情況我就錯了的判斷,事後無法評分」。既然要求了就要顯示,
    # 否則那個必填只保護了 JSON,沒有保護讀者。
    trigger = _s(c.get("falsification_trigger"))
    return line + (f"\n  - 什麼情況代表這個判斷錯了:{trigger}" if trigger else "")


def render(obj: Optional[dict], packet=None) -> str:
    """把驗證過的分析 JSON 轉成晨報 Markdown。

    **無法渲染時回空字串**,不回半份。呼叫端會據此走既有的降級路徑 ——
    回半份的症狀是「信寄出去了但少了一半」,那比沒寄更難發現。
    """
    if not isinstance(obj, dict):
        return ""
    stance = obj.get("stance") if isinstance(obj.get("stance"), dict) else {}
    label = _s(stance.get("label"))
    summary = _s(obj.get("executive_summary"))
    if not label or not summary:
        # 這兩個是既有後處理的硬需求(缺了頂部 KPI 會變「—」)。
        return ""

    parts: list = []

    # 七、昨夜三大重點 —— 取 materiality 最高的驅動因子
    drivers = [c for c in (obj.get("key_drivers") or []) if isinstance(c, dict)]
    order = {"high": 0, "medium": 1, "low": 2}
    drivers.sort(key=lambda c: order.get(_s(c.get("materiality")), 3))
    top3 = _lines(drivers[:3], _claim_line)
    if top3:
        parts.append(f"## {SECTION_TOP3}\n" + "\n".join(top3))

    # 橫向綜合**排在最前面**:使用者要的是「今天這些訊號合起來說什麼」,
    # 而不是逐條看完再自己拼。
    syn = _synthesis(obj.get("cross_market_synthesis"), packet)
    if syn:
        parts.append(f"## {SECTION_SYNTHESIS}\n" + syn)

    gm = obj.get("global_market") if isinstance(obj.get("global_market"), dict) else {}
    glob = [x for x in (_s(gm.get("summary")), _s(gm.get("us_to_tw_linkage"))) if x]
    if glob:
        parts.append(f"## {SECTION_GLOBAL}\n" + "\n".join(f"- {w}" for w in glob))

    news = _lines(obj.get("top_news_analysis"), _news_line)
    if news:
        # 第十八輪 P1-9:走到財務層是 advisory,加深失敗就照原樣寄出 ——
        # 那對「晨報不可斷」是合理的,對收件人卻是隱瞞:他不知道這條
        # 標成「重大」的事件其實只推到情緒。**不擋,但要說出來。**
        import analysis_stages as _ast
        stub = _ast.incomplete_chains(obj)
        if stub:
            # 第十九輪 P2-2:先前只印前三則,**其餘靜默消失** ——
            # 八則裡有五則停在情緒時,讀者只看到三則,而「還有幾則」
            # 正是他判斷這封信可不可信的關鍵。
            news.append("- *傳導未完成:" + "、".join(
                f"{sid} {why}" for sid, why in stub[:3])
                + (f";另有 {len(stub) - 3} 則同樣未完成"
                   if len(stub) > 3 else "")
                + " —— 這幾則的影響幅度本報無法確認。*")
        # 第十九輪 P1-5:**看過而決定不談,讀者有權知道。**
        # 不顯示的話,「沒發生」與「發生了但本報判斷不重要」長得一樣。
        skipped = [d for d in (obj.get("dismissed_events") or [])
                   if isinstance(d, dict) and _s(d.get("why_not_material"))]
        if skipped:
            news.append("- *今日看過但未展開:" + "、".join(
                f"{_s(d.get('cluster_id'))}({_s(d.get('why_not_material'))})"
                for d in skipped[:4]) + "*")
        parts.append(f"## {SECTION_NEWS}\n" + "\n".join(news))

    # 台股與台積電。`summary` 是台股整體、兩個 view 是細部,**同一段**裡
    # 由粗到細 —— 先前 summary 被丟進「台灣本地動態」(那一段講的是
    # 證交所新制、勞動基金這類在地消息),兩者不是同一件事。
    tw = obj.get("taiwan_market") if isinstance(obj.get("taiwan_market"), dict) else {}
    tw_lines = [x for x in (_s(tw.get("summary")), _s(tw.get("taiex_view")),
                            _s(tw.get("tsmc_view"))) if x]
    if tw_lines:
        parts.append(f"## {SECTION_TW}\n" + "\n".join(f"- {o}" for o in tw_lines))

    # **`priced_in` 先前整段沒有被渲染。** 它是這份 schema 裡最像分析的欄位
    # (「哪些已經在價格裡、哪些還沒」),模型產出了、驗證器檢查了,
    # 而收件人從來沒看到 —— 這正好是使用者反映「只有數據沒有分析」的一部分。
    pi = obj.get("priced_in") if isinstance(obj.get("priced_in"), dict) else {}
    done = [_s(x) for x in (pi.get("already_reflected") or []) if _s(x)]
    todo = [_s(x) for x in (pi.get("not_yet_reflected") or []) if _s(x)]
    if done or todo:
        blk = []
        if done:
            blk.append("- **已被市場反映**:" + "、".join(done[:4]))
        if todo:
            blk.append("- **尚未反映**:" + "、".join(todo[:4]))
        parts.append(f"## {SECTION_PRICED}\n" + "\n".join(blk))

    # 情境樹與觀察點:這是 Luna 特化相對於既有散文的實質增量
    tree = obj.get("scenario_tree") if isinstance(obj.get("scenario_tree"), dict) else {}
    scen = []
    for key, name in (("base", "基準"), ("bull", "偏多"), ("bear", "偏空")):
        blk = tree.get(key)
        if isinstance(blk, dict) and _s(blk.get("narrative")):
            # r2(Codex,#5):**數字完全不進信件。** 標明「模型主觀機率」
            # 仍不滿足這個 repo 的不變式 —— 信裡出現的數字必須是 Python 算的,
            # 而情境機率沒有任何 Python 來源。它留在 JSON 裡供指標與事後判讀,
            # 但收件人看到的只有情境敘述本身。
            # 第十九輪 P2-1:**觸發條件先前整個被丟掉。** 機率不進信是
            # 既有不變式(信裡的數字必須是 Python 算的,而情境機率沒有
            # 任何 Python 來源)—— 但 `triggers` 不是數字,它是可觀察的
            # 條件,而「什麼情況會讓這個情境成立」正是情境樹的用處。
            # 只印敘述等於把三段散文並排,讀者無從判斷該盯什麼。
            trig = [_s(x) for x in (blk.get("triggers") or []) if _s(x)]
            line = f"- **{name}**:{_s(blk.get('narrative'))}"
            if trig:
                line += "\n  - 什麼情況代表它成立:" + "、".join(trig[:3])
            scen.append(line)
    if scen:
        parts.append("## 情境與觸發條件\n" + "\n".join(scen))

    gaps = _lines(obj.get("data_gaps"),
                  lambda g: f"{_s(g.get('what_is_missing'))}"
                            f"{'——' + _s(g.get('impact_on_conclusions')) if _s(g.get('impact_on_conclusions')) else ''}")
    if gaps:
        # **資料缺口要出現在信裡。** 只記在 manifest 等於沒有揭露:
        # 收件人看到的是一份看起來完整的報告。
        parts.append("## 資料缺口\n" + "\n".join(gaps))

    contra = _lines(obj.get("contradictions"),
                    lambda c: f"{_s(c.get('topic'))}:{_s(c.get('resolution'))}")
    if contra:
        parts.append("## 證據衝突與調和\n" + "\n".join(contra))

    watch = _lines(obj.get("watch_triggers"),
                   lambda w: f"{_s(w.get('trigger'))}"
                             f"{'（' + _s(w.get('why')) + '）' if _s(w.get('why')) else ''}")
    if watch:
        parts.append("## 觀察觸發點\n" + "\n".join(watch))

    # 我的明確立場 —— 格式必須讓 `_extract_stance` 解析得出來
    stance_lines = [f"立場：{label}"]
    score = stance.get("score")
    if isinstance(score, int):
        stance_lines.append(f"淨分 {score:+d}")
    if _s(stance.get("rationale")):
        stance_lines.append(_s(stance.get("rationale")))
    pf = (obj.get("portfolio_implications")
          if isinstance(obj.get("portfolio_implications"), dict) else {})
    if _s(pf.get("summary")):
        stance_lines.append(_s(pf.get("summary")))
    # `actions_to_consider` 先前沒有被渲染 —— 模型寫了、驗證了、沒人看得到。
    for a in (pf.get("actions_to_consider") or [])[:3]:
        if _s(a):
            stance_lines.append(f"可考慮的做法:{_s(a)}")
    for r in (pf.get("risks") or [])[:3]:
        if _s(r):
            stance_lines.append(f"風險:{_s(r)}")
    inval = [_s(t) for t in (tree.get("invalidation_triggers") or []) if _s(t)]
    if inval:
        stance_lines.append("失效條件:" + "、".join(inval[:3]))
    parts.append(f"## {SECTION_STANCE}\n" + "\n".join(stance_lines))

    parts.append(f"## {SECTION_SUMMARY}\n{summary}")
    return "\n\n".join(parts)
