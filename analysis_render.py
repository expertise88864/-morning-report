# -*- coding: utf-8 -*-
"""**Luna 的 strict JSON → 晨報 Markdown**(確定性 renderer)。

## 為什麼需要它

主分析的介面契約是「回一段 Markdown」,而下游有一整套既有的後處理靠**段落
標題**辨識內容(`_extract_stance` 找「我的明確立場」、`_extract_summary` 找
「一句話總結」、`_strip_llm_sections` 移除已上移到結論卡的段落)。

Luna 走 strict Structured Outputs,拿到的是 JSON。**模型不直接控制信件排版**
—— 那是刻意的:排版由程式決定,模型只負責判斷內容。這個模組就是那一刀。

## 驗收條件不是「看起來像」

`morning_report._analysis_complete_enough` 是既有的截斷偵測器:少了立場或
總結、或立場解析不出來,頂部 KPI 與結論卡會變成「—」。所以本模組的測試
直接拿那個函式當斷言 —— 那才是「這份 Markdown 真的能用」的定義,
而不是我自己覺得格式對。

## 段落標題必須沿用既有詞彙

`八、科技板塊脈動` / `九、其他類股資訊` / `七之二、世界大事速覽` 等字串在
主模組裡是常數,後處理靠它們切段。自創標題不會有錯誤訊息,只會讓那些段落
在信裡消失。
"""
from __future__ import annotations

from typing import Optional

from analysis_contracts import top_drivers as _top   # 條數與驗證器同源
from analysis_render_depth import _news_line, _synthesis

RENDER_SCHEMA_VERSION = 1

#: 這些標題**必須與主模組的常數一致**。改一個字,對應段落就會在信裡消失
#: 而且沒有任何錯誤 —— 由測試比對主模組的 `_SECTION_*`。
SECTION_TOP3 = "七、昨夜三大重點"
#: Commit E:逐標的淨效果。**新段落**,不與既有標題衝突 ——
#: 既有的 `_strip_llm_sections` 只移除它認得的那幾個,新段落原樣留下。
SECTION_NET = "九之一、各標的合計影響"
#: 第十五輪 P1-3:**段落名要說實話。**
#:
#: 舊的映射把 `global_market`(美股→台股連動)放進「世界大事速覽」——
#: 那一段在 legacy 契約裡的定義是**股市之外的世界**;把整個
#: `top_news_analysis` 無條件放進「科技板塊脈動」,即使那則新聞是金融、
#: 航運或生技;把 `taiwan_market.taiex_view` / `tsmc_view` 放進
#: 「**其他**類股資訊」——台積電是最不「其他」的那一檔。
#:
#: 這不是排版問題。Luna 特化路徑跑成的那一天,收件人會看到
#: 「世界大事速覽」裡寫美股連動、「其他類股資訊」裡只有台積電 ——
#: 而信看起來仍然完整,沒有任何錯誤訊息。
#: Luna 的 schema **沒有**「股市之外的世界」這種欄位,所以正確的做法是
#: **不要宣稱有**,而不是找一個欄位塞進去。
SECTION_GLOBAL = "七之二、全球市場與美股台股連動"
SECTION_NEWS = "八、重點新聞分析"
SECTION_TW = "九、台股與台積電"
SECTION_PRICED = "已被市場反映 vs 尚未反映"
#: schema v2:橫向綜合。**排在最前面** —— 使用者要的是「這些訊號合起來
#: 說什麼」,逐條看完再自己拼是他反映了三次的那個問題。
SECTION_SYNTHESIS = "七之一、今日訊號的橫向綜合"
SECTION_STANCE = "我的明確立場"
SECTION_SUMMARY = "一句話總結"


def _s(v) -> str:
    return str(v or "").strip()


def _lines(items, fmt) -> list:
    out = []
    for it in (items or []):
        if isinstance(it, dict):
            text = fmt(it)
            if text:
                out.append(f"- {text}")
    return out


