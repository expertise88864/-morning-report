# -*- coding: utf-8 -*-
"""**延燒事件的身分**(外審 P1-9:Commit 4)。

## 舊身分是「某國的某類新聞」,不是事件

`event_type:主體集合` 這個鍵在生產同時產生兩種相反的錯:

**(a) 同一樁事情裂成好幾條。** 2026-08-07 的實際 state:

    geopolitical:伊朗              days=6   伊朗、阿曼研議限制敵對船舶通行荷姆茲海峽
    geopolitical:伊朗、美國、阿曼   days=1   美伊荷姆茲海峽談判傳出進展

同一條荷姆茲海峽的線,因為兩則報導**點名的主體集合不同**而變成兩個
「第 N 天」。英文報導再裂一次(`Iran-Oman` / `United States-Iran`)。

**(b) 不同的事情被算成同一條。** 同一天的 state:

    geopolitical:美國   days=4   北京不滿對台軍售致美國防官員訪中受阻

這條的 `latest_title` 已經從八月初的別件事漂到「對台軍售」,而系統仍然
算它連續第 4 天 —— 讀者看到的「延燒四天」指的是兩件不同的事。

兩個錯有同一個根:**身分裡沒有「發生了什麼」**,只有「誰」與「哪一類」。

## 這裡的解法:動作是主要判準,主體是次要

    {event_type}:{action}:{month}          ← 認得出動作時
    {event_type}:{canonical subjects}      ← 認不出動作時(降級,行為同舊版)

動作由**宣告式關鍵詞表**判定(與 `event_graph.DRIVER_TABLE` 同一個路數),
不用語意相似度 —— 身分不能靠相似度,那是這個 repo 已經寫在別處的規矩:
漏歸類只是退回原本的行為,誤歸類會把兩件事永久黏成一件。

動作當主鍵同時解掉 (a) 與 (b):

  * 兩則荷姆茲報導不論點名 `伊朗` 還是 `伊朗、美國、阿曼`,動作都是
    `hormuz_passage` → **同一條線**;
  * `對台軍售` 的動作是 `arms_sale`,與 `hormuz_passage` 不同 →
    **兩條線**,「延燒四天」不會再跨到別的事情上。

英文關鍵詞與中文並列在同一列,所以 `Iran-Oman ... Hormuz` 與
「伊朗、阿曼…荷姆茲」落在同一個動作上,跨語言分裂一併解掉。

## 月份為什麼還在

同一個動作跨月會被切成兩集(多算一次),而不帶時間會讓「每年同一批
軍售案」永久共用一條線(少算一次真事件)。方向上前者安全 ——
這與 `news_events._event_timeline_key` 對年份的取捨是同一個理由。
"""
from __future__ import annotations

import re

#: 身分公式的版本。**改動判定規則就要升版** —— 舊 state 的鍵是用舊公式
#: 算的,不升版就沒有人知道混在一起的兩批鍵各自是什麼意思。
#: v6(第二十五輪 P1-2/P1-3):帶對象的動作把對象寫進鍵;
#: legacy 認領要動作相符(主體有交集不代表同一件事)。
#: v7(2026-08-08,外審 P1-4):未知動作的鍵加月份、對象依種類過濾、
#: 記錄帶 `incident_tokens`。**公式變了就要進版** —— 不進版的話
#: `adopt_legacy` 會因為 `identity_schema >= VERSION` 而跳過既有記錄,
#: 每一條 lineage 在上線當天從第 1 天重算,而沒有人看得出原因。
IDENTITY_SCHEMA_VERSION = 7

# ---------------------------------------------------------------- 相容出口
#
# 動作表與動作辨識搬到 `event_actions`(見該檔:宣告式資料與身分計算
# 是兩件事,失效方式也不同)。此處再匯出,既有 import 路徑不變。
from event_actions import (                       # noqa: E402,F401
    ACTION_TABLE, CANONICAL_SUBJECTS, NEEDS_OBJECT, canonical_subject,
    event_action)


