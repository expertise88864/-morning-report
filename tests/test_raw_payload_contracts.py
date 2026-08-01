# -*- coding: utf-8 -*-
"""**外部 payload 的真實形狀契約:raw payload → state**(第七輪 P1-9)。

第七輪要求「從原始 payload 跑到 state」的契約測試。它一直卡在「取不到真實
回應」—— 而這一輪已經有兩次「猜欄位形狀」的代價,所以不猜。
2026-07-30 直接向各來源取回真實回應,截成小樣本存進 `tests/fixtures/`,
由本檔驗**生產程式碼**吃得下它們。

r1(Codex,P2):第一版**停在 `fetch_*` 的回傳值**,而這個檔案的名字宣稱的是
「到 state」—— 下游對應或接線改掉時,`txo_pc_oi_ratio` 可以在歷史列裡缺失或
錯位,而這份「契約」照樣全綠。現在每個案例都跑到真正的 state 邊界:
籌碼走 `_chip_fields_for_session`(產生實際存進歷史列的欄位名),
除權息/停牌走各自的 `update_*`(寫檔並用 loader 讀回)。

**為什麼是 fixture 而不是即時打 API**:CI 不該依賴外部服務的可用性
(那會讓 CI 的紅綠反映對方機房而不是我們的程式碼);而 schema 漂移的訊號
本來就該在**更新 fixture 時**被看見 —— 更新 fixture 是一個需要有人看過的動作。

fixture 內容全是公開市場資料,無個資;截樣本是為了測試可讀,不是為了讓它通過
(見 `taifex_large_traders.json` 刻意保留的誘餌列)。
"""
import datetime as dt
import json
from pathlib import Path

import pytest

import morning_report as mr

FIXTURES = Path(__file__).resolve().parent / "fixtures"
_NOW = dt.datetime(2026, 7, 30, 6, 45, tzinfo=mr.TPE)


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


def _serve(monkeypatch, fixture):
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: _Resp(_load(fixture)))


# --------------------------------------------------------------- 各來源案例
# 每個案例都必須跑到**真正的 state 邊界**,不是只看 fetcher 的回傳值。

def _case_put_call_ratio(monkeypatch, tmp_path, fixture):
    """`txo_pc_oi_ratio` 的來源 → 歷史列欄位。

    2026-07-30 實測欄位:`Date / PutVolume / CallVolume / PutCallVolumeRatio% /
    PutOI / CallOI / PutCallOIRatio%`,值全部是**字串**。
    """
    _serve(monkeypatch, fixture)
    pcr = mr.fetch_taifex_options_pc_ratio()
    assert pcr["date"] == "20260729"

    fields = mr._chip_fields_for_session(None, pcr, "2026-07-29")
    assert fields["txo_pc_oi_ratio"] == 84.29, "解析對了但沒進到歷史列欄位"
    assert fields["txo_pcr_source_date"] == "20260729"
    # 日期對不上該交易日時必須留空(錯位特徵比缺值更糟)
    off = mr._chip_fields_for_session(None, pcr, "2026-07-30")
    assert off["txo_pc_oi_ratio"] is None


def _case_large_traders(monkeypatch, tmp_path, fixture):
    """`taifex_top10_net` 等 → 歷史列欄位。

    真實回應是**全市場 1366 筆**;解析器要挑出 `Contract=TX` 且
    `SettlementMonth=999912`(所有契約合計),再依 `TypeOfTraders` 分辨
    全部(0)與特定法人(1)。fixture 刻意留一列單月份(202608)當**誘餌**:
    它的數字完全不同(Top10Buy 83225 / Top10Sell 79982 / OI 116651),
    合計過濾一壞就會拿到 +3243 與 116651 —— 別的東西的籌碼,而且看起來正常。
    """
    _serve(monkeypatch, fixture)
    large = mr.fetch_taifex_large_traders()
    assert large["date"] == "20260729"
    assert large["top10_net"] == 84346 - 84362, "採用了非 999912(合計)的列"
    assert large["oi_market"] == 124519
    assert large["spec_top10_net"] == 79351 - 84362, "特定法人(type=1)沒取到"

    fields = mr._chip_fields_for_session(large, None, "2026-07-29")
    assert fields["taifex_top10_net"] == -16
    assert fields["taifex_spec_top10_net"] == -5011
    assert isinstance(fields["taifex_top10_concentration_pct"], (int, float))
    assert fields["taifex_chip_source_date"] == "20260729"


def _case_exdiv_preview(monkeypatch, tmp_path, fixture):
    """除權息預告 → `state/exdiv_history.json`。

    民國日期 `1150805`;`CashDividend` 常是空字串(ETF 待公告實際配息)。
    """
    _serve(monkeypatch, fixture)
    monkeypatch.setattr(mr, "EXDIV_HISTORY_FILE", tmp_path / "exdiv_history.json")
    landed = mr.update_exdiv_history(mr.fetch_exdiv_preview("2026-07-30"), _NOW)
    assert landed["records"], "抓到了卻沒有落地"
    assert all(r["ex_date"].count("-") == 2 and r["code"]
               for r in landed["records"])
    assert landed["days"] == ["2026-07-30"]
    # 落地的檔案必須能被 loader 讀回來(批#82 r7 的形狀守衛在這裡把關)
    assert mr.load_exdiv_history()["records"] == landed["records"]


