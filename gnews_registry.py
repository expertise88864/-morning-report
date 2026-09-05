# -*- coding: utf-8 -*-
"""**Google News 查詢註冊表**(OPTIMIZATION_PLAN V2-N4)。

## 問題

固定查詢先前散在 `RSS_FEEDS` 的字面量裡,而**沒有人知道哪一條還有用**。
一條查詢寫錯了、或它涵蓋的族群退燒了,症狀是「那一段內容變少」——
而那看起來就像「今天沒什麼新聞」。查詢是**取材的入口**,入口壞掉是
最安靜的失效。

## 做法

查詢與它的**用途**寫在同一個地方(這裡),`RSS_FEEDS` 從這裡長出來。
每天記各查詢的命中則數,累積進既有的 30 天滾動健康歷史
(`state/source_health_history.json`,不另開檔);連續 30 天零命中的
查詢在月報列為**候刪**。

## 只列不刪

計劃書寫得很清楚:「**只列不刪**,刪除需使用者確認」。零命中可能是
查詢壞了,也可能是那個主題這個月真的沒事發生 —— 前者要修,後者不要動。
自動刪除會把第二種也一起刪掉,而且刪掉之後**再也不會有人發現**。
"""
from __future__ import annotations

from typing import Optional

#: `(標籤, 查詢字串, 用途)`。**標籤就是 `RSS_FEEDS` 的鍵**,
#: 也是健康歷史裡的鍵 —— 三邊同一份宣告,漂移不了。
#:
#: 動態查詢(個股、當日類股領先股)刻意**不在這裡**:它們由當日股票池
#: 產生,今天有明天就沒有,列進註冊表只會讓「連續 30 天零命中」永遠
#: 不成立(每天都是新的鍵)。
QUERIES = (
    # === 主題(取代已停的 Reuters,廣度覆蓋)===
    ("Google-半導體", "半導體 AI晶片 台積電 輝達", "半導體與 AI 供應鏈"),
    ("Google-美股科技", "美股 那斯達克 科技股 財報", "美股科技與財報"),
    ("Google-Fed利率", "Fed 聯準會 利率 通膨 CPI", "美國貨幣政策與通膨"),
    ("Google-台股大盤", "台股 加權指數 外資 三大法人", "台股大盤與籌碼"),
    # 2026-09-06 真實 RSS 驗證:舊 AND 查詢 0 則;OR 100 則(去重/日期仍照舊)。
    ("Google-地緣", "台海 OR 晶片管制 OR 出口管制 OR 美中 OR 關稅", "地緣與貿易管制"),
    ("Google-半導體設備", "半導體 設備 OR ASML OR Applied Materials 出口管制",
     "設備投資與出口限制;實際 RSS HTTP 200 / 13 則"),
    ("Google-能源電網", "能源 供應 OR 天然氣 OR 電網 投資",
     "能源供需與電網投資;實際 RSS HTTP 200 / 32 則"),
    # === 科技二線族群(讓「科技板塊脈動」不只有 2330/2454)===
    # 純取材:不掛個股標籤、不進計分。
    ("Google-散熱", "散熱 水冷 液冷 AI伺服器", "散熱族群取材"),
    ("Google-先進封裝", "CoWoS 先進封裝 台積電 日月光", "先進封裝族群取材"),
    ("Google-載板PCB", "ABF載板 PCB CCL 銅箔基板", "載板/PCB 族群取材"),
    ("Google-光通訊", "光通訊 CPO 矽光子 800G", "光通訊族群取材"),
    # === 世界大事(股市之外;供「世界大事速覽」取材)===
    # 使用者需求(2026-07-16):一封信掌握昨日世界。查詢經實測校準
    # (召回 46-100 則/2d);不掛 company_label、不進任何計分。
    ("世界-國際大事", "戰爭 OR 停火 OR 大選 OR 政變 OR 峰會 OR 制裁",
     "地緣政治與國際政局"),
    ("世界-災難極端", "地震 OR 颱風 OR 洪災 OR 熱浪 OR 空難", "災難與極端天氣"),
    ("世界-科學太空", "NASA OR SpaceX OR 諾貝爾 OR 核融合 OR 太空任務",
     "科學與太空"),
    ("世界-AI大事", "OpenAI OR Anthropic OR DeepMind OR AI模型 發布",
     "AI 前沿動態"),
)

#: 本 run 各標籤的命中則數。**測試之間要清空**(見 `reset`)。
HITS: dict = {}

#: `url → 標籤`。由 `feed_entries()` 建立 —— 記帳只認得註冊表發出去的
#: URL,別人組的 Google 查詢(個股、類股)不會誤記到主題查詢頭上。
_URL_TO_LABEL: dict = {}


def reset() -> None:
    HITS.clear()


def feed_entries(build_url) -> dict:
    """`{標籤: URL}`,並建立記帳用的反查表。

    `build_url(query)` 由呼叫端注入(`morning_report._gnews_rss`)——
    本模組刻意不相依主模組,它才單獨測得起來。
    """
    out = {}
    for label, query, _purpose in QUERIES:
        url = build_url(query)
        out[label] = url
        _URL_TO_LABEL[url] = label
    return out


def record(url: str, n_entries: int) -> None:
    """記一次抓取的命中則數。**不認得的 URL 直接忽略。**"""
    label = _URL_TO_LABEL.get(str(url or ""))
    if label:
        HITS[label] = HITS.get(label, 0) + max(0, int(n_entries or 0))


def today_hits() -> dict:
    """**註冊表裡的每一條都要有一格。** 缺席與零命中是兩件事:
    缺席代表今天根本沒抓(熔斷、時間不夠),那不該算成「這條查詢沒用」。
    """
    return {label: int(HITS.get(label, 0)) for label, _, _ in QUERIES}


def purposes() -> dict:
    return {label: purpose for label, _, purpose in QUERIES}


def zero_hit_candidates(history: Optional[list], days: int = 30) -> list:
    """連續 `days` 天**每天都抓了、每天都零命中**的查詢。

    回 `[(標籤, 用途, 連續天數)]`。**只列不刪** —— 零命中可能是查詢壞了,
    也可能是那個主題這陣子真的沒事發生。前者要修,後者不要動,而程式
    分不出來;分得出來的是人。
    """
    hist = [h for h in (history or []) if isinstance(h, dict)]
    hist.sort(key=lambda h: str(h.get("date") or ""))
    tail = hist[-days:]
    # **樣本不足自動不判**:`streak` 最多等於 `len(tail)`,所以歷史不滿
    # `days` 天時下面的 `streak >= days` 本來就不會成立。
    # (第一版在這裡多寫了一個 `len(tail) < days: return []` —— 突變驗證
    # 顯示拿掉它沒有任何測試變紅,因為它是死碼。**留著會讓人以為那裡
    # 有一道守衛**,而真正生效的是迴圈末尾那個門檻。)
    out = []
    p = purposes()
    for label, _query, purpose in QUERIES:
        streak = 0
        for h in reversed(tail):
            q = h.get("queries")
            if not isinstance(q, dict) or label not in q:
                break                   # 那天沒抓 → 中斷,不算進連續
            if q[label] != 0:
                break
            streak += 1
        if streak >= days:
            out.append((label, p.get(label, purpose), streak))
    return out
