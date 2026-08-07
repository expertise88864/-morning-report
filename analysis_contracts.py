# -*- coding: utf-8 -*-
"""**跨模組共用的結構契約判準**(第二十四輪 P1-5/P1-6 拆出)。

搬出來的理由就是那兩個缺陷的形狀:同一個契約被三個模組各自寫死一次,
於是它們**互相矛盾而沒有人發現**。

  * 「昨夜三大重點」的條數:schema 是一般 array、驗證器不管數量、
    renderer 取 `[:3]` —— 三個地方各有一份定義,結果 0/1-2/4+ 全都能過。
  * 可合法駁回的事件群:覆蓋率契約說「只有必分析清單能駁回」,而
    top-event 與總經發布契約要求「前三名/第二個發布要嘛具名要嘛駁回」——
    一個不在必分析清單的 top-3 事件,模型兩邊都做不對。

**判準只能有一份。** 這個模組不 import 分析側的任何模組(它是被 import 的
那一端),避免把循環相依換個方向再長回來。
"""
from __future__ import annotations

#: 「昨夜三大重點」的條數上限。**驗證器、renderer、段落標題是同一個數字。**
KEY_DRIVERS_REQUIRED = 3


def key_drivers_required(packet=None):
    """今天要幾條「重點」:**恰好 `min(3, 合格事件群數)`**;沒有分母時回 `None`。

    上限 3 是硬的(呼叫端另外驗):第四條會被 renderer 靜默隱藏,而驗證器
    先前會把它當成已處理 —— 讀者永遠看不到,卻計入「都寫了」。

    下限刻意**不是**固定的 3:清淡的一天只有兩個真事件時,強迫湊出第三條
    等於要模型把價格變化寫成事件,那正是這個 repo 反覆拒絕的東西
    (fixture 的原話:「湊一段不會讓分析更深」)。

    **分母只能來自 packet**(Python 計分後的 `top_events`),不能由模型自評 ——
    拿不到 packet 就回 `None`:不猜比猜錯好。

    **0 是合法答案,不是「不知道」**(外審 P1-5)。上一版在 `n == 0` 時
    一併回 `None`,把「今天真的沒有合格事件」與「沒有分母可判斷」混成同
    一件事 —— 於是條數檢查整段跳過、renderer 的 `or 3` 又把它當 fallback,
    「今天沒有三大事件」被迫變成虛構的三條。**只有拿不到 packet /
    `top_events` 才是 `None`。**
    """
    if not isinstance(packet, dict):
        return None
    top = packet.get("top_events")
    if not isinstance(top, dict):
        return None
    return min(KEY_DRIVERS_REQUIRED, len(top.get("top_cluster_ids") or []))


def dismissable_cluster_ids(packet) -> set:
    """**可以合法駁回的事件群** = 必分析 ∪ 計分前三 ∪ 總經發布。

    先前覆蓋率契約只認必分析清單,而另外兩套契約要求計分前三與第二個以後的
    總經發布「要嘛具名、要嘛駁回」—— 三套契約用三個不同的集合,模型照著
    其中一套做就會違反另一套。
    """
    pk = packet or {}
    out = {str(c) for c in
           ((pk.get("news_clusters") or {}).get("required_cluster_ids") or [])}
    top = pk.get("top_events")
    if isinstance(top, dict):
        out |= {str(c) for c in (top.get("top_cluster_ids") or [])}
    graph = pk.get("event_graph") or {}
    out |= {str(c) for c in (graph.get("macro_release_cluster_ids") or [])}
    out.add(str(graph.get("macro_release_cluster_id") or ""))
    return out - {""}


def top_drivers(drivers, packet):
    """依**今日應有條數**切片 —— renderer 與驗證器用同一個判準,不各自寫死。

    **不得寫 `or KEY_DRIVERS_REQUIRED`**(外審 P1-5):`0` 是合法答案而它是
    falsy,那個 `or` 會把「今天沒有合格事件」變成「顯示三條」。只有 `None`
    (拿不到分母)才退回硬上限。
    """
    want = key_drivers_required(packet)
    return list(drivers or [])[:KEY_DRIVERS_REQUIRED if want is None else want]


