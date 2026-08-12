# -*- coding: utf-8 -*-
"""**EvidencePacket v1** —— provider 中立的證據包(Luna 特化實驗的公平性基礎)。

## 這個模組要解決什麼

十天實驗要比較的是「Luna xhigh + Luna 專用 prompt」對上「DeepSeek V4 Pro max +
既有 prompt」。兩邊的 prompt **刻意不同**(那正是「深度特化」的意思),所以
公平性不可能建立在「同一份 prompt 字串」上 —— 它只能建立在:

    兩邊看到的**證據**完全相同,而且證明得出來。

因此本模組把 `(quotes, fair, predictions, news, tw0050, calibration)` 正規化成
一份確定性的 dict,算出 `evidence_sha`,兩個 profile 都從**同一個 packet** 出發。
某一天兩邊的 sha 不同,那天就不得計入十筆有效樣本 —— 而不是事後才發現不可比。

## 為什麼是「投影」而不是重寫 prompt 組裝

`morning_report._build_prompt` 有 1,355 行。把它拆成結構化欄位再重組,是一次
會動到 DeepSeek 產出的大手術 —— 而使用者明說要保留 DeepSeek 的現有設計,
`tests/test_deepseek_legacy_golden.py` 也已經把它逐位元組凍結。

所以 packet 是同一組輸入的**正規化投影**:
  - `deepseek_legacy_v1` 仍走既有的 `_build_prompt`(輸出逐位元組不變)
  - `luna56_xhigh_v1` 從 packet 組自己的 prompt
兩者的證據同源、sha 同值,而 prompt 各自最佳化。

## 隱私

持股**明細不得進來**。packet 會進 prompt、也會被算 sha 記進 state,
而 state 是 commit 進公開 repo 的。這裡只收「彙總曝險」,不收代號與股數 ——
`portfolio_summary()` 是唯一入口,並由測試盯住。
"""
from __future__ import annotations

from typing import Optional

import evidence_namespaces as _ns
import signal_tensions as _tension
from evidence_serialize import core_evidence_sha  # noqa: F401

