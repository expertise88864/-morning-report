# -*- coding: utf-8 -*-
"""2026-08-22 外審 P2-4/P2-5:**同一個 corrupt→empty→overwrite 還活在
獨立 workflow 裡**。

晨報那批把 forecast_ledger / conformal / source_health / history 都接上
`state_store` 了,但 `podcast_digest.py` 與 `gooaye_radar.py` 各自保留舊政策:
「讀不出來就視為空」,而之後會無條件覆寫同一個檔。

Radar 更嚴重,因為它的副作用是**寄信**:
壞檔 → 當空 → 已寄過的最新一集看起來沒寄過 → 重轉錄、重分析、
**重複寄一封** → 寄成功之後才把空 state 寫回去 → 舊 GUID 永久消失。
「今天少寄一封」的代價遠低於「對同一集重複寄信」。
"""
import json

import gooaye_radar as gr
import podcast_digest as pdg
import pytest
import state_store as ss


@pytest.mark.parametrize("body", ["{", "[]", ""])
def test_podcast_state_corrupt_stops_the_run(monkeypatch, tmp_path, body):
    """壞檔 → 不轉錄、不寄、不寫,非零離開(獨立 workflow 沒有不可斷的理由)。"""
    f = tmp_path / "podcast_digest.json"
    f.write_text(body, encoding="utf-8")
    monkeypatch.setattr(pdg, "STATE_FILE", f)
    monkeypatch.setattr(pdg, "DEEPSEEK_API_KEY", "x")
    before = f.read_bytes()
    called = []
    monkeypatch.setattr(pdg, "find_new_episodes",
                        lambda *a, **k: called.append(1) or [])
    monkeypatch.setattr(pdg, "save_state",
                        lambda *a, **k: called.append("save"))
    assert pdg.main() != 0
    assert f.read_bytes() == before, "壞檔被覆寫"
    assert not called, f"壞檔還往下跑:{called}"


