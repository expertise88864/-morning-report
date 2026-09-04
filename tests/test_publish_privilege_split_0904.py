# -*- coding: utf-8 -*-
"""**跑第三方依賴的 job 不得握有 git 寫入憑證**(外審 2026-09-04 P2)。

原本 `send-report` 一個 job 就做完全部:`pip install` 整套依賴、跑應用程式碼
(拿 Gmail 憑證與 LLM 金鑰)、最後 `git push` 發佈 state。`actions/checkout`
預設把 token 寫進 git config,所以那段期間**任何一個行程**都能推 main ——
一個被投毒的套件在 import 時就能做到。權限分離之後:

    send-report   contents: read   + persist-credentials: false   ← 依賴在這裡跑
    publish-state contents: write  + 不裝任何依賴                  ← 只有它能推

延後發佈不會開出重複寄信的窗口:concurrency group 是 **workflow 層級**的,
備援班要等整個 run(含 publish-state)結束才拿得到名額。這個檔把上面每一句
話都變成機械檢查 —— 註解會過期,斷言不會。
"""
import copy
import os
import re
from pathlib import Path

import pytest
import yaml

import morning_report as mr

_ROOT = Path(mr.__file__).resolve().parent
_WF = _ROOT / ".github" / "workflows" / "morning-report-b.yml"


def _wf() -> dict:
    return yaml.safe_load(_WF.read_text(encoding="utf-8"))


def test_the_job_that_runs_dependencies_cannot_push():
    wf = _wf()
    assert wf.get("permissions") == {"contents": "read"}, wf.get("permissions")
    send = wf["jobs"]["send-report"]
    assert send["permissions"] == {"contents": "read"}, send["permissions"]

    checkout = next(s for s in send["steps"] if "actions/checkout" in str(s.get("uses")))
    assert (checkout.get("with") or {}).get("persist-credentials") is False, checkout
    assert "token" not in (checkout.get("with") or {}), \
        "checkout 帶 token 就等於把憑證寫進 git config"

    # 這個 job 裡不得再有任何 push
    blob = _blob(send)
    for forbidden in ("git push", "push_committed_state", "push_state.sh"):
        assert forbidden not in blob, forbidden


def _blob(obj) -> str:
    """yaml.dump 預設 width=80,會在長字串**中間**插入換行 —— `git add` 就這樣
    被斷成兩截,讓「有沒有 git add」這種黑名單/白名單檢查靜默失準。"""
    return yaml.dump(obj, allow_unicode=True, width=10 ** 9)


def _executable(script: str) -> str:
    """只留下會被 shell 執行的那些行。

    守衛看的是**會執行什麼**,不是腳本怎麼解釋自己:「不可以讓別人刪掉
    morning_report.py」這句註解不該被當成「這個 job 跑了應用程式」。只剔掉
    整行註解 —— `python x.py  # 說明` 這種行尾註解仍然完整留著被檢查。
    """
    return "\n".join(ln for ln in str(script or "").splitlines()
                     if not ln.lstrip().startswith("#"))


def _without_shell_comments(job) -> str:
    stripped = copy.deepcopy(job)
    for step in stripped.get("steps") or []:
        if "run" in step:
            step["run"] = _executable(step["run"])
    return _blob(stripped)


def test_only_the_clean_job_holds_write_and_it_installs_nothing():
    pub = _wf()["jobs"]["publish-state"]
    assert pub["permissions"] == {"contents": "write"}
    assert pub["needs"] == "send-report"
    blob = _without_shell_comments(pub)
    # **不裝、也不跑任何第三方套件** —— 否則權限分離只是換個地方承擔同一個風險
    for forbidden in ("pip install", "requirements", "morning_report",
                      "setup-python", "npm ", "curl "):
        assert forbidden not in blob, forbidden
    # 它用的是只有 stdlib 的那支原語
    assert "import state_publish" in blob


def test_the_receipt_is_published_before_the_state_contract_gate():
    """收據**不受契約影響**:「已經寄出但 state 沒發佈成功」只有它答得出來。"""
    wf = _wf()
    steps = wf["jobs"]["publish-state"]["steps"]
    names = [str(s.get("name") or (s.get("uses") or "")) for s in steps]
    r_i = next(i for i, s in enumerate(steps) if "state_publish" in str(s.get("run") or ""))
    s_i = next(i for i, s in enumerate(steps) if "push_state.sh" in str(s.get("run") or ""))
    assert r_i < s_i, f"收據必須先推:{names}"
    r_if = str(steps[r_i].get("if") or "")
    assert "contract_outcome" not in r_if and "state_dirty" not in r_if, \
        f"收據被契約或 state_dirty 擋住了 —— 那正是它獨立存在的理由:{r_if}"
    assert "contract_outcome == 'success'" in str(steps[s_i].get("if") or "")


