# -*- coding: utf-8 -*-
"""**Luna 主分析的 strict 輸出契約**(第 Phase 2)。

## 為什麼是 schema 而不是「請模型照格式寫」

既有 DeepSeek 路徑要的是一段 Markdown,由後處理去猜段落。那條路徑保留不動,
但 Luna 這一側改成 **strict Structured Outputs**:形狀由 API 保證,Python 驗過
之後再由確定性 renderer 轉成信件。理由是十天實驗要量的東西
(證據支持率、數字一致性、反證處理)在自由文字上量不了 —— 只能靠人讀,
而人讀十天不可行。

## 不要求揭露思考過程

schema 裡沒有「逐步推理」欄位,也不存模型的隱藏推理。要的是**可稽核的
證據連結**:結論、支持證據 ID、反證 ID、不確定性、信心、以及**證偽條件**。
證偽條件是這份 schema 最重要的欄位 —— 一個說不出「什麼情況下我就錯了」的
判斷,事後無法評分,而無法評分的判斷在十天實驗裡等於沒有價值。

## strict 模式的硬性限制(官方文件,2026-08-01 查證)

  - 每個 object 都要 `additionalProperties: false`
  - **所有屬性都要列進 `required`**(沒有選填欄位;可空就給空陣列/空字串)
  - root 必須是 object
  - 上限:5000 屬性、10 層巢狀、12 萬字元、1000 個 enum 值

因此本檔的「選填」一律表達成「必填但允許空值」。這不是風格選擇,是 API 規則。
"""
from __future__ import annotations

import evidence_namespaces as _ns

#: 輸出契約版本。**改欄位就要進版** —— cohort 以它為身分的一部分,
#: 悄悄改欄位等於把不同定義的樣本混進同一個平均。
#: v2(第十五輪 P1-1):**prompt 叫模型深入分析,而 schema 沒有地方放深度。**
#: v1 的 `top_news_analysis` 只有四個淺欄位,模型最安全的填法就是
#: 「需求增加、對 2330 偏多」—— 改 prompt 的效果因此有天花板。
#: v2 加因果鏈/量級/時程/驗證與失效/關係,另加 `cross_market_synthesis`。
#: v3(第十六輪 P2-2):`cross_market_synthesis.addressed_tension_ids` ——
#: 「逐條處理每個 Python 張力」先前只有 prompt 要求、沒有東西驗得出來。
#: v4(第十七輪 P1-3/P1-7):`tension_resolutions` 取代 `addressed_tension_ids`
#: (點名不等於處理)、mechanism step 加 `stage`(鏈停在哪一層要驗得出來)。
#: v11(Commit C):`key_drivers[].cluster_id` —— 三大重點要指名它講的
#: 是哪一個事件群。價格變化沒有主詞也沒有動作,它不是事件。
#: v12(Commit D):`asset_net_effects`(方向相反的標的要給淨方向 ——
#: 使用者要的是「合起來是利多還是利空」)、`shared_driver_notes`。
ANALYSIS_SCHEMA_VERSION = 12

#: 立場詞彙沿用 Python 端既有的四個值(`_compute_stance_score`)。
#: 刻意不自創一套 —— 渲染層與「立場一致性」指標都吃這一組,
#: 多一套詞彙會讓比較變成翻譯問題。
STANCE_LABELS = ("偏多", "偏空", "中性", "資料不足")

CLAIM_TYPES = ("fact", "inference", "scenario", "unknown")
DIRECTIONS = ("bullish", "bearish", "neutral")
MATERIALITY = ("high", "medium", "low")
HORIZONS = ("intraday", "1-5d", "1-4w")

#: 影響量級。**`unknown` 是頭等公民,不是逃生口** —— 使用者反映的問題
#: 正是「用形容詞冒充答案」,而誠實說「這則說不出量級」比寫「小幅利多」好。
#: 選 `unknown` 時 `why_this_magnitude` 必須說出**缺哪些資料**(見驗證器)。
MAGNITUDE_BANDS = ("negligible", "small", "moderate", "large", "unknown")

#: 條目之間的關係。**「互相排擠」與「互相加強」是兩件不同的事** ——
#: 兩則新聞搶同一段 CoWoS 產能,合起來的影響小於各自相加;
#: 而 v1 根本沒有欄位表達這件事,於是十條各寫各的,像十個孤島。
RELATIONSHIPS = ("reinforcing", "conflicting",
                 "competing_for_same_capacity", "same_underlying_driver")

