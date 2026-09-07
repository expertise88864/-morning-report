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
import reader_prose as _reader

from analysis_contracts import top_drivers as _top   # 條數與驗證器同源
from analysis_render_depth import _news_line

RENDER_SCHEMA_VERSION = 1

#: 這些標題**必須與主模組的常數一致**。改一個字,對應段落就會在信裡消失
#: 而且沒有任何錯誤 —— 由測試比對主模組的 `_SECTION_*`。
SECTION_TOP3 = "七、昨夜三大重點"
#: Commit E:逐標的淨效果。**新段落**,不與既有標題衝突 ——
#: 既有的 `_strip_llm_sections` 只移除它認得的那幾個,新段落原樣留下。
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
#: 2026-08-19 第四批(使用者貼了幾個禮拜前的完整實信要求照做):
#: 科技/其他從「八、重點新聞分析」的子段**升回獨立段落** ——
#: legacy 信就是這兩個 h2。過濾仍是真的(產業別/registry 宣告)。
SECTION_TECH = "八、科技板塊脈動"
SECTION_OTHER = "九、其他類股資訊"
#: 本段的保留事項(傳導未完成 / 看過但未展開)—— 跟在第九段後面。
SUBSECTION_NOTES = "本段的保留事項"
#: legacy 信的骨架(2026-08-19 第四批,schema v21 各有對應欄位):
SECTION_WORLD = "七之二、世界大事速覽"
SECTION_48H = "七之三、未來 48 小時關鍵事件情境"
SECTION_DELTA = "七之四、敘事變化(昨日觀點 vs 今日新證據)"
SECTION_MACRO = "十、總體經濟與政策環境"
#: 政策深度解析從 v20 的「九、台灣政策與在地動態」改編號(內容同一個欄位)。
SECTION_POLICY = "十之二、重大政策深度解析"
SECTION_LOCAL = "十一、台灣本地動態"
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


def _headline(ref: str, packet=None) -> str:
    """把內部識別碼換成**讀者看得懂的那件事**(2026-08-18)。

    信裡出現過 `nfb2726dbc24`、`cluster:n562eeb06cc9` 這種字串 ——
    使用者的原話是「為何一堆亂碼」。它們是 packet 的 `source_item_id`
    與群 ID,對系統有意義、對讀者沒有。這裡回查那則新聞的標題;
    `cluster:` 前綴的群回查群裡任一成員的標題。

    **查不到就回空字串**,呼叫端據此只留計數 —— 印一串識別碼比不印糟:
    它看起來像出錯,而它其實只是沒被翻譯。
    """
    ref = _s(ref)
    if not ref or not isinstance(packet, dict):
        return ""
    by_id = {_s(x.get("source_item_id")): _s(x.get("title"))
             for x in (packet.get("news") or []) if isinstance(x, dict)}
    if ref in by_id:
        return by_id[ref]
    cid = ref.split(":", 1)[1] if ref.startswith("cluster:") else ref
    if cid in by_id:
        return by_id[cid]
    for c in ((packet.get("news_clusters") or {}).get("clusters") or []):
        if not isinstance(c, dict) or _s(c.get("cluster_id")) != cid:
            continue
        for m in (c.get("member_source_ids") or []):
            if by_id.get(_s(m)):
                return by_id[_s(m)]
    return ""


def _first_sentence(text: str, limit: int = 60) -> str:
    """理由收成一句話。整段理由是寫給驗證器看的,排進信裡就是一面牆。"""
    t = _s(text)
    for i, ch in enumerate(t):
        if ch in "。！？!?":
            return t[:i + 1]
        if i >= limit:
            return t[:limit] + "…"
    return t