def test_the_receipt_survives_a_timeout_after_the_mail_went_out(monkeypatch):
    """**寄出之後、尾端卡住**是收據最需要在的那一刻(Codex 2026-09-04 P1)。

    contract 約 3 分鐘、job 上限 50 分;SMTP 成功後撞上逾時的話,舊設計
    (交棒排在契約之後)會讓收據永遠到不了發佈 job —— workflow 結束、
    concurrency 釋放、下一班讀不到遠端收據就再寄一封(守衛是 fail-open)。
    三件事一起才成立:交棒緊接在晨報之後、用 `always()`、發佈 job 不因上游
    失敗而被跳過。
    """
    wf = _wf()
    send = wf["jobs"]["send-report"]["steps"]
    report_i = next(i for i, s in enumerate(send) if "morning_report.py" in str(s.get("run") or ""))
    receipt_i = next(i for i, s in enumerate(send)
                     if (s.get("with") or {}).get("name") == "delivery-receipt")
    assert receipt_i == report_i + 1, "收據交棒要緊接在晨報之後,中間不得插入會卡住的步驟"
    assert "always()" in str(send[receipt_i].get("if") or "")
    assert (send[receipt_i].get("with") or {}).get("if-no-files-found") == "ignore"

    pub_if = str(wf["jobs"]["publish-state"].get("if") or "")
    assert "!cancelled()" in pub_if, "上游失敗時發佈 job 必須照跑,否則收據還是到不了"
    assert "state_dirty" not in pub_if, "job 層級綁 state_dirty 會把收據一起擋掉"


def test_deletions_are_handed_off_not_silently_dropped():
    """artifact 是**疊上去**的:被刪掉的檔在發佈 job 的 checkout 裡還在
    (Codex 2026-09-04 P2)。信件存檔的 365 天修剪就是這個形狀 ——
    只 `git add` 的話舊檔永遠留在 repo,保留政策靜默失效。"""
    wf = _wf()
    send_blob = _blob(wf["jobs"]["send-report"])
    assert "--diff-filter=D" in send_blob and "_state_deleted.txt" in send_blob
    upload = next(s for s in wf["jobs"]["send-report"]["steps"]
                  if (s.get("with") or {}).get("name") == "state-to-publish")
    assert "_state_deleted.txt" in str((upload.get("with") or {}).get("path"))
    push = next(s for s in wf["jobs"]["publish-state"]["steps"]
                if "push_state.sh" in str(s.get("run") or ""))
    run = _executable(push.get("run"))
    assert "git rm" in run and "_state_deleted.txt" in run
    # 新增與刪除**都只用驗證器的輸出**;shell 不再自己判斷路徑安全
    assert "python3 -m state_publish paths" in run
    assert "python3 -m state_publish deletions" in run
    assert "case " not in run, "shell 前綴比對擋不住 state/../ —— 交給驗證器"
    # 驗證器**被擋下時要讓整步失敗**:`mapfile < <(cmd)` 的退出碼不受 set -e
    # 檢查,shell 會拿到空陣列繼續跑完 —— 靜默的「成功」比失敗更難發現。
    assert "set -euo pipefail" in run
    assert "< <(" not in run, "process substitution 會吞掉驗證器的退出碼"
    for mode in ("paths", "deletions"):
        assert re.search(rf"python3 -m state_publish {mode} [^\n|]*>", run), (mode, run)
    # 真的會修剪的那條路徑仍在白名單裡(不然這條測試在守一個不存在的東西)
    assert "EMAIL_ARCHIVE_DIR" in (_ROOT / "morning_report.py").read_text(encoding="utf-8")
    src = (_ROOT / "morning_report.py").read_text(encoding="utf-8")
    assert "unlink()" in src[src.index("def archive_report_html"):][:3000]


def test_the_publish_job_only_stages_the_allowlisted_paths():
    """白名單仍只有一份(`_state_push_paths()`),由 send-report 產生後交棒。"""
    wf = _wf()
    send_blob = _blob(wf["jobs"]["send-report"])
    assert "_state_push_paths()" in send_blob
    pub_blob = _blob(wf["jobs"]["publish-state"])
    assert "_state_paths.txt" in pub_blob and "git add" in pub_blob
    # 不得出現「整個 state 目錄一起 add」這種繞過白名單的寫法
    assert not re.search(r"git add\s+(-A\s+)?state/?\s*$", pub_blob, flags=re.M), pub_blob


