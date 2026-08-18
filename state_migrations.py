# -*- coding: utf-8 -*-
"""**修 producer 不會把已經寫壞的 state 修好**(repo-wide 外審 2026-08-18,P1-1)。

`entity` 先前同時是三件事(模型宣告的主體、編輯標註的相關個股、發起查詢的
代號),於是確定性層把這些寫進了帳本與時間軸:

    e:2454|l:geopolitical|202608   聯發科  ← 「黃金終於鬆開手煞車!8月大漲9%」
    e:2890|l:earnings|2026q2       永豐金  ← 「【公告】勝悅-KY 第2季合併財報…」
    e:3231|l:earnings|2026q3       緯創    ← 「緯穎飆出6740元歷史新天價」
    geopolitical:2454:2026-08              ← 同一則黃金新聞

那幾筆會跨日回流:昨日敘事、線索追蹤、延燒天數、催化評分都讀它們。
**只修 producer,明天仍然會讀到同一批錯誤歸因。**

這裡是一次性的清理,判準與 producer 是**同一個函式**
(`news_events.mentions_entity`)—— 兩份判準會分歧,而分歧的症狀是
「清掉的明天又長回來」或「清掉了不該清的」。

## 三個刻意的設計

* **保守**:只清「有公司主體、而那個主體在自己的標題裡指不出來」的列。
  沒有主體的市場級列(`e:clusterXXXX`)、以及**查不到別名表**的實體
  一律留著 —— 詞彙表沒收錄不等於歸因錯了。
* **可觀測**:回傳被清掉的列,呼叫端寫進 run manifest。靜默清理與靜默
  污染一樣糟。
* **可重入**:清過的東西不會再被清一次(清完就不符合條件了),所以
  每天跑都安全,不需要一個「已經跑過」的旗標 —— 那種旗標本身會壞掉。
"""
from __future__ import annotations

import news_events as _ne


def _named(title: str, code: str, known_names) -> bool:
    """標題有沒有指名這個實體。**別名表查不到就當成「不知道」→ 留著。**"""
    if not str(code or "").strip():
        return True                     # 沒有主體的列不歸這裡管
    if not ((known_names or {}).get(code)):
        return True                     # 詞彙表沒收錄 ≠ 歸因錯了
    return bool(_ne.mentions_entity(title, code, known_names))


def purge_misattributed_stories(ledger, known_names) -> tuple:
    """回 `(留下來的列, 被清掉的列)`。判準與 producer 同一個函式。"""
    keep, dropped = [], []
    for row in (ledger or []):
        if not isinstance(row, dict):
            keep.append(row)
            continue
        title = str(row.get("headline") or row.get("last_delta") or "")
        code = str(row.get("entity") or "").strip()
        # **帶著依據的列不判**(外審 2026-08-18 第三輪):生產者驗證用的是
        # 標題+摘要,而帳本只存標題 —— 拿標題去判有依據的列,會把生產者
        # 昨天建立的合法 state 刪掉。這個清理的工作是**修正之前寫下的舊列**,
        # 那些列沒有 `subject_basis`。
        if str(row.get("subject_basis") or "").strip():
            keep.append(row)
            continue
        if title and not _named(title, code, known_names):
            dropped.append(row)
        else:
            keep.append(row)
    return keep, dropped


def _timeline_subject(key: str, row: dict) -> str:
    """時間軸這一列的主體。

    **鍵的格式已經換過**(外審 2026-08-18 P1-2):新版是
    `型別:動作:對象:月`(`event_identity.timeline_identity`),第二段是
    **動作**不是代號 —— 照舊解析鍵會把 `arms_sale` 當主體,而詞彙表查不到它,
    於是污染列因為 fail-open 永遠留著。真正的主體在列自己的
    `entity` / `subjects` 欄位,優先讀那裡;兩個都沒有才退回解析舊鍵。
    """
    ent = str((row or {}).get("entity") or "").strip()
    if ent:
        return ent
    subs = (row or {}).get("subjects") or ()
    if subs:
        return str(subs[0] or "").strip()
    parts = str(key or "").split(":")
    # 舊鍵是 `型別:主體:月`(三段);四段以上是新版動作鍵,那時沒有主體可取。
    return parts[1] if len(parts) == 3 else ""


def purge_misattributed_timeline(timeline, known_names) -> tuple:
    """事件時間軸同一套判準。主體來自列的欄位(舊列才解析鍵)。"""
    keep, dropped = {}, []
    for key, row in (timeline or {}).items():
        if not isinstance(row, dict):
            keep[key] = row
            continue
        # 與帳本同一條規則:**帶著依據的列不判**(生產者用標題+摘要驗證,
        # 清理只看得到標題)。沒有依據的才是修正之前寫下的舊列。
        if str(row.get("subject_basis") or "").strip():
            keep[key] = row
            continue
        title = str(row.get("latest_title") or "")
        code = _timeline_subject(key, row)
        if title and not _named(title, code, known_names):
            dropped.append(key)
        else:
            keep[key] = row
    return keep, dropped
