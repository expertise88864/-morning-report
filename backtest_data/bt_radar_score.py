"""離線回測:雷達/Top5 計分『方案』的前瞻報酬力(分位數價差 + IC)。

回答兩個問題:
  (1) 目前評分方式真的有準嗎?——把「MA20 乖離單因子(注意:非正式 ranking_score 公式——正式公式是動能正向計分+極端門檻才扣過熱罰分,此處僅取其唯一有長歷史的『反向乖離』成分做方向診斷)」
      與動能、波動、大型股等方案,放在同一個前瞻報酬框架下比較。
  (2) 「進場時機/買相對低點(避過熱)」這個建議,加進去到底有沒有幫助?——直接做成一個方案
      (偏好『還沒漲、距MA20低』者),看它 vs 動能 vs 大盤平均的前瞻報酬。

方法:每個交易日 t,對當日股票池用『因子等級(rank)』組出各方案分數 → 取分數最高 20%(上分位)
與最低 20%(下分位),計算其『前瞻 H 日 close→close 報酬』均值;再對所有 t 取平均。
上分位 − 池內平均 = 該方案的選股超額;上分位 − 下分位 = 多空價差。同時報 Spearman IC。
close→close、因子用 d 日、報酬用 d→d+H,無前視。

⚠ 僅技術/市值因子有長歷史(~130+ 日)可信;營收/籌碼僅 ~8-13 日、估值/獲利率僅 1 日(見 bt_factor_ic),
   故本檔聚焦『有長歷史』的方案;基本面方案待資料累積數月後再驗。
"""
import sys
from pathlib import Path

# Windows cp950 終端印非 BMP 符號(⚠ 等)會 UnicodeEncodeError(GPT-5.6 四審 P3)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 共用 loader(三審 P1:勿再讀凍結 legacy 單檔)
from model_history_store import load_model_history  # noqa: E402
from backtest_data.bt_stats import newey_west_t  # noqa: E402

HORIZON = 20          # 波段視角(專案既有結論:約 20 日才有訊號)
QUANTILE = 0.20       # 上/下分位各取 20%

# 方案:{名稱: [(欄位, 方向)]};方向 +1=高者分數高、-1=低者分數高(等權合成各因子的 rank)
SCHEMES = {
    "MA20乖離單因子(非正式公式)": [("ma20_dist_pct", -1)],
    "動能(5日漲幅高)":            [("pct_5d", +1)],
    "進場時機/買低(距MA20低+未漲)": [("ma20_dist_pct", -1), ("pct_5d", -1)],
    "波動度(高Beta)":             [("daily_vol_pct", +1)],
    "大型股(市值高)":             [("market_cap", +1)],
    "順勢綜合(動能+市值+量比)":    [("pct_5d", +1), ("market_cap", +1), ("vol_ratio_20d", +1)],
}


def _rank(vals):
    """回傳 {index: 百分位 rank 0..1};並列取平均。缺值的 index 不納入。"""
    idx = [i for i, v in enumerate(vals) if isinstance(v, (int, float))]
    if len(idx) < 5:
        return None
    order = sorted(idx, key=lambda i: vals[i])
    out = {}
    n = len(order)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2 / max(1, n - 1)
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def _spearman(xs, ys):
    n = len(xs)
    if n < 5:
        return None
    rx, ry = _rank(xs), _rank(ys)
    if not rx or not ry:
        return None
    rxs = [rx[i] for i in range(n)]
    rys = [ry[i] for i in range(n)]
    mx, my = sum(rxs) / n, sum(rys) / n
    num = sum((rxs[i] - mx) * (rys[i] - my) for i in range(n))
    dx = (sum((rxs[i] - mx) ** 2 for i in range(n))) ** 0.5
    dy = (sum((rys[i] - my) ** 2 for i in range(n))) ** 0.5
    return num / (dx * dy) if dx and dy else None