def test_the_app_defers_the_receipt_push_when_it_has_no_credential(tmp_path, monkeypatch):
    """`STATE_PUSH_DEFERRED=1` 的意思是「這個行程不該推」—— 收據也一樣。

    先前它只擋整批 state 的 push,收據照推;在唯讀 job 裡那會每天失敗一次,
    而失敗的標籤 `delivery_receipt_publish` 是 defect 級(`_ALARMING`)。
    """
    receipt = tmp_path / "r.json"
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", receipt)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setenv("STATE_PUSH_DEFERRED", "1")
    monkeypatch.setattr(mr, "publish_receipt_from_remote_base",
                        lambda *a, **k: pytest.fail("唯讀 job 不得嘗試 push"))
    mr._DEGRADED_STEPS.clear()
    mr._RUN_MANIFEST.pop("delivery", None)
    mr._publish_delivery_receipt("2026-09-04", {"success": True,
                                                "delivered_at": "2026-09-04T07:46:18+08:00"})
    assert receipt.exists(), "收據檔還是要寫(發佈 job 靠它)"
    assert "delivery_receipt_publish" not in mr._DEGRADED_STEPS, "延後發佈不是降級"
    assert (mr._RUN_MANIFEST.get("delivery") or {}).get("receipt_publish") == "deferred"

    # 沒有這個旗標時(本機以外的舊行為)照樣推
    pushed = []
    monkeypatch.setenv("STATE_PUSH_DEFERRED", "0")
    monkeypatch.setattr(mr, "publish_receipt_from_remote_base",
                        lambda *a, **k: pushed.append(a))
    mr._publish_delivery_receipt("2026-09-04", {"success": True,
                                                "delivered_at": "2026-09-04T07:46:18+08:00"})
    assert pushed, "沒有延後旗標時應該立刻發佈"


def test_the_deferred_mark_lands_in_the_manifest_file(tmp_path, monkeypatch):
    """**留痕要在檔案裡,不是只在記憶體**(Codex 2026-09-04 P3)。

    `_publish_delivery_receipt` 是在 manifest **atomic write 之後**才被呼叫的,
    它在那裡改 `_RUN_MANIFEST` 不會進檔案;而 `_refresh_state_writes_in_manifest`
    只補 state-write 欄位。看門狗與品質判準讀的是檔案 —— 記憶體裡的宣稱它們
    看不到,那等於沒有留痕。
    """
    import datetime as _dt
    import json
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"date": "2026-09-04 07:12"}), encoding="utf-8")
    monkeypatch.setattr(mr, "RUN_MANIFEST_FILE", m)
    monkeypatch.setattr(mr, "DELIVERY_RECEIPT_FILE", tmp_path / "r.json")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("STATE_PUSH_DEFERRED", "1")
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(mr, "_publish_terminal_outcome", lambda *a, **k: None)
    monkeypatch.setattr(mr, "_RUN_STAMP", "")
    mr._set_run_stamp(_dt.datetime(2026, 9, 4, 7, 12, tzinfo=mr.TPE))
    mr._mark_delivery_in_manifest(attempted=True, success=True)
    dv = json.loads(m.read_bytes().decode("utf-8"))["delivery"]
    assert dv.get("receipt_publish") == "deferred", dv

    # 沒有那個旗標時不留這一格(它描述的是「這一班沒有寫入憑證」)
    monkeypatch.setenv("STATE_PUSH_DEFERRED", "0")
    m.write_text(json.dumps({"date": "2026-09-04 07:12"}), encoding="utf-8")
    monkeypatch.setattr(mr, "_publish_delivery_receipt", lambda *a, **k: None)
    mr._mark_delivery_in_manifest(attempted=True, success=True)
    assert "receipt_publish" not in json.loads(m.read_bytes().decode("utf-8"))["delivery"]