#: schema 版本。**改欄位就要進版**:cohort 以它為身分的一部分,
#: 悄悄改欄位等於把不同定義的樣本混進同一個平均。
#: v2(第十五輪 P2-1):加 `signal_tensions` —— 矛盾由 Python 先算好,
#: 模型從「在 97K token 裡找矛盾」變成「解釋矛盾」。形狀變了,不可與 v1 相加。
#: v3(第十六輪):張力改純觀測形狀(left/right/relationship/tension_id/
#: usable_for_inference),registry 改 typed(market:*、tension:*)。
#: v4(第十七輪 P1-1/P1-4):registry 遞迴到巢狀葉節點、廣度張力分
#: 「方向」與「強度」(59.7% 不是方向相反)、關係詞不再帶經濟解釋。
#: v15(第二十二輪 P1-9/P2-3):延續事件的標題比對改 token 邊界
#: (裸子字串讓 `US` 命中 `ASUS`);別名表整批拿掉國家/首都
#: (「伊朗戰事」≠「德黑蘭地震」,只留同一主體的不同寫法);
#: 分群交集吃別名(「台積電」與「TSMC」不再拆成兩群重複計權)。
#: v16(重構規格 Commit B):事件群帶**獨立性** —— `independent_sources`
#: (已驗證的獨立編輯群組,寫進信裡的佐證等級用它)、
#: `potential_independent_sources`(覆蓋率地板用,保守方向相反)、
#: `unverified_sources` / `aggregator_only_sources`(說得出自己驗不了
#: 什麼)。同集團轉載與通訊社稿件不再各算一家。
#: v17(Commit C):packet 帶 `top_events`(多軸計分的三大重點候選、
#: 排除掉的純價格變化、權重宣告)。
#: v18(Commit D):packet 帶 `event_graph` —— 共用底層驅動的事件群
#: (就業→降息預期→殖利率是同一件事的三個表現)、今天的總經發布。
#: v19(Commit E 修生產缺陷):正規化保留 `source_name`,近似去重改用
#: **發布者**當鍵(先前是聚合器別名 `Google:2330` —— 同一個查詢帶回的
#: 三家媒體被判成「同一家改版重發」而砍掉兩則,Commit B 的獨立來源數
#: 在生產因此永遠是 1)。
#: v20(第二十三輪):event_graph 帶 `macro_release_cluster_ids`(全部
#: 的總經發布,不只挑一個);來源註冊表 ASCII 別名改 token 邊界
#: (`ft` 不再命中 SoftBank/Microsoft);未知來源以發布者字串去重。
#: v21(深度優化,橫向):跨語言同事件以數字錨點橋接(`cross_lang` ——
#: CNBC 與經濟日報報的同一筆金額不再是兩個分析單位);聚合器條目的
#: 發布者從標題尾綴解析(`source_registry.title_publisher`,獨立來源數
#: 與 aggregator_only 都會動);分群比對剝發布者尾綴。
#: v22(分析面縱深):事件群帶 `yesterday_view` —— 本報昨天對同一件事
#: 的判斷(`analysis_recap`,同日重跑不自比)。模型先前被要求「延續
#: 事件寫增量」卻沒有 diff 的對象;它只是 diff 基準,不進 registry
#: (拿自己昨天的判斷當今天的證據是循環引用)。
#: v23(外審補審 F3/F4/F6):timeline **記錄整筆帶著走**(先前折成
#: `{entity: days}`,同主體的兩個活躍事件共用最大天數,新事件被標成
#: 延燒第 7 天);`yesterday_view` 的比對加事件層(同公司兩件事不再
#: 互換觀點,分不出來時不給基準);跨語言金額橋接要**事件類別一致**
#: (投資 $10B 與營收 $10B 是兩件事,先前併群後佐證虛增成 multi_source)。
#: v26(縱深第四批 C):事件群多 `transmission_candidates` ——
#: 宣告式供應鏈地圖(`sector_map`)算出的上下游標的;宣告不是證據。
#: v25(縱深第四批 B):事件群多 `origin_view`(首見判斷,由 recap
#: 逐日 carry)—— 與 `yesterday_view` 分開兩個欄位:混成一串的話,
#: `restatements` 拿整串算重疊,正確回顧當初預期會被誤判成重述。
#: v28(2026-08-11):每一筆 `numeric_facts` 帶著自己的 `evidence_id`
#: —— 先前要模型自己組 `fact:<新聞ID>.<序號>`,而序號從 0 起算這件事
#: packet 裡看不出來(2026-08-10 current-head 生產驗收:模型寫 `.1`、
#: 那則的合法 ID 是 `.0`,整份特化分析作廢)。**packet 的形狀變了就要進版。**
#: v24(2026-08-09,縱深第四批):`story_arcs` —— 線索帳本
#: (`story_ledger`,狀態機 + 逐步軌跡)先前**只餵 legacy prompt**,
#: 特化路徑看不到:同一條延燒中的線索,legacy 的信寫得出
#: 「上週 X → 前天 Y → 今天 Z」,特化的信只有「第 N 天」+ 昨天一句。
#: 故事縱深不是沒有,是沒接上。
#: v29(2026-08-11):packet 帶 `unavailable_namespaces` ——
#: 今天一個 ID 都沒有的命名空間,模型不得引用(它會自己發明名字)。
#: v30(2026-08-11):packet 帶 `key_drivers_required` —— 今天要幾條重點
#: 是 Python 算出來的,別讓模型自己數(寫 4 條被擋下整份分析,兩次)。
#: v31(2026-08-12 生產):`ALERTS` 移出 `_NON_EVIDENCE` —— 它是市場觀測
#: (昨日過熱/恐慌訊號)不是管線診斷,payload 給模型看、registry 卻不給
#: ID,claim 引用 `market:ALERTS` 因此整份作廢。其餘區塊維持不可引用
#: (循環引用/假根據,各有測試釘著)。
EVIDENCE_SCHEMA_VERSION = 31

#: 新聞來源等級的排序權重(小的優先)。官方 > A > B > C > 未知。
#: 截斷時依此排序,**不是依抓取順序** —— 抓取順序沒有語意,
#: 而「今天剛好排在後面所以被丟掉」會讓兩天的證據品質不可比。
_GRADE_RANK = {"OFFICIAL": 0, "A": 1, "B": 2, "C": 3}

#: 進 prompt 的新聞上限。超過就依 materiality 截斷,並把被丟掉的**數量與等級**
#: 記進 `truncation` —— 靜默截斷會讓「證據不足」看起來像「模型沒看到」。
MAX_NEWS_ITEMS = 220

