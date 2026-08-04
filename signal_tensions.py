# -*- coding: utf-8 -*-
"""**Python 先把訊號之間的矛盾與同向算出來**(第十五輪 P2-1)。

## 為什麼要在 Python 算

2026-08-04 的信裡同時有:半導體類股中位數 +3.6%、台積電 -2.3%、
上漲家數 59.7%、外資台指期淨空 90,038 口、QQQ +1.76% —— 五個數字
分散在 97K token 的證據裡,而模型把它們寫成五句互不相干的平行敘述。
外審點名的正是這個:**橫向分析的原料要模型自己從資料堆裡挖,
它就會退化成逐條摘要。**

這些矛盾是**確定性可算的**:門檻固定、輸入是已抓好的行情。算出來
放進 EvidencePacket,prompt 要求 `cross_market_synthesis` 逐條回應 ——
模型的工作從「找出矛盾」變成「解釋矛盾」,後者才是它擅長的。

## 界線

  * **只陳述事實,不下結論。** 每條記錄是「A 是 X、B 是 Y、兩者方向
    相反」,不寫「所以偏多」—— 結論是模型(與立場計分)的工作。
  * **門檻沿用 repo 既有的出處**:外資期貨 ±5,000 口是 11 維立場分的
    方向門檻;普漲 60% 是 R 規則第 11 維的門檻。自訂的門檻
    (美股顯著漲跌 0.8%、產業分歧 1.5%/-1.0%)寫在常數旁。
  * **缺資料要看得見**(守衛不得靜默 no-op):`coverage` 記錄哪些檢查
    跑了、哪些因缺資料跳過 —— 空清單要分得出「今天真的沒有張力」
    與「今天根本沒資料可查」。
"""
from __future__ import annotations

from typing import Optional

#: 美股單日「顯著」漲跌(%)。QQQ/SOX 的日常波動約 ±0.5%,0.8% 以上
#: 才值得當成一個方向訊號拿去與本地籌碼對照。
US_MOVE_PCT = 0.8
#: 外資台指期淨部位的方向門檻(口)。沿用 11 維立場分的 ±5,000。
TAIFEX_NET_LOTS = 5000
#: 普漲門檻(%)。沿用 R 規則第 11 維:上漲家數佔比 ≥60 = 普漲。
BREADTH_BROAD = 60.0
#: 指數預測「有方向」的門檻(%)。
PRED_MOVE_PCT = 0.3
#: 產業內部分歧:中位數漲這麼多(%)而權值領頭跌超過這麼多(%),
#: 代表資金在產業**內部**輪動而不是全面買進 —— 2026-08-04 的實例是
#: 半導體中位 +3.6% 對台積電 -2.3%,而信裡完全沒有人指出來。
SECTOR_MEDIAN_PCT, LEADER_DROP_PCT = 1.5, -1.0
#: 只掃成交值前幾大的產業 —— 小產業的中位數噪音大。
TOP_SECTORS = 3