def key_driver_count_problems(obj, packet) -> list:
    """條數契約的問題清單。**上限與 packet 無關**(多的一定被 renderer 隱藏);
    **下限要有分母才驗**,而分母只能來自 packet —— 沒有就不猜。"""
    drivers = [d for d in ((obj or {}).get("key_drivers") or [])
               if isinstance(d, dict)]
    want = key_drivers_required(packet)
    if len(drivers) > KEY_DRIVERS_REQUIRED:
        return [f"key_drivers 有 {len(drivers)} 條,超過 {KEY_DRIVERS_REQUIRED} 條"
                f" —— 第 {KEY_DRIVERS_REQUIRED + 1} 條以後只會被靜默隱藏"]
    if want is not None and len(drivers) != want:
        return [f"key_drivers 有 {len(drivers)} 條,今天要恰好 {want} 條 —— "
                "少了讀者看不出被省略"
                + ("(合格事件群不足三個,所以要求的是全部 —— 湊一段不會讓分析更深)"
                   if want < KEY_DRIVERS_REQUIRED else "")]
    return []


def _claims_by_id(obj) -> dict:
    return {str(c.get("claim_id") or ""): c
            for c in ((obj or {}).get("claim_audit") or []) if isinstance(c, dict)}


def _cluster_ids(packet) -> set:
    return {str(c.get("cluster_id") or "")
            for c in (((packet or {}).get("news_clusters") or {}).get("clusters") or [])
            if isinstance(c, dict)} - {""}


