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
        # **引用的主張要真的關於這個標的**:方向靠一條、標的靠另一條,
        # 等於沒有任何一條真的支撐這個淨判斷。
        elif cids and aid and direction not in ("", "unknown"):
            same_asset = [c for c in cids
                          if str((claims[c] or {}).get("asset_scope") or "") in ("", aid)]
            if not same_asset:
                out.append(
                    f"asset_net_effects[{aid}] 引用的主張沒有一條是關於 {aid} 的"
                    " —— 淨方向要站在這個標的自己的主張上")
        if known_clusters:
            bad = [str(c) for c in (n.get("offsetting_cluster_ids") or [])
                   if str(c) not in known_clusters]
            if bad:
                out.append(
                    f"asset_net_effects[{aid}] 的 `offsetting_cluster_ids` "
                    f"指到不存在的事件群:{bad}")

    syn = obj.get("cross_market_synthesis") or {}
    for note in (syn.get("shared_driver_notes") or []):
        if not isinstance(note, dict) or not known_clusters:
            continue
        bad = [str(c) for c in (note.get("cluster_ids") or [])
               if str(c) not in known_clusters]
        if bad:
            out.append(
                f"shared_driver_notes[{note.get('driver')}] 的 `cluster_ids` "
                f"指到不存在的事件群:{bad} —— 「為什麼不算重複計權」"
                "的根據要指得到真的東西")
    return out
