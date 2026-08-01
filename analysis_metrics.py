# -*- coding: utf-8 -*-
"""**確定性品質指標**(Phase 6)。

## 這個模組存在的理由與它的界線

指令書明說:「不得用文章長度、body overlap 或單純 stance agreement 當作
『更好』」。同意 —— 但把指標寫出來之前,得先承認一件會讓整個比較失效的事:

    **Luna 產出結構化 JSON,DeepSeek legacy 產出 Markdown。**

所以「schema 合規率」「claim 帶證據的比例」「矛盾數」這類指標,在 DeepSeek 側
**根本算不出來**。把它們混進一個綜合分數,等於讓 Luna 在一堆它獨有的欄位上
自動全勝 —— 那不是模型比較,那是「有結構 vs 沒結構」。

因此本模組把指標分成兩類,而且**刻意不提供把兩類合併成單一分數的函式**:

  A. `text_metrics` —— 兩邊都算得出來(數字一致性、證據涵蓋、來源多樣性、
     立場、長度、成本、延遲)。**只有這一類可以直接對比。**
  B. `structured_metrics` —— 只有 Luna 有(schema 合規、claim 稽核、
     資料缺口誠實度)。它回答的是「Luna 這條路徑本身健不健康」,
     不是「Luna 比 DeepSeek 好」。

## 數字一致性的已知誤判

`numeric_consistency` 抓出文字裡的數字,看它在不在證據裡。模型合法地會算
衍生數字(百分比、差值、加總),那些會被誤判成「未出現在證據」。所以它
回報的是**比率與樣本**,不是「錯誤數」,而且門檻由人判讀。
把一個有已知誤判的指標當成硬性判準,比沒有指標更糟。
"""
from __future__ import annotations

import re
from typing import Optional

import analysis_schema as _sch
import evidence_packet as _ep

METRICS_SCHEMA_VERSION = 1

#: 抓數字用。刻意包含千分位與小數,排除純年份(2026 這種會製造大量誤判)。
_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+\.\d+|\d+)(?![\w])")

#: 這些數字太常見,出現在任何文字裡都不具鑑別力,計算一致性時忽略。
#: (年份、月份、常見序數、百分比的 0/100)
_TRIVIAL = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
            "100", "2024", "2025", "2026", "2027"}


def _numbers(text: str) -> list:
    return [m.group(1).replace(",", "") for m in _NUM.finditer(text or "")]


def _evidence_numbers(packet: dict) -> set:
    """證據裡出現過的所有數字(含 JSON 內的數值)。"""
    blob = _ep.canonical_json(packet or {})
    return set(_numbers(blob))


def numeric_consistency(text: str, packet: dict) -> dict:
    """文字裡的數字有多少能在證據裡找到。

    **這是有已知誤判的指標。** 模型合法地會算衍生數字(百分比、差值),
    那些不會出現在證據裡卻不是錯的。所以回報比率與未命中清單供人判讀,
    不回報「錯誤數」—— 把有誤判的指標當硬性判準比沒有指標更糟。
    """
    seen = [n for n in _numbers(text) if n not in _TRIVIAL]
    if not seen:
        return {"checked": 0, "matched": 0, "rate": None, "unmatched": []}
    known = _evidence_numbers(packet)
    unmatched = [n for n in seen if n not in known]
    return {"checked": len(seen), "matched": len(seen) - len(unmatched),
            "rate": round((len(seen) - len(unmatched)) / len(seen), 3),
            "unmatched": sorted(set(unmatched))[:20]}


def evidence_coverage(text: str, packet: dict) -> dict:
    """高重要性證據被談到了多少。

    判準是**實體與標題關鍵字有沒有出現在文字裡**,不是 claim 有沒有引用 ID
    —— 後者 DeepSeek 側根本沒有,用它比較就是在比格式。

    `official_covered` 特別重要:官方來源(央行、公報、MOPS)被漏掉,
    是這類報告最實質的失誤。
    """
    body = text or ""
    news = (packet or {}).get("news") or []
    if not news:
        return {"items": 0, "covered": 0, "rate": None,
                "official_items": 0, "official_covered": 0}

    def _hit(item) -> bool:
        for ent in (item.get("entities") or []):
            if ent and str(ent) in body:
                return True
        title = str(item.get("title") or "")
        # 標題的前 8 個字當指紋:整句比對幾乎不會命中(模型會改寫),
        # 太短又會誤命中。
        return bool(title) and title[:8] in body

    covered = [n for n in news if _hit(n)]
    official = [n for n in news if n.get("official") or n.get("source_grade") == "A"]
    off_cov = [n for n in official if _hit(n)]
    return {
        "items": len(news), "covered": len(covered),
        "rate": round(len(covered) / len(news), 3),
        "official_items": len(official), "official_covered": len(off_cov),
        "official_rate": (round(len(off_cov) / len(official), 3)
                          if official else None),
    }


def source_diversity(text: str, packet: dict) -> dict:
    """談到了幾個不同的來源。單一來源撐起整份分析是一種風險。"""
    body = text or ""
    sources = {str(n.get("source") or "") for n in ((packet or {}).get("news") or [])}
    sources.discard("")
    mentioned = {s for s in sources if s in body}
    return {"sources_available": len(sources), "sources_mentioned": len(mentioned),
            "rate": round(len(mentioned) / len(sources), 3) if sources else None}


