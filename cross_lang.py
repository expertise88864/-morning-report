# -*- coding: utf-8 -*-
"""**跨語言的同一件事,靠數字認**(深度優化:橫向 P2-7)。

## 問題

分群的標題重疊實測:同語言同事件 0.69/0.90,**跨語言同事件 0.33** ——
低於 0.5 門檻,所以 CNBC 的英文報導與經濟日報的中文報導**必然**各成
一群。後果是三層的:同一件事兩個分析單位(橫向重複計權)、獨立來源數
被砍半(CNBC + 經濟日報本來是兩個獨立群組,卻各自算 1 = 「僅單一來源」)、
全文預算花兩份。第二十五輪 P2-7 指名這個缺口。

## 為什麼是數字,不是語意相似度

「台積電法說會」與「台積電董事會」語意相似度很高,而它們是兩件事 ——
這個 repo 已經為此拒絕過相似度模型一次。但**金額不會騙人**:
「SK海力士砸383億美元」與 "SK Hynix to spend $38 billion" 講的是同一筆
錢,383億美元 = $38.3B,而兩家獨立媒體在同一天報出同量級金額、
主體又是同一家公司的機率,壓倒性地指向同一件事。

## 三道防線(誤併比漏併危險,每一道都往保守偏)

1. **只橋接跨語言的配對**。同語言的低重疊配對已經被 0.5 門檻判過
   「不是同一件事」——同一家公司同一天的兩則中文新聞常共用收盤價,
   拿數字橋接會把「南亞科財報」與「南亞科盤中爆量」併成一件事。
2. **只認 1,000 萬以上的金額**。收盤價(457 元)、目標價、漲跌點數
   都在門檻之下 —— 它們是**行情數字**,同一天出現在同一家公司的
   每一則新聞裡,不指認任何事件。資本支出、併購、發債、罰款在門檻上。
3. **幣別要相同,數值容差 2.5%**。容差是因為媒體會四捨五入
   ($38 billion vs 383億美元 = $38.3B,差 0.8%);幣別不同就不是
   同一筆錢(383億**台幣**與 $38.3B 差 30 倍)。

呼叫端(`news_clusters._same_event`)已先要求**實體別名組有交集** ——
數字錨點是第四道判準,不是唯一判準。
"""
from __future__ import annotations

import re

#: 金額下限(見 docstring 第 2 道防線)。
MIN_ANCHOR_VALUE = 1e7

#: 數值容差(第 3 道防線):媒體四捨五入的量級,不是「差不多就好」。
REL_TOLERANCE = 0.025

_CJK = re.compile(r"[一-鿿]")

#: 中文金額:可帶「人民幣」前綴;裸「元」在台灣財經媒體 = 新台幣。
_ZH_MONEY = re.compile(
    r"(人民幣)?([0-9][0-9,]*(?:\.[0-9]+)?)\s*(兆|億|萬)?"
    r"(美元|美金|新台幣|台幣|日圓|日元|歐元|元)")
_ZH_UNIT = {"兆": 1e12, "億": 1e8, "萬": 1e4, None: 1.0, "": 1.0}
_ZH_CURR = {"美元": "USD", "美金": "USD", "新台幣": "TWD", "台幣": "TWD",
            "日圓": "JPY", "日元": "JPY", "歐元": "EUR", "元": "TWD"}

#: 英文金額:`$38 billion`、`US$1.2bn`、`$165B`。只認美元符號 ——
#: `euro`/`yen` 拼寫在英文財經標題裡極少見,漏掉只是不橋接(保守側)。
_EN_MONEY = re.compile(
    r"(?:US|NT)?\$\s?([0-9][0-9,]*(?:\.[0-9]+)?)\s*"
    r"(trillion|billion|million|tn|bn|mn|[TBM])?(?![a-zA-Z])")
_EN_UNIT = {"trillion": 1e12, "billion": 1e9, "million": 1e6,
            "tn": 1e12, "bn": 1e9, "mn": 1e6,
            "t": 1e12, "b": 1e9, "m": 1e6, None: 1.0, "": 1.0}


