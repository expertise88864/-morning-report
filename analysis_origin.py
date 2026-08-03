# -*- coding: utf-8 -*-
"""**這份分析到底是誰寫的、走的哪一條路**(第十四輪 P0-1)。

## 實機證據

2026-08-03 06:43 那一班,`run_manifest.json` 記著
`degraded_steps: ["llm:luna_path_failed"]`、`llm_experiment: null` ——
Luna 特化路徑沒跑成,實驗零產出。而同一天的 `llm_shadow_ledger.json` 記著::

    primary_model: gpt-5.6-luna
    primary_effort: xhigh
    primary_ok:    true

兩份帳本都沒有說謊:特化路徑確實失敗了,而**主分析確實產出了文字**
(Luna 這個模型跑了 DeepSeek 的舊 prompt)。問題是十天後翻帳本的人
看到的是「Luna xhigh 成功」—— 而那正是這個實驗要回答的問題本身。

`primary_ok = bool(primary_text)` 量的是「有沒有東西可以寄」,
不是「新路徑成不成立」。這兩件事在正常的日子裡剛好一致,
**只有在失敗的日子裡才會分開** —— 也就是唯一需要它們分開的時候。

## 這個模組

把「哪條路」變成一個必須明講的欄位。它進 cohort 鍵,所以落回 legacy 的日子
不會跟特化成功的日子被平均在一起;而 `counts_as_primary_success()` 是
**唯一**可以宣告「Luna 成功」的地方。
"""
from __future__ import annotations

#: Luna 專用 prompt + Responses + strict schema,整條走完。
LUNA_SPECIALIZED = "luna_specialized"
#: 沒有啟用特化路徑(設定就沒開),走的是既有的單段 prompt。
LEGACY_PRIMARY = "legacy_primary"
#: **特化路徑試過、失敗了**,由既有路徑補上。寫信的模型可能仍是 Luna ——
#: 這正是最容易被誤讀成「Luna 成功」的那一種。
LEGACY_AFTER_LUNA_FAILURE = "legacy_fallback_after_luna_failure"
#: 連 legacy 也失敗,寄出的是 Python 組的備援文字(沒有任何模型判斷)。
EMERGENCY_FALLBACK = "emergency_fallback"
#: 沒有記錄。**舊資料才該是這個值** —— 新寫入的列一律要指名。
UNKNOWN = "unknown"

ORIGINS = frozenset({LUNA_SPECIALIZED, LEGACY_PRIMARY,
                     LEGACY_AFTER_LUNA_FAILURE, EMERGENCY_FALLBACK, UNKNOWN})


def normalize(origin) -> str:
    """認不得的值一律變成 `unknown`,**不得靜默當成成功**。

    寧可少算一天樣本,也不要讓一個打錯字的字串被計成 Luna 成功 ——
    這個實驗的結論會直接建立在這個計數上。
    """
    o = str(origin or "").strip()
    return o if o in ORIGINS else UNKNOWN


def counts_as_primary_success(origin, *, has_text: bool = True) -> bool:
    """**只有特化路徑整條走完才算 Luna 成功。**

    `has_text` 仍然是必要條件(沒有輸出就沒有東西可比),但不再是充分條件。
    """
    return bool(has_text) and normalize(origin) == LUNA_SPECIALIZED


def describe(origin) -> str:
    """給人看的一句話。帳本要能被人讀懂,不只是被程式讀。"""
    return {
        LUNA_SPECIALIZED: "Luna 特化路徑(專用 prompt + strict JSON)",
        LEGACY_PRIMARY: "既有路徑(未啟用特化)",
        LEGACY_AFTER_LUNA_FAILURE: "特化路徑失敗後由既有路徑補上 —— 不算 Luna 成功",
        EMERGENCY_FALLBACK: "備援文字(沒有模型判斷)",
        UNKNOWN: "沒有記錄",
    }[normalize(origin)]
