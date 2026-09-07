# -*- coding: utf-8 -*-
"""**schema v2 深度欄位的渲染**(因果鏈/量級/關係/橫向綜合)。

從 `analysis_render` 拆出:那個檔管**整封信的組裝順序與段落語意**,
這裡管**單一條目要長什麼樣**。拆的直接原因是 schema v2 讓渲染多了
一百多行,而兩塊的變更理由不同 —— 段落順序跟著信件結構走,
條目寫法跟著 schema 版本走。

**渲染層丟資料時,schema 再深也沒用**(第十五輪 P1-2)—— 這裡的每個
函式都對應一組「模型填了、讀者要看得到」的欄位。
"""
from __future__ import annotations

import news_impact as _impact


def _s(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _chain_line(chain: list) -> str:
    """因果鏈壓成一行:`A → B → C`。

    **鏈斷掉時不假裝連續**:下一步的起點不等於上一步的終點,就用
    `；` 分段。用一條箭頭串起兩件不相干的事,是這份報告最該避免的
    那種句子(通道與 fact/推論 標記拿掉了 —— 那些括號正是把一句話
    撐成三行的東西,欄位本身仍在 schema 裡被驗證)。
    """
    # **連續性的判準要與驗證器同一個**(外審 2026-08-17):schema 明說
    # 「照抄再補充」是合法的接法(`先進封裝產能擴充` → `先進封裝產能擴充
    # (CoWoS 量產)`),`analysis_validate._same_node` 用包含判準放行 ——
    # 渲染層若改用逐字相等,**驗證過的連續鏈會被畫成斷鏈**,而讀者只會
    # 看到一條莫名其妙分成兩段的因果鏈。判準只有一份,不在這裡重寫。
    try:
        from analysis_validate import _same_node as _cont
    except Exception:               # noqa: BLE001 - 判準載不到就退回逐字
        def _cont(prev_to, cur_from, subjects=()):
            return str(prev_to or "") == str(cur_from or "")
    segs, nodes = [], []
    for st in (chain or []):
        a, b = _s(st.get("from_what")), _s(st.get("to_what"))
        if not a or not b:
            continue
        if not nodes:
            nodes = [a, b]
        elif _cont(nodes[-1], a):
            # 接得上就只補終點 —— 起點的補充細節不再重複印一次。
            nodes.append(b)
        else:
            segs.append(nodes)
            nodes = [a, b]
    if nodes:
        segs.append(nodes)
    return "；".join(" → ".join(seg) for seg in segs)


def _universe_index(packet) -> dict:
    """`{代號: row, 名稱: row}` —— 兩種寫法都查得到同一檔。"""
    out = {}
    for row in ((packet or {}).get("tw_universe") or []):
        if not isinstance(row, dict):
            continue
        code, name = _s(row.get("code")), _s(row.get("name"))
        if code:
            out[code] = row
        if name:
            out[name] = row
    return out


def _blurb(row: dict) -> str:
    """一句公司側寫。**寫得出「這家公司在做什麼」才有意義。**

    `tw_universe` 的 `desc` 有兩種來源,而它們**開頭都是公司名**:
      * 手寫的業務簡介:`台積電 — 全球晶圓代工龍頭,先進製程市佔超過 90%`
      * 查不到時的退化字串:`鴻海 — 其他電子業`
    上一版用「開頭是公司名就丟掉」來擋第二種 —— 那把**五十檔手寫的側寫
    全部丟掉了**,信裡只剩產業別。判準改成「拿掉開頭的公司名之後還剩什麼」:
    剩下的若與產業別相同(或空)就是退化字串,否則那就是真正的側寫。
    """
    desc, name = _s(row.get("desc")), _s(row.get("name"))
    industry = _s(row.get("industry"))
    if desc.startswith(name):
        desc = desc[len(name):].lstrip(" 　—–-:：,,")
    return desc if desc and desc != industry else industry


def news_subject(n: dict, packet=None) -> dict:
    """**這則新聞在講誰**:`{"label": 顯示用, "industry": 產業別, "name": 主體}`。

    **候選要在標題裡被指名才算主體**(2026-08-19 生產:五則新聞的小標題
    全是「台積電」—— 總經新聞被 `Google:2330` 查回來就帶著 2330 的編輯
    標註,而這裡沒驗證就採用。那與事件層修過的 P1-1 是**同一種病**:
    「跟誰有關」被當成「在講誰」)。判準與事件層同一個函式
    (`news_events.mentions_entity`),不在這裡重寫一份。

    指不出來就沒有公司主體 —— 呼叫端會用**新聞標題本身**當小標題,
    那正是使用者要的:「小標題是要昨日新聞的標題,不是都台積電」。
    """
    idx = _universe_index(packet)
    by_id = {_s(x.get("source_item_id")): x for x in ((packet or {}).get("news") or [])
             if isinstance(x, dict)}
    item = by_id.get(_s(n.get("source_item_id"))) or {}
    title = _s(item.get("title"))
    cands = [_s(e) for e in (item.get("entities") or []) if _s(e)]
    cands += [_s(a.get("asset_id")) for a in (n.get("affected_assets") or [])
              if isinstance(a, dict) and _s(a.get("asset_id"))]
    try:
        import news_events as _ne
    except Exception:                   # noqa: BLE001 - 判準載不到就沒有主體
        _ne = None
    try:
        import instrument_registry as _ir
    except Exception:                   # noqa: BLE001
        _ir = None
    try:
        import company_profiles as _cp
    except Exception:                   # noqa: BLE001
        _cp = None
    for c in cands:
        row = idx.get(c)
        if row is not None:
            # 台股:別名 = universe 宣告的公司名。**標題沒指名就跳過。**
            code = _s(row.get("code")) or c
            kn = {code: (_s(row.get("name")),)}
            if _ne is None or not _ne.mentions_entity(title, code, kn):
                continue
            name, blurb = _s(row.get("name")), _blurb(row)
            label = f"{name}（{code}" + (f",{blurb}" if blurb else "") + "）"
            return {"label": label, "industry": _s(row.get("industry")), "name": name}
        # 外國個股:範疇問 registry,別名 = 宣告的顯示名 + 代號本身。
        if _ir is None or _ne is None:
            continue
        try:
            _cid, scope, status = _ir.resolve_status(c)
        except Exception:               # noqa: BLE001 - 單一候選查壞不影響其他
            continue
        if scope != "equity" or status == "invalid":
            continue
        disp = _cp.display_name(c) if _cp else c
        if not _ne.mentions_entity(title, c, {c: (disp, c)}):
            continue
        prof = _cp.profile_of(c) if _cp else ""
        label = (f"{disp}（{c}" + (f",{prof}" if prof else "") + "）"
                 if disp != c else (f"{c}（{prof}）" if prof else c))
        return {"label": label, "industry": "", "name": c}
    return {"label": "", "industry": "", "name": ""}


def is_tech(subject: dict) -> bool:
    """這則新聞歸科技類股嗎。**判準不在這裡自己寫一份**(見 `industry_class`)。"""
    import industry_class as _ic
    sub = subject or {}
    return (_ic.is_tech_industry(sub.get("industry"))
            or _ic.is_tech_foreign(sub.get("name")))


def _headline_of(n: dict, packet=None, subject_name: str = "") -> str:
    """**昨天發生什麼事** —— 用新聞自己的標題(客觀事實),不是模型的判斷。

    小標題已經寫了公司名,標題開頭再寫一次會排成
    「台積電(2330,晶圓代工龍頭):**台積電** CoWoS 產能再擴一倍」。
    開頭重複的那一段拿掉;**只拿掉開頭、且剩下的還成句**才動 ——
    標題是外部文字,削過頭比重複更糟。
    """
    by_id = {_s(x.get("source_item_id")): x for x in ((packet or {}).get("news") or [])
             if isinstance(x, dict)}
    title = _s((by_id.get(_s(n.get("source_item_id"))) or {}).get("title"))
    # **代號與顯示名都要能削**:主體是 `MSFT` 而標題寫「Microsoft Q4 財報…」,
    # 只比對代號的話會排成「Microsoft（MSFT,…）:Microsoft Q4 財報…」。
    names = [_s(subject_name)]
    try:
        import company_profiles as _cp
        names.append(_cp.display_name(_s(subject_name)))
    except Exception:                   # noqa: BLE001 - 側寫載不到就只削代號
        pass
    for name in sorted({x for x in names if x}, key=len, reverse=True):
        if title.startswith(name):
            rest = title[len(name):].lstrip(" 　:::,,、-—")
            if len(rest) >= 4:
                return rest
    return title


#: 佐證等級的顯示。**這是資料**(packet 的 `source_grade`),不是判斷。
_GRADES = {"A": "A 級", "B": "B 級", "C": "C 級", "D": "D 級"}

#: 標籤的第二格:**這件事被幾個獨立來源說過**(外審 2026-08-18,三輪)。
#:
#: 使用者要的是 `[A 級・信心:中]`。三版都被駁回,而三次的理由是同一個:
#:   1. 模型自己填 0–1 → 同一份證據在兩次取樣拿到不同標籤;
#:   2. 由鏈的 `step_type` / `magnitude_band` 推導 → 那些仍是模型寫的;
#:   3. 由 packet 的獨立來源數推導,但**仍叫「信心」** → 三家獨立報導會讓
#:      一段推測性的分析看起來被驗證過。第三次的指正是對的:
#:      **佐證數不是分析的可信度。**
#:
#: 所以這一格不再叫「信心」,改寫它真正量到的東西。它是 Python 算的
#: (`news_clusters` 分群時就算好),而且與「A 級」是不同軸:
#: 等級講「最好的那個來源有多可靠」,這一格講「有幾家互相獨立地說同一件事」
#: —— 一家 A 級媒體獨家 = `[A 級・僅單一來源]`,那正是讀者需要的組合。
#:
#: **分析本身可不可信,由「傳導」與「什麼會推翻它」自己說**;
#: 這份報告沒有 Python 算得出來的「分析信心」,所以就不寫一個。
_CORROBORATION_HIGH = 3


def _corroboration_word(n: dict, packet=None) -> str:
    """`官方公告` / `3 家獨立報導` / `僅單一來源`。**輸入只有 packet。**

    查不到這則屬於哪一群就回空字串 —— 沒有依據時給一個標籤是最糟的
    那種假精確。
    """
    sid = _s(n.get("source_item_id"))
    if not sid or not isinstance(packet, dict):
        return ""
    for c in ((packet.get("news_clusters") or {}).get("clusters") or []):
        if not isinstance(c, dict) or sid not in (c.get("member_source_ids") or []):
            continue
        if c.get("official"):
            return "官方公告"
        try:
            indep = int(c.get("independent_sources") or 0)
        except (TypeError, ValueError):
            return ""
        # **0 不是 1**(外審 2026-08-18 第四輪):`independent_sources == 0`
        # 代表發布者查不到或全是聚合器轉載 —— 那一群可能有好幾則,
        # 寫成「僅單一來源」是**說了一件沒發生的事**。
        if indep >= 2:
            return f"{indep} 家獨立報導"
        return "僅單一來源" if indep == 1 else "來源獨立性未驗證"
    return ""


def _credibility(n: dict, packet=None) -> str:
    """`[A 級・2 家獨立報導]`。**兩半各自缺就各自不寫**,不用另一半頂替。"""
    by_id = {_s(x.get("source_item_id")): x for x in ((packet or {}).get("news") or [])
             if isinstance(x, dict)}
    item = by_id.get(_s(n.get("source_item_id"))) or {}
    bits = []
    grade = _GRADES.get(_s(item.get("source_grade")).upper())
    if grade:
        bits.append(grade)
    corr = _corroboration_word(n, packet)
    if corr:
        bits.append(corr)
    return f"[{'・'.join(bits)}]" if bits else ""


def _attribution(n: dict, packet=None) -> str:
    """`（鉅亨台股）`。發布者是**新聞自己帶的欄位**,不是模型寫的。

    `source_name` 是真正的發布者;`source` 常是聚合器別名
    (`Google:2330`、`類股-金融-台股`)—— 那種字串印給讀者看沒有意義,
    所以只在它不長得像內部標籤時才退而用它。
    """
    by_id = {_s(x.get("source_item_id")): x for x in ((packet or {}).get("news") or [])
             if isinstance(x, dict)}
    item = by_id.get(_s(n.get("source_item_id"))) or {}
    who = _s(item.get("source_name"))
    if not who:
        raw = _s(item.get("source"))
        who = "" if (":" in raw or raw.startswith("類股-")) else raw
    return f"（{who}）" if who else ""


def _news_line(n: dict, packet=None) -> str:
    """一則新聞 = **一小段散文**(2026-08-19 使用者定案)。

    形狀:

        **小標題**:昨天發生什麼事(發布者)。為什麼重要。
        傳導:A → B → C;若 X,此判斷不成立。2330:一階、二階。[A 級・佐證]

    * **小標題優先是公司**(標題有指名時),否則就是**新聞標題本身** ——
      使用者原話:「小標題是要昨日新聞的標題,不是都台積電」。
    * 傳導 / 什麼會推翻它 / 逐標的影響**併進同一段**,不再排成清單 ——
      使用者原話:「全部整合成一小段落語句敘述即可」。
      內容一樣都在,少的是排版的行數。
    """
    body = _s(n.get("why_it_matters"))
    if not body:
        return ""
    subject = news_subject(n, packet)
    headline = _headline_of(n, packet, subject.get("name") or "")
    attribution = _attribution(n, packet)
    from render_utils import safe_href
    from urllib.parse import quote
    item = next((x for x in ((packet or {}).get("news") or [])
                 if isinstance(x, dict) and x.get("source_item_id") == n.get("source_item_id")), {})
    href = safe_href(str(item.get("url") or item.get("link") or ""), max_chars=2048)
    if headline and href:
        headline = f"[{headline.replace('[', '（').replace(']', '）')}]({quote(href, safe=':/?=&%#@+;,$!-_~')})"
    if subject.get("label"):
        lead = (f"**{subject['label']}**:"
                + (_join_sentence(headline.rstrip(_TERMINAL_MARKS) + attribution)
                   if headline else ""))
    elif headline:
        # 沒有公司主體(總經/利率/油價…):**新聞標題就是小標題**。
        display_head = headline if href else f"**{headline.rstrip(_TERMINAL_MARKS)}**"
        lead = _join_sentence(display_head + attribution)
    else:
        lead = ""
    parts = [lead + body if lead else body]
    import news_research_context as _research
    history = _research.history_prose(n, packet)
    if history:
        parts.append(history)
    chain = _chain_line([st for st in (n.get("mechanism_steps") or [])
                         if isinstance(st, dict)])
    if chain:
        parts.append(f"\n\n傳導:{chain}。\n\n")
    impact = _impact.readout(n, _s)
    if impact:
        parts.append(impact)
    inval = _s(n.get("invalidation_signal"))
    if inval:
        parts.append(_join_sentence(f"若{inval},此判斷不成立"))
    assets = _assets_prose(n, packet)
    if assets:
        parts.append(assets)
    tail = _credibility(n, packet)
    if tail:
        parts.append(tail)
    # **`source_caveat` 要進信**(全案審查 2026-09-03 LM-4):驗證器為單一來源
    # 強制它非空(寫「無」即駁回、燒一輪 semantic 額度),schema 說「由 renderer
    # 固定呈現」—— 而這裡從未讀取,讀者只看到「僅單一來源」的標籤,「該保留
    # 什麼」一句都沒進信。判準回 validator 問(與 `_assets_prose` 同一個理由:
    # 不在渲染層自己判一份);多方證實/官方那些寫「無」的不排。
    cav = _s(n.get("source_caveat"))
    if cav and cav != "無":
        try:
            import analysis_validate as _av10
            weak = _av10.weakly_corroborated(n, packet)
        except Exception:               # noqa: BLE001 - 判準失敗不毀渲染
            weak = False
        if weak:
            parts.append("\n\n" + _join_sentence(f"保留:{cav.rstrip(_TERMINAL_MARKS)}"))
    # 同一段:`_md_to_html` 會把相鄰的非空行併進同一個 <p>,
    # 這裡直接用空格接起來,語意與排版一致。
    return " ".join(parts)


def _assets_prose(n: dict, packet=None) -> str:
    """逐標的影響壓成**句子**:`2330:一階、二階;00662:…。`

    〔推測性傳導〕的揭露不因改排版而消失(第三十二輪 P1-3)——
    判準仍回 validator 問,不在渲染層自己判一份。
    """
    try:
        import analysis_validate as _av9
    except Exception:                   # noqa: BLE001 - 標籤失敗不毀渲染
        _av9 = None
    rows = []
    for a in (n.get("affected_assets") or []):
        if not isinstance(a, dict) or not _s(a.get("asset_id")):
            continue
        aid = _s(a.get("asset_id"))
        spec = ""
        if _av9 is not None:
            try:
                if _av9.speculative_transmission(aid, n, packet):
                    spec = "〔推測性傳導〕"
            except Exception:           # noqa: BLE001
                spec = ""
        effects = "、".join(x.rstrip("。") for x in
                            (_s(a.get("first_order_effect")),
                             _s(a.get("second_order_effect"))) if x)
        rows.append(f"{aid}{spec}:{effects}" if effects else f"{aid}{spec}")
    return _join_sentence(";".join(rows)) if rows else ""


#: 句末標點(**全形半形都要**,外審 2026-08-18:只收半形的話
#: 「財測上修!」會排成「財測上修!(Reuters)。」)。
_TERMINAL_MARKS = "。." + chr(65281) + chr(65311) + "!?" + chr(65294)


def _join_sentence(text: str) -> str:
    """接成句子:自己有句末標點就不再補一個。"""
    t = str(text or "").strip()
    return t if (not t or t[-1] in "。！？;;") else t + "。"
