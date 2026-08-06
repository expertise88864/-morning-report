# -*- coding: utf-8 -*-
"""**一則新聞的身分**(第二十四輪 P1-1 拆出)。

從 `evidence_packet` 搬出來,理由是 P1-1 那個缺陷的形狀:ID 原本只在
EvidencePacket 階段的 `normalize_news()` 裡產生,而**分群、全文計畫、
逐事件群抓取**三個更早的相位全都以它索引。ID 住在 packet 模組裡,
就會讓人以為「那是 packet 的事」—— 而它其實是整條管線的共用身分。

放在葉模組還有一個效果:`assign_source_item_ids()` 可以在管線最前端呼叫,
而不必為了一個 ID 去 import 整個 packet 建構器。

**這個模組不得 import 管線的其他部分** —— 身分是最底層的東西。
"""
from __future__ import annotations

import hashlib


def _sid(item: dict, index: int) -> str:
    """新聞的穩定識別碼。

    優先用上游已有的 `source_item_id`;沒有就用 (來源, 標題, 發布時間) 的雜湊。
    **不用陣列索引** —— 索引會隨當日抓取數量漂移,而 claim 要靠它回指證據,
    索引一變,昨天的 claim 就指到今天的另一則新聞。
    """
    existing = str(item.get("source_item_id") or "").strip()
    if existing:
        return existing[:16]
    raw = "|".join(str(item.get(k) or "") for k in ("source", "title", "published"))
    if not raw.strip("|"):
        raw = f"__empty__{index}"
    return "n" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:11]


def assign_source_item_ids(news):
    """**在任何依賴 ID 的相位之前把 `source_item_id` 補齊**(就地寫入,回同一個 list)。

    第二十四輪 P1-1:生產接線是
    `plan_for_run(news)` → `fetch_news_fulltext(news, targets=...)`,
    而 ID 要到更後面的 `normalize_news()` 才產生。於是三個環節同時看不到它:

        news_clusters.clusters()  過濾 `and n.get("source_item_id")` → 0 群
        fetch_plan.plan()         同樣過濾 → targets = []
        fetch_news_fulltext()     by_id 是空的 → 一篇都抓不到

    2026-08-06 生產 manifest 正是如此:`available news = 563`、
    `clusters = 0`、`targets = 0`。**不是當日資料問題,是固定的接線順序錯誤;**
    兩階段抓取整段是 no-op,全文預算實際上一格都沒有花在事件上。

    上一輪的單元測試沒抓到,因為 fixture 都預先手工填好了 `source_item_id` ——
    所以 `tests/test_fetch_plan_wiring.py` 的鐵則是 fixture 一律不得帶 ID。

    冪等:`_sid()` 對已有 ID 原樣回傳,故之後 `normalize_news()` **不會改號**
    (同一則新聞在 planner、抓取、packet、claim 回指裡是同一個 ID)。
    """
    for i, n in enumerate(news or []):
        if isinstance(n, dict):
            n["source_item_id"] = _sid(n, i)
    return news
