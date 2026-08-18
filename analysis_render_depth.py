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
    """一句簡介。**沒有簡介時不重複公司名。**

    `tw_universe` 的 `desc` 有兩種來源:手寫的業務簡介(「晶圓代工龍頭」),
    以及查不到時的退化字串「<名稱> — <產業別>」。後者放進
    「鴻海(2317,鴻海 — 其他電子業)」會把公司名印兩次 —— 那種行是
    上一版被抱怨的「讀起來像表單」的來源之一。退化字串就只留產業別。
    """
    desc, name = _s(row.get("desc")), _s(row.get("name"))
    if desc and name and not desc.startswith(name):
        return desc
    return _s(row.get("industry"))


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
                return {"label": c, "industry": "", "name": c}
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
    name = _s(subject_name)
    if name and title.startswith(name):
        rest = title[len(name):].lstrip(" 　:::,,、-—")
        if len(rest) >= 4:
            return rest
    return title


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
    # 小標題:主體 + 昨天發生什麼事。兩者都查不到就不硬掰一個標題,
    # 直接寫敘述(舊排版)—— 編一個公司名比沒有標題糟得多。
    head = ""
    if subject.get("label") and headline:
        head = f"**{subject['label']}:{headline}**"
    elif subject.get("label"):
        head = f"**{subject['label']}**"
    elif headline:
        head = f"**{headline}**"
    # **小標題與敘述之間要空一行**(外審 2026-08-18)。`_md_to_html` 是
    # 逐行的:相鄰的非空行會被 `" ".join` 併成同一個 `<p>` —— 只隔一個
    # 換行的話,信裡看到的是「**小標題** 敘述接在後面」,而使用者要的是
    # 「在公司發生新聞的**下方**寫一段」。空行才會 flush 段落。
    out = [(head + chr(10) * 2 + body) if head else body]
    chain = [st for st in (n.get("mechanism_steps") or []) if isinstance(st, dict)]
    line = _chain_line(chain)
    if line:
        out.append("  - 傳導:" + line)
    inval = _s(n.get("invalidation_signal"))
    if inval:
        out.append(f"  - 什麼會推翻它:{inval}")
    # 佐證等級收成一句話的尾巴(第二十輪 P2-7 的理由不變:「沒發生」與
    # 「只有一家說」不能長得一樣)—— 但不再自己佔一行。
    _CORR = {"single_source": "單一來源", "unverified": "未證實"}
    lvl = _CORR.get(_s(n.get("corroboration_assessment")))
    if lvl:
        out[0] = out[0] + f"（{lvl}）"
    # **受影響標的仍要列出來,但不再逐檔寫方向**(2026-08-18):使用者的原話
    # 是「不是整篇都是偏多什麼的」—— 方向與幅度在「各標的合計影響」那一段
    # (那一段的名字就是在回答方向)。這裡留的是兩件別處沒有的事:
    # 這則新聞牽動到誰(橫向),以及**哪一個只是推測性傳導**
    # (第三十二輪 P1-3 的揭露不能因為改版而消失)。
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