#: **通用新聞動詞**:每一則都有,不指認任何事件。與主體名一樣,
#: 它們在標題重疊裡是雜訊 —— 而雜訊剛好足以讓兩件不相干的事
#: 越過門檻(第二輪外審:「伊朗宣布軍演」vs「伊朗宣布荷姆茲協議」
#: 共同詞是「伊朗/朗宣/宣布」,重疊 0.38 > 0.35)。
GENERIC_NEWS_WORDS = (
    "宣布", "表示", "指出", "公布", "傳出", "據悉", "傳", "證實", "強調",
    "今日", "昨日", "近期", "最新", "報導", "消息", "傳聞", "回應",
    "announce", "announces", "announced", "says", "said", "report",
    "reports", "reported", "unveil", "unveils", "confirms", "confirmed",
)

#: 一則標題至少要留下幾個辨識詞才拿來比對。低於這個數代表整個標題
#: 幾乎只有主體與通用詞 —— **那時任何重疊都不構成證據**。
MIN_DISCRIMINATIVE = 2


def discriminative_tokens(title, subjects=()) -> set:
    """標題裡**真正指認事件**的 token(去掉主體名與通用新聞動詞)。

    第二輪外審 F2/F3:主體相交已經在上一層判過了,標題重疊若又被
    主體名灌滿,等於**把同一份證據算兩次** —— 而「台積電宣布法說會」
    與「台積電宣布擴建新廠」的共同詞正好全部是這一類(重疊 0.50)。

    **從文字裡挖掉再切詞**,不是切完再過濾:中文用二元組,
    「台積電宣布」會產生「電宣」這種跨越主體與動詞的詞,事後濾不掉。
    """
    from news_clusters import _tokens
    text = str(title or "")
    names = [str(s) for s in (subjects or ()) if str(s).strip()]
    # 主體的別名一起挖(今天寫 TSMC、昨天寫台積電)
    import entity_alias as _ea
    for grp in _ea.ALIAS_GROUPS:
        if any(n in grp for n in names):
            names.extend(grp)
    # **正規化是多對一,反查要展開**(第五輪外審 F1)。主體傳進來的是
    # canonical(「美國」「伊朗」),而英文標題寫的是 `US` / `Iran` ——
    # 只挖 canonical 的話,英文標題的主體詞留在辨識詞裡,兩件不相干的
    # 英文事件會靠共用的國名越過門檻。
    canon = {canonical_subject(n) for n in names}
    names.extend(raw for raw, c in CANONICAL_SUBJECTS.items() if c in canon)
    for w in sorted(set(names) | set(GENERIC_NEWS_WORDS), key=len, reverse=True):
        if not w:
            continue
        if w.isascii():
            # **英文要不分大小寫、要詞界**:表裡存的是小寫 `us`/`iran`,
            # 標題寫的是 `US`/`Iran`;逐字替換的話一個都挖不掉
            # (第五輪外審 F1 的修正第一版就是這樣,實測沒生效)。
            # 詞界則是避免 `us` 把 `focus`/`versus` 挖出洞。
            text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(w)
                          + r"(?![A-Za-z0-9])", " ", text, flags=re.IGNORECASE)
        else:
            text = text.replace(w, " ")
    return _tokens(text)


#: 兩個標題要重疊到這個比例才算「同一個故事的兩種寫法」。
#: 訂 0.35 比分群的 0.5 寬:這裡兩邊已經同型別、同主體,而且是**跨日**
#: 的兩則報導(用詞差異本來就比同日大)。仍然擋得住「軍演 vs 通行談判」。
SHADOW_TITLE_OVERLAP = 0.35


