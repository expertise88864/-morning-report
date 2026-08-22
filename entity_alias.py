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
    # 油價傳導鏈上的台股(見 `sector_map` 的商品邊)。別名組讓
    # 「華航」與 `2610` 是同一個主體 —— 新聞寫名字、模型寫代號。
    ("華航", "中華航空", "2610"),
    ("長榮航", "2618"),
    ("長榮", "2603"),
    ("陽明", "2609"),
    ("萬海", "2615"),
    ("台塑", "1301"),
    ("台塑化", "6505"),
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
    # **代號與期間縮寫撞名的公司要被宣告**(第二十六輪 P1-6 外審第四輪)。
    # `MTD` 既是 Mettler-Toledo 也是 month-to-date —— 沒有宣告的話,
    # 那家公司真的上新聞時會被判成「這裡的 MTD 是期間」。
    # 宣告是唯一的權威:從「公司名後面接括號」推導會讓 `Apple (TTM)` 過關。
    ("Mettler-Toledo", "MTD", "梅特勒-托利多"),
    ("廣達", "Quanta", "2382"),
    ("緯穎", "Wiwynn", "6669"),
)

#: `別名 → 組編號`。**一個別名只能屬於一組** —— 屬於兩組等於把兩個
#: 主體接起來,而那是誤併。
#: 2026-08-22:表裡原有兩組**逐字重複**的宣告(長榮/2603、陽明/2609)。
#: `setdefault` 讓後出現的那份完全不生效 —— 改到它的人不會看到任何
#: 變化,而那是最難查的一種。重複由 `tests/test_batch_2026_08_22b.py`
#: 的守衛擋住。組代表(每組第一個)自 2026-08-22 起是**持久鍵的一部分**
#: (見 `subject_identity.identity_name`),重排組內順序 = 靜默改寫 state。
_INDEX: dict = {}
#: 大小寫不敏感的**後備**索引(2026-08-22 外審 r1 P1)。持久化的 story 鍵
#: 是 `_norm` 過的**小寫**字串(`e:nvda|…`,生產有 116 筆;`e:aapl` 91 筆)
#: —— 逐字索引查不到 `nvda`,於是那些線的鍵永遠遷移不掉,而 producer
#: 明天寫的是 `e:輝達`,同一家公司再裂一次。先查逐字(保住宣告的寫法),
#: 查不到才 casefold。
_INDEX_CI: dict = {}
for _i, _grp in enumerate(ALIAS_GROUPS):
    for _name in _grp:
        _INDEX.setdefault(str(_name), _i)
        _INDEX_CI.setdefault(str(_name).casefold(), _i)


def group_of(name) -> int:
    """這個名字屬於哪一組(-1 = 不在表裡;大小寫不敏感)。"""
    n = str(name or "")
    gi = _INDEX.get(n)
    return _INDEX_CI.get(n.casefold(), -1) if gi is None else gi


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
