# -*- coding: utf-8 -*-
"""**引用的東西存不存在**(schema v2 時從 `analysis_schema` 拆出)。

這個 repo 把「分析輸出的合法性」刻意切成三塊,各自有版本、各自能被單獨測:

  * `analysis_schema` —— **形狀**。strict Structured Outputs 保證得了的部分。
  * `analysis_grounding` —— **有話說就要說得出根據**。哪些會進信的段落
    必須帶證據。
  * 這個模組 —— **引用的 ID 是不是真的存在**,以及 schema 表達不了的
    跨欄位不變式(高重要性主張要有證據、關係要指向真的存在的條目、
    沒有證據的因果步驟不得自稱 fact)。

**編造的引用比沒有引用更危險** —— 它讓錯誤看起來有根據。這是本模組
存在的唯一理由,所有判準都繞著它。

拆出來的直接原因是 schema v2 讓 `analysis_schema.py` 逼近行數上限,
而「形狀」與「檢查」本來就是那個檔的 docstring 自己說要分開的兩件事。
"""
from __future__ import annotations

import re as _re

import analysis_grounding as _gr

#: 條數契約住在 `analysis_contracts`(P1-5:判準只能有一份)。此處再匯出。
import analysis_contracts as _ac  # noqa: E402
from analysis_contracts import KEY_DRIVERS_REQUIRED, key_drivers_required  # noqa: E402,F401

#: **不算標的的泛稱。** 這些字出現在 `asset_id` 時,那一格等於沒有拆 ——
#: 而 renderer 會把它排得跟真的逐標的分析一模一樣,讓泛論看起來更像
#: 深度分析。**比不拆更糟。**(第十九輪 P1-9)
_GENERIC_ASSETS = frozenset({
    "市場", "大盤", "台股", "股市", "整體市場", "相關產業", "產業", "概念股",
    "供應鏈", "科技股", "電子股", "類股", "全球市場", "市場情緒", "投資人",
})

#: 泛稱**詞素**。第二十輪 P1-8:exact-match 黑名單一個修飾詞就繞過
#: (「台灣市場」「半導體產業」「相關電子族群」全數通過)——
#: 判準改成:**長得像代號/指數的放行,其餘只要含泛稱詞素就擋**。
_GENERIC_MORPHEMES = ("市場", "產業", "類股", "族群", "供應鏈", "概念",
                      "相關", "整體", "主要", "板塊", "個股", "同業")

#: 長得像具體標的:台股代號(2330、00662、6510A)、常見指數/ETF 代碼。
_ASSET_LIKE = _re.compile(r"[0-9]{4,6}[A-Z]?|[A-Z]{2,6}")
_KNOWN_ASSETS = frozenset({"TAIEX", "OTC", "SOX", "QQQ", "SPY", "TSM",
                           "market-wide", "加權指數", "櫃買指數", "費半"})


def _is_generic_asset(aid: str) -> bool:
    if aid in _KNOWN_ASSETS or _ASSET_LIKE.fullmatch(aid):
        return False
    return aid in _GENERIC_ASSETS or any(m in aid for m in _GENERIC_MORPHEMES)


#: 台股代號的形狀(2330、00662、6510A)。
_TW_CODE = _re.compile(r"[0-9]{4,6}[A-Z]?")

#: **永遠不是股票代號的商用縮寫**(第二十五輪 P1-7)。
#: 它們長得像 2–4 位大寫 ticker、常常出現在標題裡、不在概念詞表 ——
#: 於是「CEO resigns after earnings miss」可以掛 `asset_id: "CEO"`。
#: 與概念詞表同一個道理:這些詞在標題出現的頻率極高,而它們**永遠不是
#: 可交易標的**,「出現在證據裡」對它們等於沒有判準。
_NOT_TICKER_ABBREV = frozenset({
    "CEO", "CFO", "COO", "CTO", "CIO", "IPO", "SPO", "EPS", "PER", "PBR",
    "ROE", "ROA", "GDP", "CPI", "PPI", "PCE", "PMI", "FOMC", "SEC", "FDA",
    "FTC", "DOJ", "WTO", "IMF", "OPEC", "ADR", "GDR", "ETF", "API", "FAQ",
    "GAAP", "EBITDA", "CAPEX", "OPEX", "YOY", "QOQ", "MOM",
    # 政府間組織與機關:是事件的主體,不是可交易標的
    "UN", "NATO", "OECD", "WHO", "IEA", "BIS", "ECB", "BOJ", "PBOC",
    "DOE", "DOD", "DHS", "USTR", "CBP", "BIS2",
})

#: **產品與技術概念** —— 在新聞標題出現的頻率極高,「出現在證據裡」
#: 對它們永遠成立;而它們永遠不是可交易標的(第二十二輪 P1-6)。
_CONCEPT_TERMS = frozenset({
    "ai", "gpu", "cpu", "chip", "chips", "hbm", "asic", "ml", "llm",
    "cowos", "ev", "5g", "iot", "cloud", "saas", "api", "ar", "vr",
    # 雲端與技術品牌:它們是產品線,不是可交易標的
    # (要談那家公司請寫 AMZN / MSFT / GOOGL / NVDA)
    "aws", "azure", "gcp", "cuda", "rocm", "arm64", "x86", "risc-v",
})


#: **會計期間不是標的**(第二十六輪 P1-6)。`Q2`、`FY25`、`1H`、`TTM`
#: 出現在幾乎每一則財報新聞的標題裡,而它們永遠不是可交易標的 ——
#: 與 `CEO`/`GPU` 同一個形狀:「出現在證據裡」對它們**永遠成立**,
#: 那個判準等於沒有判準,而 renderer 會把 `asset_id: "Q2"` 排得跟
#: 真的逐標的分析一模一樣。
#: **只擋帶數字的那些**(外審第二輪 P2)。第一版把裸縮寫也列進去,
#: 而 `MTD` 是 Mettler-Toledo、`TTM` 也有人在用 —— 絕對黑名單會在
#: 那家公司真的上新聞的那天把合法分析判掉,而**誤殺比漏放危險**
#: (這個 repo 記過)。帶數字的期間詞沒有這個歧義:美股代號不含數字,
#: 台股代號是純數字加選擇性的字尾,兩邊都不會長成 `Q2` / `FY25`。
#: 代價是裸 `FY`、`YTD` 仍可能混進來 —— 那是刻意選的那一側。
#: 判準本體移到 `news_rules.PERIOD_TOKEN`(r3:與 news_events 的
#: literal-subject 篩查共用同一份,兩份已經漂移過一次)。
from news_rules import PERIOD_TOKEN as _PERIOD_TOKEN  # noqa: E402


#: **既是期間縮寫、也可能是代號**的那些(外審第二輪 P2)。
#: `MTD` 是 Mettler-Toledo、`TTM` 也有人在用 —— 一律列進絕對黑名單會在
#: 那家公司真的上新聞的那天把合法分析判掉;而一律放行,
#: 「Revenue rises on a TTM basis」這種標題又能讓 `asset_id: "TTM"` 過關。
#: 判準因此**看上下文**:出現在 `entities` 裡才算公司,
#: 只在標題裡出現的那個字,幾乎必然是被當成期間在用。
_AMBIGUOUS_PERIOD_ABBREV = frozenset({
    "MTD", "TTM", "YTD", "QTD", "FY", "CY", "FYE", "LTM",
})

#: **與法域撞名的真代號**(第二十七輪外審第二輪)。`EU` 是歐盟,也是
#: enCore Energy 的美股代號。與期間縮寫是同一個形狀,但**確認的方式更嚴**:
#: 期間那組可以靠「出現在 `entities` 裡」確認,而 `EU` 出現在 entities
#: 裡多半就是歐盟本身 —— 所以只認**宣告過的別名組**或**交易所限定寫法**
#: (`NASDAQ: EU`)。
_AMBIGUOUS_JURISDICTION = frozenset({"EU"})

#: 兩組合起來:這些字**不進絕對黑名單**,改走看上下文的判準。
_AMBIGUOUS_ABBREV = _AMBIGUOUS_PERIOD_ABBREV | _AMBIGUOUS_JURISDICTION

#: **與普通英文單字撞名的已宣告 ticker**(第二十九輪外審 P1-2B)。
#: `NOW` 是 ServiceNow,也是副詞;`NET` 是 Cloudflare,也出現在
#: "net income";`ARM`/`SNOW`/`COIN` 同理。「宣告過」只回答得了
#: 「它是不是真 ticker」,回答不了「這個句子裡的 now 是不是在講那家
#: 公司」—— 所以這些字**不得靠標題裸字命中**:要嘛 entities 裡有
#: **大小寫一致**的那個代號(抽取器抓 ticker 時保留大寫),
#: 要嘛交易所限定寫法(`NYSE: NOW`)。
_COMMON_WORD_TICKERS = frozenset({"NOW", "NET", "ARM", "SNOW", "COIN"})

