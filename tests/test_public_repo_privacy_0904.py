# -*- coding: utf-8 -*-
"""公開 repo 的隱私邊界:**不得把「實際持股」寫進版控**(外審 2026-09-04 P1)。

外審指出兩件在公開 repo 裡互相補完的事:

  * `.gitignore` 的註解**逐一列出**實際持股代號 —— 忽略規則本身變成揭露,
    而且是永久留在 git history 的那一種;
  * `tests/test_portfolio.py` 的 fixture 是「同一批代號 + 看起來很像真的股數」,
    連換算過程都寫在註解裡。

單獨看每一項都能辯稱是範例;拼起來就是一份可推論的持股明細。程式端的防線
(`portfolio_summary` 只出百分比、`calc_portfolio_actual` 回傳無代號)一直都在,
缺的是**版控內容**這一層。這個檔把那一層機械化:數量 fixture 一律合成代號,
`.gitignore` 不點名。

**這個檔自己也守同一條規則**(Codex r1 P1:第一版把剛移除的代號與股數又寫回
說明與探針裡 —— 修正比缺陷更糟的典型)。所有例子一律用不存在的合成代號,
不描述真實數字。

`_parse_portfolio` 的格式測試不在此列 —— 它測的是字串解析,數量是個位數的
示範值,推論不出任何東西。
"""
import ast
import re
from pathlib import Path

import morning_report as mr

_ROOT = Path(mr.__file__).resolve().parent
_TESTS = _ROOT / "tests"
#: 合成代號的約定:公開 repo 裡的股數 fixture 一律以此開頭。
SYNTHETIC_PREFIX = "TEST"


#: 會開新作用域的節點 —— 收集賦值時**不得跨越**它們。
_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _assigns(scope) -> list:
    """該 scope **自己**的 `NAME = <expr>` / `A, B = "x", "y"` → `[(行號, 名稱, 值)]`。

    **不進巢狀作用域**(Codex r3 P2):`ast.walk` 會鑽進內層函式,而內層同名的
    賦值行號通常比外層晚 —— 於是「取呼叫行之前最後一次賦值」會取到內層那一個,
    外層的真代號就拿內層的合成 dict 去驗而過關。作用域的邊界要在收集時就守住,
    不是在挑選時。
    """
    out = []

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _NESTED_SCOPES):
                continue                      # 內層自己管自己
            if isinstance(child, ast.Assign):
                for tgt in child.targets:
                    if isinstance(tgt, ast.Name):
                        out.append((child.lineno, tgt.id, child.value))
                    elif isinstance(tgt, ast.Tuple) and isinstance(child.value, ast.Tuple):
                        for nm, val in zip(tgt.elts, child.value.elts):
                            if isinstance(nm, ast.Name):
                                out.append((child.lineno, nm.id, val))
            visit(child)

    visit(scope)
    return out


def _module_assigns(tree) -> list:
    """只有**模組層**的賦值(`_assigns` 已經不跨越函式,所以直接用它)。"""
    return _assigns(tree)


def _scan_source(filename: str, source: str) -> list:
    """`calc_portfolio_actual(…)` 第一個引數裡的代號 → `[(檔, 行, 代號)]`。

    **名稱解析要分作用域**(Codex r2 P2):第一版用 `ast.walk` 收整個檔、只用變數
    名當鍵 —— 兩個函式都寫 `portfolio = {…}` 時後者會蓋掉前者,於是一個真代號的
    fixture 可以拿另一個函式的合成 dict 去驗而過關。現在先找呼叫所在的函式,
    在**該函式內、呼叫行之前**找最後一次賦值,找不到才退回模組層。
    **解不開的一律回報 `<解不出>` 而不是跳過** —— 掃描器看不懂 ≠ 安全。
    """
    tree = ast.parse(source)
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    mod = _module_assigns(tree)
    cache: dict = {}

    def scope_of(node):
        while node is not None:
            node = parents.get(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node
        return None

    def resolve(name: str, node):
        scope = scope_of(node)
        if scope is not None:
            local = cache.setdefault(id(scope), _assigns(scope))
            hit = [v for (ln, n, v) in local if n == name and ln < node.lineno]
            if hit:
                return hit[-1]
        hit = [v for (ln, n, v) in mod if n == name and ln < node.lineno]
        return hit[-1] if hit else None

    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args):
            continue
        f = node.func
        fname = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if fname != "calc_portfolio_actual":
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Name):
            arg = resolve(arg.id, node) or arg          # 區域變數 → 它綁的 dict
        if not isinstance(arg, ast.Dict):
            out.append((filename, node.lineno, f"<解不出:{ast.unparse(node.args[0])}>"))
            continue
        for k in arg.keys:
            if isinstance(k, ast.Name):
                k = resolve(k.id, node) or k
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                out.append((filename, node.lineno, k.value))
            else:
                out.append((filename, node.lineno, f"<解不出:{ast.unparse(k)}>"))
    return out