#: 因果鏈**走到哪一層**(第十七輪 P1-7)。先前只驗「至少兩步、前後連續」,
#: 於是「事件 → 市場關注提高 → 投資情緒改善」是合法的兩步連續鏈 ——
#: 它沒有走到訂單、稼動率、營收、估值或股價的任何一層。
#: 分層之後,「這條鏈停在哪裡」變成可驗證的事實。
CHAIN_STAGES = ("event", "operations", "industry_supply_demand",
                "revenue", "margin", "earnings", "valuation",
                "positioning", "price", "sentiment")

#: 算「走到財務或價格層」的階段。**`sentiment` 刻意不算** ——
#: 「情緒改善」正是那種讀起來像分析、卻沒有走到任何可驗證後果的終點。
TERMINAL_STAGES = ("revenue", "margin", "earnings", "valuation",
                   "positioning", "price")
#: 算「走到營運/產業層」的階段。
OPERATIONAL_STAGES = ("operations", "industry_supply_demand")


def _obj(props: dict, *, desc: str = "") -> dict:
    """strict 模式的 object:全欄位必填 + 禁止額外欄位。"""
    out = {"type": "object", "properties": props,
           "required": sorted(props), "additionalProperties": False}
    if desc:
        out["description"] = desc
    return out


def _s(desc: str = "") -> dict:
    return {"type": "string", "description": desc} if desc else {"type": "string"}


def _enum(values, desc: str = "") -> dict:
    out = {"type": "string", "enum": list(values)}
    if desc:
        out["description"] = desc
    return out


def _num(desc: str, lo: float = 0.0, hi: float = 1.0) -> dict:
    return {"type": "number", "minimum": lo, "maximum": hi, "description": desc}


def _arr(items: dict, desc: str = "") -> dict:
    out = {"type": "array", "items": items}
    if desc:
        out["description"] = desc
    return out


#: 第十七輪 P2-1:說明仍寫著「source_item_id」,而合法值早已含 `market:`
#: 與 `tension:` —— **模型會照說明走**,typed registry 因此形同虛設。
_EVIDENCE_IDS = _arr(_s(), _ns.schema_description())

_CLAIM = _obj({
    "statement": _s("一句話的主張,不要重述整則新聞"),
    "claim_type": _enum(CLAIM_TYPES, "fact=證據直接陳述;inference=由證據推得;"
                                     "scenario=條件成立才發生;unknown=資料不足"),
    "direction": _enum(DIRECTIONS),
    "materiality": _enum(MATERIALITY, "對本日決策的重要性,不是新聞熱度"),
    "confidence": _num("0–1;資料不足時要降低而不是補故事"),
    "horizon": _enum(HORIZONS),
    "evidence_ids": _EVIDENCE_IDS,
    "counterevidence_ids": _arr(_s(), "反向證據的 ID;沒找到就給空陣列"),
    "falsification_trigger": _s("什麼情況出現就代表這個判斷錯了"),
})

#: **稽核清單那一份多一個 `claim_id`。** 第十八輪:claim audit 先前是孤島
#: —— 它非空且合法,而信裡真正寫出來的立場、已反映/未反映、投資組合影響
#: **沒有任何東西回指它**。於是可以「今日偏多,主因半導體需求強勁」而稽核
#: 裡只有一條「QQQ 昨日上漲」,形式完全合法。
#: `key_drivers` 刻意**不**帶 ID:回指的對象是稽核,不是重點條目 ——
#: 兩份清單各自發 ID 的話,`c1` 會有兩個意思。
_AUDITED_CLAIM = _obj(dict(
    {"claim_id": _s("本則主張的代號(例:c1);各段用 `claim_ids` 回指"),
     # 第十九輪 P1-8:**回指只證明「有連上」,不證明「連對了」。**
     # 立場寫 1-4 週而它靠的主張只談今日盤前,那個回指是形式上的。
     # `asset_scope` 讓「這條主張在講誰」變成可比對的東西 ——
     # 泛稱(市場、大盤、類股)不算標的,那等於沒有指定範圍。
     "asset_scope": _arr(_s(), "這條主張涵蓋哪些標的;整體市場級別寫 "
                               "`market-wide`,否則給代號/指數/ETF")},
    **_CLAIM["properties"]))

