# -*- coding: utf-8 -*-
"""實驗紀錄的**落地**:寫進跨日帳本,以及失敗那天也要留一列。

2026-08-03 從主模組抽出。主模組已達行數上限,而前兩次都是靠壓縮註解擠進去
—— 那不是重構,是把問題往後推。這一叢本來就自成一塊:它回答的是
「這一天在實驗裡留下了什麼」,與「怎麼算分析」無關。

寫檔、設定與 manifest 由呼叫端注入,本模組不碰模組全域。
"""
from __future__ import annotations

from typing import Optional

import llm_experiment as _lx


def persist(record: dict, today: str, *, experiment_id: str, ledger_path,
            target: int, write, manifest: dict, degraded: list, log) -> None:
    """把今天這一列寫進跨日帳本並回報進度。

    r2(Codex,#3):先前紀錄只進當日 manifest,而 manifest 每天覆寫 ——
    十配對的計數機制**存在但不會計數**,那比沒有機制更糟:它看起來在運作。

    **失敗只是今天沒有累積,不得讓晨報中斷。** 讀不出帳本就不寫
    (覆蓋等於把十配對清零),寫不進去也只記一筆降級。
    """
    if not experiment_id:
        return
    try:
        progress = _lx.record_day(
            record=record, today=today, ledger_path=ledger_path,
            read_ledger=_lx.load_ledger, write_ledger=write,
            target=target, log=log)
        manifest["llm_experiment"] = dict(record, progress=progress)
    except Exception as e:                      # noqa: BLE001 - 晨報不可斷
        degraded.append("llm_experiment_ledger")
        log(f"[llm-experiment] 帳本寫入失敗(不影響晨報):{type(e).__name__}: {e}")
        manifest["llm_experiment"] = dict(record, ledger_error=str(e)[:120])


def record_failure(packet: Optional[dict], reason: str, *, experiment_id: str,
                   row, persist_row, log) -> None:
    """主分析失敗的那一天**也要有一列**(r1 Codex,#4)。

    只記成功的那幾天,「誰比較常失敗」的答案永遠是 100%。

    2026-08-03 實機:**證據組裝就失敗的那天完全沒有紀錄** —— 舊守衛要求
    packet 存在,而那是最早也最常見的失敗點,實驗第一天因此零產出。
    (r4 當時擋的是「legacy 呼叫端捏出一列**成功**」;這裡是明確的失敗列,
     而且帳本已改成追加不覆蓋,那個顧慮不再成立。)
    """
    if not experiment_id:
        return
    try:
        persist_row(row(packet, reason))
    except Exception as e:                      # noqa: BLE001 - 記錄不得弄壞晨報
        log(f"[llm] 實驗失敗紀錄寫不進去(不影響晨報): {e}")
