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

持久化的 state **不重寫鍵**:story/timeline 的既有 entity 原樣留著,
所有比較(story 匹配、timeline 鍵、migration 重驗、producer 正規化)
一律先過這裡。要新增一個主體:法域加 `event_actions.CANONICAL_SUBJECTS`,
機構/貨幣加下面的 `_ORG_ALIASES` —— 不要再開第四張表。
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


def cross_language_display(name) -> str:
    """**只做法域/機構**的跨語言正規名;公司與認不得的名字原樣返還。

    timeline/story 的公司鍵慣例是**代號**(`2330|earnings|…`)——
    `canonical_display` 會把 2330 收斂成台積電,拿它當鍵等於重寫全部
    公司鍵(生產 state 大遷移)。跨語言互打的病灶只在法域/機構
    (Russia/俄羅斯、UAE/阿聯、Pentagon/五角大廈),鍵的收斂只做這一段。
    """
    n = str(name or "").strip()
    if not n:
        return ""
    org = _ORG_LOOKUP.get(n.lower())
    if org:
        return org
    juris = _ea.canonical_subject(n)
    return juris if juris != n else n


def same_subject(a, b) -> bool:
    """兩個寫法是不是同一個主體(空值不算;原樣相等也算)。"""
    aa, bb = str(a or "").strip(), str(b or "").strip()
    if not aa or not bb:
        return False
    return aa == bb or canonical_display(aa) == canonical_display(bb)


def aliases_of(name) -> tuple[str, ...]:
    """這個主體宣告過的全部別名(含 canonical 本身);認不得回空 tuple。

    供逐字驗證:「候選是 Pentagon、標題寫五角大廈」要靠這份清單接上。
    法域的別名從 `CANONICAL_SUBJECTS` 反查(它是 alias→canonical 表)。
    """
    canon = canonical_display(name)
    if not canon:
        return ()
    if canon in _ORG_ALIASES:
        return _ORG_ALIASES[canon]
    juris = tuple({canon}
                  | {a for a, c in _ea.CANONICAL_SUBJECTS.items() if c == canon})
    if len(juris) > 1 or canon in _ea.CANONICAL_SUBJECTS.values():
        return juris
    return ()
