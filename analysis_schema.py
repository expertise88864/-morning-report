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

#: 輸出契約版本。**改欄位就要進版** —— cohort 以它為身分的一部分,
#: 悄悄改欄位等於把不同定義的樣本混進同一個平均。
ANALYSIS_SCHEMA_VERSION = 1

#: 立場詞彙沿用 Python 端既有的四個值(`_compute_stance_score`)。
#: 刻意不自創一套 —— 渲染層與「立場一致性」指標都吃這一組,
#: 多一套詞彙會讓比較變成翻譯問題。
STANCE_LABELS = ("偏多", "偏空", "中性", "資料不足")

CLAIM_TYPES = ("fact", "inference", "scenario", "unknown")
DIRECTIONS = ("bullish", "bearish", "neutral")
MATERIALITY = ("high", "medium", "low")
HORIZONS = ("intraday", "1-5d", "1-4w")


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


_EVIDENCE_IDS = _arr(_s(), "EvidencePacket 裡的 source_item_id;沒有就給空陣列")

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

_SCENARIO = _obj({
    "narrative": _s(),
    "probability": _num("0–1"),
    "triggers": _arr(_s(), "會讓這個情境成立的可觀察條件"),
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
        "label": _enum(STANCE_LABELS),
        "score": {"type": "integer", "minimum": -11, "maximum": 11,
                  "description": "與 Python 11 維立場分同尺度;不一致時要在 "
                                 "contradictions 說明理由"},
        "confidence": _num("0–1"),
        "time_horizon": _enum(HORIZONS),
        "rationale": _s(),
    }),
    "key_drivers": _arr(_CLAIM, "今日真正驅動判斷的因子,依 materiality 排序"),
    "scenario_tree": _obj({
        "base": _SCENARIO, "bull": _SCENARIO, "bear": _SCENARIO,
        "invalidation_triggers": _arr(_s(), "整體判斷失效的條件"),
    }),
    "priced_in": _obj({
        "already_reflected": _arr(_s(), "市場已反映的部分"),
        "not_yet_reflected": _arr(_s(), "尚未反映的部分"),
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
    })),
    "contradictions": _arr(_obj({
        "topic": _s(),
        "supporting_ids": _EVIDENCE_IDS,
        "opposing_ids": _EVIDENCE_IDS,
        "resolution": _s("如何調和;不得只採一邊"),
    })),
    "data_gaps": _arr(_obj({
        "what_is_missing": _s(),
        "impact_on_conclusions": _s(),
    }), "資料不足要說出來,不得用模糊語句掩蓋"),
    "watch_triggers": _arr(_obj({
        "trigger": _s(),
        "why": _s(),
        "horizon": _enum(HORIZONS),
    })),
    "claim_audit": _arr(_CLAIM, "所有重大 claim 的稽核清單;評分以此為準"),
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
def validate(obj, evidence_ids) -> list:
    """回傳問題清單(空 = 通過)。**不拋例外**:呼叫端決定要修還是降級。

    只驗「schema 管不到」的:
      - 證據 ID 是否真的存在於本日 packet(**編造的 ID 比沒有 ID 更危險**,
        它看起來有根據)
      - 高重要性的 fact/inference 有沒有帶證據
      - 立場詞彙是否合法
    """
    problems: list = []
    if not isinstance(obj, dict):
        return ["輸出不是 JSON 物件"]
    known = set(evidence_ids or ())

    def _check_ids(ids, where):
        for i in (ids or []):
            if str(i) not in known:
                problems.append(f"{where} 引用了不存在的證據 ID:{i!r}")

    for i, c in enumerate(obj.get("claim_audit") or []):
        if not isinstance(c, dict):
            problems.append(f"claim_audit[{i}] 不是物件")
            continue
        _check_ids(c.get("evidence_ids"), f"claim_audit[{i}]")
        _check_ids(c.get("counterevidence_ids"), f"claim_audit[{i}] 的反證")
        if (c.get("materiality") == "high"
                and c.get("claim_type") in ("fact", "inference")
                and not (c.get("evidence_ids") or [])):
            problems.append(
                f"claim_audit[{i}] 是高重要性的 {c.get('claim_type')},"
                "卻沒有任何支持證據")
    for i, d in enumerate(obj.get("key_drivers") or []):
        if isinstance(d, dict):
            _check_ids(d.get("evidence_ids"), f"key_drivers[{i}]")
    for i, n in enumerate(obj.get("top_news_analysis") or []):
        if isinstance(n, dict):
            _check_ids([n.get("source_item_id")], f"top_news_analysis[{i}]")

    label = ((obj.get("stance") or {}) if isinstance(obj.get("stance"), dict)
             else {}).get("label")
    if label is not None and label not in STANCE_LABELS:
        problems.append(f"立場詞彙不合法:{label!r}")
    return problems