def title_related(a, b, subjects=()) -> bool:
    """兩個標題講的是同一個故事嗎。

    **只用辨識詞比**(第二輪外審 F3)—— 主體與通用動詞是上一層已經
    算過的證據,拿它們再算一次會讓兩件不相干的事越過門檻。
    辨識詞太少(任一邊 < `MIN_DISCRIMINATIVE`)時**不敢說是**:
    那個標題幾乎只有主體與套語,任何重疊都不構成證據。
    """
    ta = discriminative_tokens(a, subjects)
    tb = discriminative_tokens(b, subjects)
    if len(ta) < MIN_DISCRIMINATIVE or len(tb) < MIN_DISCRIMINATIVE:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= SHADOW_TITLE_OVERLAP


_title_related = title_related        # 既有呼叫端


def match_days(records, entities, titles) -> int:
    """事件群接得上哪一筆 timeline 記錄的第幾天(接不上回 0)。

    **主體相交是必要條件,不是充分條件**(外審補審 F3/F4)。同一個主體
    可以同時有兩個活躍事件 —— 荷姆茲通行第 7 天、對同一國的制裁第 2 天。
    先前兩個消費端(`fetch_plan.timeline_map` 與 packet 的 `_days`)都把
    state 壓成 `{主體: 天數}`,於是制裁案第一天就被標成「第 7 天」、
    拿到全文優先權,而 `event_identity` 引進 action/object 的**全部理由**
    就是主體身分會把不同事件併在一起。

    判準:主體相交 **且動作相同**(含兩邊都認不出來)。今天點得出動作、
    記錄沒有(或相反)時**不接** —— 低估天數只是少一句「第 N 天」,
    接錯會讓今天才發生的事顯示成追蹤一週。兩種錯誤的代價不對稱。

    多筆同時命中(同動作、同主體的兩筆)時取**最小**天數:那代表身分
    仍未分辨得開,保守側是少算。
    """
    # **兩邊都要正規化**(第三輪外審 F1)。`timeline_identity` 存的是
    # `canonical_subject` 之後的主體(「美國」),而今天的實體保留原文
    # 拼寫(`news_normalize` 刻意不動它)—— 英文報導的
    # `United States` 因此接不上,一條延燒了 7 天的事件回 0 天、
    # 掉出全文優先權。**正規化是身分的一部分,不是顯示的細節。**
    ents = {canonical_subject(str(e)) for e in (entities or ())
            if str(e).strip()}
    # **不因為實體集合是空的就早退**:實體抽取會漏,標題不會 ——
    # `_subjects_meet` 的第三層(標題含記錄的主體名)是正當的比對路徑。
    keys = expand_alias(ents)
    today_action = event_action(titles)
    hits = []
    for r in (records or []):
        if not isinstance(r, dict):
            continue
        subs = {canonical_subject(str(x)) for x in (r.get("subjects") or [])
                if str(x).strip()}
        if not subs and r.get("entity"):
            subs = {canonical_subject(str(r["entity"]))}
        if not (_subjects_meet(ents, keys, subs, titles)):
            continue
        rec_action = str(r.get("action") or "") or event_action(
            r.get("latest_title"), r.get("latest_summary"))
        if rec_action != today_action:
            continue
        # **帶對象的動作要比對象**(第二輪外審 F1)。「美國對台軍售」
        # 與「美國對日軍售」都是 `arms_sale`、都含「美國」——
        # 只比動作的話,今天才發生的對日軍售會繼承對台那條的 7 天。
        # `NEEDS_OBJECT` 存在的理由就是這個。
        if rec_action in NEEDS_OBJECT:
            mine = object_signature(rec_action, ents or [])
            theirs = object_signature(rec_action, subs)
            if not mine or not theirs or mine != theirs:
                continue          # 對象對不上、或算不出來 → 保守不接
        hits.append(int(r.get("days") or 0))
    return min(hits) if hits else 0


def expand_alias(names) -> set:
    import entity_alias as _ea
    return _ea.expand(names)


