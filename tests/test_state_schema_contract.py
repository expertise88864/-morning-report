# -*- coding: utf-8 -*-
"""**已落地 state 的 schema 契約。**

批#77(第七輪 P1-9 的可離線部分)。

第七輪建議建立 raw adapter contract tests(從 Google RSS / MOPS / TWSE /
DeepSeek 的**原始 payload** 跑到 state)。那需要真實回應樣本,而本機取不到 ——
本輪已經有兩次「猜欄位/猜回應形狀」的代價,不再猜第三次。

**能做而且有價值的是另一半**:對 repo 裡**真實落地**的 state 檔立 schema 契約。
它抓得到的正是 raw adapter 測試要抓的東西 —— 上游 schema 漂移最終一定會表現成
「state 檔的形狀變了」或「該有值的欄位變空」。差別只在時機:raw adapter 測試
在 CI 就擋下,這一層則是在資料落地後才發現。**晚一步,但比沒有好**,而且
它驗的是真實生產資料,不是我構造出來的 fixture。

刻意設計成:
  - 檔案不存在 → 跳過(全新 repo / 尚未產生該狀態不該讓 CI 紅)
  - 只驗**結構與不變式**,不驗數值(數值每天都會變)
  - 訊息要指出「哪一筆、哪個欄位」,而不是只說形狀不對
"""
import json
from pathlib import Path

import pytest

import morning_report as mr
import story_ledger as sl

STATE = Path("state")


def _load(name):
    path = STATE / name
    if not path.exists():
        pytest.skip(f"{name} 尚未產生(全新 repo 或該功能未跑過)")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:      # 壞檔本身就是要抓的東西,不能跳過
        pytest.fail(f"{name} 無法解析:{e}")


def _iso_like(value) -> bool:
    return bool(mr._parse_news_time_required(str(value or "")))


# ---------------------------------------------------------------- model_history
def test_model_history_rows_have_a_session_date_and_sane_prices():
    rows = _load("model_history.json")
    assert isinstance(rows, list) and rows
    for i, r in enumerate(rows):
        assert isinstance(r, dict), f"第 {i} 筆不是 dict"
        assert str(r.get("session_date") or "").count("-") == 2, \
            f"第 {i} 筆 session_date 異常:{r.get('session_date')!r}"
        close = r.get("taiex_close")
        if close is not None:
            assert isinstance(close, (int, float)) and close > 1000, \
                f"{r['session_date']} 的 taiex_close 不合理:{close}"
    dates = [r["session_date"] for r in rows]
    assert len(dates) == len(set(dates)), "同一 session_date 出現多次"


def test_model_history_structured_events_keep_their_contract():
    """事件欄位型別走樣時,prompt 的清洗層會把整欄剔除 —— 那是靜默失分。"""
    rows = _load("model_history.json")
    seen = 0
    for r in rows:
        for e in (r.get("structured_events") or []):
            seen += 1
            assert isinstance(e, dict)
            assert e.get("event_type"), f"{r['session_date']} 有事件缺 event_type"
            assert isinstance(e.get("direction"), int) \
                and not isinstance(e.get("direction"), bool), \
                f"{r['session_date']} 的 direction 型別錯:{e.get('direction')!r}"
            assert _iso_like(e.get("published")), \
                f"{r['session_date']} 的 published 無法解析:{e.get('published')!r}"
            corr = e.get("corroboration_count")
            if corr is not None:
                assert isinstance(corr, int) and corr >= 0
                srcs = e.get("sources") or []
                assert corr <= max(1, len(srcs)), \
                    f"交叉驗證數 {corr} 超過來源數 {len(srcs)}"
    assert seen, "歷史裡完全沒有結構化事件 —— 抽取管線可能整段失效"


# ---------------------------------------------------------------- story ledger
def test_story_ledger_rows_are_well_formed():
    rows = _load("story_ledger.json")
    assert isinstance(rows, list)
    keys = set()
    for i, s in enumerate(rows):
        assert isinstance(s, dict), f"第 {i} 筆不是 dict"
        key = str(s.get("key") or "")
        assert key, f"第 {i} 筆缺 key"
        assert key not in keys, f"重複的線索 key:{key}"
        keys.add(key)
        # 狀態集合**從程式碼取**,不手抄 —— 手抄就是漂移的來源
        # (自測抓到:我憑印象寫 "converging",實際是 "resolving")。
        assert s.get("state") in set(sl.STATE_WEIGHT), \
            f"{key} 的 state 異常:{s.get('state')!r}"
        assert isinstance(s.get("updates"), int) and s["updates"] >= 1
        for p in (s.get("timeline") or []):
            assert isinstance(p, dict) and p.get("d"), f"{key} 有壞的軌跡點"
            link = str(p.get("l") or "")
            assert not link or link.startswith(("http://", "https://")), \
                f"{key} 的軌跡點 link 不是 http(s):{link!r}"


