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
import subject_identity as _si


#: **可信的主體依據**(repo-wide 外審 2026-08-19 P1-2)。`unverified` 是
#: 舊 producer 的「沒驗過但照收」—— 與 code/alias/literal 語意完全不同,
#: 不能靠 truthiness 一視同仁豁免:生產帳本裡 `US-Iran War`(模型自取名)
#: 就是帶著 unverified 永久存活的。
TRUSTED_SUBJECT_BASES = frozenset(("code", "alias", "literal"))


def _revalidated_basis(title: str, code: str, known_names) -> str:
    """legacy `unverified` 列的重驗:用**新的**信任規則再走一次。

    成功的判準是「解析出來的主體就是這一列的主體」—— 跨語言時 canonical
    相同也算(entity=俄羅斯、標題 Russia:鍵不動、依據升級),否則升級
    會改到 story key。查證不出來回空字串(呼叫端清掉)。
    """
    subj, basis = _ne.resolve_subject(title, [code], known_names)
    if basis not in TRUSTED_SUBJECT_BASES or not subj:
        return ""
    if subj == code or _si.same_subject(subj, code):
        return basis
    return ""


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
        _basis = str(row.get("subject_basis") or "").strip()
        if _basis in TRUSTED_SUBJECT_BASES:
            keep.append(row)
            continue
        if _basis == "unverified" and code:
            # legacy fail-open 列(P1-2):用新的信任規則重驗 ——
            # Pentagon+五角大廈 升級,US-Iran War 查證不出來就清。
            _nb = _revalidated_basis(title, code, known_names)
            if _nb:
                row["subject_basis"] = _nb
                keep.append(row)
            else:
                dropped.append(row)
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


def purge_misattributed_timeline_points(rows, known_names):
    """清**正確 story 裡的錯 nested point**(repo-wide 外審 2026-08-19 P1-A)。

    story 列與 event_timeline 列的清理都做過了,但 story 的 `timeline[]`
    是會重新餵給模型的軌跡(`_arc_steps` / story prompt)—— 生產實證:
    2330 earnings story 的軌跡裡還活著「迅得上半年EPS3.79元」。
    這與 market wrap 的教訓同形狀(story headline 乾淨,nested 點仍髒,
    見 story_ledger 對 is_market_wrap 的逐點掃描)。

    判準與 story 列一致:
    - point 帶 `b`(subject_basis,producer 已用當時的完整文字裁決過)
      → 保留 —— 標題重驗會誤殺「靠摘要證實」的合法 point;
    - legacy point(無 `b`)→ 用標題對 story 主體保守重驗,指不出來就清;
    - 詞彙表查不到 story 主體 → 整列不動(證明不了 ≠ 錯,與 `_named` 同)。

    回 (rows, dropped):rows 就地更新 timeline;dropped 是 (key, 標題) 清單。
    可重入:清完的點不會再出現。
    """
    dropped = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("entity") or "")
        tl = row.get("timeline") or []
        if not code or not tl or not (known_names or {}).get(code):
            continue
        keep = []
        for p in tl:
            if not isinstance(p, dict):
                keep.append(p)
                continue
            if str(p.get("b") or ""):
                keep.append(p)
                continue
            title = str(p.get("t") or "")
            if _ne.mentions_entity(title, code, known_names):
                keep.append(p)
            else:
                dropped.append((str(row.get("key") or ""), title[:60]))
        if len(keep) != len(tl):
            row["timeline"] = keep
    return rows, dropped


def purge_misattributed_timeline(timeline, known_names) -> tuple:
    """事件時間軸同一套判準。主體來自列的欄位(舊列才解析鍵)。"""
    keep, dropped = {}, []
    for key, row in (timeline or {}).items():
        if not isinstance(row, dict):
            keep[key] = row
            continue
        # 與帳本同一條規則:**可信依據的列不判**(生產者用標題+摘要驗證,
        # 清理只看得到標題);legacy `unverified` 用新的信任規則重驗
        # (P1-2:US-Iran War 就是帶著 unverified 在時間軸永久存活的)。
        _basis = str(row.get("subject_basis") or "").strip()
        title = str(row.get("latest_title") or "")
        code = _timeline_subject(key, row)
        if _basis in TRUSTED_SUBJECT_BASES:
            keep[key] = row
            continue
        if _basis == "unverified" and code:
            _nb = _revalidated_basis(title, code, known_names)
            if _nb:
                row["subject_basis"] = _nb
                keep[key] = row
            else:
                dropped.append(key)
            continue
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