def _subjects_meet(ents: set, keys: set, subs: set, titles: str) -> bool:
    """主體相交(精確 / 別名組 / 標題含記錄的主體名)。

    標題那一層維持 ASCII token 邊界 —— 裸子字串會讓 `US` 命中 `ASUS`。
    """
    import entity_alias as _ea
    if ents & subs:
        return True
    if keys & _ea.expand(subs):
        return True
    text = str(titles or "")
    # 記錄存的是 canonical 主體,而標題寫的是原文拼寫 —— 兩邊都比
    # (`canonical_subject` 是多對一,反查不了,所以比原字串也比標準名)。
    for s in {x for s0 in subs for x in (s0, canonical_subject(s0)) if x}:
        if not s.isascii():
            if s in text:
                return True
        elif re.search(r"(?<![A-Za-z0-9])" + re.escape(s)
                       + r"(?![A-Za-z0-9])", text, re.IGNORECASE):
            return True
    return False


#: 兩組辨識詞要重疊到這個比例才算**同一樁**。
#: 比遮蔽的 0.35 寬一點:這裡兩邊已經同型別、同動作、同對象、同月,
#: 剩下要分的只是「這個月對同一個標的的第幾起事件」——
#: 而同一樁的後續報導幾乎必然共用核心詞(「勒索軟體」「產線停擺」)。
INCIDENT_OVERLAP = 0.3


def incident_suffix(tokens) -> str:
    """同鍵下另一樁事件的穩定後綴(由辨識詞決定,不用序號)。

    序號會隨處理順序漂移 —— 昨天的 `#2` 明天可能指到另一樁。
    """
    import hashlib
    core = "|".join(sorted(str(t) for t in (tokens or []))[:4])
    return hashlib.sha1(core.encode("utf-8")).hexdigest()[:6]


def same_incident(tokens_a, tokens_b) -> bool:
    """兩組辨識詞講的是同一樁事件嗎(任一邊太少 → **當成同一樁**)。

    **不確定時併,不切** —— 這裡與遮蔽的方向相反,理由是後果不同:
    遮蔽切錯會讓一樁真事件從信裡消失;這裡切錯會讓一條延燒中的線
    每天從第 1 天重來,而那會讓「延燒第 N 天」整個功能失去意義。
    """
    a = {str(t) for t in (tokens_a or [])}
    b = {str(t) for t in (tokens_b or [])}
    if len(a) < MIN_DISCRIMINATIVE or len(b) < MIN_DISCRIMINATIVE:
        return True
    return len(a & b) / min(len(a), len(b)) >= INCIDENT_OVERLAP


#: 一則報導最多取幾個主體進身分。多了會讓同一件事因為某一則多抓到
#: 一個配角而分裂;這個上限與排序讓它變成**確定性**的。
MAX_SUBJECTS = 4


def canonical_subjects(subjects) -> list:
    """主體清單 → 去重、正規化、排序、截斷的**組代表寫法**。

    抽出來是因為它有第二個呼叫端(跨語言橋接)——**判準只能有一份**:
    兩邊各寫一次的話,「同一件事」在分群與在 timeline 會是兩個答案。
    """
    return sorted(dict.fromkeys(
        c for c in (canonical_subject(s) for s in (subjects or [])) if c)
    )[:MAX_SUBJECTS]


def object_signature(action: str, subjects) -> str:
    """帶對象的動作 → 對象簽章;不帶對象的動作 → 空字串。

    **對象只取該動作真正的標的種類**(外審 P1-4C):法域類的動作
    (軍售/制裁/關稅…)只看國家,廠商名不進簽章 —— 同一批軍售不會
    因為某一則多抓到一個承包商就分裂成兩條線。判準在
    `event_actions.OBJECT_SCOPE`;那不是剖析受詞,是限定種類。

    **簽章是主體集合本身**(已正規化、已排序、已截斷)。為什麼不是
    「挑出受詞」:那需要語意剖析,而剖析錯會把兩件事黏在一起 ——
    這正是要修的缺陷。用整個主體集合當簽章是保守的:
    同一件事的兩則報導若主體集合不同會**分裂**(退回今天以前的行為),
    而不同的事**不會合併**。兩種錯誤的代價不對稱。
    """
    act = str(action or "")
    if act not in NEEDS_OBJECT:
        return ""
    names = [str(s) for s in (subjects or []) if str(s).strip()]
    from event_actions import OBJECT_SCOPE
    if OBJECT_SCOPE.get(act) == "jurisdiction":
        # 只留法域(國家)—— 廠商名不是這類動作的標的。
        # 一個都不是法域時**不縮**:那時整個主體集合就是我們知道的全部,
        # 硬縮成空字串會讓鍵退化成「同月同動作全部一條」。
        juris = [n for n in names if n in set(CANONICAL_SUBJECTS.values())]
        names = juris or names
    return "、".join(sorted(dict.fromkeys(names)))[:24]