#: 歧義代號 → **公司名**(第三十輪外審 P2-4)。extractor 抽出的是公司名、
#: 模型寫的是代號,中間沒有橋的話,`ServiceNow` 的真新聞會與副詞 `now`
#: 一起被擋(fail-closed 但誤殺)。
#:
#: **刻意不放進 `entity_alias.ALIAS_GROUPS`**(外審 r2):那張表是全域的
#: 主體等價關係,`canonical("Arm")` 一旦回 `Arm Holdings`,
#: 「Arm 架構」的新聞在 event identity 那邊就會與 Arm Holdings 的線索
#: 併成同一條。歧義只該影響問這個問題的人 —— 這裡只回答
#: 「這則新聞的實體支持這個代號嗎」。
_TICKER_COMPANY_NAMES = {
    "NOW": ("servicenow",),
    "NET": ("cloudflare",),
    "ARM": ("arm holdings",),
    "SNOW": ("snowflake",),
    "COIN": ("coinbase",),
}


def _ticker_notation(a: str, news_item) -> bool:
    """這則新聞用**交易所限定的寫法**點名了這個代號嗎(`NYSE: MTD`)。

    只認這一種自由文字裡的權威寫法。**「公司名後面接括號」不算**
    (外審第四輪):`Apple (TTM) valuation reaches…` 完全符合那個樣式,
    而 `TTM` 在那裡是估值的期間、不是蘋果的代號 —— 文字相鄰證明不了
    「這個縮寫是那家公司的代號」,那是一個**要被宣告的對應**
    (`entity_alias.ALIAS_GROUPS`),不是從版面推導得出來的。
    """
    body = (str((news_item or {}).get("title") or "") + " "
            + str((news_item or {}).get("summary") or ""))
    return bool(_re.search(
        r"(?:NYSE|NASDAQ|AMEX|NYSEARCA|TWSE|TPEX|OTC)\s*[:：]\s*"
        + _re.escape(a) + r"(?![A-Za-z0-9])", body, _re.IGNORECASE))


def period_word_not_an_entity(aid, news_item) -> bool:
    """歧義縮寫**只在標題裡**出現 → 那是期間/法域,不是這則新聞的主角。

    只擋這一種情形:字在 `_AMBIGUOUS_PERIOD_ABBREV` 裡,而且這則新聞
    沒有把它當公司在寫 —— 出現在 `entities`、**宣告過**的別名同組
    (`entity_alias.ALIAS_GROUPS`)、或交易所限定寫法(`NYSE: MTD`)都算。
    抽取器把公司名放進 `entities`;標題裡的 `TTM` 不會進去,
    因為它在那句話裡是個期間。

    **「公司名後面接括號」不是權威**(外審第四輪):`Apple (TTM)` 符合
    那個樣式,而它是估值期間。代號與公司的對應要被宣告,不是從版面推導。
    """
    a = str(aid or "").strip()
    if a.upper() not in _AMBIGUOUS_ABBREV:
        return False
    ents = {str(e).lower() for e in ((news_item or {}).get("entities") or [])}
    # **與法域撞名的那組不認 `entities` 字面**:`EU` 出現在 entities 裡
    # 多半就是歐盟本身,拿它當「這是公司」的證據會把每一則歐盟新聞都
    # 變成一張逐標的方向卡。只認宣告過的別名組或交易所限定寫法。
    if a.upper() not in _AMBIGUOUS_JURISDICTION and a.lower() in ents:
        return False
    import entity_alias as _ea
    if _ea.same(a, _ea.expand({str(e) for e in
                               ((news_item or {}).get("entities") or [])})):
        return False
    return not _ticker_notation(a, news_item)


def never_an_instrument(aid) -> bool:
    """這個字**在定義上就不是可交易標的**(與今天的證據無關)。

    三類同一個形狀:商用縮寫(`CEO`/`IPO`/`EPS`)、產品概念(`AI`/`GPU`)、
    會計期間(`Q2`/`FY25`)。它們長得像 ticker、在標題裡出現的頻率極高,
    所以「有沒有出現在證據裡」這個判準對它們永遠成立。

    **獨立成一個判準是為了讓訊息說得出真正的理由**:先前這三類都從
    `_asset_unknown_to_evidence` 回 `True`,而呼叫端的訊息寫「不在這則
    新聞的實體或標題裡」—— 對 `Q2` 來說那句話是**假的**,它就在標題裡。
    """
    a = str(aid or "").strip()
    if not a:
        return False
    import event_actions as _ea
    # **撞名的那些不進絕對黑名單**(外審第二輪):`EU` 是歐盟,也是
    # enCore Energy 的美股代號 —— 一律擋會把合法的逐標的卡判掉,
    # 而那會讓整份特化分析進修補、修不好就降級。改走看上下文的判準
    # (`period_word_not_an_entity`)。
    if a.upper() in _AMBIGUOUS_ABBREV:
        return False
    return (a.upper() in _NOT_TICKER_ABBREV
            or a.lower() in _CONCEPT_TERMS
            or bool(_PERIOD_TOKEN.fullmatch(a))
            # **法域永遠不是可交易標的**(第二十七輪外審 P1-5):
            # `US` 精確出現在 entities 與標題裡,於是被當成可渲染的
            # 逐標的方向卡 —— 而「出現在證據裡」對事件主體永遠成立。
            or _ea.is_jurisdiction(a))


#: 兩步之間「同一個節點」至少要共用幾個字。太短的共用(「油價」)
#: 可能只是巧合,而這一關的用途是確認**上一步的終點就是這一步的起點**。
_NODE_MIN_CHARS = 4

#: 因果鏈交接判準裡**不算指名**的泛用二元組。這些詞出現在大半的
#: 財經句子裡,共用它證明不了「上一步的終點就是這一步的起點」。
#: 只在 `_same_node` 用 —— 事件身分那套 `GENERIC_NEWS_WORDS`
#: 是另一個問題的清單(那邊挖的是新聞動詞),別合併。
_GENERIC_NODE_TOKENS = frozenset({
    "市場", "經濟", "資金", "投資", "價格", "需求", "供給",
    "成長", "風險", "壓力", "趨勢", "股市", "影響", "預期"})


