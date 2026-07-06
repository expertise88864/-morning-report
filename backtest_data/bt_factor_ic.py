"""離線回測:技術/動能 + 基本面/估值/籌碼因子的前瞻預測力(IC)—— 用 model_history.json 快照。

每日對「因子值」與「次一交易日報酬(close→close)」做橫斷面 Spearman 等級相關(IC),
再對所有交易日取平均 → 平均 IC、IC 的 t 值(穩定度)、命中率。
IC>0 = 因子高者後續報酬高(順勢有效);IC<0 = 反向(均值回歸/低者較強,如 PER);≈0 = 無預測力。
Spearman 為等級相關,因子原始尺度(市值、PER 等)不需標準化。close→close 無前視偏誤(因子用 d 日、報酬 d→d+1)。

※ 基本面/估值因子(per/margin/roe/市值…)自 2026-06 起才隨每日快照累積(見 morning_report
   _attach_listing_fundamentals 與 _snapshot_for_model);在累積足夠交易日前,其 20 日 IC 會顯示「樣本不足」,
   屬正常——這正是「鋪路先存、夠長再驗、通過才改 radar_score 權重」的設計。
"""
import json
import statistics
from pathlib import Path

MH = Path(__file__).resolve().parent.parent / "state" / "model_history.json"
#   涵蓋 morning_report.MODEL_FEATURES 全 22 項(缺一項 D1 驗收時該因子就拿不出 IC 證據)
#   + 額外基本面/估值因子(op_margin/per… 非模型特徵,但鋪路供日後評估)。
#   凡列於此的名稱都必須是 _snapshot_for_model 的 keep 欄位,否則整欄樣本為 0。
FACTORS = [
    # 技術/動能(歷史最久)
    "pct_5d", "ma20_dist_pct", "vol_ratio_20d", "day_pct", "daily_vol_pct",
    "rel_strength_5d",
    # 基本面/成長/估值(自 2026-06 起累積)
    "rev_yoy_pct", "rev_mom_pct", "rev_surprise_pct", "eps_percentile",
    "op_margin", "net_margin", "roe_q", "per", "yield_pct", "market_cap",
    # 籌碼(中期)
    "foreign_lot", "invest_lot", "foreign_30d_lot", "invest_30d_lot",
    "foreign_streak", "invest_streak", "margin_change_lot", "tdcc_wow_pct",
    "inst_buy_vol_ratio", "short_cover_ratio",
    # 事件/新聞、流動性(模型特徵,先前無 IC 追蹤)
    "news_catalyst_score", "trade_value", "slippage_bps",
]
HORIZONS = (1, 3, 5, 20)


def _spearman(xs, ys):
    """Spearman 等級相關(無 scipy:自算 rank + Pearson)。"""
    n = len(xs)
    if n < 5:
        return None

    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = (sum((rx[i] - mx) ** 2 for i in range(n))) ** 0.5
    dy = (sum((ry[i] - my) ** 2 for i in range(n))) ** 0.5
    return num / (dx * dy) if dx and dy else None


def main():
    days = json.load(open(MH, encoding="utf-8"))
    days = [d for d in days if isinstance(d.get("stocks"), dict)]
    days.sort(key=lambda d: d.get("session_date", ""))
    print(f"model_history 交易日 n={len(days)}  期間 {days[0]['session_date']}..{days[-1]['session_date']}")

    # 每檔 close 序列(用代號跨日 join)
    for h in HORIZONS:
        print(f"\n=== 前瞻 {h} 交易日報酬(close→close)IC ===")
        print("  因子              平均IC    t值   IC>0比率  (n_days)")
        for f in FACTORS:
            ics = []
            for i in range(len(days) - h):
                cur = days[i]["stocks"]
                fut = days[i + h]["stocks"]
                xs, ys = [], []
                for code, s in cur.items():
                    fv = s.get(f)
                    c0 = s.get("close")
                    fc = (fut.get(code) or {}).get("close")
                    if (isinstance(fv, (int, float)) and isinstance(c0, (int, float))
                            and isinstance(fc, (int, float)) and c0 > 0):
                        xs.append(float(fv))
                        ys.append(fc / c0 - 1.0)
                ic = _spearman(xs, ys)
                if ic is not None:
                    ics.append(ic)
            if len(ics) >= 5:
                mean_ic = sum(ics) / len(ics)
                sd = statistics.pstdev(ics) or 1e-9
                t = mean_ic / (sd / len(ics) ** 0.5)
                pos = sum(1 for x in ics if x > 0) / len(ics) * 100
                print(f"  {f:16}  {mean_ic:+.4f}  {t:+5.1f}   {pos:4.0f}%    ({len(ics)})")
            else:
                print(f"  {f:16}  樣本不足")
    print("\n註:|t|>2 約達顯著。負 IC = 高因子值後續較弱(均值回歸/過熱),"
          "正 IC = 順勢有效。可據此檢視 Top5 結構分對各因子的方向是否正確。")


if __name__ == "__main__":
    main()
