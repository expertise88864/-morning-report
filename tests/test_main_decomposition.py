# -*- coding: utf-8 -*-
"""**main() 相位拆解的契約**(第十一輪 P2-3)。

## 為什麼需要這個檔

拆 `main()` 的時候我先查了一件事:**整個測試套件沒有任何一條會執行 `main()`**
(`grep` 只找得到 `podcast_digest.main()` 與註解裡提到 main 的字樣)。
也就是說這個一千多行的函式,以及把它拆開的每一步,都沒有行為層的驗證網。

`ruff` 會抓到未定義名稱(F821),所以「相位沒寫回去、呼叫端仍在用」會被擋下。
抓不到的是**屬性名對不上**:相位寫 `ctx.macro`、呼叫端讀 `ctx.MACRO` ——
`AttributeError` 只會在生產那一班的凌晨六點出現,而那時的症狀是整封信沒寄出。
(`AppContext.__slots__` 會讓**寫**錯字當場爆掉,但讀錯字仍要靠這裡。)

所以用 AST 把「context 有哪些欄位」與「相位/main 用了哪些」對起來。這條會隨著
P2-3 逐相位進行一直有效,不需要每拆一個就手動加一條測試。

刻意**不**改成「跑一次 main()」:那需要把數十個對外抓取全部樁掉,而樁本身
會變成另一份會漂移的規格。這裡驗的是拆解引入的那一類錯誤,不是晨報的行為。
"""
import ast
from pathlib import Path

import pytest

import app_context

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "morning_report.py"


def _tree() -> ast.Module:
    if not _SRC.exists():
        pytest.fail(f"找不到 {_SRC} —— 契約測試不得因檔案不見而跳過")
    return ast.parse(_SRC.read_text(encoding="utf-8"))


def _phase_functions(tree: ast.Module) -> list:
    return [n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name.startswith("_phase_")]


def _main(tree: ast.Module) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    pytest.fail("morning_report.py 沒有頂層 main()")


def _ctx_attrs(node) -> tuple:
    """`ctx.<attr>` 的 (被寫的, 被讀的)。"""
    stored, loaded = set(), set()
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                and sub.value.id == "ctx"):
            (stored if isinstance(sub.ctx, ast.Store) else loaded).add(sub.attr)
    return stored, loaded


def test_there_is_at_least_one_phase_function():
    """空集合不算通過。

    P2-3 若被回退,這條要紅著提醒「下面那些檢查已經沒有對象了」,
    而不是安靜地全部通過。
    """
    assert _phase_functions(_tree()), (
        "morning_report.py 沒有任何 `_phase_*` 函式 —— "
        "若 P2-3 的相位拆解被回退,請連同本檔一起移除,不要留著空轉")


def test_nobody_uses_a_context_field_that_does_not_exist():
    """相位與 main 用到的每個 `ctx.X`,都必須是 `AppContext` 的欄位。

    寫錯字有 `__slots__` 當場擋;**讀**錯字沒有人擋 —— 它會在生產那一班
    變成 AttributeError,而那時的症狀是整封信沒寄出。
    """
    tree = _tree()
    slots = set(app_context.AppContext.__slots__)
    assert slots, "AppContext 沒有任何欄位 —— 這條測試會因此空集合真空通過"
    for node in _phase_functions(tree) + [_main(tree)]:
        stored, loaded = _ctx_attrs(node)
        unknown = sorted((stored | loaded) - slots - {"mark_phase"})
        assert not unknown, (
            f"{node.name} 用了 AppContext 沒有的欄位:{unknown}")


def test_every_context_field_is_both_written_and_read():
    """context 不得變成一袋雜物:每個欄位都要有人寫、有人讀。

    只有寫沒有讀 = 拆解留下的殘骸,下一個人會以為它有用途;
    只有讀沒有寫 = 那個值永遠是 `None`,而 `None` 會安靜地一路傳下去。
    """
    tree = _tree()
    nodes = _phase_functions(tree) + [_main(tree)]
    stored, loaded = set(), set()
    for n in nodes:
        st, ld = _ctx_attrs(n)
        stored |= st
        loaded |= ld
    slots = set(app_context.AppContext.__slots__)
    # `recorder` 由建構子填,不會在 morning_report 裡被指派。
    never_written = sorted(slots - stored - {"recorder"})
    never_read = sorted(slots - loaded)
    assert not never_written, (
        f"這些欄位沒有任何相位寫入,永遠是 None:{never_written}")
    assert not never_read, (
        f"這些欄位沒有任何人讀,是拆解留下的殘骸:{never_read}")


