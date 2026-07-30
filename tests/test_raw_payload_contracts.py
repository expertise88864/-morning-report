# -*- coding: utf-8 -*-
"""**外部 payload 的真實形狀契約**(第七輪 P1-9)。

第七輪要求「從原始 payload 跑到 state」的契約測試。它一直卡在「取不到真實
回應」—— 而這一輪已經有兩次「猜欄位形狀」的代價,所以不猜。
2026-07-30 直接向各來源取回真實回應,截成小樣本存進 `tests/fixtures/`,
由本檔驗生產解析器吃得下它們。

**為什麼是 fixture 而不是即時打 API**:CI 不該依賴外部服務的可用性
(那會讓 CI 的紅綠反映對方機房而不是我們的程式碼);而 schema 漂移的訊號
本來就該在**更新 fixture 時**被看見 —— 更新 fixture 是一個需要有人看過的動作。

fixture 內容全是公開市場資料,無個資。每份都註明取得日期與原始筆數,
截樣本是為了測試可讀,不是為了讓它通過。
"""
import json
from pathlib import Path

import pytest

import morning_report as mr

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    path = FIXTURES / name
    if not path.exists():
        pytest.fail(f"缺少 fixture {name} —— 契約測試不得因為檔案不見而跳過")
    return json.loads(path.read_text(encoding="utf-8"))


class _Resp:
    """最小的 requests 回應替身;只換掉 HTTP 那一層,其餘走生產程式碼。"""

    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


# ------------------------------------------------------------------ TAIFEX
def test_put_call_ratio_parses_the_real_payload(monkeypatch):
    """`txo_pc_oi_ratio` 的來源。2026-07-30 實測欄位:
    `Date / PutVolume / CallVolume / PutCallVolumeRatio% / PutOI / CallOI /
     PutCallOIRatio%`,值全部是**字串**(含 `%` 欄名的百分比字串)。
    """
    monkeypatch.setattr(mr, "_http_get",
                        lambda *a, **k: _Resp(_load("taifex_put_call_ratio.json")))
    out = mr.fetch_taifex_options_pc_ratio()
    assert out["date"] == "20260729"
    assert out["pc_oi_ratio"] == 84.29 and out["pc_vol_ratio"] == 100.21


def test_large_traders_parses_the_real_payload(monkeypatch):
    """`taifex_top10_net` / `spec_top10_net` / 集中度的來源。

    真實回應是**全市場 1366 筆**(各種契約),解析器要自己挑出 TX 並分辨
    `TypeOfTraders`(0=全部、1=特定法人)。fixture 刻意留一筆非 TX 的列,
    確認過濾真的有在做 —— 不然「挑錯契約」會靜默算出別的商品的籌碼。
    """
    monkeypatch.setattr(mr, "_http_get",
                        lambda *a, **k: _Resp(_load("taifex_large_traders.json")))
    out = mr.fetch_taifex_large_traders()
    assert out["date"] == "20260729"
    for f in ("top10_net", "spec_top10_net", "concentration_pct", "oi_market"):
        assert isinstance(out[f], (int, float)), f"{f} 沒解析出來:{out.get(f)!r}"
    # **釘住是哪一列被採用**,不只是「有值」。fixture 裡的單月份列
    # (202608:Top10Buy 83225 / Top10Sell 79982 / OI 116651)數字完全不同,
    # 若合計過濾壞掉就會拿到 +3243 與 116651 —— 那是別的東西的籌碼,
    # 而且看起來完全正常。
    assert out["top10_net"] == 84346 - 84362, "採用了非 999912(所有契約合計)的列"
    assert out["oi_market"] == 124519
    assert out["spec_top10_net"] == 79351 - 84362, "特定法人(type=1)那列沒取到"


# -------------------------------------------------------------------- TWSE
def test_exdiv_preview_parses_the_real_payload(monkeypatch):
    """除權息預告。民國日期 `1150805`、`CashDividend` 常是空字串(ETF 待公告)。"""
    monkeypatch.setattr(mr, "_http_get",
                        lambda *a, **k: _Resp(_load("twse_exdiv_preview.json")))
    mr._RUN_MANIFEST.pop("exdiv_preview", None)
    try:
        out = mr.fetch_exdiv_preview("2026-07-30")
        assert out and all(r["ex_date"].count("-") == 2 for r in out)
        assert all(r["code"] for r in out)
    finally:
        mr._RUN_MANIFEST.pop("exdiv_preview", None)


def test_trading_halt_parses_the_real_payload(monkeypatch):
    """暫停交易表。欄位是 `TradingHaltDate` / `TradingResumptionDate`,民國格式。"""
    monkeypatch.setattr(mr, "_http_get",
                        lambda *a, **k: _Resp(_load("twse_trading_halt.json")))
    mr._RUN_MANIFEST.pop("corporate_actions", None)
    try:
        out = mr.fetch_trading_halts("2026-07-30")
        assert out and all(r["halt_date"].count("-") == 2 for r in out)
    finally:
        mr._RUN_MANIFEST.pop("corporate_actions", None)


def test_delisted_parses_the_real_payload(monkeypatch):
    """終止上市表。日期是**帶斜線**的民國 `115/06/23` —— 與上面兩張表不同格式,
    這正是「猜形狀」最容易錯的地方。"""
    monkeypatch.setattr(mr, "_http_get",
                        lambda *a, **k: _Resp(_load("twse_delisted.json")))
    saved = list(mr._DEGRADED_STEPS)
    try:
        out = mr.fetch_delisted_codes()
        assert out and all(d.count("-") == 2 for d in out.values())
        assert "corpact:delisted_fetch_failed" not in mr._DEGRADED_STEPS, \
            "真實 payload 不該被判成改版"
    finally:
        mr._DEGRADED_STEPS[:] = saved


def test_every_fixture_is_actually_exercised():
    """**fixture 不得只是躺在那裡。** 沒有這條的話,某個 fixture 對應的測試被
    刪掉或改名之後,檔案還在、看起來有覆蓋,實際上沒有人讀它。
    """
    used = set()
    src = Path(__file__).read_text(encoding="utf-8")
    for path in sorted(FIXTURES.glob("*.json")):
        if f'_load("{path.name}")' in src:
            used.add(path.name)
    missing = sorted({p.name for p in FIXTURES.glob("*.json")} - used)
    assert not missing, f"這些 fixture 沒有任何測試在讀:{missing}"
    assert used, "一份 fixture 都沒被使用 —— 掃描器可能壞了"