def money_anchors(text) -> set:
    """文字裡的 `(幣別, 金額)` 集合,只收 `MIN_ANCHOR_VALUE` 以上。"""
    t = str(text or "")
    out = set()
    for cny, num, unit, curr in _ZH_MONEY.findall(t):
        val = float(num.replace(",", "")) * _ZH_UNIT.get(unit or "", 1.0)
        code = "CNY" if cny else _ZH_CURR.get(curr, "")
        if code and val >= MIN_ANCHOR_VALUE:
            out.add((code, val))
    for num, unit in _EN_MONEY.findall(t):
        val = float(num.replace(",", "")) * _EN_UNIT.get((unit or "").lower(), 1.0)
        # `NT$` 由 regex 吃進前綴但不分組 —— 英文語境的 NT$ 極少,
        # 而把 NT$ 當 USD 會讓幣別防線失效;乾脆整個當 USD 之前先查。
        code = "TWD" if re.search(r"NT\$\s?" + re.escape(num), t) else "USD"
        if val >= MIN_ANCHOR_VALUE:
            out.add((code, val))
    return out


def shared_money(a_text, b_text) -> bool:
    """兩段文字有沒有**同幣別、同量級**的金額(容差見 `REL_TOLERANCE`)。"""
    aa, bb = money_anchors(a_text), money_anchors(b_text)
    for ca, va in aa:
        for cb, vb in bb:
            if ca == cb and abs(va - vb) <= REL_TOLERANCE * max(va, vb):
                return True
    return False


def _cjk_dominant(title) -> bool:
    t = str(title or "")
    return len(_CJK.findall(t)) > len(re.findall(r"[A-Za-z]", t))


#: **雙語事件類別**。同一筆金額可以是資本支出、營收、融資或罰款 ——
#: 那是四件事。外審補審 F6:先前只比金額,於是「Micron 投資 $10B」與
#: 「美光營收 100 億美元」被併成一群,`independent_sources` 變 2、
#: 佐證等級升到 multi_source —— **虛增的可信度會寫進信裡**。
#:
#: 金額只用來**產生候選配對**;合併還要求類別一致。認不出類別的一律
#: 不橋接(保守側:漏併只是退回今天的行為,誤併會造出假的佐證)。
EVENT_CATEGORIES = (
    ("capex", ("投資", "擴產", "建廠", "設廠", "新廠", "資本支出", "擴建",
               "加碼", "invest", "capex", "spend", "build", "expansion",
               "plant", "fab", "factory")),
    ("revenue", ("營收", "營業額", "銷售額", "財報", "獲利", "毛利",
                 "revenue", "sales", "earnings", "profit", "income")),
    ("financing", ("發債", "公司債", "融資", "增資", "募資", "上市",
                   "bond", "debt", "financing", "raise", "ipo", "offering")),
    ("mna", ("併購", "收購", "合併", "入股", "出售",
             "acquire", "acquisition", "merger", "buyout", "stake")),
    ("penalty", ("罰款", "裁罰", "和解金", "賠償",
                 "fine", "penalty", "settlement", "damages")),
    ("subsidy", ("補助", "補貼", "獎勵", "撥款",
                 "subsidy", "grant", "award", "funding")),
)


def _word_hit(word: str, blob: str) -> bool:
    """ASCII 詞要 token 邊界(第二輪外審 F4):`raise` 不得命中 `praise`。
    中文無詞界,維持子字串 —— 但歧義詞已由 `_AMBIGUOUS` 排除。"""
    w = word.lower()
    if not w.isascii():
        return w in blob
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", blob))


#: **歧義詞:含類別詞當子字串,語意卻是別的東西。** 「投資人」含「投資」
#: 但講的是股東;先把它們從文字挖掉再判類別(第二輪外審 F4:
#: 「美光營收100億美元,投資人關注」被判成 capex,與英文的 invest 併群)。
_AMBIGUOUS = ("投資人", "投資者", "投資機構", "投資銀行", "外資", "法人",
              "投顧", "分析師")


def event_category(title) -> str:
    """標題屬於哪一類事件(認不出來、或**同時命中多類**時回空字串)。

    多重命中回空是刻意的(第二輪外審 F4):「營收 100 億,將投資擴產」
    同時是 revenue 與 capex —— 那時「第一個命中就贏」會讓兩邊各自
    取到不同的類別,或更糟,取到同一個錯的類別而併群。
    **分不出來就不橋接**:漏併只是退回今天的行為,誤併會造出假的佐證。
    """
    blob = str(title or "").lower()
    for amb in _AMBIGUOUS:
        blob = blob.replace(amb.lower(), " ")
    hits = {code for code, words in EVENT_CATEGORIES
            if any(_word_hit(w, blob) for w in words)}
    return hits.pop() if len(hits) == 1 else ""