def _same_node(prev_to: str, cur_from: str, subjects=()) -> bool:
    """這一步的起點,就是上一步的終點嗎。

    先前的判準是**逐字相等**,而 prompt 與 schema 從來沒說過要逐字沿用
    (2026-08-11 生產:上一步走到「荷莫茲海峽重開協議停滯」、這一步從
    「荷莫茲海峽承載全球約五分之一石油與天然氣、重開無望」開始 ——
    語意上是同一個節點,整份特化分析卻因此作廢)。**沒告訴模型的規則
    不能拿來駁回**;規則已經寫進 schema 說明,而判準同時放寬到
    「一方包含另一方」:補充細節仍是同一個節點,而兩個不相干的片段
    共用不了整段文字。
    """
    import re as _re2

    def _norm(s):
        return _re2.sub(r"[\s,,、。;;::「」『』()()]+", "", str(s or ""))

    a, b = _norm(prev_to), _norm(cur_from)
    if not a or not b:
        return True
    if a == b:
        return True
    # **照抄再補充**(schema v18 明講的形狀):上一步的終點完整出現在
    # 這一步的起點裡 —— 這個方向不設長度下限,因為短邊就是**整個**
    # to_what,模型照規格抄它就該放行(第三十一輪外審 P1-4:
    # 「需求」→「需求持續轉弱」是合規的接法,不是巧合)。
    if len(a) >= 2 and a in b:
        return True
    # 反向(起點是終點的截取)仍要 ≥ _NODE_MIN_CHARS —— 這一向沒有
    # 「照抄」的語意背書,太短的包含(「油價」)可能只是巧合。
    if len(b) >= _NODE_MIN_CHARS and b in a:
        return True
    # **改寫不是斷鏈**(2026-08-11 生產,schema 已經明講要沿用之後仍然
    # 發生):上一步走到「美伊重啟談判的希望再降,雙方立場差距擴大」、
    # 這一步從「美伊談判希望降低」開始 —— 同一個節點換句話說。
    # 這一關要抓的是**不相干的片段被接成因果**,不是措辭。
    # 判準用既有的辨識詞(與事件身分同一套機器)。**問「有沒有指名」,
    # 不問「重疊多少」**(2026-08-11 CI #495,同一條規則第三次擋下整封信):
    # 上一步走到「…選擇相信談判:油價收 82.5 美元、新興市場資產反彈」、
    # 這一步從「油價與通膨預期」開始 —— 讀起來完全接得上,而重疊比例只有
    # 0.17。上一步是一句**列舉多個結果**的長句(那正是本報要的敘事),
    # 下一步合理地只接走其中一個,共用的那一格於是被整句稀釋 ——
    # 比例量的是「兩段在講同一件事」,而這一關要問的是
    # **「上一步的終點有沒有指名過這一步的起點」**,不是同一個問題。
    # 實測:不相干的片段共用 0 個辨識詞,而三次誤擋都在 1 個以上。
    # 誠實記下代價:兩句偶然共用一個二元組(「市場」)也會過關 ——
    # 換來的是一句改寫不再讓整封信退回 legacy。
    # **主體名要挖掉**(外審 r2,而這是本 repo 記過的同一條規矩):
    # 主體相交是另一層的判準,標題重疊若又被主體名灌滿等於把同一份證據
    # 算兩次 —— 「台積電營收創高」與「台積電法說下週」共用的
    # 「台積」「積電」就足以越過門檻,而它們是兩件事。
    import event_identity as _eid2
    ta = _eid2.discriminative_tokens(prev_to, subjects)
    tb = _eid2.discriminative_tokens(cur_from, subjects)
    if min(len(ta), len(tb)) < _eid2.MIN_DISCRIMINATIVE:
        # **判不出來 → 不接**(第三十一輪外審 P1-4)。先前這裡回 True,
        # 於是「需求」→「毛利」這種完全斷掉的鏈確定性放行 ——
        # 而 schema 已明講要照抄,合規的短節點在上面的包含判準就放行了,
        # 走到這裡的短節點只剩「沒有照抄的兩個片段」。
        return False
    # **一個泛用詞不算指名**(同輪 P1-4):「市場需求轉弱」→「市場資金
    # 回流」靠「市場」過關,而那是兩個節點。泛用詞清單只在這一關用
    # (事件身分那套共用機器不動 —— 改它會牽動 timeline 與 recap)。
    return bool((ta & tb) - _GENERIC_NODE_TOKENS)


#: 非主角的標的要寫得出多長的傳導機制。短於這個長度的多半是
#: 「成本上升」這種標籤,說明不了任何一步 —— 而「說得出機制」正是
#: 這條放行條件的全部理由。
_MECHANISM_MIN_CHARS = 12


def _transmission_ok(aid: str, news_item, packet) -> bool:
    """`transmission_tier` 的布林出口(放行集合不變)。"""
    return bool(transmission_tier(aid, news_item, packet))


def transmission_tier(aid: str, news_item, packet) -> str:
    """這個非主角標的是哪一層傳導:`core` / `declared` / `universe` / 空。

    第三十二輪外審 P1-3 的產品決策(選項 B):universe 放行**維持**
    (撤掉會回到真台股被擋、整封退 legacy),但兩層要分得開 ——
    `tw_universe` 證明的是「這是一檔真的股票」,不是「這件事真的會
    傳導到它」。宣告邊(sector_map)與核心標的是**已驗證**的傳導;
    只靠 universe 放行的,信裡標「推測性傳導」讓讀者自行折價。
    宣告邊的判準排在 universe 之前 —— 又真又宣告過的標的要拿到
    比較強的那個標籤。
    """
    import instrument_registry as _ir5
    if _ir5.is_core_asset(aid):
        return "core"
    # **當日 universe 裡的台股**:這道閘門本來要擋的是「假代號」——
    # 「指數上漲 9999 點」的 9999、「2026 展望」的 2026。而 universe 是
    # Python 抓回來的當日上市清單:在裡面就代表它是一檔**真的、今天在
    # 交易的股票**,那個擔憂不成立(閘門自己的訊息也寫著「真的要談某檔
    # **美股**」—— 亂灑的風險在美股那側)。
    #
    # 2026-08-11 連三班因為這條被擋下整份分析:2610 華航、3661 世芯-KY、
    # 2408 南亞科 —— 每一檔都是真的,而供應鏈圖一次只補得到一兩個名字。
    # 逐個宣告追不上,而**讀者的代價是整封信退回 legacy**(沒有事件卡、
    # 沒有淨效果、沒有橫向綜合)。
    # 呼叫端仍然要求寫得出傳導機制;美股那側的規則一個字都沒動。
    try:
        import entity_alias as _ea5
        import sector_map as _sm2
        want = _ea5.canonical(aid)
        # **候選是整個事件群算出來的**(外審 r2):packet 裡那份
        # `transmission_candidates` 聚合了群內所有成員的實體,而模型看到的
        # 就是那一份。只用被選中的**那一篇**重算,會拒絕模型照著我們給的
        # 候選寫出來的合法標的 —— 自相矛盾,而且症狀是整份降級。
        sid = str((news_item or {}).get("source_item_id") or "")
        by_id = {str(x.get("source_item_id")): x
                 for x in ((packet or {}).get("news") or [])
                 if isinstance(x, dict)}
        # **主體要用整群的**(外審第二輪):候選是聚合群內所有成員的實體
        # 算出來的,而被選中的那一篇未必含那個主體 —— 只看它自己的話,
        # 「群內另一篇提到 WTI + 這條邊排在版面上限之外」的合法組合
        # 兩條路都走不通,分析照樣被駁回。
        ents = [str(e) for e in ((news_item or {}).get("entities") or [])]
        for c in ((packet or {}).get("news_clusters") or {}).get(
                "clusters", []) or []:
            if not isinstance(c, dict):
                continue
            members = [str(m) for m in (c.get("member_source_ids") or [])]
            if sid and sid not in members:
                continue
            if any(_ea5.canonical((x or {}).get("name")) == want
                   for x in (c.get("transmission_candidates") or [])):
                return "declared"
            ents = [str(e) for m in members
                    for e in (by_id.get(m, {}).get("entities") or [])] or ents
            break
        # 候選沒帶(ID-set 相容路徑、分群失敗),或那條邊排在**版面上限
        # 之外** —— 問「這條邊有沒有被宣告過」。上限是給模型看幾個的
        # 預算,不是語意邊界。
        if any(_ea5.canonical(c.get("name")) == want
               for c in _sm2.declared_neighbours(ents)):
            return "declared"
    except Exception as _te:                            # noqa: BLE001
        # 宣告層炸掉不吃掉 universe 層(原本 universe 在最前)——
        # 但**降級不得靜默**(外審 r1):宣告過的台股會被錯標成推測層,
        # 而事後沒有任何痕跡就查不到為什麼。stderr 進 job log,
        # 呼叫端(run_quality)另有 unknown-degradation 白名單機制,
        # 這裡選 stderr + GitHub annotation(::warning:: 不需要 admin)。
        import sys as _sys
        print(f"::warning::transmission_tier 宣告層失效,"
              f"退用 universe 層:{type(_te).__name__}: {_te}",
              file=_sys.stderr)
    # **當日 universe 裡的台股**:這道閘門本來要擋的是「假代號」——
    # universe 證明它是一檔真的、今天在交易的股票(2026-08-11 連三班
    # 真台股被擋、整封退 legacy 的教訓);但它證明不了「這件事會傳導
    # 到它」—— 所以是最弱的一層,信裡標「推測性傳導」。
    if _TW_CODE.fullmatch(str(aid or "")):
        _uni = {str(x.get("code") or "")
                for x in (((packet or {}).get("tw_universe")) or [])
                if isinstance(x, dict)}
        if aid in _uni:
            return "universe"
    return ""


def speculative_transmission(aid: str, analysis_entry, packet) -> bool:
    """信裡要標「推測性傳導」的標的:不是新聞主角、也不是核心/宣告邊,
    只靠當日 universe 放行(第三十二輪 P1-3,選項 B)。

    renderer 呼叫這裡而不是自己判 —— 判準要與 validator 同一份,
    各寫一份會漂移(這個 repo 記過的規矩)。
    """
    try:
        sid = str((analysis_entry or {}).get("source_item_id") or "")
        item = next((x for x in ((packet or {}).get("news") or [])
                     if isinstance(x, dict)
                     and str(x.get("source_item_id")) == sid), None)
        if item is None:
            return False
        if not _asset_unknown_to_evidence(aid, item, packet):
            return False               # 主角:名字就在新聞裡,不是推測
        return transmission_tier(aid, item, packet) == "universe"
    except Exception:                                   # noqa: BLE001
        return False                   # 標籤是加值,失敗不得毀掉渲染