#: 每則新聞摘要的字元上限(截斷同樣要記)。
#:
#: r1(Codex,#2):原本是 400,而 legacy prompt 用 **600**、另外還帶最多 1,500
#: 字的 `fulltext`。也就是說 Luna 看到的證據比 DeepSeek **少**,而兩邊卻蓋同一個
#: `evidence_sha` —— 那個 sha 因此是**假的保證**,而整個實驗的公平性建立在它上面。
#: 對齊 legacy 的深度。
MAX_SUMMARY_CHARS = 600

#: 全文上限。與 `morning_report._format_news_block` 的 `with_full` 分支一致。
MAX_FULLTEXT_CHARS = 1500

#: **外部文字的消毒函式**,由呼叫端注入。
#:
#: r1(Codex,#1):`morning_report._external_text` 是前一輪外審立的 P0 控制
#: (「所有 RSS/新聞/事件標題與摘要進 prompt 的唯一入口」)。第一版的 packet
#: 直接複製原始字串,等於**替注入內容開了一條繞過那個控制的旁路** ——
#: 而 strict JSON 只約束輸出形狀,約束不了 prompt 裡的指令。
#:
#: 用注入而不是 import:本模組刻意不相依主模組(它才能單獨測)。
#: 預設是**恆等函式**,但 `build()` 會在沒有拿到消毒器時拒絕組裝 ——
#: 「忘了傳」不得靜默退化成「沒有消毒」。
def _identity(text: str) -> str:
    return text


