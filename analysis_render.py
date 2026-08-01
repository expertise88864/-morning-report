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

RENDER_SCHEMA_VERSION = 1

#: 這些標題**必須與主模組的常數一致**。改一個字,對應段落就會在信裡消失
#: 而且沒有任何錯誤 —— 由測試比對主模組的 `_SECTION_*`。
SECTION_TOP3 = "七、昨夜三大重點"
SECTION_WORLD = "七之二、世界大事速覽"
SECTION_TECH = "八、科技板塊脈動"
SECTION_OTHER = "九、其他類股資訊"
SECTION_LOCAL = "十、台灣本地動態"
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
    return body + (f"（{'、'.join(bits)}）" if bits else "")


def render(obj: Optional[dict]) -> str:
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

    gm = obj.get("global_market") if isinstance(obj.get("global_market"), dict) else {}
    world = [x for x in (_s(gm.get("summary")), _s(gm.get("us_to_tw_linkage"))) if x]
    if world:
        parts.append(f"## {SECTION_WORLD}\n" + "\n".join(f"- {w}" for w in world))

    news = _lines(obj.get("top_news_analysis"),
                  lambda n: _s(n.get("why_it_matters")))
    if news:
        parts.append(f"## {SECTION_TECH}\n" + "\n".join(news))

    tw = obj.get("taiwan_market") if isinstance(obj.get("taiwan_market"), dict) else {}
    other = [x for x in (_s(tw.get("taiex_view")), _s(tw.get("tsmc_view"))) if x]
    if other:
        parts.append(f"## {SECTION_OTHER}\n" + "\n".join(f"- {o}" for o in other))
    if _s(tw.get("summary")):
        parts.append(f"## {SECTION_LOCAL}\n- " + _s(tw.get("summary")))

    # 情境樹與觀察點:這是 Luna 特化相對於既有散文的實質增量
    tree = obj.get("scenario_tree") if isinstance(obj.get("scenario_tree"), dict) else {}
    scen = []
    for key, name in (("base", "基準"), ("bull", "偏多"), ("bear", "偏空")):
        blk = tree.get(key)
        if isinstance(blk, dict) and _s(blk.get("narrative")):
            p = blk.get("probability")
            pct = f"（{round(float(p) * 100)}%）" if isinstance(p, (int, float)) else ""
            scen.append(f"- **{name}{pct}**:{_s(blk.get('narrative'))}")
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
    for r in (pf.get("risks") or [])[:3]:
        if _s(r):
            stance_lines.append(f"風險:{_s(r)}")
    inval = [_s(t) for t in (tree.get("invalidation_triggers") or []) if _s(t)]
    if inval:
        stance_lines.append("失效條件:" + "、".join(inval[:3]))
    parts.append(f"## {SECTION_STANCE}\n" + "\n".join(stance_lines))

    parts.append(f"## {SECTION_SUMMARY}\n{summary}")
    return "\n\n".join(parts)
