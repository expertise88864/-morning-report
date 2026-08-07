# -*- coding: utf-8 -*-
"""**該談的談了嗎、談過的接得起來嗎**(第十八輪拆出)。

`analysis_validate` 問的是「引用的 ID 存不存在」;這裡問的是三個
**完整性**問題,而它們的失效方式一模一樣 —— 都是「看起來有做」:

  * **必分析事件**:分析一則次要新聞就通過,而重要性是模型自評的;
  * **同向訊號**:橫向先前只嚴格處理矛盾,同向放在自由文字裡,
    「有沒有把同一個底層驅動重複計權」驗不了;
  * **claim 圖**:稽核非空且合法,而信裡真正寫出來的立場、已反映/未反映、
    投資組合影響**沒有任何東西回指它** —— 它是一座孤島。

三者都不是「形狀對不對」,而是「有沒有真的做完」。
"""
from __future__ import annotations


#: 用來打發的套語。**它們是標籤,不是理由** —— 一句 15 字以內、
#: 而且只由這些詞組成的句子,等於沒有回答「為什麼不談」。
_BOILERPLATE = ("影響有限", "市場已消化", "已反映", "不具實質影響", "無重大影響",
                "例行公告", "不重要", "影響輕微", "無關本日", "中性")


#: `asset_scope` 不算範圍的泛稱。**與 `analysis_validate._GENERIC_ASSETS`
#: 是同一份清單**(從那裡取,避免兩份各自漂移 —— 這個 repo 栽過
#: 「同一個事實兩個名字」)。
def _generic_scope() -> frozenset:
    import analysis_validate as _av
    return _av._GENERIC_ASSETS


class _LazyGeneric(frozenset):
    """延遲取用:`analysis_validate` 會反向 import 本模組。"""

    def __contains__(self, item):        # noqa: D105
        return item in _generic_scope()


_GENERIC_SCOPE = _LazyGeneric()


def _cites_own_cluster(d: dict, cid: str, packet) -> bool:
    """駁回要引用**被駁回的那一群自己的新聞**(第二十輪 P2-2)。"""
    info = (packet or {}).get("news_clusters") or {}
    members: set = set()
    for c in (info.get("clusters") or []):
        if isinstance(c, dict) and str(c.get("cluster_id") or "") == cid:
            members = set(map(str, c.get("member_source_ids") or ()))
            break
    cited = {str(x) for x in (d.get("supporting_evidence_ids") or [])}
    return bool(cited & members)


def _is_boilerplate(why: str) -> bool:
    t = why.strip()
    return len(t) <= 15 and any(w in t for w in _BOILERPLATE)


#: 可合法駁回的集合住在 `analysis_contracts`(第二十四輪 P1-6)—— 三套契約
#: 必須用同一個集合,先前各自為政正是它們互相矛盾的原因。此處再匯出。
from analysis_contracts import dismissable_cluster_ids  # noqa: E402,F401