def _blocks(items, fmt) -> list:
    """一則新聞 = **一個段落區塊**(小標題 / 敘述 / 傳導 / 什麼會推翻它)。

    與 `_lines` 的差別只有一個:不在整塊前面加 `- `。2026-08-18 使用者要回
    舊版寫法 —— 舊版的小標題是「公司(代號,簡介):昨天發生什麼事」這樣的
    **粗體行**,不是清單項目;整塊掛在一個 bullet 底下會讓小標題也長出
    一顆點,而底下的「傳導 / 什麼會推翻它」才是真正的清單。
    """
    out = []
    for it in (items or []):
        if isinstance(it, dict):
            text = fmt(it)
            if text:
                out.append(text)
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
    # **失效條件收起來**(2026-08-19 使用者:「我只要三大消息重點即可」)。
    # 它仍在 schema 裡被要求與驗證 —— 「說不出什麼情況我就錯了的判斷,
    # 事後無法評分」那個理由沒有變,變的是它評分用、不進讀者的視線。
    # 第八段的逐則分析裡「若…,此判斷不成立」仍在,失效條件沒有從信裡
    # 整個消失。
    return body


def _cluster_of(packet, cluster_id: str) -> dict:
    for c in (((packet or {}).get("news_clusters") or {}).get("clusters") or []):
        if isinstance(c, dict) and _s(c.get("cluster_id")) == cluster_id:
            return c
    return {}


def _derived_target(packet, cluster_id: str) -> str:
    """這個事件群最相關的持倉標的(**確定性**,由編輯標註實體推導)。

    判準宣告在 `company_profiles`(NDX 名單)—— 兩份會漂,所以不抄。
    """
    if not cluster_id or not isinstance(packet, dict):
        return ""
    blk = _cluster_of(packet, cluster_id)
    members = set(blk.get("member_source_ids") or ())
    if not members:
        return ""
    try:
        import company_profiles as _cp
        ndx = set(_cp.NASDAQ_TOP15_LABELS)
    except Exception:                   # noqa: BLE001 - 名單載不到就不掛
        return ""
    ents: set = set()
    for n in (packet.get("news") or []):
        if isinstance(n, dict) and _s(n.get("source_item_id")) in members:
            ents |= {_s(e) for e in (n.get("entities") or []) if _s(e)}
    # 編輯標註同一家公司會用不同寫法(台積電/TSMC/2330/台積、NVDA/輝達),
    # 逐字比對會漏 —— 先過 `entity_alias.canonical` 正規化再比
    # (外審 2026-08-19 第四輪)。canonical 對不認得的名字原樣返還,
    # 所以 NDX 名單兩邊都正規化後聯集,ticker 與中文名都接得住。
    try:
        from entity_alias import canonical as _canon
    except Exception:                   # noqa: BLE001 - 載不到就退回逐字比對
        _canon = lambda x: x            # noqa: E731
    canon = {_canon(e) for e in ents} | ents
    if _canon("2330") in canon or "2330" in canon:
        return "2330"
    if canon & ({_canon(t) for t in ndx} | ndx):
        return "00662"
    return ""


def _event_card(c: dict, packet=None) -> str:
    """一條「昨夜三大重點」。**判斷 + 這件事的來歷。**

    先前只有判斷。而「三家獨立媒體證實」與「僅一家、未經證實」在信裡
    長得一模一樣 —— 可信度是分析的一部分,不是附註。
    """
    line = _claim_line(c)
    if not line:
        return ""
    cid = _s(c.get("cluster_id"))
    # 「最相關」標記:**Python 推導,模型不參與**(外審 2026-08-19 三輪
    # 定案 —— 模型自選的版本被駁回)。判準:這條重點指名的事件群,其成員
    # 新聞的**編輯標註實體**含 2330/台積電 → 2330;含 NDX 名單裡的美股 →
    # 00662。推不出來就不掛 —— 硬掛「市場最相關」是廢話。
    _target = _derived_target(packet, cid)
    if _target:
        line = f"**{_target} 最相關**:{line}"
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


