# -*- coding: utf-8 -*-
"""**CI 紅了要看得見為什麼**(2026-09-04)。

job log 要 repo admin 權限才讀得到;沒有 admin 的時候,唯一讀得到的是
check-run 的 annotation。那天 CI 連三個 commit 紅,而全部訊息只有一句
「Process completed with exit code 2」—— 於是只能用猜的,猜了四輪還沒中。

`tools/ci_pytest_annotations.py` 把 pytest 的 junit 報告轉成 annotation。
它的失效模式比它的功能更重要:**診斷工具不該有能力改變 job 的結論**,
也不該在自己壞掉的時候安靜地讓失敗看起來像沒事。

**報告用真的 pytest 跑出來**(外審 2026-09-04 r1 P2):第一版我手寫 XML,
還替每一筆填上 `file` 與 `line` —— 那是我**以為**的形狀。實際上預設的
xunit2 家族兩者都沒有,收集錯誤的 `classname` 還是空的。手寫的 fixture
會把我的誤解釘成通過條件。
"""
import configparser
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import ci_pytest_annotations as ann       # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]

#: 失敗的那個 `def` 刻意放在第 5 行 —— 若行號沒被正確讀出來,結果會是預設的
#: 「第 1 行」,兩者才分得開(放在第 1 行的話兩種情況長得一模一樣)。
_BAD = "# 1\n# 2\n# 3\n# 4\ndef test_bad():\n    assert 1 == 2\n"
_COLLECT = "import nosuchmodule_xyz_zzz  # noqa: F401\n\n\ndef test_x():\n    pass\n"


def _configured_family() -> str:
    """repo 自己設定的 junit 家族 —— 測試要驗**設定之後的行為**,不是某個寫死值。"""
    cp = configparser.ConfigParser()
    cp.read(_ROOT / "pytest.ini", encoding="utf-8")
    return cp.get("pytest", "junit_family", fallback="xunit2")


def _report(dirpath, name, body, family=None):
    """跑一次真的 pytest,回傳它產生的 junit 報告。

    測試檔刻意放在**子目錄**裡:junit 的 `file` 這樣才會含有路徑分隔符,
    「Windows 的反斜線有沒有被正規化」才有機會被量到。放在根目錄的話
    `file` 只是 `test_bad.py`,那個反例分不出勝負(第一版就是這樣)。
    """
    f = dirpath / "sub" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body, encoding="utf-8")
    xml = dirpath / "r.xml"
    subprocess.run(
        [sys.executable, "-m", "pytest", str(dirpath), "-q", f"--junitxml={xml}",
         "-p", "no:cacheprovider",
         "-o", f"junit_family={family or _configured_family()}"],
        cwd=str(dirpath), capture_output=True, timeout=300)
    assert xml.exists(), "pytest 沒有產生報告 —— 這個 fixture 沒有量到東西"
    return xml


@pytest.fixture(scope="module")
def real_report(tmp_path_factory):
    """一般失敗。"""
    return _report(tmp_path_factory.mktemp("junit_fail"), "test_bad.py", _BAD)


@pytest.fixture(scope="module")
def collect_error_report(tmp_path_factory):
    """收集錯誤要**單獨跑一份**:它會中斷整個 session,同一輪裡其他測試根本
    不會執行 —— 那正是 `Interrupted: 1 error during collection` 的真實行為
    (也正是 exit code 2 的來源)。"""
    return _report(tmp_path_factory.mktemp("junit_collect"), "test_collect.py", _COLLECT)


def test_a_real_failure_points_at_the_real_line(real_report):
    rows = {r[2].split("::")[-1]: r for r in ann.annotations(real_report)}
    assert "test_bad" in rows, rows
    file, line, _title, body = rows["test_bad"]
    assert file.endswith("test_bad.py"), file
    assert "\\" not in file, f"Windows 的反斜線路徑 annotation 認不得:{file}"
    assert line == "5", f"行號沒被讀出來(預設會退成 1):{line}"
    assert "assert" in body


