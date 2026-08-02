# -*- coding: utf-8 -*-
"""**一份真的合乎 strict schema 的分析輸出**(第十三輪 P2-3)。

## 為什麼要有這個檔

`ANALYSIS_OUTPUT_SCHEMA` 是 strict:所有欄位必填、`additionalProperties: False`。
而先前各處測試裡叫 `_GOOD` 的 fixture **不合法** —— 實測 8 條違規:
`top_news_analysis` 少三個必填、`claim_audit` 少兩個,而且帶著一個
schema 根本沒有的 `claim_id`(我自己發明的欄位)。

也就是說那些拿來驗「整條生產路徑」的測試,驗的是**真實 API 永遠不會產出
的形狀**。而真實輸出多出來的那些欄位(`persistence`、`direction`、
`confidence`…)在測試裡從來沒出現過,renderer 與 grounding 在它們身上的
行為因此完全沒被覆蓋。

**測試要用生產的形狀** —— 這個 repo 反覆栽在這句話上,而這次連形狀本身
都不對。所以改成一份共用 fixture,並由 `json_contract` 在測試裡當場驗它
合不合法:fixture 一旦退化,先紅的是 fixture 自己,不是某個下游測試。

## 新聞與證據 ID

`news()` 提供 `n1` / `n2`,分析裡的每個 `evidence_ids` 都只引用它們 ——
引用不存在的 ID 是另一種缺陷(`analysis_schema.validate` 會抓),
fixture 不該同時觸發兩種。
"""


def news() -> list:
    """兩則新聞;分析裡的證據 ID 只會引用這兩個。"""
    return [
        {"source_item_id": "n1", "title": "費城半導體指數收漲 2.1%",
         "summary": "SOX 收漲,台股電子期待傳導。", "source": "Reuters",
         "entities": ["費半"], "published_at": "2026-08-02T20:00:00+08:00"},
        {"source_item_id": "n2", "title": "台積電法說會下週登場",
         "summary": "市場關注資本支出。", "source": "經濟日報",
         "entities": ["台積電"], "published_at": "2026-08-02T18:00:00+08:00"},
    ]


def _claim(statement: str, *, evidence=("n1",), materiality="high",
           claim_type="fact") -> dict:
    """一則合乎 schema 的主張。**九個必填欄位一個都不能少。**"""
    return {
        "statement": statement,
        "claim_type": claim_type,
        "direction": "bullish",
        "materiality": materiality,
        "confidence": 0.8,
        "horizon": "intraday",
        "evidence_ids": list(evidence),
        "counterevidence_ids": [],
        "falsification_trigger": "夜盤翻黑",
    }


def valid_analysis() -> dict:
    """一份**通得過 strict schema、也通得過語意根據檢查**的分析。

    兩種合格是不同的事:schema 管形狀,`analysis_grounding` 管有沒有根據。
    這份兩者都要過 —— 否則拿它當「合格輸出」去驗路徑,驗到的是半個合格。
    """
    return {
        "executive_summary": "今日偏多,留意台積電法說。",
        "stance": {"label": "偏多", "score": 6, "confidence": 0.7,
                   "time_horizon": "1-5d", "rationale": "多數訊號同向。"},
        "market_regime": {"label": "偏多", "evidence_ids": ["n1"]},
        "key_drivers": [_claim("費半走強")],
        "scenario_tree": {
            "base": {"narrative": "震盪走高", "probability": 0.6, "triggers": []},
            "bull": {"narrative": "突破前高", "probability": 0.2, "triggers": []},
            "bear": {"narrative": "回測季線", "probability": 0.2, "triggers": []},
            "invalidation_triggers": []},
        "taiwan_market": {"summary": "量能回升。", "taiex_view": "偏多",
                          "tsmc_view": "守月線", "evidence_ids": ["n2"]},
        "global_market": {"summary": "美股收紅。",
                          "us_to_tw_linkage": "費半傳導",
                          "evidence_ids": ["n1"]},
        "portfolio_implications": {"summary": "維持核心部位。",
                                   "actions_to_consider": [], "risks": []},
        "top_news_analysis": [{"source_item_id": "n1",
                               "why_it_matters": "費半傳導台股電子",
                               "direction": "bullish", "materiality": "high",
                               "persistence": "數個交易日"}],
        "contradictions": [],
        "data_gaps": [],
        "watch_triggers": [],
        "claim_audit": [_claim("費半走強")],
        "priced_in": {"already_reflected": [], "not_yet_reflected": []},
    }


def ungrounded_analysis() -> dict:
    """形狀合法、但**沒有根據**的那種輸出(第十二輪 P1-3 的反例)。

    刻意保持 schema 合法:要驗的是「語意根據」那一關擋不擋得住,
    而不是讓它在形狀那一關就先被擋掉 —— 兩關混在一起就分不出誰在作用。
    """
    obj = valid_analysis()
    obj["key_drivers"] = [_claim("台股必漲", evidence=[])]
    obj["market_regime"]["evidence_ids"] = []
    obj["taiwan_market"]["evidence_ids"] = []
    obj["global_market"]["evidence_ids"] = []
    obj["top_news_analysis"] = []
    obj["claim_audit"] = []
    return obj
