#!/usr/bin/env python3
"""refactor_audit.py — A5 絞殺者模組化的機械化檢核工具(純 stdlib)。

給後續(較小的)模型用:本工具把「這個函式能不能安全搬出 morning_report.py」
變成可執行的檢查,取代人腦判讀 15k 行。**工具只給證據,不給決定**;
判 BLOCK 的絕不搬,判 OK 仍須跑全套 pytest + Codex 閘門。

子命令(對應 A5_MODULARIZATION_MAP.md §A 施工程序):
  python tools/refactor_audit.py nomove
      掃 tests/ 的 monkeypatch 目標(同時抓 `mr.name` 屬性式與
      `setattr(mr, "name", ...)` 字串式——計劃書 §0.10 的舊 grep 只抓得到前者,
      會漏掉九成,勿再用)。輸出「不可搬清單」,貼進 commit message 當證據。
  python tools/refactor_audit.py list
      列出 morning_report.py 所有頂層函式的依賴分類與可搬性判定,
      並輸出「純函式閉包」的建議群組(連通元件,依行數排序)。
  python tools/refactor_audit.py check FUNC [FUNC ...]
      單一函式的外部參照明細(它到底碰了 mr 的什麼)。
  python tools/refactor_audit.py group FUNC [FUNC ...]
      驗證一組函式(含其常數)可一起搬:閉包封閉 + monkeypatch 陷阱檢查。
      必須 ALL-CLEAR 才動工。
  python tools/refactor_audit.py verify-move MODULE FUNC [FUNC ...]
      搬完後驗證:新模組內每個函式本體與 git HEAD 的 morning_report.py 正規化文本相同
      (忽略行尾 CRLF/LF 與尾隨空白;非嚴格 byte-identity)。

判定規則(寫死在程式裡,想改先問使用者):
  BLOCK — 參照網路/IO 名稱(requests、_http_get、yf、feedparser、open、os、Path…)
          或 mr 的可變模組狀態(空容器初始化的全域、會被突變的全域)。
          → 這類函式留在 morning_report(新模組不能 import mr,會循環)。
  NEEDS — 參照 mr 其他函式/常數 → 只能「整群一起搬」且群組必須封閉。
  TRAP  — 群內成員 A 被 tests monkeypatch,且群內成員 B 會呼叫 A:
          搬走後 B 對 A 的呼叫走新模組命名空間,patch mr.A 攔不到 → 測試失真。
          → 把 B 留在 mr,或整群放棄。
  OK    — 只用 stdlib/本地葉模組(llm_postprocess、render_utils 等不 import mr 的模組)
          + 自帶常數 → 可搬。
"""
import ast
import builtins
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MR_PATH = ROOT / "morning_report.py"

try:  # Windows 主控台 cp950 防護
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BUILTINS = set(dir(builtins)) | {"__name__", "__file__", "__doc__"}

# 網路/IO/程序類名稱:參照到任何一個 → BLOCK(留在 morning_report)
NET_IO = {
    "requests", "_http_get", "yf", "yfinance", "feedparser", "smtplib",
    "urllib", "socket", "subprocess", "open", "os", "Path", "shutil",
    "ssl", "http", "email", "mimetypes", "tempfile", "webbrowser",
}
# 本地葉模組(不 import morning_report,新模組可以安全 import 它們)
LOCAL_LEAF = {
    p.stem for p in ROOT.glob("*.py")
    if p.stem not in {"morning_report", "gooaye_radar"}
}


def _read_mr(src: str | None = None):
    return src if src is not None else MR_PATH.read_text(encoding="utf-8")