def test_phase_functions_take_the_context_as_a_parameter():
    """相位必須把 `ctx` 收在簽章上,而不是抓記錄器的模組全域。

    P2-3 的重點就是這一刀:相依看得見,才換得掉。相位若直接用 `_RECORDER`
    或 `_mark_phase`,拆出函式只是搬位置,注入從來沒有發生。
    """
    tree = _tree()
    for node in _phase_functions(tree):
        params = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
        assert "ctx" in params, f"{node.name} 沒有把 ctx 收在簽章上"
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        leaked = names & {"_RECORDER", "_mark_phase"}
        assert not leaked, (
            f"{node.name} 直接碰了模組全域 {sorted(leaked)} —— "
            "請改用 `ctx`,否則注入只是名義上的")


#: 相位裡**還在**直接寫 `_RUN_MANIFEST` 的次數。只能降不能升。
#:
#: 這是 P1-12 沒清完的債:manifest 的**擁有權**已經進 `ManifestRecorder`,
#: 但主流程裡仍有一批「直接往 dict 塞一個鍵」的寫法。P2-3 不順手改掉它們,
#: 理由是相位本體要**逐字不動** —— 那是這批唯一的行為等價證據
#: (`main()` 沒有任何測試會執行到)。
#:
#: 這裡不假裝債務已清,改成擋住它變多:新增的相位邏輯要走 `ctx.recorder`。
#:
#: **這個數字是量出來的,不是估的。** 我第一版憑印象寫 14、實測是 11 ——
#: 突變測試(在相位裡多塞一個 `_RUN_MANIFEST` 寫入)因此**沒有轉紅**,
#: 那條棘輪等於預留了三格空間。門檻要量,不要推理。
PHASE_RUN_MANIFEST_WRITES = 11


def test_direct_run_manifest_writes_in_phases_do_not_grow():
    """相位裡直接碰 `_RUN_MANIFEST` 的次數是**棘輪**,只能降。

    寫成「一個都不准」會逼我在同一批裡改動相位本體,而本體逐字不動正是
    這批行為等價的唯一證據。寫成「不檢查」則是假裝債務不存在 ——
    下一批就會再長出十個。
    """
    tree = _tree()
    hits = [n for fn in _phase_functions(tree) for n in ast.walk(fn)
            if isinstance(n, ast.Name) and n.id == "_RUN_MANIFEST"]
    assert len(hits) <= PHASE_RUN_MANIFEST_WRITES, (
        f"相位裡直接寫 _RUN_MANIFEST 的次數升到 {len(hits)}(上限 "
        f"{PHASE_RUN_MANIFEST_WRITES})—— 新的相位邏輯請走 `ctx.recorder`")
    assert len(hits) >= 1, (
        "一個都沒有了 —— 請把 PHASE_RUN_MANIFEST_WRITES 降到 0 並刪掉這條測試,"
        "留著一條永遠不會觸發的上限只是裝飾")


def test_main_builds_the_context_once_and_passes_it_down():
    """`ctx` 由 main() 建構,而且每個相位都是用它呼叫的。

    相位若被呼叫時沒帶 ctx(或帶了別的東西),拆解就退化成「函式化」——
    共用狀態會偷偷跑回模組全域。
    """
    tree = _tree()
    main = _main(tree)
    built = [n for n in ast.walk(main)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "AppContext"]
    assert len(built) == 1, f"main() 建了 {len(built)} 個 AppContext,應該剛好一個"

    phase_names = {n.name for n in _phase_functions(tree)}
    called = {}
    for n in ast.walk(main):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id in phase_names):
            called[n.func.id] = n
    missing = sorted(phase_names - set(called))
    assert not missing, f"這些相位沒有被 main() 呼叫:{missing}"
    for name, call in called.items():
        args = [a.id for a in call.args if isinstance(a, ast.Name)]
        assert args == ["ctx"], (
            f"{name} 不是以 `{name}(ctx)` 呼叫的:{ast.unparse(call)[:70]}")