def _entities(item) -> list:
    ents = (item or {}).get("entities")
    return [str(x) for x in (ents or []) if str(x).strip()]


def _shared_specific_anchor(a: dict, b: dict, *, obj: str = "") -> bool:
    """兩則報導有沒有指到**同一個具體的東西**。

    法域(國家)不算 —— 那是動作的對象,同一天的兩樁事本來就共用它。

    **對象自己也不算**(第二十八輪外審 P1-3)。上一版拿「兩邊都點名的
    非法域實體」當第二道錨,而 `cyberattack` 的對象**就是那家公司** ——
    於是「台積電遭勒索軟體攻擊」與 "TSMC reports separate supplier-portal
    data breach" 會被判成同一件事:錨只是把同一個受害者再驗一次,
    它不是獨立的判準。同一承包商的兩批軍售、同一目標的兩項管制同理。

    留下來的錨要**只屬於這一樁**:
      * 同幣別同量級的金額(`shared_money`);
      * 兩邊都出現的**數量級數字**(受影響筆數、通知編號、批次規模);
      * 兩邊都點名、而且**不是這個動作的對象**的實體
        (例如軍售案裡的承包商)。
    """
    import entity_alias as _ea
    import event_actions as _eac
    ta = str((a or {}).get("title") or "")
    tb = str((b or {}).get("title") or "")
    if shared_money(ta, tb):
        return True
    if _shared_numeric(ta, tb):
        return True
    juris = set(_eac.CANONICAL_SUBJECTS.values())
    # **對象本身要排除**:它是這兩則共用的前提,不是分辨它們的東西。
    obj_groups = {_ea.group_of(x) for x in
                  (y.strip() for y in str(obj or "").split("、"))
                  if x.strip()} - {-1}

    def _groups(item):
        # **一個判準,不是兩個**:第一版同時比名稱與別名組,而沒有別名組
        # 的名稱本來就被 `g >= 0` 濾掉 —— 兩個條件互相冗餘,
        # 單獨突變任何一個都被另一個蓋住(突變驗證當場證明它量不到)。
        out = set()
        for n in _entities(item):
            if _eac.canonical_subject(n) in juris:
                continue
            g = _ea.group_of(n)
            if g >= 0 and g not in obj_groups:
                out.add(g)
        return out

    return bool(_groups(a) & _groups(b))


#: 當成錨的數字最少要幾位 —— 一兩位數(名次、季別)到處都是。
MIN_NUMERIC_ANCHOR = 3


def _shared_numeric(a_text: str, b_text: str) -> bool:
    """兩段文字有沒有共同的**量級數字**(受影響筆數、批次編號…)。

    只收三位數以上,而且比對的是去掉千分位之後的字串 ——
    「1,200 萬筆」與 "12 million records" 不會因為寫法不同而錯過,
    而「第 3 季」這種到處都是的小數字不會製造假的錨。
    """
    import re as _re

    def _nums(t):
        out = set()
        for raw in _re.findall(r"[0-9][0-9,]{2,}", t):
            v = raw.replace(",", "")
            # **年份不是事件專屬的數字**(外審第二輪 F2):同一年的兩起
            # 事件都會寫到「2026」—— 拿它當錨等於沒有錨,
            # 而那正好把 P1-3 修掉的誤併路徑再打開一次。
            if len(v) == 4 and 1900 <= int(v) <= 2100:
                continue
            out.add(v)
        return out

    return bool(_nums(a_text) & _nums(b_text))


