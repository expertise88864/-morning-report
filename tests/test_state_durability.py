# -*- coding: utf-8 -*-
"""2026-08-22 repo-wide 外審 P1-3 / P2:**「讀不動」不等於「今天是第一天」**。

`state/forecast_ledger.json` 同時承載預測記分帳本、Top5 可執行帳本與 MZ
影子 OOS 樣本。兩個寫入端先前都是「讀失敗 → `ledger=[]` → 結尾無條件
`_atomic_write_text`」—— 一次暫時性損壞就把幾百列歷史換成今天這一列,
而下一班讀到的是合法 JSON,那個新基線從此看起來完全正常。
最安靜的是「合法 JSON、錯 root type」(`{}`):連「載入失敗」都不會印。
"""
import io
import json

import morning_report as mr
import pytest
import state_store as ss


# ---------------------------------------------------------------- 讀取政策

def test_missing_and_corrupt_are_different_answers(tmp_path):
    """四態要分開 —— 混在一起正是這條規則要消滅的形狀。"""
    p = tmp_path / "s.json"
    assert ss.load_json_state(p, expected=list) == ([], "missing")
    p.write_text('[{"a": 1}]', encoding="utf-8")
    assert ss.load_json_state(p, expected=list) == ([{"a": 1}], "ok")
    for bad, why in (("[", "JSON"), ("", "空的"), ("{}", "root")):
        p.write_text(bad, encoding="utf-8")
        with pytest.raises(ss.StateCorrupt) as ei:
            ss.load_json_state(p, expected=list)
        assert why in str(ei.value), (bad, str(ei.value))


def test_wrong_root_type_is_corrupt_not_empty(tmp_path):
    """合法 JSON 但 root 型別不對**不是空狀態**,是另一個檔案的形狀。
    先前這條路徑連 log 都沒有(`isinstance` 不成立就靜默當成空的)。"""
    p = tmp_path / "s.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(ss.StateCorrupt):
        ss.load_json_state(p, expected=list)
    # 反向:預期 dict 時 `{}` 是合法的空狀態
    assert ss.load_json_state(p, expected=dict) == ({}, "ok")


# ---------------------------------------------------------------- 不覆寫

_ROWS = [{"type": "top5", "target_session": "2026-08-01", "status": "resolved"},
         {"type": "forecast", "key": "2330_open_up", "target": "2026-08-01"}]


def _corrupt(monkeypatch, tmp_path, body: str):
    f = tmp_path / "forecast_ledger.json"
    f.write_text(body, encoding="utf-8")
    monkeypatch.setattr(mr, "FORECAST_LEDGER_FILE", f)
    before = f.read_bytes()
    return f, before


@pytest.mark.parametrize("body", ["[", "{}", ""])
def test_top5_writer_never_overwrites_a_corrupt_ledger(monkeypatch, tmp_path,
                                                       body):
    f, before = _corrupt(monkeypatch, tmp_path, body)
    import datetime as dt
    steps = len(mr._DEGRADED_STEPS)
    out = mr.update_top5_ledger([], [], dt.datetime(2026, 8, 22, 6, 0),
                                "2026-08-22")
    assert f.read_bytes() == before, "壞檔被覆寫 —— 歷史一次性清空"
    assert out.get("skipped") == "state_corrupt"
    assert any("state:corrupt:forecast_ledger" in s
               for s in mr._DEGRADED_STEPS[steps:]), "壞掉卻沒留痕"


def test_both_ledger_writers_use_the_corrupt_policy():
    """接線:兩個寫入端都要走同一個載入器並在壞檔時**提早返回**
    (沒接上等於不存在;先前正是結尾無條件覆寫)。"""
    src = io.open(mr.__file__, encoding="utf-8").read()
    n = src.count("_register_state_corrupt(\"forecast_ledger\"")
    assert n >= 3, f"只有 {n} 處走壞檔政策(預期 Top5/forecast/active_codes)"
    assert "json.loads(FORECAST_LEDGER_FILE.read_text" not in src, \
        "還有繞過載入器的直接讀取"
    # 舊的「載入失敗,重建」語意不得殘留
    assert "載入失敗,重建" not in src


def test_conformal_and_source_health_follow_the_same_policy():
    src = io.open(mr.__file__, encoding="utf-8").read()
    for label in ("conformal_intervals", "source_health_history"):
        assert f'_register_state_corrupt("{label}"' in src, label
    assert "json.loads(SOURCE_HEALTH_HISTORY_FILE.read_text" not in src
    assert "json.loads(CONFORMAL_STATE_FILE.read_text" not in src


def test_conformal_corrupt_state_does_not_reset_q(monkeypatch, tmp_path):
    """壞檔被當成 `{}` 時,q 會從既有值掉回預設 = 把區間校準重置。"""
    f = tmp_path / "conformal.json"
    f.write_text("{oops", encoding="utf-8")
    monkeypatch.setattr(mr, "CONFORMAL_STATE_FILE", f)
    before = f.read_bytes()
    steps = len(mr._DEGRADED_STEPS)
    out = mr.compute_conformal_adjustments({"2330_open": {"coverage_pct": 50}})
    assert out == {}, "壞檔還算出調整表"
    assert f.read_bytes() == before, "壞檔被覆寫"
    assert any("state:corrupt:conformal_intervals" in s
               for s in mr._DEGRADED_STEPS[steps:])


