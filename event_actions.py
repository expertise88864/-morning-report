# -*- coding: utf-8 -*-
"""**事件動作的宣告表**(從 `event_identity` 拆出;行數棘輪逼出來的)。

拆分的切點不是主題喜好:`ACTION_TABLE` 是**宣告式資料**、
`event_action` / `action_label` 是**純字串函式** —— 它們回答的是
「這則報導在講什麼動作」;而 `event_identity` 回答的是
「這是不是同一件事」(鍵怎麼組、要不要遮蔽、天數接不接得上)。
兩者的失效方式也不同:這裡漏一個動作詞 → 退回主體 fallback;
那裡判準錯 → 兩件事被黏成一條線。

**新增動作詞只改這個檔。** 每一列都要說得出代碼與判準,
不做模糊比對(理由見 `event_identity` 的模組 docstring)。
"""
from __future__ import annotations

#: 動作表:`(代碼, 說明, 關鍵詞…)`。中英並列,**由上而下第一個命中者勝**
#: —— 順序即優先序,具體的排在概括的前面。
#:
#: 判準刻意保守:寧可認不出(降級回主體集合,行為與舊版相同),
#: 不要誤認(把兩件事黏成一件是不可逆的,而且會靜靜地錯很多天)。
#: `NEEDS_OBJECT` 的動作**必須帶對象才構成身分**(第二十五輪 P1-2)。
#:
#: 上一版的鍵是 `{型別}:{動作}:{月份}` —— 完全不含對象,於是同一個月裡
#: 「美國對台軍售」與「美國對日本軍售」是同一條線;三件不同公司的資安
#: 事件、三個國家的關稅案也全部黏在一起。**動作過粗與主體過粗一樣錯**,
#: 只是換了一個方向。
#:
#: 判準:動作若是「某人對某個對象做的事」,對象就是身分的一部分;
#: 動作若本身就指名了唯一標的(荷姆茲海峽),對象是常數、不必再帶。
NEEDS_OBJECT = frozenset({
    "arms_sale", "cyberattack", "tariff_action", "export_control",
    "sanction", "election", "summit_talks", "fx_intervention",
})

ACTION_TABLE = (
    # 這一條**自帶唯一對象**(海峽只有一個),所以不在 `NEEDS_OBJECT`。
    ("hormuz_passage", "荷姆茲海峽通行",
     "荷姆茲", "荷莫茲", "霍爾木茲", "Hormuz"),
    ("arms_sale", "軍售",
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
    # 台海情勢自帶地理對象,同上。
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
