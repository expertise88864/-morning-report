# -*- coding: utf-8 -*-
"""模型信賴集合(MCS)+ 優越預測能力檢定(SPA):回答「合成到底有沒有比單一模型好」。

**為什麼需要這支工具**

現有評估用 Newey-West HAC t 統計比較模型,但那是**兩兩比較、沒有多重檢定校正**。
系統每天在 model1~4 與 weighted_final 之間隱性做選擇,這正是資料窺探
(data snooping)的溫床:五個模型裡「看起來最好的那個」,有相當機率只是運氣。

- **MCS**(Hansen–Lunde–Nason 2011):不需要指定基準,直接給出「在給定信心水準下,
  績效無法被區分的模型集合」。集合若只剩一個 → 它真的比較好;集合若還剩全部 →
  資料量不足以區分,**此時應選最簡單的那個**,而不是選樣本內最好的那個。
- **SPA**(Hansen 2005,White's Reality Check 的改良):檢定「最好的模型是否真的
  贏過基準」,對多重比較做了校正。基準取隨機漫步(昨收),那是短期價格預測的
  誠實對照。

兩者都用 stationary bootstrap 處理序列相關,不假設常態。

**定位**:與 overfit_check.py 相同——純離線研究工具,**不進每日信、不影響生產路徑**。
`arch` 只在跑這支工具時才需要,未列入 requirements.lock(每日信的依賴保持不變)。

用法:
    pip install arch
    python model_confidence.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

STATE_FILE = Path("state/history.json")

# 待比較的模型:history.json 的欄位名 → 顯示名
MODELS_2330 = {
    "model1_2330": "model1",
    "model2_2330": "model2",
    "model3_2330": "model3",
    "model4_2330": "model4",
    "weighted_final_2330": "weighted_final",
}

CONFIDENCE = 0.90          # MCS 信心水準
BOOTSTRAP_REPS = 5000
BLOCK_SIZE = 5             # stationary bootstrap 平均區塊長度(約一週交易日)
MIN_PAIRS = 20             # 少於此數不做檢定:結論不會有意義


def _load_history() -> list[dict]:
    raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    return raw.get("entries") or raw.get("history") or []


def _actual_open(mh: dict, session: str, code: str = "2330"):
    """目標交易日的實際開盤價。label_prices 優先(那是標籤價的權威來源)。"""
    rec = mh.get(session)
    if not rec:
        return None
    lp = (rec.get("label_prices") or {}).get(code) or {}
    if lp.get("open") is not None:
        return lp["open"]
    return ((rec.get("stocks") or {}).get(code) or {}).get("open")


def _prev_close(mh: dict, sessions: list[str], target: str, code: str = "2330"):
    """隨機漫步基準:**目標交易日之前**最近一個交易日的收盤價。

    這裡曾經踩過一個會讓整份結論反轉的坑:history.json 的 `date` 與
    `target_session_date` 有 50/51 是**同一天**(晨報在當日開盤前發出,預測的就是
    當天開盤)。若直接拿 `date` 那天的收盤當基準,等於用「今天的收盤」預測
    「今天的開盤」——那是前視偏誤,會讓隨機漫步基準假性地強到打敗所有模型。
    必須嚴格取 target 之前的那個交易日。
    """
    idx = sessions.index(target) if target in sessions else -1
    if idx <= 0:
        return None
    rec = mh.get(sessions[idx - 1])
    if not rec:
        return None
    lp = (rec.get("label_prices") or {}).get(code) or {}
    if lp.get("close") is not None:
        return lp["close"]
    return ((rec.get("stocks") or {}).get(code) or {}).get("close")


def build_loss_frame():
    """組出各模型的逐日損失序列(絕對百分誤差)。

    回傳 (DataFrame[日期 × 模型], 基準損失 Series, 診斷 dict)。
    刻意用**絕對百分誤差**而非平方誤差:2330 股價數千元,平方誤差會被少數跳空日
    完全主導,MCS 等於只在比較「誰在那三天比較準」。
    """
    import pandas as pd

    sys.path.insert(0, str(Path(__file__).parent))
    import morning_report as mr

    mh = {r.get("session_date"): r for r in mr.load_model_history()}
    sessions = sorted(s for s in mh if s)
    rows, dates, base = [], [], []
    skipped = {"no_target": 0, "no_actual": 0, "no_base": 0, "incomplete_models": 0}

    for entry in _load_history():
        target = entry.get("target_session_date")
        if not target:
            skipped["no_target"] += 1
            continue
        actual = _actual_open(mh, target)
        if actual is None or actual <= 0:
            skipped["no_actual"] += 1
            continue
        prev = _prev_close(mh, sessions, target)
        if prev is None or prev <= 0:
            skipped["no_base"] += 1
            continue
        preds = {name: entry.get(field) for field, name in MODELS_2330.items()}
        if any(v is None for v in preds.values()):
            # 缺任一模型就整列剔除:MCS 要求所有模型在同一組樣本上比較,
            # 各自用不同天數會讓「誰比較準」變成「誰挑到比較好的日子」。
            skipped["incomplete_models"] += 1
            continue
        rows.append({n: abs(v / actual - 1.0) * 100.0 for n, v in preds.items()})
        base.append(abs(prev / actual - 1.0) * 100.0)
        dates.append(target)

    losses = pd.DataFrame(rows, index=pd.Index(dates, name="target_session"))
    baseline = pd.Series(base, index=losses.index, name="random_walk")
    return losses, baseline, skipped


def run(confidence: float = CONFIDENCE) -> int:
    try:
        from arch.bootstrap import MCS, SPA
    except ImportError:
        print("需要 arch 套件:pip install arch", file=sys.stderr)
        print("(僅離線研究用,未列入 requirements.lock)", file=sys.stderr)
        return 2

    losses, baseline, skipped = build_loss_frame()
    n = len(losses)
    print(f"可用樣本:{n} 個交易日")
    print(f"剔除:{skipped}")
    if n < MIN_PAIRS:
        print(f"\n樣本不足({n} < {MIN_PAIRS}),不做檢定——"
              "小樣本下 MCS 幾乎必然保留全部模型,那個結果沒有資訊量。")
        return 1

    print("\n=== 平均絕對百分誤差(越小越好)===")
    means = losses.mean().sort_values()
    for name, v in means.items():
        print(f"  {name:16} {v:6.3f}%")
    print(f"  {'random_walk(基準)':16} {baseline.mean():6.3f}%")

    print(f"\n=== 模型信賴集合 MCS({confidence:.0%})===")
    mcs = MCS(losses, size=1 - confidence, reps=BOOTSTRAP_REPS,
              block_size=BLOCK_SIZE, method="R", seed=20260725)
    mcs.compute()
    included = list(mcs.included)
    excluded = list(mcs.excluded)
    print(f"  留在集合內:{included}")
    print(f"  被排除    :{excluded or '(無)'}")
    print("\n  各模型 MCS p 值(越小越可能被排除):")
    for name, p in mcs.pvalues.sort_values(by="Pvalue").iterrows():
        print(f"    {str(name):16} {float(p.iloc[0]):.3f}")

    print("\n=== SPA:最佳模型 vs 隨機漫步基準 ===")
    best = means.index[0]
    spa = SPA(baseline.values, losses.values, reps=BOOTSTRAP_REPS,
              block_size=BLOCK_SIZE, seed=20260725)
    spa.compute()
    pv = spa.pvalues
    print(f"  樣本內最佳:{best}(平均誤差 {means.iloc[0]:.3f}%)")
    print(f"  SPA p 值 — lower {pv['lower']:.3f} / consistent {pv['consistent']:.3f}"
          f" / upper {pv['upper']:.3f}")
    print("  (虛無假設=沒有任何模型優於基準;p 大 → 無法宣稱贏過隨機漫步)")

    # r1(Codex):**新診斷必須由 run() 呼叫**。先前它們定義在 __main__ 之後且
    # 沒被呼叫,跑文件裡那行 `python model_confidence.py` 只會看到舊的 SPA 結論
    # ——而那正是本批要指出「因巢狀比較而不成立」的那個結論。
    _print_nested_diagnostics()

    print("\n=== 判讀 ===")
    if len(included) == len(losses.columns):
        print(f"  MCS 保留全部 {len(included)} 個模型 → 目前樣本無法區分它們的優劣。")
        print("  依「無法區分時選最簡單者」原則,不應因樣本內排名而偏好任何一個;")
        print("  合成的價值此刻**未被證實**,但也未被否證。")
    elif len(included) == 1:
        print(f"  MCS 只留下 {included[0]} → 它顯著優於其餘模型。")
    else:
        print(f"  MCS 留下 {len(included)} 個:{included}。這些之間無法區分,"
              "應在其中選最簡單/最穩健者。")
    print(f"  SPA consistent p={pv['consistent']:.3f} —— **但 SPA 對巢狀比較"
          "(合成模型 vs 隨機漫步)在理論上不成立**,此處僅列出供對照,"
          "結論以上方 Clark-West 為準。")
    return 0


def build_price_frame():
    """(實際開盤, weighted_final 預測, 隨機漫步基準)三組**價格**序列。

    CW 與 MZ 都需要**有號**的實際值與預測值,不能用 build_loss_frame() 的絕對
    百分誤差——絕對值失去正負號後,CW 公式裡的 (small−big)² 那一項就不再等於
    「兩個預測之間的距離」,算出來的東西沒有意義。這是接線時自測抓到的。
    """
    sys.path.insert(0, str(Path(__file__).parent))
    import morning_report as mr
    mh = {r.get("session_date"): r for r in mr.load_model_history()}
    sessions = sorted(s for s in mh if s)
    act, pred, base = [], [], []
    for entry in _load_history():
        target = entry.get("target_session_date")
        if not target:
            continue
        a = _actual_open(mh, target)
        prev = _prev_close(mh, sessions, target)
        w = entry.get("weighted_final_2330")
        if None in (a, prev, w) or a <= 0 or prev <= 0:
            continue
        act.append(float(a))
        pred.append(float(w))
        base.append(float(prev))
    return act, pred, base


def _print_nested_diagnostics() -> None:
    """巢狀比較與預測效率診斷。SPA/MCS 對巢狀模型不成立,這裡才是正確的比較。"""
    act, pred, base = build_price_frame()
    print(f"\n=== Clark-West:合成模型 vs 隨機漫步(巢狀正確檢定,n={len(act)})===")
    stat, p, mean = clark_west(act, pred, base)
    if stat is None:
        print("  樣本不足")
    else:
        verdict = ("合成模型顯著含有隨機漫步以外的資訊"
                   if p is not None and p < 0.05 else "尚無法宣稱")
        print(f"  CW stat={stat:.3f}(已含 HLN 小樣本修正)單尾 p={p:.4f} → {verdict}")
        print("  註:SPA/MCS 對此比較在理論上不成立,結論以本項為準。")

    print("\n=== Mincer-Zarnowitz:預測是否無偏且有效率 ===")
    mz = mincer_zarnowitz(act, pred)
    if not mz:
        print("  樣本不足")
    else:
        print(f"  a={mz['a']:.2f}(t vs 0 = {mz['a_t_vs_0']:.2f})  "
              f"b={mz['b']:.4f}(t vs 1 = {mz['b_t_vs_1']:.2f})")
        print(f"  聯合檢定 (a,b)=(0,1):Wald={mz['joint_wald']:.2f} p={mz['joint_p']:.4f}")
        print(f"  {mz['shrink_hint'] or '未偵測到系統性偏誤或過度反應'}")

    print("\n=== Pesaran-Timmermann:方向預測能力 ===")
    import numpy as np
    a = np.asarray(act) - np.asarray(base)
    q = np.asarray(pred) - np.asarray(base)
    s, pp, hit, exp = pesaran_timmermann(a, q)
    if s is None:
        print(f"  樣本不足或變異數退化(命中率 {hit:.1%} 期望 {exp:.1%})"
              if hit is not None else "  樣本不足")
    else:
        print(f"  命中率 {hit:.1%} vs 邊際分布期望 {exp:.1%};stat={s:.3f} p={pp:.4f}")
        print("  (PT 已校正「指數本來就偏漲、每天猜漲也有高命中率」的假象)")

    print("\n=== 區間鋒利度 ===")
    print("  Interval/Winkler score 需要區間上下界;接上 forecast ledger 後啟用。")


# ============================================================================
# 批#48:巢狀比較、小樣本修正、區間鋒利度、預測效率
#
# 為什麼需要這一段(這是**正確性**問題,不是新功能):
#
# DM / SPA / MCS 對「巢狀模型」在理論上不成立。「多模型合成 vs 隨機漫步(前日
# 收盤)」正是典型的巢狀比較——大模型在虛無假設下等價於小模型,loss differential
# 的極限分布**不是常態**,統計量會系統性偏向「無法拒絕」。
# 也就是說,本檔上半部跑出的「SPA consistent p=0.121、尚無法宣稱贏過基準」這個
# 結論,可能是被錯誤的檢定壓出來的,而不是模型真的不夠好。
# ============================================================================

def clark_west(actual, big, small, horizon: int = 1):
    """Clark-West adjusted MSPE(Clark & West 2007):巢狀模型的正確比較。

    核心想法:大模型在 H0 下多估了一堆真值為 0 的參數,這些純噪音會**抬高**它的
    MSPE。CW 把這個「估計噪音」項從 MSPE 差值中扣掉,還原出乾淨的比較:

        f_t = (y-small)² − [(y-big)² − (small-big)²]

    正的平均值代表大模型真的有訊息,再對 f_t 做單尾 t 檢定(H0: E[f]=0)。
    刻意用單尾:CW 的虛無假設是「小模型才是真模型」,對立假設只有一個方向。

    **統計量已內含 HLN 小樣本修正,p 值以 t(n-1) 計算**(不是常態)——
    這兩件事必須一起做,否則回傳的 p 與宣稱的檢定不一致。

    回傳 (cw 統計量, 單尾 p 值, 平均調整後差值)。樣本不足回 (None, None, None)。
    """
    import numpy as np
    y = np.asarray(actual, dtype=float)
    b = np.asarray(big, dtype=float)
    s = np.asarray(small, dtype=float)
    if min(len(y), len(b), len(s)) < MIN_PAIRS:
        return None, None, None
    f = (y - s) ** 2 - ((y - b) ** 2 - (s - b) ** 2)
    mean = float(f.mean())
    # 用 HAC(Newey-West)標準誤:單日預測仍可能有序列相關
    se = _nw_se(f)
    if not se:
        return None, None, mean
    n = len(f)
    # r1(Codex):**p 值必須由修正後的統計量、以 t(n-1) 算**。先前回傳的是未修正
    # 統計量搭常態 CDF,而 docstring 與 commit 都聲稱「HLN 修正後」——在門檻附近
    # 會給出與宣稱不同的結論。
    raw = mean / se
    stat = hln_correction(raw, n, horizon)
    return stat, _t_sf(stat, n - 1), mean


def _nw_se(x, lags: int | None = None) -> float | None:
    """Newey-West HAC 標準誤(平均數的)。lags 預設用 floor(4*(n/100)^(2/9))。"""
    import numpy as np
    a = np.asarray(x, dtype=float)
    n = len(a)
    if n < 3:
        return None
    if lags is None:
        lags = int(4 * (n / 100.0) ** (2.0 / 9.0))
    d = a - a.mean()
    gamma0 = float((d * d).sum() / n)
    s = gamma0
    for k in range(1, max(1, lags) + 1):
        if k >= n:
            break
        gk = float((d[k:] * d[:-k]).sum() / n)
        s += 2.0 * (1.0 - k / (lags + 1.0)) * gk
    if s <= 0:
        return None
    return float((s / n) ** 0.5)


def _t_sf(t: float, dof: int) -> float:
    """t 分布的單尾上尾機率。無 scipy 依賴,用不完全 beta 的連分數展開。"""
    if dof <= 0:
        return float("nan")
    x = dof / (dof + t * t)
    a, b = dof / 2.0, 0.5
    ib = _betainc(a, b, x)
    p = 0.5 * ib
    return p if t > 0 else 1.0 - p


def _betainc(a: float, b: float, x: float) -> float:
    """正則化不完全 beta 函數 I_x(a,b)(Lentz 連分數;僅供 t 分布用)。"""
    import math
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    # r2(七維度審查,P1)**實跑確認**:Lentz 連分數只在 x < (a+1)/(a+b+2) 收斂,
    # 否則必須用對稱式 I_x(a,b) = 1 − I_{1−x}(b,a)。這裡 x = dof/(dof+t²),
    # **t→0 時 x→1**,恆在收斂域外,300 次迭代遠不足以收斂。
    # 實測 dof=49、t=0.001 時本函式回 p=0.0227,真值 0.4996 ——
    # 會把「毫無證據」報成 p<0.05 顯著。失敗方向是**假陽性**,
    # 恰好是這支工具最不該犯的方向(現有測試取樣的 t=1.96/2.086/0.0 三點
    # 剛好全部避開失效帶:前兩點在收斂域內,t=0 走 x>=1 短路分支碰巧正確)。
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _betainc(b, a, 1.0 - x)
    lbeta = (math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b))
    front = math.exp(math.log(x) * a + math.log(1 - x) * b - lbeta) / a
    f, c, d = 1.0, 1.0, 0.0
    for i in range(0, 300):
        m = i // 2
        if i == 0:
            num = 1.0
        elif i % 2 == 0:
            num = (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            num = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + num * d
        d = 1e-30 if abs(d) < 1e-30 else d
        d = 1.0 / d
        c = 1.0 + num / c
        c = 1e-30 if abs(c) < 1e-30 else c
        f *= c * d
        if abs(1.0 - c * d) < 1e-10:
            break
    return front * (f - 1.0)


def _norm_cdf(x: float) -> float:
    import math
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def hln_correction(stat: float, n: int, horizon: int = 1) -> float:
    """Harvey-Leybourne-Newbold 小樣本修正。

    DM 在小樣本下**過度拒絕虛無假設**。HLN 把統計量乘上
        sqrt((T + 1 - 2h + h(h-1)/T) / T)
    並改用 t(T-1) 而非常態臨界值。文獻建議 T<20 或 h>1 時必用——本系統的 T 只有
    數十到數百,加上去等於免費降低假陽性。
    """
    if n <= 1:
        return stat
    h = max(1, int(horizon))
    adj = (n + 1 - 2 * h + h * (h - 1) / n) / n
    return float(stat * (max(adj, 1e-9) ** 0.5))


def interval_score(actual, lower, upper, alpha: float = 0.2):
    """Interval(Winkler)score:**同時**懲罰過寬與漏失。越小越好。

        (u−l) + (2/α)(l−y)·1{y<l} + (2/α)(y−u)·1{y>u}

    為什麼需要:conformal PID 保證覆蓋率收斂到目標,但**一個無窮寬的區間也能
    通過**。現有評估只驗覆蓋率、沒驗鋒利度,等於有一半沒在看。
    這個分數讓「窄但偶爾漏」與「寬但總是包住」變成可比的**單一數字**,
    可以直接進 forecast ledger 逐日累積。
    """
    import numpy as np
    y = np.asarray(actual, dtype=float)
    lo = np.asarray(lower, dtype=float)
    hi = np.asarray(upper, dtype=float)
    if not (len(y) == len(lo) == len(hi)) or len(y) == 0:
        return None
    width = hi - lo
    below = np.where(y < lo, (2.0 / alpha) * (lo - y), 0.0)
    above = np.where(y > hi, (2.0 / alpha) * (y - hi), 0.0)
    return float((width + below + above).mean())


def mincer_zarnowitz(actual, pred):
    """Mincer-Zarnowitz 迴歸 y = a + b·ŷ,檢定 a=0, b=1。

    **這是唯一一個能直接改善預測本身的診斷**:其他方法只告訴你「好不好」,
    這個告訴你「怎麼改」——若 b 顯著小於 1,代表預測**過度反應**,
    把預測往均值收縮(shrink)就能立刻降低 MSE。

    回傳 dict(a, b, b_se, b_t_vs_1, n, shrink_hint)。樣本不足回 {}。
    """
    import numpy as np
    y = np.asarray(actual, dtype=float)
    x = np.asarray(pred, dtype=float)
    n = len(y)
    if n < MIN_PAIRS or n != len(x):
        return {}
    X = np.column_stack([np.ones(n), x])
    try:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    except np.linalg.LinAlgError:
        return {}
    resid = y - X @ beta
    dof = n - 2
    if dof <= 0:
        return {}
    sigma2 = float((resid ** 2).sum() / dof)
    try:
        cov = sigma2 * np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return {}
    b_se = float(cov[1, 1] ** 0.5)
    a_se = float(cov[0, 0] ** 0.5)
    a, b = float(beta[0]), float(beta[1])
    # r1(Codex):**MZ 的虛無假設是 (a,b)=(0,1) 兩個限制**,只驗 b 會漏掉純加法
    # 偏誤(actual ≈ pred + 10 時 b≈1、只有 a 偏離)——那是實打實的預測無效率,
    # 卻會靜默通過。補聯合 Wald 檢定。
    r = np.array([a - 0.0, b - 1.0])
    try:
        wald = float(r @ np.linalg.inv(cov) @ r)
    except np.linalg.LinAlgError:
        wald = None
    # W ~ chi2(2);chi2(2) 的上尾機率有封閉解 exp(-W/2)
    import math
    joint_p = float(math.exp(-wald / 2.0)) if wald is not None else None
    b_t = (b - 1.0) / b_se if b_se else None
    a_t = a / a_se if a_se else None
    hints = []
    if b_t is not None and b_t < -2.0:
        hints.append("預測過度反應(b<1),建議往均值收縮(改用 a + b*pred)")
    # r2(七維度審查)**實跑確認,原建議是有害的**:本檢定跑在**價格水準**上,
    # 而水準迴歸中 a ≈ ȳ − b·x̄,截距與斜率幾乎完全負相關——「a≠0」是「b≠1」的
    # 機械推論,不是獨立的加法偏誤。真實資料(n=48、平均價位 2364.9)實測:
    #   真實平均偏誤只有 +2.02 元,水準版卻報 a=422.12(t=3.67)
    #   照原建議「扣掉截距」:MAE 25.66 → 424.14(**惡化 16 倍**)
    #   改用 a+b*pred 校準:    MAE 25.66 → 21.89(有效)
    #   差分版 MZ 對照:a=2.36(t=0.54)→ 無加法偏誤,證實水準版截距是假訊號
    # 既有測試用 truth~N(100,10)(白噪音、非 I(1)、均值離 0 不遠),那個設定下
    # 水準迴歸沒問題——測試等於針對實作校準過,真實序列才露餡。
    # 只在**斜率沒有顯著偏離 1** 時,a≠0 才可能是真的加法偏誤。
    if (a_t is not None and abs(a_t) > 2.0
            and b_t is not None and abs(b_t) <= 2.0):
        hints.append("預測有系統性偏誤(a≠0 且 b≈1),建議整體平移 a")
    return {
        "a": a, "a_se": a_se, "a_t_vs_0": a_t,
        "b": b, "b_se": b_se, "b_t_vs_1": b_t, "n": n,
        "joint_wald": wald, "joint_p": joint_p,
        "shrink_hint": ";".join(hints),
    }


def pesaran_timmermann(actual_dir, pred_dir):
    """Pesaran-Timmermann 方向準確度檢定。

    命中率的 sign test,但**校正了兩序列各自邊際分布造成的假象**——指數本來就
    55% 的天數上漲,你每天猜「漲」就有 55% 命中率,那不是預測力。
    n≈50 就有意義,適合本系統的樣本量。

    回傳 (統計量, 雙尾 p 值, 實際命中率, 期望命中率)。樣本不足回四個 None。
    """
    import numpy as np
    a = (np.asarray(actual_dir) > 0).astype(float)
    p = (np.asarray(pred_dir) > 0).astype(float)
    n = len(a)
    if n < MIN_PAIRS or n != len(p):
        return None, None, None, None
    hit = float((a == p).mean())
    pa, pp = float(a.mean()), float(p.mean())
    exp = pa * pp + (1 - pa) * (1 - pp)
    var_hit = exp * (1 - exp) / n
    var_exp = (((2 * pa - 1) ** 2) * pp * (1 - pp) / n
               + ((2 * pp - 1) ** 2) * pa * (1 - pa) / n
               + 4 * pp * pa * (1 - pp) * (1 - pa) / (n ** 2))
    denom = var_hit - var_exp
    if denom <= 0:
        return None, None, hit, exp
    stat = (hit - exp) / (denom ** 0.5)
    return float(stat), float(2 * (1 - _norm_cdf(abs(stat)))), hit, exp


if __name__ == "__main__":
    raise SystemExit(run())
