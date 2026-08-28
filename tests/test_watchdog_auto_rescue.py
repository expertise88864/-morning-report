# -*- coding: utf-8 -*-
"""2026-08-28 使用者定案:看門狗偵測到今天沒寄成功時**自動補寄**。

這是給自動化「主動寄信給收件人」的能力,所以測試的重點不是「它會補」,
而是**什麼情況下它不可以補**。多寄一封收不回來。
"""
import datetime as dt
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import report_watchdog as w                                   # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_NOW = dt.datetime(2026, 8, 28, 8, 5, tzinfo=w.TPE)


def test_it_only_rescues_when_today_really_has_no_letter():
    assert w.rescue_decision(1, 0)[0] is True                 # 沒跑起來
    assert w.rescue_decision(2, 0)[0] is False, "寄到了只是品質差 —— 再寄是打擾"
    assert w.rescue_decision(0, 0)[0] is False


def test_at_most_one_rescue_a_day():
    """一天最多一次。已經補過卻仍然沒信,那就不是排程漏跑 —— 再補一次
    也不會好,留給人。使用者自己手動補過的那天同樣不再自動補。"""
    assert w.rescue_decision(1, 1)[0] is False
    assert w.rescue_decision(1, 5)[0] is False


def test_not_knowing_means_not_sending():
    """**查不出來就不寄。** 第一版判準寫 `> 0`,於是「查不到」的 -1 一路
    放行 —— 而 docstring 說的是「查不出來就不補」。宣稱與實作差的那一層,
    剛好是這個能力最危險的地方。"""
    assert w.rescue_decision(1, -1)[0] is False
    assert "不知道" in w.rescue_decision(1, -1)[1]


def test_the_kill_switch_works():
    assert w.rescue_decision(1, 0, enabled=False)[0] is False


def test_counting_todays_dispatches_uses_taipei_days():
    """`created_at` 是 UTC,而「今天」是台北日曆日 —— 用錯尺度會讓
    台北清晨那幾個小時的補寄被算成昨天,於是同一天補兩次。"""
    def fake(url):
        assert "event=workflow_dispatch" in url, url
        return {"workflow_runs": [
            {"created_at": "2026-08-27T23:30:00Z"},   # 台北 08/28 07:30 → 今天
            {"created_at": "2026-08-27T10:00:00Z"},   # 台北 08/27 18:00 → 昨天
        ]}
    assert w.dispatch_runs_today(_NOW, fake) == 1


def test_a_broken_listing_reports_unknown_not_zero():
    """讀不到要回 -1(不知道),**不是 0**(確定沒補過)—— 那兩個在
    判準裡的處置完全相反。"""
    def boom(url):
        raise RuntimeError("network down")
    assert w.dispatch_runs_today(_NOW, boom) == -1


def test_rescue_never_raises_and_never_posts_when_it_should_not():
    """看門狗的本業是告警:補寄失敗不得把告警一起弄死。"""
    posted = []
    ok, why = w.rescue(_NOW, 2, get_json=lambda u: {"workflow_runs": []},
                       post=lambda u, b: posted.append(u))
    assert ok is False and not posted, why
    ok, why = w.rescue(_NOW, 1,
                       get_json=lambda u: (_ for _ in ()).throw(OSError("x")),
                       post=lambda u, b: posted.append(u))
    assert ok is False and not posted, "查不出來卻還是送出了觸發"
    # 真的要補的時候才 POST,而且打的是晨報那支 workflow
    ok, why = w.rescue(_NOW, 1, get_json=lambda u: {"workflow_runs": []},
                       post=lambda u, b: posted.append((u, b)))
    assert ok is True and len(posted) == 1, (ok, why, posted)
    url, body = posted[0]
    assert url.endswith("/actions/workflows/morning-report.yml/dispatches")
    assert body == {"ref": "main"}


def test_the_rescue_is_wired_into_the_workflow():
    """沒有接線的話,上面那些判準只是一段永遠不執行的宣稱。"""
    import yaml
    wf = yaml.safe_load(io.open(
        _ROOT / ".github" / "workflows" / "report-watchdog.yml",
        encoding="utf-8").read())
    assert wf["permissions"].get("actions") == "write", wf["permissions"]
    steps = wf["jobs"]["check"]["steps"]
    names = [s.get("name") for s in steps]
    rescue = steps[names.index("Auto rescue")]
    assert rescue["if"] == "steps.check.outputs.rc == '1'", rescue["if"]
    assert "--rescue" in rescue["run"]
    # 補寄**不取代告警**:告警仍在,而且排在補寄之後(才帶得到結果)
    assert names.index("Auto rescue") < names.index("Alert")
    alert = steps[names.index("Alert")]
    assert alert["if"] == "steps.check.outputs.rc != '0'"
    assert "WATCHDOG_RESCUE" in (alert.get("env") or {})


def test_the_real_cli_entry_point_actually_runs(tmp_path):
    """**r1 外審 P1:8 條測試全綠,而生產真正用的入口是壞的。**

    上面每一條都是 `import` 模組(定義全跑完)再直接呼叫函式;
    workflow 跑的卻是 `python tools/report_watchdog.py --rescue`,而
    `rescue()` 當時被 append 在 `__main__` 之後 —— 執行到那一行時它還
    沒定義,`NameError` 當場炸掉。接線測試也只比對 workflow 的字串。
    這一條走**真正的子行程入口**,是唯一抓得到那個形狀的測試。

    用 kill switch 關掉 → 判準在碰網路之前就回答完(測試不碰網路)。
    """
    import os
    import subprocess
    out_file = tmp_path / "gh_output"
    out_file.write_text("", encoding="utf-8")
    env = dict(os.environ, WATCHDOG_AUTO_RESCUE="0",
               GITHUB_OUTPUT=str(out_file), PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        [sys.executable, "tools/report_watchdog.py", "--rescue"],
        cwd=str(_ROOT), env=env, capture_output=True,
        encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    assert "自動補寄" in r.stdout, r.stdout
    # 結果要真的寫進 GITHUB_OUTPUT(告警信靠它帶上補寄結果)
    note = out_file.read_text(encoding="utf-8")
    assert note.startswith("note=") and "未補寄" in note, note


def test_the_kill_switch_answers_before_touching_the_network(monkeypatch):
    """關掉的時候不該還去打 API。**要明確關掉才量得到** —— 預設是開著的,
    照預設跑的話那個分支根本沒被執行(第一版就是這樣寫的)。"""
    calls = []
    monkeypatch.setattr(w, "AUTO_RESCUE", False)
    ok, why = w.rescue(_NOW, 1,
                       get_json=lambda u: calls.append(u) or {},
                       post=lambda u, b: calls.append(u))
    assert ok is False and not calls, (why, calls)
    # rc 不對(信已經寄到了)同樣不必碰網路
    monkeypatch.setattr(w, "AUTO_RESCUE", True)
    ok, why = w.rescue(_NOW, 2,
                       get_json=lambda u: calls.append(u) or {},
                       post=lambda u, b: calls.append(u))
    assert ok is False and not calls, (why, calls)
