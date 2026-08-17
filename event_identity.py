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
#: v8(2026-08-09,第二十七輪外審 P1-3/P1-4A):辨識詞比對改**三態**
#: (「不知道」不再壓成「同一樁」)、後綴雜湊改吃完整的辨識詞集合、
#: 法域類動作的對象改先看標題裡的方向詞(「對台」/"to Taiwan")——
#: **鍵的算法變了就要進版**,否則 `adopt_legacy` 會跳過既有記錄。
#: v9(第二十八輪外審 P1-4):帶方向的動作認不出受詞時,鍵放
#: `UNKNOWN_OBJECT` 而不是整個主體集合(那等於拿 actor+recipient 冒充
#: recipient);受詞也會從 summary 找。**鍵的算法變了就要進版。**
#: v12(第三十輪外審 P1-1/P1-4):態勢表收窄成「持續態勢」(選舉與峰會
#: 回到逐樁)、家具剝除改正面辨識(發布者/版名宣告表)、樣板前綴
#: (「重大訊息」「公告」)進通用詞 —— **辨識詞的公式變了就要進版**。
#: v11(縱深第五批):標題家具(來源名與版名)不進辨識詞。
#: v10(第二十九輪 Commit 2):`CANONICAL_SUBJECTS` 補齊法域的中英對應
#: (France→法國 等 28 組)—— 主體正規化是鍵的一部分,表變了鍵就變。
IDENTITY_SCHEMA_VERSION = 12

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
    # v12(外審 P1-4 的下半):**樣板前綴**。「台積電重大訊息公告 -
    # 高雄廠停工」與「… - 董事長辭任」剝完家具之後仍有 0.56 重疊 ——
    # 六個共同詞全部來自這個前綴。它每一則公告都有,不指認任何事件,
    # 與「宣布」「表示」是同一類;不挖掉的話,家具剝乾淨了,
    # 兩件事還是會被判成同一樁(外審指的 observable failure)。
    "重大訊息", "公告", "澄清說明", "新聞稿",
    "announce", "announces", "announced", "says", "said", "report",
    "reports", "reported", "unveil", "unveils", "confirms", "confirmed",
)

#: 一則標題至少要留下幾個辨識詞才拿來比對。低於這個數代表整個標題
#: 幾乎只有主體與通用詞 —— **那時任何重疊都不構成證據**。
MIN_DISCRIMINATIVE = 2


#: **標題尾綴的發布者**(只為「這一段是不是家具」服務)。
#:
#: 為什麼不塞進 `source_registry.OWNER_GROUPS`:那張表回答的是**另一個
#: 問題** ——「這是不是一個獨立的編輯決策」,而它刻意保守(不在表裡的不
#: 計入獨立數)。LINE TODAY、Yahoo股市是**聚合器**:它們是貨真價實的
#: 標題家具,卻絕不該讓「多家獨立證實」的數字加一。兩張表、兩個問題。
#:
#: 名單由 2026-08-10 的真實 state 量出來(6,197 個標題裡帶 " - " 的
#: 3,876 個,依尾綴出現次數由高至低)。**認不得就不剝** —— 漏剝只是讓
#: 家具留在辨識詞裡(批#5 之前的老樣子),誤剝會把「重大訊息 - 高雄廠
#: 停工」與「重大訊息 - 董事長辭任」壓成同一件事(外審 P1-4)。
#: 兩種錯誤的代價不對稱。
_TITLE_TAIL_PUBLISHERS = frozenset({
    "cmoney投資網誌", "cmoney", "yahoo股市", "yahoo新聞", "yahoo 財經",
    "yahoo運動", "yahoo奇摩股市", "line today", "蕃新聞", "富聯網",
    "tradingkey", "ftnn 新聞網", "ftnn新聞網", "台視全球資訊網",
    "股市報價& 財經新聞", "大纪元", "大紀元", "鏡週刊mirror media",
    "鏡週刊", "tradingview", "富途牛牛", "pchome online 新聞", "今周刊",
    "rfi", "世界新聞網", "非凡新聞台", "風傳媒", "上報", "太報",
    "nownews今日新聞", "nownews", "newtalk", "民視新聞網", "民視財經網",
    "ctwant", "techorange 科技報橘", "動區動趨", "香港經濟日報hket",
    "報新聞", "華僑時報", "新頭殼newtalk", "數位時代", "遠見雜誌",
    "經濟日報", "工商時報", "財訊", "信傳媒", "商周", "yahoo",
    "hinet生活誌", "yahoo奇摩新聞", "自由財經", "自由時報",
    "旺得富理財網", "moomoo", "鏈新聞 abmedia", "民報", "商傳媒",
    "business insider taiwan", "理財周刊", "新唐人電視台", "環球生技月刊",
    "biggo 財經", "壹蘋新聞網", "台灣好新聞", "t客邦", "ithome",
    "mashdigi", "好房網news", "卡優新聞網", "信報網站", "太報 taisounds",
    "中華新聞雲／中華日報", "科技新報", "數位時代", "天下雜誌",
})