def _quantity_fixtures() -> list:
    return [row for path in sorted(_TESTS.glob("test_*.py"))
            for row in _scan_source(path.name, path.read_text(encoding="utf-8"))]


def _ticker_shaped(text: str) -> list:
    """文字裡「長得像台股代號」的 token(4–6 位數字,可帶一個字尾字母)。

    先剝掉**語法上認得出來**的日期與年份;剝不掉的一律回報 —— 不設數值豁免區,
    因為那個區間裡有真的代號(`2002` 是中鋼,repo 自己的 registry 就寫著)。
    要在註解裡寫年份就寫成 `2026-09-04` 或 `2026 年`,不要裸寫四位數。
    """
    # **邊界要用 ASCII 類別,不能用 `\w`**(Codex r2 P2):Python 的 `\w` 是
    # Unicode-aware,`股` 也算 word character —— `# 持股2002` 的 lookbehind 會失敗,
    # 代號就這樣繞過偵測,而中文緊貼代號正是這個 repo 註解最自然的寫法。
    t = re.sub(r"\d{4}-\d{2}-\d{2}", "", text)          # ISO 日期
    t = re.sub(r"\d{4}\s*年", "", t)                      # 2026 年
    t = re.sub(r"(?<![A-Za-z0-9_.])(19|20)\d{2}(?=\s*[-/～~至])", "", t)   # 年份區間
    return re.findall(r"(?<![A-Za-z0-9_.])(\d{4,6}[A-Z]?)(?![A-Za-z0-9_.])", t)


def test_share_quantity_fixtures_use_synthetic_tickers():
    found = _quantity_fixtures()
    assert len(found) >= 5, f"掃不到股數 fixture 就是判準壞了:{found}"
    bad = [f for f in found if not str(f[2]).startswith(SYNTHETIC_PREFIX)]
    assert not bad, ("公開 repo 的股數 fixture 要用合成代號(TEST…)—— "
                     f"真代號配上股數就是可推論的持股明細:{bad}")


def test_the_ignore_rules_do_not_name_holdings():
    """`.gitignore` 說得出「這裡面有持股」,但不可以說出**是哪幾檔**。"""
    text = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    comments = "\n".join(ln for ln in text.splitlines() if ln.lstrip().startswith("#"))
    hits = _ticker_shaped(comments)
    assert not hits, f".gitignore 的註解點名了代號 —— 忽略規則本身變成揭露:{hits}"
    assert "持股" in comments, "反過來也不對:要說得出這裡有持股,只是不點名"
    # **判準不可空轉,而且不可以有豁免區**(Codex r1 P2):第一版把 1900–2100 的
    # 四位數整段排除當「年份」,而那個區間裡有真的台股代號(repo 自己的
    # `instrument_registry` 就有 2002)。現在只剝**語法上認得出來的日期/年份**,
    # 剩下的一律回報。三種探針都要抓得到:五位、帶字尾字母、以及落在舊豁免區的。
    # 五位、帶字尾字母、落在舊「年份豁免區」的、以及**中文緊貼代號**的都要抓得到
    for probe in ("# 例:00000", "# 例:00000X", "# 例:2002",
                  "# 持股2002", "# 2002持股"):
        assert _ticker_shaped(comments + "\n" + probe), probe


def test_the_scanner_sees_the_local_variable_fixture():
    """Codex r1 P2:主要 fixture 是 `portfolio = {…}` 再傳變數,而第一版掃描器
    看到 `ast.Name` 就靜默跳過 —— 其他直接字面的呼叫讓「非空」門檻照樣滿足,
    守衛綠著而真代號躺在公開 repo 裡。這條釘住那個形狀本身。"""
    src = (_TESTS / "test_portfolio.py").read_text(encoding="utf-8")
    assert "portfolio = {" in src, "fixture 不再用區域變數?那這條要重寫"
    line = next(i + 1 for i, ln in enumerate(src.splitlines())
                if "calc_portfolio_actual(portfolio, closes)" in ln)
    found = _scan_source("test_portfolio.py", src)
    at_call = [f for f in found if f[1] == line]
    assert {f[2] for f in at_call} == {"TESTETF", "TESTLEV"}, (line, at_call)
    assert not [f for f in _quantity_fixtures() if str(f[2]).startswith("<解不出")]

    # **同名區域變數不得互相覆蓋**(Codex r2 P2):第一版用整檔一張表、只以變數名
    # 當鍵,於是下面這種寫法會拿 b() 的合成 dict 去驗 a() 的真代號而過關。
    dup = (
        'def a():\n'
        '    portfolio = {"9999X": 1}\n'
        '    return calc_portfolio_actual(portfolio, {})\n'
        '\n'
        'def b():\n'
        '    portfolio = {"TESTOK": 1}\n'
        '    return calc_portfolio_actual(portfolio, {})\n')
    rows = _scan_source("dup.py", dup)
    assert sorted(r[2] for r in rows) == ["9999X", "TESTOK"], rows

    # **巢狀作用域不得污染外層**(Codex r3 P2):內層同名賦值的行號比外層晚,
    # 「呼叫行之前最後一次賦值」會取到它 —— 外層的真代號就被內層的合成 dict 蓋過。
    nested = (
        'def outer():\n'
        '    portfolio = {"9999X": 1}\n'
        '    def helper():\n'
        '        portfolio = {"TESTOK": 1}\n'
        '        return portfolio\n'
        '    return calc_portfolio_actual(portfolio, {})\n')
    assert [r[2] for r in _scan_source("nested.py", nested)] == ["9999X"], \
        _scan_source("nested.py", nested)
    # 解不出來的要說出來,不是跳過
    assert _scan_source("d.py", "calc_portfolio_actual(build(), {})")[0][2].startswith("<解不出")


