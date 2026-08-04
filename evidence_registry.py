# -*- coding: utf-8 -*-
"""**證據圖:每個可引用的 ID 對應什麼、什麼時候的、能不能拿來推論。**

## 為什麼「一串合法字串」不夠(第十八輪 P1-1/P1-2)

先前的 registry 只回答一件事:**這個 ID 存不存在?** 於是引用檢查只擋得住
「編造的名字」,擋不住這幾種:

  * 引用了**昨天的**美股數字去解釋今天的台股開盤(美股休市那天);
  * 引用了一個存在但**與結論無關**的欄位;
  * 模型想談 00662 的估值、2330 的開盤預測、模型校準最近變差、
    持倉曝險集中 —— 而這些**在 registry 裡根本沒有對應的 ID**,
    它只能不引用(被擋)或拿一則新聞去頂(形式合法、語意錯誤)。

第三點是最實質的:**registry 只覆蓋了 packet 的一部分**,而沒被覆蓋的
那部分正好是最需要根據的判斷。

## 這裡刻意**不**做的事

`unit` 只從欄位名的後綴推,而且**推不出來就留空**。猜一個單位比留空更糟:
下游會拿它去格式化,而「4.32 %」與「4.32」是兩個不同的錯誤。
`quality` 同理 —— 只在 packet 真的有 `DATA_QUALITY` / `US_HOLIDAY` 這類
判準時才填,不從數值本身臆測。

**`usable_for_inference=False` 不代表丟掉。** 它仍然是合法引用 ——
談「今天美股沒開,所以外部定價的參考性下降」本身就需要引用那個欄位。
不可用的意思是:**拿它當今天的方向證據要被擋**(見 `analysis_validate`)。
"""
from __future__ import annotations

from typing import Optional

#: 欄位名後綴 → 單位。**只放看得出來的**,推不出來留空。
_UNIT_SUFFIX = (
    ("_pct", "%"), ("pct", "%"), ("_bps", "bps"), ("_lots", "lots"),
    ("_ratio", "ratio"), ("_yoy", "%"), ("_amount", "TWD"),
    ("oi_net", "lots"), ("advance_ratio", "%"), ("yield", "%"),
)

#: **美股/美元側的區塊。** 美股休市那天這些是上一個交易日的延續值,
#: 拿來解釋今天的台股開盤不同步 —— 沿用 11 維立場分的同一個判準。
_US_BLOCKS = ("QQQ", "TSM", "SPY", "MACRO", "MACRO_VINTAGE", "SEC_FILINGS")

#: 不當證據用的診斷區塊(它們談的是**資料本身**,不是市場)。
_NON_EVIDENCE = ("DATA_QUALITY", "SOURCE_HEALTH", "SOURCE_DATA_CHECKS",
                 "HEALTH_WARNINGS", "ALERTS", "HISTORY")

#: 遞迴深度上限。`SECTOR_HEAT.sectors.<產業>.leaders.<代號>.pct` 正好第五層。
_MAX_DEPTH = 5

#: 清單裡用來當穩定路徑的識別欄位(索引會隨排序漂移)。
_ID_FIELDS = ("code", "id", "symbol", "name", "ticker")


def _unit(field: str) -> str:
    low = str(field or "").lower()
    for suffix, unit in _UNIT_SUFFIX:
        if low.endswith(suffix):
            return unit
    return ""


#: 字串葉節點的長度上限。**標籤是證據,散文不是。**
#: `MARKET_REGIME.label = "risk-on"`、`MA200_STATUS.status` 這種要引用得到;
#: 而公報全文、事件敘述那種幾百字的區塊,引用它說明不了任何具體事實 ——
#: 讓它合法只會讓引用檢查變成橡皮圖章(區塊本身的 ID 仍然引用得到)。
_MAX_STRING_LEAF = 60


def _scalar(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, str):
        return 0 < len(v) <= _MAX_STRING_LEAF
    return isinstance(v, (int, float))


def _walk(node, prefix: str, out: dict, meta: dict, depth: int) -> None:
    """把一棵樹展成 `{路徑: 值}`。**只收純量葉子** —— 引用一整個 dict
    沒有意義,而讓它合法會使引用檢查失去作用。"""
    if depth > _MAX_DEPTH:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            key = str(k)
            if _scalar(v) or isinstance(v, (dict, list)):
                _walk(v, f"{prefix}.{key}" if prefix else key, out, meta, depth + 1)
    elif isinstance(node, list):
        for item in node:
            if not isinstance(item, dict):
                continue
            ident = next((str(item[f]) for f in _ID_FIELDS
                          if item.get(f) not in (None, "")), None)
            if ident:
                _walk(item, f"{prefix}.{ident}" if prefix else ident,
                      out, meta, depth + 1)
    elif _scalar(node):
        out[prefix] = node


def _entries(tree, root: str, meta: dict) -> dict:
    """`root` 是完整的 ID 前綴(`market:QQQ` 或 `valuation:`)。

    命名空間與路徑用不同的分隔符是刻意的:`market:QQQ.change_pct` 一眼
    看得出「哪一種證據」與「樹裡的哪一格」,而全用同一個符號就分不開了。
    """
    flat: dict = {}
    _walk(tree, "", flat, meta, 0)
    join = "" if root.endswith(":") else "."
    out = {}
    for path, val in flat.items():
        # **root scalar 的路徑是空字串。** 先前被 `if path` 濾掉,於是
        # `market:USDTWD_prev`(整個 block 就是一個數字)在 registry 裡
        # 只剩下一個 `value=None` 的殼 —— ID 存在、值不見了。
        # 引用它的模型會通過檢查,而檢查器根本不知道那個數字是多少。
        key = f"{root}{join}{path}" if path else root.rstrip(":")
        out[key] = dict(meta, value=val,
                        unit=("" if isinstance(val, str)
                              else _unit((path or key).rsplit(".", 1)[-1])))
    return out


