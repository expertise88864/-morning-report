# -*- coding: utf-8 -*-
"""ADR→2330 校準對照的**渲染**(從主模組搬出,refactor_audit ALL-CLEAR)。

**表是投影,不是真相來源**(2026-08-08 生產抓到)。先前
`build_historical_calibration` 直接回傳這段字串,於是證據包裡的
`calibration` 是一整塊 markdown —— 攤平不出任何葉節點,`calibration:`
這個命名空間**一個 ID 都生不出來**,而 prompt 同時告訴模型它是合法的
引用前綴。模型照規則猜了 `calibration:tsm2330_7d_absdev`,被自己的
引用檢查擋掉,整份特化分析作廢、退回舊路徑 —— 連續多日。

數字先算好(結構化 dict,見主模組的 builder;它靠 yfinance 抓資料,
refactor_audit 判 BLOCK 所以留在原地)、表由數字生成,兩邊就不可能
再各說各話。這裡只有純渲染:同一份 dict 進來,同一段字出去。
"""


def render_calibration_table(series: dict) -> str:
    """把結構化校準資料渲染成 legacy prompt 用的那段文字。"""
    s = series if isinstance(series, dict) else {}
    rows = list((s.get("by_date") or {}).items())
    if not rows:
        return f"（{s.get('note') or '無有效對照資料'}）"
    rows_str = "\n".join(
        f"  {d}：TSM 收盤 {r['tsm_pct']:+.2f}% → 2330 開盤 "
        f"{r['tw_open_pct']:+.2f}%（偏離 {r['delta_pct']:+.2f}%）"
        for d, r in rows)
    return (f"近 {s.get('n_days', len(rows))} 個交易日 TSM 漲跌 vs 2330 開盤"
            f"對照（驗證 ADR 預測準確度）：\n{rows_str}\n"
            f"平均偏離 = {s.get('mean_delta_pct', 0.0):+.2f}% "
            f"（正值 = 2330 開盤通常比 ADR 暗示偏高）\n"
            f"平均絕對偏離 = {s.get('mean_abs_delta_pct', 0.0):.2f}% "
            f"（此為預測誤差參考）")


def _calibration_note(obj: dict) -> str:
    """把 calibration 欄位轉成一句人類可讀說明（純文字，render 與 prompt 共用）。"""
    if not isinstance(obj, dict):
        return ""
    cal = obj.get("calibration")
    if not isinstance(cal, dict):
        return ""
    if cal.get("applied"):
        b = cal.get("bias_pct", 0) or 0
        sign = "+" if b >= 0 else ""
        return (f"已自我校正（近 {cal.get('samples')} 日平均偏誤 {sign}{b}%，"
                f"原值 {cal.get('raw')};屬追趕型修正,市場結構驟變時失效率較高）")
    return f"自我校正未套用：{cal.get('reason', '樣本累積中')}"