def test_the_lock_refresh_hands_off_before_running_new_dependencies():
    """供應鏈(Codex r1 P2 第二輪):被投毒的依賴不得改到會被 commit 的那份 lock。

    結構性的三件事 —— 上傳在**執行新依賴之前**、跑新依賴的 job **沒有**
    `actions` 權限(動不到已上傳的 artifact)、開 PR 的 job 要 needs 到 verify。
    這條證明的是接線,不是不可竄改性(那由 GitHub 的 artifact 不可覆寫保證)。
    """
    import yaml
    wf = yaml.safe_load((_ROOT / ".github/workflows/lock-refresh.yml")
                        .read_text(encoding="utf-8"))
    jobs = wf["jobs"]
    assert set(jobs) == {"resolve", "verify", "open-pr"}, list(jobs)
    steps = jobs["resolve"]["steps"]
    names = [s.get("name") or (s.get("uses") or "").split("@")[0] for s in steps]
    up = next(i for i, n in enumerate(names) if "Upload" in n)
    recompile = next(i for i, n in enumerate(names) if "Recompile" in n)
    # **「新解析出來的」才危險**:安裝 uv 用的是 repo 裡既有(已 review、已 hash)
    # 的 uvtool lock,而且發生在 Recompile **之前** —— 那不是新依賴。
    # 真正的不變量是:Recompile 之後、Upload 之前,不得執行任何安裝或測試。
    assert recompile < up, names
    between = yaml.dump(steps[recompile + 1:up], allow_unicode=True)
    for forbidden in ("pip install", "pytest", "venv"):
        assert forbidden not in between, (forbidden, names[recompile + 1:up])
    for i, s in enumerate(steps[:recompile]):
        if "pip install" in (s.get("run") or ""):
            assert "requirements-uvtool.lock" in s["run"], (i, s.get("name"))
    assert jobs["verify"]["permissions"] == {"contents": "read"}
    assert "actions" not in jobs["verify"]["permissions"], "跑新依賴的 job 不得能動 artifact"
    assert not [s for s in jobs["verify"]["steps"] if "upload-artifact" in (s.get("uses") or "")]
    assert set(jobs["open-pr"]["needs"]) == {"resolve", "verify"}
    assert jobs["open-pr"]["permissions"]["contents"] == "write"


def test_the_code_side_privacy_guards_are_still_there():
    """這一批只補版控內容;程式端原本的防線不得被順手改掉。"""
    import evidence_packet as ep
    out = mr.calc_portfolio_actual({"TESTCO": 5000}, {"TESTCO": (100.0, 101.0)})
    assert "TESTCO" not in str(out) and "5000" not in str(out.get("n_holdings", ""))
    assert set(out) <= {"gain_pct", "gain_amount", "prev_value", "last_value",
                        "n_holdings", "n_priced"}
    # packet 進 prompt 也進公開 repo 的 state:只有百分比,沒有代號、沒有股數
    summary = ep.portfolio_summary({"PORTFOLIO_ACTUAL": {
        "p1": {"gain_pct": 1.4, "n_holdings": 3, "n_priced": 3,
               # 就算上游順手把明細與金額塞進來,投影也不得帶出去
               "gain_amount": 103470.8, "prev_value": 4837080,
               "codes": ["TESTCO"], "shares": {"TESTCO": 5000}}}})
    assert summary["available"] is True and set(summary) == {"available", "slots"}
    assert set(summary["slots"]["p1"]) == {"change_pct", "holdings", "priced"}
    blob = str(summary)
    for leaked in ("TESTCO", "5000", "103470", "4837080"):
        assert leaked not in blob, (leaked, summary)
