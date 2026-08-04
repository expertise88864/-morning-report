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
        # schema v2:**這份 fixture 要示範的正是「有深度長什麼樣」。**
        # 兩則刻意不同:n1 說得出量級,n2 誠實說量級判斷不出來 ——
        # 後者是本次改版最重要的合法答案(用形容詞冒充答案才是失敗)。
        "top_news_analysis": [
            {"source_item_id": "n1", "why_it_matters": "費半傳導台股電子",
             "direction": "bullish", "materiality": "high",
             "persistence": "數個交易日",
             "mechanism_steps": [
                 {"from_what": "費半收漲", "to_what": "台股電子開盤定價",
                  "channel": "外部定價", "stage": "event",
                  "step_type": "fact", "evidence_ids": ["n1"]},
                 # **鏈要接得起來**:這一步的起點就是上一步的終點。
                 # 第十六輪 P1-7 的連續性守衛第一次跑就抓到這份 fixture
                 # 原本是斷的 —— 參考答案自己要示範它要求的性質。
                 {"from_what": "台股電子開盤定價", "to_what": "電子權值稼動預期",
                  "channel": "產業供需", "stage": "industry_supply_demand",
                  "step_type": "inference", "evidence_ids": []},
                 # **要走到財務/股價層**(第十七輪 P1-7):停在「情緒改善」
                 # 通得過連續性檢查,卻沒有碰到任何可驗證的後果。
                 {"from_what": "電子權值稼動預期", "to_what": "指數開盤價",
                  "channel": "權值佔比", "stage": "price",
                  "step_type": "inference", "evidence_ids": []}],
             "magnitude_band": "moderate",
             "why_this_magnitude": "費半漲幅與台股電子的歷史連動落在中段",
             "horizon": "intraday",
             "confirmation_signal": "電子權值開高且量能跟上",
             "invalidation_signal": "夜盤台指期翻黑",
             "relates_to": [{"other_source_item_id": "n2",
                             "relationship": "same_underlying_driver",
                             "evidence_ids": ["n1", "n2"],
                             "explanation": "都指向台積電的先進製程需求"}]},
            {"source_item_id": "n2", "why_it_matters": "法說會的資本支出指引",
             "direction": "neutral", "materiality": "medium",
             "persistence": "延續到法說當週",
             "mechanism_steps": [
                 {"from_what": "資本支出指引", "to_what": "設備與封裝訂單",
                  "channel": "資本支出", "stage": "operations",
                  "step_type": "scenario", "evidence_ids": ["n2"]}],
             "magnitude_band": "unknown",
             "why_this_magnitude": "尚未公布金額與時程,缺資本支出區間",
             "horizon": "1-5d",
             "confirmation_signal": "法說給出高於市場預期的資本支出區間",
             "invalidation_signal": "指引持平或下修",
             "relates_to": []}],
        "cross_market_synthesis": {
            "reinforcing_signals": ["費半走強", "美債利率回落"],
            "conflicting_signals": ["外資台指期淨空"],
            "dominant_driver": "美股科技股的外部定價",
            "why_it_dominates": "開盤前唯一已定價的資訊,本地籌碼要等現貨開出",
            "net_effect_intraday": "偏多,但主要反映在權值股開盤",
            "net_effect_next_days": "取決於期貨空單回補與否,方向未定",
            "funds_moving_from": ["塑化"],
            "funds_moving_to": ["半導體"],
            "what_would_flip_it": "外資空單續增且現貨量能萎縮",
            # 第十七輪 P1-3:**點名不等於處理** —— 每筆張力自己帶調和方式、
            # 哪一側可信、憑什麼、什麼情況分出勝負。
            "tension_resolutions": [],
            "evidence_ids": ["n1"]},
        "contradictions": [],
        "data_gaps": [],
        "watch_triggers": [],
        "claim_audit": [_claim("費半走強")],
        "priced_in": {"already_reflected": ["費半漲幅"],
                      "not_yet_reflected": ["台積電法說指引"],
                      "evidence_ids": ["n1"]},
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
    # schema v2:橫向綜合也是**會進信而且帶證據**的段落,反例要一起拔掉
    # 證據 —— 留著的話 grounding 會因為它有根據而放行,這個反例就失效了。
    obj["cross_market_synthesis"]["evidence_ids"] = []
    obj["priced_in"]["evidence_ids"] = []
    return obj