def _keep_longer(bucket: dict, key: str, row) -> None:
    """撞鍵時留天數多的那一筆(改鍵遷移共用;兩個方向都要擋 ——
    只擋一邊的話,dict 順序就決定了結果,而順序不是判準)。"""
    prev = bucket.get(key)
    if isinstance(prev, dict) and isinstance(row, dict):
        if int(prev.get("days") or 0) >= int(row.get("days") or 0):
            return
    bucket[key] = row


def migrate_cross_language_timeline_keys(timeline) -> tuple:
    """把主體是英文寫法的鍵改名成跨語言正規名(Pentagon→五角大廈)。

    2026-08-20 P1-2 r2:producer 與 event_identity 已統一走
    `subject_identity`,但既有 **current-schema** 列若留著英文主體
    (`geopolitical:Pentagon:2026-08`、4 段鍵的 ICC 對象段),隔天算出的
    鍵對不上 → 同一條 lifecycle 裂成兩條、延燒天數從 1 重算,收斂白做。
    `adopt_legacy()` 只處理舊 schema,接不了它們 —— 照 cyber 改鍵的先例
    處理:**改名不丟資料**;4 段鍵的對象段用 `object_signature` 對
    正規化後的 subjects 重算(不抄舊鍵裡截過的字);row 的 entity/subjects
    同步正規化;撞鍵留天數多者。可重入。

    回 `(timeline, renamed, repaired)`:`renamed` 是鍵真的改了的列,
    `repaired` 是鍵沒動、只把 row 的 `object` 修回與鍵一致的列
    (2026-08-22 外審 P2)。
    """
    import subject_identity as _sid
    out, renamed, repaired = {}, [], []
    for key, row in (timeline or {}).items():
        k = str(key)
        parts = k.split(":")
        if not isinstance(row, dict) or len(parts) not in (3, 4):
            _place_by_incident(out, k, row)   # 未改名列同一政策(r3:順序不得決定誰活)
            continue
        ent = str(row.get("entity") or "").strip()
        subs = [str(x).strip() for x in (row.get("subjects") or ())
                if str(x).strip()] or ([ent] if ent else [])
        c_ent = _sid.identity_name(ent) or ent
        c_subs = [_sid.identity_name(x) or x for x in subs]
        c_seg = (_sid.identity_name(parts[1]) or parts[1]
                 if len(parts) == 3 else parts[1])
        # **4 段鍵的 object 欄位也要跟著正規化**(2026-08-22 外審 P2)。
        # 上一版只重算了鍵裡的對象段,row 的 `object` 原樣留著 ——
        # 生產現況正是 `key=…:國際刑事法院:2026-08` 配
        # `object="International Criminal C"`(遷移前的英文截斷值),
        # 同一列的鍵身分與列身分互相矛盾。而消費端
        # (`event_identity._lineage_hits`)**優先信任存下來的 object**、
        # 算不出一致就 `continue`,於是中文續報接不回這條世系。
        # 更糟的是它**修不掉自己**:鍵已正規化 → 下一班的相等判斷成立
        # → 永遠不進修補分支。所以判斷要把 object 一起算進去。
        obj = ""
        if len(parts) == 4:
            import event_identity as _eid
            # **與 producer 同一支**(2026-08-22 外審 r1 P1)。上一版用
            # `object_signature`,但 producer(`timeline_identity` 與落盤)
            # 用的是 `action_object` —— 兩者對 **directional action** 不等價:
            # 軍售記錄的對象是「台灣」,簽章卻是「台灣、美國」。用簽章重算
            # 會把鍵改成 `arms_sale:台灣、美國:…`,而明天 producer 算的是
            # `arms_sale:台灣:…` —— 天數重設、世系裂成兩條,正是本批要修的
            # 那種傷害由我自己造出來。實測:
            # `object_signature("arms_sale", ["美國","台灣"]) == "台灣、美國"`。
            obj = _eid.action_object(
                parts[1], str(row.get("latest_title") or ""),
                c_subs or [c_ent],
                summary=str(row.get("latest_summary") or ""))
            if obj == _eid.UNKNOWN_OBJECT:
                # **`"?"` 是「辨識不出來」,不是一個新身分**(r2 外審 P1)。
                # 它是 truthy,於是「最新標題變模糊」的日子(「美國軍售案
                # 追蹤」、summary 空)會把 `arms_sale:台灣` 改寫成
                # `arms_sale:?` 並覆寫 object —— 明天那則明確的「對台軍售」
                # 反而接不回來。重算失敗就沿用既有的身分。
                obj = ""
        # 算不出對象就**沿用既有的非空值**(不得拿空字串、佔位符或另一套
        # 算法蓋掉當天算好的身分)。
        stale_obj = bool(obj) and str(row.get("object") or "") != obj
        if len(parts) == 4 and not obj:
            obj = str(row.get("object") or "") or parts[2]
        if (c_ent == ent and c_subs == subs
                and (len(parts) != 3 or c_seg == parts[1])
                and not stale_obj):
            _place_by_incident(out, k, row)   # 未改名列同一政策(r3:順序不得決定誰活)
            continue
        if len(parts) == 3:
            new_key = f"{parts[0]}:{c_seg}:{parts[2]}"
        else:
            new_key = f"{parts[0]}:{parts[1]}:{obj}:{parts[3]}"
        new_row = dict(row, entity=c_ent)
        if row.get("subjects"):
            new_row["subjects"] = c_subs
        if obj:
            new_row["object"] = obj
        # **改名與只修對象要分開報**:把「鍵沒動、只修好 object」記成
        # rename,manifest 就在宣稱一件沒發生的事。
        (renamed if new_key != k else repaired).append(k)
        _place_by_incident(out, new_key, new_row)
    return out, renamed, repaired


