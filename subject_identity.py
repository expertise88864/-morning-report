# -*- coding: utf-8 -*-
"""**主體身分的唯一判準**(repo-wide 外審 2026-08-20 P1-2)。

同一個主體先前有三套 canonical 權威各自為政:
`news_events.SEMANTIC_ENTITY_ALIASES`(英文 canonical:Russia/Pentagon)、
`event_actions.CANONICAL_SUBJECTS`(中文法域:俄羅斯/阿聯)、
`entity_alias.ALIAS_GROUPS`(公司:台積電/聯準會)。生產已因此互相打架:
08/20 同一班裡 migration 把 `UAE|阿聯控伊朗…` 當不認得**刪掉**,而 producer
對同一則新聞判 `阿聯` literal **保留** —— 一邊刪、一邊寫。

這個模組是**比較與鍵的單一出口**:
- `canonical_display(name)`:跨語言寫法收斂後的正規顯示名。法域採
  `event_actions` 的中文(俄羅斯/阿聯 —— 與既有 state 及 event_identity
  一致,Russia 的續報才接得回 `geopolitical:俄羅斯:…` 的舊鍵);
  公司採 `entity_alias`(台積電);機構/貨幣extras 在本檔宣告(五角大廈/
  台電/美元)。認不得的名字**原樣返還**(證明不了 ≠ 錯)。
- `same_subject(a, b)`:兩個寫法是不是同一個主體。
- `aliases_of(name)`:這個主體宣告過的全部別名(供逐字驗證)。

所有比較(story 匹配、timeline 鍵、migration 重驗、producer 正規化)
一律先過這裡。要新增一個主體:法域加 `event_actions.CANONICAL_SUBJECTS`,
機構/貨幣加下面的 `_ORG_ALIASES` —— 不要再開第四張表。

**持久化的鍵會被重寫**(2026-08-22 外審 P1 起,取代原本「不重寫鍵」的
說法):`identity_name` 是鍵的主體權威,而既有 state 由
`state_migrations` 的兩支遷移改名(event timeline / story ledger)。
所以這裡的 canonical 寫法是**契約**,不是顯示偏好 —— 改它要配遷移。
"""
from __future__ import annotations

import entity_alias as _al
import event_actions as _ea

#: 機構/貨幣/公用事業 —— 法域表(event_actions)與公司表(entity_alias)
#: 之外的宣告。canonical 用**中文顯示名**(與法域表同一慣例;既有 state
#: 兩種寫法都有,比較端收斂即可,不追溯改鍵)。
_ORG_ALIASES: dict[str, tuple[str, ...]] = {
    "五角大廈": ("五角大廈", "Pentagon", "美國國防部", "US Department of Defense"),
    "白宮": ("白宮", "White House"),
    "聯準會": ("聯準會", "Fed", "Federal Reserve", "聯儲", "FOMC"),
    "台電": ("台電", "Taipower", "台灣電力", "台灣電力公司"),
    "美元": ("美元", "US Dollar", "USD", "DXY", "美元指數"),
    "國際刑事法院": ("國際刑事法院", "International Criminal Court", "ICC"),
    "歐洲央行": ("歐洲央行", "ECB", "European Central Bank"),
    "OPEC": ("OPEC", "OPEC+", "石油輸出國組織"),
    "北約": ("北約", "NATO"),
    "聯合國": ("聯合國", "UN", "United Nations"),
    "世界衛生組織": ("世界衛生組織", "WHO"),
    "國際貨幣基金": ("國際貨幣基金", "IMF"),
}

_ORG_LOOKUP: dict[str, str] = {a.lower(): canon
                               for canon, aliases in _ORG_ALIASES.items()
                               for a in aliases}


def canonical_display(name) -> str:
    """跨語言寫法收斂後的正規顯示名;認不得原樣返還。

    順序:機構extras → 法域(event_actions,中文)→ 公司(entity_alias)。
    機構在前是因為法域表把 "us" 這類縮寫收得很寬 —— "US Dollar" 要先在
    機構表命中,不能被拆去配美國。
    """
    n = str(name or "").strip()
    if not n:
        return ""
    org = _ORG_LOOKUP.get(n.lower())
    if org:
        return org
    juris = _ea.canonical_subject(n)
    if juris != n:
        return juris
    comp = _al.canonical(n)
    return comp if comp else n


