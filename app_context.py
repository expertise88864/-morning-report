# -*- coding: utf-8 -*-
"""一次執行的**共用狀態**(第十一輪 P2-3)。

## 為什麼是 context 物件而不是回傳值

批#121 拆第一個相位時用的是「回傳 dict、呼叫端解包」,當時就寫下:
「其餘七個若每個都要 +20 行,那是拆法不對」。實測跨相位的區域變數有 **35 個** ——
照那個做法要寫 35 行解包,而且每加一個欄位就要改三個地方(相位的 return、
呼叫端的解包、下游的使用)。

所以改成一個共用容器:相位收 `ctx`,在**邊界**讀進來、寫回去,
中間的本體一行都不動。這件事很重要 —— `main()` 至今**沒有任何測試會執行到**,
所以「行為等價」只能靠人看 diff,而本體逐字不變的 diff 才看得出來。

## `__slots__` 是刻意的

沒有 slots 的話,`ctx.quotez = …`(打錯字)會安靜地生出一個新屬性,
而讀的那一端拿到 `AttributeError` —— 在生產是凌晨六點、信沒寄出。
有 slots 就是**寫的當下**就爆。這是這個檔唯一的邏輯。

欄位一律預設 `None`,不給型別預設值:「還沒跑到那個相位」與「那個相位算出
空值」必須分得出來。
"""
from __future__ import annotations


class AppContext:
    """一次晨報執行裡,跨相位共用的東西。

    `recorder` 是 `run_manifest.ManifestRecorder` —— 相位透過它記階段耗時,
    不再抓模組全域(那就是 P2-3 要的那一刀)。
    """

    #: **欄位隨相位逐一加入,不預先開好。**
    #: 預先把 35 個欄位全開,會讓「沒有任何人用的欄位」看起來很正常 ——
    #: 而 `tests/test_main_decomposition.py` 正是靠「每個欄位都要有人寫、
    #: 有人讀」來確認 context 沒有變成一袋雜物。先開好就等於先關掉那條檢查。
    __slots__ = (
        # 執行環境:序幕算好,相位共用
        "recorder", "now_tpe", "mode", "report_date",
        "target_session_date", "target_session_day",
        # 相位一:行情 / 總經 / FX / 除息 / 兩檔預測
        "quotes", "hist_2330", "ex_div", "fair", "predictions",
        # 相位二:新聞 / 政策 / 體育
        "news", "sec_filings",
        # 相位三:TAIFEX 籌碼、台指與 0050 預測、融資券與週動能
        "backtest_block", "breadth", "earnings_proximity", "history", "margin",
        "night_txf", "taiex_pred", "taifex_large", "taifex_oi", "taifex_pcr",
        "tw0050_pred", "twse_taiex_close", "weekly",
        # 相位四:TWSE universe / 候選新聞 / 集保快照
        "tdcc_snapshot_for_state", "tw0050", "tw_mops",
        # 相位五:事件抽取 / 模型 / walk-forward
        "calibration", "model_history", "trading_sessions",
        # 相位六~七:LLM 主分析與渲染
        "analysis", "html", "pending_state_entry",
    )

    def __init__(self, recorder):
        for name in self.__slots__:
            setattr(self, name, None)
        self.recorder = recorder

    def mark_phase(self, label: str, clock: float) -> None:
        """相位邊界的時間標記。轉給 recorder —— 相位只需要認得 `ctx`。"""
        self.recorder.mark_phase(label, clock)
