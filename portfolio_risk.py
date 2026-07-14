"""portfolio_risk.py — 持倉曝險引擎(白話)的純計算核心(G1)。

設計原則
--------
* **純函式、零網路**:只吃「已抓好的價格序列」({date: close} 或 return list),
  不 import morning_report、不碰 yfinance → 可用合成序列做「精確」單測
  (yfinance 本機 geo-block,真數字要上 Actions 才有;但數學這層可離線驗到底)。
* **隱私鐵律**:本模組**不回傳任何代號/股數/金額/個股權重**。呼叫端在函式內把
  持股市值換成權重、加權彙總後,只有「組合層的比例(%)」離開本模組。
* **白話鐵律**:beta 不外露字眼;由本模組把 beta 翻成「約 X 倍」「資產約變動 Y%」,
  渲染層直接用。不出現 beta/波動/追蹤誤差/標準差等術語。

方法論(與既有 00662 回歸一致的假設,見 morning_report.compute_fair_value)
-----------------------------------------------------------------------
* 台股標的對「美股盤(QQQ)」的連動有隔夜時差:台股第 T 日反應的是美股第 T-1 收盤,
  故對美股類驅動因子(QQQ)配對時 **lag 一個交易日**;對台股大盤(^TWII)與匯率
  (TWD=X,台灣盤中連續報價)則同日配對。
* 單因子 OLS 斜率 = cov(資產, 驅動)/var(驅動)。各情境為「僅該因子變動」的獨立近似,
  不宣稱可相加(台股/美股本身相關,分開估是刻意的白話簡化)。
"""
from __future__ import annotations

import bisect
from typing import Optional


# ── 序列 → 報酬 ────────────────────────────────────────────────────────────
def _returns_by_date(close_by_date: dict) -> dict:
    """{date: close} → {date: 當日相對「前一個有效交易日」的報酬}。

    date 需為可排序字串(如 'YYYY-MM-DD');非正/None 收盤直接剔除後再算,
    故報酬永遠是相鄰兩個「有效」交易日之比,不會被中間缺值污染。
    """
    items = sorted((d, c) for d, c in close_by_date.items()
                   if c is not None and isinstance(c, (int, float)) and c > 0)
    out: dict = {}
    for i in range(1, len(items)):
        _, c_prev = items[i - 1]
        d_cur, c_cur = items[i]
        out[d_cur] = c_cur / c_prev - 1.0
    return out


def aligned_returns(asset_by_date: dict, driver_by_date: dict,
                    lag_driver: bool = False) -> tuple[list, list]:
    """把資產與驅動因子的日報酬對齊,回傳等長的 (asset_rets, driver_rets)。

    lag_driver=False:同日配對(台股 vs 台股大盤/匯率)。
    lag_driver=True :資產第 T 日報酬,配「驅動因子在 T 之前最後一個交易日」的報酬
                     (美股隔夜 → 台股隔日;等價於既有回歸的 shift(1) 語意)。
    任一序列樣本不足 → 回 ([], [])。
    """
    a_ret = _returns_by_date(asset_by_date)
    d_ret = _returns_by_date(driver_by_date)
    if not a_ret or not d_ret:
        return [], []
    d_dates = sorted(d_ret.keys())
    xs: list = []   # 資產報酬
    ys: list = []   # 驅動報酬
    for d in sorted(a_ret.keys()):
        if not lag_driver:
            if d in d_ret:
                xs.append(a_ret[d])
                ys.append(d_ret[d])
        else:
            j = bisect.bisect_left(d_dates, d) - 1   # 嚴格早於 d 的最後一個驅動交易日
            if j >= 0:
                xs.append(a_ret[d])
                ys.append(d_ret[d_dates[j]])
    return xs, ys


# ── 單因子 beta ────────────────────────────────────────────────────────────
def ols_beta(asset_rets: list, driver_rets: list, min_n: int = 20,
             lo: float = -3.0, hi: float = 4.0) -> Optional[float]:
    """單因子 OLS 斜率(資產 ~ a + beta·驅動)= cov/var。

    樣本 < min_n、長度不一、或驅動變異≈0 → None(呼叫端視為該因子「無有效曝險」)。
    結果夾在 [lo, hi] 以擋離群(資料異常時的爆炸值);合成「資產=beta·驅動」時回傳精確 beta。
    """
    n = len(asset_rets)
    if n < min_n or n != len(driver_rets):
        return None
    mx = sum(driver_rets) / n          # 驅動(x)均值
    my = sum(asset_rets) / n           # 資產(y)均值
    var = sum((x - mx) ** 2 for x in driver_rets)
    if var <= 0:
        return None
    cov = sum((asset_rets[i] - my) * (driver_rets[i] - mx) for i in range(n))
    beta = cov / var
    return max(lo, min(hi, beta))


# ── 權重與組合層彙總(隱私:權重只活在這裡,不外流) ───────────────────────
def value_weights(values: dict) -> dict:
    """{key: 市值} → {key: 權重}(和=1)。呼叫端算好市值(股×價)後傳入;
    非正總市值 → {}。本函式輸出的權重僅供組合彙總,呼叫端不得外洩。"""
    tot = sum(v for v in values.values() if v and v > 0)
    if tot <= 0:
        return {}
    return {k: (v / tot if v and v > 0 else 0.0) for k, v in values.items()}


def portfolio_beta(weights: dict, betas: dict) -> tuple[float, float]:
    """組合對某驅動因子的 beta = Σ 權重·beta。

    回 (pf_beta, coverage):coverage = 有有效 beta 之持股的權重和。
    缺 beta 者(資料不足)不計入(視為該因子 0 曝險),並反映在 coverage < 1。
    呼叫端可據 coverage 決定是否顯示(太低→白話標「資料不足」)。
    """
    pf = 0.0
    cov = 0.0
    for k, w in weights.items():
        b = betas.get(k)
        if b is None:
            continue
        pf += w * b
        cov += w
    return pf, cov


# ── 情境 / 壓力 ─────────────────────────────────────────────────────────────
def scenario_rows(pf_betas: dict, scenarios: list) -> list:
    """組合層情境:對每個 (driver_key, move_pct, 白話標籤) 算資產預估變動 %。

    pf_betas: {'tw': beta, 'qqq': beta, 'fx': beta}(缺鍵 → 跳過該情境)。
    回 [{'label':.., 'move_pct':.., 'delta_pct':..}],delta_pct = beta·move。
    """
    rows: list = []
    for key, move, label in scenarios:
        b = pf_betas.get(key)
        if b is None:
            continue
        rows.append({"label": label, "move_pct": move,
                     "delta_pct": round(b * move, 2)})
    return rows


def stress_rows(pf_beta_qqq: Optional[float], drawdowns: list) -> list:
    """QQQ 壓力測試:對每個回撤幅度(正數 %,如 10/20/30)算資產預估變動 %(負值)。
    pf_beta_qqq 為 None → 回 []。"""
    if pf_beta_qqq is None:
        return []
    return [{"drawdown_pct": d, "delta_pct": round(pf_beta_qqq * (-d), 1)}
            for d in drawdowns]


# ── 白話措辭 ─────────────────────────────────────────────────────────────────
def phrase_multiple(beta: Optional[float]) -> str:
    """把「組合對台股大盤的 beta」翻成白話倍數,如 1.23 → 「約 1.2 倍」。None → 「—」。"""
    if beta is None:
        return "—"
    return f"約 {beta:.1f} 倍"