#: 第二十輪 P1-5:**「七、昨夜三大重點」是 Email 的第一段,而它先前
#: 完全在 claim 圖之外** —— 讀者最先看到的三條可以與正式稽核矛盾。
#: `claim_ids` 只加在這一份:稽核那一份是**被回指的對象**,不回指別人。
_DRIVER_CLAIM = _obj(dict(
    {"claim_ids": _arr(_s(), "這條重點靠哪幾條 `claim_audit.claim_id` 支撐"),
     # 重構規格 Commit C:**三大重點要指名它講的是哪一件事。**
     # 2026-08-05 那封信的第一段寫的是 QQQ 漲 1.2%、台積電 ADR 跌 0.4%
     # —— 價格變化是別的事件造成的結果,它沒有主詞也沒有動作。
     # 要求指名 `cluster_id` 之後,那三格只能放**事件**(候選由
     # `EVIDENCE.top_events` 給,純價格變化已經整批排除)。
     "cluster_id": _s("這條重點講的是哪一個事件群(`EVIDENCE.top_events."
                      "top_cluster_ids` 裡的 `cluster:<id>`);"
                      "真的不是任何一群才留空,並在 `statement` 說明")},
    **_CLAIM["properties"]))

_SCENARIO = _obj({
    "narrative": _s(),
    "probability": _num("0–1"),
    "triggers": _arr(_s(), "會讓這個情境成立的可觀察條件"),
    # 第二十輪 P1-6:**情境是最前瞻的判斷,先前卻是唯一不用根據的段落。**
    # 「台積電明日可能跌停」配一句「外資情緒轉弱」可以整段進信,
    # 而稽核裡什麼都沒有。
    "claim_ids": _arr(_s(), "這個情境靠哪幾條 `claim_audit.claim_id`"),
})

