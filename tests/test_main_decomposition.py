# -*- coding: utf-8 -*-
"""**main() 相位拆解的契約**(第十一輪 P2-3)。

## 為什麼需要這個檔

拆 `main()` 的時候我先查了一件事:**整個測試套件沒有任何一條會執行 `main()`**
(`grep` 只找得到 `podcast_digest.main()` 與註解裡提到 main 的字樣)。
也就是說這個 1,275 行的函式,以及把它拆開的每一步,都沒有行為層的驗證網。

`ruff` 會抓到未定義名稱(F821),所以「相位沒回傳、呼叫端仍在用」會被擋下。
抓不到的是**鍵名對不上**:相位回傳 `{"macro": …}`、呼叫端寫 `_p1["MACRO"]` ——
`KeyError` 只會在生產那一班的凌晨六點出現,而那時的症狀是整封信沒寄出。

所以用 AST 把「相位回傳什麼」與「呼叫端讀什麼」對起來。這條會隨著 P2-3
逐相位進行一直有效,不需要每拆一個就手動加一條測試。

刻意**不**改成「跑一次 main()」:那需要把數十個對外抓取全部樁掉,而樁本身
會變成另一份會漂移的規格。這裡驗的是拆解引入的那一類錯誤,不是晨報的行為。
"""
import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "morning_report.py"


def _tree() -> ast.Module:
    if not _SRC.exists():
        pytest.fail(f"找不到 {_SRC} —— 契約測試不得因檔案不見而跳過")
    return ast.parse(_SRC.read_text(encoding="utf-8"))


def _phase_functions(tree: ast.Module) -> dict:
    """`_phase_*` 頂層函式 → 它回傳的 dict 鍵集合。

    只認**字面量 dict** 的 return。相位若改成回傳算出來的 dict,這裡會回
    `None`,而下面的測試會明講「無法驗證」而不是預設通過 ——
    「解析不出來就算過」正是守衛最常見的失效方式。
    """
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("_phase_"):
            continue
        keys = set()
        literal = False
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                literal = True
                for k in sub.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
                    else:
                        literal = False
        out[node.name] = keys if literal else None
    return out


def _main(tree: ast.Module) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    pytest.fail("morning_report.py 沒有頂層 main()")


def _phase_bindings(main: ast.FunctionDef, phases: dict) -> dict:
    """main() 裡 `x = _phase_y(...)` 的 x → y。"""
    out = {}
    for node in ast.walk(main):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in phases):
            out[node.targets[0].id] = node.value.func.id
    return out


def test_there_is_at_least_one_phase_function():
    """空集合不算通過。

    P2-3 若被回退,這條要紅著提醒「下面那些檢查已經沒有對象了」,
    而不是安靜地全部通過。
    """
    phases = _phase_functions(_tree())
    assert phases, (
        "morning_report.py 沒有任何 `_phase_*` 函式 —— "
        "若 P2-3 的相位拆解被回退,請連同本檔一起移除,不要留著空轉")


def test_main_only_reads_keys_the_phase_actually_returns():
    """呼叫端讀的每一個鍵,相位都必須真的回傳。

    這是拆解唯一會靜默的失敗:`ruff` 看得到未定義的**名字**,
    看不到對不上的**字串鍵**,而症狀是生產那一班 KeyError → 信沒寄出。
    """
    tree = _tree()
    phases = _phase_functions(tree)
    main = _main(tree)
    bindings = _phase_bindings(main, phases)
    assert bindings, "main() 沒有呼叫任何相位函式 —— 相位拆完就該由 main() 呼叫"

    for var, fname in bindings.items():
        returned = phases[fname]
        assert returned is not None, (
            f"{fname} 的 return 不是字面量 dict,本契約無法驗證它的鍵 —— "
            "請維持字面量,或改寫這條測試(不要讓它預設通過)")
        read = {n.slice.value for n in ast.walk(main)
                if isinstance(n, ast.Subscript)
                and isinstance(n.value, ast.Name) and n.value.id == var
                and isinstance(n.slice, ast.Constant)
                and isinstance(n.slice.value, str)}
        assert read, f"main() 拿到 {fname} 的結果卻沒有讀任何鍵({var})"
        unknown = sorted(read - returned)
        assert not unknown, (
            f"main() 讀了 {fname} 沒有回傳的鍵:{unknown}(它回傳 {sorted(returned)})")
        unused = sorted(returned - read)
        assert not unused, (
            f"{fname} 回傳了沒有人讀的鍵:{unused} —— 沒人讀的回傳值會讓下一個"
            "改這裡的人以為它有用途")


def test_phase_functions_take_the_recorder_as_a_parameter():
    """相位必須把 `recorder` 收在簽章上,而不是抓模組全域。

    P2-3 的重點就是這一刀:相依看得見,才換得掉。相位若直接用 `_RECORDER`
    或 `_mark_phase`,拆出函式只是搬位置,注入從來沒有發生。
    """
    tree = _tree()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("_phase_"):
            continue
        params = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
        assert "recorder" in params, f"{node.name} 沒有把 recorder 收在簽章上"
        globals_used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        leaked = globals_used & {"_RECORDER", "_RUN_MANIFEST", "_mark_phase"}
        assert not leaked, (
            f"{node.name} 直接碰了模組全域 {sorted(leaked)} —— "
            "請改用參數 `recorder`,否則注入只是名義上的")