def test_story_ledger_market_wrap_backlog_is_reported_not_enforced():
    """**刻意不當成失敗。**

    批#63/#71 的大盤總結清掃是在 `update_ledger` 執行時才套用到既有帳本的,
    所以 repo 裡的 state 會在「修正已上線、但下一次生產執行還沒跑」的窗口裡
    仍然帶著舊資料。在那個窗口把 CI 弄紅,是**沒有任何 commit 能修好的紅**
    —— 而那種紅會訓練人忽略 CI。

    清掃邏輯本身由單元測試涵蓋
    (`test_market_wrap_timeline_points_are_swept_from_existing_stories`);
    這裡只把待遷移的數量印出來,讓「還剩多少」看得見。
    """
    rows = _load("story_ledger.json")
    vocab = ("台積電", "聯電", "鴻海", "廣達", "聯發科", "美光")
    backlog = [s["key"] for s in rows
               if sl.is_market_wrap(str(s.get("headline") or ""), vocab)]
    print(f"[state-contract] 待清掃的大盤總結線索:{len(backlog)}/{len(rows)}")
    # 只保證不會**惡化到整個帳本都是**(那代表清掃邏輯反了)
    assert len(backlog) < len(rows) * 0.5, \
        f"超過半數線索是大盤總結({len(backlog)}/{len(rows)})—— 清掃可能反了"


# ---------------------------------------------------------------- forecast ledger
def test_forecast_ledger_rows_are_internally_consistent():
    rows = _load("forecast_ledger.json")
    assert isinstance(rows, list)
    for i, e in enumerate(rows):
        assert isinstance(e, dict), f"第 {i} 筆不是 dict"
        kind = e.get("type")
        if kind == "top5":
            assert e.get("status") in {"awaiting_entry", "entered", "void",
                                       "void_legacy"}, \
                f"top5 第 {i} 筆 status 異常:{e.get('status')!r}"
            for h, res in (e.get("res") or {}).items():
                assert isinstance(res, dict), f"top5 res[{h}] 不是 dict"
                if res.get("void"):
                    # 批#73 的 legacy reason 標記是**下一次執行**才套用到既有
                    # 帳本的,所以這裡不強制(理由同 backlog 那條測試:
                    # 在遷移窗口把 CI 弄紅,是沒有任何 commit 能修好的紅)。
                    # 有 reason 時必須是可判讀的字串。
                    assert isinstance(res.get("reason", ""), str)
                else:
                    assert isinstance(res.get("excess_pct"), (int, float))
        elif kind == "mz_shadow":
            for f in ("raw", "shadow"):
                assert isinstance(e.get(f), (int, float)), f"mz_shadow 缺 {f}"
            if e.get("resolved") and not e.get("void"):
                assert isinstance(e.get("actual"), (int, float))
        elif e.get("question"):
            assert 0.0 <= float(e.get("prob", 0.5)) <= 1.0
            assert e.get("forecast_version"), "機率題缺版本血統"


# ---------------------------------------------------------------- exdiv history
def test_exdiv_history_shape_and_coverage_metadata():
    data = _load("exdiv_history.json")
    assert isinstance(data, dict), "除權息史應是 {since, days, records}"
    assert isinstance(data.get("records"), list)
    assert isinstance(data.get("days"), list)
    for r in data["records"]:
        assert isinstance(r, dict) and r.get("code") and r.get("ex_date")
        assert str(r["ex_date"]).count("-") == 2, f"ex_date 格式異常:{r}"
    # `days` 有值卻沒有任何 record 是**危險組合**:覆蓋檢查會判定完整、
    # 紀錄卻是空的 → Top5 用原始價格照常結算(批#71 r1 的真實損毀情境)
    if data["days"]:
        assert data["records"], (
            "days 宣稱收集過但 records 是空的 —— 覆蓋檢查會誤判為完整,"
            "Top5 會用未調整價格結算")


# ---------------------------------------------------------------- run manifest
def test_run_manifest_carries_the_observability_fields():
    """批#68–#75 陸續加的診斷欄位,每一個都曾經因為漏列重建白名單而被丟掉。
    這條驗**真實落地的 manifest**確實帶著它們(至少 date 與階段耗時)。"""
    m = _load("run_manifest.json")
    assert isinstance(m, dict)
    assert str(m.get("date") or "").count("-") == 2
    assert isinstance(m.get("total_seconds"), (int, float))
    assert isinstance(m.get("degraded_steps"), list)
    for optional in ("data_checks", "llm_extractor", "capability_health",
                     "mz_shadow", "delivery"):
        if m.get(optional) is not None:
            assert isinstance(m[optional], dict), \
                f"manifest 的 {optional} 型別錯:{type(m[optional]).__name__}"
