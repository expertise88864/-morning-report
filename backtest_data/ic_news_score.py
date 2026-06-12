# -*- coding: utf-8 -*-
"""新聞分數 IC 回測:檢驗 news_catalyst_score 對次日/3日報酬有沒有預測力。

IC(Information Coefficient)= 每個 session 橫斷面上「分數 vs 未來報酬」的 Spearman 秩相關。
IC 均值顯著 > 0 → 有預測力;否則 = 新聞分在排名裡只是噪音。
對照基準:attention_score(結構分)的 IC。
"""
import json
import sys

import numpy as np
from scipy import stats

HIST = "state/model_history.json"


def main() -> None:
    history = json.load(open(HIST, encoding="utf-8"))
    history = [h for h in history if h.get("session_date") and h.get("stocks")]
    history.sort(key=lambda h: h["session_date"])
    sessions = [h["session_date"] for h in history]
    by_date = {h["session_date"]: h["stocks"] for h in history}

    def future_return(code: str, t_idx: int, horizon: int):
        cur = by_date[sessions[t_idx]].get(code)
        fut_idx = t_idx + horizon
        if fut_idx >= len(sessions):
            return None
        fut = by_date[sessions[fut_idx]].get(code)
        if not cur or not fut:
            return None
        c0, c1 = cur.get("close"), fut.get("close")
        if not c0 or not c1:
            return None
        return (c1 / c0 - 1) * 100

    for horizon in (1, 3):
        for field, label in (("news_catalyst_score", "新聞分"),
                             ("attention_score", "結構分(對照)")):
            ics = []
            for t in range(len(sessions) - horizon):
                xs, ys = [], []
                for code, s in by_date[sessions[t]].items():
                    score = s.get(field)
                    if score is None:
                        continue
                    r = future_return(code, t, horizon)
                    if r is None:
                        continue
                    xs.append(float(score))
                    ys.append(r)
                # 橫斷面至少 10 檔且分數有變異才算 IC
                if len(xs) >= 10 and len(set(xs)) > 2:
                    ic = stats.spearmanr(xs, ys).statistic
                    if not np.isnan(ic):
                        ics.append(ic)
            if ics:
                arr = np.array(ics)
                t_stat, p = stats.ttest_1samp(arr, 0.0)
                print(f"[{horizon}d] {label}: sessions={len(arr)} "
                      f"IC均值={arr.mean():+.4f} IC_IR={arr.mean()/arr.std():+.3f} "
                      f"t={t_stat:+.2f} p={p:.4f} 正IC比率={float((arr>0).mean()):.2f}")
            else:
                print(f"[{horizon}d] {label}: 無有效橫斷面(分數無變異或樣本不足)")

    # 額外:只看「有新聞事件(分數非零)」的子集 — 有新聞的股票之後表現是否異於大盤?
    print()
    for horizon in (1, 3):
        diffs = []
        for t in range(len(sessions) - horizon):
            with_news, without = [], []
            for code, s in by_date[sessions[t]].items():
                r = future_return(code, t, horizon)
                if r is None:
                    continue
                (with_news if (s.get("news_catalyst_score") or 0) != 0 else without).append(r)
            if len(with_news) >= 5 and len(without) >= 10:
                diffs.append(np.mean(with_news) - np.mean(without))
        if diffs:
            arr = np.array(diffs)
            t_stat, p = stats.ttest_1samp(arr, 0.0)
            print(f"[{horizon}d] 有新聞 vs 無新聞 平均超額: {arr.mean():+.3f}% "
                  f"(sessions={len(arr)}, t={t_stat:+.2f}, p={p:.4f})")


if __name__ == "__main__":
    sys.exit(main())