def render(obj: Optional[dict], packet=None, admitted_watch=None,
           diag: Optional[dict] = None) -> str:
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
        # **各自成段**(2026-08-21 實信:單一換行被 markdown 摺成同一段,
        # 三條重點黏成一坨、佐證括號看起來像連環重複)。
        parts.append(f"## {SECTION_TOP3}\n" + "\n\n".join(top3))

    # ------- legacy 骨架(2026-08-19 第四批,schema v21)-------
    # 世界大事:**股市之外的世界**。這個段名曾被刪(schema 沒有對應欄位
    # 時掛這個招牌是假的);v21 有 `world_events` 之後,名字才誠實。
    def _sent(t: str) -> str:
        """句尾補標點 —— 兩句黏接時「…航運中。:戰爭…」那種
        「。:」殘骸就是沒做這件事的樣子(2026-08-29 實信)。"""
        t = _s(t)
        return t if (not t or t[-1] in "。!?!?…」)』") else t + "。"

    def _world_line(w) -> str:
        # 2026-08-29 使用者:「這邊整個排版格式跑掉了」。第一版用
        # 「head:why」黏接 —— head 尾常帶句號,排成「…。:戰…」;
        # 「後續可能影響」又另起縮排行,在郵件客戶端裡斷成碎片。
        # 改成 legacy 信的排法:**標題句。解讀句。後續可能影響:…**
        # 一氣呵成一個段落(郵件渲染最穩的形狀)。
        head = _sent(w.get("what"))
        why = _sent(w.get("why_it_matters"))
        nxt = _s(w.get("what_next"))
        return ("- " + head + why
                + (f"後續可能影響:{_sent(nxt)}" if nxt else "")).rstrip()

    world = [_world_line(w) for w in (obj.get("world_events") or [])
             if isinstance(w, dict) and _s(w.get("what"))]
    if world:
        parts.append(f"## {SECTION_WORLD}" + chr(10) + chr(10).join(world))

    # 未來 48 小時:每件事一個小段(基準/偏多/偏空/最受影響/失效)。
    scen_blocks = []
    for ev in (obj.get("upcoming_event_scenarios") or []):
        if not isinstance(ev, dict) or not _s(ev.get("event")):
            continue
        head = "**" + "|".join(x for x in (_s(ev.get("when")), _s(ev.get("event"))) if x) + "**"
        rows = [head]
        for field_label, key in (("基準預期", "base_expectation"), ("偏多情境", "bull_case"),
                           ("偏空情境", "bear_case"), ("最受影響", "most_affected"),
                           ("失效條件", "invalidation")):
            if _s(ev.get(key)):
                rows.append(f"{field_label}:{_s(ev.get(key))}")
        scen_blocks.append(rows[0] + "\n\n" + " ".join(rows[1:]))
    if scen_blocks:
        parts.append(f"## {SECTION_48H}" + chr(10) + (chr(10) * 2).join(scen_blocks))

    # 敘事變化:昨日觀點 → 強化/升溫/持續/減弱/反轉。
    # **昨日觀點的文字依 ID 取回 Python 保存的 statement**(同批外審 r2):
    # 模型可以拿合法的 pv1 配上改寫過的「昨日觀點」—— validator 只驗 ID
    # 存在,信裡就會出現偽造的昨日。packet 查得到就用 Python 的原文;
    # 查不到(舊測試無 packet)才退回模型抄本 —— validator 是門,這裡
    # 只負責不讓抄本蓋過原文。
    _pv_map = {str((it or {}).get("id") or ""):
               str((it or {}).get("statement") or "")
               for it in ((((packet or {}).get("market") or {})
                           .get("ANALYSIS_RECAP") or {}).get("items") or [])
               if isinstance(it, dict)}

    def _pv_text(d):
        return (_pv_map.get(str(d.get("prior_view_id") or ""))
                or _s(d.get("prior_view")))
    deltas = [f"- 「{_pv_text(d)}」→ **{_s(d.get('change'))}**:"
              f"{_s(d.get('evidence_today'))}"
              for d in (obj.get("narrative_delta") or [])
              if isinstance(d, dict) and _pv_text(d)
              and _s(d.get("change")) and _s(d.get("evidence_today"))]
    if deltas:
        parts.append(f"## {SECTION_DELTA}" + chr(10) + chr(10).join(deltas))

    # 七之五「多空交鋒」段已刪(2026-09-03 使用者)。`stance_extremes` 仍留在
    # packet 裡當證據給模型看,只是不再渲染成一段。

    # **第八段先寫、市場那一段後寫**(2026-08-18 使用者定案):
    # 使用者要的順序是「哪間公司昨天發生什麼事」在前,綜合判斷在後。
    tech_items, other_items = [], []
    tech_news, other_news, _diag_rows = [], [], []
    import finance_editorial as _finance
    _selected, _limited = _reader.select_cards(
        _finance.order_analyses(obj.get("top_news_analysis"), packet), packet or {})
    for _n in _selected:
        if not isinstance(_n, dict):
            continue
        # **無主體的新聞退回標題判準**(2026-08-29 實信):主體判準只認
        # 「可指名的公司」,於是長鑫、SK 海力士、CCL 漲價、NVL72 這種
        # **產業級**科技新聞(標題沒指名台股/註冊個股)全部掉進
        # 「其他類股」—— 那天八段只剩兩條,九段變科技大雜燴。
        # 有主體時仍以主體為準(公司的產業別比關鍵字可靠)。
        _is_t = _reader.article_is_tech(_n, packet or {})
        (tech_items if _is_t else other_items).append(_n)
        # **丟掉的卡要留痕**(2026-09-04 實信):`_news_line` 對空的
        # `why_it_matters` 回空、`_blocks` 不排 —— 模型分析 18 則、信裡只剩 7 則,
        # 而 `news_analyzed` 仍寫 18。(九段整段消失的主因另有其人:
        # `morning_report._cap_analysis_text` 的舊上限,2026-09-05 查明。)這裡把每一則
        # 的渲染結果記進 `diag`(進 manifest `llm.news_render`),讓判準說得出
        # 「分析了幾則、渲染了幾則、丟了哪幾則」;驗證器另擋空正文。
        _text = _news_line(_n, packet)
        (tech_news if _is_t else other_news).append(_text) if _text else None
        _diag_rows.append({"sid": _s(_n.get("source_item_id")),
                           "section": "tech" if _is_t else "other",
                           "rendered": bool(_text),
                           "why_chars": len(_s(_n.get("why_it_matters")))})
    if isinstance(diag, dict):
        diag.clear()
        diag.update({"analyzed": len(_diag_rows) + len(_limited),
                     "editorial_limit": 6,
                     "editorial_omitted": [_s(c.get("source_item_id")) for c in _limited],
                     "rendered_tech": len(tech_news), "rendered_other": len(other_news),
                     "dropped": [r for r in _diag_rows if not r["rendered"]][:20]})
    news = tech_news + other_news
    notes = []
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
            # 2026-08-18:**識別碼換成新聞標題**。`nfb2726dbc24` 對讀者是
            # 亂碼(使用者原話:「為何一堆亂碼」)。查不到標題時**只寫理由**
            # —— 理由才是內容,識別碼只是噪音;筆數與「另有幾則」不變。
            notes.append("- *傳導未完成:" + "、".join(
                (f"{t}({why})" if (t := _headline(sid, packet)) else why)
                for sid, why in stub[:3])
                + (f";另有 {len(stub) - 3} 則同樣未完成"
                   if len(stub) > 3 else "")
                + " —— 這幾則的影響幅度本報無法確認。*")
        # 第十九輪 P1-5:**看過而決定不談,讀者有權知道。**
        # 不顯示的話,「沒發生」與「發生了但本報判斷不重要」長得一樣。
        skipped = [d for d in (obj.get("dismissed_events") or [])
                   if isinstance(d, dict) and _s(d.get("why_not_material"))]
        if skipped:
            # 同上:群 ID 換標題、查不到就只寫理由;理由收成一句話
            # (整段理由是寫給驗證器看的,排進信裡就是使用者說的那面牆)。
            def _row(d) -> str:
                why = _first_sentence(_s(d.get("why_not_material")))
                t = _headline(_s(d.get("cluster_id")), packet)
                return f"{t}({why})" if t else why
            notes.append("- *今日看過但未展開:"
                         + "、".join(_row(d) for d in skipped[:4])
                         + (f";另有 {len(skipped) - 4} 件" if len(skipped) > 4 else "")
                         + "*")
        # 2026-08-19 第四批:科技/其他升回獨立 h2(legacy 信的排法)。
        # 條目之間空一行:`_md_to_html` 是逐行的,兩則新聞不能黏成同一個 <p>。
        body = []
        if tech_news:
            body.append("## " + SECTION_TECH)
            body.extend(tech_news)
        if other_news:
            body.append("## " + SECTION_OTHER)
            body.extend(other_news)
        if notes:
            body.append("### " + SUBSECTION_NOTES)
            body.extend(notes)
        parts.append((chr(10) * 2).join(body))

    # 十、總體經濟與政策環境:(A)(B)(C) 三個切面(legacy 的排法)。
    macro = obj.get("macro_environment") if isinstance(
        obj.get("macro_environment"), dict) else {}

    def _macro_text(key):
        sec = macro.get(key)
        # v22:切面是 {analysis, evidence_ids} 物件(裸字串時代已終結 ——
        # 有內容必有證據,證據檢查在 validator,信裡只排 analysis)。
        return _s(sec.get("analysis")) if isinstance(sec, dict) else ""
    macro_rows = [f"**({tag})** {_macro_text(key)}"
                  for tag, key in (("A", "us_rates_fx_vix"), ("B", "fed_policy"),
                                   ("C", "geopolitics")) if _macro_text(key)]
    if macro_rows:
        parts.append(f"## {SECTION_MACRO}" + chr(10) + (chr(10) * 2).join(macro_rows))

    # 十之二、重大政策深度解析(v20 的 `taiwan_policy`,改編號不改欄位):
    # **政策名當小標、分析當內文** —— legacy 的排法。一項一段。
    pol_blocks = [f"**{_s(x.get('what'))}**" + chr(10) + chr(10) + _s(x.get("impact"))
                  for x in (obj.get("taiwan_policy") or [])
                  if isinstance(x, dict) and _s(x.get("what")) and _s(x.get("impact"))]
    if pol_blocks:
        parts.append(f"## {SECTION_POLICY}" + chr(10) + (chr(10) * 2).join(pol_blocks))

    # 十一、台灣本地動態:一項一行(GDP/例行公告/天氣…)。
    local = [f"- {_s(x.get('what'))}:{_s(x.get('impact'))}"
             for x in (obj.get("taiwan_local") or [])
             if isinstance(x, dict) and _s(x.get("what")) and _s(x.get("impact"))]
    if local:
        parts.append(f"## {SECTION_LOCAL}" + chr(10) + chr(10).join(local))

    # **「九、今日市場關注與預測」整段拿掉**(2026-08-19 使用者:
    # 「直接刪除整段今日市場關注與預測」)。它是 08-18 那批把五個段落
    # (橫向綜合/全球連動/台股與台積電/各標的合計影響/已被市場反映)
    # 併成的一段,而使用者隔天的回饋是整段都不要 —— 重點在第八段的
    # 逐則敘事,綜合判斷由頂部結論卡與第七段承擔。
    # 對應的欄位仍在 schema 裡被要求與驗證(拿掉要求會讓第八段的品質
    # 跟著掉:模型是先想清楚全局才寫得好逐則);它們進
    # `DELIBERATELY_UNRENDERED_TOP_LEVEL` 帳本,不是被遺忘。

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
            # **不可以叫 `label`**(2026-08-29 實信):`label` 在函式開頭
            # 已經綁定成**立場**(偏多/偏空/中性),這個迴圈把它蓋掉之後,
            # 底下的「立場:{label}」印的是最後一條昨日觀察點的文字 ——
            # 實信印出「立場:NVDA財報前AI板塊資金動向(2330是否站穩2400)」。
            # 特化路徑第一次上線就中,因為 legacy 路徑不走這一段。
            watch_text = _wid_text.get(wid) or wid
            status = _WR_ZH.get(str(w.get("status") or ""), "?")
            what = _s(w.get("what_happened"))
            _wr_lines.append(f"{watch_text}：{status}"
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
    return _reader.public_sections("\n\n".join(parts), obj, packet)
