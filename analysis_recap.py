# -*- coding: utf-8 -*-
"""**昨日觀點的閉環**(分析面縱深:延續事件要寫增量,不是重述)。

## 缺口

prompt 早就要求「延續中的事件要寫增量」—— 但特化路徑的模型只知道
`continuing_days = N`,**不知道本報昨天對這件事說了什麼**。沒有 diff 的
對象,「寫增量」就是一句無法執行的要求;驗證器也驗不了「今天是不是把
背景再講一次」。legacy prompt 有 `_format_narrative_delta`(昨日立場 +
五條標題),特化路徑什麼都沒有。

## 閉環三段

    分析成功 → `save()` 把 key_drivers 的(敘述, 方向, 實體)存進 state
      → 明天 `load()` + `view_for()` 把昨日觀點掛在對應的事件群上
      → `overlap()` 給 depth advisory 驗「重述度」,過高就用加深額度重寫

## 設計取捨

* **只存 key_drivers(首屏三條)**:它們是信裡最載重的判斷,也是
  「昨天的觀點」該指的東西。整份分析都存的話,明天的 packet 會被
  昨天的長文擠占預算。
* **以實體比對,不以 cluster_id**:cluster_id 是「群裡最小的
  source_item_id」,明天必然不同。實體 + 別名組(`entity_alias`)
  是跨日仍然穩定的身分 —— 與 `continuing_days` 用同一套比對哲學。
* **同日重跑不得自比**(`usable` 的日期守衛):手動 dispatch 會把
  「今天早上」存進 state,不濾掉就會拿今天比今天,產生假的
  「昨日觀點」—— legacy 的 `_format_narrative_delta` 已經踩過這個洞。
* **存檔失敗不斷晨報**:遞迴帳(昨日觀點)是加深,不是核心;
  但失敗要印出來,靜默的失敗明天才發現就晚了。
"""
from __future__ import annotations

import json
import sys

#: 一則觀點存這麼多字。**存的是判斷,不是全文** —— 太長會擠占明天的
#: payload 預算,而「昨天說了什麼」的重點在方向與量級,不在修辭。
STATEMENT_CHARS = 160

#: 觀點總數上限。首屏三條之外,次要事件(top_news_analysis)也存 ——
#: 但整份都存的話,明天的 packet 會被昨天的長文擠占預算;超過上限時
#: **主要的先留**(items 依重要性排序,截尾不截頭)。
MAX_ITEMS = 12

#: 重述門檻:今天敘述的 token 有六成以上昨天就說過,視為重述。
#: 訂 0.6 不是量出來的 —— 主體與事件名本來就會重複(那部分合理),
#: 六成以上代表連判斷句都在重複。外審可挑戰。
RESTATEMENT_OVERLAP = 0.6

#: 方向代碼 → 中文(進 packet 給模型看,也給 advisory 引用)。
_DIRECTION_ZH = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性",
                 "mixed": "多空並陳"}


def extract(analysis_obj, packet) -> dict:
    """從**通過驗證的**分析物件抽出要存的觀點(純函式,不碰檔案)。

    實體從 packet 的事件群成員收集 —— key_driver 只帶 `cluster_id`,
    而 cluster_id 明天就換號;實體才是跨日的身分。

    **次要事件也存**(`top_news_analysis` 的 `why_it_matters`):延燒到
    第三天的事件常常已經不在首屏 —— 只存首屏的話,恰恰是最需要 diff
    基準的長尾事件沒有基準。**每個事件群只存一則**,首屏的優先
    (兩者講同一件事時,首屏那句才是本報的正式判斷)。
    """
    obj = analysis_obj if isinstance(analysis_obj, dict) else {}
    pk = packet if isinstance(packet, dict) else {}
    by_id = {str(n.get("source_item_id")): n for n in (pk.get("news") or [])
             if isinstance(n, dict)}
    clusters = [c for c in ((pk.get("news_clusters") or {}).get("clusters")
                            or []) if isinstance(c, dict)]
    members_of = {str(c.get("cluster_id") or ""):
                  [str(m) for m in (c.get("member_source_ids") or [])]
                  for c in clusters}
    cluster_of = {m: cid for cid, ms in members_of.items() for m in ms}

    def _ents(member_ids) -> list:
        return sorted({str(e) for m in member_ids
                       for e in (by_id.get(m, {}).get("entities") or [])
                       if str(e).strip()})

    items, seen = [], set()

    def _add(stmt: str, direction, cluster_id: str, fallback_sid: str = ""):
        stmt = str(stmt or "").strip()
        if not stmt or (cluster_id and cluster_id in seen):
            return
        ents = _ents(members_of.get(cluster_id, [])) or (
            _ents([fallback_sid]) if fallback_sid else [])
        if not ents:
            return          # 明天接不回來的觀點是死重量,不存
        seen.add(cluster_id or f"__{len(items)}")
        items.append({"statement": stmt[:STATEMENT_CHARS],
                      "direction": str(direction or ""),
                      "entities": ents})

    for d in (obj.get("key_drivers") or []):
        if isinstance(d, dict):
            _add(d.get("statement"), d.get("direction"),
                 str(d.get("cluster_id") or ""))
    for n in (obj.get("top_news_analysis") or []):
        if isinstance(n, dict):
            sid = str(n.get("source_item_id") or "")
            _add(n.get("why_it_matters"), n.get("direction"),
                 cluster_of.get(sid, ""), fallback_sid=sid)
    return {"date": str(pk.get("target_session_date") or ""),
            "items": items[:MAX_ITEMS]}


