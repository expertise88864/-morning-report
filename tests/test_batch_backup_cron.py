# -*- coding: utf-8 -*-
"""2026-08-27 使用者:排程漏跑要有備援,但不能重複寄信。

當天實況:`0 22 * * *` 那一輪 GitHub **完全沒有建立**(不是失敗,是不存在),
而看門狗自己也是排程、同樣沒跑 —— 本來要通知「今天沒跑」的機制,和晨報死在
同一個原因上。備援 cron 是修法,同日冪等是它的前提。
"""
import datetime as dt
import json

import morning_report as mr


def _stamp(tmp_path, monkeypatch, date_str, delivery):
    f = tmp_path / "run_manifest.json"
    f.write_text(json.dumps({"date": date_str, "delivery": delivery},
                            ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", f)
    return f


def test_a_scheduled_backup_does_not_send_twice(tmp_path, monkeypatch):
    now = dt.datetime(2026, 8, 27, 6, 40, tzinfo=mr.TPE)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    _stamp(tmp_path, monkeypatch, "2026-08-27 06:13",
           {"attempted": True, "success": True, "run_kind": "schedule"})
    assert "已寄出" in mr.already_delivered_today(now)
    # 刻意不寄(週日無新內容)也算有結論
    _stamp(tmp_path, monkeypatch, "2026-08-27 06:13",
           {"attempted": False, "success": False,
            "skipped_reason": "weekend_no_new_content"})
    assert "已判定不寄" in mr.already_delivered_today(now)


def test_ambiguity_means_run_not_skip(tmp_path, monkeypatch):
    """**模稜兩可時要補寄,不是不寄** —— 那正是備援存在的理由。"""
    now = dt.datetime(2026, 8, 27, 6, 40, tzinfo=mr.TPE)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    # 昨天的紀錄(今天根本沒跑)
    _stamp(tmp_path, monkeypatch, "2026-08-26 06:13",
           {"attempted": True, "success": True})
    assert mr.already_delivered_today(now) == ""
    # 今天跑過但**寄失敗**
    _stamp(tmp_path, monkeypatch, "2026-08-27 06:13",
           {"attempted": True, "success": False})
    assert mr.already_delivered_today(now) == ""
    # 檔案壞掉 / 不存在
    f = tmp_path / "run_manifest.json"
    f.write_text("{壞掉", encoding="utf-8")
    assert mr.already_delivered_today(now) == ""
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", tmp_path / "nope.json")
    assert mr.already_delivered_today(now) == ""


def test_a_manual_dispatch_is_never_blocked(tmp_path, monkeypatch):
    """手動觸發是使用者的救援管道(08/26 儲值後就是這樣重寄的)——
    擋掉它等於把救援也關上。"""
    now = dt.datetime(2026, 8, 27, 6, 40, tzinfo=mr.TPE)
    _stamp(tmp_path, monkeypatch, "2026-08-27 06:13",
           {"attempted": True, "success": True, "run_kind": "schedule"})
    for ev in ("workflow_dispatch", "", "local"):
        monkeypatch.setenv("GITHUB_EVENT_NAME", ev)
        assert mr.already_delivered_today(now) == "", ev


def test_the_guard_runs_before_any_work_and_leaves_the_manifest_alone():
    """接線:判斷要在 `main()` 最前面(晚了就白花 LLM 的錢),而且
    **不得覆寫 manifest** —— 那一份正是「今天寄成功了」的證據。"""
    import inspect
    src = inspect.getsource(mr.main)
    i = src.index("already_delivered_today(now_tpe)")
    assert i < src.index("determine_mode("), src[:400]
    assert i < src.index("run_weekend_digest("), src[:400]
    seg = src[i:i + 300]
    assert "return 0" in seg and "_write_run_manifest" not in seg, seg


def test_the_workflow_really_has_a_backup_trigger():
    """守衛做好了而 workflow 只有一個 cron,等於這批什麼都沒改。"""
    from pathlib import Path

    import yaml
    root = Path(mr.__file__).resolve().parent / ".github" / "workflows"
    wf = yaml.safe_load((root / "morning-report-b.yml").read_text(
        encoding="utf-8"))
    on = wf.get("on") or wf.get(True)
    crons = [str(c["cron"]) for c in on["schedule"]]
    assert len(crons) >= 2, ("沒有備援觸發", crons)
    # **都不要落在整點**:那是 GitHub Actions 最壅塞的一分鐘
    mins = [int(c.split()[0]) for c in crons]
    assert all(m != 0 for m in mins), crons
    # 兩者要隔開(同一分鐘等於沒有備援)
    assert max(mins) - min(mins) >= 20, crons
    # **看門狗要排在補漏跑「最晚可能結束」之後,不是它的 cron 之後**
    # (r1 外審):cron 之後就發警報的話,補漏跑還在跑就被判成「沒跑」,
    # 而假警報會讓人把看門狗關掉 —— 那比沒有看門狗更糟。
    job_cap = int(wf["jobs"]["send-report"]["timeout-minutes"])
    delay_allowance = 15          # 實測排程延遲 8~15 分
    latest_finish = (max(int(c.split()[1]) * 60 + int(c.split()[0])
                         for c in crons) + delay_allowance + job_cap)
    wd = yaml.safe_load((root / "report-watchdog-b.yml").read_text(
        encoding="utf-8"))
    wd_on = wd.get("on") or wd.get(True)
    wd_cron = str(wd_on["schedule"][0]["cron"]).split()
    wd_minutes = int(wd_cron[1]) * 60 + int(wd_cron[0])
    if wd_minutes < latest_finish - 12 * 60:      # 跨過 UTC 午夜
        wd_minutes += 24 * 60
    assert wd_minutes > latest_finish, (
        "看門狗會對著還在跑的補漏跑發假警報",
        wd_cron, crons, job_cap, latest_finish, wd_minutes)


def test_the_watchdog_uses_the_same_freshness_rule_as_the_guard(monkeypatch):
    """r2 外審:冪等守衛用**台北日曆日**,看門狗用**3 小時** —— 同一個
    問題兩個尺度。加了補漏跑、看門狗移到 08:05 之後,「04:30 手動跑成功、
    兩個排程都正確跳過」的那一天會被判成沒跑。假警報會訓練人忽略告警。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(mr.__file__).resolve().parent / "tools"))
    import report_watchdog as w

    now = dt.datetime(2026, 8, 27, 8, 5, tzinfo=w.TPE)
    assert not w._too_old(now, "2026-08-27 04:30", 3.5), "同一天卻被判成過期"
    assert not w._too_old(now, "2026-08-27 06:07", 2.0)
    assert w._too_old(now, "2026-08-26 06:07", 26.0), "昨天的沒被判成過期"
    # 逃生門:設了環境變數才回到舊的小時判準
    monkeypatch.setattr(w, "MAX_AGE_HOURS", "3")
    assert w._too_old(now, "2026-08-27 04:30", 3.5)
    monkeypatch.setattr(w, "MAX_AGE_HOURS", "壞掉")
    assert not w._too_old(now, "2026-08-27 04:30", 3.5), "壞值應退回日期判準"


def test_the_three_budget_layers_fit_inside_each_other():
    """2026-08-27 使用者拍板「加時間」(LLM 1200→1800):三層預算要一起
    動 —— 只動 LLM 那層的話,run 預算或 job timeout 會先把它砍掉,
    多出來的時間一秒都用不到(第三十一輪 P2-5 的同一個形狀)。"""
    from pathlib import Path

    import llm_config as lc
    import yaml
    base, _ = lc.timeout_base("deepseek")
    llm_total = lc.timeout_for("max", base)
    assert llm_total >= 1800, llm_total
    assert lc.MAX_TOTAL_TIMEOUT >= llm_total
    # run 預算要裝得下「LLM 全額 + 抓取/渲染的實測 ~800s」
    assert mr.RUN_BUDGET_SECONDS >= llm_total + 800, mr.RUN_BUDGET_SECONDS
    # job timeout 要裝得下 run 預算 + 安裝/上傳邊際
    root = Path(mr.__file__).resolve().parent / ".github" / "workflows"
    wf = yaml.safe_load((root / "morning-report-b.yml").read_text(
        encoding="utf-8"))
    job_s = int(wf["jobs"]["send-report"]["timeout-minutes"]) * 60
    assert job_s > mr.RUN_BUDGET_SECONDS + 120, (job_s, mr.RUN_BUDGET_SECONDS)
