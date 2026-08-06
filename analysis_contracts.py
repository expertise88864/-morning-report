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
    """
    top = (packet or {}).get("top_events")
    if isinstance(top, dict):
        n = len(top.get("top_cluster_ids") or [])
        if n:
            return min(KEY_DRIVERS_REQUIRED, n)
    return None


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
    """依**今日應有條數**切片 —— renderer 與驗證器用同一個判準,不各自寫死。"""
    return list(drivers or [])[:key_drivers_required(packet) or KEY_DRIVERS_REQUIRED]


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
