# -*- coding: utf-8 -*-
"""**A/B 盲評卡**:產生、拆分、落地(Luna vs DeepSeek 實驗)。

十配對達標後的判讀**明文要求人工盲評**。這個要求在四輪外審裡落空四次,
每次形狀相同 —— 機制存在、看起來在運作、實際交付不出東西:

  1. (r4 #2)`blind_review_pair` **沒有任何生產呼叫端**。影子的文字算完
     指標就被丟掉,兩份文字再也湊不齊。
  2. (r5 #2)卡片產生了,但解碼表和 A/B 內容在**同一個 JSON** 裡 ——
     評審點開第一眼就看得到誰是誰。
  3. (r5 #1)卡片是盲的了,但 `sink` 只是個字串,**沒有任何搬運行為**:
     卡片只活在 runner 上,十天後一張都取不回。
  4. (r6 #1)有 consumer 了,但寫成功就回 `review_ok=True` —— 於是
     `local` 這個「寫得成功、拿不回來」的預設每天都被記成有材料,
     帳本可以顯示 10/10 而實際一張不存在。

所以本模組的判準全部訂在**可觀察的交付結果**上,而不是「有沒有做這個動作」:

    有材料 = 寫成功 **AND** job 結束後拿得回來 **AND** 還沒過期

## 隱私

卡片含**兩份完整的分析文字**,而本 repo 是公開的。所以:落地目錄不在
`STATE_ROOT` 之下(state 會 commit 進公開 repo)、manifest 只帶存在性、
解碼表另存一檔。本模組**不碰檔案系統**,寫檔器由呼叫端注入。
"""
from __future__ import annotations

import datetime as _dt

#: sink → **job 結束之後拿不拿得到**。`local` 只寫在 runner 上,job 結束即
#: 消失;`artifact` 由 workflow 的 upload 步驟接走。認不得的 sink 直接拋。
SINKS = {"local": False, "artifact": True}
#: artifact 保留幾天。**要蓋得住整個累積期**:十配對的分母是成功配對數,
#: 失敗與跳過的日子不推進它,累積期因此可以遠長於保留期(r6 Codex,#3)。
#: workflow 的 `retention-days` 必須是同一個數字(有測試盯)。
RETENTION_DAYS = 90

def blind_review_pair(primary_text: str, shadow_text: str, *, seed: str) -> dict:
    """產生**隱去模型名稱**的 A/B 對照,供人工盲評。

    `seed` 必須由呼叫端給(通常是日期)—— 本模組不呼叫 `random`,
    否則同一天重看會拿到不同的 A/B 排列,而人已經寫好的評分就對不上了。

    A/B 的對應關係要**存下來**但不要顯示。評分完成後才用它解碼。
    """
    flip = sum(ord(c) for c in str(seed or "")) % 2 == 1
    a, b = ((shadow_text, primary_text) if flip else (primary_text, shadow_text))
    return {
        "seed": str(seed or ""),
        "A": a or "", "B": b or "",
        # 解碼表:盲評時**不得顯示**,評完才用
        "_key": {"A": "shadow" if flip else "primary",
                 "B": "primary" if flip else "shadow"},
        "criteria": ("完整度", "因果推理", "證據忠實度", "反證處理",
                     "市場洞察", "可行動性", "文字清晰度"),
        "scale": "1-5",
    }


def blind_review_is_decodable(card: dict) -> bool:
    """解碼表在不在。沒有它,評完的分數對不回模型 —— 整天的盲評作廢。"""
    key = (card or {}).get("_key") or {}
    return set(key) == {"A", "B"} and set(key.values()) == {"primary", "shadow"}


def blind_review_is_blind(card: dict) -> bool:
    """評審**真正會打開的那份**有沒有洩漏身分(r5 Codex,#2)。

    先前解碼表與 A/B 內容同在一個 JSON 裡:評審點開檔案第一眼就看得到
    哪一邊是誰,「盲評」只剩名字。而 `blind_review_is_decodable` 反而把
    這個共存行為固化成測試通過的條件。
    """
    return "_key" not in (card or {})


