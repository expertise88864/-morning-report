# -*- coding: utf-8 -*-
"""晨報主流程的 AST 掃描範圍(第十一輪 P2-3)。

## 為什麼要有這個檔

有三條既有測試寫成「在 `main()` 裡找某個呼叫/寫入」:
`test_extraction_alias_map_is_wired_at_both_call_sites`、
`test_mixed_versions_reach_the_run_manifest`、
`test_capability_health_is_refreshed_after_event_extraction`。

P2-3 把 `main()` 拆成 `_phase_*` 之後,那些掃描的**範圍憑空縮小**了 ——
它們要驗的性質(「主流程真的有做這件事」)完全沒變,變的只是程式碼住在哪個
函式裡。這一次它們紅了所以看得見;但同一形狀在本 repo 已經無聲失效過兩次
(批#95 的葉模組漏列、r1 的 provider 前綴 regex)。

所以掃描範圍改由**性質**決定:「晨報主流程」= `main()` 加上所有 `_phase_*`。
下一次再拆一層,只要新函式仍叫 `_phase_*`,這裡就自動涵蓋。
"""
import ast


def report_pipeline_functions(tree: ast.Module) -> list:
    """`main()` 與所有 `_phase_*` 頂層函式。

    找不到 `main()` 就是這個檔案不是晨報主模組 —— 直接爆,不要回空清單:
    空清單會讓呼叫端的 `for` 一圈都不跑,而那是「假綠燈」的標準形狀。
    """
    out = [n for n in tree.body
           if isinstance(n, ast.FunctionDef)
           and (n.name == "main" or n.name.startswith("_phase_"))]
    assert any(n.name == "main" for n in out), (
        "找不到頂層 main() —— 掃描範圍的定義需要同步更新,不得回空清單")
    return out


def walk_pipeline(tree: ast.Module):
    """主流程裡的每一個 AST 節點。"""
    for fn in report_pipeline_functions(tree):
        yield from ast.walk(fn)