def _coverage_problems(obj, packet, analysed_ids) -> list:
    """**必分析事件的覆蓋率**(第十八輪 P1-3)。

    分母來自 packet(官方來源、多家同時報),不是模型自評的重要性 ——
    自評當分母時,「只分析一則次要新聞」與「該談的都談了」長得一樣。
    模型仍可主張某個事件今天不值得談,但**要留下理由**。
    """
    import news_clusters as _nc
    out: list = []
    info = packet.get("news_clusters") or {}
    need = list(info.get("required_cluster_ids") or [])
    if not need:
        return out
    groups = info.get("clusters") or []
    covered = {_nc.cluster_of(groups, sid) for sid in analysed_ids}
    dismissed = {str((d or {}).get("cluster_id") or ""): d
                 for d in (obj.get("dismissed_events") or [])
                 if isinstance(d, dict)}
    for cid in need:
        if cid in covered:
            continue
        d = dismissed.get(cid)
        if d is None:
            out.append(
                f"本報要求分析的事件 {cid} 既沒有分析、也沒有說為什麼不談"
                " —— 靜默略過與判斷不重要,在信裡長得一模一樣")
        elif not str(d.get("why_not_material") or "").strip():
            out.append(f"dismissed_events[{cid}] 沒有寫為什麼不值得分析")
        elif not str(d.get("revisit_trigger") or "").strip():
            # 第二十輪 P2-2:套語偵測靠字面,一個修飾詞就繞過。
            # 「什麼情況出現這個駁回就不成立」是機械化的判準 ——
            # 說不出回頭條件的駁回,與「懶得分析」分不開。
            out.append(f"dismissed_events[{cid}] 沒有寫 revisit_trigger ——"
                       "說不出什麼情況要回頭看,駁回就只是略過")
        elif not _cites_own_cluster(d, cid, packet):
            out.append(f"dismissed_events[{cid}] 的證據沒有引用"
                       "該事件群自己的新聞 —— 要證明你看過才能駁回")
        elif _is_boilerplate(str(d.get("why_not_material"))):
            # 第十九輪 P1-5:**「影響有限」不是理由,是換句話說。**
            # 只驗非空的話,駁回一則央行公告與駁回一則例行公告
            # 在檢查器眼裡一模一樣。
            out.append(
                f"dismissed_events[{cid}] 的理由只是套語 —— "
                "要說出這件事的哪個環節今天不會傳導到價格")
    for cid in sorted(set(dismissed) - dismissable_cluster_ids(packet)):
        out.append(f"dismissed_events 宣稱駁回 {cid!r},而它不在本報的"
                   "必分析清單、計分前三、也不是總經發布")
    # 同一個事件群分析兩次以上 —— **那不是更深,是同一條鏈改寫兩次**,
    # 而它會讓 `news_analyzed` 這個數字看起來變好。
    seen: dict = {}
    for sid in analysed_ids:
        cid = _nc.cluster_of(groups, sid)
        if cid:
            seen.setdefault(cid, []).append(sid)
    for cid, sids in sorted(seen.items()):
        if len(sids) > 1:
            out.append(
                f"{cid} 被分析了 {len(sids)} 次({sorted(sids)})——"
                "同一件事的不同報導要合併成一個分析單位")
    return out


def _alignment_problems(cms, packet, known) -> list:
    """同向訊號要逐筆解讀(第十八輪 P1-7)。"""
    import tension_refs as _tr
    out: list = []
    need = _tr.required_alignment_ids(packet.get("signal_tensions"))
    rows = [r for r in ((cms or {}).get("alignment_readings") or [])
            if isinstance(r, dict)]
    got = {str(r.get("alignment_id") or "") for r in rows}
    for x in sorted(need - got):
        out.append(f"同向訊號 {x} 沒有解讀 —— 橫向不能只處理矛盾")
    for x in sorted(got - need):
        out.append(f"alignment_readings 宣稱解讀了 {x!r},而今天沒有這筆同向訊號")
    seen = set()
    for r in rows:
        aid = str(r.get("alignment_id") or "")
        if aid in seen:
            out.append(f"alignment_readings 有重複的 {aid!r}")
            continue
        seen.add(aid)
        blank = [k for k in ("interpretation", "marginal_information",
                             "double_count_risk")
                 if not str(r.get(k) or "").strip()]
        if aid in need and blank:
            out.append(f"alignment_readings[{aid}] 的 {blank} 是空的")
        for i in (r.get("evidence_ids") or []):
            if str(i) not in known:
                out.append(f"alignment_readings[{aid}] 引用了不存在的證據 ID:{i!r}")
        # 第十九輪 P1-7:**只驗「合法」不驗「相關」。** 矛盾那一側早就
        # 要求引用該張力本身或兩側,同向這一側卻只要 ID 存在就過 ——
        # 於是「利率與科技股同向」可以拿一則航運新聞當證據,
        # 而整段橫向分析仍然是模型自由發揮。兩側用同一條規則。
        if aid in need:
            import analysis_stages as _ast
            if not _ast.both_sides_cited(
                    {"tension_id": aid, "evidence_ids": r.get("evidence_ids")},
                    packet):
                out.append(
                    f"alignment_readings[{aid}] 的證據沒有涵蓋這筆同向訊號"
                    " —— 要引用它本身,或兩側各至少一個")
    return out