def _asset_unknown_to_evidence(aid: str, news_item, packet) -> bool:
    """**大寫字母的「標的」要是證據裡的人**(第二十輪 P2-4 的收尾)。

    `_ASSET_LIKE` 放行任何 2–6 個大寫字母 —— 於是 `AI`、`GPU`、`CHIP`
    都能冒充標的,而 renderer 會把它們排得跟真的逐標的分析一模一樣。
    可是 `AMD`、`TSM` 又是真的 —— **字串格式分不出「代號」與「概念」**。
    分得出的是證據:分析 AMD 新聞時,AMD 在那則新聞的 `entities` 裡;
    「GPU」不會是任何新聞的實體。台股代號與已知指數/ETF 照舊放行。
    """
    a = str(aid or "").strip()
    if not a:
        return False
    # **「是已知標的」不等於「與這件事有關」**(P1-12):先前白名單是無條件
    # 繞過。型別查表、相關性看證據;只有指數豁免(理由見 instrument_registry)。
    import instrument_registry as _ir
    _cid, _scope = _ir.resolve(a, packet)
    if _cid and not _ir.needs_event_evidence(_scope):
        return False
    # 第二十二輪 P1-6:**產品概念永遠不是可交易標的** —— 即使標題就叫
    # 「GPU demand accelerates」。這些詞在標題出現的頻率極高,
    # 「在證據裡」這個判準對它們永遠成立,等於沒有判準。
    if never_an_instrument(a):
        return True
    ents = {str(e) for e in ((news_item or {}).get("entities") or [])}
    title = str((news_item or {}).get("title") or "")
    body = title + " " + str((news_item or {}).get("summary") or "")
    # 第二十一輪 P1-9:**大小寫不是判準。** 上一版只檢查
    # `a.isupper()` —— `gpu`、`Ai`、`chip` 全部繞過。
    # 判準是「這個字在證據裡出現過嗎」,而比對要忽略大小寫。
    # 第二十五輪 P1-7:**registry 是判準,不是參考。** 上一版對 ASCII
    # token 只看「有沒有出現在標題/實體裡」—— 於是 `CEO`、`IPO`、`EPS`、
    # `CFO`、`ADR` 這些只要出現在標題就通過(它們長得像 2–6 位 ticker、
    # 不在概念黑名單、也不是已知標的)。
    # **只擋「確定不是標的」的那些。** 第一版拿 registry 的
    # `INVALID` 當條件,而 `_KNOWN` 只有八檔 —— `NVDA`、`AMD` 這些
    # 真 ticker 一起被殺。**誤殺比漏放危險**(repo 記過),
    # 改成明確的縮寫黑名單。
    low = a.lower()
    if a.isascii() and not _TW_CODE.fullmatch(a):
        # **歧義縮寫要先問**(第二十七輪外審第二輪):`EU` 出現在
        # `entities` 裡多半就是歐盟本身 —— 讓「字面命中 entities」先跑的話,
        # 每一則歐盟新聞都會變成一張逐標的方向卡。
        # (`TTM`/`MTD` 那組仍然認 entities,判準見那個函式。)
        if period_word_not_an_entity(a, news_item):
            return True
        if a.upper() in _COMMON_WORD_TICKERS:
            # 裸字命中不算(副詞 now / net income / Arm 架構)——
            # 要 entities 大小寫一致、**公司名的別名組**,或交易所寫法。
            # 別名組那一條是第三十輪外審 P2-4:extractor 抽出的是
            # `ServiceNow`,而模型寫的是 `NOW` —— 中間沒有橋的話,
            # 合法新聞與副詞一起被擋(fail-closed 但誤殺)。
            if any(a.upper() == str(e) for e in ents):
                return False
            # 宣告過的**公司名**才算橋(表在上面;代號自己不算 ——
            # 否則裸字會從 entities 那條路回來:`Arm architecture` 的
            # entities 就是 `Arm`)。
            _names = _TICKER_COMPANY_NAMES.get(a.upper(), ())
            if any(str(e).lower() in _names for e in ents):
                return False
            return not _ticker_notation(a, news_item)
        # **未知的大寫字串是「未知實體」,不是「可能是標的」**
        # (第二十八輪外審 P1-2)。上一版的判準是「長得像 2–6 位大寫字母
        # 且出現在證據裡」,再靠黑名單排除 —— 而黑名單追不完開放字彙:
        # `ASEAN`、`BRICS` 是國際組織,`XYZAB` 誰也不是,三者都通過。
        # 改成**正面條件**:要嘛被宣告過(`instrument_registry.is_declared`
        # —— `_KNOWN` 或 `entity_alias` 的別名組),要嘛這則新聞用交易所
        # 限定寫法點名了它(`NASDAQ: EU`)。
        import instrument_registry as _ir2
        if not (_ir2.is_declared(a) or _ticker_notation(a, news_item)):
            return True
        if any(low == str(e).lower() for e in ents):
            return False
        # **token 邊界,不是裸子字串**(第二十二輪 P1-6):
        # `Ai` 曾因藏在 `Taiwan` 裡被放行。
        if _re.search(r"(?<![A-Za-z0-9])" + _re.escape(low)
                      + r"(?![A-Za-z0-9])", body, _re.IGNORECASE):
            return False
        return True
    if not a.isascii():
        # **中文實體也要走宣告閘門**(第二十九輪外審 P1-2A):上一版只問
        # 「有沒有出現在證據裡」—— 而「聯準會」精確出現在自己的新聞裡,
        # 它是機構不是可交易標的。ASCII 分支已經是正面條件,
        # 這裡不補的話等於同一道門只關了一半。
        import instrument_registry as _ir3
        if not _ir3.is_declared(a):
            return True
        if any(a in str(e) or str(e) in a for e in ents) or a in body:
            return False
        import entity_alias as _ea
        if _ea.same(a, _ea.expand(ents)):
            return False
        return True
    if _TW_CODE.fullmatch(a):
        # 台股代號:**要與這一則新聞有關**。先前任何 4–6 位數都放行,
        # 於是 `999999`、`12345A` 這種不存在的代號冒充逐標的分析。
        # `packet is None` 的舊呼叫端**不再是全放行**(第二十九輪
        # P1-2C):證據判準(下面三關)不需要 packet,照走。
        #
        # **正文命中要有兩個限制**(第二輪 F2):
        #   * token 邊界 —— 裸子字串會讓 `2330` 藏在 `123300` 裡也算;
        #   * **年份形狀的代號(1900–2100)不吃正文** ——
        #     `asset_id="2026"` 配 "2026 market outlook",命中的是年份
        #     不是公司;這種代號要 entities 精確命中或別名組。
        if any(a == str(e).strip() for e in ents):
            return False
        # **正文命中只給「驗過的代號」加分**(第三輪外審):數字與公司名
        # 不同 —— 「指數上漲 9999 點」的 9999 是點位、「2026 展望」的
        # 2026 是年份。正文出現一個數字證明不了它是代號;
        # 它要嘛**宣告過**(2330 的別名組)、要嘛在**當日 universe** 裡,
        # 正文命中才算相關。年份形狀(1900–2100)連這個都不吃 ——
        # 那個範圍的四位數在財經文本裡幾乎必然是年份。
        _yearish = len(a) == 4 and a.isdigit() and 1900 <= int(a) <= 2100
        import instrument_registry as _ir4
        known = {str(x.get("code") or "")
                 for x in (((packet or {}).get("tw_universe")) or [])
                 if isinstance(x, dict)}
        if (not _yearish and (_ir4.is_declared(a) or a in known)
                and _re.search(r"(?<![0-9A-Za-z])" + _re.escape(a)
                               + r"(?![0-9A-Za-z])", body)):
            return False
        import entity_alias as _ea
        if _ea.same(a, _ea.expand(ents)):
            return False               # 2317 的新聞實體寫「鴻海」
        # **走到這裡代表代號不在證據裡**(前面三關都沒命中)——
        # 那不論 universe 在不在都該擋(第二十九輪外審 P1-2C):
        # 上一版在 universe 空的那天放行,而「資料斷供的日子」正是
        # 假代號最不會被抓到的日子。宣告過的代號(2330 那些)在
        # `never_an_instrument` 之前的宣告閘門就處理了,不受影響。
        return True
    if not a.isascii():
        return False                    # 中文名稱交給泛稱檢查
    return True

# `STANCE_LABELS` 在函式內延遲取用 —— `analysis_schema` 的尾端會反向
# import 本模組(相容出口),頂層互相 import 會在「誰先被載入」上翻車。