def main():
    days = load_model_history(ROOT / "state/model_history.json",  # strict:壞分區即中止
                          ROOT / "state/model_history", strict=True)
    days = [d for d in days if isinstance(d.get("stocks"), dict)]
    days.sort(key=lambda d: d.get("session_date", ""))
    print(f"model_history 交易日 n={len(days)}  期間 {days[0]['session_date']}..{days[-1]['session_date']}")
    print(f"前瞻 {HORIZON} 交易日 close→close;上/下分位各 {QUANTILE:.0%}\n")

    # 先算大盤背景:每日池內平均前瞻報酬(等權)
    bench = []
    for i in range(len(days) - HORIZON):
        cur, fut = days[i]["stocks"], days[i + HORIZON]["stocks"]
        rets = [fut[c]["close"] / s["close"] - 1
                for c, s in cur.items()
                if isinstance(s.get("close"), (int, float)) and s["close"] > 0
                and isinstance((fut.get(c) or {}).get("close"), (int, float))]
        if rets:
            bench.append(sum(rets) / len(rets))
    bench_avg = sum(bench) / len(bench) if bench else 0.0
    print(f"基準:池內等權前瞻 {HORIZON} 日平均報酬 = {bench_avg * 100:+.2f}%（{len(bench)} 個再平衡日）")
    # 倖存者偏誤量測:本回測以「t+H 仍在池內且有收盤」定義可用樣本(無 H 日標籤價可回填離開者)。
    # 存活率越高,偏誤越小;離開前100大/停牌者多為走弱股,排除它們會讓報酬略偏樂觀。
    surv = []
    for i in range(len(days) - HORIZON):
        cur, fut = days[i]["stocks"], days[i + HORIZON]["stocks"]
        base = [c for c, s in cur.items() if isinstance(s.get("close"), (int, float)) and s["close"] > 0]
        if base:
            alive = sum(1 for c in base if isinstance((fut.get(c) or {}).get("close"), (int, float)))
            surv.append(alive / len(base))
    surv_avg = sum(surv) / len(surv) if surv else 0.0
    print(f"樣本存活率:t 日標的於 t+{HORIZON} 仍可取價 = {surv_avg:.1%}"
          f"(其餘因離開前100大/缺價被排除;此為倖存者偏誤來源,存活率越高偏誤越小)\n")

    print(f"{'方案':<26}{'上分位超額':>10}{'多空價差':>10}{'平均IC':>9}{'IC t值':>8}{'勝率':>7}")
    for name, factors in SCHEMES.items():
        top_ex, ls_spread, ics = [], [], []
        for i in range(len(days) - HORIZON):
            cur, fut = days[i]["stocks"], days[i + HORIZON]["stocks"]
            # 僅保留:有收盤、有 H 日後收盤、且本方案所有因子皆有值者(逐方案各取交集,不整日丟棄)
            codes = [c for c, s in cur.items()
                     if isinstance(s.get("close"), (int, float)) and s["close"] > 0
                     and isinstance((fut.get(c) or {}).get("close"), (int, float))
                     and all(isinstance(s.get(fld), (int, float)) for fld, _ in factors)]
            if len(codes) < 20:
                continue
            rets = {c: fut[c]["close"] / cur[c]["close"] - 1 for c in codes}
            # 合成分數 = 各因子方向×百分位 rank 之和(codes 已確保每個因子皆有值)
            score = {c: 0.0 for c in codes}
            for fld, sign in factors:
                rk = _rank([cur[c].get(fld) for c in codes])
                for j, c in enumerate(codes):
                    score[c] += sign * rk[j]
            ranked = sorted(codes, key=lambda c: score[c], reverse=True)
            k = max(1, int(len(ranked) * QUANTILE))
            top, bot = ranked[:k], ranked[-k:]
            avg = sum(rets.values()) / len(rets)
            top_ret = sum(rets[c] for c in top) / k
            bot_ret = sum(rets[c] for c in bot) / k
            top_ex.append(top_ret - avg)
            ls_spread.append(top_ret - bot_ret)
            ic = _spearman([score[c] for c in codes], [rets[c] for c in codes])
            if ic is not None:
                ics.append(ic)
        if len(top_ex) >= 5 and ics:
            te = sum(top_ex) / len(top_ex) * 100
            ls = sum(ls_spread) / len(ls_spread) * 100
            mic = sum(ics) / len(ics)
            # 20 日重疊視窗 → Newey-West lag=19 為準(四審 P1)
            t = newey_west_t(ics, HORIZON - 1) or 0.0
            win = sum(1 for x in top_ex if x > 0) / len(top_ex) * 100
            print(f"{name:<26}{te:>+9.2f}%{ls:>+9.2f}%{mic:>+9.4f}{t:>+8.1f}{win:>6.0f}%")
        else:
            print(f"{name:<26}{'樣本不足(此方案因子歷史太短)':>20}")

    print("\n判讀:")
    print("· 上分位超額>0 且 IC 的 |t|>2 → 該方案選出的前 20% 確實跑贏池內平均、且穩定。")
    print("· 『進場時機/買低』若超額為負,代表此窗格(多頭)中『買相對低點/避過熱』反而拖累——")
    print("  與 bt_factor_ic 顯示『距MA20正 IC(動能)』一致;此建議不宜進計分,只宜當『追高風險』提示。")
    print("· 營收/籌碼/估值方案未列:歷史僅 1~13 日,前瞻 20 日視窗=0,須待累積數月(見 bt_factor_ic)。")
    print("\n⚠ 限制(結論勿過度外推):")
    print("  (1) 單一多頭區間(基準 +8.48%),高Beta/動能/大型股之優勢多為此 regime 的市場貝他,非穩定 alpha;")
    print("      換空頭/盤整可能反轉。(2) 樣本為『每日市值前100大』,非雷達的股癌題材中小型股,因子行為未必可轉移。")
    print("      (3) 倖存者偏誤(見上方存活率)+ 以當日收盤為進場價(訊號與成交同價,偏樂觀)。")
    print("  → 視為『方向性診斷』,不可當作可交易績效保證;真正定論需多 regime + 雷達同宇宙樣本。")


if __name__ == "__main__":
    main()