#: **版名/導覽段**(管線分隔的那幾格)。同樣是正面宣告:
#: 「短」不是家具的證據(外審 P1-4:「法說」「停工」也只有兩個字,
#: 而那是內容)。名單同樣由真實 state 量出來。
_SECTION_LABELS = frozenset({
    "全球", "全球財經", "股市話題", "產業熱點", "市場焦點", "股市要聞",
    "個股", "股市", "產經", "國際", "生活", "科技", "產業", "新聞",
    "財經", "요즘", "時事", "焦點", "頭條", "重點新聞", "熱門新聞",
    "即時新聞", "國際財經", "台股", "美股", "陸股", "政治", "社會",
})


#: 招牌尾字:認得的發布者名字扣掉之後,這些字不算事件內容
#: (「三立新聞網SETN.com」→ 扣掉 `三立新聞`/`setn` 還剩「網.com」)。
#: **只收招牌用語** —— 「停工」「法說」「辭任」不在裡面,所以它擴大的是
#: 辨識率,不是誤剝的面積(外審 P1-4 的判準:正面宣告,不是長度)。
_OUTLET_SUFFIX_WORDS = (
    "新聞網", "新聞雲", "財經雲", "電視台", "資訊網", "新聞台", "網站",
    "新聞", "財經", "理財", "網", "雲", "報", "台", "com", "tw", "net",
)


def _publisher_aliases() -> tuple:
    """所有認得的發布者別名(註冊表 + 本檔宣告),小寫、由長至短。

    由長至短是必要的:先扣掉「聯合新聞網」才不會被「聯合」切成碎片,
    而碎片會讓殘餘看起來像內容。
    """
    global _ALIAS_CACHE
    if _ALIAS_CACHE is None:
        import source_registry as _sr
        names = set(_TITLE_TAIL_PUBLISHERS)
        for grp in _sr.OWNER_GROUPS:
            names.update(str(x).casefold() for x in grp[1:])
        names.update(str(x).casefold() for x in _sr.WIRES)
        names.update(str(x).casefold() for x in _sr.AGGREGATORS)
        _ALIAS_CACHE = tuple(sorted(
            (n for n in names if len(n) >= 2), key=len, reverse=True))
    return _ALIAS_CACHE


_ALIAS_CACHE = None


