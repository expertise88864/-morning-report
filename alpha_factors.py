# -*- coding: utf-8 -*-
"""WorldQuant Alpha101 風格的公式因子工具箱 + IC 驗效(離線研究、不進每日信)。

借鏡 Kakushadze(2015)"101 Formulaic Alphas" 之「算子詞彙」(rank/ts_rank/delta/delay/
correlation/scale/decay_linear…),clean-room 自寫;再用這些算子組幾條可由 model_history
既有欄位(close/open/volume)計算的 alpha,接 factor_ic 的 rank-IC 流程驗證在台股有無 edge。
純自用、零 scipy 依賴(僅用既有 numpy/pandas)。

⚠ 限制:model_history 無逐日 high/low,故只實作可由 close/open/volume 算的 alpha;
這是「因子研究腳手架」,挑出有 IC 的再考慮接進主流程(現階段不接、不進每日信)。

用法:python alpha_factors.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HIST = Path("state/model_history.json")
HORIZON = 5
MIN_NAMES = 15

# ── Alpha101 算子(都吃「日期×個股」DataFrame,沿用 WorldQuant 語意,clean-room)──


def cs_rank(df: pd.DataFrame) -> pd.DataFrame:
    """橫斷面排名(每日對所有個股),回百分位 ∈(0,1]。"""
    return df.rank(axis=1, pct=True)


def ts_delay(df: pd.DataFrame, k: int) -> pd.DataFrame:
    """k 日前的值。"""
    return df.shift(k)


def ts_delta(df: pd.DataFrame, k: int) -> pd.DataFrame:
    """今值 − k 日前值。"""
    return df - df.shift(k)


def ts_std(df: pd.DataFrame, k: int) -> pd.DataFrame:
    return df.rolling(k).std()


def ts_sum(df: pd.DataFrame, k: int) -> pd.DataFrame:
    return df.rolling(k).sum()


def ts_rank(df: pd.DataFrame, k: int) -> pd.DataFrame:
    """過去 k 日中,今值的時序百分位排名 ∈(0,1]。"""
    return df.rolling(k).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)


def ts_corr(a: pd.DataFrame, b: pd.DataFrame, k: int) -> pd.DataFrame:
    """兩序列過去 k 日的滾動相關(逐個股)。"""
    return a.rolling(k).corr(b)


def scale(df: pd.DataFrame, a: float = 1.0) -> pd.DataFrame:
    """每日橫斷面正規化:各值 / 當日 |值| 總和 × a(Σ|scaled|=a)。"""
    denom = df.abs().sum(axis=1).replace(0, np.nan)
    return df.mul(a).div(denom, axis=0)


def decay_linear(df: pd.DataFrame, k: int) -> pd.DataFrame:
    """過去 k 日線性遞減加權移動平均;rolling 視窗為時序(舊→新),近值權重最大。
    視窗值 [oldest..newest] 對應權重 [1,2,…,k]/Σ(故 newest 權重最大 = WorldQuant decay_linear)。"""
    w = np.arange(1, k + 1, dtype=float)
    w /= w.sum()
    return df.rolling(k).apply(lambda x: float(np.dot(x, w)), raw=True)


# ── 幾條可由 close/open/volume 計算的 alpha(回「日期×個股」訊號)──


def alpha_short_reversal(P: dict) -> pd.DataFrame:
    """短線反轉:近 5 日報酬的橫斷面排名取負(漲多者隔期偏弱)。≈ Alpha 系列常見 reversal。"""
    ret5 = P["close"] / P["close"].shift(5) - 1.0
    return -cs_rank(ret5)


def alpha_momentum_20(P: dict) -> pd.DataFrame:
    """中線動能:20 日價格變化的橫斷面排名(延續)。"""
    return cs_rank(ts_delta(P["close"], 20))


def alpha_volume_price_corr(P: dict) -> pd.DataFrame:
    """量價背離:rank(close) 與 rank(volume) 近 5 日相關取負(量價齊揚後常回吐)。≈ Alpha#?。"""
    return -cs_rank(ts_corr(cs_rank(P["close"]), cs_rank(P["volume"]), 5))


