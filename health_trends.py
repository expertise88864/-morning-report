# -*- coding: utf-8 -*-
"""**30 天健康歷史的讀取端**(OPTIMIZATION_PLAN V2-N3 / V2-N4)。

`morning_report.update_source_health_history` 每天往
`state/source_health_history.json` 追加一筆;這裡是**讀**的那一半 ——
月報用它把「今天怎麼樣」變成「這個月的走勢」。

## 為什麼交叉驗證只算趨勢,不自動遮蔽

`_audit_dramatic_macro_claims` 抓的是「敘述用了戲劇性字眼,而實際幅度
撐不起來」。它**刻意只記錄不遮蔽**(計劃書 V2-N3:「不要升級為自動
遮蔽,誤殺風險>收益」)—— 那個判準靠關鍵詞,而同一個詞在不同語境
可以是準確的。趨勢有用:**數字一路往上,代表寫法在漂**,那時要看的是
prompt,不是把句子擋掉。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def load(path) -> list:
    """讀 30 天歷史。**讀不到就回空 list** —— 月報不因此失敗。"""
    try:
        p = Path(path)
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        return [h for h in (data or []) if isinstance(h, dict)]
    except Exception:
        return []


def drama_trend(history: Optional[list], days: int = 30) -> dict:
    """敘述-數字交叉驗證的警告數走勢。

    回 `{days, total, max, latest, series}`。`series` 是 `(日期, 數量)`,
    **只含真的有記到那一格的日子** —— 舊資料沒有這個欄位,把缺席算成 0
    會讓走勢圖看起來「以前都很好」。
    """
    hist = sorted([h for h in (history or []) if isinstance(h, dict)],
                  key=lambda h: str(h.get("date") or ""))[-days:]
    series = [(str(h.get("date") or ""), int(h.get("drama") or 0))
              for h in hist if isinstance(h.get("drama"), int)]
    counts = [n for _, n in series]
    return {"days": len(series), "total": sum(counts),
            "max": max(counts) if counts else 0,
            "latest": counts[-1] if counts else None,
            "series": series}


def monthly_block(path, days: int = 30) -> str:
    """月報用的 Markdown 區塊。**沒有資料時說「沒有資料」**,
    不要印一個空表格讓人以為一切正常。"""
    import gnews_registry as _gr
    hist = load(path)
    if not hist:
        return ("## 來源健康(30 天)\n\n"
                "> 讀不到 `state/source_health_history.json` —— "
                "**這本身就是一個訊號**(晨報沒跑成、或狀態沒有落地)。\n\n")
    lines = ["## 來源健康(30 天)\n"]
    cand = _gr.zero_hit_candidates(hist, days=days)
    if cand:
        lines.append(f"### ⚠ 連續 {days} 天零命中的查詢(**候刪,不自動刪**)\n")
        lines.append("| 查詢標籤 | 用途 | 連續天數 |")
        lines.append("| --- | --- | --- |")
        lines += [f"| `{lab}` | {purpose} | {n} |" for lab, purpose, n in cand]
        lines.append("\n> 零命中可能是**查詢壞了**,也可能是那個主題這陣子"
                     "真的沒事發生。前者要修,後者不要動 —— 程式分不出來,"
                     "所以只列出來交給人判斷。\n")
    else:
        lines.append(f"- 固定查詢:近 {days} 天沒有連續零命中的項目。\n")
    d = drama_trend(hist, days=days)
    if d["days"]:
        lines.append("### 敘述-數字交叉驗證(僅記錄,不遮蔽)\n")
        lines.append(f"- 近 {d['days']} 天累計 **{d['total']}** 次警告"
                     f"(單日最高 {d['max']},最近一天 {d['latest']})。")
        lines.append("- 數字一路往上代表**寫法在漂** —— 那時要看的是 prompt,"
                     "不是把句子擋掉(自動遮蔽的誤殺風險大於收益)。\n")
    else:
        lines.append("### 敘述-數字交叉驗證\n- 尚未累積到資料。\n")
    return "\n".join(lines) + "\n"
