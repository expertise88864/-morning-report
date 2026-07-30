# -*- coding: utf-8 -*-
"""**主模組尺寸凍結**(第七輪 P2-4)。

## 為什麼是「凍結」而不是「抽出」
第七輪建議抽出 `top5_ledger.py` / `forecast_ledger.py` / `corporate_actions.py`
等模組。我用 repo 自己的稽核工具實測過:

```
$ python tools/refactor_audit.py group exdiv_events_in_window exdiv_coverage_ok \
      load_exdiv_history update_exdiv_history _roc_to_iso
  BLOCK  load_exdiv_history:   state=['EXDIV_HISTORY_FILE'] unknown=['ExdivHistoryUnreadable']
  BLOCK  update_exdiv_history: state=['EXDIV_HISTORY_FILE']
```

`update_top5_ledger` / `update_forecast_ledger` 同樣是 BLOCK(碰 `FORECAST_LEDGER_FILE`
與 `_atomic_write_text`,而前者被測試 monkeypatch)。工具建議「可搬」的群組
全部只有 8–9 行。也就是說**依 repo 自己的規約(「判 BLOCK 的絕不搬」),
實質抽取做不到** —— 除非先改動測試隔離的做法,而那個風險遠大於收益。

所以做 P2-4 的另一半:**擋住繼續膨脹**。這比一次性抽取更貼近問題本身
(檔案是一批一批長大的,不是一次長成的)。

## 這條測試怎麼運作
上限是**棘輪**:只能降不能升。要新增超過上限的業務邏輯時,得先把等量的東西
搬出去或刪掉,而不是順手調高數字。調高上限本身是一個需要在 commit 裡說明的動作,
而不是無聲的漂移。

刻意用行數而非函式數:行數是「讀這個檔的人要承受多少」的直接代理,
而函式數會被「把一個大函式拆成三個仍然留在原檔」蒙混過去。
"""
from pathlib import Path

import pytest

#: 主模組行數上限。**只能降不能升。**
#: 2026-07-31 基準:21,797 行(第七輪期間從 20,572 行成長 1,225 行 ——
#: 那些是批#66–#77 的修正與註解,大多是必要的,但趨勢必須被擋住)。
#: 留 200 行緩衝給進行中的修正,不留給新功能。
MAIN_MODULE_LINE_CEILING = 22_000

#: 其餘模組的上限。它們是「抽出去之後應該接住成長」的地方,
#: 上限比較寬鬆但仍然有 —— 否則只是把膨脹換個檔案繼續。
MODULE_CEILINGS = {
    "news_events.py": 1_400,
    "story_ledger.py": 2_000,
    "render_utils.py": 1_900,
    "data_quality.py": 500,
    "model_history_store.py": 600,
}


def _lines(name: str) -> int:
    path = Path(name)
    if not path.exists():
        pytest.skip(f"{name} 不存在")
    return len(path.read_text(encoding="utf-8").splitlines())


def test_main_module_does_not_grow_past_the_ceiling():
    """`morning_report.py` 不得繼續膨脹。

    超過上限時**不要直接調高數字** —— 先問這批新增的東西能不能放進既有的
    葉模組(news_events / story_ledger / data_quality / render_utils),
    或者能不能刪掉等量的舊東西。真的必須調高時,在 commit message 裡說明
    為什麼那些行無法放到別處。
    """
    n = _lines("morning_report.py")
    assert n <= MAIN_MODULE_LINE_CEILING, (
        f"morning_report.py 已達 {n} 行,超過上限 {MAIN_MODULE_LINE_CEILING}。\n"
        "  這是**棘輪**:請先把等量的邏輯搬到葉模組或刪除,而不是調高數字。\n"
        "  可搬性請用 `python tools/refactor_audit.py group <FUNC...>` 判定"
        "(判 BLOCK 的絕不搬)。")


def test_leaf_modules_do_not_absorb_the_bloat():
    """葉模組也有上限 —— 否則「抽出去」只是把膨脹換個檔案繼續。"""
    over = {name: (_lines(name), cap)
            for name, cap in MODULE_CEILINGS.items()
            if _lines(name) > cap}
    assert not over, "、".join(
        f"{k} {v[0]} 行 > 上限 {v[1]}" for k, v in over.items())


def test_the_ceiling_is_not_far_above_reality():
    """**棘輪必須貼著現況**,否則它只是一個永遠不會觸發的裝飾。

    上限與實際行數差距過大時,這條會失敗並要求把上限調降到接近現況 ——
    也就是說「降低上限」是被強制的,而不是靠自律。
    """
    n = _lines("morning_report.py")
    slack = MAIN_MODULE_LINE_CEILING - n
    assert slack <= 600, (
        f"上限 {MAIN_MODULE_LINE_CEILING} 比實際 {n} 行高出 {slack} 行 —— "
        "棘輪鬆掉了,請把上限調降到接近現況(建議 現況 + 200)。")