def alpha_intraday_strength(P: dict) -> pd.DataFrame:
    """當日強弱:(close−open)/open 的橫斷面排名(收高於開=買盤強)。"""
    rng = (P["close"] - P["open"]) / P["open"].abs().replace(0, np.nan)
    return cs_rank(rng)


ALPHAS = {
    "short_reversal_5": alpha_short_reversal,
    "momentum_20": alpha_momentum_20,
    "volume_price_corr_5": alpha_volume_price_corr,
    "intraday_strength": alpha_intraday_strength,
}


def _build_panels() -> dict:
    """從 model_history 建 {field: DataFrame(index=date, columns=code)}(close/open/volume)。"""
    if not HIST.exists():
        return {}
    snaps = [s for s in json.loads(HIST.read_text(encoding="utf-8"))
             if s.get("session_date") and isinstance(s.get("stocks"), dict)]
    snaps.sort(key=lambda s: s["session_date"])
    if len(snaps) < HORIZON + 30:
        return {}
    fields = ("close", "open", "volume")
    data = {f: {} for f in fields}
    for s in snaps:
        d = s["session_date"]
        for f in fields:
            data[f][d] = {c: v.get(f) for c, v in s["stocks"].items()
                          if isinstance(v.get(f), (int, float))}
    panels = {f: pd.DataFrame.from_dict(data[f], orient="index").sort_index().astype(float)
              for f in fields}
    # open 逐格缺漏用 close 補(部分快照可能無 open),欄位對齊後再補
    panels["open"] = panels["open"].reindex_like(panels["close"]).fillna(panels["close"])
    return panels


def _rank_ic(signal: pd.DataFrame, fwd: pd.DataFrame) -> dict:
    """signal 與 fwd(各為 日期×個股)逐日 rank-IC(Spearman),回平均/IR/t/樣本日。"""
    ics = []
    common = signal.index.intersection(fwd.index)
    for d in common:
        x = signal.loc[d]
        y = fwd.loc[d]
        m = x.notna() & y.notna()
        if m.sum() < MIN_NAMES:
            continue
        rx = x[m].rank()
        ry = y[m].rank()
        rx = rx - rx.mean()
        ry = ry - ry.mean()
        den = math.sqrt(float((rx * rx).sum()) * float((ry * ry).sum()))
        if den:
            ics.append(float((rx * ry).sum() / den))
    if len(ics) < 5:
        return {}
    arr = np.asarray(ics)
    mean_ic = float(arr.mean())
    ir = mean_ic / arr.std() if arr.std() else 0.0
    return {"mean_ic": round(mean_ic, 4), "ir": round(ir, 3),
            "t": round(ir * math.sqrt(len(arr)), 2),
            "pos_pct": round(100 * float((arr > 0).mean()), 1), "days": len(arr)}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore
        except Exception:
            pass
    P = _build_panels()
    if not P:
        print(f"資料不足(需 {HIST} 累積足夠交易日)")
        return 1
    close = P["close"]
    fwd = close.shift(-HORIZON) / close - 1.0          # 未來 HORIZON 日報酬
    print(f"面板:{close.shape[0]} 交易日 × {close.shape[1]} 檔;前瞻 {HORIZON} 日\n")
    print(f"{'alpha':<22}{'平均IC':>9}{'IC-IR':>8}{'t值':>7}{'為正%':>7}{'樣本日':>7}")
    print("-" * 62)
    for name, fn in ALPHAS.items():
        try:
            sig = fn(P)
        except Exception as e:
            print(f"{name:<22} 計算失敗: {e}")
            continue
        r = _rank_ic(sig, fwd)
        if not r:
            print(f"{name:<22} 樣本不足")
            continue
        flag = "  ★" if abs(r["mean_ic"]) > 0.03 and abs(r["ir"]) > 0.3 else ""
        print(f"{name:<22}{r['mean_ic']:>+9.4f}{r['ir']:>+8.3f}"
              f"{r['t']:>+7.2f}{r['pos_pct']:>6.1f}%{r['days']:>7}{flag}")
    print("\n※ |平均IC|>0.03 且 |IC-IR|>0.3 標 ★(弱但有參考);重疊窗口使 t 值偏樂觀,僅供因子相對比較。")
    print("※ 離線研究腳手架;有 edge 的 alpha 才考慮接主流程(現不接、不進每日信)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