def text_metrics(text: str, packet: dict, *, stance: Optional[dict] = None) -> dict:
    """**兩邊都算得出來的指標。只有這一類可以直接對比。**"""
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "chars": len(text or ""),
        "numeric_consistency": numeric_consistency(text, packet),
        "evidence_coverage": evidence_coverage(text, packet),
        "source_diversity": source_diversity(text, packet),
        "stance": dict(stance or {}),
    }


# ---------------------------------------------------------------- 僅 Luna

#: 信件需要的段落。缺一塊 renderer 就少排一塊,而那不會有任何錯誤訊息。
REQUIRED_SECTIONS = (
    "executive_summary", "stance", "key_drivers", "scenario_tree",
    "taiwan_market", "global_market", "portfolio_implications",
    "top_news_analysis", "data_gaps", "watch_triggers", "claim_audit",
)


def _claims(obj: dict) -> list:
    return [c for c in (obj.get("claim_audit") or []) if isinstance(c, dict)]


def structured_metrics(obj: Optional[dict], packet: dict) -> dict:
    """**只有 Luna 有。** 回答「這條路徑健不健康」,不是「比 DeepSeek 好」。

    把這些數字拿去和 DeepSeek 比,比的是有沒有結構,不是模型能力 ——
    所以本模組刻意不提供把 A、B 兩類合成單一分數的函式。
    """
    if not isinstance(obj, dict):
        return {"schema_version": METRICS_SCHEMA_VERSION, "parsed": False}

    ids = _ep.evidence_ids(packet)
    problems = _sch.validate(obj, ids)
    claims = _claims(obj)
    material = [c for c in claims if c.get("materiality") == "high"]
    factual = [c for c in claims if c.get("claim_type") in ("fact", "inference")]
    with_ev = [c for c in factual if (c.get("evidence_ids") or [])]
    unsupported_critical = [
        c for c in material
        if c.get("claim_type") in ("fact", "inference")
        and not (c.get("evidence_ids") or [])]
    with_falsifier = [c for c in claims
                      if str(c.get("falsification_trigger") or "").strip()]
    types = {}
    for c in claims:
        t = str(c.get("claim_type") or "?")
        types[t] = types.get(t, 0) + 1

    missing = [s for s in REQUIRED_SECTIONS if not obj.get(s)]
    # `data_gaps` 空著**不一定**是失誤 —— 證據齊全的那天本來就沒有缺口。
    # 但「證據被截斷了卻說沒有缺口」是失誤,那個要抓。
    truncated = int(((packet or {}).get("truncation") or {}).get("news_dropped") or 0)
    gaps = obj.get("data_gaps") or []
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "parsed": True,
        "validation_problems": len(problems),
        "validation_detail": problems[:10],
        "sections_required": len(REQUIRED_SECTIONS),
        "sections_present": len(REQUIRED_SECTIONS) - len(missing),
        "sections_missing": missing,
        "completeness_rate": round(
            (len(REQUIRED_SECTIONS) - len(missing)) / len(REQUIRED_SECTIONS), 3),
        "claims": len(claims),
        "claims_high_materiality": len(material),
        "evidence_supported_rate": (round(len(with_ev) / len(factual), 3)
                                    if factual else None),
        "unsupported_critical_claims": len(unsupported_critical),
        "falsifiable_rate": (round(len(with_falsifier) / len(claims), 3)
                             if claims else None),
        "claim_type_mix": dict(sorted(types.items())),
        "contradictions": len(obj.get("contradictions") or []),
        "data_gaps": len(gaps),
        "data_gap_honesty_flag": bool(truncated and not gaps),
        "duplicate_claim_rate": _duplicate_rate(claims),
    }


def _duplicate_rate(claims: list) -> Optional[float]:
    """重複主張的比例。同一件事講三次會讓報告看起來很長而沒有更多資訊。"""
    if not claims:
        return None
    seen = [re.sub(r"\s+", "", str(c.get("statement") or ""))[:40] for c in claims]
    seen = [s for s in seen if s]
    if not seen:
        return None
    return round(1 - len(set(seen)) / len(seen), 3)


# ---------------------------------------------------------------- 成本效益

def cost_effectiveness(cost_usd: Optional[float], accepted: bool,
                       structured: Optional[dict] = None) -> dict:
    """每封信、每個**有證據支持的重大主張**的成本。

    只看總額會讓「便宜但空洞」贏 —— 而那正是換模型最不想要的結果。
    DeepSeek 側算不出分母(它沒有 claim 稽核),那時只回每封成本並明說。
    """
    out = {"cost_usd": cost_usd, "accepted": bool(accepted),
           "cost_per_accepted_report": cost_usd if accepted else None}
    st = structured or {}
    n = st.get("claims_high_materiality")
    unsupported = st.get("unsupported_critical_claims")
    if cost_usd is not None and isinstance(n, int) and isinstance(unsupported, int):
        supported = max(0, n - unsupported)
        out["supported_material_claims"] = supported
        out["cost_per_supported_material_claim"] = (
            round(cost_usd / supported, 6) if supported else None)
    else:
        out["cost_per_supported_material_claim"] = None
        out["basis"] = "此 profile 沒有結構化 claim 稽核,分母不可得"
    return out


# ---------------------------------------------------------------- 人工盲評

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