#: **主分析的完整輸出**。欄位對應信件既有的需求:明確立場、淨分、一句話總結、
#: 台美連動、台積電/大盤/持倉衝擊、關鍵風險與觀察點 —— 一個都不能少,
#: 否則 renderer 產出的信會比現在的少東西。
ANALYSIS_OUTPUT_SCHEMA = _obj({
    "executive_summary": _s("一句話總結,收件人只讀這一句也要拿得到今天的重點"),
    "market_regime": _obj({
        "label": _s("以證據描述當前市場狀態"),
        "evidence_ids": _EVIDENCE_IDS,
    }),
    "stance": _obj({
        "claim_ids": _arr(_s(), "支撐這一段的 `claim_audit.claim_id`"),
        "label": _enum(STANCE_LABELS),
        "score": {"type": "integer", "minimum": -11, "maximum": 11,
                  "description": "與 Python 11 維立場分同尺度;不一致時要在 "
                                 "contradictions 說明理由"},
        "confidence": _num("0–1"),
        "time_horizon": _enum(HORIZONS),
        "rationale": _s(),
    }),
    "key_drivers": _arr(_DRIVER_CLAIM, "今日真正驅動判斷的因子,依 materiality 排序"),
    # 重構規格 Commit D:**同一個標的被不同事件推往相反方向時,
    # 兩段各自寫完就結束了** —— 而使用者要的是「合起來是利多還是利空」。
    "asset_net_effects": _arr(_obj({
        "asset_id": _s("代號或指數(例:2330、0050、TAIEX)"),
        "net_direction": _enum(("bullish", "bearish", "neutral", "unknown"),
                               "**合起來**的方向;抵銷到看不出來就寫 neutral,"
                               "判斷不出來寫 unknown 並說明缺什麼"),
        "net_magnitude_band": _enum(("negligible", "small", "moderate",
                                     "large", "unknown"), "合起來的量級"),
        "offsetting_cluster_ids": _arr(_s(), "互相抵銷的那幾個事件群"),
        "why": _s("為什麼是這個淨方向 —— 哪一邊比較重、憑什麼"),
        "claim_ids": _arr(_s(), "支撐這個淨判斷的 `claim_audit.claim_id`"),
    }), "方向相反的標的要給淨效果;沒有衝突的標的不必列"),
    "scenario_tree": _obj({
        "base": _SCENARIO, "bull": _SCENARIO, "bear": _SCENARIO,
        "invalidation_triggers": _arr(_s(), "整體判斷失效的條件"),
    }),
    "priced_in": _obj({
        "claim_ids": _arr(_s(), "支撐這一段的 `claim_audit.claim_id`"),
        "already_reflected": _arr(_s(), "市場已反映的部分"),
        "not_yet_reflected": _arr(_s(), "尚未反映的部分"),
        # 第十六輪 P2-4:「已反映/未反映」是**高推論性**判斷,
        # 比新聞摘要更需要根據 —— 先前它進信卻不必帶證據。
        "evidence_ids": _EVIDENCE_IDS,
    }),
    "taiwan_market": _obj({
        "summary": _s(), "taiex_view": _s(), "tsmc_view": _s(),
        "evidence_ids": _EVIDENCE_IDS,
    }),
    "global_market": _obj({
        "summary": _s(), "us_to_tw_linkage": _s("美股訊號如何傳導到台股"),
        "evidence_ids": _EVIDENCE_IDS,
    }),
    "portfolio_implications": _obj({
        "claim_ids": _arr(_s(), "支撐這一段的 `claim_audit.claim_id`"),
        "summary": _s("只談曝險方向與風險,不得推測持股明細"),
        "actions_to_consider": _arr(_s()),
        "risks": _arr(_s()),
    }),
    "top_news_analysis": _arr(_obj({
        "source_item_id": _s(),
        "why_it_matters": _s("不要複述標題"),
        "direction": _enum(DIRECTIONS),
        "materiality": _enum(MATERIALITY),
        "persistence": _s("一天的事還是會延續的事"),
        # ---- v2:方向標籤不是分析,下面這些才是 ----
        "mechanism_steps": _arr(_obj({
            "from_what": _s("這一步從什麼開始"),
            "to_what": _s("走到什麼"),
            "channel": _s("透過什麼傳導(製程/封裝/匯率/資本支出/估值…)"),
            "stage": _enum(CHAIN_STAGES,
                           "這一步走到哪一層。**高重要性事件要走到營運層,"
                           "再走到營收/毛利/獲利/估值/籌碼/股價其中之一**;"
                           "只走到 sentiment 等於沒有走到可驗證的後果"),
            "step_type": _enum(CLAIM_TYPES),
            "evidence_ids": _EVIDENCE_IDS,
        }), "事件到股價之間的每一步。**沒有證據的那一步要標成 inference 或 "
            "unknown,不得自稱 fact** —— 那正是「看起來有根據」的來源。"),
        "magnitude_band": _enum(
            MAGNITUDE_BANDS,
            "影響有多大。判斷不出來就選 unknown 並在下一欄說缺什麼資料;"
            "**用形容詞冒充答案是這份報告最常見的失敗**"),
        "why_this_magnitude": _s(
            "為什麼是這個量級。選 unknown 時要寫「缺金額/數量/時程的哪一項」"),
        "horizon": _enum(HORIZONS, "最快什麼時候看得到"),
        # 第二十輪 P2-7:**「有沒有揭露」先前只有 prompt 要求。**
        # 指標量得出「幾成的高重要性分析建立在單一來源上」,卻回答不了
        # 更重要的問題:讀者被告知了嗎?改成結構化欄位,由 renderer
        # 固定呈現 —— 逐字比對只會逼出樣板句。
        "corroboration_assessment": _enum(
            ("official", "multi_source", "single_source", "unverified"),
            "這則事件的佐證等級;以 EVIDENCE 的 `news_clusters[].corroboration` 為準"),
        "source_caveat": _s("單一來源或未證實時要說出讀者該保留什麼;"
                            "多方證實或官方公告寫「無」"),
        "confirmation_signal": _s("什麼出現代表這條真的在走"),
        "invalidation_signal": _s("什麼出現代表這條不成立"),
        # 第十八輪:**同一件事對不同標的的影響不一樣。** 先前每則只有
        # 單一 direction/magnitude/horizon,於是「對台積電中期中度正面、
        # 對台股指數即日可忽略、對成熟製程可能是負面」被壓成一個
        # 「偏多」—— 而那正是使用者說的「泛論」。
        "affected_assets": _arr(_obj({
            "asset_id": _s("個股代號、指數或 ETF(例:2330、TAIEX、00662)"),
            "direction": _enum(DIRECTIONS),
            "magnitude_band": _enum(MAGNITUDE_BANDS),
            "horizon": _enum(HORIZONS),
            "first_order_effect": _s("直接影響:訂單、產能、成本、評價"),
            "second_order_effect": _s("次級影響;想不到就寫「本報看不出次級影響」"),
            "evidence_ids": _EVIDENCE_IDS,
        }), "高重要性事件至少要拆出一個標的"),
        "relates_to": _arr(_obj({
            "other_source_item_id": _s("今天另一則的 source_item_id"),
            "relationship": _enum(RELATIONSHIPS),
            "evidence_ids": _EVIDENCE_IDS,
            "explanation": _s(),
        }), "與今天其他條目的關係。**沒有根據就不要硬湊** —— 空陣列是"
            "完全合法的答案,而編造的關聯比沒有關聯更糟。"),
    })),
    # v2:**橫向問題要有自己的地方**,不能全部塞進 stance rationale 的兩三句。
    "cross_market_synthesis": _obj({
        "reinforcing_signals": _arr(_s(), "今天互相強化的訊號"),
        "conflicting_signals": _arr(
            _s(), "互相抵銷的訊號。**確實沒有衝突時要明講,不得留空敷衍**"),
        "dominant_driver": _s("今天真正的主導因子"),
        "why_it_dominates": _s("為什麼是它而不是別的"),
        "net_effect_intraday": _s("即日的淨效果"),
        "net_effect_next_days": _s("未來 1–5 日的淨效果;與即日不同時要說為什麼"),
        "funds_moving_from": _arr(_s(), "資金從哪些地方出來"),
        "funds_moving_to": _arr(_s(), "資金往哪些地方去"),
        "what_would_flip_it": _s("什麼情況會讓主導因子失效"),
        # 第十七輪 P1-3:**點名不等於處理。** v3 只回填一串 ID,而
        # `conflicting_signals` 是自由文字 —— 驗證器確認得了「ID 都列到」,
        # 確認不了「哪一段文字處理哪一筆、怎麼調和、憑什麼判斷哪邊可信」。
        # 改成**一對一的結構**,每一筆張力自己帶調和方式與判準。
        # 第十八輪 P1-7:**橫向先前只嚴格處理矛盾。** 同向訊號放在自由
        # 文字裡,於是「哪些同向訊號共同構成主導因子」與「有沒有把同一個
        # 底層訊號重複計權」都驗不了 —— 而重複計權正是立場分虛高的來源。
        "alignment_readings": _arr(_obj({
            "alignment_id": _s("EVIDENCE 的 `tension:*`(kind=alignment 的那些)"),
            "interpretation": _s("兩個同向訊號合起來說明什麼"),
            "marginal_information": _s("第二個訊號**多告訴了你什麼**;"
                                       "沒有就寫「沒有增量」"),
            "double_count_risk": _s("兩者會不會其實是同一個底層驅動"),
            "evidence_ids": _EVIDENCE_IDS,
        }), "同向訊號的解讀"),
        # Commit D:**共用底層驅動的事件不是獨立確認。** 就業數據 →
        # 降息預期 → 殖利率是同一件事的三個表現;三段各加一次權重,
        # 讀者看到的是「三個獨立訊號同向」。
        "shared_driver_notes": _arr(_obj({
            "driver": _s("`EVIDENCE.event_graph.shared_driver_groups[].driver`"),
            "cluster_ids": _arr(_s(), "共用這個驅動的事件群"),
            "why_not_double_counted": _s("為什麼把它們一起看仍然成立 ——"
                                         "例如只計一次、或它們其實是"
                                         "傳導鏈上可分辨的兩段"),
        }), "共用底層驅動的事件群怎麼處理(沒有就給空陣列)"),
        "tension_resolutions": _arr(_obj({
            "tension_id": _s("EVIDENCE.signal_tensions 的 `tension:<id>`"),
            "resolution": _s("兩邊怎麼調和;不得只複述兩個數字"),
            "dominant_side": _enum(
                ("left", "right", "neither"),
                "今天哪一側比較可信;真的分不出就選 neither 並在 why 說明"),
            "why": _s("憑什麼是這一側 —— 時間尺度、資料新鮮度、或部位性質"),
            "decision_rule": _s("什麼情況會分出勝負(可觀察的條件)"),
            "evidence_ids": _EVIDENCE_IDS,
        }), "**每一筆 kind=tension 都要有自己的一項** —— 沒有的那筆等於沒處理"),
        "evidence_ids": _EVIDENCE_IDS,
    }, desc="橫向綜合:訊號之間的關係,不是把各市場各寫一句"),
    "contradictions": _arr(_obj({
        "topic": _s(),
        "supporting_ids": _EVIDENCE_IDS,
        "opposing_ids": _EVIDENCE_IDS,
        "resolution": _s("如何調和;不得只採一邊"),
    })),
    # 第十九輪 P1-8:**最可能被單獨閱讀的那一段先前完全脫離稽核。**
    # `executive_summary` 是字串(既有後處理靠它),所以回指放在頂層 ——
    # 攤平而不是再包一層(schema 深度已貼齊 strict 上限)。
    "executive_summary_claim_ids": _arr(
        _s(), "總結那一句靠哪幾條 `claim_audit.claim_id`"),
    "data_gaps": _arr(_obj({
        # 第十八輪 P1-8:**缺口要能對得上是哪一項。** 先前規則只是
        # 「skipped 非空 → data_gaps 不能全空」,於是一筆完全無關的缺口
        # (「缺某公司的資本支出金額」)就能替今天所有跑不成的橫向檢查過關。
        "gap_id": _s("本報給的缺口代號(`gap:*`);自己發現的缺口填 `gap:other`"),
        "what_is_missing": _s(),
        "impact_on_conclusions": _s(),
    }), "資料不足要說出來,不得用模糊語句掩蓋"),
    # 第十八輪 P1-3:**靜默略過與判斷不重要,在信裡長得一模一樣。**
    # 模型可以主張本報列為必分析的事件今天不值得談,但要說出為什麼。
    "dismissed_events": _arr(_obj({
        "cluster_id": _s("EVIDENCE 的 `news_clusters.required_cluster_ids` 之一"),
        "why_not_material": _s("為什麼今天不值得分析 —— 不得只寫「影響有限」"),
        # 第二十輪 P2-2:套語偵測靠字面,一個修飾詞就繞過 ——
        # **機械化的判準是這兩格**:引用你駁回的那則新聞本身
        # (證明你看過它),以及說得出什麼情況要回頭看它。
        "supporting_evidence_ids": _EVIDENCE_IDS,
        "revisit_trigger": _s("什麼情況出現,這個駁回就不成立"),
    }), "本報要求分析而你決定不談的事件"),
    "watch_triggers": _arr(_obj({
        "trigger": _s(),
        "why": _s(),
        "horizon": _enum(HORIZONS),
        "claim_ids": _arr(_s(), "為什麼要盯它 —— 靠哪幾條主張"),
    })),
    "claim_audit": _arr(_AUDITED_CLAIM,
                        "所有重大 claim 的稽核清單;評分以此為準"),
}, desc=f"晨報主分析 v{ANALYSIS_SCHEMA_VERSION}")


