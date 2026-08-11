# -*- coding: utf-8 -*-
"""**宣告式的供應鏈地圖**(縱深第四批 C,橫向)。

使用者要的橫向是:一件事發生在鏈上的一點,信要寫得出它沿著上下游
會走到誰。模型自己也會猜 —— 但猜出來的名字先前正是 instrument
authority 要擋的東西(`ASEAN` 那一類)。所以這張圖是**宣告**:

  * 每一條邊都是人寫的(誰是誰的設備商/客戶/同業),不是相似度;
  * 每一個名字都必須是 `instrument_registry` **宣告過的標的** ——
    表的完整性有守衛盯著(`tests/test_sector_map.py`),
    加了一個沒宣告的名字當場紅;
  * 候選**不是證據**:它只告訴模型「這一步可以走到誰」,
    新聞本身要支持那一步才走(prompt 明講)。

漏邊只是少一個候選(模型仍可依新聞明講的名字寫);錯邊會把兩家無關的
公司黏在一條傳導鏈上 —— 所以這張表寧可短,不收關係弱的
(例如「同屬科技股」不是邊)。
"""
from __future__ import annotations

#: `(甲, 乙, 甲→乙的關係, 乙→甲的關係)`。名字用**別名組的代表寫法**
#: (`entity_alias.canonical` 之後的樣子)—— 比對端會先正規化。
EDGES = (
    # ── 晶圓代工的兩側:設備(上游)與客戶(下游)
    ("台積電", "ASML", "關鍵設備供應商(EUV)", "最大客戶之一"),
    ("台積電", "AMAT", "製程設備供應商", "主要客戶"),
    ("台積電", "LRCX", "製程設備供應商", "主要客戶"),
    ("台積電", "KLAC", "檢測設備供應商", "主要客戶"),
    ("台積電", "NVDA", "先進製程主要客戶", "晶圓代工供應商"),
    ("台積電", "AMD", "先進製程客戶", "晶圓代工供應商"),
    ("台積電", "AAPL", "先進製程最大客戶", "晶圓代工供應商"),
    ("台積電", "QCOM", "先進製程客戶", "晶圓代工供應商"),
    ("台積電", "AVGO", "先進製程客戶", "晶圓代工供應商"),
    ("台積電", "聯發科", "先進製程客戶", "晶圓代工供應商"),
    ("台積電", "INTC", "代工競爭者(部分產品亦為客戶)",
     "代工競爭者"),
    # ── AI 加速器的記憶體與整機
    ("NVDA", "美光", "HBM 供應商之一", "AI 加速器客戶"),
    ("NVDA", "SK海力士", "HBM 主力供應商", "AI 加速器客戶"),
    ("NVDA", "三星電子", "HBM 供應商之一(兼代工競爭)", "AI 加速器客戶"),
    ("NVDA", "鴻海", "AI 伺服器組裝夥伴", "GPU 供應商"),
    ("NVDA", "廣達", "AI 伺服器組裝夥伴", "GPU 供應商"),
    ("NVDA", "緯穎", "AI 伺服器組裝夥伴", "GPU 供應商"),
    ("NVDA", "SMCI", "AI 伺服器系統商", "GPU 供應商"),
    # ── 記憶體同業(價格與供需互相牽動)
    ("美光", "SK海力士", "記憶體同業(供需連動)", "記憶體同業(供需連動)"),
    ("美光", "三星電子", "記憶體同業(供需連動)", "記憶體同業(供需連動)"),
    ("SK海力士", "三星電子", "記憶體同業(供需連動)",
     "記憶體同業(供需連動)"),
    # ── 成熟製程與代工同業
    ("台積電", "聯電", "成熟製程代工同業", "成熟製程代工同業"),
    ("台積電", "GFS", "成熟製程代工同業", "成熟製程代工同業"),
    # ── 封測與代工
    ("台積電", "日月光", "封測合作夥伴(先進封裝互補)", "晶圓代工上游"),
    # ── **商品 → 產業**(2026-08-11 生產:油價暴漲那則,模型寫
    #    「→ 2610 華航」而 2610 既不是核心標的也不在圖上,整份作廢)。
    #    油價是這些產業**直接的成本項**,不是「同屬景氣循環」那種弱關係
    #    —— 這張表的規矩沒有變:寧可短,不收關係弱的。
    ("WTI", "華航", "燃油成本(佔營運成本兩成上下)", "油品需求端"),
    ("WTI", "長榮航", "燃油成本(佔營運成本兩成上下)", "油品需求端"),
    ("WTI", "長榮", "船用燃油成本", "油品需求端"),
    ("WTI", "陽明", "船用燃油成本", "油品需求端"),
    ("WTI", "萬海", "船用燃油成本", "油品需求端"),
    ("WTI", "台塑化", "原油是煉化的原料", "煉化業者"),
    ("WTI", "台塑", "石化原料成本", "石化業者"),
    # 布蘭特與西德州是同一件事的兩個報價 —— 邊要一樣完整,
    # 不然「今天的新聞寫哪一個」會決定分析過不過(外審 r1)。
    ("BRENT", "華航", "燃油成本(佔營運成本兩成上下)", "油品需求端"),
    ("BRENT", "長榮航", "燃油成本(佔營運成本兩成上下)", "油品需求端"),
    ("BRENT", "長榮", "船用燃油成本", "油品需求端"),
    ("BRENT", "陽明", "船用燃油成本", "油品需求端"),
    ("BRENT", "萬海", "船用燃油成本", "油品需求端"),
    ("BRENT", "台塑化", "原油是煉化的原料", "煉化業者"),
    ("BRENT", "台塑", "石化原料成本", "石化業者"),
)