def split_card(card: dict) -> tuple:
    """拆成 `(評審看的, 解碼表)` —— 兩份分開存、分開授權。

    解碼表要等評分寫完才拿出來;放在同一份裡就不是盲評了。
    """
    c = dict(card or {})
    key = {"seed": c.get("seed"), "_key": c.pop("_key", None)}
    return c, key


def card_files(primary_text: str, shadow_text: str, *, today: str, sink: str,
               dirname: str, sinks: dict) -> tuple:
    """回傳 `(要寫的檔案, manifest 摘要)`。**本模組不碰檔案系統。**

    三份分開是重點:

      * **評審看的那份不含解碼表**。先前兩者同在一個 JSON 裡,評審點開
        第一眼就看得到哪一邊是誰 ——「盲評」只剩名字(r5 Codex,#2)。
      * 解碼表另存,評分寫完才拿出來。
      * manifest 只帶存在性:它會被 commit 進**公開 repo**,而卡片含兩份
        完整分析文字。不准帶文字,也不准帶解碼表。

    `sinks` 是「sink → job 結束後拿不拿得到」的對照表。認不得的 sink 直接
    拋 —— 先前 sink 只被寫進 manifest、沒有任何分派或搬運行為,於是十天後
    一張卡都取不回,而 manifest 看起來像是有在交付(r5 Codex,#1)。
    """
    if sink not in sinks:
        raise ValueError(f"未知的 sink {sink!r}(可用:{'/'.join(sorted(sinks))})")
    reviewer, key = split_card(
        blind_review_pair(primary_text, shadow_text, seed=today))
    return ([(f"{today}.json", reviewer), (f"{today}.key.json", key)], {
        "date": today, "dir": dirname, "sink": sink, "ok": True,
        "blind": blind_review_is_blind(reviewer),
        "decodable": blind_review_is_decodable(key),
        "retrievable_after_job": bool(sinks[sink]),
        "criteria": list(reviewer.get("criteria") or ()),
    })


def card_expiry(today: str, retention_days: int) -> str:
    """卡片到哪一天為止還拿得到(`YYYY-MM-DD`)。

    r6(Codex,#3):十配對的分母是**成功配對數,不是天數** —— 失敗與跳過
    的日子不推進它,所以累積期可以遠長於保留期。早期的卡片會先過期,而
    帳本那一列的 `review_ok` 卻永遠是 True:進度於是高估了「還拿得回來的」。
    到期日要記進帳本,判讀時才算得出真正還在的材料。
    """
    y, m, d = (int(x) for x in str(today).split("-")[:3])
    return (_dt.date(y, m, d) + _dt.timedelta(days=int(retention_days))).isoformat()


def write_card(primary_text: str, shadow_text: str, *, today: str, sink: str,
               sinks: dict, dirname: str, retention_days: int,
               build, write, degrade) -> dict:
    """把盲評卡落地,回傳**要記進帳本的那一段**(manifest 也用同一份)。

    寫檔器由呼叫端注入(本模組不 import 主模組、不碰 `Path`)——
    與 `run_comparison` 同一個做法。

    回傳值裡 `review_ok` 是重點,而它**不等於「寫成功」**:

        r6(Codex,#1):先前寫成功就回 True,於是預設的 `local` sink ——
        它明明 job 結束就消失 —— 每天都被記成「這天有盲評材料」。
        帳本可以顯示 10/10 有材料、警語不出現,而實際上一張卡都不存在。
        r5 修的東西被自己的回傳值繞過去了。

    所以:**寫成功 AND 拿得回來**,才叫有材料。
    """
    try:
        files, entry = build(primary_text, shadow_text, today=today, sink=sink,
                             dirname=dirname, sinks=sinks)
        for name, obj in files:
            write(name, obj)
        keep = bool(entry["retrievable_after_job"])
        if not keep:
            degrade(f"blind_review:not_retrievable:{sink}")
        entry["review_ok"] = keep
        entry["review_expires"] = card_expiry(today, retention_days) if keep else ""
        return entry
    except Exception as e:                     # noqa: BLE001 - 觀測不得弄壞晨報
        degrade("blind_review:write_failed")
        return {"date": today, "sink": sink, "ok": False,
                "error": f"{type(e).__name__}: {e}"[:160],
                "blind": False, "decodable": False,
                "retrievable_after_job": False,
                "review_ok": False, "review_expires": ""}
