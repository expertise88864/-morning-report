"""每月因子 IC 報告產生器(給 monthly-ic-report workflow 用,也可本機跑)。

跑 bt_factor_ic + bt_radar_score(皆純 stdlib、讀 ../state/model_history.json),
把輸出寫成 reports/YYYY-MM.md。資料未累積足夠交易日前多顯示「樣本不足」,屬正常
(見 OPTIMIZATION_PLAN.md 的 D1:約 2026-09 後基本面因子才驗得出 20 日 IC)。

用法:python backtest_data/monthly_report.py [YYYY-MM]
"""
import contextlib
import datetime as dt
import io
import sys
from pathlib import Path

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


def main() -> None:
    month = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().strftime("%Y-%m")
    ic = _run("bt_factor_ic")
    rs = _run("bt_radar_score")
    out = Path(__file__).resolve().parent / "reports" / f"{month}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"# 因子 IC 月報 {month}\n\n"
        "> 自動產出(monthly-ic-report workflow)。**這是方向性診斷,非可交易績效保證**;\n"
        "> 基本面/估值/籌碼因子自 2026-06 起才逐日累積,樣本不足前顯示「樣本不足」屬正常。\n"
        "> 任何計分權重變更仍須:IC 顯著(|t|>2、方向正確)+ bt_top5 複驗 + 使用者同意。\n\n"
        f"## bt_factor_ic(各因子前瞻 IC)\n```\n{ic}\n```\n\n"
        f"## bt_radar_score(計分方案分位數超額)\n```\n{rs}\n```\n",
        encoding="utf-8",
    )
    print(f"[monthly_report] 寫入 {out}")


if __name__ == "__main__":
    main()
