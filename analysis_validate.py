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
})

#: **產品與技術概念** —— 在新聞標題出現的頻率極高,「出現在證據裡」
#: 對它們永遠成立;而它們永遠不是可交易標的(第二十二輪 P1-6)。
_CONCEPT_TERMS = frozenset({
    "ai", "gpu", "cpu", "chip", "chips", "hbm", "asic", "ml", "llm",
    "cowos", "ev", "5g", "iot", "cloud", "saas", "api", "ar", "vr",
})


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
    if a.lower() in _CONCEPT_TERMS:
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
    if a.upper() in _NOT_TICKER_ABBREV:
        return True
    low = a.lower()
    if a.isascii() and not _TW_CODE.fullmatch(a):
        if any(low == str(e).lower() for e in ents):
            return False
        # **token 邊界,不是裸子字串**(第二十二輪 P1-6):
        # `Ai` 曾因藏在 `Taiwan` 裡被放行。
        if _re.search(r"(?<![A-Za-z0-9])" + _re.escape(low)
                      + r"(?![A-Za-z0-9])", body, _re.IGNORECASE):
            return False
        return True
    if not a.isascii():
        # 中文名稱同樣要在證據裡 —— 上一版對非 ASCII 一律放行,
        # 於是 AMD 新聞可以掛「華碩」當受影響標的。
        if any(a in str(e) or str(e) in a for e in ents) or a in body:
            return False
        import entity_alias as _ea
        if _ea.same(a, _ea.expand(ents)):
            return False
        return True
    if _TW_CODE.fullmatch(a):
        # 台股代號:**要真的在今天的資料裡**。先前任何 4–6 位數都放行,
        # 於是 `999999`、`12345A` 這種不存在的代號冒充逐標的分析。
        if packet is None:
            return False
        known = {str(x.get("code") or "")
                 for x in (packet.get("tw_universe") or []) if isinstance(x, dict)}
        # 「代號存在」不等於「與這則新聞有關」(P1-12):先前 `a in known`
        # 讓 universe 裡的任何一檔都能掛到任何一則新聞上。
        if a in body or any(a in str(e) for e in ents):
            return False
        import entity_alias as _ea
        if _ea.same(a, _ea.expand(ents)):
            return False               # 2317 的新聞實體寫「鴻海」
        # universe 抓不到時不擋(降級不誤擋),但那是**沒驗**不是驗過
        return bool(known)
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
            elif aid and packet is not None:
                _item = next((x for x in (packet.get("news") or [])
                              if str(x.get("source_item_id")) ==
                              str(n.get("source_item_id"))), None)
                if _asset_unknown_to_evidence(aid, _item, packet):
                    problems.append(
                        f"{where}.affected_assets[{j}] 的 {aid!r} 不在這則"
                        "新聞的實體或標題裡 —— 「GPU」「AI」是概念不是標的;"
                        "真的要談某檔美股,它得是新聞裡的主角")
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
        for j in range(1, len(steps)):
            prev_to = str(steps[j - 1].get("to_what") or "").strip()
            cur_from = str(steps[j].get("from_what") or "").strip()
            if prev_to and cur_from and prev_to != cur_from:
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
        need_gaps = _tr.required_gap_ids(packet.get("signal_tensions"))
        told = {str((g or {}).get("gap_id") or "")
                for g in (obj.get("data_gaps") or []) if isinstance(g, dict)}
        for gid in sorted(set(need_gaps) - told):
            problems.append(
                f"{gid} 今天沒有答案({need_gaps[gid]}),data_gaps 沒有揭露它")
        for gid in sorted(told - set(need_gaps) - {"gap:other", ""}):
            problems.append(
                f"data_gaps 宣稱 {gid!r},而今天沒有這一項 —— "
                "自己發現的缺口請填 `gap:other`")
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