def display_label(record) -> str:
    """**給讀者看的事件名稱**(2026-08-08 生產抓到)。

    信裡的「延燒中事件」先前印的是 `key.split(":", 1)[-1]` —— 舊的兩段式
    鍵剛好切出主體(「伊朗」),而新的三段式鍵切出來是
    `hormuz_passage:2026-08`,**內部識別碼直接進了信**。

    鍵是給程式用的,標籤是給人看的;這兩件事先前是同一個字串,
    所以沒有人發現它們其實是兩個東西。
    """
    r = record if isinstance(record, dict) else {}
    label = action_label(str(r.get("action") or ""))
    subs = [str(x) for x in (r.get("subjects") or []) if str(x).strip()]
    who = "、".join(subs[:2])
    if label and who:
        return f"{who}{label}"
    if label:
        return label
    if who:
        # **主體 fallback 的線要說得出自己是哪一件事**(外審補審 F5)。
        # 2026-08-08 的抱怨是「同一件事兩個矛盾的第 N 天」,而它之所以
        # 讀起來矛盾,是因為兩條都只寫得出主體名。遮蔽掉是一種解法,
        # 但那會連**真的另一樁**同主體事件一起藏掉(外審抓到)。
        # 改成:留著,但帶上自己的標題片段 —— 兩條就分得開,
        # 讀者看到的是兩件事,不是同一件事的兩個矛盾天數。
        hint = _title_hint(r.get("latest_title"))
        return f"{who}:{hint}" if hint else who
    # 連主體都沒有時才退回鍵,而且只取最後一段的**非日期**部分。
    tail = [p for p in str(r.get("key") or "").split(":")[1:]
            if p and not p[:4].isdigit()]
    return "、".join(tail) or "事件"


#: 主體 fallback 線的標題片段長度。**短到一眼看完**,長到分得出是哪件事。
TITLE_HINT_CHARS = 18


def _title_hint(title) -> str:
    """標題的前幾個字(去掉發布者尾綴與空白);沒有標題回空字串。"""
    t = str(title or "").strip()
    if not t:
        return ""
    t = re.split(r"\s[-–—|]\s", t)[0].strip()
    return t[:TITLE_HINT_CHARS] + ("…" if len(t) > TITLE_HINT_CHARS else "")


def action_label(code: str) -> str:
    for row in ACTION_TABLE:
        if row[0] == code:
            return row[1]
    return ""


def _month(day: str) -> str:
    d = str(day or "")
    return d[:7] if re.match(r"^\d{4}-\d{2}", d) else ""


