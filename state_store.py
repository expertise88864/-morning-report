# -*- coding: utf-8 -*-
"""**「讀不動」不等於「今天是第一天」**(repo-wide 外審 2026-08-22 P1-3/P2)。

持久 state 的讀取端先前各自發明政策:有的 raise、有的回 `{}`、有的回 `[]`、
有的把壞檔改名保存、有的直接重建。差異本身就是缺陷 —— 因為**同一個
錯誤在不同檔案會有不同後果**,而最嚴重的那種是靜默的:

  `state/forecast_ledger.json` 同時承載預測記分帳本、Top5 可執行帳本與
  MZ 影子 OOS 樣本。兩個寫入端都是「讀失敗 → `ledger = []` → 照常往下跑
  → 結尾無條件 `_atomic_write_text`」。於是一次暫時性的檔案損壞(部分寫入、
  手動編輯、上一班 state push 中斷)會把幾百列歷史換成今天這一列,
  而且**下一班讀到的是合法 JSON**,那個新基線從此看起來完全正常。
  更安靜的是「合法 JSON、錯的 root type」(`{}`):`isinstance` 判斷不成立,
  連「載入失敗」都不會印。

這個模組把四種狀態分開,而且**只分開,不代做決定**:

  * `missing`  —— 檔案不存在。這才是「第一天」,可以建立。
  * `ok`       —— 讀到而且型別對。
  * corrupt    —— 讀得到位元組但解不開,或 root type 不對 → `StateCorrupt`。

corrupt 的處置權在呼叫端,但**預設答案只有一個**:不要覆寫。原始位元組
留著就是保存現場(不必另存 `.corrupt` 檔 —— 只要沒有人覆寫它,它就還在),
而且要在 manifest / 降級清單留痕,否則「今天沒更新」與「今天沒事」
在紀錄裡長得一樣。

`model_history_store` 早就是這個政策(checksum + 未刻意重寫的損壞檔不得
重新 baseline);這裡是把同一條規矩給其餘的 state 檔。
"""
from __future__ import annotations

import json
from pathlib import Path


class StateCorrupt(RuntimeError):
    """讀得到檔案但內容不可信。**與「檔案不存在」語意不同** —— 兩者
    的正確處置相反:不存在可以建立,不可信不能覆寫。"""

    def __init__(self, path, why: str):
        super().__init__(f"{path}: {why}")
        self.path = str(path)
        self.why = why


def load_json_state(path, *, expected=list):
    """回 `(value, status)`;`status` 是 `"missing"` 或 `"ok"`。

    壞檔一律 `raise StateCorrupt` —— **不回傳空值**。回空值正是這條規則
    要消滅的形狀:呼叫端拿到空的之後,分不出「還沒有」與「壞掉了」,
    而它接下來多半會覆寫。

    `expected` 是 root 的型別(`list` / `dict`)。合法 JSON 但 root 型別
    不對同樣算 corrupt:那不是「空狀態」,是**另一個檔案**的形狀。
    """
    p = Path(path)
    try:
        if not p.exists():
            return (expected(), "missing")
        # **先讀 bytes 再在同一個受保護區塊解碼**(r1 外審 P1):
        # `read_text` 的 `UnicodeDecodeError` 不是 `OSError`,會直接逸出 ——
        # 而呼叫端只接 `StateCorrupt`,於是「非 UTF-8 的壞檔」不是降級而是
        # **主流程中止、晨報寄不出去**。壞檔的處置只有一種,不因編碼而異。
        raw = p.read_bytes().decode("utf-8")
    except OSError as e:                      # 讀不到 ≠ 沒有:也不能覆寫
        raise StateCorrupt(p, f"讀取失敗:{type(e).__name__}") from e
    except UnicodeError as e:
        raise StateCorrupt(p, f"不是合法的 UTF-8:{type(e).__name__}") from e
    if not raw.strip():
        # 空檔:寫入被中斷的典型殘骸。當成 corrupt 而不是「第一天」——
        # 原子寫入不會產生空檔,所以它一定是別的東西弄壞的。
        raise StateCorrupt(p, "檔案是空的(疑似寫入中斷)")
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise StateCorrupt(p, f"JSON 解析失敗:{str(e)[:60]}") from e
    if not isinstance(data, expected):
        raise StateCorrupt(
            p, f"root 型別是 {type(data).__name__},預期 {expected.__name__}")
    return (data, "ok")
