"""離線回測共用統計工具(純 stdlib)。

Newey-West(HAC)t 值:每日 IC 序列的觀測值因前瞻視窗重疊而強自相關
(前瞻 20 日 → 相鄰觀測共享 19 天報酬),naive mean/(sd/√n) 會嚴重高估顯著性
(GPT-5.6 四審 P1:market_cap t 4.43 → NW 後 1.63)。lag 取 horizon-1。
"""


def newey_west_t(series: list[float], lag: int) -> float | None:
    """對序列均值做 HAC(Bartlett kernel)t 檢定;樣本不足或變異退化回 None。"""
    n = len(series)
    if n < 2:
        return None
    mean = sum(series) / n
    resid = [x - mean for x in series]
    var = sum(v * v for v in resid) / n          # gamma_0
    max_lag = max(0, min(int(lag), n - 1))
    for k in range(1, max_lag + 1):
        w = 1 - k / (max_lag + 1)                # Bartlett 權重
        gamma = sum(resid[i] * resid[i - k] for i in range(k, n)) / n
        var += 2 * w * gamma
    if var <= 0:
        return None
    return mean / (var / n) ** 0.5