#: 每個事件群最多**附幾個候選給模型看**。多了會稀釋 —— 模型該走的是
#: 新聞支持的那一兩步,不是把整條鏈抄一遍。
#:
#: **這是版面預算,不是語意邊界**(外審 r2):驗證端問的是「這條邊有沒有
#: 被宣告過」,那個問題與「今天秀幾個」無關。混用的話,第七條邊
#: (`WTI → 台塑`)宣告了卻永遠不算數 —— 宣告與生效分家,而症狀是
#: 模型照著合法的關係寫、分析整份被駁回。
MAX_CANDIDATES = 6


def _canon(name: str) -> str:
    import entity_alias as _ea
    return _ea.canonical(name)


def declared_neighbours(entities) -> list:
    """這些主體沿宣告過的邊走得到的**全部**標的(不套版面上限)。

    驗證端用這一個:「這條邊有沒有被宣告過」與「今天秀幾個」是兩個問題
    (外審 r2)。給模型看的那一份在 `transmission_candidates`。
    """
    canon_ents = {_canon(str(e)) for e in (entities or ()) if str(e).strip()}
    out, seen = [], set()
    for a, b, rel_ab, rel_ba in EDGES:
        ca, cb = _canon(a), _canon(b)
        for src, dst, rel in ((ca, cb, rel_ab), (cb, ca, rel_ba)):
            if src in canon_ents and dst not in canon_ents and dst not in seen:
                seen.add(dst)
                out.append({"name": dst, "via": src, "relation": rel})
    return out


def transmission_candidates(entities) -> list:
    """這一群事件的主體,沿宣告過的邊可以走到誰。

    回 `[{name, via, relation}]`(`via` 是鏈上的哪一個主體、
    `relation` 是那條邊的說明)。已在主體集合裡的不列(那不是傳導,
    是本人);同一個名字命中多條邊時取第一條(表的順序就是宣告的
    優先序)。認不出主體、或主體不在圖上 → 空清單(**不猜**)。
    """
    # **表的節點也要正規化**:表裡寫 `NVDA`,而別名組的代表寫法是
    # 「輝達」—— 兩邊不走同一套正規化的話,英文節點的邊整條失效
    # (第一版實測 `NVDA → []`,守衛只驗宣告、驗不到這件事)。
    return declared_neighbours(entities)[:MAX_CANDIDATES]


#: 每天最多為傳導對象發幾條橫向查詢。與縱向(`FOLLOWUP_MAX_QUERIES=5`)
#: 分開列預算 —— 每條是一次 Google News RSS 請求,而抓新聞是 wall-clock
#: 主導者。三條夠蓋當日主線的第一步傳導,不拖垮 25 分鐘預算。
HORIZONTAL_MAX_QUERIES = 3


def horizontal_queries(followups, limit: int = HORIZONTAL_MAX_QUERIES) -> list:
    """沿宣告過的邊,為追蹤中線索的**傳導對象**補主動查詢(橫向)。

    縱向追蹤(`story_ledger.followup_queries`)問「這條線索本身有沒有
    後續」;這裡問「**鏈上的下一步有沒有動**」。packet 裡的
    `transmission_candidates` 只是宣告的可能性、不是證據 —— 這支就是去把
    證據抓回來:查詢綁「候選 + 本尊」("ASML 台積電"),抓回的文章要
    真的提到本尊才會接回線索(`fetch_news` 的貼標閘門,與縱向同一個),
    只提到候選的進一般新聞池 —— 那是廣度,不是歸因。

    形狀與縱向的 followup dict 完全相同(`key`/`query`/`entity`/`name`,
    key/entity/name 都是**發起線索**的)—— 呼叫端與 `fetch_news`
    不需要分辨這條查詢是縱的還是橫的。

    候選的選法:

      * **輪流拿**(round-robin):每條線索先各拿第一個候選,額度還有
        再輪第二個 —— 單一線索不獨占橫向預算;
      * **縱向已經在追的名字不查**(它自己有查詢了);
      * 同一個候選被多條線索走到只查一次;
      * 線索的主體不在圖上 → 這條沒有橫向查詢(**不猜**,與
        `transmission_candidates` 同一條規矩)。
    """
    fus = [f for f in (followups or []) if isinstance(f, dict)
           and str(f.get("name") or "").strip()]
    tracked = {_canon(str(f.get("name"))) for f in fus}
    tracked |= {_canon(str(f.get("entity") or "")) for f in fus}
    per_story = [[c for c in transmission_candidates([f["name"]])
                  if c["name"] not in tracked] for f in fus]
    out, used = [], set()
    for rank in range(MAX_CANDIDATES):
        for f, cands in zip(fus, per_story):
            if len(out) >= limit:
                return out
            if rank >= len(cands) or cands[rank]["name"] in used:
                continue
            cand = cands[rank]
            used.add(cand["name"])
            out.append({"key": str(f.get("key") or ""),
                        "query": f"{cand['name']} {f['name']}",
                        "entity": str(f.get("entity") or ""),
                        "name": str(f.get("name"))})
    return out
