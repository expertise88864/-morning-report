# -*- coding: utf-8 -*-
"""**Python 先把訊號之間的矛盾與同向算出來**(第十五輪 P2-1)。

## 為什麼要在 Python 算

2026-08-04 的信裡同時有:半導體類股中位數 +3.6%、台積電 -2.3%、
上漲家數 59.7%、外資台指期淨空 90,038 口、QQQ +1.76% —— 五個數字
分散在 97K token 的證據裡,而模型把它們寫成五句互不相干的平行敘述。
**橫向分析的原料要模型自己從資料堆裡挖,它就會退化成逐條摘要。**

## 這個模組**只給觀測,不給結論**(第十六輪 P1-3)

第一版的 `note` 寫著「兩者不可能同時說對今天的方向」「高開之後的續航
要靠廣度補上」「其中一邊的走勢通常撐不久」—— 那些**不是事實,是市場
經驗法則**,而且未必成立(外資期貨淨空可能是避險部位,與方向預測是
兩件事;兩個訊號也可能屬於不同時間尺度)。Python 把結論先下了,模型
只會照抄,而信裡會出現一個沒有人驗證過的推論。

現在每一筆只給:兩邊的**數值與單位**、關係的**幾何性質**
(`opposite_sign` / `same_sign` / `below_threshold`)、以及可引用的
`evidence_refs`。**怎麼調和、哪邊可信、會不會高開走低,是模型的工作**,
而且要標成 inference。

## 新鮮度與資料品質(第十六輪 P1-4)

美股休市那天 QQQ 是**上一個交易日的延續值**,拿它與今天的本地籌碼
對照沒有意義。既有 11 維立場分已經有 `US_HOLIDAY.detected` 這個判準,
本模組沿用同一個來源 —— 判定 stale 時仍然產出該筆,但標
`usable_for_inference=False` 並說明理由,**不靜默丟掉**
(丟掉的話,「今天沒有張力」與「今天的張力不可用」會長得一樣)。
"""
from __future__ import annotations

from typing import Optional

#: 美股單日「顯著」漲跌(%)。QQQ/SOX 的日常波動約 ±0.5%,0.8% 以上
#: 才值得當成一個方向訊號拿去與本地籌碼對照。**本模組自訂,無 repo 出處。**
US_MOVE_PCT = 0.8
#: 外資台指期淨部位的方向門檻(口)。**沿用 11 維立場分的 ±5,000。**
TAIFEX_NET_LOTS = 5000
#: 普漲門檻(%)。**沿用 R 規則第 11 維**:上漲家數佔比 ≥60 = 普漲。
BREADTH_BROAD = 60.0
#: 指數預測「有方向」的門檻(%)。**本模組自訂。**
PRED_MOVE_PCT = 0.3
#: 產業內部分歧:中位數與權值領頭差距這麼多個百分點以上才算分歧。
#: **用差距而不是各自的絕對門檻** —— 第一版寫成
#: `med >= 1.5 and pct <= -1.0` / `med <= -1.5 and pct >= -(-1.0)`,
#: 後者實際要求權值股**上漲 1%**,於是「中位 -2.5% 而權值只跌 0.2%」
#: 這種最典型的抗跌完全抓不到(第十六輪 P1-5A,實測確認)。
#: **本模組自訂,無 repo 出處。** 訂 2.0:類股中位數的日常波動約 1–2%,
#: 而「中位 −2.5% 而權值只跌 0.2%」(差 2.3pp)是典型的權值抗跌 ——
#: 訂 2.5 會把它濾掉。門檻放寬一點、由模型判斷值不值得寫,比 Python
#: 先替它決定「這不算分歧」好。
SECTOR_GAP_PP = 2.0
#: 利率變動的顯著門檻(bps)。**本模組自訂。**
RATE_MOVE_BPS = 8
#: **允許的幾何關係。** 全部是符號/大小的描述,**不含任何經濟解釋** ——
#: 「利率升所以科技股受壓」是模型的工作(而且要標成 inference),
#: 不是 Python 的(第十七輪 P1-6)。
#: 利率 × 科技股用**象限名**(第十八輪 P1-4)。先前寫
#: `same = (dbps < 0) == (qqq > 0)` —— 把「利率降+科技漲」叫做
#: `same_direction`,而兩者的原始符號明明一正一負。那個等式本身就是
#: 「折現率下行有利長天期成長股」這條經濟假說,只是換了個名字。
#: 象限名不含任何主張:它就是兩個符號的組合。
RELATIONSHIPS = ("same_sign", "opposite_sign", "same_direction",
                 "opposite_direction", "aligned_but_narrow",
                 "median_above_leader", "median_below_leader",
                 "yield_up_tech_up", "yield_up_tech_down",
                 "yield_down_tech_up", "yield_down_tech_down")