def _place_by_incident(bucket: dict, key: str, row) -> None:
    """撞鍵時走**既有的 incident 政策**(r2 外審 P1):`_keep_longer` 只用
    天數裁決,會把「共用 base key 的另一樁」滅掉 —— 而 base key 刻意粗
    (主體:型別:月),producer 靠 `incident_match` + sibling 鍵保住不同樁。
    遷移端同一份政策:同一樁(MATCH)才併(留天數多);另一樁/不知道 →
    掛 sibling(`base#incident_suffix`,與 producer 同一個後綴函式),
    後綴再撞就退避加序號(遷移端的撞鍵本來就罕見,退避只求不滅資料)。
    """
    import event_identity as _eid
    prev = bucket.get(key)
    if prev is None or not isinstance(prev, dict) or not isinstance(row, dict):
        if prev is None:
            bucket[key] = row
        else:
            _keep_longer(bucket, key, row)
        return
    verdict = _eid.incident_match(prev.get("incident_tokens"),
                                  row.get("incident_tokens"))
    if verdict == _eid.MATCH:
        _keep_longer(bucket, key, row)
        return
    sib = f"{key}#{_eid.incident_suffix(row.get('incident_tokens') or [])}"
    n = 2
    while sib in bucket:
        prev_sib = bucket.get(sib)
        if (isinstance(prev_sib, dict)
                and _eid.incident_match(prev_sib.get("incident_tokens"),
                                        row.get("incident_tokens"))
                == _eid.MATCH):
            _keep_longer(bucket, sib, row)
            return
        sib = f"{key}#{_eid.incident_suffix(row.get('incident_tokens') or [])}~{n}"
        n += 1
    bucket[sib] = row


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


def migrate_company_story_keys(ledger) -> tuple:
    """線索帳本的鍵改走機器身分:`e:2330|l:earnings|2026q3` →
    `e:台積電|l:…`(2026-08-22 外審 P1 的**必要配套**)。

    `story_key_for_event` 是從 `_event_timeline_key` 衍生的,主體段一旦
    改走組代表寫法,生產帳本裡 9,846 列的公司線今天就全部對不上新鍵。
    而歸屬雖然會先試 `_match_open_story`(主體相似度),**它接不住這種
    情況**:後續報導的標題與原標題差得夠遠時分數不過門檻 —— 自測反例
    (〈台積電法說〉AI 營收… → 台積電法說會確認先進封裝擴產)當場裂成
    兩條。不遷移就是上線第一天切斷所有公司線,比原缺陷更糟。

    **撞鍵不合併**:目標鍵已經有人時就原地不動。合併兩條線索要決定
    軌跡點、首見日與權威來源怎麼取捨,那是另一種語意;而原地不動不丟
    任何資料,重複線索本來就會被既有的主體比對隨時間收斂。可重入。
    """
    import subject_identity as _sid
    import story_ledger as _sl
    src = list(ledger or [])
    taken = {str(r.get("key") or "") for r in src if isinstance(r, dict)}
    out, renamed = [], []
    for r in src:
        if not isinstance(r, dict):
            out.append(r)
            continue
        k = str(r.get("key") or "")
        if not k.startswith("e:") or "|" not in k:
            out.append(r)
            continue
        subj, rest = k[2:].split("|", 1)
        # 鍵是 `_norm` 過的字串 —— 遷移端要用同一支,否則算出來的新鍵
        # 與 producer 明天算的不一致(那等於換個方式再裂一次)。
        new_subj = _sl._norm(_sid.identity_name(subj) or subj)
        new_key = f"e:{new_subj}|{rest}"
        if new_key == k or new_key in taken:
            out.append(r)
            continue
        taken.discard(k)
        taken.add(new_key)
        # **不就地改呼叫端的列**:遷移回傳新清單,輸入保持原樣 ——
        # 就地變異會讓「沒跑遷移」的對照組拿到已經改過的資料
        # (自測的反例當場失效,而那正是驗證這條規則的東西)。
        out.append(dict(r, key=new_key))
        renamed.append(k)
    return out, renamed


