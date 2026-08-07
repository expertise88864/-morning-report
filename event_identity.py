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
IDENTITY_SCHEMA_VERSION = 5

#: 動作表:`(代碼, 說明, 關鍵詞…)`。中英並列,**由上而下第一個命中者勝**
#: —— 順序即優先序,具體的排在概括的前面。
#:
#: 判準刻意保守:寧可認不出(降級回主體集合,行為與舊版相同),
#: 不要誤認(把兩件事黏成一件是不可逆的,而且會靜靜地錯很多天)。
ACTION_TABLE = (
    ("hormuz_passage", "荷姆茲海峽通行",
     "荷姆茲", "荷莫茲", "霍爾木茲", "Hormuz"),
    ("arms_sale", "對台軍售",
     "軍售", "對台軍售", "arms sale", "arms package", "FMS"),
    ("cyberattack", "網路攻擊",
     "網攻", "網路攻擊", "駭客入侵", "勒索軟體", "資安事件",
     "cyberattack", "ransomware", "data breach"),
    ("export_control", "出口管制",
     "出口管制", "禁售", "實體清單", "管制清單",
     "export control", "entity list", "chip ban"),
    ("tariff_action", "關稅措施",
     "關稅", "課稅", "反傾銷", "tariff", "anti-dumping"),
    ("fx_intervention", "匯市干預",
     "匯市干預", "干預匯市", "聯合干預", "fx intervention",
     "yen-market intervention", "currency intervention"),
    ("sanction", "制裁",
     "制裁", "凍結資產", "sanction", "asset freeze"),
    ("strait_tension", "台海情勢",
     "台海", "軍演", "共機", "灰色地帶", "Taiwan Strait", "military drill"),
    ("election", "選舉",
     "大選", "選舉", "投票日", "election", "referendum"),
    ("summit_talks", "峰會與談判",
     "峰會", "元首會談", "貿易談判", "會談", "summit", "trade talks",
     "negotiation"),
)

#: 跨語言的主體正規化。**只放看得出來的對照**,推不出來就原樣留著 ——
#: 猜一個對照會把兩個不同的主體黏成一個,而那比分裂更難發現。
#: (別名同組的公司代號/中文名由 `entity_alias` 負責,這裡只補國家與機構。)
CANONICAL_SUBJECTS = {
    "iran": "伊朗", "united states": "美國", "u.s.": "美國", "us": "美國",
    "usa": "美國", "america": "美國", "oman": "阿曼", "japan": "日本",
    "china": "中國", "prc": "中國", "beijing": "中國", "taiwan": "台灣",
    "south korea": "南韓", "korea": "南韓", "russia": "俄羅斯",
    "ukraine": "烏克蘭", "israel": "以色列", "eu": "歐盟",
    "european union": "歐盟", "germany": "德國", "india": "印度",
    "白宮": "美國", "華府": "美國", "北京": "中國", "美方": "美國",
    "中方": "中國", "日方": "日本",
}


def canonical_subject(name: str) -> str:
    """把主體正規化成同一種寫法(認不出就原樣回,不猜)。"""
    raw = str(name or "").strip()
    if not raw:
        return ""
    return CANONICAL_SUBJECTS.get(raw.lower(), raw)


def event_action(*texts) -> str:
    """這則報導在講**什麼動作**;認不出來回空字串。

    由上而下第一個命中者勝(順序即優先序)。認不出來是合法答案 ——
    呼叫端會降級回主體集合,那與舊版行為相同。
    """
    blob = " ".join(str(t or "") for t in texts).lower()
    if not blob.strip():
        return ""
    for row in ACTION_TABLE:
        code, words = row[0], row[2:]
        if any(w.lower() in blob for w in words):
            return code
    return ""


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
    canon = sorted(dict.fromkeys(
        c for c in (canonical_subject(s) for s in (subjects or [])) if c))[:4]
    action = event_action(ev.get("title"), ev.get("summary"))
    if action:
        month = _month(today or str(ev.get("published") or ""))
        key = f"{etype}:{action}" + (f":{month}" if month else "")
        basis = "action"
    else:
        key = f"{etype}:{'、'.join(canon)[:20]}"
        basis = "subjects"
    return {"key": key, "action": action, "subjects": canon, "basis": basis}


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
        if best is None or _int(v.get("days")) > _int(best.get("days")):
            best_key, best = k, v
    return (dict(best) if best else None), best_key