def action_anchor(a: dict, b: dict) -> bool:
    """非貨幣事件的跨語言錨:**同一個動作、同一個對象**。

    金額橋只接得起「有一筆大錢」的事件 —— 而軍售、制裁、出口管制、
    峰會、選舉、資安事件多半沒有金額,於是同一件事的中英報導永遠是
    兩群:`independent_sources` 少算,交叉驗證看起來比實際弱。

    錨用的是既有的雙語判準,不是新的相似度:
      * `event_actions.event_action()` 的關鍵詞表本來就中英並列;
      * 對象簽章用 `event_identity.object_signature`,而主體正規化
        (`canonical_subjects`)把 "United States" 與「美國」收成同一個。

    **只認算得出對象的事件**:沒有對象的動作(例如台海情勢)只說得出
    「這是同一類事」,說不出「這是同一件事」—— 而誤併會造出假的獨立
    來源數,那比漏併貴。判準交給 `object_signature` 一句話:
    它對 `NEEDS_OBJECT` 以外的動作回空字串,所以下面那個 `bool(sa)`
    就是這條規則本身。**不另外再寫一次 `act in NEEDS_OBJECT`** ——
    那個分支不可能單獨失敗,而不可能失敗的守衛只會讓人以為驗過了。
    """
    import event_actions as _ea
    import event_identity as _ei
    act = _ea.event_action(str((a or {}).get("title") or ""),
                           (a or {}).get("summary"))
    if not act:
        return False
    if act != _ea.event_action(str((b or {}).get("title") or ""),
                               (b or {}).get("summary")):
        return False
    def _obj(item):
        # **公司別名也要收斂**:`canonical_subjects` 只正規化法域
        # (那是 timeline 鍵的判準,動它要進版),而跨語言比對的對象常常
        # 是公司 —— 「台積電」與 "TSMC" 不收在一起的話,`sanction` 這類
        # 對象是 `any` 的動作永遠對不上,這條錨等於只對法域事件有用。
        # 這一層只在這裡做,不影響 timeline 的鍵。
        import entity_alias as _ea2
        subs = sorted(dict.fromkeys(
            _ea2.canonical(x) for x in _ei.canonical_subjects(_entities(item))))
        # **與 timeline 同一份對象判準**(第二十九輪外審 P2-1):先前這裡
        # 認不出受詞時退回主體集合,而 timeline 放 `UNKNOWN_OBJECT` ——
        # 同一則事件在分群與 timeline 拿到不同的對象身分。
        return _ei.action_object(act, (item or {}).get("title"), subs,
                                 summary=(item or {}).get("summary"))

    sa, sb = _obj(a), _obj(b)
    if not sa or sa != sb:
        return False
    if sa == _ei.UNKNOWN_OBJECT:
        # **兩邊都不知道對象不等於同一個對象**:UNKNOWN 對 UNKNOWN 只說
        # 得出「都認不出受詞」—— 拿它當「同對象」會把美國與法國各自的
        # 軍售案橋在一起(與 P1-3 同一個形狀,在跨語言這一側)。
        return False
    # **同動作同對象仍可能是同一天的兩樁不同的事**(外審 P1-4B):
    # 同一國同日兩批不同軍售、同一目標兩輪不同制裁。誤併的代價很重 ——
    # 獨立來源數被灌高、全文預算只留一個事件,而驗證器禁止同一群分析兩次,
    # 於是其中一件真事件會整條消失。
    #
    # 所以再要一個**具體的共同錨**。
    # (辨識詞在這裡**用不上**:它是語言相依的 —— 中文是二元切詞、英文是
    #  單字,同一件事的中英報導必然零重疊,拿 `incident_match` 來擋會把
    #  每一組跨語言配對都判成不同事件。第一版寫了,實測當場全滅。)
    return _shared_specific_anchor(a, b, obj=sa)


def bridge(a: dict, b: dict) -> bool:
    """跨語言配對的第二次機會。**呼叫端已驗過實體別名組交集** ——
    這裡只補「標題重疊量不到跨語言」那一段。

    三道防線 + 第四道:同語言不橋(第 1)、金額 ≥1e7(第 2)、
    幣別相同 + 容差(第 3)、**事件類別一致**(第 4,外審補審 F6)——
    金額只產生候選,類別才決定是不是同一件事。
    """
    ta = str((a or {}).get("title") or "")
    tb = str((b or {}).get("title") or "")
    if _cjk_dominant(ta) == _cjk_dominant(tb):
        return False
    # **金額不是唯一的錨**(2026-08-09 P2):軍售、制裁、出口管制、峰會
    # 多半沒有金額,而它們正是最需要交叉驗證的那一類事件。
    if action_anchor(a, b):
        return True
    if not shared_money(ta, tb):
        return False
    ca, cb = event_category(ta), event_category(tb)
    return bool(ca) and ca == cb