def _registry(evidence_ids):
    """接受 **packet 或 ID 集合**(第十六輪 P1-1/P1-2)。

    傳 packet 時才驗得了「有張力卻沒有橫向綜合」這類**與當日輸入有關**
    的不變式 —— 只有一個 ID 集合的話,驗證器看不到今天有幾筆張力、
    有幾則高重要性新聞,於是空的輸出可以真空通過。
    舊呼叫端傳 set 仍然可用(只是少掉那幾條判準,並且說得出少了什麼)。
    """
    if isinstance(evidence_ids, dict) and "news" in evidence_ids:
        import evidence_packet as _ep
        return _ep.evidence_ids(evidence_ids), evidence_ids
    return set(evidence_ids or ()), None


def _unusable(packet) -> dict:
    """今天**不能拿來當方向證據**的 ID(第十八輪 P1-2 的用途)。

    有 metadata 才問得出這個問題 —— 只有一串合法字串時,
    「引用了昨天的美股數字」與「引用了今天的」長得一模一樣。
    """
    if not isinstance(packet, dict):
        return {}
    import evidence_registry as _reg
    return _reg.unusable_ids(packet)


# ---------------------------------------------------------------- 相容出口
#
# 完整性檢查搬到 `analysis_crosscheck`(見該檔:形狀與完整是兩件事)。
from analysis_crosscheck import (                  # noqa: E402,F401
    _alignment_problems, _claim_graph_problems, _coverage_problems,
    event_graph_problems, top_event_problems)