def _is_publisher_tail(tail: str) -> bool:
    """這一段是不是**發布者標籤**(不是事件內容)。

    三種正面證據:註冊表認得的發布者、網域形狀(`ftnn.com.tw`)、
    本檔宣告的尾綴發布者。**長度不算證據**(外審 P1-4)。
    """
    t = str(tail or "").strip()
    if not t or re.search(r"[:：。!！?？「」]", t):
        return False          # 帶引述/句讀的是內容(「路透:停火近了」)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}", t):
        return True           # money.udn.com / ftnn.com.tw
    if t.casefold() in _TITLE_TAIL_PUBLISHERS:
        return True
    # **把認得的發布者名字扣掉,看剩下什麼**(外審 r2 的一般化)。
    # 註冊表是子字串比對,所以「含發布者」不等於「是發布者標籤」:
    #   「路透獨家披露停火條件全文內容整理」扣掉「路透」還剩 14 字 → 內容;
    #   「TechNews 科技新報」兩個名字都扣掉之後什麼都不剩 → 標籤。
    # 用長度當閘門的話,兩個字的真內容(「法說」)會過、十四個字的真
    # 標籤(中英並列的媒體名)會被擋 —— 兩頭都錯。
    left, hit = t.casefold(), False
    for alias in _publisher_aliases():
        if alias and alias in left:
            left = left.replace(alias, " ")
            hit = True
    if hit:
        # 認得的名字扣掉之後,常見的**招牌尾字**也不算內容
        # (「三立新聞網SETN.com」→「網.com」、「ETtoday財經雲」→「財經雲」)。
        # 這張表刻意只收招牌用語 —— 「停工」「法說」不在裡面,
        # 所以它擴大的是辨識率,不是誤剝的面積。
        for w in _OUTLET_SUFFIX_WORDS:
            left = left.replace(w, " ")
    # **一定要有名字被扣掉,而且要扣得一乾二淨**:
    #   * 沒扣到任何東西 → 殘餘等於原文,兩個字的真內容(「法說」)會過關;
    #   * 留一點餘裕(舊版 ≤2)→「路透停火」「彭博破局」扣掉媒體名之後
    #     剩下的**正是分辨兩樁的內容**,而它剛好兩個字(外審 r1)。
    # 招牌用語已經在上面扣過了,所以「乾淨」是做得到的要求。
    return hit and not re.sub(r"[\s.,·・\-–—_/|()（）]", "", left)


def strip_title_furniture(title) -> str:
    """剝掉標題**尾端**的發布者與版名;認不出來就原樣退回。

    這些不是事件內容(v11)—— 2026-08-10 實際 state 的 incident_tokens
    裡有 `com`/`money`/`udn`/「聯合」「新聞」:同一條線索兩天在不同媒體
    報導,家具不同會稀釋重疊、家具相同會灌高重疊 —— 兩個方向都在扭曲
    「是不是同一樁」的判斷。

    **判準是正面辨識,不是長度**(外審 P1-4):「台積電重大訊息 -
    高雄廠停工」與「… - 董事長辭任」的尾段都短、都沒句讀,靠長度剝的話
    兩件事會被壓成同一個標題、同一組辨識詞 —— 那正是這個模組要防的
    false merge。認不得的尾綴留著(家具進辨識詞是舊行為,保守側)。
    """
    text = str(title or "").strip()
    # 「內容 - 發布者」:Google News 的尾綴慣例。**逐段往前剝** ——
    # 真實資料是「內容 | 版名 - 站名 - 發布者」這種多層尾巴。
    while True:
        head, sep, tail = text.rpartition(" - ")
        if not sep or len(head.strip()) < 6 or not _is_publisher_tail(tail):
            break
        text = head.strip()
    # 「內容 | 版名 | 站名」:尾段是發布者才動;錨定之後只再剝**宣告過的
    # 版名**。全形「｜」在真實標題裡有語意用途(「美國宣布新制裁｜鎖定
    # 無人機供應鏈」)—— 剝到內容比留著家具貴得多。
    seps = [m.start() for m in re.finditer(r"[|｜]", text)]
    if seps:
        _tail = text[seps[-1] + 1:].strip()
        if _is_publisher_tail(_tail) or _tail.casefold() in _SECTION_LABELS:
            cut = seps[-1]
            # 用**切片**而不是 split/join —— 重組會把全形｜換成半形,
            # 留下的內容一個字元都不該變。
            for pos in reversed(seps[:-1]):
                seg = text[pos + 1:cut].strip()
                if seg.casefold() in _SECTION_LABELS or _is_publisher_tail(seg):
                    cut = pos
                else:
                    break
            text = text[:cut].strip()
    return text if len(text) >= 6 else str(title or "").strip()