def identity_name(name) -> str:
    """**持久化鍵的主體權威**(機器身分)—— 法域/機構/公司全部收斂。

    2026-08-22 外審 P1:上一版刻意把公司排除在鍵的收斂之外,理由是
    「公司鍵慣例是代號,收斂等於重寫全部公司鍵」。**那個前提被生產
    自己反證**:`state/event_timeline.json` 存在
    `export_controls:輝達:2026-08` —— 公司中文名早就在當持久身分。
    於是同一家公司的三種寫法(輝達/NVIDIA/NVDA)是三條 lifecycle:
    昨天 rumor、今天 confirmed 卻查不到前一代,重新拿 full weight,
    污染的是 event_id、延燒天數、unique event 計數與 event-study 樣本。

    **顯示字串與機器身分今天是同一個字串,但契約不同**:顯示可以改
    措辭,機器身分改一個字就要配一次 state 遷移。組代表(
    `entity_alias.ALIAS_GROUPS` 每組第一個)因此成為**凍結的契約**,
    重排組內順序會靜默改寫所有持久鍵 —— 由
    `tests/test_batch_2026_08_22b.py` 的凍結表釘住。

    刻意**不**自創 `equity:US:NVDA` 這種命名空間 ID(外審的建議形):
    別名表沒有市場/交易所欄位,那個 ID 得先憑空補一份對照資料,而補錯
    就是新的一類誤併。宣告過的組代表已經是穩定 ID,且已有生產驗證過的
    遷移路徑(08/21 renamed 2 條)。
    """
    return canonical_display(name)


def same_subject(a, b) -> bool:
    """兩個寫法是不是同一個主體(空值不算;原樣相等也算)。"""
    aa, bb = str(a or "").strip(), str(b or "").strip()
    if not aa or not bb:
        return False
    return aa == bb or canonical_display(aa) == canonical_display(bb)


#: 期間詞:與 `news_rules.PERIOD_TOKEN` 同一份(該模組是單一權威)。
def usable_alias(a) -> bool:
    """這個寫法**單獨出現**時算不算指名了那個主體。

    裸數字(股票代號)與期間詞不算:它們在文字裡多半是張數/金額/季別。
    2026-08-22 r1 外審:這條規則原本只長在 `news_events.resolve_subject`,
    而 `aliases_of` 開始回公司別名(含代號)之後,`event_identity.
    _subjects_meet` 也會拿 `3231` 去比對「成交量 3231 張」——
    同一條規則要在**身分層**只有一份,否則擋住的那條路會從沒擋的漏回來。
    """
    import news_rules as _nr
    x = str(a or "").strip()
    return (len(x) >= 2 and not x.isdigit()
            and not _nr.PERIOD_TOKEN.fullmatch(x))


def aliases_of(name) -> tuple[str, ...]:
    """這個主體宣告過的全部別名(含 canonical 本身);認不得回空 tuple。

    供逐字驗證:「候選是 Pentagon、標題寫五角大廈」要靠這份清單接上。
    法域的別名從 `CANONICAL_SUBJECTS` 反查(它是 alias→canonical 表)。
    """
    canon = canonical_display(name)
    if not canon:
        return ()
    # **同一個主體的別名做聯集**(2026-08-22 外審 P2-2):三張表都可能各自
    # 宣告一部分(`_ORG_ALIASES` 有 Federal Reserve/聯儲、`entity_alias` 有
    # 美聯儲)。先前是「第一張表命中就回」,漏掉的那些在 producer 端就等於
    # 不存在。**別名的來源可以有多個,但 identity 只有一個** —— 聯集的成員
    # 全部 canonical 到同一個 `canon`,所以不會併到別的主體。
    out: list = []
    seen: set = set()

    def _add(names):
        for n in names or ():
            n = str(n or "").strip()
            if n and n not in seen:
                seen.add(n)
                out.append(n)

    _add(_ORG_ALIASES.get(canon))
    _gi = _al.group_of(canon)
    if _gi >= 0:
        _add(_al.ALIAS_GROUPS[_gi])
    if out:
        _add([canon])
        return tuple(out)
    # **公司也要在這裡**(2026-08-22 repo-wide 外審 P1-2)。先前只回機構與
    # 法域,而 producer 的跨語言驗證(`news_events.semantic_canonical`)
    # 是「`aliases_of` 非空才成立」—— 於是中文標題〈輝達否認…〉配上
    # 抽取器給的候選 `NVIDIA` 三條路全滅(known_names 查的是代號 NVDA、
    # 語意驗證因空 alias 不成立、literal 找不到英文字),entity 掉成空。
    # 鍵那一層收斂得再好也沒用:資料根本沒有以那個身分進來。
    # 這也讓本函式的 docstring(「宣告過的全部別名」)重新成立。
    juris = tuple({canon}
                  | {a for a, c in _ea.CANONICAL_SUBJECTS.items() if c == canon})
    if len(juris) > 1 or canon in _ea.CANONICAL_SUBJECTS.values():
        return juris
    return ()