def sanitize_tree(node, clean):
    """遞迴把消毒器套用到**每一個字串葉節點**,數值型別原樣保留。

    r3(Codex,#1):我 r1 只消毒了 `news` 的五個欄位,而 `market` 區塊裡的
    `GAZETTE_RECORDS`、`STRUCTURED_NEWS_EVENTS`、`EVENT_CALENDAR`、
    `HISTORY` **同樣是抓來的外部文字** ——
    它們被原樣序列化進 payload,公報裡一個偽造的 `</UNTRUSTED_SOURCE_DATA>`
    就能提前關掉圍欄,讓後面的內容被當成指令。legacy 路徑對這些是逐欄呼叫
    `_external_text` 的;我只補了一半。

    **改成整棵樹一次掃完**,而不是繼續維護一份「哪些欄位要消毒」的清單 ——
    那份清單正是這次漏掉的東西,而且每加一個 quotes 鍵就會再漏一次。
    """
    if isinstance(node, str):
        return clean(node)
    if isinstance(node, dict):
        # 鍵也可能來自外部(例如以公司名當鍵),一起消毒。
        return {clean(k) if isinstance(k, str) else k: sanitize_tree(v, clean)
                for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [sanitize_tree(v, clean) for v in node]
    return node


#: 新聞身分住在 `news_ids`(第二十四輪 P1-1 拆出)—— 它是整條管線的共用身分,
#: 不是 packet 階段專屬的東西。此處再匯出,既有 import 路徑不變。
from news_ids import _sid, assign_source_item_ids  # noqa: E402,F401


def _grade(item: dict) -> str:
    if item.get("official"):
        return "OFFICIAL"
    g = str(item.get("source_grade") or "").strip().upper()
    return g if g in _GRADE_RANK else "C"


# ---------------------------------------------------------------- 相容出口
#
# 新聞正規化與截斷搬到 `news_normalize`(見該檔:誰留下來是獨立的決定)。
from news_normalize import (                        # noqa: E402,F401
    normalize_news, _forced_ids)


def portfolio_summary(quotes: dict) -> dict:
    """**只有彙總曝險,沒有代號、沒有股數。**

    packet 會進 prompt、會被算 sha、sha 會進 commit 到公開 repo 的 state。
    持股明細一旦進來就再也拿不回去,所以入口只有這一個,而且刻意不接受
    「順便帶一下代號」的參數。由 `tests/` 盯住。
    """
    actual = (quotes or {}).get("PORTFOLIO_ACTUAL") or {}
    if not isinstance(actual, dict):
        return {"available": False}
    out: dict = {}
    for slot in ("p1", "p2"):
        block = actual.get(slot)
        if not isinstance(block, dict):
            continue
        pct = block.get("gain_pct")
        if not isinstance(pct, (int, float)):
            continue
        # **只放百分比與檔數。** 刻意不放 gain_amount / prev_value /
        # last_value —— 那三個是絕對金額,等於淨值訊號。信件裡顯示金額是
        # 使用者看自己的信;packet 會進 prompt、它的 sha 會進 commit 到
        # 公開 repo 的 state,標準要更嚴。也刻意不放倉位名稱。
        out[slot] = {"change_pct": pct,
                     "holdings": int(block.get("n_holdings") or 0),
                     "priced": int(block.get("n_priced") or 0)}
    return {"available": bool(out), "slots": out}


#: packet 從 `quotes` 取哪些鍵。**明列**,不是 `dict(quotes)` ——
#: quotes 是主流程的萬用袋子,裡面有持股明細、有渲染用的中間物、
#: 也有未來會被加進去的東西。全部倒進 packet 等於讓 evidence_sha 對
#: 「與證據無關的改動」敏感,十天樣本會莫名其妙分裂。
EVIDENCE_QUOTE_KEYS = (
    "QQQ", "TSM", "SPY", "USDTWD", "USDTWD_prev", "MACRO", "MACRO_VINTAGE",
    "EX_DIV_TODAY", "TAIFEX_OI", "TAIFEX_LARGE", "TAIFEX_PCR", "NIGHT_TXF",
    "TAIEX_PRED", "BREADTH", "MARGIN", "FOREIGN_TOP10_TOTAL", "SECTOR_HEAT",
    "MARKET_REGIME", "MA200_STATUS", "ANALYST_MOMENTUM", "SEC_FILINGS",
    "STRUCTURED_NEWS_EVENTS", "EVENT_TIMELINE", "EVENT_CALENDAR",
    "ANALYSIS_RECAP", "STORY_LEDGER",
    "GAZETTE_RECORDS", "POLICY_NEW_KEYWORDS",
    "MODEL_WALK_FORWARD", "MODEL_MONITORING", "MIDTERM", "ABSORPTION",
    "DATA_QUALITY", "SOURCE_HEALTH", "SOURCE_DATA_CHECKS", "HEALTH_WARNINGS",
    "ALERTS", "LAST_TRADING_SESSION", "HISTORY", "STANCE_PY",
    # 第十八輪 P1-8:**新鮮度判準本身也要進 packet。** 先前
    # `signal_tensions` 讀原始 quotes 看得到美股休市,而 registry 只看得到
    # packet —— 於是同一天「張力標成不可用」而「`market:QQQ.change_pct`
    # 標成可用」。兩個真相來源不一致時,下游信哪一個是隨機的。
    "US_HOLIDAY",
)


def build(quotes: dict, fair: dict, predictions: dict, news: Optional[list],
          tw0050: Optional[list], calibration: Optional[dict], *,
          as_of: str = "", target_session_date: str = "",
          trading_session: str = "", sanitize=None) -> dict:
    """組出一份確定性的 EvidencePacket。

    兩個 profile 必須拿到**同一個物件**(或至少同一個 sha)。呼叫端只組一次。
    """
    if sanitize is None:
        # **忘了傳不得靜默退化成「沒有消毒」。** 這是前一輪外審立的 P0 控制,
        # 而它最可能的失效方式就是「新的呼叫端沒有接上」——
        # 那時沒有任何東西會變紅,只有注入內容會靜靜進 prompt。
        raise ValueError("evidence_packet.build 需要 sanitize —— "
                         "外部文字進 prompt 必須經過消毒器")
    kept_news, trunc, cluster_info = normalize_news(news, sanitize)
    packet = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "as_of": str(as_of or ""),
        "target_session_date": str(target_session_date or ""),
        "trading_session": str(trading_session or ""),
        "market": {k: (quotes or {}).get(k) for k in EVIDENCE_QUOTE_KEYS
                   if (quotes or {}).get(k) is not None},
        "valuation_00662": fair or {},
        "predictions_2330": predictions or {},
        "tw_universe": list(tw0050 or []),
        "calibration": calibration or {},
        "news": kept_news,
        "portfolio": portfolio_summary(quotes or {}),
        "truncation": trunc,
        # v2:確定性的訊號張力(矛盾/同向,附數字)。**在消毒之前放進來**
        # —— 產業名與領頭股名來自外部 API,要跟整棵樹一起過消毒器。
        "signal_tensions": _tension.detect(quotes or {}),
    }
    # 第十八輪 P1-8:**模型要知道今天有哪幾項沒有答案。** 不給清單而
    # 要求逐項揭露,等於要它猜驗證器在想什麼 —— 那種規則只會逼出
    # 「什麼都寫一點」的自保式輸出。
    import tension_refs as _tr
    packet["required_disclosures"] = _tr.required_gap_ids(
        packet["signal_tensions"])
    # 第十八輪 P1-3:**分母不能是模型自評的重要性。** 同一件事被四家媒體
    # 報導會產生四個分析單位(`news_analyzed` 看起來變深、實際是同一條鏈
    # 改寫四次);而覆蓋率先前只擋得住「一則都沒分析」。
    # **必分析清單來自完整新聞池**(截斷前),但列出的成員只保留真的
    # 進了 packet 的那些 —— 模型引用不到被截掉的 ID。
    kept_ids = {n["source_item_id"] for n in kept_news}
    # **延續事件要寫增量,不是重述**(深度優化第三批)。EVENT_TIMELINE
    # 已經在數「第 N 天」—— 把它接到事件群上,模型才知道哪些事昨天
    # 已經分析過。信裡的「延燒中事件(第 4 天)」與八段的分析先前
    # 是兩個互不知道對方的系統。
    # **記錄整筆帶著走**(外審補審 F4):先前折成 `{entity: days}`,
    # 同一個 entity 出現兩次時後者覆蓋前者,而同主體的兩個活躍事件
    # 正是 action/object 身分要分辨的東西。
    timeline = [t for t in (packet["market"].get("EVENT_TIMELINE") or [])
                if isinstance(t, dict)]
    by_id = {n["source_item_id"]: n for n in kept_news}

    def _days(c):
        # 判準在 `event_identity.match_days`(與 fetch_plan 的延燒優先
        # 共用 —— 「抓了全文的事件」與「標成第 N 天的事件」必須是同一
        # 個集合)。主體相交**且動作相同**才算同一件事。
        ents = {str(e) for m in c["member_source_ids"]
                for e in (by_id.get(m, {}).get("entities") or [])}
        titles = " ".join(str(by_id.get(m, {}).get("title") or "")
                          for m in c["member_source_ids"])
        import event_identity as _eid
        return _eid.match_days(timeline, ents, titles)
    # **昨日觀點掛在事件群上**(分析面縱深):prompt 要求延續事件寫增量,
    # 而模型先前沒有 diff 的對象。同日重跑的守衛在 `usable`(拿今天比
    # 今天會產生假的強化/推翻);比對身分與 continuing_days 同一套。
    import analysis_recap as _rc
    _recap_items = _rc.usable(packet["market"].get("ANALYSIS_RECAP"),
                              str(packet.get("target_session_date") or ""))

    def _yview(c):
        ents = {str(e) for m in c["member_source_ids"]
                for e in (by_id.get(m, {}).get("entities") or [])}
        # **標題要一起傳**(外審補審 F3):只比實體的話,同一家公司
        # 昨天的兩件事會隨機配一個給今天的群。
        titles = " ".join(str(by_id.get(m, {}).get("title") or "")
                          for m in c["member_source_ids"])
        # 消毒交給最後的 `sanitize_tree` 整樹掃(它是字串葉節點之一)。
        return _rc.view_for(ents, _recap_items, titles=titles)

    def _chain(c):
        # **橫向傳導候選**(縱深第四批 C):事件的主體沿宣告過的供應鏈邊
        # 可以走到誰。宣告不是證據 —— prompt 要求新聞支持那一步才走。
        import sector_map as _sm
        ents = {str(e) for m in c["member_source_ids"]
                for e in (by_id.get(m, {}).get("entities") or [])}
        return _sm.transmission_candidates(ents)

    def _oview(c):
        # **首見與昨日是兩個欄位**:混成一串的話,`restatements` 拿整串
        # 算重疊,模型正確回顧當初預期會被誤判成重述(外審)。
        ents = {str(e) for m in c["member_source_ids"]
                for e in (by_id.get(m, {}).get("entities") or [])}
        titles = " ".join(str(by_id.get(m, {}).get("title") or "")
                          for m in c["member_source_ids"])
        return _rc.origin_view_for(ents, _recap_items, titles=titles)
    _kept_clusters = [dict(c,
                           member_source_ids=[m for m in c["member_source_ids"]
                                              if m in kept_ids],
                           continuing_days=_days(c),
                           yesterday_view=_yview(c),
                           origin_view=_oview(c),
                           transmission_candidates=_chain(c))
                      for c in cluster_info["clusters"]
                      if any(m in kept_ids for m in c["member_source_ids"])]
    packet["news_clusters"] = dict(cluster_info, clusters=_kept_clusters)
    # **「昨夜三大重點」的候選由這裡算出來**(重構規格 Commit C)。
    # 2026-08-05 那封信的第一段寫的是 QQQ 漲 1.2%、台積電 ADR 跌 0.4%
    # —— 那些是價格變化,不是事件。使用者原話:「不是數據文字堆疊」。
    # 候選是多軸計分的結果,而純價格變化整批排除(見 `event_score`)。
    # **多日敘事弧接進特化路徑**(縱深第四批)。`story_ledger` 的狀態機
    # 與逐步軌跡先前只餵 legacy prompt —— 特化的信因此寫不出
    # 「起因→轉折→今天」。選擇與 legacy 同一套(見 `story_arcs`)。
    # **原始帳本不留在 packet**:數百條線索會吃掉 payload 預算,
    # 也讓 evidence_sha 對「與今天無關的舊線索變動」敏感 ——
    # 蒸餾後的 `story_arcs` 才是證據面向的形狀。
    import story_ledger as _sl
    # **新鮮度用產報日,不是目標交易日**(外審 F1):週六產報時
    # `target_session_date` 指到週一,而帳本的 `last_update` 是實際產報日
    # —— 拿目標日比的話,今天才更新的線索全部被標成不新鮮、
    # 被舊線索擠出 12 條上限,而 legacy 用的是台北當日。
    # 兩條路徑的「今天」必須是同一天。
    packet["story_arcs"] = _sl.story_arcs(
        packet["market"].pop("STORY_LEDGER", None) or [],
        today=str(as_of or "")[:10])
    # **預期→結果的閉環**(縱深第四批 D):昨天信裡的觀察點
    # (`watch_triggers`)先前寫完就被遺忘 —— 沒有任何東西隔天回頭問
    # 「觸發了沒」。代號由 Python 派(w1…),schema 的 `watch_review`
    # 逐條回指,validator 驗全覆蓋 —— 漏一條驗證就說話,
    # 「逐日追蹤」才是性質而不是宣稱。
    packet["yesterday_watch"] = _rc.usable_watch(
        packet["market"].get("ANALYSIS_RECAP"),
        str(packet.get("target_session_date") or ""))
    import event_score as _es
    packet["top_events"] = _es.rank(_kept_clusters, kept_news)
    # **事件之間的關係由 Python 先算**(重構規格 Commit D):哪些事件
    # 共用同一個底層驅動(三段各加一次權重 = 同一件事說三次)、
    # 今天有沒有總經發布(那是情境樹的**分岔本身**,不是一件會影響
    # 市場的事)。模型要先知道,才說得出 `double_count_risk`。
    import event_graph as _eg
    packet["event_graph"] = _eg.build(_kept_clusters, kept_news)
    # r3(Codex,#1):**整棵樹消毒。** `market` 區塊裡的公報、結構化事件、
    # 政策情報、歷史全都是外部文字,先前被原樣序列化進 payload。
    # 在算 sha **之前**做 —— 指紋要對應真正送出去的內容。
    packet = sanitize_tree(packet, sanitize)
    # r2(Codex,#2):可比性判準與深度揭露一起放進 packet ——
    # 兩者都必須進實驗帳本,事後才分得出「模型差異」與「餵進去的東西不同」。
    packet["core_sha"] = core_evidence_sha(news, target_session_date)
    packet["coverage"] = coverage(packet, news)
    # **今天一個 ID 都沒有的命名空間**(2026-08-11 生產:模型引用了
    # `derived:tsmc_capex_twd_9503` —— 那個命名空間宣告在 prompt 裡,
    # 而當天一個 ID 都沒有,它只好自己發明一個名字)。
    # 這與 `required_disclosures` 是同一種東西:Python 算出來的當日提示,
    # **放 packet 不放穩定前綴**(前綴要逐位元組相同才打得中快取)。
    # **算在 packet 組完之後** —— 插在中間的話,後面才加進來的區塊
    #(`quality:` 就是)會被誤報成「今天沒有」,而那句話會叫模型
    # 不要引用它真的有的東西。
    # **`fact:` 不豁免**(外審 r1):我上一版把它與新聞當成「骨幹」放行,
    # 而那正好是這個功能要關掉的洞 —— 沒有任何數字事實的日子,模型照樣
    # 會寫 `fact:n3.0`。靜態清單仍然講 `fact:` 是什麼(那是規則),
    # 這一格說的是「今天有沒有」(那是資料),兩者是兩件事。
    # **今天要幾條「昨夜三大重點」**:驗證器要求恰好這個數字(上限 3,
    # 而清淡的日子可以更少 —— 湊一段不會讓分析更深)。判準一直只寫在
    # 驗證器裡:prompt 只在散文提過「三大重點」、schema 一個字都沒說,
    # 而模型要自己去數 `top_events.top_cluster_ids` 才猜得到。
    # 2026-08-11 生產兩次因為寫了 4 條被擋下整份分析 ——
    # 這是「我們沒說的規則拿來駁回」的第五次。**算好的數字直接給。**
    import analysis_contracts as _ac2
    packet["key_drivers_required"] = _ac2.key_drivers_required(packet)
    packet["unavailable_namespaces"] = sorted(
        _ns.unrealizable(evidence_ids(packet)))
    return packet