def is_situation_action(action) -> bool:
    """這個動作是不是「態勢」(同鍵之下不做逐樁切分)。宣告見
    `event_actions.SITUATION_ACTIONS`(與 ACTION_TABLE 同檔,同一份權威)。"""
    from event_actions import SITUATION_ACTIONS
    return str(action or "") in SITUATION_ACTIONS


def discriminative_tokens(title, subjects=()) -> set:
    """標題裡**真正指認事件**的 token(去掉主體名與通用新聞動詞)。

    第二輪外審 F2/F3:主體相交已經在上一層判過了,標題重疊若又被
    主體名灌滿,等於**把同一份證據算兩次** —— 而「台積電宣布法說會」
    與「台積電宣布擴建新廠」的共同詞正好全部是這一類(重疊 0.50)。

    **從文字裡挖掉再切詞**,不是切完再過濾:中文用二元組,
    「台積電宣布」會產生「電宣」這種跨越主體與動詞的詞,事後濾不掉。
    """
    from news_clusters import _tokens
    # 家具先剝(v11):來源與版名不是事件內容 —— 見 `strip_title_furniture`。
    text = strip_title_furniture(title)
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


def match_lineage(records, entities, titles, summary: str = "") -> str:
    """事件群接得上的 timeline **世系 ID**(接不上或分不出回空字串)。

    第三十一輪外審的方向:timeline 的 `key` 已經是「同一樁事的逐日世系」
    (身分分裂/承接/退場全在 producer 處理)—— recap 與 origin 各自再推
    一次身分,正是「同一件事兩個答案」反覆出現的根源。這裡把世系交出去,
    下游**同世系直接接、不同世系直接否**,推身分只留給沒有世系的事件。

    多筆命中而世系不同 = 身分仍分不開 → 回空(保守側:退回既有的
    模糊判準,不擲骰子)。
    """
    hits = {k for _, k in _lineage_hits(records, entities, titles, summary)
            if k}
    return hits.pop() if len(hits) == 1 else ""


def match_days(records, entities, titles, summary: str = "") -> int:
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
    return min((h[0] for h in _lineage_hits(records, entities, titles,
                                            summary)), default=0)


def _objects_agree(mine: str, theirs: str) -> bool:
    """兩側的對象算同一個嗎 —— **簽章退回時當候選集合**。

    producer 端(timeline)存的是剝掉 actor 的明確對象(「伊朗」),而
    消費端的實體集合常帶著 actor,`action_object` 退回整組簽章
    (「伊朗、美國」)—— 等值比對永遠對不上,`continuing_days` 回 0、
    世系接不上(Batch A 端到端測試當場抓到)。

    退回的簽章不是「對象是這一串」,是「對象在這一串裡、分不出是哪個」
    —— 所以:單一側的明確對象出現在多元側的候選裡就算對上;
    **兩側都是多元時仍要求相等**(兩個都分不出,猜了就是擲骰子)。
    """
    if mine == theirs:
        return True
    ma, tb = mine.split("、"), theirs.split("、")
    if len(ma) == 1 and len(tb) > 1:
        return mine in tb
    if len(tb) == 1 and len(ma) > 1:
        return theirs in ma
    return False


