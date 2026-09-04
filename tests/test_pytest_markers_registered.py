# -*- coding: utf-8 -*-
"""**每一個用到的 marker 都要登記**(2026-09-04:CI 連紅三個 commit 的原因)。

`pytest.ini` 有 `--strict-markers`,而 `@pytest.mark.slow` 從來沒有登記過。
本機的 pytest 9.0.3 放過了它,CI 的 9.1.1 擋下 —— 收集階段錯誤、退出碼 2、
整個 session 中斷。我看到的全部訊息是「Process completed with exit code 2」,
於是連猜四輪都沒中。

**這個守衛不依賴 pytest 版本**:它自己用 AST 掃出所有 `@pytest.mark.X`,
再跟 `pytest.ini` 的 `markers` 對。也就是說,即使本機那一版剛好放行,
`bash tools/preflight.sh` 一樣會紅 —— 這正是本機閘門存在的意義。

順帶把「本機環境與 CI 不同」這件事寫下來(同一天量到三個):

    pandas  本機 2.2.3   / CI 與 production lock 3.0.3
    pytest  本機 9.0.3   / lock 9.1.1
    Python  本機 3.13    / CI 3.11

所以「本機測試綠」證明的比我以為的少。
"""
import ast
import configparser
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

#: pytest 自己內建的標記,不必登記
_BUILTIN = {"skip", "skipif", "xfail", "parametrize", "usefixtures", "filterwarnings",
            "tryfirst", "trylast"}


def _registered() -> set:
    cp = configparser.ConfigParser()
    cp.read(_ROOT / "pytest.ini", encoding="utf-8")
    raw = cp.get("pytest", "markers", fallback="")
    return {ln.split(":")[0].strip() for ln in raw.splitlines() if ln.strip()}


def _used():
    """所有 `@pytest.mark.X` 的 X → 用到它的檔案。

    **用 AST 找真正的裝飾器**,不是搜這幾個字:這個檔自己的說明文字裡就
    寫著 `@pytest.mark.slow`,字串比對會把守衛自己報成違規。
    """
    out = {}
    for path in sorted(_ROOT.glob("tests/**/*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        except SyntaxError:                 # 語法錯由 compileall 那一關負責
            continue
        for node in ast.walk(tree):
            for deco in getattr(node, "decorator_list", []):
                expr = deco.func if isinstance(deco, ast.Call) else deco
                # pytest.mark.X 或 pytest.mark.X(...)
                if (isinstance(expr, ast.Attribute)
                        and isinstance(expr.value, ast.Attribute)
                        and expr.value.attr == "mark"):
                    out.setdefault(expr.attr, set()).add(path.name)
    return out


def test_every_marker_used_is_registered():
    used = _used()
    assert used, "一個 marker 都沒掃到 —— 掃描條件失準了,這個守衛沒有量到東西"
    reg = _registered()
    missing = {m: sorted(f) for m, f in used.items()
               if m not in reg and m not in _BUILTIN}
    assert not missing, (
        f"這些 marker 沒有登記在 pytest.ini 的 `markers`:{missing}\n"
        "`--strict-markers` 會讓收集階段直接錯 —— 而那是整個 session 中斷、"
        "退出碼 2、訊息只有一句『exit code 2』的那一種失敗。")


def test_the_registration_list_is_not_decoration():
    """登記了卻沒人用的 marker 要清掉 —— 不然這份清單會變成沒人維護的裝飾。"""
    used = set(_used())
    unused = sorted(m for m in _registered() if m not in used)
    assert not unused, f"pytest.ini 登記了但沒有任何測試在用:{unused}"


def test_the_scanner_sees_the_marker_this_file_documents():
    """守衛自己要驗得動:`slow` 真的被掃到,而且真的登記了。"""
    used = _used()
    assert "slow" in used, "掃不到 slow —— AST 條件壞了(而它就在 hermetic 那個檔裡)"
    assert "slow" in _registered()
    # 內建標記不該被要求登記(否則守衛會製造一堆誤報,而誤報的下場是被關掉)
    assert "parametrize" in _BUILTIN and "parametrize" not in _registered()


def test_strict_markers_is_actually_on():
    """沒有 `--strict-markers` 的話,上面幾條就只是在守一個不生效的規則。"""
    cp = configparser.ConfigParser()
    cp.read(_ROOT / "pytest.ini", encoding="utf-8")
    assert "--strict-markers" in cp.get("pytest", "addopts", fallback="")
    # 而且 pytest 真的認得這個設定(不是我以為它認得)
    assert pytest.__version__, "pytest 版本讀不到?"
