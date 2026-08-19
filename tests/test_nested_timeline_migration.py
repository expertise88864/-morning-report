# -*- coding: utf-8 -*-
"""repo-wide 外審 2026-08-19 P1-A:正確 story 裡的錯 nested timeline point。

story 列與 event_timeline 列的清理(2026-08-18 Commit A)漏了第三層:
story 的 `timeline[]` 是會重新餵給模型的軌跡,生產實證 2330 earnings
story 裡還活著「迅得上半年EPS3.79元」。
"""
import state_migrations as sm
import story_ledger as sl

_KN = {"2330": ("台積電", "TSMC"), "2317": ("鴻海", "Foxconn")}


def _story(points):
    return {"key": "e:2330|l:earnings|2026q3", "entity": "2330",
            "headline": "台積電上半年財報出爐", "timeline": list(points)}


def test_subject_migration_removes_bad_nested_timeline_point():
    """外審指定的生產反例:2330 story 軌跡裡的迅得點必須消失,
    台積電自己的點必須留著。"""
    rows = [_story([
        {"d": "2026-08-14", "t": "台積電 Q2 財報亮眼", "l": "", "s": "x", "f": []},
        {"d": "2026-08-15", "t": "迅得上半年EPS3.79元並將配息3元", "l": "", "s": "x", "f": []},
        {"d": "2026-08-16", "t": "台積電 Arizona 廠獲利 311 億", "l": "", "s": "x", "f": []},
    ])]
    out, dropped = sm.purge_misattributed_timeline_points(rows, _KN)
    titles = [p["t"] for p in out[0]["timeline"]]
    assert titles == ["台積電 Q2 財報亮眼", "台積電 Arizona 廠獲利 311 億"], titles
    assert len(dropped) == 1 and "迅得" in dropped[0][1], dropped


def test_timeline_point_preserves_summary_only_subject_basis():
    """帶 `b` 的 point = producer 當時用完整文字(可能是摘要)證實過 ——
    標題重驗會誤殺它,必須豁免。標題「第二季獲利優於預期」完全沒有
    台積電字樣,靠的是摘要;清理不得動它。"""
    rows = [_story([
        {"d": "2026-08-15", "t": "第二季獲利優於預期", "l": "", "s": "x",
         "f": [], "b": "alias"},
    ])]
    out, dropped = sm.purge_misattributed_timeline_points(rows, _KN)
    assert out[0]["timeline"], "靠摘要證實的合法 point 被誤殺"
    assert not dropped


def test_unknown_entity_story_is_left_alone():
    """詞彙表查不到 story 主體 → 證明不了 ≠ 錯,整列不動(_named 同規)。"""
    rows = [{"key": "e:Pentagon|l:geopolitical", "entity": "Pentagon",
             "timeline": [{"d": "2026-08-15", "t": "黃金大漲", "l": "",
                           "s": "x", "f": []}]}]
    out, dropped = sm.purge_misattributed_timeline_points(rows, _KN)
    assert out[0]["timeline"] and not dropped


def test_timeline_entry_records_the_subject_basis():
    """落盤側:事件帶 subject_basis 時 point 要存 `b`;沒有就不存
    (空 b 的 point 之後仍受標題保守重驗)。"""
    ev = {"title": "第二季獲利優於預期", "link": "", "source": "測試",
          "subject_basis": "alias"}
    p = sl._timeline_entry(ev, "2026-08-19", [])
    assert p.get("b") == "alias", p
    p2 = sl._timeline_entry({"title": "x", "link": "", "source": "y"},
                            "2026-08-19", [])
    assert "b" not in p2, p2


def test_story_prompt_never_replays_other_company_timeline_point(monkeypatch):
    """接線:走生產的 `purge_story_misattribution` 載入路徑,清完之後
    軌跡取樣(_arc_steps)不得再吐出別家公司的點。"""
    import morning_report as mr
    monkeypatch.setattr(mr, "_run_alias_map", lambda tw0050=None: _KN)
    bad = _story([
        {"d": "2026-08-14", "t": "台積電 Q2 財報亮眼", "l": "", "s": "x", "f": []},
        {"d": "2026-08-15", "t": "迅得上半年EPS3.79元並將配息3元", "l": "",
         "s": "x", "f": []},
    ])
    out = mr.purge_story_misattribution([bad])
    joined = " ".join(p["t"] for r in out for p in (r.get("timeline") or []))
    assert "迅得" not in joined, joined
    assert "台積電" in joined