def migrate_story_action_event_types(ledger) -> tuple:
    """線索帳本的 lineage 段對齊 action→event_type 契約(r1 外審 P2)。

    story key 是 `e:<主體>|l:<型別>|<期別>`,型別段直接來自
    `_event_timeline_key` —— 契約改了而帳本沒遷移的話,同一條制裁線索的
    續報今天算出 `geopolitical`、帳本裡是 `export_controls`,標題改寫幅度
    大到過不了模糊比對時就會另開一條、原線索孤立(公司鍵那次同型)。

    判準與 producer 同一份:**型別段要在粗粒度家族內**,動作由該列存下來的
    標題推導。撞鍵原地不動(不合併,不丟資料)。可重入。
    """
    import event_actions as _ea
    import story_ledger as _sl
    src = list(ledger or [])
    taken = {str(r.get("key") or "") for r in src if isinstance(r, dict)}
    out, renamed = [], []
    _family = set(_ea.ACTION_EVENT_TYPE.values()) | {"", "general"}
    for r in src:
        if not isinstance(r, dict):
            out.append(r)
            continue
        k = str(r.get("key") or "")
        if not k.startswith("e:") or "|l:" not in k:
            out.append(r)
            continue
        head, lineage = k.split("|l:", 1)
        seg = lineage.split("|", 1)
        cur = seg[0]
        title = str(r.get("headline") or r.get("last_delta") or "")
        want = (_ea.ACTION_EVENT_TYPE.get(_ea.event_action(title, ""))
                if cur in _family else None)
        if not want or want == cur:
            out.append(r)
            continue
        new_key = f"{head}|l:" + "|".join([_sl._norm(want)] + seg[1:])
        if new_key in taken:
            out.append(r)
            continue
        taken.discard(k)
        taken.add(new_key)
        # **列的 event_type 也要跟著改**(r2 外審):只改鍵的話,追蹤查詢會
        # 拿列裡的舊型別去找「出口管制」而不是地緣制裁,而型別升級只處理
        # `general → 具體`,永遠自己修不回來。同一個缺陷我在 timeline 那支
        # 修過一次,這裡漏了 —— 鍵與列不得互相矛盾。
        out.append(dict(r, key=new_key, event_type=want))
        renamed.append(k)
    return out, renamed