def coverage(packet: dict, news: Optional[list]) -> dict:
    """這個 packet 涵蓋了來源池的多少 —— **深度差異要被記錄,不是被隱藏**。

    `included / available` 就是「Luna 這一側看到多少」。legacy 那一側的對應
    數字由它自己的 bucket 邏輯決定,兩者不同是預期的;把它記下來,
    十配對的結論才說得出「這是模型差異還是餵進去的東西不同」。
    """
    # 第二十輪 P2-3:**去重成功不該顯示成「涵蓋不足」。** 一家媒體重發
    # 十次同一篇時,packet 正確地只留一篇,而 `included/available` 會
    # 暴跌 —— 讀指標的人會以為證據抓不夠。分母改成**去重後**的可用數,
    # 原始數另外報。
    avail_raw = sum(1 for n in (news or []) if isinstance(n, dict))
    trunc = (packet or {}).get("truncation") or {}
    avail = max(0, avail_raw - int(trunc.get("near_duplicates_dropped") or 0))
    kept = len((packet or {}).get("news") or [])
    full = sum(1 for n in ((packet or {}).get("news") or []) if n.get("fulltext"))
    return {"available": avail, "raw_available": avail_raw,
            "near_duplicates_dropped": int(
                trunc.get("near_duplicates_dropped") or 0),
            "included": kept,
            "with_fulltext": full,
            "rate": round(kept / avail, 3) if avail else None}


