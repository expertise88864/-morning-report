# -*- coding: utf-8 -*-
"""**「測試全綠」不等於「每個模組 import 得動」。**

r19 外審:`attempt_stats.py` 頂層寫著 `from experiment_ledger import ...`,
而 `experiment_ledger.py` 在 2026-08-07 隨整批 shadow/experiment 機制被刪掉。
那個檔從此**根本 import 不起來**,而 3,160 條測試全綠 —— 因為:

  * `compileall` 只驗語法,**不解析依賴**(`from missing import X` 編得過);
  * ruff 不負責證明 import 目標存在;
  * 同一批刪掉的 158 條測試裡,沒有任何一條還 import 它。

它甚至還留在 `MODULE_CEILINGS` 裡被「管著」—— 一個沒有人發現已經死掉的檔。

這條守衛刻意用 **AST 靜態檢查**而不是真的 import:模組層的 import 可能有
副作用(讀檔、起連線),而我們要問的只是「這個名字解析得到嗎」。
"""
import ast
import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

#: 掃描範圍:root 的生產模組 + `tools/`(工作流程真的會執行它們)。
_FILES = sorted(
    [p for p in _ROOT.glob("*.py")]
    + [p for p in (_ROOT / "tools").glob("*.py")])


def _toplevel_imports(tree: ast.Module):
    """模組層、**沒有被 try 包住**的 import 目標(頂層套件名)。

    `try: import X except ImportError:` 是刻意的可選依賴,不算違規。
    包在函式裡的 import 也跳過:那是延後載入,不影響「這個檔 import 得動」。
    """
    out = []
    for node in tree.body:                  # 只看模組層
        if isinstance(node, ast.Import):
            out += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                  # 相對 import(這個 repo 是扁平的)
                continue
            if node.module:
                out.append(node.module.split(".")[0])
    return out


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_every_module_level_import_resolves(path):
    """模組層 import 的每一個名字都要解析得到 —— 否則那個檔是死的。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for name in _toplevel_imports(tree):
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError) as e:      # 連找都找不到
            pytest.fail(f"{path.name} 的 `import {name}` 解析失敗:{e}")
        assert spec is not None, (
            f"{path.name} 頂層 import 了 `{name}`,而它不存在 —— "
            "這個檔 import 不起來(功能刪掉時留下了半個依賴圖)")


def test_the_scan_is_not_vacuous():
    """空集合不算通過(這個 repo 記過這條)。"""
    assert len(_FILES) > 50, len(_FILES)
    assert any(p.name == "morning_report.py" for p in _FILES)
    assert any(p.parent.name == "tools" for p in _FILES)
