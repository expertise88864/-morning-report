"""每月因子 IC 報告產生器(給 monthly-ic-report workflow 用,也可本機跑)。

跑 bt_factor_ic + bt_radar_score(皆純 stdlib、讀 ../state/model_history.json),
把輸出寫成 reports/YYYY-MM.md。資料未累積足夠交易日前多顯示「樣本不足」,屬正常
(見 OPTIMIZATION_PLAN.md 的 D1:約 2026-09 後基本面因子才驗得出 20 日 IC)。

用法:python backtest_data/monthly_report.py [YYYY-MM]
"""
import contextlib
import datetime as dt
import io
import re
import sys
from pathlib import Path

# Windows cp950 終端印非 BMP 符號(⚠ 等)會 UnicodeEncodeError(GPT-5.6 四審 P3)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _run(module_name: str) -> str:
    buf = io.StringIO()
    try:
        mod = __import__(module_name)
        with contextlib.redirect_stdout(buf):
            mod.main()
    except Exception as e:   # 單一腳本失敗不擋整份報告
        buf.write(f"\n(執行失敗: {type(e).__name__}: {e})\n")
    return buf.getvalue().strip() or "(無輸出)"


def d1_readiness(ic_text: str) -> str:
    """掃 bt_factor_ic 的『前瞻 20 交易日』段,看基本面因子(rev_yoy/op_margin/per…)的 n_days
    是否 ≥30 → 回傳 D1 就緒度橫幅(V2-D1:到期自動提醒使用者可啟動因子 IC 驗收)。"""
    targets = ("rev_yoy_pct", "rev_mom_pct", "rev_surprise_pct", "op_margin",
               "net_margin", "roe_q", "per", "yield_pct")
    m = re.search(r"前瞻 20 交易日.*?(?=前瞻 \d+ 交易日|判讀|限制|\Z)", ic_text, re.S)
    if not m:   # 定位不到 20 日段(輸出異常/截斷)→ 視為尚未就緒(絕不用其它 horizon 誤判為就緒)
        return "> ⏳ 無法定位『前瞻 20 交易日』段(bt_factor_ic 輸出異常)→ D1 就緒度未知,視為尚未就緒。\n\n"
    seg = m.group(0)
    ndays = {}
    for line in seg.splitlines():
        parts = line.split()
        if parts and parts[0] in targets:
            n = re.search(r"\((\d+)\)\s*$", line)
            if n:
                ndays[parts[0]] = int(n.group(1))
    ready = [f"{k}={v}日" for k, v in sorted(ndays.items()) if v >= 30]
    if ready:
        return ("> ✅ **基本面因子 20 日樣本已足**(" + "、".join(ready) +
                ")→ 可啟動 **D1 因子 IC 驗收**:對顯著者(|t|>2、方向正確)提權重變更提案、"
                "再經 bt_top5 複驗、通知使用者拍板(見 OPTIMIZATION_PLAN 的 D1)。\n\n")
    maxn = max(ndays.values(), default=0)
    return (f"> ⏳ 基本面因子 20 日樣本仍不足(目前最多 {maxn} 日,需 ≥30)→ D1 尚未就緒,繼續累積。\n\n")


def main() -> None:
    month = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().strftime("%Y-%m")
    ic = _run("bt_factor_ic")
    rs = _run("bt_radar_score")
    readiness = d1_readiness(ic)
    out = Path(__file__).resolve().parent / "reports" / f"{month}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"# 因子 IC 月報 {month}\n\n"
        "> 自動產出(monthly-ic-report workflow)。**這是方向性診斷,非可交易績效保證**;\n"
        "> 基本面/估值/籌碼因子自 2026-06 起才逐日累積,樣本不足前顯示「樣本不足」屬正常。\n"
        "> 任何計分權重變更仍須:IC 顯著(|t|>2、方向正確)+ bt_top5 複驗 + 使用者同意。\n\n"
        + readiness
        + f"## bt_factor_ic(各因子前瞻 IC)\n```\n{ic}\n```\n\n"
        f"## bt_radar_score(計分方案分位數超額)\n```\n{rs}\n```\n",
        encoding="utf-8",
    )
    print(f"[monthly_report] 寫入 {out}")


if __name__ == "__main__":
    main()
