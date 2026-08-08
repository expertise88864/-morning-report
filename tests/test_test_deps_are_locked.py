# -*- coding: utf-8 -*-
"""**測試用到的套件要在 lock 裡**(2026-08-09,CI 連紅四個 commit)。

本機什麼都裝得有,CI 只裝 `requirements.lock`。於是一條裸 `import yaml`
在本機永遠是綠的,而在 CI 是 ImportError —— 而且那條測試盯的正是
「CI 會不會容忍失敗」,守衛失效的地方剛好就是它要保護的地方。

紅了四個 commit 沒有人發現,因為 preflight 跑的是**本機**的環境。
這個檔把那個落差變成本機就看得見的東西。

**不 import 那些套件**(裝不到也要跑得動):只靜態掃 `import` 那一行。
"""
from __future__ import annotations

import ast
import io
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

#: `import 名稱 → 發行套件名`。兩者不同的才要列。
_DIST = {"yaml": "pyyaml", "dateutil": "python-dateutil", "bs4": "beautifulsoup4",
         "PIL": "pillow", "sklearn": "scikit-learn", "opencc": "opencc-python-reimplemented"}


def _repo_modules() -> set:
    """這個 repo 自己的模組(含 `tests/` 裡的共用 fixture 與各子目錄)。

    **不能只掃根目錄**:`fixtures_analysis` 住在 `tests/`、`tools/` 是
    一個套件 —— 漏掉它們的話,這個守衛第一次跑就會把自己人全報成缺套件,
    而「誤報太多」的下場通常是有人把守衛關掉。
    """
    out = {"tests", "conftest"}
    for p in _ROOT.rglob("*.py"):
        rel = p.relative_to(_ROOT).parts
        if any(x.startswith(".") or x in ("__pycache__", ".venv") for x in rel):
            continue
        out.add(p.stem)
        if len(rel) > 1:
            out.add(rel[0])          # 套件目錄本身(`tools`)
    return out


def _files_pytest_loads() -> list:
    """pytest 會載入的檔案 —— **不只 `test_*.py`**(外審)。

    `conftest.py` 是自動載入的、`fixtures_analysis.py` 被三十幾個測試
    直接 import,而它們先前完全不在掃描範圍裡:在那裡放一個 lock 沒有的
    import,CI 會在 **collection 階段**就 ImportError,而這個守衛是綠的。
    """
    out = [p for p in sorted((_ROOT / "tests").rglob("*.py"))
           if "__pycache__" not in p.parts]
    root_conf = _ROOT / "conftest.py"
    if root_conf.exists():
        out.append(root_conf)
    return out


def _top_level_imports(path: Path) -> set:
    """這個檔 import 了哪些頂層模組(含函式內的 import)。"""
    tree = ast.parse(io.open(path, encoding="utf-8").read(), str(path))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def _locked_distributions() -> set:
    """lock 裡**釘住的套件名**。

    **不能用子字串比對**:`"pyyaml" in lock` 對 `pyyamlxx==…` 也成立,
    於是把 lock 改壞的突變照樣是綠的(第一版當場踩到)。逐行取
    `名稱==版本` 的名稱。
    """
    out = set()
    for line in io.open(_ROOT / "requirements.lock", encoding="utf-8"):
        m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*==", line)
        if m:
            out.add(m.group(1).lower().replace("_", "-"))
    return out


def test_every_third_party_module_the_tests_import_is_in_the_lock():
    lock = _locked_distributions()
    assert len(lock) > 20, f"lock 只解析出 {len(lock)} 個套件 —— 解析壞了"
    repo, missing = _repo_modules(), {}
    files = _files_pytest_loads()
    assert len(files) > 40, f"只掃到 {len(files)} 個檔 —— 範圍縮掉了"
    assert any(f.name == "conftest.py" for f in files), "conftest 不在範圍裡"
    for f in files:
        for name in _top_level_imports(f):
            if name in sys.stdlib_module_names or name in repo or name == "pytest":
                continue
            dist = _DIST.get(name, name).lower().replace("_", "-")
            if dist not in lock:
                missing.setdefault(dist, []).append(f.name)
    assert not missing, (
        f"測試 import 了 lock 裡沒有的套件:{missing} —— "
        "本機裝得有所以是綠的,CI 只裝 lock,那裡是 ImportError")


def test_the_guard_would_notice_a_missing_package():
    """**守衛自己要驗得動**:把 lock 換成空字串,同一套邏輯要判失敗。"""
    repo = _repo_modules()
    names = set()
    for f in _files_pytest_loads():
        names |= {n for n in _top_level_imports(f)
                  if n not in sys.stdlib_module_names and n not in repo
                  and n != "pytest"}
    assert names, "測試完全沒有用到第三方套件 —— 那這個守衛沒有量到東西"


def test_no_test_makes_itself_optional_with_importorskip():
    """**守衛不得靠「裝不到就跳過」失效。**

    `pytest.importorskip` 讓一個測試在缺套件的環境自動變綠 —— 而缺套件
    的環境正是 CI。`test_workflow_contract` 先前就是這樣:它盯的是
    「CI 會不會容忍失敗」,而它在 CI 裡從來沒有跑過。
    要用第三方套件就把它加進 `requirements.txt` 並重編 lock。
    """
    # **用 AST 找真正的呼叫**,不是找這幾個字:這個檔自己的說明文字裡
    # 就有「importorskip」,字串比對會讓守衛把自己報成違規。
    hits = []
    for f in _files_pytest_loads():
        tree = ast.parse(io.open(f, encoding="utf-8").read(), str(f))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else fn.id if isinstance(fn, ast.Name) else "")
            if name == "importorskip":
                hits.append(f"{f.name}:{node.lineno}")
    assert not hits, (
        f"這些測試會在缺套件時自動跳過:{hits} —— "
        "而缺套件的環境正是 CI")