def timeline_identity(event: dict, subjects, today: str = "") -> dict:
    """`{key, action, subjects, basis}` —— 延燒事件的身分。

    `subjects` 由呼叫端給(已正規化過的清單),這裡再做一次跨語言正規化。
    **認得出動作就以動作為主鍵**,認不出才退回主體集合。
    """
    ev = event if isinstance(event, dict) else {}
    etype = str(ev.get("event_type") or "general")
    canon = canonical_subjects(subjects)
    title = ev.get("title")
    action = event_action(title, ev.get("summary"))
    month = _month(today or str(ev.get("published") or ""))
    obj = ""
    if action:
        # 第二十五輪 P1-2:**帶對象的動作要把對象寫進鍵。**
        # 少了它,同月的每一樁軍售/資安/關稅案都是同一條線。
        obj = object_signature(action, canon)
        key = ":".join(x for x in (etype, action, obj, month) if x)
        basis = "action+object" if obj else "action"
    else:
        # **未知動作的鍵加上月份**:純主體的鑰匙會把同一個國家跨月的
        # 每一件事串成一條永久 lineage(外審 P1-4A)。月份不能分開
        # 同月的兩件事 —— 那一層由 `incident_tokens` 在呼叫端判(見下)。
        key = ":".join(x for x in (etype, "、".join(canon)[:20], month) if x)
        basis = "subjects"
    # **辨識詞跟著回傳,但不進鍵。**
    #
    # 第一版把指紋雜湊寫進鍵,結果同一樁攻擊的後續報導(標題多了「再遭」
    # 兩個字)拿到不同的鍵 —— 而後續報導正是「延燒第 N 天」的常態,
    # 那個修法會讓每一條線每天都從第 1 天重來,**比原本的缺陷更糟**。
    #
    # 鍵維持粗粒度,「是不是同一樁」交給呼叫端用辨識詞比對:
    # 相關 → 同一條線;不相關 → 這個鍵下的**另一樁**,另開一條。
    return {"key": key, "action": action, "subjects": canon,
            "object": obj, "basis": basis,
            "incident_tokens": sorted(discriminative_tokens(title, canon))}


def drop_shadowed(active: list) -> list:
    """**主體 fallback 不得與已識別的事件並列**(2026-08-08 第二封信)。

    `timeline_identity` 認不出動作時退回主體集合當鍵 —— 而同一個故事的
    標題**有時點得出動作、有時點不出**:
        「認了飛彈庫存吃緊,荷姆茲有望重啟」 → `hormuz_passage`
        「川普稱與伊朗戰爭很快將結束」       → 退回 `伊朗`
    於是兩條線永遠並存,信裡出現「伊朗(第 7 天)」與
    「伊朗荷姆茲海峽通行(第 2 天)」兩個互相矛盾的天數。

    `supersede_legacy` 接不到它,因為主體那條**已經被蓋成新 schema**
    (它是今天才走 fallback 的,不是舊格式的遺留)。

    **只做顯示層的遮蔽,不合併天數。** 合併要冒的風險是把另一樁伊朗事件
    的天數接到荷姆茲上;而主體那條的意思本來就是「今天認不出這是什麼」,
    在旁邊已經有一條認得出來的線時,它對讀者沒有增加任何東西。
    """
    rows = [r for r in (active or []) if isinstance(r, dict)]
    named = [r for r in rows if str(r.get("action") or "").strip()]
    out = []
    for r in rows:
        if str(r.get("action") or "").strip():
            out.append(r)
            continue
        subs = {str(x) for x in (r.get("subjects") or []) if str(x).strip()}
        etype = str(r.get("event_type") or "")
        # **主體相交不足以證明是同一個故事**(外審補審 F5)。
        # 「伊朗荷姆茲通行」與「伊朗革命衛隊軍演」同型別、同主體、
        # 而後者的動作不在 ACTION_TABLE 裡 —— 只比主體的話,一樁真的
        # 事件會從信裡整條消失。**隱藏真事件比顯示兩條更糟**:
        # 兩條線讀者看得出來混亂,消失的那條讀者不知道它存在過。
        # 因此再要求標題有實質重疊(同一個故事的兩種寫法會共用詞)。
        shadowed = any(
            str(n.get("event_type") or "") == etype
            and subs & {str(x) for x in (n.get("subjects") or [])}
            and title_related(r.get("latest_title"), n.get("latest_title"),
                              subs | {str(x) for x in (n.get("subjects") or [])})
            for n in named)
        if not shadowed:
            out.append(r)
    return out


