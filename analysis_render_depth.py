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


def _s(v) -> str:
    return v.strip() if isinstance(v, str) else ""


#: 量級的中文。**`unknown` 不寫成「影響有限」** —— 那正好是使用者抱怨的
#: 那種形容詞;誠實的說法是「量級判斷不出來」,後面接缺什麼資料。
_BANDS = {"negligible": "量級可忽略", "small": "量級小", "moderate": "量級中等",
          "large": "量級大", "unknown": "量級判斷不出來"}
_RELS = {"reinforcing": "互相強化", "conflicting": "方向相反",
         "competing_for_same_capacity": "互相排擠(搶同一段產能)",
         "same_underlying_driver": "同一個底層驅動"}
#: 因果步驟的可信度。**沒有證據的推論要看得出來**,否則整條鏈讀起來像事實。
_STEP = {"fact": "", "inference": "(推論)", "scenario": "(情境)",
         "unknown": "(資料不足)"}


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

    使用者 2026-08-18 定案:小標題要回到「哪間公司昨天發生什麼事」,
    而不是一串偏多/偏空。主體的來源依序是:
      1. 新聞自己的**編輯標註實體**(`entities`,人工標的代號/公司名);
      2. `affected_assets` 的第一個標的(模型宣告的受影響對象)。
    台股查 `tw_universe` 拿名稱、代號、產業別與一句簡介。
    **查不到就用原字串**,不編造公司名;完全沒有主體時回空 label,
    呼叫端會退回沒有小標題的舊排版(編一個公司名比沒有標題糟得多)。
    """
    idx = _universe_index(packet)
    by_id = {_s(x.get("source_item_id")): x for x in ((packet or {}).get("news") or [])
             if isinstance(x, dict)}
    item = by_id.get(_s(n.get("source_item_id"))) or {}
    cands = [_s(e) for e in (item.get("entities") or []) if _s(e)]
    cands += [_s(a.get("asset_id")) for a in (n.get("affected_assets") or [])
              if isinstance(a, dict) and _s(a.get("asset_id"))]
    try:
        import instrument_registry as _ir
    except Exception:                   # noqa: BLE001 - 查不到就只認 universe
        _ir = None
    # **一個候選一個候選地問完兩張表,再換下一個候選**(外審 2026-08-18)。
    # 先前是「先拿所有候選掃 universe,掃不到再拿所有候選問 registry」——
    # 那讓**查得到的地方**壓過了上面寫的候選順序:`entities=["ASML"]` 而
    # `affected_assets` 是 2330 時,ASML 不在當日台股 universe 裡,於是
    # 小標題寫成「台積電」,而標題與編輯標註講的都是 ASML。
    for c in cands:
        row = idx.get(c)
        if row:
            code, name, blurb = _s(row.get("code")), _s(row.get("name")), _blurb(row)
            label = f"{name}（{code}" + (f",{blurb}" if blurb else "") + "）"
            return {"label": label, "industry": _s(row.get("industry")), "name": name}
        # 不在當日 universe 的:**只有個股能當主體**。
        # 「費半」「加權指數」「WTI」不是「哪間公司昨天發生什麼事」的答案
        # —— 拿指數當小標題,讀者看到的是一個沒有主體的標題。範疇問
        # `instrument_registry`(它就是回答「這是哪一種標的」的那個模組)。
        if _ir is not None:
            try:
                _cid, scope, status = _ir.resolve_status(c)
            except Exception:           # noqa: BLE001 - 單一候選查壞不影響其他
                continue
            if scope == "equity" and status != "invalid":
                # 外國個股的側寫是**宣告**(`company_profiles`),台股那半
                # 來自 universe 的手寫簡介 —— 兩邊都不是從新聞猜出來的。
                # 沒宣告就只寫名字與代號,不編造這家公司在做什麼。
                try:
                    import company_profiles as _cp
                    disp, prof = _cp.display_name(c), _cp.profile_of(c)
                except Exception:       # noqa: BLE001 - 側寫載不到不毀渲染
                    disp, prof = c, ""
                label = (f"{disp}（{c}" + (f",{prof}" if prof else "") + "）"
                         if disp != c else
                         (f"{c}（{prof}）" if prof else c))
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
    """一則新聞:**哪間公司、昨天發生什麼事**,底下才接傳導與失效條件。

    **2026-08-18 使用者定案(第三次要求)**:小標題要回到舊版的寫法 ——
    「公司(代號,簡介):昨天發生的那則新聞」是**客觀事實**,由新聞標題
    與 `tw_universe` 的宣告資料組出來,不是模型的判斷;模型的判斷寫在
    下面那一段敘述,再下面才是一行傳導、一行什麼會推翻它。

    先前整段由「偏多/偏空、量級、時間窗」開頭 —— 使用者的原話是
    「而不是整篇都是偏多什麼的」。逐標的方向沒有消失,它在
    「各標的合計影響」那一段(那一段的名字就是在講方向)。
    """
    body = _s(n.get("why_it_matters"))
    if not body:
        return ""
    subject = news_subject(n, packet)
    headline = _headline_of(n, packet, subject.get("name") or "")
    # **舊版的寫法:公司、昨天發生什麼事、分析,在同一段裡**
    # (2026-08-18 使用者貼了舊信要求照做):
    #     台積電（2330,全球晶圓代工龍頭…）:熊本廠測得 7.1 強震…（鉅亨台股）。
    #     短期產線停機天數將影響 Q3 出貨節奏…[A 級・信心:中]
    # 底下才接傳導 / 什麼會推翻它 / 後續影響。
    # 主體查不到就不硬掰一個公司名 —— 直接從新聞本身寫起。
    lead = f"**{subject['label']}**:" if subject.get("label") else ""
    # **出處接在句末標點之前**(外審 2026-08-18 P3):標題自帶「。」「!」「?」
    # 時直接串會排成「公司公布財報。(鉅亨網)。」—— 標點先拿掉,
    # 由 `_join_sentence` 統一補一個。
    what = (_join_sentence(headline.rstrip(_TERMINAL_MARKS) + _attribution(n, packet))
            if headline else "")
    tail = _credibility(n, packet)
    out = [lead + what + body + tail]
    chain = [st for st in (n.get("mechanism_steps") or []) if isinstance(st, dict)]
    line = _chain_line(chain)
    if line:
        out.append("  - 傳導:" + line)
    inval = _s(n.get("invalidation_signal"))
    if inval:
        out.append(f"  - 什麼會推翻它:{inval}")
    # **佐證由 packet 說,不由模型說**(外審 2026-08-18 第三輪的延伸):
    # 先前句尾那個「(單一來源)」來自模型的 `corroboration_assessment`,
    # 而同一件事 packet 分群時就算好了(而且 schema 自己寫著「以 EVIDENCE 的
    # `news_clusters[].corroboration` 為準」)。兩處寫同一件事、其中一處是
    # 模型抄的 —— 留 packet 那份,放進行尾的標籤裡。
    # **受影響標的與後續影響仍要寫,但不再逐檔掛方向詞**(2026-08-18):
    # 使用者的原話是「不是整篇都是偏多什麼的」—— 方向、幅度與時間窗在
    # 「各標的合計影響」那一段(那一段的名字就是在回答方向)。
    # 這裡留下的是別處沒有的兩件事:一階/二階影響(使用者要的「後續
    # 影響、脈絡」),以及**哪一個只是推測性傳導**(第三十二輪 P1-3
    # 的揭露不能因為改版而消失)。
    out.extend(_assets(n, packet))
    return chr(10).join(out)


def _assets(n: dict, packet=None) -> list:
    rows = []
    for a in (n.get("affected_assets") or []):
        if not isinstance(a, dict) or not _s(a.get("asset_id")):
            continue
        # **兩層傳導要分得開**(第三十二輪 P1-3,選項 B):只靠當日
        # universe 放行的標的,讀者要能自行折價 —— universe 證明它是
        # 真股票,證明不了這件事真的會傳導到它。判準回 validator 問
        # (`speculative_transmission`),不在渲染層自己判一份。
        _spec = ""
        try:
            import analysis_validate as _av9
            if _av9.speculative_transmission(_s(a.get("asset_id")), n, packet):
                _spec = "〔推測性傳導,未宣告的供應鏈關係〕"
        except Exception:               # noqa: BLE001 - 標籤失敗不毀渲染
            _spec = ""
        # **2026-08-18:方向/幅度/時間窗整組拿掉。** 那三個標籤逐檔重複,
        # 正是使用者說的「整篇都是偏多什麼的」;同樣的三件事在
        # 「各標的合計影響」那一段有一次、而且是合計後的版本。
        head = _s(a.get("asset_id")) + _spec
        # 兩段影響是**兩句話**,先前用「、」黏起來會接出「。、」
        # (2026-08-17 生產信裡看得到)。
        body = "".join(_join_sentence(x) for x in
                       (_s(a.get("first_order_effect")),
                        _s(a.get("second_order_effect"))) if x)
        rows.append(f"  - {head}:{body}" if body else f"  - {head}")
    return rows


#: 句末標點(**全形半形都要**,外審 2026-08-18:只收半形的話
#: 「財測上修!」會排成「財測上修!(Reuters)。」)。
_TERMINAL_MARKS = "。." + chr(65281) + chr(65311) + "!?" + chr(65294)


def _join_sentence(text: str) -> str:
    """接成句子:自己有句末標點就不再補一個。"""
    t = str(text or "").strip()
    return t if (not t or t[-1] in "。！？;;") else t + "。"


def _tension_head(tid: str, packet) -> str:
    """張力本身長什麼樣 —— **由 renderer 從 packet 回查,不讓模型重述數字**。

    第十八輪:信裡連著三個「矛盾調和:…(偏向前者)」,而讀者無從知道
    「前者」是 QQQ、是開盤預測、還是產業中位數。調和說得再好,
    看不出在調和什麼就等於沒說。
    """
    if not isinstance(packet, dict):
        return ""
    for it in ((packet.get("signal_tensions") or {}).get("items") or []):
        if not isinstance(it, dict) or f"tension:{it.get('tension_id')}" != tid:
            continue

        def _one(side):
            side = side if isinstance(side, dict) else {}
            v, u = side.get("value"), _s(side.get("unit"))
            num = (f"{v:+.2f}".rstrip("0").rstrip(".") if isinstance(v, float)
                   else f"{v:+}" if isinstance(v, int) else "")
            return f"{_s(side.get('label'))} {num}{'%' if u == '%' else ' ' + u}".strip()
        return (f"【{_s(it.get('topic'))}】{_one(it.get('left'))} ↔ "
                f"{_one(it.get('right'))}")
    return ""


def _synthesis(cms: dict, packet=None) -> str:
    """橫向綜合。**這是這次改版要的東西** —— 訊號之間的關係,
    而不是把各市場各寫一句。"""
    if not isinstance(cms, dict):
        return ""
    rows = []
    for key, name in (("reinforcing_signals", "互相強化"),
                      ("conflicting_signals", "互相抵銷")):
        vals = [_s(x) for x in (cms.get(key) or []) if _s(x)]
        if vals:
            rows.append(f"- **{name}**:" + "、".join(vals[:5]))
    # Commit E:**共用底層驅動的說明要進信。** 「三個獨立訊號同向」與
    # 「同一件事的三個表現」對讀者是完全不同的訊息 —— schema 收了、
    # 驗證器擋了,而先前渲染層一個字都沒印。
    for x in (cms.get("shared_driver_notes") or []):
        if not isinstance(x, dict):
            continue
        why = _s(x.get("why_not_double_counted"))
        cids = [_s(c) for c in (x.get("cluster_ids") or []) if _s(c)]
        if why and len(cids) >= 2:
            rows.append(f"- **這 {len(cids)} 件事共用同一個驅動**,"
                        f"不重複計權:{why}")
    if _s(cms.get("dominant_driver")):
        rows.append(f"- **今天的主導因子**:{_s(cms.get('dominant_driver'))}"
                    + (f" —— {_s(cms.get('why_it_dominates'))}"
                       if _s(cms.get("why_it_dominates")) else ""))
    for key, name in (("net_effect_intraday", "即日"),
                      ("net_effect_next_days", "未來 1–5 日")):
        if _s(cms.get(key)):
            rows.append(f"- **{name}**:{_s(cms.get(key))}")
    src = [_s(x) for x in (cms.get("funds_moving_from") or []) if _s(x)]
    dst = [_s(x) for x in (cms.get("funds_moving_to") or []) if _s(x)]
    if src or dst:
        rows.append("- **資金流向**:"
                    + ("、".join(src[:4]) if src else "(來源不明)")
                    + " → " + ("、".join(dst[:4]) if dst else "(去向不明)"))
    # 第十七輪 P1-3:**逐筆張力的調和要看得到。** 只印一句「訊號互有矛盾」
    # 等於沒有處理 —— 而那正是這個結構要取代的東西。
    # 第十八輪 P1-7:同向訊號的解讀也要進信 —— 只印矛盾的話,
    # 「兩個訊號其實是同一個底層驅動」這種話讀者永遠看不到。
    for r in (cms.get("alignment_readings") or []):
        if not isinstance(r, dict) or not _s(r.get("interpretation")):
            continue
        head = _tension_head(_s(r.get("alignment_id")), packet)
        if head:
            rows.append(f"  - {head}")
        extra = _s(r.get("marginal_information"))
        risk = _s(r.get("double_count_risk"))
        rows.append(f"  - **同向訊號**:{_s(r.get('interpretation'))}"
                    + (f";增量資訊:{extra}" if extra else "")
                    + (f"。會不會重複計算:{risk}" if risk else ""))
    for r in (cms.get("tension_resolutions") or []):
        if not isinstance(r, dict) or not _s(r.get("resolution")):
            continue
        side = {"left": "偏向前者", "right": "偏向後者",
                "neither": "兩邊都不夠強"}.get(_s(r.get("dominant_side")), "")
        head = _tension_head(_s(r.get("tension_id")), packet)
        if head:
            rows.append(f"  - {head}")
        rows.append(f"  - **矛盾調和**:{_s(r.get('resolution'))}"
                    + (f"({side})" if side else "")
                    + (f";{_s(r.get('why'))}" if _s(r.get("why")) else "")
                    + (f"。什麼情況分出勝負:{_s(r.get('decision_rule'))}"
                       if _s(r.get("decision_rule")) else ""))
    if _s(cms.get("what_would_flip_it")):
        rows.append(f"- **什麼會讓它翻盤**:{_s(cms.get('what_would_flip_it'))}")
    return "\n".join(rows)