def evidence_meta(packet: dict) -> dict:
    """每個 ID 的 `{value, unit, as_of, session, source, quality, 可否推論}`。

    第十八輪 P1-2:先前 registry 只是一串合法字串,回答得了「這個名字
    存不存在」,回答不了「引用的是**今天的**資料嗎」。
    """
    import evidence_registry as _reg
    return _reg.registry(packet)


def evidence_ids(packet: dict) -> set:
    """packet 裡所有可被 claim 回指的證據 ID。

    Luna 的每個重大 claim 都要帶 evidence_ids,而「帶了一個不存在的 ID」
    與「沒帶」是兩種不同的失敗 —— 前者看起來有根據,更危險。

    第十六輪 P1-1:**先前只回新聞 ID,而行情事實沒有合法的引用對象** ——
    模型只能留空(被擋)、或拿新聞 ID 去替行情數字背書(形式合法、語意錯誤;
    測試 fixture 自己就示範了後者)。改成 typed:`n1` / `market:QQQ.change_pct`
    / `tension:t_us_vs_taifex`,而「引用不存在的東西」仍然抓得出來。
    """
    # 第十八輪 P1-1:**改由證據圖推導。** 先前只涵蓋新聞、張力與 market,
    # 而 `valuation_00662` / `predictions_2330` / `calibration` /
    # `tw_universe` / `portfolio` / `coverage` 一個 ID 都沒有 ——
    # 模型要談 00662 估值或模型校準時,只能不引用或拿新聞去頂。
    # **證據圖是唯一的真相來源。** 先前這裡把三個來源聯集起來,
    # 於是「哪些東西引用得到」由三套規則共同決定,而它們互相不知道
    # 對方的存在 —— 幽靈路徑正是從那個縫隙進來的。
    out = set(evidence_meta(packet))
    mkt = market_refs(packet.get("market"))
    # 第十八輪 P1-4:**張力給什麼 ref、這裡就收什麼**,於是
    # `market:MACRO.10Y.change_bps`(packet 裡根本沒有這個 leaf)
    # 靜靜變成合法引用 —— 引用檢查在那一刻只證明「名字合法」,
    # 不再證明「引用了真的存在的資料」。核對責任在這裡:packet
    # 才知道樹長什麼樣。**幽靈路徑不進 registry**(而且說得出是哪些)。
    _ = mkt          # 幽靈路徑的核對在 `phantom_market_refs`
    return out