def validate(obj, evidence_ids) -> list:
    """回傳問題清單(空 = 通過)。**不拋例外**:呼叫端決定要修還是降級。

    只驗「schema 管不到」的:
      - 證據 ID 是否真的存在於本日 packet(**編造的 ID 比沒有 ID 更危險**,
        它看起來有根據)
      - 高重要性的 fact/inference 有沒有帶證據
      - **會進到信裡的段落有沒有帶得出根據**(第十二輪 P1-3)
      - 立場詞彙是否合法

    ## 第十二輪 P1-3:strict schema 保證形狀,不保證根據

    「有話說就要說得出根據」那一半在 `analysis_grounding`(緣由寫在那裡)。
    這裡只保留「ID 存不存在」與立場詞彙 —— 形狀與根據刻意分成兩個模組。
    """
    problems: list = []
    if not isinstance(obj, dict):
        return ["輸出不是 JSON 物件"]
    known, packet = _registry(evidence_ids)

    def _check_ids(ids, where):
        for i in (ids or []):
            if str(i) not in known:
                problems.append(f"{where} 引用了不存在的證據 ID:{i!r}")

    for i, c in enumerate(obj.get("claim_audit") or []):
        if not isinstance(c, dict):
            problems.append(f"claim_audit[{i}] 不是物件")
            continue
        _check_ids(c.get("evidence_ids"), f"claim_audit[{i}]")
        _check_ids(c.get("counterevidence_ids"), f"claim_audit[{i}] 的反證")
        if (c.get("materiality") == "high"
                and c.get("claim_type") in ("fact", "inference")
                and not (c.get("evidence_ids") or [])):
            problems.append(
                f"claim_audit[{i}] 是高重要性的 {c.get('claim_type')},"
                "卻沒有任何支持證據")
    # **「昨夜三大重點」的條數**(P1-5;判準與判斷都在 `analysis_contracts`)
    problems += _ac.key_driver_count_problems(obj, packet)
    for i, d in enumerate(obj.get("key_drivers") or []):
        if isinstance(d, dict):
            _check_ids(d.get("evidence_ids"), f"key_drivers[{i}]")
            # **反證也要驗**(外審 P1-8):renderer 看到非空的
            # `counterevidence_ids` 就在信裡標「有反面證據」,而先前這裡
            # 只驗支持證據 —— 模型塞一個捏造的 ID,讀者就看到一個
            # 不存在的反面觀點,而那正是「這條判斷有多穩」的訊號。
            _check_ids(d.get("counterevidence_ids"), f"key_drivers[{i}] 的反證")
    # **預期→結果閉環的驗收**(縱深第四批 D)。昨天的觀察點每一條都要
    # 被回顧 —— 缺一條,「逐日追蹤」就是宣稱而不是性質;「已觸發」不引
    # 今天的證據,就只是一句話。只有 packet 知道昨天有哪些觀察點,
    # ID-set 相容路徑驗不了覆蓋(但仍驗證據 ID 的存在)。
    declared_watch = {str(w.get("watch_id") or ""): w
                      for w in ((packet or {}).get("yesterday_watch") or [])
                      if isinstance(w, dict)}
    seen_watch = set()
    for i, w in enumerate(obj.get("watch_review") or []):
        if not isinstance(w, dict):
            problems.append(f"watch_review[{i}] 不是物件")
            continue
        wid = str(w.get("watch_id") or "")
        _check_ids(w.get("evidence_ids"), f"watch_review[{i}]")
        if packet is not None and wid not in declared_watch:
            problems.append(
                f"watch_review[{i}] 回顧了不存在的觀察點:{wid!r}")
            continue
        if wid in seen_watch:
            problems.append(f"watch_review 對 {wid} 回顧了兩次")
        seen_watch.add(wid)
        # **空的 `what_happened` 讓閉環有形無實**(外審 F1):
        # strict schema 只保證欄位在,空字串是合法 JSON —— 而信裡那一行
        # 會只剩「已觸發」三個字。三種狀態都要有內容:已觸發/不再相關
        # 要說今天發生了什麼,未觸發要說還在等什麼。
        if not str(w.get("what_happened") or "").strip():
            problems.append(
                f"watch_review[{i}]({wid})的 `what_happened` 是空的 —— "
                "已觸發要說發生了什麼,未觸發要說還在等什麼")
        if str(w.get("status") or "") in ("triggered", "no_longer_relevant"):
            cited = [str(x) for x in (w.get("evidence_ids") or [])]
            _verdict_zh = ("已觸發"
                           if str(w.get("status")) == "triggered"
                           else "不再相關")
            if not cited:
                # **關閉一條觀察點是今天的事實判斷**(第三十輪外審 P2-1):
                # 「前提已經不存在」與「已經發生」一樣需要今天的證據 ——
                # 少了它,模型可以一句話永久關掉一條還沒驗證的預期。
                # 「還沒觸發」不需要證據(它什麼都沒宣稱),而「過期」
                # 由 Python 判(見 `analysis_recap.carry_watch`)。
                problems.append(
                    f"watch_review[{i}]({wid})說「{_verdict_zh}」"
                    "卻沒有任何今天的證據 ID")
            else:
                # **不同步的資料不得單獨支撐「已觸發」**(外審 F2):
                # 判準與高重要性 claim 同一條(`_unusable`)—— 美股休市日
                # 拿 `market:QQQ.*` 當唯一根據,「今天出現了」根本不是
                # 今天的觀察。引用不禁止,禁止的是**只**靠它。
                _stale_w = _unusable(packet)
                if _stale_w and all(x in _stale_w for x in cited):
                    problems.append(
                        f"watch_review[{i}]({wid})的「{_verdict_zh}」只靠"
                        f"今天不同步的資料({cited[:2]}:"
                        f"{_stale_w[cited[0]]})—— 觸發與否只看今天的證據")
    if packet is not None:
        for wid in sorted(set(declared_watch) - seen_watch):
            problems.append(
                f"昨天的觀察點 {wid} 沒有被回顧(watch_review 要逐條)")
    news = [n for n in (obj.get("top_news_analysis") or []) if isinstance(n, dict)]
    # 第十九輪 P1-6:**集合化把重複吃掉了。** 同一個 `source_item_id`
    # 寫兩段先前完全抓不到 —— 那可以灌高分析則數,甚至對同一個標的
    # 給出互相矛盾的方向。事件群層級的重複已經擋了,而**同一則**重複
    # 反而漏了(它連分群都不需要)。
    from collections import Counter
    for sid, n_times in sorted(Counter(
            str(n.get("source_item_id") or "") for n in news).items()):
        if n_times > 1:
            problems.append(
                f"top_news_analysis 對 {sid!r} 寫了 {n_times} 段 —— "
                "同一則新聞只該有一個分析單位")
    own_ids = {str(n.get("source_item_id") or "") for n in news}
    # **政策段的引用也要真的存在**(外審 2026-08-19):`taiwan_policy` 是
    # v20 新欄位,漏了這一關的話,一個捏造的 `source_item_id` 會讓政策與
    # 它宣稱的影響**看起來有根據地**進信 —— 那正是引用檢查存在的理由。
    # v20/v21 的敘事段落都帶 `source_item_id` —— 捏造的引用會讓內容
    # 「看起來有根據地」進信,一視同仁全部過同一關。
    for field in ("taiwan_policy", "world_events", "taiwan_local"):
        for i, row in enumerate((obj.get(field) or [])):
            if isinstance(row, dict):
                _check_ids([row.get("source_item_id")], f"{field}[{i}]")
    # 情境:**虛構的未來事件要擋得住**(外審 2026-08-19 第二輪)。
    # 每一件都要引用 EVIDENCE 裡真的存在的 ID;引用了不存在的照樣報。
    for i, sc in enumerate((obj.get("upcoming_event_scenarios") or [])):
        if not isinstance(sc, dict):
            continue
        where = f"upcoming_event_scenarios[{i}]"
        _check_ids(sc.get("evidence_ids"), where)
        if str(sc.get("event") or "").strip() and not (sc.get("evidence_ids") or []):
            problems.append(f"{where} 沒有引用任何 EVIDENCE ID —— "
                            "沒有來源的未來事件與編的沒有分別")
    # v22(repo-wide 外審 2026-08-19 P1-B):敘事變化要**綁真的昨日觀點**。
    # prior_view_id 必須是 packet 裡 ANALYSIS_RECAP 真的有的 id(Python 派
    # 的 pv1…),今天的證據也要真的存在 —— 否則「昨日判斷 Fed 已準備大幅
    # 降息 → 強化」可以整條虛構,而欄位名稱讓讀者以為那是系統記得的觀點。
    _pv_ok = {str((it or {}).get("id") or "")
              for it in ((((packet or {}).get("market") or {})
                          .get("ANALYSIS_RECAP") or {}).get("items") or [])
              if isinstance(it, dict)} - {""}
    for i, d in enumerate((obj.get("narrative_delta") or [])):
        if not isinstance(d, dict):
            continue
        where = f"narrative_delta[{i}]"
        if str(d.get("prior_view_id") or "") not in _pv_ok:
            problems.append(f"{where} 的 prior_view_id "
                            f"{d.get('prior_view_id')!r} 不在昨日觀點清單裡 "
                            "—— 昨日觀點不可虛構")
        _check_ids(d.get("evidence_ids"), where)
        if not (d.get("evidence_ids") or []):
            problems.append(f"{where} 沒有引用今天的任何 EVIDENCE ID —— "
                            "沒有新證據就談不上強化或反轉")
    # v22:總經三切面有內容就要有證據(裸字串時代「美伊已達成永久和平
    # 協議」可以不帶任何根據進信)。
    _mac = obj.get("macro_environment") if isinstance(
        obj.get("macro_environment"), dict) else {}
    for _k in ("us_rates_fx_vix", "fed_policy", "geopolitics"):
        _sec = _mac.get(_k) if isinstance(_mac.get(_k), dict) else {}
        where = f"macro_environment.{_k}"
        _check_ids(_sec.get("evidence_ids"), where)
        if (str(_sec.get("analysis") or "").strip()
                and not (_sec.get("evidence_ids") or [])):
            problems.append(f"{where} 有內容卻沒有任何 EVIDENCE ID")
    for i, n in enumerate(news):
        where = f"top_news_analysis[{i}]"
        _check_ids([n.get("source_item_id")], where)
        # v2:因果鏈。**沒有證據的那一步不得自稱 fact** —— 那正是
        # 「看起來有根據」的來源,而它比完全沒有分析更難察覺。
        for j, st in enumerate(n.get("mechanism_steps") or []):
            if not isinstance(st, dict):
                problems.append(f"{where}.mechanism_steps[{j}] 不是物件")
                continue
            _check_ids(st.get("evidence_ids"), f"{where}.mechanism_steps[{j}]")
            if st.get("step_type") == "fact" and not (st.get("evidence_ids") or []):
                problems.append(
                    f"{where}.mechanism_steps[{j}] 自稱 fact 卻沒有證據 ——"
                    "沒有證據的那一步要標成 inference 或 unknown")
            # 第十六輪 P1-7:**空字串的步驟先前算一步。** 驗證器數 dict 個數,
            # 而 renderer 會把空的過濾掉 —— 於是「驗證器說有兩步、讀者看不到
            # 任何因果鏈」。步驟的三個欄位都要有內容才算一步。
            blank = [k for k in ("from_what", "to_what", "channel")
                     if not str(st.get(k) or "").strip()]
            if blank:
                problems.append(
                    f"{where}.mechanism_steps[{j}] 有空欄位 {blank} ——"
                    "空步驟不算一步,寫不出來就不要放這一步")
        # 第十八輪:**「新聞影響股市」是泛論。** 高重要性事件要說得出
        # 對哪個標的、多大、多久 —— 同一件事對台積電與對成熟製程
        # 可以是相反方向,壓成一個「偏多」就是使用者說的數據堆疊。
        seen_assets: set = set()
        for j, a in enumerate(n.get("affected_assets") or []):
            if not isinstance(a, dict):
                problems.append(f"{where}.affected_assets[{j}] 不是物件")
                continue
            aid = str(a.get("asset_id") or "").strip()
            # 第十九輪 P1-9:**`asset_id="市場"` 不是拆標的,是換句話說。**
            # renderer 會把它排得跟真的逐標的分析一模一樣,讓泛論看起來
            # 更像深度分析 —— 比不拆更糟。
            if aid and _is_generic_asset(aid):
                problems.append(
                    f"{where}.affected_assets[{j}] 的標的是泛稱 {aid!r} ——"
                    "要給得出代號、指數或 ETF,給不出就不要列這一項")
            elif aid and never_an_instrument(aid):
                # **兩個問題要分開問**:這個字是不是標的、它與這件事有沒有關。
                # 混在一起的話,`Q2` 會拿到一句「不在這則新聞的標題裡」——
                # 而它就在標題裡,那句話是假的,讀著訊息的人會去修錯的東西。
                problems.append(
                    f"{where}.affected_assets[{j}] 的 {aid!r} 不是可交易標的"
                    " —— 會計期間(`Q2`/`FY25`)、商用縮寫(`CEO`/`EPS`)、"
                    "產品概念(`AI`/`GPU`)在標題出現的頻率極高,"
                    "「出現在證據裡」對它們等於沒有判準")
            elif aid:
                # **ID-set 相容路徑不得跳過標的驗證**(第二十九輪外審
                # 第二輪 F1):先前整段掛在 `packet is not None` 底下,
                # 於是 `validate(obj, ids)` 這條入口連 `ASEAN` 都放行 ——
                # 上一輪關掉的每一條 bypass 在這條路上全部無效。
                # 沒有 packet 時 `_item=None`(沒有證據可看):
                # 指數(豁免相關性)照過,其餘 fail-closed。
                _item = None if packet is None else next(
                    (x for x in (packet.get("news") or [])
                     if str(x.get("source_item_id")) ==
                     str(n.get("source_item_id"))), None)
                if period_word_not_an_entity(aid, _item):
                    # **理由要對得上**:`TTM` 就在標題裡,說它「不在這則
                    # 新聞裡」是假的。它不在的是 `entities`。
                    #
                    # 而**法域撞名的那組要另一句**(外審第二輪):
                    # 對 `EU` 說「是期間」「沒有出現在實體清單」兩句都假
                    # —— 它就在實體清單裡,只是那裡的 `EU` 是歐盟。
                    # 這句話會被原樣送進修補 prompt,說錯了模型就去修錯的
                    # 東西,還可能把唯一一次修補機會用掉。
                    if aid.upper() in _AMBIGUOUS_JURISDICTION:
                        problems.append(
                            f"{where}.affected_assets[{j}] 的 {aid!r} 在這則"
                            "新聞裡指的是**法域**,不是同名的那檔股票 ——"
                            "要談那家公司,請用交易所限定寫法"
                            f"(`NASDAQ: {aid.upper()}`)或它的正式名稱")
                    else:
                        problems.append(
                            f"{where}.affected_assets[{j}] 的 {aid!r} 在這則"
                            "新聞裡是**期間**不是公司 —— 它沒有出現在實體"
                            "清單;真的要談那家公司,它得是新聞裡被點名的主角")
                elif _asset_unknown_to_evidence(aid, _item, packet):
                    # **主角與傳導對象是兩件事**(2026-08-11 生產驗收)。
                    # 這一關本來只認「新聞裡的主角」,於是
                    # 「油價暴漲 → 通膨 → 估值 → 00662 偏空」被判為
                    # 幽靈標的,整份特化分析作廢退回 legacy ——
                    # 而那條傳導鏈正是這份報告要寫的東西。
                    #
                    # 放行的條件是**宣告 + 說得出機制**,不是放寬:
                    #   * 標的要嘛是本報的核心標的(`CORE_ASSETS`,有界),
                    #     要嘛是這一群主體在供應鏈圖上的鄰居
                    #     (`sector_map` 的宣告邊,而那份候選我們自己
                    #     餵給模型過);
                    #   * 而且 `first_order_effect` 要寫得出來 ——
                    #     說不出機制的「受影響」與亂灑沒有分別。
                    # 任意美股仍然要被點名(閘門本體沒有動)。
                    _why = str(a.get("first_order_effect") or "").strip()
                    if not _transmission_ok(aid, _item, packet):
                        problems.append(
                            f"{where}.affected_assets[{j}] 的 {aid!r} 不在這則"
                            "新聞的實體或標題裡,也不是本報核心標的或這一群"
                            "主體在供應鏈上的鄰居 —— 真的要談某檔美股,"
                            "它得是新聞裡的主角")
                    elif len(_why) < _MECHANISM_MIN_CHARS:
                        problems.append(
                            f"{where}.affected_assets[{j}] 的 {aid!r} 不是這則"
                            "新聞的主角,那就要寫得出傳導機制 ——"
                            f"`first_order_effect` 只有 {len(_why)} 個字")
            # **`2330` 與「台積電」是同一個標的**(第二十六輪外審 P1)。
            # 原樣比對讓同一則新聞可以對同一檔給出**兩個相反方向** ——
            # 而衝突偵測會正規化別名,於是那一則同時進了利多側與利空側。
            # 淨效果的「兩側各自接地」判準因此**整段靜默跳過**
            # (兩側的差集都是空的),換標籤假裝權衡照樣過。
            _canon = _ac._canon_asset(aid) if aid else ""
            if _canon and _canon in seen_assets:
                problems.append(
                    f"{where}.affected_assets[{j}] 的 {aid!r} 重複了 ——"
                    "同一個標的只該有一組方向與量級"
                    + ("(別名同組:先前寫過同一檔的另一種寫法)"
                       if _canon != aid else ""))
            seen_assets.add(_canon)
            _check_ids(a.get("evidence_ids"), f"{where}.affected_assets[{j}]")
            if not str(a.get("asset_id") or "").strip():
                problems.append(f"{where}.affected_assets[{j}] 沒有標的代號")
            if not str(a.get("first_order_effect") or "").strip():
                problems.append(
                    f"{where}.affected_assets[{j}] 沒有寫直接影響 ——"
                    "只給方向與幅度等於沒有拆")
        # 第二十輪 P2-7:佐證等級由 packet 決定,**不是模型自評** ——
        # 自評的話「單一來源」永遠不會出現。單一來源/未證實時要說出
        # 讀者該保留什麼;寫「無」等於沒有揭露。
        if packet is not None:
            groups = (packet.get("news_clusters") or {}).get("clusters") or []
            want = ""
            for c in groups:
                if str(n.get("source_item_id") or "") in (
                        c.get("member_source_ids") or ()):
                    want = str(c.get("corroboration") or "")
                    break
            got = str(n.get("corroboration_assessment") or "")
            # **只擋「宣稱得比實際更強」。** 把 single_source 寫成
            # multi_source 是讓讀者高估可信度;反過來(實際多方證實而
            # 保守寫成單一來源)只是更謹慎,擋它沒有保護到任何人。
            rank = {"unverified": 0, "single_source": 1,
                    "multi_source": 2, "official": 3}
            if want and got and rank.get(got, 0) > rank.get(want, 0):
                problems.append(
                    f"{where} 的佐證等級寫 {got!r},而本報算出來只有 {want!r}"
                    " —— 這一格是資料說了算,不得往上寫")
            # 第二十一輪 P2-4:**`want or got` 是布林短路,不是聯集。**
            # want=multi_source、got=single_source 時取到 multi_source,
            # 於是保守降級的那則不要求 caveat —— 而 renderer 仍然把它
            # 印成「僅單一來源」,讀者看到警語卻沒有看到該保留什麼。
            _weak = {"single_source", "unverified"}
            if want in _weak or got in _weak:
                cav = str(n.get("source_caveat") or "").strip()
                if not cav or cav in ("無", "無。", "N/A", "none"):
                    problems.append(
                        f"{where} 是單一來源/未證實,卻沒有寫 source_caveat"
                        " —— 讀者會把一家媒體的說法當成多方證實的事實")
        if n.get("materiality") == "high" and not (n.get("affected_assets") or []):
            problems.append(
                f"{where} 是高重要性事件,卻沒有拆出任何受影響標的")
        # v2:**`unknown` 不是免費的逃生口。** 選它就要說出缺哪些資料,
        # 否則它只是「小幅利多」換一個寫法。
        if (n.get("magnitude_band") == "unknown"
                and not str(n.get("why_this_magnitude") or "").strip()):
            problems.append(
                f"{where} 的量級選了 unknown,卻沒有說缺哪些資料")
        # v2:關係要指向**今天真的存在的另一則**,而且不能指向自己。
        for j, rel in enumerate(n.get("relates_to") or []):
            if not isinstance(rel, dict):
                problems.append(f"{where}.relates_to[{j}] 不是物件")
                continue
            other = str(rel.get("other_source_item_id") or "")
            _check_ids(rel.get("evidence_ids"), f"{where}.relates_to[{j}]")
            if other == str(n.get("source_item_id") or ""):
                problems.append(f"{where}.relates_to[{j}] 指向自己")
            elif other not in own_ids:
                problems.append(
                    f"{where}.relates_to[{j}] 指向 {other!r},"
                    "而本報今天沒有分析那一則 —— 關係不得指向不存在的東西")
        # 連續性:下一步要從上一步的終點接下去。斷開的鏈讀起來像因果,
        # 其實是三個不相干的片段各自成立。
        steps = [st for st in (n.get("mechanism_steps") or [])
                 if isinstance(st, dict)]
        # 主體由**這則新聞**給(見 `_same_node`:主體名不算共用)
        _step_item = next(
            (x for x in ((packet or {}).get("news") or [])
             if str(x.get("source_item_id")) == str(n.get("source_item_id"))),
            None) if packet is not None else None
        _step_subj = [str(e) for e in ((_step_item or {}).get("entities")
                                       or [])]
        for j in range(1, len(steps)):
            prev_to = str(steps[j - 1].get("to_what") or "").strip()
            cur_from = str(steps[j].get("from_what") or "").strip()
            if prev_to and cur_from and not _same_node(prev_to, cur_from,
                                                      _step_subj):
                problems.append(
                    f"{where}.mechanism_steps[{j}] 從 {cur_from!r} 開始,"
                    f"而上一步走到 {prev_to!r} —— 鏈斷了,"
                    "中間缺的那一步要補上(不確定就標 inference)")

    cms = obj.get("cross_market_synthesis")
    if isinstance(cms, dict):
        _check_ids(cms.get("evidence_ids"), "cross_market_synthesis")
    # 第十六輪 P1-2/P2-2:**空的橫向/縱向不得真空通過。**
    # 只有拿得到 packet 才驗得了 —— 這些判準問的是「今天的輸入要求什麼」。
    if packet is not None:
        import signal_tensions as _st
        need = _st.required_tension_ids(packet.get("signal_tensions"))
        # 第十七輪 P1-3:**點名不等於處理。** 改成逐筆檢查結構化的
        # `tension_resolutions` —— 每一筆都要說得出怎麼調和、哪邊可信、
        # 什麼情況分出勝負,而不是丟一串 ID 加一段自由文字。
        res = [r for r in ((cms or {}).get("tension_resolutions") or [])
               if isinstance(r, dict)]
        got = {str(r.get("tension_id") or "") for r in res}
        if need:
            for x in sorted(need - got):
                problems.append(f"訊號張力 {x} 沒有對應的 tension_resolutions 條目")
        for x in sorted(got - need):
            problems.append(
                f"tension_resolutions 宣稱處理了 {x!r},而今天沒有這筆張力"
                "(或它已標為不可用)—— 不得回填不存在的 ID")
        # 第十八輪 P1-6:**重複不算多處理一筆。** `got` 是集合,所以同一筆
        # 填三次仍然滿足 required —— 而指標數的是 `len(res)`,於是
        # 「處理了 3 筆 / 需要 2 筆」這種大於 100% 的覆蓋率。
        seen_tid = set()
        for r in res:
            tid = str(r.get("tension_id") or "")
            if tid in seen_tid:
                problems.append(
                    f"tension_resolutions 有重複的 {tid!r} —— 一筆張力"
                    "只該有一個調和,重複會讓覆蓋率虛胖")
                continue
            seen_tid.add(tid)
            blank = [k for k in ("resolution", "why", "decision_rule")
                     if not str(r.get(k) or "").strip()]
            if blank:
                problems.append(f"tension_resolutions[{tid}] 的 {blank} 是空的"
                                " —— 那等於只點名沒有處理")
            _check_ids(r.get("evidence_ids"), f"tension_resolutions[{tid}]")
            # 第十八輪 P1-5:**引用存在的 ID ≠ 引用相關的 ID。** 拿一則
            # 不相干的新聞去調和「QQQ vs 外資期貨」形式上完全合法 ——
            # 而測試 fixture 自己就在示範那個寫法。要嘛引用該張力本身,
            # 要嘛兩側各引用到至少一個。
            if tid in need:
                import analysis_depth as _ad
                if not _ad.both_sides_cited(r, packet):
                    problems.append(
                        f"tension_resolutions[{tid}] 的證據沒有涵蓋這筆張力"
                        " —— 要引用該張力本身,或兩側各至少一個")
        # 第十七輪 P2-2:**跑不成的檢查要揭露。** stale/unavailable 代表
        # 今天某個橫向面向根本沒查 —— 不寫進 data_gaps,收件人會以為查過了。
        # 第十八輪 P1-8:**逐項對得上,不是「有寫就好」。** 先前只要
        # data_gaps 非空就通過,於是三項橫向檢查全部沒跑成、而模型寫一句
        # 「缺某公司的資本支出金額」就過關 —— 收件人會以為那三項查過了。
        import tension_refs as _tr
        # **need 集合要讀模型看到的那一格**(2026-08-12 CI #502)。
        # `payload_budget` 裁掉區塊時會把 `gap:payload_omitted:<區塊>` 寫進
        # packet["required_disclosures"] 給模型 —— 模型照做揭露,而這裡
        # 先前**重新**從 signal_tensions 推導,不讀那一格:兩個真相來源,
        # 模型聽了其中一個、被另一個判成「回填不存在的缺口」,整份作廢。
        # 平日兩者相等(ep.build 就是拿 required_gap_ids 填的,有測試釘著);
        # 分歧的日子 —— 正是被裁的日子 —— 模型看到的那一份才是契約。
        # 這同時把「被裁掉的區塊必須揭露」接上線:先前只寫在 packet 裡,
        # 沒有任何檢查執行它(沒有呼叫端的宣稱是假的)。
        _rd = packet.get("required_disclosures")
        need_gaps = (dict(_rd) if isinstance(_rd, dict)
                     else _tr.required_gap_ids(packet.get("signal_tensions")))
        told = {str((g or {}).get("gap_id") or "")
                for g in (obj.get("data_gaps") or []) if isinstance(g, dict)}
        for gid in sorted(set(need_gaps) - told):
            problems.append(
                f"{gid} 今天沒有答案({need_gaps[gid]}),data_gaps 沒有揭露它")
        # **`gap:other` 可以帶標籤**(2026-08-12 生產:模型寫
        # `gap:other:cpi_pending` / `gap:other:news_truncation` 被判成
        # 「回填不存在的缺口」,整份特化分析作廢)。標籤比光禿禿的
        # `gap:other` 更有資訊,而且沒有指涉風險 —— gap ID 不會被解參照,
        # 這裡守的是「回填**本報宣告過**的缺口 ID」那種假揭露。
        # `gap:otherX`(沒有冒號)不算:那是另一個名字,不是加標籤。
        for gid in sorted(told - set(need_gaps) - {"gap:other", ""}):
            if gid.startswith("gap:other:"):
                continue
            problems.append(
                f"data_gaps 宣稱 {gid!r},而今天沒有這一項 —— "
                "自己發現的缺口請填 `gap:other`(可加標籤:`gap:other:<標籤>`)")
        # **不同步的資料不得單獨支撐今天的方向判斷。** 談「美股沒開所以
        # 參考性下降」需要引用它,所以不禁止引用 —— 禁止的是**只**靠它。
        stale = _unusable(packet)
        if stale:
            for i, c in enumerate(obj.get("claim_audit") or []):
                if not isinstance(c, dict) or c.get("materiality") != "high":
                    continue
                cited = [str(x) for x in (c.get("evidence_ids") or [])]
                if cited and all(x in stale for x in cited):
                    problems.append(
                        f"claim_audit[{i}] 的證據今天全部不同步"
                        f"({cited[:2]}:{stale[cited[0]]})—— "
                        "高重要性判斷不能只靠不同步的資料")
        hi = [n for n in news if n.get("materiality") == "high"]
        if not news and (packet.get("news") or []):
            problems.append("有新聞可分析,top_news_analysis 卻是空的")
        problems.extend(_coverage_problems(obj, packet, own_ids))
        # 重構規格 Commit C:三大重點要是三個**事件**,不是三個價格變化。
        problems.extend(top_event_problems(obj, packet))
        # Commit D:淨效果、共同驅動、總經發布的聯合情境。
        problems.extend(event_graph_problems(obj, packet))
        # P1-8/P1-9:結構化引用的**指涉**完整性(見 `analysis_contracts`)。
        problems.extend(_ac.reference_problems(obj, packet))
        problems.extend(_alignment_problems(cms, packet, known))
        if hi and not str((cms or {}).get("dominant_driver") or "").strip():
            problems.append(
                "有高重要性事件,cross_market_synthesis 卻沒有指出主導因子")
    # r1(Codex,P1):「要求非空」本身在鼓勵模型隨便填一個 —— 新守衛因此
    # 製造了開頭那句話說的風險:編造的 ID 比沒有 ID 更危險。
    for sec in _gr.EVIDENCE_BEARING:
        node = obj.get(sec)
        if isinstance(node, dict):
            _check_ids(node.get("evidence_ids"), sec)

    # 進信的段落要帶得出根據(`analysis_grounding`)。**空著不算過** ——
    # 迴圈跑不到不等於沒問題,而那正是這條缺陷活下來的方式。
    problems.extend(_gr.problems(obj))
    problems.extend(_claim_graph_problems(obj))

    from analysis_schema import STANCE_LABELS as _labels   # 延遲:避免循環
    label = ((obj.get("stance") or {}) if isinstance(obj.get("stance"), dict)
             else {}).get("label")
    if label is not None and label not in _labels:
        problems.append(f"立場詞彙不合法:{label!r}")
    return problems

