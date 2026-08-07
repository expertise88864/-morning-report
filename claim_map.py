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
    # **淨效果也回指主張**(外審 P1-7.4)。先前它不在這張圖裡,於是:
    #   * 引用完整性要求淨效果有 `claim_ids`,而 claim 圖不知道它用了誰 ——
    #     專門為淨效果寫的高重要性主張會被判成孤兒;
    #   * 飽和率看不到這一段(一條主張填滿所有淨效果也不會顯示集中)。
    # 「是不是有根據」在不同消費者眼裡再次漂移,而那正是本模組要消滅的。
    for i, n in enumerate(o.get("asset_net_effects") or []):
        if isinstance(n, dict) and str(n.get("asset_id") or "").strip():
            out[f"asset_net_effects[{i}]"] = [str(x) for x in (n.get("claim_ids") or [])]
    return out


def referenced_claim_ids(obj: Optional[dict]) -> set:
    """被任何段落回指過的 claim。**孤兒判定的分母。**"""
    return {c for ids in section_claim_mappings(obj).values() for c in ids}


def claims_by_id(obj: Optional[dict]) -> dict:
    return {str(c.get("claim_id") or ""): c
            for c in ((obj or {}).get("claim_audit") or [])
            if isinstance(c, dict) and c.get("claim_id")}


#: 時間尺度由短到長。
HORIZON_ORDER = ("intraday", "1-5d", "1-4w")

#: **相容矩陣**(第二十二輪 P1-5)。上一版是 `got >= want` 一條算式,
#: 它只擋一個方向 —— 而**兩個方向都會出錯**:
#:
#:   * 主張比段落**短**:觀察點宣告 1-4 週,引用的全是 intraday 的
#:     QQQ 漲幅 —— 形式上的引用(第二十輪 P1-5 的原始反例)。
#:   * 主張比段落**長兩階**:立場宣告當日,撐它的全是 1-4 週的結構性
#:     主張 —— 「這個月看多」推不出「今天會漲」。算式寫不出這一側,
#:     因為它在算式裡是「更安全」的方向。
#:
#: 矩陣寫得出:**每一格都是一個講得出理由的決定**,而不是一個不等號的
#: 副作用。相鄰一階以內相容;差兩階不相容(尺度差太遠,回指只是形式)。
HORIZON_MATRIX = {
    #  段落 \ 主張      intraday        1-5d           1-4w
    "intraday": {"intraday": True,  "1-5d": True,  "1-4w": False},
    "1-5d":     {"intraday": False, "1-5d": True,  "1-4w": True},
    "1-4w":     {"intraday": False, "1-5d": False, "1-4w": True},
}


def horizon_covers(section_horizon: str, claim_horizon: str) -> bool:
    """這條主張的時間尺度**撐得起**這一段嗎(第二十輪 P1-5)。

    查 `HORIZON_MATRIX`。**不認得的尺度不做判斷** —— 降級不誤擋,
    而那是「沒驗」不是「驗過」(呼叫端要說得出自己驗不了什麼)。
    """
    row = HORIZON_MATRIX.get(str(section_horizon))
    if not isinstance(row, dict):
        return True
    got = row.get(str(claim_horizon))
    return True if got is None else bool(got)


def horizons_compatible_with(section_horizon: str) -> list:
    """這一段可以靠哪些尺度的主張撐住 —— **錯誤訊息要說得出正解**。"""
    row = HORIZON_MATRIX.get(str(section_horizon)) or {}
    return [h for h in HORIZON_ORDER if row.get(h)]