def _claim_line(c: dict) -> str:
    """一條 claim 的行文。

    **2026-08-17 使用者定案:敘事為主。** 特化路徑第一次在生產成功那天,
    使用者的回饋是「敘述方式變成這樣,原本的還比較好」—— 信裡長出了
    「(推論、信心 60%、1-5d、有反面證據)」這種括號,讀起來像表單。
    保留的是**判斷本身**與**什麼情況代表它錯了**(那兩項舊版沒有);
    型別、信心百分比、時間窗、反面證據旗標都收起來 —— 它們仍在 schema
    裡被驗證,只是不再排進讀者的視線。
    """
    body = _s(c.get("statement"))
    if not body:
        return ""
    line = body
    # **失效條件先前也被丟掉。** schema 把它列為必填,理由寫在測試裡:
    # 「說不出什麼情況我就錯了的判斷,事後無法評分」。既然要求了就要顯示,
    # 否則那個必填只保護了 JSON,沒有保護讀者。
    trigger = _s(c.get("falsification_trigger"))
    return line + (f"\n  - 什麼情況代表這個判斷錯了:{trigger}" if trigger else "")


def _cluster_of(packet, cluster_id: str) -> dict:
    for c in (((packet or {}).get("news_clusters") or {}).get("clusters") or []):
        if isinstance(c, dict) and _s(c.get("cluster_id")) == cluster_id:
            return c
    return {}


def _event_card(c: dict, packet=None) -> str:
    """一條「昨夜三大重點」。**判斷 + 這件事的來歷。**

    先前只有判斷。而「三家獨立媒體證實」與「僅一家、未經證實」在信裡
    長得一模一樣 —— 可信度是分析的一部分,不是附註。
    """
    line = _claim_line(c)
    if not line:
        return ""
    cid = _s(c.get("cluster_id"))
    # **反面證據不依賴 cluster 查得到**:先前兩個早退(沒有 cluster_id、
    # 查不到那一群)會把整個括號跳過,而反面證據是 claim 自己帶的欄位。
    blk = _cluster_of(packet, cid) if cid else {}
    bits = []
    if blk.get("official"):
        bits.append("官方公告")
    else:
        n = blk.get("independent_sources")
        if isinstance(n, int) and n >= 2:
            bits.append(f"{n} 個獨立來源")
        elif isinstance(n, int):
            # **說得出自己驗不了什麼。** 三種情況三種話(第二十三輪
            # P2-4):「僅單一來源」是查證過只有一家;「未驗證」是
            # 發布者不在註冊表;「原始發布者未解析」是**我們自己**只
            # 解析到聚合器 —— 第三種是抓取缺口,不是事件可信度。
            un = blk.get("unverified_sources")
            agg = blk.get("aggregator_only_sources")
            if un:
                bits.append(f"來源 {un} 家未驗證")
            elif agg:
                bits.append("原始發布者未解析")
            else:
                bits.append("僅單一來源")
    days = blk.get("continuing_days")
    if isinstance(days, int) and days >= 1:
        bits.append(f"連續追蹤第 {days + 1} 天")
    # 第十五輪 P1-2 的理由不變:**有反面證據要看得見** —— 一條「有人持
    # 相反看法」的判斷,不能與一面倒的判斷長得一樣。2026-08-17 把它從
    # 機械括號(推論、信心 60%…)搬到這個**來歷**括號:留下來的都是
    # 誠實性訊號(幾個來源、有沒有反面證據、追了幾天),機械欄位收起來。
    if [x for x in (c.get("counterevidence_ids") or []) if _s(x)]:
        bits.append("有反面證據")
    # **來歷收成句末的一個小括號**(2026-08-17 使用者定案):獨立一行的
    # 「這件事的來歷:…」讀起來像表單。可信度不刪 —— 「僅單一來源」與
    # 「三家證實」在信裡仍然長得不一樣,只是不再各佔一行。
    # 括號要接在**判斷那一句**後面,不是接在失效條件那一行後面
    # (`_claim_line` 回的是「判斷 \n 失效條件」兩行)。
    if not bits:
        return line
    head, _, rest = line.partition("\n")
    return head + f"（{'、'.join(bits)}）" + (f"\n{rest}" if rest else "")


def _net_effects(rows) -> str:
    """**逐標的的淨效果。** 使用者的第六條回饋逐字是:
    「對整體經濟/對 2330/對 0050/**利多還是利空**」。

    schema 收了、驗證器擋了,而先前渲染層一個字都沒印 —— 那個必填
    只保護了 JSON,沒有保護讀者(與 `falsification_trigger` 同一個形狀)。
    """
    out = []
    word = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性",
            "unknown": "判斷不出來"}
    band = {"negligible": "可忽略", "small": "小", "moderate": "中等",
            "large": "大", "unknown": "量級不明"}
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        aid = _s(r.get("asset_id"))
        if not aid:
            continue
        d = word.get(_s(r.get("net_direction")), _s(r.get("net_direction")))
        m = band.get(_s(r.get("net_magnitude_band")), "")
        head = f"- **{aid}**:合計{d}" + (f"、幅度{m}" if m else "")
        why = _s(r.get("why"))
        out.append(head + (f"\n  - 為什麼是這個方向:{why}" if why else ""))
    return "\n".join(out)


