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
    for cid in sorted(set(dismissed) - set(need)):
        out.append(f"dismissed_events 宣稱駁回 {cid!r},而它不在本報的必分析清單")
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
    # **回指要連對,不只是連上。** 相容 = 主張的尺度**不短於**段落的
    # 尺度:段落宣告了一個期間,就要有主張講到那個期間。
    # (第二十一輪 P1-6:上一版的註解寫反了,與程式和錯誤訊息矛盾。)
    for sec, want in _section_horizons(obj).items():
        cited = [by_id[x] for x in mappings.get(sec, ()) if x in by_id]
        if want and cited and not any(
                _cm.horizon_covers(want, c.get("horizon")) for c in cited):
            out.append(
                f"{sec} 的時間尺度是 {want},而它引用的主張全都比它更短"
                f"({sorted({str(c.get('horizon')) for c in cited})})")
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
        if want_dir and cited and not any(
                str(c.get("direction") or "") == want_dir for c in cited):
            out.append(
                f"key_drivers[{i}] 的方向是 {want_dir},而它引用的主張"
                f"沒有一條同向({sorted({str(c.get('direction')) for c in cited})})"
                " —— 讀者最先看到的三條不能與稽核相反")
        # 證據也要有交集:引用一條「講別件事」的主張,方向對上也沒有意義。
        own = {str(x) for x in (d.get("evidence_ids") or [])}
        if own and cited and not any(
                own & {str(x) for x in (c.get("evidence_ids") or [])}
                for c in cited):
            out.append(
                f"key_drivers[{i}] 與它引用的主張沒有共同證據 ——"
                "回指要指向真的在講同一件事的那一條")
    # **孤兒主張**:寫進稽核卻沒有任何一段用到。它不是根據,是配菜。
    referenced = _cm.referenced_claim_ids(obj)
    for c in claims:
        cid = str(c.get("claim_id") or "")
        if c.get("materiality") == "high" and cid and cid not in referenced:
            out.append(f"claim_audit 的高重要性主張 {cid!r} 沒有被任何段落引用")
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