def unrealizable_namespaces(packet: dict) -> set:
    """宣告在 prompt 裡、registry 卻生不出任何 ID 的命名空間。

    判準與事故記錄在 `evidence_namespaces.unrealizable`(宣告在哪,
    「宣告要能實現」的守衛就在哪)。這裡只負責把 packet 攤成 ID 集合。
    """
    return _ns.unrealizable(evidence_ids(packet))


def phantom_market_refs(packet: dict) -> set:
    """張力宣稱、而 market 樹裡**不存在**的路徑。

    回非空集合代表張力模組與 packet 對不上 —— 那是程式缺陷,不是
    模型的問題。測試盯著它;生產也記進 manifest,否則下次只會再靜默一次。
    """
    return (_tension.market_refs_claimed(packet.get("signal_tensions"))
            - market_refs(packet.get("market")))


#: 遞迴註冊的深度上限。**不是效能考量,是語意的**:巢狀太深的路徑
#: 引用起來沒有意義,而且會讓 registry 膨脹到「什麼都引得到」——
#: 那時引用檢查就失去作用。
#:
#: 訂 5:`SECTOR_HEAT.sectors.<產業>.leaders.<代號>.pct` 正好是第五層,
#: 而它是**真的會被分析的數字**(產業內部分歧就是拿它算的)。
#: 先前訂 3 把它切掉 —— 症狀是張力引用得到、registry 卻沒有,
#: 同一個事實兩個名字(新測試當場抓到)。
#: 防膨脹靠 `_NON_EVIDENCE_BLOCKS`(排除診斷區塊)與測試裡的規模斷言,
#: 不靠這個數字。
_MAX_REF_DEPTH = 5

