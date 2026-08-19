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

大部分的檢查刻意停在 AST:要真的跑完 `main()` 得把數十個對外抓取全部樁掉,
而樁本身會變成另一份會漂移的規格。

**但 AST 不夠。** r1 外審抓到一個純靜態檢查看不見的缺陷:`_phase_render` 在
`DRY_RUN=1` 時 `return 0` —— 那行在 `main()` 裡是「結束執行」,搬進相位之後
只結束那個相位。`return` 的語義取決於它在哪個函式裡,而**逐字相同的 diff
正好看不出這件事**(那是我這批驗證方法自己的盲點)。所以另有一條
`test_an_early_return_from_any_phase_ends_the_run`:用假相位真的跑一次
`main()`,驗控制流本身。那是整個套件第一條會執行 `main()` 的測試。
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
PHASE_RUN_MANIFEST_WRITES = 12  # 2026-08-19 P1-1:llm.config_invalid(fatal 設定的消費端)


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
    """`ctx` 由 main() 建構一次,而且 `_PIPELINE` 涵蓋每一個相位、順序與原始碼一致。

    `_PIPELINE` 漏掉一個相位,那段邏輯就整段不執行 —— 而那不會是錯誤,
    只會是「今天的信少了一塊」。順序也要盯:相位之間有資料相依。
    """
    import morning_report as mr

    tree = _tree()
    main = _main(tree)
    built = [n for n in ast.walk(main)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "AppContext"]
    assert len(built) == 1, f"main() 建了 {len(built)} 個 AppContext,應該剛好一個"

    in_source = [n.name for n in _phase_functions(tree)]
    in_pipeline = [f.__name__ for f in mr._PIPELINE]
    assert in_pipeline == in_source, (
        "_PIPELINE 與原始碼裡的相位不一致(或順序不同):"
        f" _PIPELINE={in_pipeline} 原始碼={in_source}")


def test_an_early_return_from_any_phase_ends_the_run(monkeypatch):
    """**相位早退必須結束整個執行。**(r1 Codex #1)

    這是本檔第一條真的會執行 `main()` 的測試,而它存在的理由是一個我自己
    造出來的缺陷:`_phase_render` 在 `DRY_RUN=1` 時 `return 0`。那行在
    `main()` 裡的意思是「結束執行」,搬進相位之後只結束那個相位 ——
    main() 會繼續往下寄信,而 `ctx.html` 還是 `None`。

    **逐字相同的 diff 看不出這件事**:`return` 的語義取決於它在哪個函式裡。
    所以這條不驗文字,驗行為 —— 用假相位跑一次真的 `main()`。
    """
    import datetime as _real_dt

    import morning_report as mr

    class _FixedDT:
        """固定在 2026-08-05(週三)—— 週日會走 weekend digest 那條路。"""

        class datetime:
            @staticmethod
            def now(tz=None):
                return _real_dt.datetime(2026, 8, 5, 6, 0, tzinfo=tz)

            strptime = staticmethod(_real_dt.datetime.strptime)

    monkeypatch.setattr(mr, "dt", _FixedDT)
    ran = []

    def _phase(name, rc=None):
        def _run(ctx):
            ran.append(name)
            return rc
        _run.__name__ = f"_phase_{name}"
        return _run

    # 中間的相位早退 → 後面的都不該跑,而且退出碼要原樣傳出來
    monkeypatch.setattr(mr, "_PIPELINE",
                        (_phase("a"), _phase("b", 7), _phase("c")))
    assert mr.main() == 7, "相位的早退沒有被傳播 —— main() 會繼續往下寄信"
    assert ran == ["a", "b"], f"早退之後還跑了後面的相位:{ran}"

    # 全部回 None → 走到底,退出碼 0
    ran.clear()
    monkeypatch.setattr(mr, "_PIPELINE", (_phase("a"), _phase("b")))
    assert mr.main() == 0
    assert ran == ["a", "b"]


def test_a_phase_that_can_return_says_so_in_its_signature():
    """會早退的相位必須把回傳型別寫出來。

    宣稱要對得上實作:標成 `-> None` 卻會 `return 0` 的相位,讀的人會以為
    它不可能結束整個執行 —— 而那正是這個缺陷當初被寫出來的原因。
    """
    tree = _tree()
    for node in _phase_functions(tree):
        inner = {id(x) for f in ast.walk(node)
                 if isinstance(f, (ast.FunctionDef, ast.Lambda)) and f is not node
                 for x in ast.walk(f)}
        returns_value = any(isinstance(r, ast.Return) and r.value is not None
                            and id(r) not in inner for r in ast.walk(node))
        ann = ast.unparse(node.returns) if node.returns else ""
        if returns_value:
            assert ann != "None", (
                f"{node.name} 會 return 一個值,簽章卻寫 `-> None`")
        else:
            assert ann == "None", (
                f"{node.name} 不會 return 值,簽章卻寫 `-> {ann}`(宣稱要對得上實作)")


#: `main()` 的行數上限。**棘輪,只能降不能升。**
#: P2-3 拆解前是 641 行(2026-07-04 的盤點),批#120–122 拆完後實測 31 行。
#: 主模組整體的上限(`MAIN_MODULE_LINE_CEILING`)擋不住這件事 ——
#: main() 可以一路長回 641 行而整個檔仍在上限之下,而「拆解還在不在」
#: 正是上面那些相位守衛的前提。
#: 2026-08-08 校正待辦清單時發現「main() 31 行」這個宣稱沒有東西撐著。
MAIN_FUNCTION_LINE_CEILING = 60


def test_main_stays_a_thin_orchestrator():
    """**拆解要擋得住回流。** 相位函式存在不代表 main() 還是薄的 ——
    有人把邏輯寫回 main() 時,上面每一條相位檢查照樣全綠。"""
    src = _SRC.read_text(encoding="utf-8")
    i = src.index("\ndef main(")
    # `main()` 是檔案最後一個頂層 def(後面只剩 `if __name__ ==`),
    # 所以「下一個 def」可能不存在 —— 找不到就量到檔尾。
    j = src.find("\ndef ", i + 10)
    n = src[i:(j if j > 0 else len(src))].count("\n")
    assert n <= MAIN_FUNCTION_LINE_CEILING, (
        f"main() 已達 {n} 行,超過上限 {MAIN_FUNCTION_LINE_CEILING}。\n"
        "這是棘輪:請把邏輯搬進相位函式,而不是調高數字 —— "
        "P2-3 拆解前它是 641 行,而主模組整體的上限擋不住它長回去。")
