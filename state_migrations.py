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


def _looks_cyber(title: str) -> bool:
    """標題用的是**宣告過的**資安詞彙(`event_actions` 的 `cyberattack`)。"""
    import news_events as _ne2
    text = str(title or "").lower()
    return any(str(t).lower() in text for t in (_ne2._cyber_tokens() or ()))


def purge_mistyped_cyber_stories(ledger) -> tuple:
    """把「被標成地緣政治的資安事件」清掉(2026-08-18 外審 P2-1)。

    生產 state 現存兩筆:

        e:aapl|l:geopolitical|202608  ← 「Apple 發出間諜軟體威脅通知…」
        geopolitical:AVGO:2026-08     ← 「駭客攻擊 VMware…」

    它們的**歸因是對的**(標題確實指名那家公司),錯的是型別 —— 所以
    Commit A 的清理刻意沒有動它們。而型別會一路影響意外度(0.90)、
    催化權重與延燒追蹤,留著就是每天用錯誤的優先序推一條線。

    **選擇丟掉而不是改型別**:型別寫在 key 裡(`l:geopolitical`),改型別
    就要改 key,而改 key 會與既有的正確 key 相撞、或製造出第二條同一件事
    的線。丟掉之後,故事若還活著,下一班會用正確型別重新建立 —— 少幾天
    延燒天數,比留著一條型別錯誤、意外度灌到 0.9 的線小得多。
    """
    keep, dropped = [], []
    for row in (ledger or []):
        if not isinstance(row, dict):
            keep.append(row)
            continue
        title = str(row.get("headline") or row.get("last_delta") or "")
        if (str(row.get("event_type") or "") == "geopolitical"
                and _looks_cyber(title)):
            dropped.append(row)
        else:
            keep.append(row)
    return keep, dropped


def _cyber_key(key: str, row: dict) -> str:
    """舊鍵 → **現行身分格式**的鍵。

    現行身分是 `型別:動作:對象:月`(`event_identity.timeline_identity`)。
    生產現存的是**舊版三段** `型別:主體:月`,而它們的 `identity_schema`
    已經是最新版 —— 也就是說 `adopt_legacy()` **不會**接手它們
    (那條路徑只處理舊 schema)。只把型別那一段改掉會產生
    `cybersecurity:AAPL:2026-08`,而隔天的事件算出來的是
    `cybersecurity:cyberattack:AAPL:2026-08` —— 對不上,天數從 1 重算,
    這個改名就白做了(外審 2026-08-18 第二輪)。

    **對象要用身分層的那個函式算,不要抄舊鍵裡的字**(外審第三輪):
    舊的三段鍵把主體截到 20 字,而現行的 `object_signature` 截到 24 字 ——
    多主體的列(「A、B、C…」)兩邊會差一段,改名之後照樣對不上。
    列裡有 `subjects` / `entity`,那才是原始資料。
    """
    parts = str(key or "").split(":")
    if len(parts) != 3:
        return "cybersecurity:" + str(key)[len("geopolitical:"):]
    subjects = [str(x) for x in ((row or {}).get("subjects") or ()) if str(x).strip()]
    if not subjects:
        ent = str((row or {}).get("entity") or "").strip()
        subjects = [ent] if ent else [parts[1]]
    try:
        import event_identity as _eid
        obj = _eid.object_signature("cyberattack", subjects)
    except Exception:                   # noqa: BLE001 - 算不出來就退回舊鍵裡的字
        obj = parts[1]
    return f"cybersecurity:cyberattack:{obj}:{parts[2]}"


def migrate_cyber_timeline_keys(timeline) -> tuple:
    """把 `geopolitical:cyberattack:*` 的舊鍵**改名**成 `cybersecurity:…`。

    回 `(新的 state, 被改名的舊鍵)`。

    **改名而不是丟掉**:時間軸的鍵是 `型別:動作:對象:月`,而動作那一段
    已經明說是 `cyberattack` —— 不必猜,改名是精確的。丟掉會讓一條真的
    延燒好幾天的線從第 1 天重算(產線停擺、客戶通報、修復進度),那個
    代價沒有必要付。
    (線索帳本那邊的鍵沒有動作段,認不出來,所以那邊仍然是丟掉。)

    目標鍵已經存在時**留天數多的那一筆** —— 那是同一件事的兩個世代,
    保守地選資訊多的;兩筆都留會讓同一條線在排序裡出現兩次。
    """
    def _keep_longer(bucket: dict, key: str, row) -> None:
        """撞鍵時留天數多的那一筆。

        **兩個方向都要擋**:舊鍵改名撞到既有的新鍵、以及既有的新鍵在
        迴圈後面才被讀到 —— 只擋一邊的話,誰先誰後就決定了結果,而 dict
        的順序不是判準(突變驗證抓到:只擋一邊時反例分不出勝負)。
        """
        prev = bucket.get(key)
        if isinstance(prev, dict) and isinstance(row, dict):
            if int(prev.get("days") or 0) >= int(row.get("days") or 0):
                return
        bucket[key] = row

    out, renamed = {}, []
    for key, row in (timeline or {}).items():
        k = str(key)
        # 兩種鍵都要收:新版 `型別:動作:對象:月`(動作那段就是 cyberattack),
        # 以及**舊版三段** `型別:主體:月`(生產現存的 `geopolitical:AAPL:2026-08`
        # 就是這一種,沒有動作段)—— 後者靠標題認,判準與型別層同一份。
        _is_cyber_key = (k.startswith("geopolitical:cyberattack:")
                         or (k.startswith("geopolitical:")
                             and isinstance(row, dict)
                             and _looks_cyber(row.get("latest_title"))))
        if not isinstance(row, dict) or not _is_cyber_key:
            _keep_longer(out, k, row)
            continue
        renamed.append(k)
        _keep_longer(out, _cyber_key(k, row), dict(row, event_type="cybersecurity",
                                                   action="cyberattack"))
    return out, renamed
