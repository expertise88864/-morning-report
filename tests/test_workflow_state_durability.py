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
    assert "state:corrupt:gooaye_radar" in rq.KNOWN_DEGRADED


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
                  / ".github" / "workflows" / "morning-report.yml",
                  encoding="utf-8").read()
    missing = [p for p in lc.VALID_PROVIDERS
               if f"{lc.PROVIDER_KEY_ENV[p]}:" not in wf]
    assert not missing, f"workflow 沒注入這些 provider 的金鑰:{missing}"