def render(obj: Optional[dict], packet=None, admitted_watch=None) -> str:
    """把驗證過的分析 JSON 轉成晨報 Markdown。

    **無法渲染時回空字串**,不回半份。呼叫端會據此走既有的降級路徑 ——
    回半份的症狀是「信寄出去了但少了一半」,那比沒寄更難發現。

    `admitted_watch`(可選)是**帳本真的收下**的觀察點 trigger 集合
    (`analysis_recap.admitted_triggers`)。給了就據此標記:沒被收下的
    那條會寫成「一次性觀察,未納入持續追蹤」—— 先前一律印成持續觀察,
    而帳本滿的時候它明天就不存在(外審 2026-08-17 P2-1)。
    **不給就不標**:沒有那個資訊時保持原樣,不假裝知道。
    """
    if not isinstance(obj, dict):
        return ""
    stance = obj.get("stance") if isinstance(obj.get("stance"), dict) else {}
    label = _s(stance.get("label"))
    summary = _s(obj.get("executive_summary"))
    if not label or not summary:
        # 這兩個是既有後處理的硬需求(缺了頂部 KPI 會變「—」)。
        return ""

    parts: list = []

    # 七、昨夜三大重點 —— **事件卡**(重構規格 Commit E)。使用者原話:
    # 「我要的是真正國際上昨夜三大發生得重大事件」。每一條除了判斷本身,
    # 還要看得出**這件事有多可信、是第幾天** —— 那兩件事 packet 早就算好了
    # (獨立編輯台數、連續追蹤天數),而先前一個字都沒有進信。
    drivers = [c for c in (obj.get("key_drivers") or []) if isinstance(c, dict)]
    # 第二十三輪 P1-6:**排序依 Python 的事件計分,不依模型自評的
    # materiality** —— 自評的重要性不能當判準(repo 既有原則)。
    _rank = {cid: i for i, cid in enumerate(
        ((packet or {}).get("top_events") or {}).get("top_cluster_ids") or [])}
    order = {"high": 0, "medium": 1, "low": 2}
    drivers.sort(key=lambda c: (_rank.get(_s(c.get("cluster_id")), 99),
                                order.get(_s(c.get("materiality")), 3)))
    top3 = [x for x in (_event_card(c, packet) for c in _top(drivers, packet)) if x]
    if top3:
        parts.append(f"## {SECTION_TOP3}\n" + "\n".join(top3))

    # 橫向綜合**排在最前面**:使用者要的是「今天這些訊號合起來說什麼」,
    # 而不是逐條看完再自己拼。
    syn = _synthesis(obj.get("cross_market_synthesis"), packet)
    if syn:
        parts.append(f"## {SECTION_SYNTHESIS}\n" + syn)

    gm = obj.get("global_market") if isinstance(obj.get("global_market"), dict) else {}
    glob = [x for x in (_s(gm.get("summary")), _s(gm.get("us_to_tw_linkage"))) if x]
    if glob:
        parts.append(f"## {SECTION_GLOBAL}\n" + "\n".join(f"- {w}" for w in glob))

    news = _lines(obj.get("top_news_analysis"),
                  lambda n: _news_line(n, packet))
    if news:
        # 第十八輪 P1-9:走到財務層是 advisory,加深失敗就照原樣寄出 ——
        # 那對「晨報不可斷」是合理的,對收件人卻是隱瞞:他不知道這條
        # 標成「重大」的事件其實只推到情緒。**不擋,但要說出來。**
        import analysis_stages as _ast
        stub = _ast.incomplete_chains(obj)
        if stub:
            # 第十九輪 P2-2:先前只印前三則,**其餘靜默消失** ——
            # 八則裡有五則停在情緒時,讀者只看到三則,而「還有幾則」
            # 正是他判斷這封信可不可信的關鍵。
            news.append("- *傳導未完成:" + "、".join(
                f"{sid} {why}" for sid, why in stub[:3])
                + (f";另有 {len(stub) - 3} 則同樣未完成"
                   if len(stub) > 3 else "")
                + " —— 這幾則的影響幅度本報無法確認。*")
        # 第十九輪 P1-5:**看過而決定不談,讀者有權知道。**
        # 不顯示的話,「沒發生」與「發生了但本報判斷不重要」長得一樣。
        skipped = [d for d in (obj.get("dismissed_events") or [])
                   if isinstance(d, dict) and _s(d.get("why_not_material"))]
        if skipped:
            news.append("- *今日看過但未展開:" + "、".join(
                f"{_s(d.get('cluster_id'))}({_s(d.get('why_not_material'))})"
                for d in skipped[:4])
                + (f";另有 {len(skipped) - 4} 件" if len(skipped) > 4 else "")
                + "*")
        parts.append(f"## {SECTION_NEWS}\n" + "\n".join(news))

    # 台股與台積電。`summary` 是台股整體、兩個 view 是細部,**同一段**裡
    # 由粗到細 —— 先前 summary 被丟進「台灣本地動態」(那一段講的是
    # 證交所新制、勞動基金這類在地消息),兩者不是同一件事。
    tw = obj.get("taiwan_market") if isinstance(obj.get("taiwan_market"), dict) else {}
    tw_lines = [x for x in (_s(tw.get("summary")), _s(tw.get("taiex_view")),
                            _s(tw.get("tsmc_view"))) if x]
    if tw_lines:
        parts.append(f"## {SECTION_TW}\n" + "\n".join(f"- {o}" for o in tw_lines))

    # **逐標的淨效果**(Commit E):同一個標的被不同事件推往相反方向時,
    # 前面幾段各自寫完就結束了 —— 這一段回答「合起來是利多還是利空」。
    nets = _net_effects(obj.get("asset_net_effects"))
    if nets:
        parts.append(f"## {SECTION_NET}\n" + nets)

    # **`priced_in` 先前整段沒有被渲染。** 它是這份 schema 裡最像分析的欄位
    # (「哪些已經在價格裡、哪些還沒」),模型產出了、驗證器檢查了,
    # 而收件人從來沒看到 —— 這正好是使用者反映「只有數據沒有分析」的一部分。
    pi = obj.get("priced_in") if isinstance(obj.get("priced_in"), dict) else {}
    done = [_s(x) for x in (pi.get("already_reflected") or []) if _s(x)]
    todo = [_s(x) for x in (pi.get("not_yet_reflected") or []) if _s(x)]
    if done or todo:
        blk = []
        if done:
            blk.append("- **已被市場反映**:" + "、".join(done[:4]))
        if todo:
            blk.append("- **尚未反映**:" + "、".join(todo[:4]))
        parts.append(f"## {SECTION_PRICED}\n" + "\n".join(blk))

    # 情境樹與觀察點:這是 Luna 特化相對於既有散文的實質增量
    tree = obj.get("scenario_tree") if isinstance(obj.get("scenario_tree"), dict) else {}
    scen = []
    for key, name in (("base", "基準"), ("bull", "偏多"), ("bear", "偏空")):
        blk = tree.get(key)
        if isinstance(blk, dict) and _s(blk.get("narrative")):
            # r2(Codex,#5):**數字完全不進信件。** 標明「模型主觀機率」
            # 仍不滿足這個 repo 的不變式 —— 信裡出現的數字必須是 Python 算的,
            # 而情境機率沒有任何 Python 來源。它留在 JSON 裡供指標與事後判讀,
            # 但收件人看到的只有情境敘述本身。
            # 第十九輪 P2-1:**觸發條件先前整個被丟掉。** 機率不進信是
            # 既有不變式(信裡的數字必須是 Python 算的,而情境機率沒有
            # 任何 Python 來源)—— 但 `triggers` 不是數字,它是可觀察的
            # 條件,而「什麼情況會讓這個情境成立」正是情境樹的用處。
            # 只印敘述等於把三段散文並排,讀者無從判斷該盯什麼。
            trig = [_s(x) for x in (blk.get("triggers") or []) if _s(x)]
            line = f"- **{name}**:{_s(blk.get('narrative'))}"
            if trig:
                line += "\n  - 什麼情況代表它成立:" + "、".join(trig[:3])
            scen.append(line)
    if scen:
        parts.append("## 情境與觸發條件\n" + "\n".join(scen))

    gaps = _lines(obj.get("data_gaps"),
                  lambda g: f"{_s(g.get('what_is_missing'))}"
                            f"{'——' + _s(g.get('impact_on_conclusions')) if _s(g.get('impact_on_conclusions')) else ''}")
    if gaps:
        # **資料缺口要出現在信裡。** 只記在 manifest 等於沒有揭露:
        # 收件人看到的是一份看起來完整的報告。
        parts.append("## 資料缺口\n" + "\n".join(gaps))

    contra = _lines(obj.get("contradictions"),
                    lambda c: f"{_s(c.get('topic'))}:{_s(c.get('resolution'))}")
    if contra:
        parts.append("## 證據衝突與調和\n" + "\n".join(contra))

    # 縱深第四批 D:昨天的觀察點回顧放在今天的新觀察點**之前** ——
    # 敘事順序是「昨天預期 → 今天結果 → 明天再盯什麼」。
    # trigger 原文從 packet 查(模型只回代號;沒有 packet 就印代號 ——
    # 那是相容路徑,信仍然完整,只是少了原文)。
    wr = [w for w in (obj.get("watch_review") or []) if isinstance(w, dict)]
    if wr:
        _wid_text = {str(w.get("watch_id") or ""): _s(w.get("trigger"))
                     for w in ((packet or {}).get("yesterday_watch") or [])
                     if isinstance(w, dict)}
        _WR_ZH = {"triggered": "已觸發", "not_triggered": "未觸發",
                  "no_longer_relevant": "不再相關"}
        _wr_lines = []
        for w in wr:
            wid = str(w.get("watch_id") or "")
            label = _wid_text.get(wid) or wid
            status = _WR_ZH.get(str(w.get("status") or ""), "?")
            what = _s(w.get("what_happened"))
            _wr_lines.append(f"{label}：{status}"
                             + (f"（{what}）" if what else ""))
        if _wr_lines:
            parts.append("## 昨日觀察點回顧\n" + "\n".join(_wr_lines))

    def _watch_line(w) -> str:
        _t = _s(w.get("trigger"))
        # **比對用帳本的身分鍵**(外審 2026-08-17 r2):身分是**完整文字**
        # 的雜湊,而顯示是截到 120 字 —— 兩者分開之後,前 120 字相同但
        # 結論相反的兩條(「跌破 1100」vs「突破 1200」)才不會被當成同一條。
        # 判準只有一份,渲染端不自己算一套。
        try:
            from analysis_recap import trigger_key as _tkey
        except Exception:            # noqa: BLE001 - 載不到就退回原文比對
            def _tkey(x):
                return str(x or "").strip()
        _key = _tkey(_t)
        _why = _s(w.get("why"))
        # **沒被帳本收下的不得寫成持續追蹤**:那是一個明天不會被兌現的
        # 承諾。標出來而不是隱藏 —— 模型提的內容仍然有參考價值。
        _tail = ""
        if admitted_watch is not None and _key and _key not in admitted_watch:
            _tail = "（一次性觀察,未納入持續追蹤）"
        return _t + (f"（{_why}）" if _why else "") + _tail

    watch = _lines(obj.get("watch_triggers"), _watch_line)
    if watch:
        parts.append("## 觀察觸發點\n" + "\n".join(watch))

    # 我的明確立場 —— 格式必須讓 `_extract_stance` 解析得出來
    stance_lines = [f"立場：{label}"]
    score = stance.get("score")
    if isinstance(score, int):
        stance_lines.append(f"淨分 {score:+d}")
    if _s(stance.get("rationale")):
        stance_lines.append(_s(stance.get("rationale")))
    pf = (obj.get("portfolio_implications")
          if isinstance(obj.get("portfolio_implications"), dict) else {})
    if _s(pf.get("summary")):
        stance_lines.append(_s(pf.get("summary")))
    # `actions_to_consider` 先前沒有被渲染 —— 模型寫了、驗證了、沒人看得到。
    for a in (pf.get("actions_to_consider") or [])[:3]:
        if _s(a):
            stance_lines.append(f"可考慮的做法:{_s(a)}")
    for r in (pf.get("risks") or [])[:3]:
        if _s(r):
            stance_lines.append(f"風險:{_s(r)}")
    inval = [_s(t) for t in (tree.get("invalidation_triggers") or []) if _s(t)]
    if inval:
        stance_lines.append("失效條件:" + "、".join(inval[:3]))
    parts.append(f"## {SECTION_STANCE}\n" + "\n".join(stance_lines))

    parts.append(f"## {SECTION_SUMMARY}\n{summary}")
    return "\n\n".join(parts)
