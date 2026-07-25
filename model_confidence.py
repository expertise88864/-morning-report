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
    if pv["consistent"] > 0.10:
        print(f"  SPA consistent p={pv['consistent']:.3f} > 0.10 → "
              "**尚無法宣稱任何模型贏過隨機漫步**(這對短期價格預測是常見結果,"
              "不代表系統無用——方向與區間的價值不在點預測誤差裡)。")
    else:
        print(f"  SPA consistent p={pv['consistent']:.3f} → 最佳模型顯著優於隨機漫步。")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
