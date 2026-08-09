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

import re

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
#: 帶對象的動作,**它的對象是什麼種類**(外審 P1-4C)。
#:
#: `object_signature` 先前直接用整個主體集合當簽章 —— 於是同一批軍售,
#: 報導 A 的實體是 `[美國, 台灣]`、報導 B 多抓到 `[…, 洛克希德馬丁]`,
#: 兩把鑰匙就不同,同一件事分裂成兩條線。
#:
#: 修法**不是剖析受詞**(這個 repo 拒絕過:剖析錯會把兩件事黏在一起),
#: 而是限定**種類**:軍售/制裁/關稅/出口管制/峰會/匯率干預/選舉的對象
#: 都是**法域**(國家),而 `CANONICAL_SUBJECTS` 正好就是那份宣告。
#: 廠商名不是法域,自然被排除 —— 不必猜哪一個才是受詞。
#:
#: `cyberattack` 的對象是**公司**,所以它留在 `any`(全部主體)。
#: **只有「對象在定義上就是法域」的動作才過濾**(第二輪外審 F4)。
#: 制裁、出口管制、關稅**可以直接針對一家公司或一個實體**
#: (實體清單就是這樣運作的)—— 一律縮成法域會讓
#: 「美國制裁甲公司」與「美國制裁乙公司」共用一把鑰匙,
#: 那是我為了修 C(承包商雜訊)引進的新 over-merge。
#:
#: 留在 `jurisdiction` 的四個,對象只可能是國家:軍售的受援國、
#: 峰會的與會國、選舉的國家、匯率干預的央行所在國。
OBJECT_SCOPE = {
    "arms_sale": "jurisdiction",
    "summit_talks": "jurisdiction",
    "election": "jurisdiction",
    "fx_intervention": "jurisdiction",
    "sanction": "any",
    "tariff_action": "any",
    "export_control": "any",
    "cyberattack": "any",
}

NEEDS_OBJECT = frozenset({
    "arms_sale", "cyberattack", "tariff_action", "export_control",
    "sanction", "election", "summit_talks", "fx_intervention",
})

ACTION_TABLE = (
    # 這一條**自帶唯一對象**(海峽只有一個),所以不在 `NEEDS_OBJECT`。
    ("hormuz_passage", "荷姆茲海峽通行",
     "荷姆茲", "荷莫茲", "霍爾木茲", "Hormuz"),
    # 英文報導常寫 "weapons package" / "arms package" 而不是 "arms sale"
    #(外審 P1-4 的反例就是這個寫法)。
    ("arms_sale", "軍售",
     "weapons package", "arms package", "weapons sale",
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
    # **`軍演` 單獨出現不是台海事件**(2026-08-08 自查):
    # 「伊朗革命衛隊舉行軍演」被判成 `strait_tension` —— 一個伊朗的軍事
    # 演習因此進了台海的 lineage,而錯誤分類會一路污染 continuing days
    # 與全文優先權。台海是**地點**,不是動作:判準要帶得出那個地點。
    ("strait_tension", "台海情勢",
     "台海", "共機", "灰色地帶", "Taiwan Strait", "military drill"),
    ("election", "選舉",
     "大選", "選舉", "投票日", "election", "referendum"),
    ("summit_talks", "峰會與談判",
     "峰會", "元首會談", "貿易談判", "會談", "summit", "trade talks",
     "negotiation"),
)

#: 跨語言的主體正規化。**只放看得出來的對照**,推不出來就原樣留著 ——
#: 猜一個對照會把兩個不同的主體黏成一個,而那比分裂更難發現。
#: (別名同組的公司代號/中文名由 `entity_alias` 負責,這裡只補國家與機構。)
#: 這張表以外的法域寫法(**只為了「這不是可交易標的」這個判準**,
#: 不進身分鍵 —— 進鍵要另外想清楚別名合併的後果)。
#: 第二十七輪外審 P1-5:`US` 精確出現在 entities 與標題裡,於是被當成
#: 可渲染的逐標的方向卡 —— 而它是國家,不是可交易標的。
EXTRA_JURISDICTIONS = frozenset({
    "uk", "united kingdom", "britain", "england", "france", "italy",
    "spain", "canada", "australia", "brazil", "mexico", "indonesia",
    "thailand", "vietnam", "singapore", "malaysia", "philippines",
    "netherlands", "switzerland", "sweden", "poland", "turkey",
    "saudi arabia", "uae", "egypt", "south africa", "argentina",
    "英國", "法國", "義大利", "西班牙", "加拿大", "澳洲", "巴西",
    "墨西哥", "印尼", "泰國", "越南", "新加坡", "馬來西亞", "菲律賓",
    "荷蘭", "瑞士", "瑞典", "波蘭", "土耳其", "沙烏地", "阿聯",
    "埃及", "南非", "阿根廷", "紐西蘭", "香港", "澳門",
    # 地區寫法(「歐洲央行」的「歐洲」不是 `CANONICAL_SUBJECTS` 的
    # 「歐盟」)—— 這張表的用途是「這不是可交易標的 / 這不是台灣」,
    # 地區名在這兩個判準上與國家同義。
    "歐洲", "europe", "亞洲", "asia", "中東", "middle east",
    "北美", "north america", "拉美", "latin america",
})


def is_jurisdiction(name) -> bool:
    """這個字是一個**法域**嗎(國家/地區)。

    法域可以是事件的主體與對象,但**永遠不是可交易標的** ——
    而判準要走宣告過的表,不是開放式黑名單(第二十七輪外審 P1-5)。
    """
    n = str(name or "").strip()
    if not n:
        return False
    low = n.lower()
    return (low in CANONICAL_SUBJECTS or n in CANONICAL_SUBJECTS
            or n in set(CANONICAL_SUBJECTS.values())
            or low in EXTRA_JURISDICTIONS or n in EXTRA_JURISDICTIONS)


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
        if any(_kw_hit(str(w).lower(), blob) for w in words):
            return code
    return ""


def _kw_hit(word: str, blob: str) -> bool:
    """ASCII 關鍵詞要 **token 邊界**;中文無詞界,維持子字串。

    **這不是修一個已確認的缺陷** —— 外審說 `export_control` 含裸的 `ban`
    因而讓 `Bank earnings` 誤判,我查了表:沒有裸 `ban`(用的是片語
    `chip ban`),實測 `Bank earnings rise` 回空字串,那條駁回。
    但這個 repo 已經為同一個形狀修過三次(`ft` 命中 SoftBank、
    `raise` 命中 praise、`us` 命中 ASUS),而現在全靠「剛好沒有人加過
    一個會撞的單字」—— 把它變成結構上不可能發生比較便宜。
    """
    if not word:
        return False
    if not word.isascii():
        return word in blob
    # **複數/第三人稱要放行**:純詞界會讓 `sanction` 不再命中
    # `sanctions`、`ban` 不再命中 `bans` —— 那是硬化造成的真回歸
    # (實測抓到:一則英文制裁報導的動作變成空字串)。
    # 只放行一個 `s`:`ban`→`bans` 通,而 `bank` 的 `k` 仍然擋得住。
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(word) + r"s?(?![a-z0-9])",
                          blob))