def reference_problems(obj, packet) -> list:
    """**結構化引用的完整性**(第二十四輪 P1-8/P1-9)。

    schema 保證得了「有這一格」,保證不了「這一格指到的東西真的存在、
    而且指對了」。先前缺的四項各自都能讓一段沒有根據的話進信:

      * `asset_net_effects.claim_ids` 可以是空陣列 —— 「2330 合計偏多」
        於是可以完全沒有任何被稽核的主張支撐;
      * 淨效果引用的主張**與這個標的無關**(方向/標的對不上)也照過;
      * `offsetting_cluster_ids` 指到不存在的事件群不會被發現;
      * `shared_driver_notes.cluster_ids` 同上,而共用驅動的說明正是
        「為什麼不算重複計權」的唯一根據。

    這裡只驗**指涉**(存在、對得上),語意品質由既有的其他判準負責。
    """
    out: list = []
    if not isinstance(obj, dict):
        return out
    claims = _claims_by_id(obj)
    known_clusters = _cluster_ids(packet)

    for n in (obj.get("asset_net_effects") or []):
        if not isinstance(n, dict):
            continue
        aid = str(n.get("asset_id") or "")
        direction = str(n.get("net_direction") or "")
        cids = [str(c) for c in (n.get("claim_ids") or [])]
        if not cids:
            out.append(f"asset_net_effects[{aid}] 沒有引用任何 `claim_ids` —— "
                       "「合起來是利多還是利空」是會進信的判斷,"
                       "它必須站在被稽核過的主張上")
        missing = [c for c in cids if c not in claims]
        if missing:
            out.append(f"asset_net_effects[{aid}] 引用了不存在的主張:{missing}")
        # **引用的主張要真的關於這個標的、而且方向對得上。**
        # 外審 P1-7 抓到兩個繞法,而第三個是型別錯誤:
        #   (a) `asset_scope` 是**陣列**,上一版 `str(...) in ("", aid)`
        #       把整個清單字串化 —— `"[\'2330\']"` 永遠不等於 `"2330"`,
        #       於是**正確標註的主張被拒、沒標範圍的反而過**,判準剛好相反;
        #   (b) 空 scope 被當成「支援所有標的」—— 一句泛稱
        #       於是可以替任何一檔的淨判斷背書;
        #   (c) 完全不看 claim 的 `direction` —— 一條 2330 bearish 的主張
        #       可以支撐「2330 合計偏多」,只要標的一樣。
        elif cids and aid and direction not in ("", "unknown"):
            same_asset = [c for c in cids if _claim_covers_asset(claims[c], aid)]
            if not same_asset:
                out.append(
                    f"asset_net_effects[{aid}] 引用的主張沒有一條是關於 {aid} 的"
                    " —— 淨方向要站在這個標的自己的主張上"
                    "(泛稱或 `market-wide` 不算指定這一檔)")
            elif direction in ("bullish", "bearish") and not [
                    c for c in same_asset
                    if str((claims[c] or {}).get("direction") or "") == direction]:
                out.append(
                    f"asset_net_effects[{aid}] 的淨方向是 {direction},"
                    f"而引用的 {aid} 主張沒有一條是同方向的 —— "
                    "淨判斷不能只靠反方向的主張撐著")
            # 第二十五輪 P1-5:**「淨」的意思是比較過雙方。**
            # 上一版只要求「至少一條同向」,於是 A(利多)與 B(利空)
            # 都存在時,可以只引用 A —— `offsetting_cluster_ids` 說有兩邊,
            # `claim_ids` 卻只分析一邊,`why` 是自由文字。那不是淨效果,
            # 是選邊之後補一句理由。
            # **鍵要用 canonical**:衝突偵測回的是別名組代表(「台積電」),
            # 而這裡的 `aid` 是輸出寫的原樣(「2330」)。
            if _eg_conflicts(obj).get(_canon_asset(aid)):
                dirs = {str((claims[c] or {}).get("direction") or "")
                        for c in same_asset}
                missing = {"bullish", "bearish"} - dirs
                if missing:
                    out.append(
                        f"asset_net_effects[{aid}] 引用的主張只有 "
                        f"{sorted(dirs & {'bullish', 'bearish'})} 這一側 —— "
                        f"今天 {aid} 同時有利多與利空,淨判斷要**兩側各至少"
                        "一條主張**才證明得出比較過(缺 "
                        f"{sorted(missing)})")
        # `offsetting_cluster_ids` 的語意是「互相抵銷」,而**一個群抵銷不了
        # 任何東西**(外審 P1-7.3)。非空時要求:至少兩個、都存在、而且
        # 與 Python 端衝突偵測算出來的那組**完全一致** —— 讓模型自選子集
        # 等於它自己決定什麼叫衝突。
        offs = [str(c) for c in (n.get("offsetting_cluster_ids") or [])]
        # 第二十五輪 P1-4:**空陣列先前整段跳過。** 三道檢查全包在
        # `if offs:` 裡,於是留空就同時避開「至少兩群」「群要存在」
        # 「要與實際衝突一致」—— 而另一側只要求 `why` 非空。
        # **先算 expected,再比 submitted**:算得出衝突時,空陣列必敗。
        expect = _offsetting_clusters_for(obj, packet, aid)
        if expect is not None:
            if set(offs) != expect:
                out.append(
                    f"asset_net_effects[{aid}] 的 `offsetting_cluster_ids` "
                    f"{sorted(set(offs)) or '(空)'} 與本日實際衝突的事件群 "
                    f"{sorted(expect)} 不一致 —— 哪些事互相抵銷由資料決定,"
                    "不由輸出自選;留空等於沒有比較過")
        elif offs:
            if known_clusters:
                bad = [c for c in offs if c not in known_clusters]
                if bad:
                    out.append(
                        f"asset_net_effects[{aid}] 的 `offsetting_cluster_ids` "
                        f"指到不存在的事件群:{bad}")
            if len(set(offs)) < 2:
                out.append(
                    f"asset_net_effects[{aid}] 的 `offsetting_cluster_ids` "
                    f"只有 {len(set(offs))} 個事件群 —— 「互相抵銷」"
                    "至少要兩件事")

    syn = obj.get("cross_market_synthesis") or {}
    groups = _shared_driver_groups(packet)
    for note in (syn.get("shared_driver_notes") or []):
        if not isinstance(note, dict):
            continue
        cids = [str(c) for c in (note.get("cluster_ids") or [])]
        # 第二十五輪 P1-6:**空 cluster_ids 先前整段跳過**,而另一側
        # 只用 driver 名稱判斷「已處理」—— 於是填一個正確的 driver、
        # cluster_ids 留空,就同時滿足「有 note」與「避開 exact-match」。
        if groups is not None and len(set(cids)) < 2:
            out.append(
                f"shared_driver_notes[{note.get('driver')}] 的 `cluster_ids` "
                f"只有 {len(set(cids))} 個 —— 「為什麼不算重複計權」講的是"
                "**哪幾件事**共用驅動,少於兩件就沒有東西要調和")
        if known_clusters:
            bad = [c for c in cids if c not in known_clusters]
            if bad:
                out.append(
                    f"shared_driver_notes[{note.get('driver')}] 的 `cluster_ids` "
                    f"指到不存在的事件群:{bad} —— 「為什麼不算重複計權」"
                    "的根據要指得到真的東西")
                continue
        # **存在不等於共用同一個驅動**(外審 P1-8):兩個真實但毫無關係的
        # 事件群一樣可以被宣稱為共同驅動,而這一段的用途正是
        # 「所以不算重複計權」。要求與 Python 端算出來的某一組完全一致。
        if groups is not None and cids:
            if len(set(cids)) < 2:
                out.append(
                    f"shared_driver_notes[{note.get('driver')}] 只列了一個事件群"
                    " —— 「共用同一個驅動」至少要兩件事")
            elif not any(set(cids) == g["ids"] for g in groups):
                out.append(
                    f"shared_driver_notes[{note.get('driver')}] 的 `cluster_ids` "
                    f"{sorted(set(cids))} 不是本日任何一組共用驅動 —— "
                    f"本日共 {len(groups)} 組")
            else:
                want = next(g["driver"] for g in groups if set(cids) == g["ids"])
                got = str(note.get("driver") or "")
                if want and got and got != want:
                    out.append(
                        f"shared_driver_notes 宣稱的驅動是 {got!r},"
                        f"而這組事件群在本日被歸類為 {want!r}")
    return out