@pytest.mark.parametrize("evil", [
    "state/../morning_report.py",     # Codex r2 P1 點名的那一條
    "state/../../etc/passwd",
    "/etc/passwd",
    "state/./x",
    "state//x",
    "morning_report.py",
    "..",
    "state" + chr(92) + "x",          # 反斜線
    "C:/x",
])
def test_the_handoff_list_cannot_escape_the_state_directory(evil):
    """**交棒清單是不可信輸入**:它由跑過第三方依賴的 job 產生,而讀它的是
    唯一有寫入權限的 job。前綴比對擋不住 `state/../` —— `git rm` 會正規化它,
    於是程式碼被刪掉並推上 main,正好把這次權限分離要消除的路徑打開。"""
    import state_publish as sp
    with pytest.raises(sp.UnsafePublishPath):
        sp.normalize_repo_path(evil)
    with pytest.raises(sp.UnsafePublishPath):
        sp.validated_deletions([evil], ["state/emails", "state/history.json"])


def test_the_validator_accepts_what_it_should_and_scopes_deletions():
    import state_publish as sp
    assert sp.normalize_repo_path(" state/emails/2026-09-04.html.gz ") == \
        "state/emails/2026-09-04.html.gz"
    allow = ["state/emails", "state/history.json"]
    assert sp.validated_deletions(["state/emails/x.gz", ""], allow) == ["state/emails/x.gz"]
    assert sp.validated_deletions([], allow) == []          # 沒有刪除是合法的
    # 白名單之外的刪除要擋(即使它在 state/ 底下)
    with pytest.raises(sp.UnsafePublishPath):
        sp.validated_deletions(["state/story_ledger.json"], ["state/emails"])
    # 空白名單不是「沒東西要發佈」,是清單壞了
    with pytest.raises(sp.UnsafePublishPath):
        sp.validated_allowlist([])


def test_the_validator_cli_fails_closed(tmp_path, capsys):
    """CLI 不合格就非零退出 —— 發佈步驟隨之失敗,什麼都不會被推出去。"""
    import state_publish as sp
    paths = tmp_path / "p.txt"
    paths.write_text("state/emails\n", encoding="utf-8")
    dele = tmp_path / "d.txt"
    dele.write_text("state/../morning_report.py\n", encoding="utf-8")
    assert sp._cli(["state_publish", "paths", str(paths)]) == 0
    assert sp._cli(["state_publish", "deletions", str(paths), str(dele)]) == 1
    assert "unsafe-publish-path" in capsys.readouterr().err
    # 刪除檔不存在(當天沒有刪除)是合法的
    assert sp._cli(["state_publish", "deletions", str(paths), str(tmp_path / "nope.txt")]) == 0


def test_the_credentialed_job_calls_the_interpreter_that_is_guaranteed_to_exist():
    """這個 job 刻意沒有 `setup-python`,所以只能用 image 保證有的 `python3`。

    裸 `python` 在不在 PATH 上要看 Ubuntu image 有沒有裝 python-is-python3 ——
    猜錯不會安靜地少發佈一次 state 就算了:收據推不上去,下一班讀不到遠端
    收據就再寄一封(那個守衛是 fail-open)。
    """
    pub = _wf()["jobs"]["publish-state"]
    assert not any("setup-python" in str(s.get("uses") or "") for s in pub["steps"])
    calls = []
    for step in pub["steps"]:
        for ln in _executable(step.get("run") or "").splitlines():
            calls += re.findall(r"(?<![\w./-])python3?(?![\w.-])", ln)
    assert calls, "發佈 job 應該要呼叫直譯器(不然這條測試在守一個不存在的東西)"
    assert set(calls) == {"python3"}, calls


def test_the_publish_primitive_needs_no_third_party_package():
    """發佈 job 不 `pip install`,所以那支原語只能用 stdlib —— 機械確認。"""
    import ast
    src = (_ROOT / "state_publish.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    stdlib = set(getattr(__import__("sys"), "stdlib_module_names", ()))
    assert imported, "掃不到任何 import 就是判準壞了"
    assert imported <= stdlib | {"__future__"}, imported - stdlib
    # morning_report 仍 re-export,既有呼叫端不必改寫
    import state_publish as sp
    assert mr.publish_receipt_from_remote_base is sp.publish_receipt_from_remote_base
    assert mr.RECEIPT_REPO_PATH == sp.RECEIPT_REPO_PATH


def test_the_backup_run_cannot_start_before_publish_finishes():
    """收據從『SMTP 當下』延到『發佈 job』不會開出重複寄信的窗口 ——
    前提是 concurrency 是 **workflow 層級**的(備援班等整個 run 結束)。"""
    wf = _wf()
    assert wf["concurrency"]["group"] == "state-writers"
    assert wf["concurrency"]["cancel-in-progress"] is False
    for name, job in wf["jobs"].items():
        assert "concurrency" not in job, (name, "job 層級 concurrency 會讓上面那句話不成立")
    assert os.path.exists(_WF)
