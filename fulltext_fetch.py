# -*- coding: utf-8 -*-
"""依抓取計畫**並行**抓新聞全文(2026-10-30 使用者:「沒辦法有全文或是更深入嗎?」)。

原本 `morning_report.fetch_news_fulltext` 逐篇抓:每篇 8–10 秒 timeout,
26 格額度就可能吃掉幾分鐘,額度因此一直壓在 26。改成固定幾條執行緒,
額度才加得上去(見 `fetch_plan.plan_for_run`)。

**順序仍是優先序**:`targets` 照計畫排好,執行緒依序領工作;每篇動工前
都檢查全域剩餘時間,低於地板就不再開始新的一篇 —— 時間不夠時有全文的
仍是最重要的那幾個事件(與逐篇版同一個保證)。

本模組不做網路呼叫:實際抓取由呼叫端傳入的 `fetch_one` 負責
(`morning_report._fetch_one_fulltext`,走 `_http_get`)。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

#: 同時抓幾篇。夠把額度加倍而不拉長步驟,又不會對單一媒體一次開太多連線。
WORKERS = 6


def fetch_targets(news, targets, fetch_one, seconds_left, floor: float,
                  workers: int = WORKERS) -> int:
    """照 `targets`(`source_item_id` 清單)抓全文,回成功篇數。

    `fetch_one(item, timeout, limit) -> bool` 就地寫 `item["fulltext"]`;
    `seconds_left()` 回本 run 剩餘秒數。重複的 ID 只抓一次(兩條執行緒
    同時寫同一則會讓冪等檢查失效)。
    """
    by_id = {str(n.get("source_item_id")): n for n in (news or [])
             if isinstance(n, dict) and n.get("source_item_id")}
    jobs = [by_id[sid] for sid in dict.fromkeys(str(s) for s in (targets or []))
            if sid in by_id]
    if not jobs:
        return 0

    def _one(item) -> bool:
        if seconds_left() < floor:
            return False
        crit = item.get("importance") == "critical"
        try:
            return bool(fetch_one(item, 10 if crit else 8, 2500 if crit else 2000))
        except Exception:      # noqa: BLE001 —— 一篇失敗不得拖垮整批(晨報不可斷)
            return False

    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), len(jobs)))) as pool:
        return sum(pool.map(_one, jobs))
