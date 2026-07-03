"""Beneish M-score(盈餘操弄偵測)單元測試。"""
import fz_score as fz

# 8 季(供 _ttm end=0 取近 4、end=4 取前 4)+ 5 個資產負債時點(供 _bs_at back=0/4)
_FLOW_DATES = [f"20{y}-{m:02d}-{d:02d}" for y, m, d in
               [(24, 3, 31), (24, 6, 30), (24, 9, 30), (24, 12, 31),
                (25, 3, 31), (25, 6, 30), (25, 9, 30), (25, 12, 31)]]
_BAL_DATES = ["2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"]


def _rows(dates, type_vals):
    """type_vals: {type: [每個 date 的值]} → FinMind 列格式。"""
    out = []
    for typ, vals in type_vals.items():
        for d, v in zip(dates, vals):
            out.append({"date": d, "type": typ, "value": v})
    return out


def _const_flow(types):
    return _rows(_FLOW_DATES, {t: [v] * 8 for t, v in types.items()})


def _const_bal(types):
    return _rows(_BAL_DATES, {t: [v] * 5 for t, v in types.items()})


def test_mscore_clean_constant_company():
    """所有比率=1、TATA=0(NI=CFO)→ M = -4.84 + Σ係數(除 TATA)≈ -2.48 → 正常(<-1.78)。"""
    fs = _const_flow({"Revenue": 100, "GrossProfit": 40, "OperatingExpenses": 20,
                      "IncomeAfterTaxes": 15})
    cf = _const_flow({"Depreciation": 10, "CashFlowsFromOperatingActivities": 15})  # =NI → TATA 0
    bs = _const_bal({"AccountsReceivableNet": 30, "PropertyPlantAndEquipment": 200,
                     "TotalAssets": 500, "CurrentAssets": 150,
                     "CurrentLiabilities": 80, "NoncurrentLiabilities": 120})
    r = fz.compute_mscore(fs, bs, cf)
    assert r and abs(r["mscore"] - (-2.48)) < 0.05
    assert r["mscore_flag"] is False
    assert r["mscore_zone"] == "正常"


def test_mscore_missing_field_returns_empty():
    fs = _const_flow({"Revenue": 100, "GrossProfit": 40, "IncomeAfterTaxes": 15})  # 缺 OperatingExpenses
    cf = _const_flow({"Depreciation": 10, "CashFlowsFromOperatingActivities": 15})
    bs = _const_bal({"AccountsReceivableNet": 30, "PropertyPlantAndEquipment": 200,
                     "TotalAssets": 500, "CurrentAssets": 150,
                     "CurrentLiabilities": 80, "NoncurrentLiabilities": 120})
    assert fz.compute_mscore(fs, bs, cf) == {}


def test_mscore_flags_aggressive_accruals_and_growth():
    """營收暴增(SGI 高)+ 應計極高(NI 遠大於 CFO → TATA 大正)→ M 升破 -1.78 → 留意操弄。"""
    rev = [50] * 4 + [120] * 4              # 近 4 季營收遠高於前 4 季(SGI≈2.4)
    gp = [20] * 4 + [40] * 4
    opex = [10] * 4 + [22] * 4
    ni = [8] * 4 + [40] * 4                 # 帳上獲利暴增
    cfo = [8] * 4 + [2] * 4                 # 但現金流沒跟上 → 高應計
    fs = _rows(_FLOW_DATES, {"Revenue": rev, "GrossProfit": gp,
                             "OperatingExpenses": opex, "IncomeAfterTaxes": ni})
    cf = _rows(_FLOW_DATES, {"Depreciation": [10] * 8,
                             "CashFlowsFromOperatingActivities": cfo})
    # 應收暴衝(DSRI 高)
    ar = [20, 20, 20, 20, 20]
    bs = _rows(_BAL_DATES, {"AccountsReceivableNet": [10, 12, 14, 16, 60],  # 最新時點應收暴增
                            "PropertyPlantAndEquipment": [200] * 5,
                            "TotalAssets": [500] * 5, "CurrentAssets": [150] * 5,
                            "CurrentLiabilities": [80] * 5, "NoncurrentLiabilities": [120] * 5})
    del ar
    r = fz.compute_mscore(fs, bs, cf)
    assert r and r["mscore"] > -1.78
    assert r["mscore_flag"] is True
    assert r["mscore_zone"].startswith("偏高")


def test_http_get_retries_on_5xx(monkeypatch):
    """fz_score._http_get:5xx 重試、下次成功即回;假物件無 status_code 直接回。"""
    calls = {"n": 0}

    class _R:
        def __init__(self, code):
            self.status_code = code

    def fake(url, **kw):
        calls["n"] += 1
        return _R(500 if calls["n"] == 1 else 200)

    monkeypatch.setattr(fz.time, "sleep", lambda *_: None)
    monkeypatch.setattr(fz.requests, "get", fake)
    assert fz._http_get("https://x", retries=2).status_code == 200 and calls["n"] == 2