def _case_trading_halt(monkeypatch, tmp_path, fixture):
    """暫停交易 → `state/corporate_actions.json`。

    欄位是 `TradingHaltDate` / `TradingResumptionDate`,民國格式(1150723)。
    """
    _serve(monkeypatch, fixture)
    monkeypatch.setattr(mr, "CORPORATE_ACTION_FILE",
                        tmp_path / "corporate_actions.json")
    landed = mr.update_corporate_actions(
        mr.fetch_trading_halts("2026-07-30"), _NOW)
    assert landed["records"] and all(
        r["halt_date"].count("-") == 2 and r["code"] for r in landed["records"])
    assert all(r["first_seen"] == "2026-07-30" for r in landed["records"])
    assert mr.load_corporate_actions()["records"] == landed["records"]


def _case_delisted(monkeypatch, tmp_path, fixture):
    """終止上市 → Top5 結算用的 `{code: ISO 日期}` 對照。

    日期是**帶斜線**的民國 `115/06/23` —— 與上面兩張表不同格式,
    這正是「猜形狀」最容易錯的地方。這張表不落地(它是歷史表,每次重抓),
    所以它的 state 邊界就是這個對照本身,由 `update_top5_ledger` 直接消費。
    """
    _serve(monkeypatch, fixture)
    saved = list(mr._DEGRADED_STEPS)
    try:
        table = mr.fetch_delisted_codes()
        assert table and all(d.count("-") == 2 for d in table.values())
        assert all(c.isdigit() for c in table)
        assert "corpact:delisted_fetch_failed" not in mr._DEGRADED_STEPS, \
            "真實 payload 不該被判成改版"
    finally:
        mr._DEGRADED_STEPS[:] = saved


#: fixture → 案例。
#:
#: r1(Codex,P3):**用明確的表,不用文字搜尋。** 第一版靠「在自己的原始碼裡
#: grep `_load("x.json")`」判斷 fixture 有沒有被用到,那把「文字裡提到」當成
#: 「執行到」—— 測試被改名而不再被 pytest 收集、或呼叫留在死分支/註解裡,
#: 它照樣通過。這張表是被 parametrize 真正執行的。
#:
#: r2(Codex,P3):**表裡的檔名還必須真的決定餵什麼。** r1 把 `fixture` 參數
#: parametrize 了卻沒用到 —— 每個 case 自己寫死檔名,於是表仍只是裝飾:
#: 改了表,case 仍餵另一個檔,而 pytest 報告卻顯示那個 fixture 被執行了。
#: 現在 `fixture` 一路傳到 `_serve`,名字與實際被讀的檔綁在一起。
CASES = [
    ("taifex_put_call_ratio.json", _case_put_call_ratio),
    ("taifex_large_traders.json", _case_large_traders),
    ("twse_exdiv_preview.json", _case_exdiv_preview),
    ("twse_trading_halt.json", _case_trading_halt),
    ("twse_delisted.json", _case_delisted),
]

#: 放在 `tests/fixtures/` 但**不是上游原始 payload** 的檔,逐一明列。
#:
#: 這個目錄原本只放「真實 API 回應樣本」,下面那條契約因此可以要求
#: 「每個檔都要有案例」。Luna 特化實驗需要凍結 prompt 的輸入,那不是
#: 上游 payload、也沒有 state 邊界可以走 —— 它由
#: `tests/test_deepseek_legacy_golden.py` 負責。
#:
#: 刻意**明列**而不是把掃描範圍縮成某個子目錄:縮範圍會讓下一份真的原始
#: payload 只要放錯位置就無聲逃過契約,而明列會在 diff 裡被看見。
NON_PAYLOAD_FIXTURES = {
    "legacy_prompt_input.json",     # DeepSeek legacy prompt 的凍結輸入
}

_MANIFEST_KEYS = ("chips", "exdiv_preview", "corporate_actions")


@pytest.mark.parametrize("fixture,case", CASES, ids=[c[0] for c in CASES])
def test_real_payload_reaches_state(fixture, case, monkeypatch, tmp_path):
    """每份真實 payload 都要能走到它的 state 邊界。"""
    for key in _MANIFEST_KEYS:
        mr._RUN_MANIFEST.pop(key, None)
    try:
        case(monkeypatch, tmp_path, fixture)
    finally:
        for key in _MANIFEST_KEYS:
            mr._RUN_MANIFEST.pop(key, None)


def test_every_fixture_has_a_case_and_every_case_has_a_fixture():
    """**fixture 不得只是躺在那裡,案例也不得指向不存在的檔。**

    兩個方向都要驗:多出來的 fixture 代表有人存了樣本卻沒寫契約(看起來有覆蓋、
    實際沒有);多出來的案例代表 fixture 被刪掉而測試會在執行時才炸。
    """
    on_disk = {p.name for p in FIXTURES.glob("*.json")} - NON_PAYLOAD_FIXTURES
    in_table = {name for name, _ in CASES}
    assert on_disk, "fixtures 目錄是空的 —— 掃描器或路徑錯了"
    stale = sorted(NON_PAYLOAD_FIXTURES
                   - {p.name for p in FIXTURES.glob("*.json")})
    assert not stale, (
        f"NON_PAYLOAD_FIXTURES 列了不存在的檔:{stale} —— "
        "豁免清單漂移會讓下一個同名的真 payload 靜默逃過契約")
    assert on_disk == in_table, (
        f"沒有案例的 fixture:{sorted(on_disk - in_table)};"
        f"沒有 fixture 的案例:{sorted(in_table - on_disk)}")