# ---------------------------------------------------------------- 相容出口
#
# 深度判準搬到 `analysis_depth`(見該檔:合法性與深度的**後果不同**)。
# 呼叫端仍可從這裡取用,一次只改一件事。
from analysis_depth import (                      # noqa: E402,F401
    depth_advisories, deepen_input, deepen_is_an_improvement)


#: 單數的**證據**引用欄位(既有驗證器對它們做存在性檢查)。
#: r3 外審:`tension_id` 與 `alignment_id` **不在這裡** —— 它們是結構欄位
#: (另一組命名空間,合法性由 `required_tension_ids` 那套自己驗)。
#: 收進來的話,切片修補補上一筆本來就該補的 tension_resolutions,
#: 會被判成「新增了看不到內容的引用」而讓一份正確的修補作廢。
_SINGULAR_EVIDENCE_FIELDS = frozenset({"source_item_id"})


def cited_evidence_ids(obj) -> set:
    """`obj` 裡**所有被引用的證據 ID**(遞迴;鍵名以 `evidence_ids` 結尾)。

    2026-08-22 外審 P1-1 r2:修補輪只附證據切片時,要能分辨「沿用前一版
    的引用」與「新增一個自己這輪看不到內容的引用」。後者才是洗白 ——
    驗證器只驗 ID 存在,於是引用合法、語意不支持的 claim 會過關。

    r2 外審:**單數的證據引用欄位也算**(但只收真的是證據的那些,
    見 `_SINGULAR_EVIDENCE_FIELDS`)。`source_item_id` 在 v20/v21 的
    world_events / taiwan_policy / taiwan_local / 敘事段落都是證據引用
    (既有驗證器對它逐一做存在性檢查,見 `_check_ids([row.get(
    "source_item_id")])`)—— 只收 `*evidence_ids` 清單的話,切片判準看不到
    它們,捏造的單數引用照樣過關。
    """
    out: set = set()

    def _walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                key = str(k)
                if key.endswith("evidence_ids") and isinstance(v, list):
                    out.update(str(i) for i in v if str(i).strip())
                elif key in _SINGULAR_EVIDENCE_FIELDS and isinstance(
                        v, (str, int)):
                    if str(v).strip():
                        out.add(str(v))
                else:
                    _walk(v)
        elif isinstance(x, list):
            for v in x:
                _walk(v)

    _walk(obj)
    return out