def response_format(name: str = "morning_analysis") -> dict:
    """Responses API 的 `text.format`(官方 strict 形狀,2026-08-01 查證)。"""
    return {"type": "json_schema", "name": name,
            "schema": ANALYSIS_OUTPUT_SCHEMA, "strict": True}


def chat_completions_response_format(name: str = "morning_analysis") -> dict:
    """Chat Completions 的 `response_format.json_schema`(備援路徑用)。

    兩個端點的**包法不同**:Responses 是 `{type, name, schema, strict}`,
    Chat Completions 是 `{type:"json_schema", json_schema:{name, schema, strict}}`。
    包錯的症狀是 400,而 400 在這條路徑上等於整份分析作廢。
    """
    return {"name": name, "schema": ANALYSIS_OUTPUT_SCHEMA, "strict": True}


# ---------------------------------------------------------------- 驗證

#: strict 模式保證得了形狀,保證不了**內容的可稽核性**。
#: 這些是 API 管不到、但十天實驗要用來評分的東西。

# ---------------------------------------------------------------- 相容出口
#
# `validate` 搬到 `analysis_validate`(見該檔的說明:形狀 / 根據 / 引用
# 是三件事)。生產與測試都用 `analysis_schema.validate` 呼叫它,
# **改呼叫端不是這次要改的東西** —— 一次改一件事,搬動才證明得了只換位置。
from analysis_validate import validate            # noqa: E402,F401