def test_source_health_corrupt_history_is_not_truncated(monkeypatch, tmp_path):
    """這份歷史的用途正是「今天不是偶發,是連續壞很多天」——
    重置它會讓 watchdog 失憶,而失憶方向恰好是**低估**問題。"""
    f = tmp_path / "sh.json"
    f.write_text('[{"date": "2026-08-01"}', encoding="utf-8")   # 截斷
    monkeypatch.setattr(mr, "SOURCE_HEALTH_HISTORY_FILE", f)
    before = f.read_bytes()
    steps = len(mr._DEGRADED_STEPS)
    out = mr.update_source_health_history({"checks": {"a": True}}, "2026-08-22")
    assert out == [], out
    assert f.read_bytes() == before, "30 天歷史被截成只剩今天"
    assert any("state:corrupt:source_health_history" in s
               for s in mr._DEGRADED_STEPS[steps:])


def test_healthy_state_still_updates(monkeypatch, tmp_path):
    """防護不得把正常路徑一起關掉(單向守衛的反面)。"""
    f = tmp_path / "sh.json"
    f.write_text(json.dumps([{"date": "2026-08-01", "checks": {}}]),
                 encoding="utf-8")
    monkeypatch.setattr(mr, "SOURCE_HEALTH_HISTORY_FILE", f)
    mr.update_source_health_history({"checks": {"a": True}}, "2026-08-22")
    rows = json.loads(f.read_text(encoding="utf-8"))
    assert [r["date"] for r in rows] == ["2026-08-01", "2026-08-22"]


# ---------------------------------------------- 外審 r1(deep):三條 CONFIRMED

def test_non_utf8_state_is_corrupt_not_a_crash(tmp_path):
    """r1 P1:`read_text` 的 UnicodeDecodeError 不是 OSError,先前直接逸出
    —— 呼叫端只接 StateCorrupt,於是「非 UTF-8 的壞檔」不是降級而是
    **主流程中止、晨報寄不出去**。壞檔的處置只有一種,不因編碼而異。"""
    p = tmp_path / "s.json"
    p.write_bytes(b"\xff\xfe[{\"a\": 1}]")
    with pytest.raises(ss.StateCorrupt) as ei:
        ss.load_json_state(p, expected=list)
    assert "UTF-8" in str(ei.value)


def test_non_utf8_conformal_state_degrades_instead_of_aborting(monkeypatch,
                                                               tmp_path):
    """功能面:主流程那一支不得因此拋出。"""
    f = tmp_path / "conformal.json"
    f.write_bytes(b"\xff\xfe{}")
    monkeypatch.setattr(mr, "CONFORMAL_STATE_FILE", f)
    before = f.read_bytes()
    assert mr.compute_conformal_adjustments({"2330_open": {"coverage_pct": 50}}) == {}
    assert f.read_bytes() == before


def test_corrupt_detail_survives_into_the_landed_manifest(monkeypatch,
                                                          tmp_path):
    """r1 P2:明細先前直接寫進 `_RUN_MANIFEST["state_writes"]`,而
    `record_state_writes` 是**整段覆寫**那一格 —— 落地前就沒了,
    程式裡「manifest 留痕」的宣稱因此是假的。"""
    f = tmp_path / "sh.json"
    f.write_text("{oops", encoding="utf-8")
    monkeypatch.setattr(mr, "SOURCE_HEALTH_HISTORY_FILE", f)
    mr.update_source_health_history({"checks": {"a": True}}, "2026-08-22")
    mr._RECORDER.record_state_writes({"x": {"ok": True}})
    got = mr._RECORDER.data.get("state_writes", {}).get("corrupt") or []
    assert any(r.get("file") == "source_health_history" for r in got), got


def test_subjects_meet_ignores_bare_numeric_company_aliases():
    """r1 P2:`aliases_of` 自本批起回公司別名組(含股票代號)——
    「成交量 3231 張」會讓不相關新聞被當成緯創舊事件的續報
    (延燒天數與全文抓取優先權跟著錯)。判準與 producer 同一份。"""
    import event_identity as ei
    import subject_identity as si
    assert not si.usable_alias("3231") and not si.usable_alias("Q2")
    assert si.usable_alias("緯創") and si.usable_alias("NVIDIA")
    # 簽名:(今日實體集, 今日別名組, 記錄的主體集, 今日標題)
    assert not ei._subjects_meet(set(), set(), {"緯創"}, "成交量 3231 張創天量")
    assert ei._subjects_meet(set(), set(), {"緯創"}, "緯創法說會展望樂觀")


def test_history_state_wrong_root_type_is_not_overwritten(monkeypatch, tmp_path):
    """外審 P2:`{}` 是合法 JSON 但 root 型別不對 —— 先前靜默變成
    `existing = []`,整份歷史(預測校準與歷史記憶)被今天這一筆蓋掉。
    與 forecast_ledger 同一形狀,只是更安靜(連 log 都沒有)。"""
    f = tmp_path / "history.json"
    f.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mr, "STATE_FILE", f)
    before = f.read_bytes()
    steps = len(mr._DEGRADED_STEPS)
    assert mr.save_history_state({"date": "2026-08-22"}, push=False) is False
    assert f.read_bytes() == before, "整份歷史被覆寫"
    assert any("state:corrupt:history" in s for s in mr._DEGRADED_STEPS[steps:])
    # 讀端降級但不中止(晨報不可斷)
    assert mr.load_history_state() == []


def test_history_state_still_saves_when_healthy(monkeypatch, tmp_path):
    """防護不得把正常路徑一起關掉。"""
    f = tmp_path / "history.json"
    f.write_text(json.dumps([{"date": "2026-08-01"}]), encoding="utf-8")
    monkeypatch.setattr(mr, "STATE_FILE", f)
    assert mr.save_history_state({"date": "2026-08-22"}, push=False) is True
    rows = json.loads(f.read_text(encoding="utf-8"))
    assert sorted(r["date"] for r in rows) == ["2026-08-01", "2026-08-22"]