def save(path, analysis_obj, packet) -> bool:
    """把今天的觀點寫進 state(**只留最新一天** —— 昨日觀點只需要
    上一次的)。失敗回 False 並印錯誤,不拋 —— 晨報不可因加深而斷。"""
    try:
        rec = extract(analysis_obj, packet)
        if not rec["items"]:
            return False
        import pathlib
        p = pathlib.Path(str(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception as e:                  # noqa: BLE001 - 加深不可斷晨報
        print(f"[recap] 昨日觀點存檔失敗(不影響晨報):{e}", file=sys.stderr)
        return False


def load(path) -> dict:
    """讀 state(讀不到回空 dict —— 降級方向是「沒有昨日觀點」)。"""
    try:
        import pathlib
        return json.loads(
            pathlib.Path(str(path)).read_text(encoding="utf-8")) or {}
    except Exception:                       # noqa: BLE001
        return {}


def usable(recap, target_session_date: str) -> list:
    """**同日重跑不得自比。** 只有日期**早於**今天交易日的觀點可用 ——
    等於今天的是同日重跑寫進去的,拿它比就是「今天比今天」,
    會產生假的強化/推翻。晚於今天的是時鐘或資料錯亂,同樣不可用。"""
    r = recap if isinstance(recap, dict) else {}
    date = str(r.get("date") or "")
    if not date or not target_session_date or date >= str(target_session_date):
        return []
    return [dict(it, date=date) for it in (r.get("items") or [])
            if isinstance(it, dict) and str(it.get("statement") or "").strip()]


def view_for(entities, items, sanitize=None) -> str:
    """這個事件群對得上的昨日觀點(對不上回空字串)。

    比對走 `entity_alias`(精確 + 別名組)—— 與 `continuing_days` 同一套
    身分哲學:「台積電」的觀點要接得上明天寫「TSMC」的群。
    """
    import entity_alias as _ea
    ents = {str(e) for e in (entities or ()) if str(e).strip()}
    keys = _ea.expand(ents)
    for it in (items or []):
        theirs = {str(e) for e in (it.get("entities") or [])}
        if ents & theirs or (keys & _ea.expand(theirs)):
            zh = _DIRECTION_ZH.get(str(it.get("direction") or ""), "")
            head = f"{it.get('date', '')}本報" + (f"({zh})" if zh else "")
            body = str(it.get("statement") or "")
            if callable(sanitize):
                body = str(sanitize(body))
            return f"{head}:{body}"[:STATEMENT_CHARS + 40]
    return ""


def restatements(analysis_obj, packet) -> list:
    """**與昨日觀點高度重複的敘述**(主要 + 次要事件),給 depth
    advisory 用。本體放這裡而不是 `analysis_depth`:重述的定義
    (存什麼、比什麼、門檻)整組屬於同一個閉環,拆兩處會各自漂移。

    次要事件比的是 `why_it_matters` —— 延燒事件掉出首屏之後,
    重述最常發生在這裡(首屏有三條的位置壓力,次要段沒有)。
    """
    obj = analysis_obj if isinstance(analysis_obj, dict) else {}
    pk = packet if isinstance(packet, dict) else {}
    clusters = [c for c in ((pk.get("news_clusters") or {}).get("clusters")
                            or []) if isinstance(c, dict)]
    yv = {str(c.get("cluster_id") or ""): str(c.get("yesterday_view") or "")
          for c in clusters}
    cluster_of = {str(m): str(c.get("cluster_id") or "") for c in clusters
                  for m in (c.get("member_source_ids") or [])}
    out = []

    def _check(where: str, stmt, cid: str):
        v = yv.get(cid, "")
        if v and overlap(stmt, v) >= RESTATEMENT_OVERLAP:
            out.append(f"{where} 的敘述與昨日觀點高度重複 —— 延續事件要"
                       "寫增量:強化/轉弱/翻轉之一,附**今天的**新證據,"
                       "不要把昨天的判斷再說一次")

    for d in (obj.get("key_drivers") or []):
        if isinstance(d, dict):
            _check(f"key_drivers 對 {d.get('cluster_id')}",
                   d.get("statement"), str(d.get("cluster_id") or ""))
    for i, n in enumerate(obj.get("top_news_analysis") or []):
        if isinstance(n, dict):
            _check(f"top_news_analysis[{i}]", n.get("why_it_matters"),
                   cluster_of.get(str(n.get("source_item_id") or ""), ""))
    return out


def overlap(statement, yesterday_view) -> float:
    """今天的敘述與昨日觀點的重述度(0~1)。

    給 depth advisory 用的**結構性**判準:借分群的 token 化
    (中文二元組 + 英文詞),算今天敘述被昨日觀點覆蓋的比例 ——
    量的是「有多少字昨天就說過」,不是語意。門檻由呼叫端訂。
    """
    from news_clusters import _tokens
    today = _tokens(str(statement or ""))
    yest = _tokens(str(yesterday_view or ""))
    if not today or not yest:
        return 0.0
    return len(today & yest) / len(today)