def test_podcast_healthy_state_still_runs(monkeypatch, tmp_path):
    """防護不得把正常路徑一起關掉。"""
    f = tmp_path / "podcast_digest.json"
    f.write_text(json.dumps({"股癌": {"episodes": []}}), encoding="utf-8")
    monkeypatch.setattr(pdg, "STATE_FILE", f)
    monkeypatch.setattr(pdg, "DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr(pdg, "find_new_episodes", lambda *a, **k: [])
    assert pdg.main() == 0


@pytest.mark.parametrize("body", ["{", "[]", ""])
def test_radar_corrupt_state_never_sends(monkeypatch, tmp_path, body):
    """**壞檔不得寄送** —— 重複寄信是使用者看得到的傷害,而且蓋不掉。"""
    f = tmp_path / "gooaye_radar.json"
    f.write_text(body, encoding="utf-8")
    monkeypatch.setattr(gr, "RADAR_STATE_FILE", f)
    before = f.read_bytes()
    sent = []
    monkeypatch.setattr(gr, "_deliver", lambda *a, **k: sent.append(1) or True,
                        raising=False)
    monkeypatch.setattr(pdg, "find_new_episodes",
                        lambda *a, **k: sent.append("found") or [])
    assert gr.process_new_episode() != 0
    assert not sent, f"壞檔還走到寄送/尋集:{sent}"
    assert f.read_bytes() == before, "壞檔被覆寫"


def test_radar_missing_state_is_a_normal_first_run(monkeypatch, tmp_path):
    """`missing` 與 `corrupt` 不是同一件事:第一次跑要能跑。"""
    f = tmp_path / "gooaye_radar.json"
    monkeypatch.setattr(gr, "RADAR_STATE_FILE", f)
    assert gr.load_radar_state() == {}
    monkeypatch.setattr(pdg, "find_new_episodes", lambda *a, **k: [])
    assert gr.process_new_episode() == 0


def test_morning_report_degrades_but_never_dies_on_radar_corruption(
        monkeypatch, tmp_path):
    """晨報不可斷 —— 這裡降級成「不去重」(最壞重複一次),但要留痕:
    否則「今天沒去重」與「雷達今天沒寄東西」長得一樣。"""
    import morning_report as mr
    import run_quality as rq
    f = tmp_path / "gooaye_radar.json"
    f.write_text("{oops", encoding="utf-8")
    monkeypatch.setattr(mr, "GOOAYE_RADAR_FILE", f)
    before = len(mr._DEGRADED_STEPS)
    assert mr._radar_processed_guids() == set()
    assert any("state:corrupt:gooaye_radar" in s
               for s in mr._DEGRADED_STEPS[before:])
    # 2026-08-22 外審 P3:個別註冊被**家族**取代 —— `state:corrupt:*` 有自己
    # 的 finding(說得出哪份壞了),所以不再從 unknown_degradation 重報。
    got = rq.assess({"degraded_steps": ["state:corrupt:gooaye_radar"],
                     "llm": {"analysis_origin": "luna_specialized"}})
    codes = {x["code"] for x in got}
    assert "unknown_degradation" not in codes and         "persistent_state_corrupt" in codes, codes


def test_no_workflow_keeps_its_own_corrupt_policy():
    """repo-level 契約:持久 state 的讀取端不得自己 `json.loads` + 回空。"""
    import io as _io
    from pathlib import Path
    # 比對**程式碼形狀**而不是字面(註解會引用舊政策的說法)。
    for mod, const in ((pdg, "STATE_FILE"), (gr, "RADAR_STATE_FILE")):
        src = _io.open(Path(mod.__file__), encoding="utf-8").read()
        assert f"json.loads({const}.read_text" not in src,             f"{mod.__name__} 還自己 json.loads 持久 state"
        assert "_ss.load_json_state" in src, f"{mod.__name__} 沒接 state_store"


def test_state_store_contract_is_shared():
    """四態的判準只有一份。"""
    assert issubclass(ss.StateCorrupt, RuntimeError)
    assert ss.load_json_state.__module__ == "state_store"


# ---------------------------------------------- 外審 P2-2 / P2-6

def test_alias_truth_is_a_union_not_first_table_wins():
    """P2-2:同一個主體的別名散在三張表(`_ORG_ALIASES` 有 Federal Reserve/
    聯儲、`entity_alias` 有美聯儲)。先前「第一張表命中就回」,漏掉的那些在
    producer 端等於不存在 —— **別名的來源可以有多個,identity 只有一個**。"""
    import subject_identity as si
    fed = set(si.aliases_of("Fed"))
    for a in ("聯準會", "Fed", "FOMC", "美聯儲", "Federal Reserve", "聯儲"):
        assert a in fed, f"{a} 不在聯集裡:{sorted(fed)}"
    # 公司/法域仍照舊(聯集不得把別的主體併進來)
    assert set(si.aliases_of("NVDA")) == {"輝達", "NVIDIA", "Nvidia", "NVDA"}
    assert "台積電" not in fed and "俄羅斯" not in fed


def test_every_declared_provider_has_its_key_in_production():
    """P2-6:`VALID_PROVIDERS` 宣告 openai 可用,而生產 workflow 沒注入它的
    金鑰 —— 一個被宣告為合法的設定實際上啟動不了。宣告與注入要一致。"""
    import io as _io
    from pathlib import Path
    import llm_config as lc
    wf = _io.open(Path(__file__).resolve().parents[1]
                  / ".github" / "workflows" / "morning-report-a.yml",
                  encoding="utf-8").read()
    missing = [p for p in lc.VALID_PROVIDERS
               if f"{lc.PROVIDER_KEY_ENV[p]}:" not in wf]
    assert not missing, f"workflow 沒注入這些 provider 的金鑰:{missing}"


# ---------------------------------------------- 外審 P2-3:action → event_type

def test_same_action_gets_one_event_type_from_both_routes():
    """P2-3:同一則「美國宣布制裁伊朗」先前確定性路徑回 export_controls、
    抽取器給 geopolitical 時不會被修 —— story key / timeline key / lifecycle /
    event-study 桶全部分裂。生產 state 兩種都有。"""
    import event_actions as ea
    import news_events as ne
    t = "美國宣布制裁伊朗央行"
    assert ea.event_action(t, "") == "sanction"
    assert ne._event_type(t) == ne.normalize_event_type("geopolitical", t)
    assert ne._event_type(t) == ea.ACTION_EVENT_TYPE["sanction"] == "geopolitical"
    # 出口管制是**另一個動作**,不得被併進來
    e = "美國對中國實施晶片出口管制"
    assert ea.event_action(e, "") == "export_control"
    assert ne._event_type(e) == "export_controls"


def test_the_table_does_not_override_the_model_outside_its_family():
    """既有決策(不拿確定性推導覆寫模型的所有判斷)要保住:模型說 earnings
    而標題提到駭客攻擊時,那則新聞真的是在講財報。"""
    import news_events as ne
    assert ne.normalize_event_type("earnings", "駭客攻擊") == "earnings"
    assert ne.normalize_event_type("geopolitical", "駭客攻擊") == "cybersecurity"


def test_old_action_event_type_keys_are_migrated():
    """判準改了要配遷移 —— 否則既有 `export_controls:sanction:*` 今天算不
    出來、上線第一天孤立(公司鍵那次的教訓)。"""
    import state_migrations as sm
    tl = {"export_controls:sanction:伊朗:2026-08": {"entity": "伊朗", "days": 4},
          "geopolitical:sanction:美國:2026-08": {"entity": "美國", "days": 2},
          "export_controls:export_control:中國:2026-08": {"entity": "中國",
                                                        "days": 1}}
    out, renamed = sm.migrate_action_event_types(tl)
    assert renamed == ["export_controls:sanction:伊朗:2026-08"], renamed
    assert "geopolitical:sanction:伊朗:2026-08" in out
    assert out["geopolitical:sanction:伊朗:2026-08"]["days"] == 4
    # 已經對的不動;export_control 那條本來就對
    assert "geopolitical:sanction:美國:2026-08" in out
    assert "export_controls:export_control:中國:2026-08" in out
    # 可重入
    out2, ren2 = sm.migrate_action_event_types(out)
    assert ren2 == [] and set(out2) == set(out)


def test_migration_uses_the_same_family_gate_as_the_producer():
    """自測抓到:producer 只在 event_type 屬於粗粒度家族時才對齊,而第一版
    遷移對任何 action 都改 —— `litigation:cyberattack:*` 會被改成 producer
    明天算不出來的鍵(與公司鍵那次的軍售完全同型)。"""
    import news_events as ne
    import state_migrations as sm
    tl = {"litigation:cyberattack:藥華藥:2026-08": {"entity": "藥華藥", "days": 3}}
    out, renamed = sm.migrate_action_event_types(tl)
    assert renamed == [] and "litigation:cyberattack:藥華藥:2026-08" in out
    # producer 對同一組輸入也不改
    assert ne.normalize_event_type("litigation", "藥華藥遭駭客攻擊") == "litigation"


# ------------------------------------------- 外審 r1(deep):五條 CONFIRMED

def test_sanction_inflections_converge_on_both_routes():
    """r1 P2:`_event_type` 的詞彙表認得 sanctioned/sanctioning,而動作詞彙
    只有 sanction —— 那些字形的標題兩條入口仍然分裂。"""
    import event_actions as ea
    import news_events as ne
    for t in ("US sanctioned Iranian banks", "US sanctioning Russian oil",
              "US sanctions Iran", "美國制裁伊朗"):
        assert ea.event_action(t, "") == "sanction", t
        assert ne._event_type(t) == ne.normalize_event_type("geopolitical", t) \
            == "geopolitical", t


def test_history_event_types_are_normalised_before_keying():
    """r1 P2:部署前的制裁事件存的是 export_controls,今天算出 geopolitical
    —— 主鍵與橋接鍵都用存下來的型別的話,前態查不到,confirmed 重新拿到
    完整權重。"""
    import news_events as ne
    hist = [{"session_date": "2026-08-21", "structured_events": [{
        "entity": "伊朗", "event_type": "export_controls",
        "title": "美國宣布制裁伊朗央行", "lifecycle": "rumor",
        "event_schema": ne.EVENT_SCHEMA_VERSION}]}]
    today = [{"entity": "伊朗", "event_type": "geopolitical",
              "title": "美國證實制裁伊朗央行", "lifecycle": "confirmed"}]
    out = ne.apply_event_timeline(hist, today)
    assert out[0]["previous_lifecycle"] == "rumor", out[0]
    assert out[0]["lifecycle_weight"] == 0.65, out[0]


def test_event_schema_version_bumped_for_the_type_contract():
    """r1 P2:型別進 `_event_timeline_key` → `_event_instance_id`;不跳版的話
    新舊 ID 都自稱當代,event-study 把同一樁制裁算成兩個獨立可信事件。"""
    import news_events as ne
    assert ne.EVENT_SCHEMA_VERSION == 6


def test_story_lineage_segment_is_migrated_too():
    """r1 P2:story key 的 lineage 段直接來自 `_event_timeline_key` ——
    不遷移的話,續報今天算 geopolitical、帳本裡是 export_controls,
    標題改寫幅度大時就另開一條、原線索孤立。"""
    import state_migrations as sm
    # **列要帶 event_type**(生產的形狀):只驗鍵的 fixture 量不到
    # 「鍵改了、列沒改」——那正是 r2 外審抓到的缺陷。
    rows = [{"key": "e:伊朗|l:export_controls|2026-08", "entity": "伊朗",
             "event_type": "export_controls",
             "headline": "美國宣布制裁伊朗央行"},
            {"key": "e:中國|l:export_controls|2026-08", "entity": "中國",
             "event_type": "export_controls",
             "headline": "美國對中國實施晶片出口管制"}]
    out, renamed = sm.migrate_story_action_event_types(rows)
    assert renamed == ["e:伊朗|l:export_controls|2026-08"], renamed
    assert out[0]["key"] == "e:伊朗|l:geopolitical|2026-08"
    assert out[0]["event_type"] == "geopolitical", "鍵改了列沒改(追蹤查詢會找錯)"
    # 出口管制那條本來就對,不動
    assert out[1]["key"] == "e:中國|l:export_controls|2026-08"
    assert out[1]["event_type"] == "export_controls"
    # 可重入 + 不變異輸入
    out2, ren2 = sm.migrate_story_action_event_types(out)
    assert ren2 == []
    assert rows[0]["key"] == "e:伊朗|l:export_controls|2026-08", "就地改了輸入"


def test_migrated_row_type_matches_its_key():
    """r1 P3:只改鍵的話,鍵說 geopolitical、列裡的 event_type 還是
    export_controls —— 與 ICC 那次的 stale object 同型。"""
    import state_migrations as sm
    tl = {"export_controls:sanction:伊朗:2026-08":
          {"entity": "伊朗", "days": 4, "event_type": "export_controls"}}
    out, renamed = sm.migrate_action_event_types(tl)
    row = out["geopolitical:sanction:伊朗:2026-08"]
    assert row["event_type"] == "geopolitical", row
    assert renamed and row["days"] == 4