def supersede_legacy(state: dict, ev: dict, subjects: list,
                     ident: dict) -> list:
    """**接不到的舊線要收掉,不能讓它繼續自己活著。**

    `adopt_legacy` 現在要求動作相符(第二十五輪 P1-3)—— 那是對的:
    制裁案的天數不該接到軍售案上。但接不到之後,舊鍵仍留在 state 裡
    繼續累計、繼續渲染,於是信裡出現**同一件事的兩個「第 N 天」**
    (2026-08-08 實測:「伊朗(第 7 天)」與「hormuz_passage(第 2 天)」)。

    這裡把「同型別 + 主體有交集 + 舊動作認不出來」的舊鍵移除。
    刻意**不碰動作認得出來而且不同**的舊鍵 —— 那是真的另一件事,
    它應該繼續有自己的天數。回傳被收掉的鍵。
    """
    etype = str(ev.get("event_type") or "")
    want = {str(x) for x in (ident.get("subjects") or subjects)}
    gone = []
    for k, v in list(state.items()):
        if not isinstance(v, dict) or k == ident.get("key"):
            continue
        if _int(v.get("identity_schema")) >= IDENTITY_SCHEMA_VERSION:
            continue
        if str(k).split(":", 1)[0] != etype:
            continue
        old_subjects = {canonical_subject(str(x))
                        for x in (v.get("subjects") or [])} or {
            canonical_subject(str(k).split(":", 1)[-1])}
        if not (old_subjects & want):
            continue
        if event_action(v.get("latest_title"), v.get("latest_summary")):
            continue                      # 認得出動作 → 是別件事,留著
        state.pop(k, None)
        gone.append(k)
    return gone


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def adopt_legacy(state: dict, ev: dict, subjects: list,
                           ident: dict) -> tuple:
    """升版當天把舊鍵的天數接過來,回 `(紀錄, 被接走的舊鍵)`。

    只接**同型別、且主體有交集**的舊鍵 —— 那是「同一條線換了身分公式」
    的保守判準。接不到就是新的一條(從第 1 天起算),那也是誠實的答案。
    同時符合的舊鍵不只一個時取 `days` 最大的:那條才是讀者看過的那個天數。
    """
    etype = str(ev.get("event_type") or "")
    want = {str(x) for x in (ident.get("subjects") or subjects)}
    new_action = str(ident.get("action") or "")
    best_key, best = "", None
    for k, v in state.items():
        if not isinstance(v, dict):
            continue
        if _int(v.get("identity_schema")) >= IDENTITY_SCHEMA_VERSION:
            continue                      # 已經是新公式的鍵,不是遷移對象
        if str(k).split(":", 1)[0] != etype:
            continue
        old_subjects = {str(x) for x in (v.get("subjects") or [])} or {
            str(k).split(":", 1)[-1]}
        old_subjects = {canonical_subject(x) for x in old_subjects}
        if not (old_subjects & want):
            continue
        # 第二十五輪 P1-3:**主體有交集不代表是同一件事。**
        # 舊鍵「geopolitical:美國(制裁案,第 4 天)」與今天的軍售案都含
        # 「美國」,於是軍售案第一天就顯示「延燒第 5 天」—— 重構本來要
        # 消掉的錯誤,只是從穩態身分搬到了遷移。
        old_action = event_action(v.get("latest_title"), v.get("latest_summary"))
        if new_action or old_action:
            if old_action != new_action:
                continue                  # 動作對不上就不認領
        if best is None or _int(v.get("days")) > _int(best.get("days")):
            best_key, best = k, v
    if best is None:
        return None, ""
    rec = dict(best)
    if not event_action(best.get("latest_title"), best.get("latest_summary")):
        # 舊 record 認不出動作 —— 接了也說不出接的是什麼。
        # **留下痕跡**:天數照舊(讀者看過那個數字),但標記不確定。
        rec["migration_uncertain"] = True
    return rec, best_key