def _module_info(src: str):
    """回傳 (imports, funcs, consts, states):模組層名稱的四分類。"""
    tree = ast.parse(src)
    imports, funcs, consts, states = {}, {}, {}, set()

    def _is_const_expr(node) -> bool:
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return all(_is_const_expr(e) for e in node.elts)
        if isinstance(node, ast.Dict):
            return all(_is_const_expr(k) and _is_const_expr(v)
                       for k, v in zip(node.keys, node.values) if k is not None)
        if isinstance(node, ast.Call):  # re.compile("...") / frozenset({...})
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
               and f.value.id == "re" and f.attr == "compile":
                return True
            if isinstance(f, ast.Name) and f.id == "frozenset":
                return True
        if isinstance(node, (ast.BinOp, ast.UnaryOp)):  # "a" + "b"、-1
            return all(_is_const_expr(v) for v in ast.iter_child_nodes(node)
                       if isinstance(v, ast.expr))
        return False

    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                imports[(a.asname or a.name).split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for a in node.names:
                imports[a.asname or a.name] = f"{mod}.{a.name}"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            for t in targets:
                if not isinstance(t, ast.Name):
                    continue
                empty = isinstance(value, (ast.Dict, ast.List, ast.Set)) and not getattr(
                    value, "keys", getattr(value, "elts", [1]))
                if value is not None and _is_const_expr(value) and not empty:
                    consts[t.id] = value
                else:  # 空容器 = 可變狀態;算式/呼叫 = 環境相依,一律當狀態
                    states.add(t.id)

    # 常數若在模組任何地方被突變 → 改列狀態
    mutated = set()
    for name in list(consts):
        pat = rf"\b{re.escape(name)}\s*(\.(update|append|add|pop|clear|extend|setdefault|remove)\b|\[[^\]]+\]\s*=)"
        if re.search(pat, src):
            mutated.add(name)
    for name in mutated:
        states.add(name)
        consts.pop(name, None)
    return imports, funcs, consts, states


def _global_refs(src: str, func_names=None):
    """用 symtable 精確取出每個頂層函式參照到的模組層名稱(含巢狀 scope)。"""
    import symtable
    table = symtable.symtable(src, "morning_report.py", "exec")

    def collect(t):
        refs = set()
        for sym in t.get_symbols():
            if sym.is_global():
                refs.add(sym.get_name())
        for child in t.get_children():
            refs |= collect(child)
        return refs

    out = {}
    for child in table.get_children():
        if child.get_type() == "function" and (func_names is None or child.get_name() in func_names):
            out.setdefault(child.get_name(), set()).update(collect(child))
    return out


def _classify(name, imports, funcs, consts, states):
    if name in BUILTINS and name not in imports and name not in funcs:
        return "builtin", None
    if name in NET_IO:
        return "net_io", None
    if name in imports:
        top = imports[name].split(".")[0]
        if top in NET_IO:
            return "net_io", imports[name]
        if top in LOCAL_LEAF:
            return "local_leaf", imports[name]
        return "import", imports[name]
    if name in funcs:
        return "mr_func", None
    if name in consts:
        return "mr_const", None
    if name in states:
        return "mr_state", None
    return "unknown", None


def _analyze(src: str):
    imports, funcs, consts, states = _module_info(src)
    refs = _global_refs(src, set(funcs))
    info = {}
    for fname, node in funcs.items():
        buckets = {"net_io": set(), "import": set(), "local_leaf": set(),
                   "mr_func": set(), "mr_const": set(), "mr_state": set(), "unknown": set()}
        for r in sorted(refs.get(fname, ())):
            if r == fname:
                continue  # 遞迴呼叫自己不算外部依賴
            kind, detail = _classify(r, imports, funcs, consts, states)
            if kind != "builtin":
                buckets[kind].add(r)
        lines = (node.end_lineno or node.lineno) - node.lineno + 1
        blocked = bool(buckets["net_io"] or buckets["mr_state"] or buckets["unknown"])
        info[fname] = {"node": node, "lines": lines, "lineno": node.lineno,
                       "buckets": buckets, "blocked": blocked}
    return info, consts, states


def cmd_nomove():
    """掃 tests/ 產不可搬清單(兩種寫法都抓)。"""
    targets = {}
    for f in sorted((ROOT / "tests").glob("*.py")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if "monkeypatch" not in line:
                continue
            for m in re.finditer(r'setattr\(\s*(mr|gr|pdg|mrpt)\s*,\s*["\']([A-Za-z_][A-Za-z0-9_]*)', line):
                targets.setdefault(f"{m.group(1)}.{m.group(2)}", set()).add(f.name)
            for m in re.finditer(r'setattr\(\s*(mr|gr|pdg|mrpt)\.([A-Za-z_][A-Za-z0-9_.]*)', line):
                targets.setdefault(f"{m.group(1)}.{m.group(2).split('.')[0]}", set()).add(f.name)
            for m in re.finditer(r'setattr\(\s*["\'](morning_report|gooaye_radar|podcast_digest)\.([A-Za-z_][A-Za-z0-9_]*)', line):
                alias = {"morning_report": "mr", "gooaye_radar": "gr", "podcast_digest": "pdg"}[m.group(1)]
                targets.setdefault(f"{alias}.{m.group(2)}", set()).add(f.name)
    print(f"# 不可搬清單(tests/ 的 monkeypatch 目標,共 {len(targets)} 個名稱)")
    print("# 規則:這些名稱必須留在(或 re-export 回)原模組命名空間;")
    print("#       且『被 patch 的函式』不可被同批搬走的函式呼叫(TRAP,見 group 子命令)。")
    for name in sorted(targets):
        print(f"  {name:<45s} <- {', '.join(sorted(targets[name]))}")
    return {n.split(".", 1)[1] for n in targets if n.startswith("mr.")}


def _pure_closure(info):
    """迭代收斂出「可搬閉包」:不 BLOCK,且 mr_func 依賴全部也在閉包內。"""
    pure = {f for f, d in info.items() if not d["blocked"]}
    changed = True
    while changed:
        changed = False
        for f in list(pure):
            if any(dep not in pure for dep in info[f]["buckets"]["mr_func"]):
                pure.discard(f)
                changed = True
    return pure


def cmd_list():
    src = _read_mr()
    info, consts, states = _analyze(src)
    patched = cmd_nomove_silent()
    pure = _pure_closure(info)

    # 連通元件(依 mr_func 呼叫邊)
    parent = {f: f for f in pure}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for f in pure:
        for dep in info[f]["buckets"]["mr_func"]:
            if dep in pure:
                ra, rb = find(f), find(dep)
                if ra != rb:
                    parent[ra] = rb
    clusters = {}
    for f in pure:
        clusters.setdefault(find(f), []).append(f)

    print(f"=== 可搬閉包:{len(pure)} 個函式(全 {len(info)} 個頂層函式)===")
    print("(判 OK ≠ 該搬;是否值得搬、搬去哪個模組,由 A5_MODULARIZATION_MAP.md 的判斷決定)\n")
    for root, members in sorted(clusters.items(), key=lambda kv: -sum(info[m]["lines"] for m in kv[1])):
        total = sum(info[m]["lines"] for m in members)
        if len(members) == 1 and total < 8:
            continue  # 太小的獨行俠不值得列
        print(f"--- 群組(共 {total} 行)---")
        for m in sorted(members, key=lambda x: -info[x]["lines"]):
            b = info[m]["buckets"]
            tags = []
            if m in patched:
                tags.append("PATCHED")
            if b["mr_const"]:
                tags.append("consts:" + ",".join(sorted(b["mr_const"])))
            if b["local_leaf"]:
                tags.append("leaf:" + ",".join(sorted(b["local_leaf"])))
            if b["mr_func"]:
                tags.append("calls:" + ",".join(sorted(b["mr_func"])))
            print(f"  {m:<42s} L{info[m]['lineno']:<6d} {info[m]['lines']:>4d}行  {'  '.join(tags)}")
        print()

    blocked_big = sorted((f for f, d in info.items() if f not in pure),
                         key=lambda f: -info[f]["lines"])[:12]
    print("=== 不可搬大戶(前 12,說明為何留在 morning_report)===")
    for f in blocked_big:
        b = info[f]["buckets"]
        why = []
        if b["net_io"]:
            why.append("net/io:" + ",".join(sorted(b["net_io"])[:4]))
        if b["mr_state"]:
            why.append("state:" + ",".join(sorted(b["mr_state"])[:4]))
        if b["unknown"]:
            why.append("unknown:" + ",".join(sorted(b["unknown"])[:4]))
        deps_out = [d for d in b["mr_func"] if d not in pure]
        if deps_out:
            why.append("依賴不可搬:" + ",".join(sorted(deps_out)[:4]))
        print(f"  {f:<42s} {info[f]['lines']:>4d}行  {'; '.join(why)}")


def cmd_nomove_silent():
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return cmd_nomove()


def cmd_check(names):
    src = _read_mr()
    info, _, _ = _analyze(src)
    for n in names:
        if n not in info:
            print(f"[check] {n}:morning_report.py 沒有這個頂層函式")
            continue
        d = info[n]
        print(f"[check] {n}(L{d['lineno']},{d['lines']} 行)"
              f" → {'BLOCK' if d['blocked'] else 'OK/NEEDS'}")
        for k, v in d["buckets"].items():
            if v:
                print(f"    {k:<10s}: {', '.join(sorted(v))}")


def cmd_group(names):
    src = _read_mr()
    info, consts, _ = _analyze(src)
    patched = cmd_nomove_silent()
    names = list(dict.fromkeys(names))
    missing = [n for n in names if n not in info]
    if missing:
        print(f"[group] 找不到頂層函式:{', '.join(missing)}")
        sys.exit(2)
    ok = True
    need_consts = set()
    for n in names:
        d = info[n]
        if d["blocked"]:
            b = d["buckets"]
            print(f"  BLOCK  {n}:net/io={sorted(b['net_io'])} state={sorted(b['mr_state'])} "
                  f"unknown={sorted(b['unknown'])}")
            ok = False
            continue
        outside = [dep for dep in d["buckets"]["mr_func"] if dep not in names]
        if outside:
            print(f"  OPEN   {n}:呼叫了群外 mr 函式 {sorted(outside)} → 一併納入群組或放棄")
            ok = False
        need_consts |= d["buckets"]["mr_const"]
        for dep in d["buckets"]["mr_func"]:
            if dep in names and dep in patched:
                print(f"  TRAP   {n} 呼叫了群內被 monkeypatch 的 {dep}:搬走後 patch mr.{dep} "
                      f"攔不到 {n} 內部的呼叫 → 把 {n} 留在 mr,或改測試(需說明)")
                ok = False
    if ok:
        print(f"[group] ALL-CLEAR:{len(names)} 個函式閉包封閉、無 TRAP。")
        if need_consts:
            print(f"        隨行常數(一併搬並 re-export):{', '.join(sorted(need_consts))}")
        pat_in = sorted(set(names) & patched)
        if pat_in:
            print(f"        提醒:{', '.join(pat_in)} 本身被 tests patch——re-export 後 patch mr.X 仍有效"
                  f"(mr 端呼叫走 mr 命名空間),但群內互呼已驗證不存在。")
    else:
        print("[group] 未通過——按上面標記縮群或放棄(誠實少搬)。")
        sys.exit(1)


def _extract_func_src(src: str, name: str) -> str | None:
    """回函式本體的「正規化文本」:splitlines()+"\n".join()+rstrip() 會刻意抹平行尾
    (CRLF/LF)與尾隨空白。這對搬遷驗證是正確語意——只改行尾/尾隨空白的搬移在邏輯上安全,
    在 Windows(CRLF 檔)對比 git blob(LF)時尤其必要。故 verify-move 宣稱的是
    「正規化文本相同」而非嚴格 byte-identity(見 cmd_verify_move 訊息;Codex re-review P2)。"""
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
            return "\n".join(lines[start:node.end_lineno]).rstrip()
    return None


def cmd_verify_move(module, names):
    head = subprocess.run(
        ["git", "-C", str(ROOT), "show", "HEAD:morning_report.py"],
        capture_output=True, check=True).stdout.decode("utf-8")
    new_src = (ROOT / f"{module}.py").read_text(encoding="utf-8")
    ok = True
    for n in names:
        a = _extract_func_src(head, n)
        b = _extract_func_src(new_src, n)
        if a is None:
            print(f"  ?     {n}:HEAD 的 morning_report.py 沒有(是不是早搬過了?)")
            ok = False
        elif b is None:
            print(f"  MISS  {n}:{module}.py 裡沒有")
            ok = False
        # 刻意用正規化文本(_extract_func_src 已抹平行尾/尾隨空白)而非 raw byte 比對:
        # 本 repo 在 Windows 以 autocrlf=CRLF 簽出,工作檔為 CRLF、git blob 為 LF——
        # 真 byte-exact 會讓「每一次」搬移都誤報 DIFF、工具直接失效。忽略行尾/尾隨空白
        # 正是「邏輯不變」的正確語意(Codex re-review#2 建議改 byte-exact,經評估駁回)。
        elif a != b:
            print(f"  DIFF  {n}:本體與 HEAD 不同(正規化文本比對:行尾/尾隨空白已忽略)"
                  f"——搬遷必須照抄,查出差異或回退")
            ok = False
        else:
            print(f"  OK    {n}:normalized-identical(行尾/尾隨空白正規化後相同)")
    if not ok:
        sys.exit(1)
    print(f"[verify-move] 全部通過:{module}.py 的 {len(names)} 個函式與 HEAD 正規化文本相同"
          f"(忽略行尾/尾隨空白;非嚴格 byte-identity)。")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "nomove":
        cmd_nomove()
    elif cmd == "list":
        cmd_list()
    elif cmd == "check" and rest:
        cmd_check(rest)
    elif cmd == "group" and rest:
        cmd_group(rest)
    elif cmd == "verify-move" and len(rest) >= 2:
        cmd_verify_move(rest[0], rest[1:])
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
