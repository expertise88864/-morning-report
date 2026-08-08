# -*- coding: utf-8 -*-
"""**同一件事的兩種寫法**(第二十一輪 P2-8)。

`EVENT_TIMELINE` 存的是抽取器**當天**用的那個實體名。隔天別家媒體寫
「德黑蘭」而不是「伊朗」、寫 `TSMC` 而不是「台積電」時,精確比對就接不上
—— 而接不上的症狀是「第 4 天的事件顯示成第 0 天」,模型於是又從頭
講一次背景。

**刻意只做一張小表。** 這裡不做模糊比對:「台積電」與「台達電」差一個字,
而它們是兩家公司。**誤併比漏併危險**,所以只收互為官方別名的組合。
"""
from __future__ import annotations

#: 每一組是同一個主體。**組內任兩個互為別名**,順序無意義。
#: 第二十二輪 P1-9:**國家/首都/機構整批拿掉。** 「伊朗戰事」與
#: 「德黑蘭地震」是兩件事;「US」還會命中「ASUS」。城市不是國家的
#: 別名,是**不同實體** —— 這張表只收「同一個主體的不同寫法」。
ALIAS_GROUPS = (
    ("台積電", "TSMC", "2330", "台積"),
    ("聯發科", "MediaTek", "2454"),
    ("鴻海", "Foxconn", "2317"),
    ("日月光", "ASE", "3711"),
    ("聯電", "UMC", "2303"),
    ("輝達", "NVIDIA", "Nvidia", "NVDA"),
    ("聯準會", "Fed", "FOMC", "美聯儲"),
    ("南亞科", "2408"),
    ("聯詠", "3034"),
    ("緯創", "3231"),
    ("華碩", "ASUS", "2357"),
    ("國巨", "2327"),
    ("大立光", "3008"),
    ("長榮", "2603"),
    ("陽明", "2609"),
    ("台新新光金", "2887"),
    ("富邦金", "2881"),
    # 深度優化(橫向):跨語言合併的前提是實體組有交集,而外媒寫英文名。
    # 只收**歧義風險低**的:Delta(台達電)會撞航空與變體、Google 會撞
    # 聚合器字串,刻意不收。
    ("SK海力士", "SK Hynix", "SK hynix", "海力士"),
    ("美光", "Micron", "MU"),
    ("三星電子", "Samsung Electronics", "三星"),
    ("英特爾", "Intel", "INTC"),
    ("超微", "AMD"),
    ("蘋果", "Apple", "AAPL"),
    ("微軟", "Microsoft", "MSFT"),
    ("亞馬遜", "Amazon", "AMZN"),
    ("特斯拉", "Tesla", "TSLA"),
    ("廣達", "Quanta", "2382"),
    ("緯穎", "Wiwynn", "6669"),
)

#: `別名 → 組編號`。**一個別名只能屬於一組** —— 屬於兩組等於把兩個
#: 主體接起來,而那是誤併。
_INDEX: dict = {}
for _i, _grp in enumerate(ALIAS_GROUPS):
    for _name in _grp:
        _INDEX.setdefault(str(_name), _i)


def group_of(name) -> int:
    """這個名字屬於哪一組(-1 = 不在表裡)。"""
    return _INDEX.get(str(name or ""), -1)


def canonical(name) -> str:
    """這個名字的**組代表寫法**(不在表裡的原樣回傳)。

    「2330」與「台積電」是同一檔 —— 而這件事先前在三個地方各寫了一份
    (衝突偵測、淨效果契約、`affected_assets` 的重複檢查),
    其中一份漏掉正規化,同一則新聞就能靠兩種寫法同時站在多空兩側。
    """
    gi = group_of(name)
    return ALIAS_GROUPS[gi][0] if gi >= 0 else str(name or "")


def expand(names) -> set:
    """一組實體名 → 它們所屬的組編號集合。**不在表裡的不產生組**。"""
    return {g for g in (group_of(n) for n in (names or ())) if g >= 0}


def same(name, groups) -> bool:
    """`name` 與這些組裡的任一個是同一個主體嗎。"""
    g = group_of(name)
    return g >= 0 and g in (groups or set())