def _claim_graph_problems(obj) -> list:
    """**每個重大結論說得出它靠哪幾條主張**,而且**連對了**。

    先前 `claim_audit` 是孤島:它非空且合法,而信裡真正寫出來的立場、
    已反映/未反映、投資組合影響**沒有任何東西回指它**。

    第二十輪 P2-5:段落清單改由 `claim_map` 生成 —— 先前四個消費者
    (驗證器、飽和率、加深保存、渲染)各自維護一份,schema 加了
    scenario / watch / key_driver 的回指之後**只有驗證器知道**。
    """
    import claim_map as _cm
    out: list = []
    claims = [c for c in (obj.get("claim_audit") or []) if isinstance(c, dict)]
    ids, dup = set(), set()
    for i, c in enumerate(claims):
        cid = str(c.get("claim_id") or "")
        if not cid:
            out.append(f"claim_audit[{i}] 沒有 claim_id,各段無法回指它")
        elif cid in ids:
            dup.add(cid)
        else:
            ids.add(cid)
    for cid in sorted(dup):
        out.append(f"claim_audit 有重複的 claim_id {cid!r} —— 回指會指向兩條")

    by_id = _cm.claims_by_id(obj)
    mappings = _cm.section_claim_mappings(obj)
    for sec, cited in sorted(mappings.items()):
        if len(cited) != len(set(cited)):
            out.append(f"{sec} 的 claim_ids 有重複 —— 同一條主張列兩次"
                       "不會讓根據變多,只會讓飽和度指標失真")
        for x in cited:
            if x not in ids:
                out.append(f"{sec} 的 claim_ids 指向不存在的主張 {x!r}")
        if not cited and claims:
            out.append(f"{sec} 沒有回指任何 claim —— "
                       "說不出這一段靠哪幾條主張,稽核就只是裝飾")
    # **回指要連對,不只是連上。** 相容由 `HORIZON_MATRIX` 逐格決定
    # (第二十二輪 P1-5):太短撐不起,差兩階也撐不起。訊息不再說
    # 「全都比它更短」—— 那句話對「差兩階更長」是錯的,而程式會擋它。
    for sec, want in _section_horizons(obj).items():
        cited = [by_id[x] for x in mappings.get(sec, ()) if x in by_id]
        if want and cited and not any(
                _cm.horizon_covers(want, c.get("horizon")) for c in cited):
            out.append(
                f"{sec} 的時間尺度是 {want},而它引用的主張沒有一條撐得起"
                f"這個尺度(引用的是 "
                f"{sorted({str(c.get('horizon')) for c in cited})},"
                f"相容的是 {_cm.horizons_compatible_with(want)})")
    for c in claims:
        cid = str(c.get("claim_id") or "")
        scope = [str(x).strip() for x in (c.get("asset_scope") or []) if str(x).strip()]
        if not scope:
            out.append(f"claim_audit[{cid}] 沒有 asset_scope —— "
                       "說不出在講誰的主張,回指到任何一段都成立")
        for a in scope:
            if a != "market-wide" and a in _GENERIC_SCOPE:
                out.append(f"claim_audit[{cid}] 的 asset_scope {a!r} 是泛稱 ——"
                           "整體市場級別請寫 `market-wide`")
    # 第二十一輪 P1-5:**key driver 進了 claim 圖,卻仍可與它引用的
    # claim 完全矛盾** —— 而那是 Email 最先看到的「昨夜三大重點」。
    # 逐條比對方向:引用的主張裡要有一條與這條重點同向。
    for i, d in enumerate(obj.get("key_drivers") or []):
        if not isinstance(d, dict):
            continue
        want_dir = str(d.get("direction") or "")
        cited = [by_id[x] for x in (d.get("claim_ids") or []) if str(x) in by_id]
        own = {str(x) for x in (d.get("evidence_ids") or [])}
        # 第二十二輪 P1-4:**兩個條件要落在同一條 claim 上。**
        # 分開驗的話,方向靠 c1 滿足、證據靠 c2 滿足 —— 而沒有任何一條
        # claim 真的支持這條重點(split-quantifier bypass,實測確認)。
        def _supports(c):
            dir_ok = (not want_dir
                      or str(c.get("direction") or "") == want_dir)
            ev_ok = (not own
                     or bool(own & {str(x)
                                    for x in (c.get("evidence_ids") or [])}))
            return dir_ok and ev_ok
        if cited and (want_dir or own) and not any(_supports(c) for c in cited):
            out.append(
                f"key_drivers[{i}] 引用的主張沒有一條**同時**同向且共享證據"
                " —— 方向靠一條、證據靠另一條,等於沒有任何一條真的支持它")
    # **孤兒主張**:寫進稽核卻沒有任何一段用到。它不是根據,是配菜。
    referenced = _cm.referenced_claim_ids(obj)
    for c in claims:
        cid = str(c.get("claim_id") or "")
        if c.get("materiality") == "high" and cid and cid not in referenced:
            out.append(f"claim_audit 的高重要性主張 {cid!r} 沒有被任何段落引用")
    return out