def registry(packet: Optional[dict]) -> dict:
    """`{evidence_id: {value, unit, as_of, session, source, quality,
    usable_for_inference, why_unusable}}`。

    **命名空間就是「這是哪一種東西」**:引用一個 `market:` 去支持一則
    新聞的因果,與引用 `n3`,在檢查器眼裡本來就該是兩件事。
    """
    pk = packet if isinstance(packet, dict) else {}
    as_of = str(pk.get("as_of") or "")
    session = str(pk.get("target_session_date") or "")
    market = pk.get("market") if isinstance(pk.get("market"), dict) else {}
    us_stale = bool((market.get("US_HOLIDAY") or {}).get("detected"))
    out: dict = {}

    # 1. 新聞。**每則都有自己的時間與來源** —— 那正是 market 側缺的東西。
    for n in (pk.get("news") or []):
        if not isinstance(n, dict) or not n.get("source_item_id"):
            continue
        out[str(n["source_item_id"])] = {
            "value": None, "unit": "",
            "as_of": str(n.get("published") or n.get("published_at") or ""),
            "session": session,
            "source": str(n.get("source") or ""),
            "quality": str(n.get("source_grade") or ""),
            # 新聞**有**自己的時間 —— 這是唯一一類說得出來的。
            "as_of_precision": "source", "observed_session": "",
            "usable_for_inference": True, "why_unusable": "",
        }

    # 2. 行情。逐區塊,因為**新鮮度是逐區塊的**(美股休市只影響美股側)。
    # **`as_of` 是 packet 級的,不是每個欄位自己的。** 先前每一格都掛上
    # packet 的 as_of 與 target session —— 那是**假精確**:QQQ 可能是前一個
    # 美股交易日、台指期是今天、法說會摘要是上週,而它們全部長得像
    # 「06:00 觀測、屬於 2026-08-05 這一盤」。模型因此會把不同交易日的
    # 數字當成同步的橫向訊號。說不出來就要說「說不出來」。
    tw_session = str((market.get("LAST_TRADING_SESSION") or {}).get("date") or "")
    for block, tree in market.items():
        if block in _NON_EVIDENCE:
            continue
        stale = us_stale and block in _US_BLOCKS
        out.update(_entries(tree, f"market:{block}", {
            "as_of": as_of, "as_of_precision": "packet",
            "observed_session": ("" if block in _US_BLOCKS else tw_session),
            "session": session, "source": f"quotes.{block}",
            "quality": "stale" if stale else "ok",
            "usable_for_inference": not stale,
            "why_unusable": ("美股昨日休市,本區塊是上一個交易日的延續值,"
                             "與今天的本地訊號不同步" if stale else ""),
        }))
        # 區塊本身也要引用得到(談「今天沒有這塊資料」時需要)
        out.setdefault(f"market:{block}", {
            "value": None, "unit": "", "as_of": as_of,
            "as_of_precision": "packet",
            "observed_session": ("" if block in _US_BLOCKS else tw_session),
            "session": session,
            "source": f"quotes.{block}", "quality": "stale" if stale else "ok",
            "usable_for_inference": not stale, "why_unusable": ""})

    # 3. packet 的其餘區塊 —— **先前它們一個 ID 都沒有**,而
    #    「00662 估值偏高」「2330 開盤預測為正」「模型校準變差」
    #    「持倉曝險集中」正是最需要根據的四種判斷。
    base = {"as_of": as_of, "as_of_precision": "packet",
            "observed_session": "", "session": session, "quality": "ok",
            "usable_for_inference": True, "why_unusable": ""}
    for ns, key in (("valuation", "valuation_00662"),
                    ("prediction", "predictions_2330"),
                    ("universe", "tw_universe"),
                    ("calibration", "calibration"),
                    # 只有彙總曝險(百分比與檔數),沒有代號也沒有股數 ——
                    # 入口在 `portfolio_summary`,標準寫在那裡。
                    ("portfolio", "portfolio"),
                    ("quality", "coverage")):
        out.update(_entries(pk.get(key), f"{ns}:", dict(base, source=key)))

    # 4. 張力與衍生值(由 `tension_refs` 定義,這裡只補上 metadata)。
    import tension_refs as _tr
    ts = pk.get("signal_tensions") or {}
    known = set(out)
    for it in (ts.get("items") or []):
        if not isinstance(it, dict):
            continue
        usable = bool(it.get("usable_for_inference"))
        why = str(it.get("caveat") or "")
        for ref in ({f"tension:{it.get('tension_id')}"}
                    | {str(r) for r in (it.get("evidence_refs") or [])}):
            # **幽靈 `market:` 路徑不得從這裡溜進來**(第十八輪 P1-4):
            # packet 才知道樹長什麼樣,張力宣稱什麼不算數。
            if ref.startswith("market:") and ref not in known:
                continue
            out.setdefault(ref, dict(
                base, value=None, unit="", source="signal_tensions",
                quality="stale" if not usable else "ok",
                usable_for_inference=usable, why_unusable=why))
    for ref in _tr.evidence_refs(ts):
        if ref.startswith("market:") and ref not in known:
            continue
        out.setdefault(ref, dict(base, value=None, unit="",
                                 source="signal_tensions"))
    return out


def unusable_ids(packet: Optional[dict]) -> dict:
    """`{id: 為什麼不能拿來當今天的方向證據}`。"""
    return {k: v.get("why_unusable") or "資料不同步"
            for k, v in registry(packet).items()
            if not v.get("usable_for_inference")}
