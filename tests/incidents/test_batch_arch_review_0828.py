# -*- coding: utf-8 -*-
"""2026-08-28 架構層外審裡**可以立刻修**的那幾條(其餘是架構決策,待定案)。

主軸只有一個:**判準只有一份,而且真的接上了。**
"""
import io
import re
from pathlib import Path

import morning_report as mr
import render_utils as ru

_ROOT = Path(mr.__file__).resolve().parent


def test_an_external_link_cannot_carry_a_dangerous_scheme():
    """`html.escape` 擋得住屬性逃逸(`" onclick=`),**擋不住 scheme**:
    `javascript:alert(1)` escape 完還是它自己,照樣是可點的 href。
    信件用戶端多半會擋,但那是別人的邊界。"""
    for bad in ("javascript:alert(1)", "JavaScript:x", "data:text/html,x",
                "httpx://evil", "httpjavascript:x", "//evil.example",
                "vbscript:x", "https://a.b/" + "x" * 600, "", None):
        assert ru.safe_href(bad) == "", repr(bad)
    # 換行可以把屬性拆開 → 控制字元一律擋
    assert ru.safe_href("https://a.b/\nonload=1") == ""
    assert ru.safe_href("https://a.b/\tx") == ""
    for good in ("https://ok.example/x?a=1", "http://ok.example"):
        assert ru.safe_href(good) == good, good


def test_the_url_rule_has_exactly_one_implementation():
    """先前有兩份:`morning_report._safe_source_url`(只用在一個寫入點)
    與 `render_utils._is_web_url`(**零呼叫端**)。兩份判準會漂移,
    而其中一份是死碼 —— 死碼給人「有在防」的錯覺。"""
    assert mr._safe_source_url("javascript:alert(1)") == ""
    assert mr._safe_source_url("https://ok.example") == "https://ok.example"
    src = io.open(_ROOT / "render_utils.py", encoding="utf-8").read()
    # 禁的是舊**實作**,不是名字 —— `safe_href` 的註解會提到它的歷史,
    # 那是說明不是第二份判準(第一版把兩者混為一談,自己紅了)。
    assert "def _is_web_url" not in src, "舊的那份還在(會漂移)"
    body = (_ROOT / "morning_report.py").read_text(encoding="utf-8")
    i = body.index("def _safe_source_url(")
    seg = body[i:i + 1400]
    assert "safe_href" in seg, "沒有委派過去,等於又養了第二份判準"


def test_every_href_goes_through_the_rule():
    """**沒接線等於不存在。** 這條掃原始碼:每一個 `href=` 的插值都要
    經過判準。先前 CWA 警特報、停班停課公告、體育連結都只做了 escape。"""
    for name in ("morning_report.py", "render_utils.py"):
        src = io.open(_ROOT / name, encoding="utf-8").read()
        for m in re.finditer(r"href=['\"]\{([^}]*)\}", src):
            expr = m.group(1)
            assert ("safe_href" in expr or "_safe_source_url" in expr
                    or "clean" in expr or "link" in expr), f"{name}: {expr}"


def test_the_sports_link_is_filtered_too():
    """走**渲染路徑**驗一次(不是只驗判準函式本身)—— 判準寫對而沒接上
    渲染,信裡照樣是可點的 javascript: 連結。"""
    import html as _h
    out = ru._render_sports_html(
        {"news": {"中華職棒": [
            {"title": "壞連結", "link": "javascript:alert(1)"},
            {"title": "好連結", "link": "https://ok.example/a"}]}},
        _h)
    assert "javascript:" not in out, out[:400]
    assert "https://ok.example/a" in out, out[:400]
    assert "壞連結" in out, "壞連結該留下標題、只是不可點"


def test_the_watchdog_time_in_docs_matches_the_cron():
    """事故當下對錯時間軸比沒有註解更糟。cron 移過兩次,而註解、模組
    docstring、告警信內文都還停在舊時間(2026-08-28 外審 P2)。"""
    import yaml
    wf_text = io.open(_ROOT / ".github" / "workflows" / "report-watchdog-b.yml",
                      encoding="utf-8").read()
    wf = yaml.safe_load(wf_text)
    crons = [c["cron"] for c in wf[True]["schedule"]]
    # 2026-08-31 使用者定案 SLA(09:00 前必到)→ 整組前移:23:50Z = 07:50
    assert crons == ["50 23 * * *"], crons
    # 人看得到的文字要與 cron 說同一件事(釘「沒有舊時刻」,不逐字釘位置)
    for stale in ("07:30", "08:05"):
        assert stale not in wf_text, f"workflow 裡還留著舊時間 {stale}"
    doc = io.open(_ROOT / "tools" / "report_watchdog.py",
                  encoding="utf-8").read()
    assert "07:50" in doc.split("\n\n")[0], doc.split("\n\n")[0]


def test_a_failed_rebase_is_cleaned_up():
    """語意衝突時原本只 `|| true` —— 工作區會停在 rebase in progress,
    下一輪的失敗語意不再乾淨(看起來像網路問題,其實是卡住)。"""
    sh = io.open(_ROOT / "tools" / "push_state.sh", encoding="utf-8").read()
    i = sh.index("git pull --rebase --autostash")
    assert "rebase --abort" in sh[i:i + 400], sh[i:i + 400]
    assert "exit 1" in sh, "推不上去仍然要紅"


def test_no_operational_reference_to_the_old_workflow_names():
    """r1 外審:改名時我用 `*.py *.yml *.sh *.md` 掃引用,漏了 `.bat` ——
    `commit_push.bat` 還在 stage 舊檔名,跑它會 `git add` 失敗連帶 tests/
    ci.yml 都沒 stage 到。**守衛要掃性質涵蓋的所有檔**,不是我記得的那
    幾種副檔名;而「哪些檔算 operational」用排除法(說明歷史的註解與
    external review 紀錄除外),不用列舉法。"""
    import subprocess
    r = subprocess.run(
        ["git", "grep", "-l", "-E",
         r"morning-report\.yml|report-watchdog\.yml"],
        cwd=str(_ROOT), capture_output=True,
        encoding="utf-8", errors="replace", timeout=60)
    hits = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    # 允許的殘留:純歷史說明(測試自己、外審 context)
    allowed = {"tests/incidents/test_batch_arch_review_0828.py"}
    bad = [h for h in hits if h not in allowed]
    assert not bad, f"這些檔還在引用舊 workflow 檔名:{bad}"