def _lineage_hits(records, entities, titles, summary: str = "") -> list:
    """`[(days, key)]` —— match_days 與 match_lineage 共用同一次比對。"""
    ents = {canonical_subject(str(e)) for e in (entities or ())
            if str(e).strip()}
    # **不因為實體集合是空的就早退**:實體抽取會漏,標題不會 ——
    # `_subjects_meet` 的第三層(標題含記錄的主體名)是正當的比對路徑。
    keys = expand_alias(ents)
    # **身分走同一個入口**(第三十一輪外審 P1-1B):先前這裡自己算
    # `event_action(titles)` + `object_signature(action, ents)` ——
    # 不吃 summary、對象退回主體集合。受詞只在 summary 的日子
    # (「美國軍售最新動向」+ summary 寫 for Taiwan),timeline 已經
    # 知道對象是台灣、這裡算不出來;actor 有沒有被抽出來也會改變
    # 主體集合 → 同一件事兩天的 `continuing_days` 對不上。
    _vi = view_identity(titles, entities, summary)
    today_action = _vi["action"]
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
            # 兩側都走 `action_object`(受詞優先、summary 也找);
            # 記錄側優先用**存下來的**對象 —— 它是當天算好的身分。
            mine = _vi["object"]
            theirs = (str(r.get("object") or "")
                      or action_object(rec_action, str(r.get("latest_title")
                                                       or ""), subs,
                                       summary=str(r.get("latest_summary")
                                                   or "")))
            if not mine or not theirs or not _objects_agree(mine, theirs):
                continue          # 對象對不上、或算不出來 → 保守不接
        # **同動作+同對象還要是同一樁**(第三十二輪外審 P1-1)。
        # `cyberattack`/`sanction`/`arms_sale` 同月同對象可以發生多起 ——
        # 不看 incident 辨識詞的話,今天全新的一樁會繼承昨天那樁的天數,
        # 拿到延燒排序與全文優先權,縱向敘事起點跟著錯。
        # 三態各有去處:同文字系統 NO_MATCH → 硬否決;跨文字系統的
        # 零共用是語言差異 → 退而要求**逐樁錨**(金額/帶單位數量/
        # 非對象第三實體,與跨語言橋接同一套);UNKNOWN(舊代記錄沒有
        # 辨識詞、短標題)→ 保守不否決(升版當天 state 幾乎全是舊代);
        # persistent situation(荷姆茲通行)本來就不逐樁。
        if not is_situation_action(rec_action):
            _mt = incident_match(_vi["incident_tokens"],
                                 r.get("incident_tokens") or [])
            # **UNKNOWN 有兩種,不是一種**(外審 2026-08-17)。
            # 先前只有 `NO_MATCH` 會被否決,`UNKNOWN` 一律放行 ——
            # 於是「舊代記錄沒有辨識詞」(遷移相容,該放行)與「記錄是
            # 現行代、**今天**的證據不足」(不知道是不是同一樁,該保守)
            # 得到同一個答案。實測反例:記錄 3 個辨識詞、今天標題
            # `TSMC cyberattack` 只有 1 個 → 繼承 7 天與舊 lineage,
            # 於是全新的一樁被寫成「第 8 天」,並拿到延燒排序與全文優先權。
            # **這也是 producer 早就採取的政策**:`incident_match` 的
            # docstring 寫著「跨代/跨日一律另開 provisional sibling,
            # 不繼承天數」—— consumer 先前與它相反。
            _rec_specific = len({str(t) for t in (r.get("incident_tokens")
                                                 or [])}) >= MIN_DISCRIMINATIVE
            if _mt == NO_MATCH or (_mt == UNKNOWN and _rec_specific):
                # 同文字系統的零重疊是硬證據(不同樁);其餘(跨語言的
                # 零重疊、今天證據不足)退而要求**逐樁錨**。
                if _mt == NO_MATCH and comparable_scripts(titles,
                                                          r.get("latest_title")):
                    continue
                import cross_lang as _cl
                if not _cl._shared_specific_anchor(
                        {"title": str(titles or ""),
                         "summary": str(summary or ""),
                         "entities": sorted(str(e) for e in (entities or ())
                                            if str(e).strip())},
                        {"title": str(r.get("latest_title") or ""),
                         "summary": str(r.get("latest_summary") or ""),
                         "entities": [str(x) for x in (r.get("subjects")
                                                       or [])]},
                        obj=str(r.get("object") or "")):
                    continue
        hits.append((int(r.get("days") or 0), str(r.get("key") or "")))
    return hits


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


#: 後綴雜湊最多吃幾個辨識詞。取大是刻意的:這個上限是**長度保護**,
#: 不是「挑最重要的四個」—— 挑選會讓不同的事共用後綴。
MAX_SUFFIX_TOKENS = 24