def top_event_problems(obj, packet) -> list:
    """**「昨夜三大重點」要是三個事件**(重構規格 Commit C)。

    候選由 Python 從資料算出來(`event_score.rank`),純價格變化整批排除。
    模型可以不談某一個候選 —— 但要在 `dismissed_events` 說明理由,
    **靜默略過與判斷不重要,在信裡長得一模一樣**(這是這個 repo 既有的
    判準,見 `news_clusters` 的模組說明)。
    """
    out: list = []
    if not isinstance(packet, dict) or not isinstance(obj, dict):
        return out
    te = packet.get("top_events")
    if not isinstance(te, dict):
        return out                      # 舊呼叫端沒有這一段,不判
    want = [str(x) for x in (te.get("top_cluster_ids") or [])]
    if not want:
        return out                      # 今天沒有夠格的事件 —— 不是問題
    excluded = {str(x) for x in (te.get("excluded_price_moves") or [])}
    drivers = [d for d in (obj.get("key_drivers") or []) if isinstance(d, dict)]
    named = {str(d.get("cluster_id") or "") for d in drivers} - {""}
    for i, d in enumerate(drivers):
        cid = str(d.get("cluster_id") or "")
        if cid and cid in excluded:
            out.append(f"key_drivers[{i}] 指到的 {cid} 是**純價格變化**,"
                       "不是事件 —— 價格是別的事件造成的結果,"
                       "三大重點要寫造成它的那件事")
    known = {str(c.get("cluster_id") or "")
             for c in ((packet.get("news_clusters") or {}).get("clusters") or [])
             if isinstance(c, dict)}
    for i, d in enumerate(drivers):
        cid = str(d.get("cluster_id") or "")
        if cid and cid not in known and cid not in excluded:
            out.append(f"key_drivers[{i}] 的 cluster_id {cid!r} 不在今天的"
                       "事件群裡 —— 編造的引用比沒有引用更危險")
    # 第二十三輪 P1-6:**「昨夜三大重點」的每一條都要是事件。**
    # 上一版留一格給非新聞的驅動因子(「至少一半」),外審指出那與段落
    # 名稱和使用者原話(「昨夜三大**發生的**重大事件」)不符 ——
    # 非新聞的訊號自有去處(橫向綜合的 dominant_driver 與張力調和)。
    for i, d in enumerate(drivers):
        if str(d.get("cluster_id") or "") not in (known - excluded):
            out.append(f"key_drivers[{i}] 沒有指向任何真正的事件群 —— "
                       "「昨夜三大重點」的每一條都要是事件;非新聞的"
                       "驅動因子(籌碼、行情結構)請寫進橫向綜合的"
                       "主導因子或張力調和,不要佔事件卡的格子")
    # 第二十三輪:**前三名每一件**都要被採用或逐一說明,不只第一名 ——
    # 第 2、3 名靜默消失與「沒發生」在信裡長得一樣。
    dismissed = {str((x or {}).get("cluster_id") or "")
                 for x in (obj.get("dismissed_events") or [])
                 if isinstance(x, dict)}
    for cid in want:
        if cid not in named and cid not in dismissed:
            out.append(f"計分前三的事件 {cid} 既沒寫進 `key_drivers`(指名 "
                       "`cluster_id`)也沒寫進 `dismissed_events` 說明理由")
    return out