def test_a_collection_error_is_annotated_too(collect_error_report):
    """**收集錯誤正是 exit code 2 的那一種** —— 也是最需要被說出來的那一種。"""
    rows = [r for r in ann.annotations(collect_error_report)
            if "test_collect" in r[2] + r[0]]
    assert rows, list(ann.annotations(collect_error_report))
    file, line, _title, body = rows[0]
    assert file.endswith("test_collect.py"), file
    assert line == "1"                      # 整個模組掛掉,沒有哪一行特別對
    assert "nosuchmodule_xyz_zzz" in body


def test_the_fallback_still_points_at_a_python_file(tmp_path):
    """**`file` 不見時的退路也要指得到地方。**

    `pytest.ini` 現在釘 legacy,所以 `file` 永遠在,那條退路平常走不到 ——
    但它存在的理由正是「有人把設定改回去」。xunit2 產生的報告沒有 `file`、
    收集錯誤的 `classname` 也是空的,只剩 dotted 名字可以還原;少了 `.py`
    的路徑 annotation 會指不到地方。
    """
    xml = _report(tmp_path, "test_bad.py", _BAD, family="xunit2")
    rows = list(ann.annotations(xml))
    assert rows, "xunit2 報告裡沒有失敗 —— 這條測試沒有量到東西"
    file = rows[0][0]
    assert file.endswith(".py"), file
    assert "\\" not in file, file


def test_the_message_survives_the_workflow_command_syntax():
    """換行與 `%` 沒跳脫的話,annotation 會斷在第一行 —— 訊息等於沒有。"""
    assert ann._esc("a\nb") == "a%0Ab"
    assert ann._esc("100% wrong") == "100%25 wrong"
    assert "\n" not in ann._esc("x\r\ny")


def test_a_missing_report_is_reported_not_swallowed(tmp_path, capsys):
    """**沒有報告不等於沒有失敗**:pytest 在寫出報告之前就死掉最需要被說出來。"""
    rc = ann.main(["x", str(tmp_path / "nope.xml")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "::error" in out and "找不到" in out


def test_a_broken_report_is_reported_not_swallowed(tmp_path, capsys):
    p = tmp_path / "r.xml"
    p.write_text("<not-xml", encoding="utf-8")
    rc = ann.main(["x", str(p)])
    out = capsys.readouterr().out
    assert rc == 0 and "::error" in out


def test_the_diagnostic_can_never_change_the_verdict(real_report, capsys):
    """它自己永遠 exit 0:job 的紅綠由 pytest 那一步決定。"""
    assert ann.main(["x", str(real_report)]) == 0
    assert ann.main(["x"]) == 0
    assert "::error" in capsys.readouterr().out


def test_ci_actually_produces_and_consumes_the_report():
    """沒有接上去的話,這整個檔在守一個不存在的東西。"""
    wf = yaml.safe_load((_ROOT / ".github" / "workflows" / "ci.yml")
                        .read_text(encoding="utf-8"))
    steps = wf["jobs"]["test"]["steps"]
    run_i = next(i for i, s in enumerate(steps)
                 if "pytest" in str(s.get("run") or "") and "junitxml" in str(s.get("run")))
    xml = str(steps[run_i]["run"]).split("--junitxml=")[1].split()[0].strip()
    ann_i = next(i for i, s in enumerate(steps)
                 if "ci_pytest_annotations.py" in str(s.get("run") or ""))
    assert ann_i > run_i, "轉換要排在測試之後"
    assert "always()" in str(steps[ann_i].get("if") or ""), \
        "沒有 always() 的話,測試失敗時這一步會被跳過 —— 剛好是唯一需要它的時候"
    assert xml in str(steps[ann_i]["run"]), (xml, steps[ann_i]["run"])
    assert (_ROOT / "tools" / "ci_pytest_annotations.py").exists()
    # 報告是 runner 上的產物,不該進版控。**問 git 本人**,不要用子字串比對
    # .gitignore:把那一行註解掉之後字串還在,守衛照樣綠(第一版當場踩到)。
    r = subprocess.run(["git", "check-ignore", "-q", xml], cwd=str(_ROOT),
                       capture_output=True, timeout=60)
    assert r.returncode in (0, 1), f"git check-ignore 查不動({r.returncode}) —— 不知道不等於沒事"
    assert r.returncode == 0, f"{xml} 沒有被 git 忽略,會被 commit 進去"