def incident_suffix(tokens) -> str:
    """同鍵下另一樁事件的穩定後綴(由辨識詞決定,不用序號)。

    序號會隨處理順序漂移 —— 昨天的 `#2` 明天可能指到另一樁。
    """
    # **用完整的辨識詞集合**(第二十七輪外審 P1-3):只雜湊排序後前四個
    # 的話,兩樁不同的事只要前四個排序詞剛好相同就共用同一個後綴 ——
    # 而後綴正是用來把它們分開的東西。上限是為了確定性與長度,
    # 不是為了挑選(`discriminative_tokens` 已經去過重、排過序)。
    import hashlib
    core = "|".join(sorted(str(t) for t in (tokens or []))[:MAX_SUFFIX_TOKENS])
    return hashlib.sha1(core.encode("utf-8")).hexdigest()[:6]


#: `incident_match()` 的三種答案。
MATCH = "match"
NO_MATCH = "no_match"
UNKNOWN = "unknown"


def comparable_scripts(a, b) -> bool:
    """兩段文字的辨識詞**比得出勝負嗎** —— 同一套書寫系統才比得出。

    中文切二元組、英文切單詞:同一件事的中英文報導共用辨識詞是零,
    那是語言差異不是事件差異。混合書寫一律當**比不出來**(保守側)。
    本體自 `analysis_recap._comparable` 搬入 —— lineage 指派(P1-1)
    也要用同一個判準,各留一份會漂移。
    """
    def _score(t):
        text = str(t or "")
        han = sum(1 for ch in text if "一" <= ch <= "鿿")
        lat = sum(1 for ch in text if ch.isascii() and ch.isalpha())
        return han, lat

    ha, la = _score(a)
    hb, lb = _score(b)
    cjk_a, cjk_b = ha >= 2 and ha >= la, hb >= 2 and hb >= lb
    lat_a, lat_b = la >= 4 and la > ha, lb >= 4 and lb > hb
    return (cjk_a and cjk_b) or (lat_a and lat_b)


def incident_match(tokens_a, tokens_b) -> str:
    """兩組辨識詞講的是同一樁事件嗎 —— **三態**,不是布林。

    第二十七輪外審 P1-3:上一版辨識詞不足時直接回「同一樁」,
    而那個「不確定時併」在兩種情況下會把不同的事黏在一起:

      * **舊代記錄沒有 `incident_tokens`**(schema 6 以前)。比對時一側
        是空集合 → 一律視為同一樁,新事件因此繼承前一樁的天數。
        而升版當天 state 裡幾乎全是舊代記錄。
      * **短標題**:扣掉主體與通用動詞、中文二元切詞之後,可能剩不到
        兩個辨識詞。同公司同月的「再遭駭」與「資料外洩」會被黏成一條。

    所以「不知道」要自己是一個答案,由呼叫端決定往哪邊倒 ——
    判準本身不替它選(見 `morning_report.update_event_timeline`:
    同代且同日的追蹤沿用 lineage,跨代/跨日一律另開 provisional sibling,
    **不繼承天數**)。
    """
    a = {str(t) for t in (tokens_a or [])}
    b = {str(t) for t in (tokens_b or [])}
    if len(a) < MIN_DISCRIMINATIVE or len(b) < MIN_DISCRIMINATIVE:
        return UNKNOWN
    return (MATCH if len(a & b) / min(len(a), len(b)) >= INCIDENT_OVERLAP
            else NO_MATCH)


def same_incident(tokens_a, tokens_b) -> bool:
    """`incident_match` 的布林相容出口(**不知道**算同一樁)。

    留著是因為它已經是別處的判準;新的呼叫端要用三態版本 ——
    把「不知道」壓成「是」正是外審 P1-3 指的那件事。
    """
    return incident_match(tokens_a, tokens_b) != NO_MATCH


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


#: **哪些動作的受詞真的跟在方向詞後面**。這是一份宣告 ——
#: 「對台影響」在選舉/峰會/匯率干預的標題裡是後果子句,不是受詞。
DIRECTIONAL_ACTIONS = frozenset({"arms_sale"})

