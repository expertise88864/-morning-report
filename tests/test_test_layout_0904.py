# -*- coding: utf-8 -*-
"""**測試的組織方式**(外審 2026-09-04 P3:測試考古學)。

外審說 `test_batch_2026_08_19.py` 這類以批次/日期命名的檔案讓人難以按主題
找測試,而它們佔了測試檔的六分之一。它們的共同性質其實不是主題,是**來歷**
—— 每一個都對應一次真實事故或一輪外審的 finding。所以它們搬進
`tests/incidents/`,`tests/` 底下留下的就是以主題命名的檔案。

標記用**目錄**套(判斷寫在 `tests/conftest.py`),不是在 26 個檔案裡各寫
一行:新丟進來的檔自動被標,不會有人忘記。

這個檔釘住三件事:批次檔不再回到 `tests/` 根、目錄裡的每一條真的帶著標記、
以及**指向測試檔的說明不會爛掉**(搬家當天就有六處指到不存在的路徑)。
"""
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_INCIDENTS = _ROOT / "tests" / "incidents"


def _collected(args) -> int:
    r = subprocess.run([sys.executable, "-m", "pytest", *args, "--collect-only", "-q",
                        "-p", "no:cacheprovider"],
                       cwd=str(_ROOT), capture_output=True, encoding="utf-8",
                       errors="replace", timeout=900)
    m = re.findall(r"(\d+) tests? collected", r.stdout or "")
    if not m:
        assert "no tests collected" in (r.stdout or ""), (r.stdout or "")[-800:]
        return 0
    return int(m[-1])


def test_batch_named_files_live_in_the_incidents_directory():
    stray = sorted(p.name for p in (_ROOT / "tests").glob("test_batch_*.py"))
    assert not stray, f"這些以批次命名的檔還在 tests/ 根:{stray}"
    moved = sorted(p.name for p in _INCIDENTS.glob("test_batch_*.py"))
    assert len(moved) >= 20, f"tests/incidents/ 只有 {len(moved)} 個檔 —— 搬家是不是掉了?"


def test_the_directory_itself_applies_the_marker():
    """**驗行為,不驗 conftest 長相**:標記是動態套的,只看原始碼看不出結果。"""
    total = _collected(["tests/incidents"])
    marked = _collected(["tests/incidents", "-m", "incident"])
    assert total >= 300, total
    assert marked == total, f"{total - marked} 條沒有被標成 incident"
    # 而且不會外溢到別的目錄(否則這個標記就不代表什麼了)
    assert _collected(["tests/test_run_quality.py", "-m", "incident"]) == 0


def test_no_pointer_names_a_test_file_that_does_not_exist():
    """**指向測試的說明會爛掉。**

    這個 repo 的導航方式是「生產模組的註解指名它的守衛在哪個測試檔」——
    那是真正在用的索引。搬家當天就有六處指到不存在的路徑,而那種錯誤沒有
    任何東西會發現:它只是一句讀起來很有信心的假話。
    """
    pat = re.compile(r"tests/[A-Za-z0-9_/]+\.py")
    bad = {}
    for path in sorted(_ROOT.rglob("*.py")):
        rel = path.relative_to(_ROOT)
        if any(x.startswith(".") or x in ("__pycache__", ".venv") for x in rel.parts):
            continue
        for ref in set(pat.findall(path.read_text(encoding="utf-8"))):
            if not (_ROOT / ref).exists():
                bad.setdefault(str(rel), []).append(ref)
    # **設定檔也會指路**(外審 2026-09-04 r1 P3):`pytest.ini` 的 marker 說明
    # 就指到一個刻意不存在的 conftest,而第一版只掃 .py 與 workflow YAML。
    others = (sorted((_ROOT / ".github" / "workflows").glob("*.yml"))
              + [_ROOT / "pytest.ini", _ROOT / "mypy.ini"]
              + sorted((_ROOT / "tools").glob("*.sh")))
    for path in others:
        if not path.exists():
            continue
        for ref in set(pat.findall(path.read_text(encoding="utf-8"))):
            if not (_ROOT / ref).exists():
                bad.setdefault(path.name, []).append(ref)
    assert not bad, f"這些地方指向不存在的測試檔:{bad}"


def test_a_sibling_directory_does_not_inherit_the_marker():
    """**前綴比對不等於路徑包含**(外審 2026-09-04 r1 P3)。

    `str.startswith` 會把 `tests/incidents_archive/` 也算進 `tests/incidents/`,
    於是 `-m incident` 悄悄多出一批不是事故回歸的測試 —— 而那個標記的意義
    就是「來歷」。同一天在發佈路徑上也踩過一次同型的錯
    (`state/../morning_report.py` 通過了 `case "$p" in state/*)`)。
    """
    from conftest import is_incident_path
    tests = _ROOT / "tests"
    assert is_incident_path(tests / "incidents" / "test_batch_backup_cron.py")
    assert is_incident_path(tests / "incidents" / "deeper" / "test_x.py")
    assert not is_incident_path(tests / "incidents_archive" / "test_x.py")
    assert not is_incident_path(tests / "test_run_quality.py")
    assert not is_incident_path("")