#: 泛稱的範圍不算指定標的(與 `analysis_validate` 同源的判準:
#: 「整體市場級別寫 `market-wide`」,而它**不能**替某一檔背書)。
_GENERIC_SCOPE = frozenset({"market-wide", "市場", "大盤", "整體", "台股", "美股"})


def _claim_covers_asset(claim, asset_id: str) -> bool:
    """這條主張**指名**了這個標的嗎。

    `asset_scope` 是陣列;空陣列與泛稱都**不算**指名 —— 否則一句
    「整體偏多」就能替任何一檔的淨判斷背書(外審 P1-7.1)。
    別名同組視為同一檔(`2330` 與「台積電」)。
    """
    import entity_alias as _ea
    scope = (claim or {}).get("asset_scope")
    if isinstance(scope, str):
        scope = [scope] if scope else []
    names = [str(x).strip() for x in (scope or []) if str(x).strip()]
    named = [x for x in names if x not in _GENERIC_SCOPE]
    if not named or not asset_id:
        return False
    gi = _ea.group_of(asset_id)
    if gi >= 0:
        return any(_ea.group_of(x) == gi or x == asset_id for x in named)
    return asset_id in named


def _shared_driver_groups(packet):
    """本日 Python 算出來的共用驅動組 `[{ids, driver}]`;
    拿不到 `event_graph` 就回 `None`(沒有分母就不驗這一條)。"""
    graph = (packet or {}).get("event_graph") if isinstance(packet, dict) else None
    if not isinstance(graph, dict) or "shared_driver_groups" not in graph:
        return None
    out = []
    for g in (graph.get("shared_driver_groups") or []):
        if isinstance(g, dict):
            ids = {str(c) for c in (g.get("cluster_ids") or [])}
            if ids:
                out.append({"ids": ids, "driver": str(g.get("driver") or "")})
    return out


def _canon_asset(aid) -> str:
    import entity_alias as _ea
    gi = _ea.group_of(str(aid))
    return _ea.ALIAS_GROUPS[gi][0] if gi >= 0 else str(aid)


def _eg_conflicts(obj) -> dict:
    """Python 端算出來的方向衝突(`{標的: [新聞 ID]}`)。"""
    import event_graph as _eg
    return _eg.conflicting_assets(obj)


def _offsetting_clusters_for(obj, packet, asset_id: str):
    """這個標的今天**實際**互相衝突的事件群集合;算不出來回 `None`。

    衝突由 `event_graph.conflicting_assets` 依輸出自己的
    `top_news_analysis` 判定(同一標的兩個相反方向),它回的是新聞 ID;
    再經 packet 的分群對回事件群 —— 兩邊都要有才驗得動。
    """
    clusters = (((packet or {}).get("news_clusters") or {}).get("clusters")
                if isinstance(packet, dict) else None)
    if not clusters:
        return None
    try:
        import entity_alias as _ea
        import event_graph as _eg
        found = _eg.conflicting_assets(obj)
        hits = found.get(asset_id)
        if hits is None:
            gi = _ea.group_of(asset_id)   # 偵測端已正規化到組代表
            hits = found.get(_ea.ALIAS_GROUPS[gi][0]) if gi >= 0 else None
    except Exception:                                   # noqa: BLE001
        return None
    if hits is None:
        return None
    sids = {str(s) for s in hits}
    got = {str(c.get("cluster_id")) for c in clusters
           if isinstance(c, dict)
           and sids & {str(m) for m in (c.get("member_source_ids") or ())}}
    # **對不回任何事件群 = 算不出來,不是「答案是空集合」。**
    # (被分析的新聞不在這份 packet 的分群裡時就會這樣;拿空集合去要求
    # 「完全一致」等於用一個不知道的東西去否定模型。)
    return got or None
