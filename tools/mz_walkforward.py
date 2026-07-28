# -*- coding: utf-8 -*-
"""MZ 收縮的 walk-forward 驗證。

**在實作之前先驗證,而不是實作完再找理由。**

批#48 的 Mincer-Zarnowitz 檢定指出預測**過度反應**(b<1),建議改用 a + b*pred
往均值收縮。但那是 **in-sample** 的擬合——用全部 48 天資料估出 a、b,再用同一批
資料算改善,必然看起來有效。真正要問的是:**只用當下已知的歷史估係數,
套到「未來」那一天,還有沒有用?**

做法(嚴格 walk-forward,無前視):
  對第 t 天,只用第 0..t-1 天估 (a, b),再套到第 t 天的預測上。
  最小訓練樣本 MIN_TRAIN 天;不足者不調整(照原值)。

比較基準:
  raw          原始預測
  shrink_wf    walk-forward 收縮
  shrink_is    in-sample 收縮(**只當對照,證明 in-sample 會高估效益**)

指標:MAE、RMSE、方向命中率、以及「有多少天被改壞」。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import model_confidence as mc  # noqa: E402

MIN_TRAIN = 20


def _ols(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0, 1.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0, 1.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    return my - b * mx, b


def _metrics(actual, pred, base):
    n = len(actual)
    mae = sum(abs(a - p) for a, p in zip(actual, pred)) / n
    rmse = (sum((a - p) ** 2 for a, p in zip(actual, pred)) / n) ** 0.5
    hit = sum(1 for a, p, b in zip(actual, pred, base)
              if (a - b) * (p - b) > 0)
    total = sum(1 for a, b in zip(actual, base) if a != b)
    return mae, rmse, (hit / total if total else 0.0)


def main():
    actual, pred, base = mc.build_price_frame()
    n = len(actual)
    print(f"樣本 n={n}(真實 model_history),最小訓練 {MIN_TRAIN} 天\n")

    # --- walk-forward ---
    wf = []
    adjusted_from = None
    for t in range(n):
        if t < MIN_TRAIN:
            wf.append(pred[t])
            continue
        if adjusted_from is None:
            adjusted_from = t
        a, b = _ols(pred[:t], actual[:t])
        wf.append(a + b * pred[t])

    # --- in-sample(對照組,證明它會高估) ---
    a_is, b_is = _ols(pred, actual)
    is_ = [a_is + b_is * p for p in pred]

    ev = slice(adjusted_from, n) if adjusted_from is not None else slice(0, 0)
    seg = f"[{adjusted_from}:{n}] 共 {n - (adjusted_from or n)} 天"
    print(f"評估區間(有實際調整的那段):{seg}\n")

    # 變動量收縮:批#51 指出水準迴歸的截距是共線性假象,**這才是正確形式**,
    # 也是最終結論所依據的那一組。
    wfd = []
    for t in range(n):
        if t < MIN_TRAIN:
            wfd.append(pred[t])
            continue
        dx = [pred[i] - base[i] for i in range(t)]
        dy = [actual[i] - base[i] for i in range(t)]
        a, b = _ols(dx, dy)
        wfd.append(base[t] + a + b * (pred[t] - base[t]))

    rows = [("raw 原始預測", pred), ("水準收縮 wf", wf),
            ("變動量收縮 wf ★", wfd), ("水準收縮 in-sample(對照)", is_)]
    print(f"{'方法':30s} {'MAE':>8s} {'RMSE':>8s} {'方向命中':>8s}")
    for name, series in rows:
        mae, rmse, hit = _metrics(actual[ev], series[ev], base[ev])
        print(f"{name:30s} {mae:8.2f} {rmse:8.2f} {hit:8.1%}")

    # 逐日:被改好 vs 改壞
    better = worse = 0
    worst = 0.0
    for a, p, w in zip(actual[ev], pred[ev], wf[ev]):
        d = abs(a - w) - abs(a - p)
        if d < -1e-9:
            better += 1
        elif d > 1e-9:
            worse += 1
            worst = max(worst, d)
    print(f"\n逐日:改好 {better} 天、改壞 {worse} 天;單日最大惡化 {worst:.2f} 元")

    # 係數穩定度
    coeffs = []
    for t in range(MIN_TRAIN, n):
        coeffs.append(_ols(pred[:t], actual[:t]))
    if coeffs:
        bs = [b for _, b in coeffs]
        as_ = [a for a, _ in coeffs]
        print(f"係數穩定度:b 介於 {min(bs):.3f}~{max(bs):.3f}"
              f"(全樣本 {b_is:.3f});a 介於 {min(as_):.1f}~{max(as_):.1f}"
              f"(全樣本 {a_is:.1f})")


if __name__ == "__main__":
    main()