#: 只掃成交值前幾大的產業 —— 小產業的中位數噪音大。
TOP_SECTORS = 3


def _num(v) -> Optional[float]:
    """數值就回它自己(bool 不算 —— True 會被當成 1)。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _side(label: str, value: float, unit: str, ref: str,
          derived_from=None) -> dict:
    """一邊的觀測。**只有數值與出處,沒有形容詞。**

    `derived_from`:這個值是**算出來的**時候,列出它的原始欄位。
    第十八輪 P1-4:先前利率那一側掛 `market:MACRO.10Y.change_bps`,
    而 packet 的 MACRO 只有 `close`/`prev_close` —— 那是個**不存在的
    market 路徑**,卻因為 `evidence_ids()` 無條件收下張力給的 ref
    而變成合法引用。衍生值要用 `derived:` 命名空間,並帶著它的來源,
    否則「引用了正確資料」與「引用了一個看起來像資料的名字」分不開。
    """
    out = {"label": label, "value": value, "unit": unit, "evidence_ref": ref}
    if derived_from:
        out["derived_from"] = list(derived_from)
    return out


def detect(quotes: Optional[dict]) -> dict:
    """從當日行情算出訊號張力清單。**純函式,只讀不寫,不下結論。**

    回 `{"checks_run": [...], "unavailable": [...], "items": [...]}`。
    每筆 `items`:`tension_id`(穩定、供驗證器比對是否逐條處理)、
    `kind`、`topic`、`left`/`right`(數值觀測)、`relationship`(幾何性質)、
    `evidence_refs`、`usable_for_inference` 與 `caveat`。
    """
    q = quotes if isinstance(quotes, dict) else {}
    macro = q.get("MACRO") if isinstance(q.get("MACRO"), dict) else {}
    # **美股休市 = 美股欄位是上一個交易日的延續值。** 沿用 11 維立場分
    # 用的同一個判準(`detected` 欄位,不是 truthiness —— 平日它也是 dict)。
    us_stale = bool((q.get("US_HOLIDAY") or {}).get("detected"))
    run, gone, items = [], [], []

    def _add(tid, kind, topic, left, right, relationship, *, us_side=False):
        stale = us_stale and us_side
        items.append({
            "tension_id": tid, "kind": kind, "topic": topic,
            "left": left, "right": right, "relationship": relationship,
            "evidence_refs": [left["evidence_ref"], right["evidence_ref"]],
            # **stale 不丟掉,只標不可用** —— 丟掉的話「沒有張力」與
            # 「張力不可用」在下游長得一模一樣。
            "usable_for_inference": not stale,
            "caveat": ("美股昨日休市,該側為上一個交易日的延續值,"
                       "與本地當日訊號不同步" if stale else ""),
        })

    # 1. 美股科技的外部定價 vs 外資台指期部位
    qqq = _num((q.get("QQQ") or {}).get("change_pct"))
    oi = _num((q.get("TAIFEX_OI") or {}).get("foreign_oi_net"))
    if qqq is None or oi is None:
        gone.append("us_vs_taifex")
    else:
        run.append("us_vs_taifex")
        if abs(qqq) >= US_MOVE_PCT and abs(oi) >= TAIFEX_NET_LOTS:
            same = (qqq > 0) == (oi > 0)
            _add("t_us_vs_taifex",
                 "alignment" if same else "tension", "外部定價 vs 本地籌碼",
                 _side("QQQ 日漲跌", qqq, "%", "market:QQQ.change_pct"),
                 _side("外資台指期淨部位", oi, "lots",
                       "market:TAIFEX_OI.foreign_oi_net"),
                 "same_sign" if same else "opposite_sign", us_side=True)

    # 2. 指數開盤預測 vs 市場廣度
    pred = _num((q.get("TAIEX_PRED") or {}).get("pred_pct"))
    ratio = _num((q.get("BREADTH") or {}).get("advance_ratio"))
    if pred is None or ratio is None:
        gone.append("prediction_vs_breadth")
    else:
        run.append("prediction_vs_breadth")
        if abs(pred) >= PRED_MOVE_PCT:
            # 第十七輪 P1-4:**59.7% 不是「方向相反」,是「正向但不夠廣」。**
            # 先前寫 `same = (pred > 0) == (ratio >= 60)` —— 於是 59.7%
            # 與真正偏空的 38% 拿到**同一個** `opposite_sign` 標籤,
            # 而模型被要求正面處理那個「矛盾」,就會寫出「市場廣度偏空」。
            # 方向的分界是 50%(多過半數上漲),60% 是**強度**門檻。
            up = ratio > 50.0
            aligned = (pred > 0) == up
            broad = ratio >= BREADTH_BROAD or ratio <= (100.0 - BREADTH_BROAD)
            if aligned and broad:
                kind, rel = "alignment", "same_direction"
            elif aligned:
                kind, rel = "tension", "aligned_but_narrow"
            else:
                kind, rel = "tension", "opposite_direction"
            _add("t_pred_vs_breadth", kind, "開盤預測 vs 市場廣度",
                 _side("加權開盤預測", pred, "%", "market:TAIEX_PRED.pred_pct"),
                 _side(f"上一交易日上漲家數佔比"
                       f"(方向分界 50%、普漲門檻 {BREADTH_BROAD:.0f}%)",
                       ratio, "%", "market:BREADTH.advance_ratio"),
                 rel)

    # 3. 產業內部分歧:中位數與權值領頭的**差距**
    #    第十六輪 P1-5B:同產業每個 leader 各發一筆會產生重複
    #    (半導體的 2330 與 2303 各一筆),而 prompt 要求逐筆處理 ——
    #    重複會在信裡重新製造「資料堆疊」。**一個產業只取差距最大的那檔。**
    sectors = (q.get("SECTOR_HEAT") or {}).get("sectors")
    ranked = (q.get("SECTOR_HEAT") or {}).get("ranked")
    if not isinstance(sectors, dict) or not isinstance(ranked, list):
        gone.append("sector_internal_divergence")
    else:
        run.append("sector_internal_divergence")
        for name in ranked[:TOP_SECTORS]:
            sec = sectors.get(name) or {}
            med = _num(sec.get("median_pct"))
            if med is None:
                continue
            worst, gap = None, 0.0
            for ld in (sec.get("leaders") or []):
                pct = _num((ld or {}).get("pct"))
                if pct is None:
                    continue
                if abs(med - pct) > abs(gap):
                    worst, gap = ld, med - pct
            if worst is None or abs(gap) < SECTOR_GAP_PP:
                continue
            _add(f"t_sector_divergence:{name}", "tension", "產業內部分歧",
                 # **路徑要與 registry 的正規路徑一致** —— 同一個事實兩個
                 # 名字的話,模型引用張力給的那個會落在一個「剛好也合法」
                 # 的別名上,而不是真正的欄位(第十七輪 P1-1 的延伸)。
                 _side(f"{name}類股中位數", med, "%",
                       f"market:SECTOR_HEAT.sectors.{name}.median_pct"),
                 _side(f"權值領頭 {worst.get('code')} {worst.get('name')}",
                       _num(worst.get("pct")), "%",
                       f"market:SECTOR_HEAT.sectors.{name}.leaders."
                       f"{worst.get('code')}.pct"),
                 "median_above_leader" if gap > 0 else "median_below_leader")

    # 4. 長債利率變動 vs 美股科技(**四個象限都要涵蓋** —— 第十六輪 P1-5C)
    y_c, y_p = _num((macro.get("10Y") or {}).get("close")), \
        _num((macro.get("10Y") or {}).get("prev_close"))
    if y_c is None or y_p is None or qqq is None:
        gone.append("rates_vs_tech")
    else:
        run.append("rates_vs_tech")
        dbps = (y_c - y_p) * 100
        if abs(dbps) >= RATE_MOVE_BPS and abs(qqq) >= US_MOVE_PCT:
            # 第十八輪 P1-4:**Python 不判斷這個組合是一致還是矛盾。**
            # 上一版寫 `same = (dbps < 0) == (qqq > 0)`,等於內建了
            # 「折現率下行有利成長股」——利率降也可能是衰退定價,
            # 利率升也可能伴隨獲利上修。四個象限**都**交給模型解釋,
            # 因此一律標 tension(= 必須正面處理),relationship 只說象限。
            rel = ("yield_up_tech_up" if dbps > 0 and qqq > 0 else
                   "yield_up_tech_down" if dbps > 0 else
                   "yield_down_tech_up" if qqq > 0 else
                   "yield_down_tech_down")
            _add("t_rates_vs_tech", "tension", "利率 vs 科技股",
                 _side("十年期美債利率變動", round(dbps, 1), "bps",
                       "derived:t_rates_vs_tech.left",
                       derived_from=["market:MACRO.10Y.close",
                                     "market:MACRO.10Y.prev_close"]),
                 _side("QQQ 日漲跌", qqq, "%", "market:QQQ.change_pct"),
                 rel, us_side=True)

    return {"checks_run": run, "unavailable": gone, "items": items}


# ---------------------------------------------------------------- 相容出口
#
# 查詢介面搬到 `tension_refs`(見該檔:偵測與查詢是兩種責任)。
from tension_refs import (                        # noqa: E402,F401
    evidence_refs, market_refs_claimed, required_tension_ids, sides_evidence)