def _num(v) -> Optional[float]:
    """數值就回它自己(bool 不算 —— True 會被當成 1)。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def detect(quotes: Optional[dict]) -> dict:
    """從當日行情算出訊號張力清單。**純函式,只讀不寫。**

    回 `{"checks_run": [...], "unavailable": [...], "items": [...]}`。
    `items` 每條:`kind`(tension/alignment)、`topic`、`a`/`b`(帶數字的
    事實描述)、`note`(兩者的關係,仍是事實不是結論)、`source_keys`。
    """
    q = quotes if isinstance(quotes, dict) else {}
    macro = q.get("MACRO") if isinstance(q.get("MACRO"), dict) else {}
    run, gone, items = [], [], []

    def _add(kind, topic, a, b, note, keys):
        items.append({"kind": kind, "topic": topic, "a": a, "b": b,
                      "note": note, "source_keys": list(keys)})

    # 1. 美股科技的外部定價 vs 外資台指期部位
    qqq = _num((q.get("QQQ") or {}).get("change_pct"))
    oi = _num((q.get("TAIFEX_OI") or {}).get("foreign_oi_net"))
    if qqq is None or oi is None:
        gone.append("us_vs_taifex")
    else:
        run.append("us_vs_taifex")
        if qqq >= US_MOVE_PCT and oi <= -TAIFEX_NET_LOTS:
            _add("tension", "外部定價 vs 本地籌碼",
                 f"QQQ 上漲 {qqq:+.2f}%",
                 f"外資台指期淨空 {abs(oi):,.0f} 口",
                 "美股科技的外部定價向上,而外資期貨部位站在空方 —— "
                 "兩者不可能同時說對今天的方向",
                 ("QQQ", "TAIFEX_OI"))
        elif qqq <= -US_MOVE_PCT and oi >= TAIFEX_NET_LOTS:
            _add("tension", "外部定價 vs 本地籌碼",
                 f"QQQ 下跌 {qqq:+.2f}%",
                 f"外資台指期淨多 {abs(oi):,.0f} 口",
                 "美股走弱而外資期貨部位站在多方 —— 兩者方向相反",
                 ("QQQ", "TAIFEX_OI"))
        elif abs(qqq) >= US_MOVE_PCT and abs(oi) >= TAIFEX_NET_LOTS:
            _add("alignment", "外部定價 vs 本地籌碼",
                 f"QQQ {qqq:+.2f}%",
                 f"外資台指期淨{'多' if oi > 0 else '空'} {abs(oi):,.0f} 口",
                 "美股方向與外資期貨部位同向", ("QQQ", "TAIFEX_OI"))

    # 2. 指數開盤預測 vs 市場廣度
    pred = _num((q.get("TAIEX_PRED") or {}).get("pred_pct"))
    ratio = _num((q.get("BREADTH") or {}).get("advance_ratio"))
    if pred is None or ratio is None:
        gone.append("prediction_vs_breadth")
    else:
        run.append("prediction_vs_breadth")
        if pred >= PRED_MOVE_PCT and ratio < BREADTH_BROAD:
            _add("tension", "開盤預測 vs 市場廣度",
                 f"加權開盤預測 {pred:+.2f}%",
                 f"上一交易日上漲家數佔比 {ratio:.1f}%(未達 {BREADTH_BROAD:.0f}% 普漲門檻)",
                 "預測開高,但上一日不是普漲 —— 高開之後的續航要靠廣度補上",
                 ("TAIEX_PRED", "BREADTH"))
        elif pred <= -PRED_MOVE_PCT and ratio >= BREADTH_BROAD:
            _add("tension", "開盤預測 vs 市場廣度",
                 f"加權開盤預測 {pred:+.2f}%",
                 f"上一交易日上漲家數佔比 {ratio:.1f}%(普漲)",
                 "預測開低,但上一日是普漲 —— 兩者方向相反",
                 ("TAIEX_PRED", "BREADTH"))

    # 3. 產業內部分歧:中位數與權值領頭走不同方向
    sectors = (q.get("SECTOR_HEAT") or {}).get("sectors")
    ranked = (q.get("SECTOR_HEAT") or {}).get("ranked")
    if not isinstance(sectors, dict) or not isinstance(ranked, list):
        gone.append("sector_internal_divergence")
    else:
        run.append("sector_internal_divergence")
        for name in ranked[:TOP_SECTORS]:
            sec = sectors.get(name)
            med = _num((sec or {}).get("median_pct"))
            if med is None:
                continue
            for ld in (sec.get("leaders") or []):
                pct = _num((ld or {}).get("pct"))
                if pct is None:
                    continue
                if med >= SECTOR_MEDIAN_PCT and pct <= LEADER_DROP_PCT:
                    _add("tension", "產業內部分歧",
                         f"{name}類股中位數 {med:+.1f}%",
                         f"權值領頭 {ld.get('code')} {ld.get('name')} {pct:+.1f}%",
                         "產業中小型普遍上漲而權值股下跌 —— 資金在產業內部"
                         "輪動,不是全面買進;指數與類股中位數可能走不同方向",
                         ("SECTOR_HEAT",))
                elif med <= -SECTOR_MEDIAN_PCT and pct >= -LEADER_DROP_PCT:
                    _add("tension", "產業內部分歧",
                         f"{name}類股中位數 {med:+.1f}%",
                         f"權值領頭 {ld.get('code')} {ld.get('name')} {pct:+.1f}%",
                         "產業普遍下跌而權值股相對抗跌 —— 指數被權值撐住,"
                         "廣度比指數弱", ("SECTOR_HEAT",))

    # 4. 長債利率變動 vs 美股科技
    y_c = _num((macro.get("10Y") or {}).get("close"))
    y_p = _num((macro.get("10Y") or {}).get("prev_close"))
    if y_c is None or y_p is None or qqq is None:
        gone.append("rates_vs_tech")
    else:
        run.append("rates_vs_tech")
        dbps = (y_c - y_p) * 100
        if dbps >= 8 and qqq >= US_MOVE_PCT:
            _add("tension", "利率 vs 科技股",
                 f"十年期美債利率上行 {dbps:+.0f}bps(至 {y_c:.2f}%)",
                 f"QQQ 上漲 {qqq:+.2f}%",
                 "折現率走高的同一天科技股上漲 —— 其中一邊的走勢通常撐不久",
                 ("MACRO", "QQQ"))
        elif dbps <= -8 and qqq >= US_MOVE_PCT:
            _add("alignment", "利率 vs 科技股",
                 f"十年期美債利率回落 {dbps:+.0f}bps(至 {y_c:.2f}%)",
                 f"QQQ 上漲 {qqq:+.2f}%",
                 "利率與科技股同向支撐成長股估值", ("MACRO", "QQQ"))

    return {"checks_run": run, "unavailable": gone, "items": items}