#: **方向詞**:法域類動作的受詞跟在它後面(「對**台**軍售」、
#: "arms sale **to** Taiwan")。這是一份**宣告**,不是語意剖析 ——
#: 找不到就退回原本的行為(整個主體集合),不猜。
#: `" for "` 收在這裡是因為軍售常寫 "intended for Taiwan" / "destined for
#: Taiwan"(外審 P1-4 的反例)。它本身很常見,但**後面要緊接一個認得的
#: 法域**才算命中,而且方向詞只對 `DIRECTIONAL_ACTIONS` 生效 ——
#: 兩層限制之下,誤命中的空間很小。
_DIRECTION_MARKERS = ("對", "向", "售予", "賣給", " to ", " toward ",
                      " towards ", " against ", " for ")


#: 帶方向的動作**認不出受詞**時的鍵。
#:
#: 第二十八輪外審 P1-4:認不出來時退回整個主體集合,等於拿
#: 「actor + recipient」冒充 recipient —— 於是同一批軍售的兩則報導
#: (一則標題寫得出「對台」、一則只在 summary 裡提)拿到兩個不同的
#: base key,而 sibling 比對只在同一個 base key 底下跑,救不回來。
#: **不知道就說不知道**:所有認不出受詞的同動作事件落在同一條
#: provisional lineage 上,而不是各自散成主體集合的排列。
UNKNOWN_OBJECT = "?"


def directional_object(action: str, title, subjects, summary="") -> str:
    """標題裡點得出來的**受詞法域**(點不出來回空字串)。

    第二十七輪外審 P1-4A:上一版把**所有**法域都留進簽章,於是同一批
    軍售因為某一則多抓到 actor(`[台灣]` vs `[美國, 台灣]`)就分裂成
    兩條線 —— 而 sibling 比對只在同一個 base key 底下跑,救不回來。
    donor 與 recipient 都是法域,「只留法域」解不了這件事。

    這裡只做一件很窄的事:找**方向詞後面**的那個法域。
    「美國宣布對台軍售」與 "US approves arms sale to Taiwan" 都指到台灣。
    找不到方向詞、或後面不是認得的法域 → 回空字串,呼叫端退回舊行為
    (**不猜**:猜錯會把兩件事黏在一起,那比分裂貴)。
    """
    # **只對宣告過的動作生效**(外審第二輪 F1)。`jurisdiction` 這一組還
    # 包含 `election`/`summit_talks`/`fx_intervention` —— 而那些標題裡的
    # 「對台影響」是**後果子句**,不是動作的受詞:
    # 「美國大選對台影響」與「日本大選對台影響」會拿到同一個 `台灣`,
    # 於是日本大選繼承美國大選的延燒天數。
    # 軍售的「對 X」在語意上就是受援國,那是這裡唯一站得住的一個。
    if str(action or "") not in DIRECTIONAL_ACTIONS:
        return ""
    # **先在標題找,找不到才連 summary 一起找**(外審第二輪 F3)。
    # 上一版用「標題裡有沒有方向詞」當閘門 —— 而「美國軍售最新動**向**」
    # 的「向」會讓它以為標題找得到,summary 那條路就走不到了。
    # 判準要看**有沒有找到受詞**,不是有沒有出現方向詞。
    text = str(title or "")
    known = {}
    for alias, canon in CANONICAL_SUBJECTS.items():
        known[str(alias).lower()] = canon
    for canon in set(CANONICAL_SUBJECTS.values()):
        known[str(canon).lower()] = canon
    # 中文的「對台」是簡稱 —— 主體集合裡的法域也一起當候選
    for s in (subjects or []):
        c = canonical_subject(s)
        if c:
            known[str(c).lower()] = c
            known[str(c)[:1].lower()] = c        # 「台」→ 台灣
    best = _scan(text.lower(), known, action)
    if best or not summary:
        return best
    return _scan(f"{text} {summary}".lower(), known, action)