def event_graph_problems(obj, packet) -> list:
    """**事件之間的關係**(重構規格 Commit D)。三條:

      1. 方向相反的標的要給**淨效果** —— 兩段各自寫完就結束了,
         而讀者要的是「合起來是利多還是利空」。
      2. 共用同一個底層驅動的事件不得被當成**獨立確認** ——
         就業數據 → 降息預期 → 殖利率是同一件事的三個表現。
      3. 有總經發布的日子,情境樹的三個分支要**條件在同一個發布上** ——
         非農不是「一件會影響台股的事」,它是分岔本身。
    """
    import event_graph as _eg
    out: list = []
    if not isinstance(obj, dict):
        return out
    # ── 1) 淨效果
    conflicts = _eg.conflicting_assets(obj)
    import entity_alias as _ea

    def _canon(aid):
        gi = _ea.group_of(str(aid))
        return _ea.ALIAS_GROUPS[gi][0] if gi >= 0 else str(aid)
    nets = {_canon((x or {}).get("asset_id") or ""): x
            for x in (obj.get("asset_net_effects") or []) if isinstance(x, dict)}
    for aid in sorted(conflicts):
        n = nets.get(aid)
        if not n:
            out.append(f"{aid} 同時被寫成利多與利空({conflicts[aid]}),"
                       "卻沒有 `asset_net_effects` —— 兩段各自寫完就結束,"
                       "讀者不知道合起來是什麼")
        elif not str(n.get("why") or "").strip():
            out.append(f"asset_net_effects[{aid}] 沒有寫 `why` —— "
                       "淨方向要說得出哪一邊比較重、憑什麼")
    for aid, n in sorted(nets.items()):
        if aid and aid not in conflicts and str(
                n.get("net_direction") or "") not in ("", "unknown"):
            out.append(f"asset_net_effects[{aid}] 沒有方向衝突要調和 —— "
                       "沒有互相抵銷的標的不必列(湊一段不會讓分析更深)")
    if not isinstance(packet, dict):
        return out
    graph = packet.get("event_graph")
    if not isinstance(graph, dict):
        return out                      # 舊呼叫端沒有這一段,不判
    # ── 2) 共同驅動
    used = {str(d.get("cluster_id") or "")
            for d in (obj.get("key_drivers") or []) if isinstance(d, dict)}
    # 第二十五輪 P1-6:**「已處理」的身分是 (驅動, 群集)**,不是驅動名稱。
    # 只比名稱時,一則 `cluster_ids=[]` 的 note 就算處理過了。
    notes = {(str((x or {}).get("driver") or ""),
              frozenset(str(c) for c in (x.get("cluster_ids") or [])))
             for x in ((obj.get("cross_market_synthesis") or {})
                       .get("shared_driver_notes") or []) if isinstance(x, dict)}
    for g in (graph.get("shared_driver_groups") or []):
        if not isinstance(g, dict):
            continue
        hit = sorted(used & {str(c) for c in (g.get("cluster_ids") or [])})
        _handled = any(d == str(g.get("driver") or "") and len(cs) >= 2
                       and cs <= {str(c) for c in (g.get("cluster_ids") or [])}
                       for d, cs in notes)
        if len(hit) >= 2 and not _handled:
            out.append(
                f"三大重點裡有 {len(hit)} 件事共用同一個底層驅動"
                f"({g.get('label')}:{hit})—— 各加一次權重等於同一件事"
                f"說 {len(hit)} 次。請在 `cross_market_synthesis."
                f"shared_driver_notes` 寫 driver={g.get('driver')!r} "
                "並說明為什麼不算重複計權")
    # ── 3) 總經發布 → 聯合情境
    # 第二十三輪 P1-8:主發布之外的總經發布不得被忽略 —— CPI 與 Fed
    # 決議同日時,第二個發布要嘛進 key_drivers、要嘛被 dismissed。
    dismissed_all = {str((x or {}).get("cluster_id") or "")
                     for x in (obj.get("dismissed_events") or [])
                     if isinstance(x, dict)}
    named_all = {str(d.get("cluster_id") or "")
                 for d in (obj.get("key_drivers") or []) if isinstance(d, dict)}
    for extra in (graph.get("macro_release_cluster_ids") or [])[1:]:
        e = str(extra)
        if e and e not in named_all and e not in dismissed_all:
            out.append(f"今天有第二個總經發布({e}),它既不在三大重點、"
                       "也沒有被 dismissed —— 忽略一個總經發布與沒看到它,"
                       "在信裡長得一樣")
    # 第二十四輪 P1-7:**每一個總經發布都要條件在三個分支上,不只第一個。**
    # 先前只驗 `macro_release_cluster_id`(單數,排序後的第一個)——
    # CPI 與 Fed 同日時,情境樹可以完全只建在 CPI 上,而 Fed 只要「被具名
    # 或被駁回」就過關。那不是聯合情境,是把第二個發布降級成一則新聞:
    # 兩個發布的交叉組合(CPI 高 × Fed 鷹 / CPI 高 × Fed 鴿…)才是真正的分岔。
    # **被合法駁回的發布不必條件化** —— 駁回是模型說「今天這個真的不影響」的
    # 唯一出口,而它已經被品質門檻把關(非套語、要引用自身事件群、要寫
    # revisit_trigger)。兩邊都要求等於沒有出口。
    macros = [str(x) for x in (graph.get("macro_release_cluster_ids") or [])
              if str(x) and str(x) not in dismissed_all]
    if not macros and graph.get("macro_release_cluster_id"):
        m0 = str(graph["macro_release_cluster_id"])
        macros = [m0] if m0 not in dismissed_all else []
    if macros:
        by_id = {str(c.get("claim_id") or ""): c
                 for c in (obj.get("claim_audit") or []) if isinstance(c, dict)}
        tree = obj.get("scenario_tree")
        groups_by_id = {str(c.get("cluster_id")): c
                        for c in ((packet.get("news_clusters") or {}).get("clusters") or [])
                        if isinstance(c, dict)}
        for macro in macros:
            members = {str(m) for m in
                       ((groups_by_id.get(macro) or {}).get("member_source_ids") or [])}
            if not (members and isinstance(tree, dict)):
                continue
            for br in ("base", "bull", "bear"):
                blk = tree.get(br)
                if not isinstance(blk, dict):
                    continue
                cited = [by_id.get(str(x)) for x in (blk.get("claim_ids") or [])]
                ev = {str(e) for c in cited if isinstance(c, dict)
                      for e in (c.get("evidence_ids") or [])}
                if not (ev & members):
                    extra = ("(今天有 %d 個總經發布,每一個都要條件在三個分支上"
                             " —— 只條件在其中一個,另一個就被降級成一則新聞)"
                             % len(macros)) if len(macros) > 1 else ""
                    out.append(
                        f"今天有總經發布({macro}),而 scenario_tree.{br} "
                        "沒有任何一條主張引用它 —— 總經發布是情境樹的"
                        "**分岔本身**,三個分支若不條件在同一件事上,"
                        f"那是三件不同的事各自展開{extra}")
    return out


def _section_horizons(obj) -> dict:
    """**有自己時間尺度的段落**。其餘不做尺度判斷 ——
    「已反映/未反映」沒有一個屬於自己的期間。"""
    o = obj if isinstance(obj, dict) else {}
    out = {}
    stance = o.get("stance")
    if isinstance(stance, dict) and stance.get("time_horizon"):
        out["stance"] = str(stance["time_horizon"])
    for i, w in enumerate(o.get("watch_triggers") or []):
        if isinstance(w, dict) and w.get("horizon"):
            out[f"watch_triggers[{i}]"] = str(w["horizon"])
    return out