#: **不註冊**的區塊 —— 本體在 `evidence_registry._NON_EVIDENCE`
#: (單一真相來源;先前兩檔各一份,加 ANALYSIS_RECAP 時只改到一份)。
from evidence_registry import _NON_EVIDENCE as _NON_EVIDENCE_BLOCKS  # noqa: E402


def market_refs(market, prefix: str = "", depth: int = 0) -> set:
    """行情區塊裡**每個有值的葉節點**的 typed ID(遞迴)。

    第十七輪 P1-1:先前只走一層,於是 `market:MACRO.10Y.close`、
    `market:SECTOR_HEAT.sectors.半導體業.median_pct` 這些**真正會被分析的
    數字**沒有合法的引用對象 —— 模型談殖利率或產業內部廣度時只能留空,
    或去引一則新聞替它背書。只有剛好被張力引用到的少數路徑是例外。

    **只註冊葉節點**(引用一整個 dict 說明不了任何事);清單裡的物件用
    **它自己的識別欄位**當路徑而不是索引 —— 索引會隨當日資料量漂移,
    昨天的引用明天會指到別的東西。
    """
    out: set = set()
    if not isinstance(market, dict) or depth > _MAX_REF_DEPTH:
        return out
    for key, val in market.items():
        if depth == 0 and key in _NON_EVIDENCE_BLOCKS:
            continue
        path = f"{prefix}.{key}" if prefix else str(key)
        if depth == 0:
            # **區塊本身也引用得到** —— registry 那側一直有這一格
            # (「談今天沒有這塊資料時需要」的 setdefault),而這裡沒有:
            # 兩邊判準不同步,`market:ALERTS` 在 registry 合法、在 phantom
            # 檢查那側卻是幽靈(2026-08-12)。同一個事實兩個名字,
            # 這個 repo 已經栽過。
            out.add(f"market:{path}")
        if isinstance(val, (int, float, str)) and val != "":
            out.add(f"market:{path}")
        elif isinstance(val, dict):
            out |= market_refs(val, path, depth + 1)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    ident = str(item.get("code") or item.get("id")
                                or item.get("name") or "").strip()
                    if ident:
                        out |= market_refs(item, f"{path}.{ident}", depth + 1)
    return out


# ---------------------------------------------------------------- 相容出口
#
# 序列化與指紋搬到 `evidence_serialize`(它有自己的失效方式,見該檔)。
# 呼叫端仍從這裡取用 —— 一次只改一件事,搬動才證明得了只換位置。
from evidence_serialize import (                  # noqa: E402,F401
    canonical_json, evidence_sha, nonstring_key_paths)