def migrate_sanction_objects(timeline) -> tuple:
    """制裁鍵的對象段改用 producer 現在的判準重算(2026-08-26 外審)。

    生產出現過 `geopolitical:sanction:Oil:2026-08` —— 標題是
    「Oil Falls Further Despite Fresh U.S. Sanctions on Iran」,Oil 是被
    影響的資產、Iran 才是制裁對象。判準修好之後,那一列今天算出來的鍵是
    `…:伊朗:…`,而**舊鍵留在 state 裡就是一條永遠接不上的孤立線**
    —— 與公司鍵、event_type 那兩次同一個道理:改判準要配遷移。

    重算只用 `event_identity.sanction_target`(**producer 自己那支**),
    不在這裡另寫一份判準;算不出來或沒變就原樣保留。撞鍵沿用既有的
    incident 政策。可重入。
    """
    import event_identity as _eid
    out, renamed, repaired = {}, [], []
    for key, row in (timeline or {}).items():
        k = str(key)
        parts = k.split(":")
        if not isinstance(row, dict) or len(parts) < 4 or parts[1] != "sanction":
            _place_by_incident(out, k, row)
            continue
        tgt = _eid.sanction_target(row.get("latest_title"),
                                   row.get("subjects") or [],
                                   summary=row.get("latest_summary") or "")
        if not tgt:
            _place_by_incident(out, k, row)
            continue
        if tgt == parts[2]:
            # **鍵已經對了不代表列也對**(2026-08-26 外審 r1):上一版的
            # 半套遷移會留下「鍵是伊朗、`subjects` 還是 Oil」的中間狀態,
            # 而它跳過這一條 —— 那些列永遠修不好,消費端照樣把它當 Oil。
            # 鍵不用改,欄位照修。
            fixed = _sanction_row_fields(row, tgt)
            if fixed != row:
                repaired.append(k)
            _place_by_incident(out, k, fixed)
            continue
        new_key = ":".join([parts[0], parts[1], tgt] + parts[3:])
        # **帶身分的欄位要一次全部同步**(2026-08-26 外審 P2)。第一版只改
        # `key`/`entity`/`object`,而消費端 `_lineage_hits` 是
        # **`subjects` 優先、`entity` 只在 subjects 為空時才補**:
        # 遷移後那一列的鍵叫 `sanction:伊朗`,消費端卻仍把它當 Oil 的事件,
        # 下一則伊朗制裁接不回去 —— 鍵改了而世系沒接上,比不改更難查。
        # `identity_schema` 同理:這一列已經用 v13 公式重寫,卻對
        # `adopt_legacy` 那類判準宣稱自己是舊世代。
        new_row = _sanction_row_fields(row, tgt)
        renamed.append(k)
        _place_by_incident(out, new_key, new_row)
    return out, renamed, repaired


def _sanction_row_fields(row: dict, tgt: str) -> dict:
    """帶身分的欄位一次同步:`object` / `subjects` / `identity_schema`
    (以及原本就有 `entity` 的話)。**沒有變動就回原物件**,呼叫端據此
    判斷要不要記一筆修補。"""
    import event_identity as _eid
    want = dict(row, object=tgt, subjects=[tgt],
                identity_schema=_eid.IDENTITY_SCHEMA_VERSION)
    if row.get("entity"):
        want["entity"] = tgt
    return row if want == row else want


def migrate_action_event_types(timeline) -> tuple:
    """鍵的 event_type 段對齊 `event_actions.ACTION_EVENT_TYPE`。

    2026-08-22 外審 P2-3 的必要配套:同一個動作先前會因入口不同拿到不同
    event_type(生產同時有 `export_controls:sanction:*` 與
    `geopolitical:sanction:*`)。producer 統一之後,既有的舊鍵今天算不出來
    —— 與公司鍵那次同一個道理:**改判準要配遷移,否則上線第一天全部孤立**。

    只改**第一段**(event_type),其餘照抄;撞鍵沿用既有的 incident 政策
    (同一樁才併、留天數多者)。可重入。
    """
    import event_actions as _ea
    out, renamed = {}, []
    for key, row in (timeline or {}).items():
        k = str(key)
        parts = k.split(":")
        if not isinstance(row, dict) or len(parts) < 3:
            _place_by_incident(out, k, row)
            continue
        # **判準要與 producer 同一份**(自測抓到):producer 只在
        # event_type 屬於粗粒度家族時才對齊(模型說 litigation 而動作是
        # cyberattack 時,那則新聞真的是在講訴訟)。遷移比它更aggressive 的話,
        # 會把 `litigation:cyberattack:*` 改成 producer 明天算不出來的鍵 ——
        # 與公司鍵那次的軍售完全同型。
        _family = set(_ea.ACTION_EVENT_TYPE.values()) | {"", "general"}
        want = (_ea.ACTION_EVENT_TYPE.get(parts[1])
                if parts[0] in _family else None)
        if not want or want == parts[0]:
            _place_by_incident(out, k, row)
            continue
        new_key = ":".join([want] + parts[1:])
        # **列也要改**(r1 外審 P3):只改鍵的話,鍵說 geopolitical、列裡的
        # `event_type` 還是 export_controls —— 與 ICC 那次的 stale object
        # 同型,而且同日重跑會把舊型別讀回活躍時間軸。
        new_row = dict(row, event_type=want) if row.get("event_type") else row
        renamed.append(k)
        _place_by_incident(out, new_key, new_row)
    return out, renamed
