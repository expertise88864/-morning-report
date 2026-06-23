# -*- coding: utf-8 -*-
"""因子有效性檢驗(Information Coefficient)— 用 state/model_history.json 的歷史面板。

對每個「已逐日儲存」的因子(動能/流動性/規模),算它與「未來 k 交易日報酬」的
橫斷面 Spearman 相關(IC):每個交易日算一次 IC,再看 平均 IC、IC-IR(平均/標準差)、
t 值、為正比率。|平均 IC| > ~0.03 且 IC-IR > ~0.3 才算有參考價值的弱訊號。

forward return 由同一股票跨快照 close join 計算(model_history 未存 target)。
無新依賴(自算 Spearman:rank 後 Pearson),不碰晨報主流程。

用法:python factor_ic.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HIST = Path("state/model_history.json")
HORIZONS = [5, 10, 20]
# 數值越大越「看多」的因子(IC 直接解讀);size/波動/滑價為中性,看 IC 正負即可。
FACTORS = ["pct_5d", "ma20_dist_pct", "day_pct", "vol_ratio_20d",
           "daily_vol_pct", "market_cap", "slippage_bps"]
MIN_NAMES = 15   # 一個橫斷面至少幾檔才算 IC


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman = 平均秩後的 Pearson;用 pandas.rank 處理同值(ties),避免
    像 slippage_bps 這種大量同值因子被 argsort 假造排序而扭曲 IC。"""
    if len(x) < MIN_NAMES:
        return np.nan
    rx = pd.Series(x).rank().to_numpy()      # method='average':同值取平均秩
    ry = pd.Series(y).rank().to_numpy()
    rx -= rx.mean()
    ry -= ry.mean()
    denom = np.sqrt((rx * rx).sum() * (ry * ry).sum())
    return float((rx * ry).sum() / denom) if denom else np.nan


def main() -> int:
    if not HIST.exists():
        print(f"缺 {HIST}")
        return 1
    snaps = json.loads(HIST.read_text(encoding="utf-8"))
    snaps = [s for s in snaps if s.get("session_date") and isinstance(s.get("stocks"), dict)]
    snaps.sort(key=lambda s: s["session_date"])
    n = len(snaps)
    print(f"面板:{n} 個交易日 {snaps[0]['session_date']}~{snaps[-1]['session_date']};"
          f"每日約 {len(snaps[-1]['stocks'])} 檔\n")

    # close[i][code]
    closes = [{c: v.get("close") for c, v in s["stocks"].items() if v.get("close")}
              for s in snaps]

    print(f"{'因子':<16}{'窗格':>5}{'平均IC':>9}{'IC-IR':>8}{'t值':>7}{'為正%':>7}{'樣本日':>7}")
    print("-" * 60)
    for fac in FACTORS:
        for k in HORIZONS:
            ics = []
            for i in range(n - k):
                fwd = closes[i + k]
                cur = snaps[i]["stocks"]
                xs, ys = [], []
                for code, sv in cur.items():
                    fv = sv.get(fac)
                    c0 = sv.get("close")
                    c1 = fwd.get(code)
                    if fv is None or not c0 or not c1:
                        continue
                    xs.append(float(fv))
                    ys.append(c1 / c0 - 1.0)
                ic = _spearman(np.array(xs), np.array(ys))
                if not np.isnan(ic):
                    ics.append(ic)
            if len(ics) < 5:
                continue
            arr = np.array(ics)
            mean_ic = arr.mean()
            ir = mean_ic / arr.std() if arr.std() else 0.0
            t = ir * np.sqrt(len(arr))
            pos = 100 * (arr > 0).mean()
            flag = "  ★" if abs(mean_ic) > 0.03 and abs(ir) > 0.3 else ""
            print(f"{fac:<16}{k:>4}日{mean_ic:>+9.3f}{ir:>+8.2f}{t:>+7.2f}{pos:>6.0f}%{len(arr):>7}{flag}")
        print()
    print("※ |平均IC|>0.03 且 |IC-IR|>0.3 標 ★(弱但有參考)。正 IC=因子值越大、未來報酬越高。")
    print("※ 重疊窗口(每日起算 k 日報酬,相鄰日高度重疊)使 IC 序列自我相關 → IC-IR/t 值『偏樂觀』,僅作因子相對排序,勿當嚴格顯著性。")
    print("※ 僅檢驗 model_history 已存的動能/流動性/規模因子;籌碼/基本面因子需另行逐日記錄後才能檢驗。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
