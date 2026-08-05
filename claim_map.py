# -*- coding: utf-8 -*-
"""**哪些段落回指了哪些主張** —— 一份宣告,四個消費者(第二十輪 P2-5)。

先前四個地方各自維護一份 section 清單:

    analysis_crosscheck   驗存在、驗孤兒
    quality_metrics       算飽和率
    analysis_depth        加深的身分保存
    (renderer 另外一份)

於是 schema 加了 `scenario_tree.*.claim_ids` 與 `watch_triggers[].claim_ids`
之後,**只有驗證器知道**:飽和率看不到那些新段落(一條主張可以填滿三個
情境與全部觀察點而指標顯示分散),加深也可以把它們整個換掉。

這個 repo 已經栽過同型的兩次(`_GENERIC_ASSETS` 在 validator 與 metric
分家、必分析清單在截斷前後不同)。**清單要從一個地方長出來。**
"""
from __future__ import annotations

from typing import Optional

#: 讀者最先看到、也最可能被單獨閱讀的段落。孤兒判定與飽和率都以它們為準。
#: `key_drivers` 是「七、昨夜三大重點」—— **Email 的第一段**,
#: 先前完全在 claim 圖之外(第二十輪 P1-5)。
_OBJECT_SECTIONS = ("stance", "priced_in", "portfolio_implications")
_SCENARIOS = ("base", "bull", "bear")


def section_claim_mappings(obj: Optional[dict]) -> dict:
    """`{段落名: [claim_id, ...]}`。**空的段落也會出現**(值是空清單)——
    「這一段沒有回指」與「這一段不存在」要分得開。"""
    o = obj if isinstance(obj, dict) else {}
    out = {"executive_summary": [str(x) for x in
                                 (o.get("executive_summary_claim_ids") or [])]}
    for sec in _OBJECT_SECTIONS:
        node = o.get(sec)
        if isinstance(node, dict):
            out[sec] = [str(x) for x in (node.get("claim_ids") or [])]
    tree = o.get("scenario_tree") if isinstance(o.get("scenario_tree"), dict) else {}
    for key in _SCENARIOS:
        blk = tree.get(key)
        if isinstance(blk, dict) and str(blk.get("narrative") or "").strip():
            out[f"scenario_tree.{key}"] = [str(x) for x in (blk.get("claim_ids") or [])]
    for i, w in enumerate(o.get("watch_triggers") or []):
        if isinstance(w, dict) and str(w.get("trigger") or "").strip():
            out[f"watch_triggers[{i}]"] = [str(x) for x in (w.get("claim_ids") or [])]
    for i, d in enumerate(o.get("key_drivers") or []):
        if isinstance(d, dict) and str(d.get("statement") or "").strip():
            out[f"key_drivers[{i}]"] = [str(x) for x in (d.get("claim_ids") or [])]
    return out


def referenced_claim_ids(obj: Optional[dict]) -> set:
    """被任何段落回指過的 claim。**孤兒判定的分母。**"""
    return {c for ids in section_claim_mappings(obj).values() for c in ids}


def claims_by_id(obj: Optional[dict]) -> dict:
    return {str(c.get("claim_id") or ""): c
            for c in ((obj or {}).get("claim_audit") or [])
            if isinstance(c, dict) and c.get("claim_id")}


#: 時間尺度由短到長。**相容 = 主張的尺度不短於段落的尺度。**
#:
#: 段落宣告了一個期間(立場說 1-5 天、觀察點說 1-4 週),
#: 就要**至少有一條主張講到那個期間**。全部靠今日盤前的主張撐一個
#: 一個月的判斷,是形式上的引用 —— 外審給的反例正是
#: 「watch trigger 1-4w 引用 intraday 的 QQQ 漲幅」。
#: 較長的主張支撐較短的段落沒有問題(月線觀點當然也涵蓋今天)。
HORIZON_ORDER = ("intraday", "1-5d", "1-4w")


def horizon_covers(section_horizon: str, claim_horizon: str) -> bool:
    """這條主張的時間尺度**撐得起**這一段嗎(第二十輪 P1-5)。"""
    try:
        want = HORIZON_ORDER.index(str(section_horizon))
        got = HORIZON_ORDER.index(str(claim_horizon))
    except ValueError:
        return True                     # 不認得的尺度不做判斷,別誤擋
    return got >= want