def _scan(low: str, known: dict, action: str) -> str:
    """在這段文字裡找方向詞後面的法域。

    **取離動作關鍵詞最近的那個方向詞**(第二十七輪外審):「第一個」會被
    前面的子句搶走 —— "US responds to China with arms sale to Taiwan" 的
    第一個 " to " 指向中國,而受援國是台灣。中文的語序把方向詞放在關鍵詞
    **前面**(「對台軍售」)、英文放在後面,所以判準是**距離**不是先後。
    """
    from event_actions import ACTION_TABLE
    words = next((row[2:] for row in ACTION_TABLE if row[0] == action), ())
    spots = [x for x in (low.find(str(w).lower()) for w in words) if x >= 0]
    best, best_dist = "", None
    for mark in _DIRECTION_MARKERS:
        at = low.find(mark.lower())
        while at >= 0:
            tail = low[at + len(mark):].lstrip()
            hit = next((known[k] for k in sorted(known, key=len, reverse=True)
                        if k and tail.startswith(k)), "")
            if hit:
                dist = min((abs(at - p) for p in spots), default=0)
                if best_dist is None or dist < best_dist:
                    best, best_dist = hit, dist
            at = low.find(mark.lower(), at + 1)
    return best


def action_object(action: str, title, subjects, summary="") -> str:
    """這個動作的**對象**(單一真相來源,第二十九輪外審 P2-1)。

    先前 timeline 與跨語言 bridge 各自拼:timeline 認不出受詞時放
    `UNKNOWN_OBJECT`,bridge 卻退回整個主體集合 —— 同一則事件在分群與
    timeline 拿到**不同的對象身分**,而那會讓同一件事重複佔據 top-event
    與全文預算(false split)。判準只能有一份,兩邊都呼叫這裡。
    """
    act = str(action or "")
    if act in DIRECTIONAL_ACTIONS:
        return (directional_object(act, title, subjects, summary=summary)
                or UNKNOWN_OBJECT)
    return object_signature(act, subjects)


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


def view_identity(title, subjects, summary: str = "") -> dict:
    """一段敘述的**事件身分**:`{action, object, incident_tokens}`。

    第三十輪外審 P1-3:`analysis_recap` 先前自己推了一套 ——
    `event_action(title)` + `object_signature(action, entities)` —— 而
    timeline 走的是 `action_object()`。兩套判準在兩個方向上都會出錯:

      * **假合併**:同公司同月的兩起資安事件,動作與對象都相同,
        而 `best_view` 只要動作對上就接 —— 今天這一起會拿到上一起的
        昨日觀點與首見,模型被要求對**另一件事**寫「應驗/落空」;
      * **假分裂**:同一批對台軍售,昨天的實體是「台灣、美國」、今天
        只寫「台灣」,`object_signature` 退回主體集合 → 兩天的對象不同
        → 昨日觀點消失。而 timeline 早就知道「受援國才是對象,
        actor 有沒有被抽出來不改身分」。

    所以身分只留一份:動作與對象走 `action_object()`(timeline、跨語言
    橋接、recap 同一個答案),再帶上 `incident_tokens` —— 「同一個動作
    對同一個對象」還要問「是不是同一樁」,那正是 timeline 早就在做的事。
    """
    _t = str(title or "")
    canon = canonical_subjects(subjects)
    action = event_action(_t, summary)
    return {"action": action,
            "object": action_object(action, _t, canon,
                                    summary=summary) if action else "",
            "incident_tokens": sorted(discriminative_tokens(_t, canon))}


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
        # **先問標題點不點得出受詞**(外審 P1-4A):主體集合當簽章會讓
        # 同一批軍售因為某一則多抓到 actor 就分裂,而 sibling 比對只在
        # 同一個 base key 底下跑,救不回來。點不出來才退回主體集合。
        # 對象的判準收在 `action_object`(單一真相來源,外審 P2-1)——
        # timeline 與跨語言 bridge 要拿到同一個答案。
        obj = action_object(action, title, canon, summary=ev.get("summary"))
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
