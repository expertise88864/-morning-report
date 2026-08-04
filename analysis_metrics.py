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

#: v2(2026-08-04):加 `prose_depth` —— 量「有多少是方向形容詞、
#: 有多少說得出量級」。舊列沒有這一格,不可與新列直接比。
#: v3(第十七輪 P1-9):**指標先前用 ID 集合驗證,看不到 packet-aware 規則**
#: (張力有沒有處理完、有新聞卻沒分析),於是帳本可能顯示
#: `validation_problems = 0` 而實際橫向沒做完。同時補上深度指標 ——
#: 十配對要回答的是「深度有沒有真的改善」,而先前量不到。
METRICS_SCHEMA_VERSION = 6

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
    # 第二十輪 P2-4:**Reuters 不是主管機關。** 先前 official 把兩者混成
    # 一格 —— dashboard 的「官方來源覆蓋」被 A 級媒體灌高,而「官方公告
    # 被漏掉」正是這個指標存在的理由。分群那側已經拆了,這裡跟上。
    official = [n for n in news if n.get("official")]
    grade_a = [n for n in news if not n.get("official")
               and n.get("source_grade") == "A"]
    off_cov = [n for n in official if _hit(n)]
    a_cov = [n for n in grade_a if _hit(n)]
    return {
        "items": len(news), "covered": len(covered),
        "rate": round(len(covered) / len(news), 3),
        "official_items": len(official), "official_covered": len(off_cov),
        "official_rate": (round(len(off_cov) / len(official), 3)
                          if official else None),
        "grade_a_items": len(grade_a), "grade_a_covered": len(a_cov),
        "grade_a_rate": (round(len(a_cov) / len(grade_a), 3)
                         if grade_a else None),
    }


def source_diversity(text: str, packet: dict) -> dict:
    """談到了幾個不同的來源。單一來源撐起整份分析是一種風險。"""
    body = text or ""
    sources = {str(n.get("source") or "") for n in ((packet or {}).get("news") or [])}
    sources.discard("")
    mentioned = {s for s in sources if s in body}
    return {"sources_available": len(sources), "sources_mentioned": len(mentioned),
            "rate": round(len(mentioned) / len(sources), 3) if sources else None}


#: 用來冒充「影響分析」的方向形容詞。**它們是標籤,不是答案。**
#: 2026-08-04 實測:八段 10 條有 10 條以這類詞作結、0 條說得出量級 ——
#: 使用者三次反映「只是在堆疊數據」,而每次都要靠人讀信才判斷得出來。
#: 量出來才知道下一版有沒有真的變好(這個 repo 栽過「改了但生產沒產出」)。
DIRECTION_LABELS = ("小幅利多", "小幅正面", "間接正面", "間接小幅正面",
                    "影響有限", "傳導有限", "暫中性", "中性偏正", "中性偏多",
                    "僅方向性利多", "方向性利多", "有帶動作用")

#: 「說得出量級」的證據:帶單位的數字。純方向詞沒有單位。
_MAGNITUDE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|個百分點|億|萬|倍|奈米|nm|口|張|噸|美元|元)")

#: 「說得出時間」的證據。
_HORIZON = ("今日", "本季", "下半年", "上半年", "明年", "第一季", "第二季",
            "第三季", "第四季", "月底", "年底", "週內", "數週", "數月")

#: 條目之間有關係的證據(互相排擠 / 同一件事的兩面 / 方向相反)。
_LINKAGE = ("與上一條", "與前一條", "同一段", "同一條線", "互相排擠",
            "方向相反", "兩者相加", "指向同一", "牴觸", "同向")


def prose_depth(text: str) -> dict:
    """**這份分析有多少是標籤、多少是真的說出了量級。**

    刻意只回可數的東西,不回一個綜合分數:分數會被拿來當「品質」,
    而這幾個計數各自回答不同的問題,合成之後就分不出是哪一項退步了。

    這是**觀測指標,不是門檻** —— 不擋任何東西,也不進立場計分。
    """
    t = str(text or "")
    lines = [ln.strip() for ln in t.splitlines() if len(ln.strip()) >= 20]
    labelled = sum(1 for ln in lines if any(w in ln for w in DIRECTION_LABELS))
    return {
        "lines_seen": len(lines),
        "lines_with_direction_label": labelled,
        "lines_with_magnitude": sum(1 for ln in lines if _MAGNITUDE.search(ln)),
        "lines_with_horizon": sum(
            1 for ln in lines if any(w in ln for w in _HORIZON)),
        "cross_item_links": sum(
            1 for ln in lines if any(w in ln for w in _LINKAGE)),
        # 誠實承認量級判斷不出來,**比寫一個形容詞好** —— 分開數,
        # 否則「說不出量級」會跟「用形容詞打發」混成同一格。
        "lines_admitting_no_magnitude": t.count("說不出量級"),
    }


def text_metrics(text: str, packet: dict, *, stance: Optional[dict] = None) -> dict:
    """**兩邊都算得出來的指標。只有這一類可以直接對比。**"""
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "chars": len(text or ""),
        "numeric_consistency": numeric_consistency(text, packet),
        "evidence_coverage": evidence_coverage(text, packet),
        "source_diversity": source_diversity(text, packet),
        "stance": dict(stance or {}),
        # 2026-08-04:量「有多少是標籤、有多少說得出量級」。
        "prose_depth": prose_depth(text),
    }


# ---------------------------------------------------------------- 僅 Luna

#: 信件需要的段落。缺一塊 renderer 就少排一塊,而那不會有任何錯誤訊息。
#: 第十七輪 P1-9:`cross_market_synthesis` 與 `priced_in` 先前不在這裡 ——
#: 於是「橫向綜合完全消失」的那天,完整度仍然是 100%。
REQUIRED_SECTIONS = (
    "executive_summary", "stance", "key_drivers", "scenario_tree",
    "taiwan_market", "global_market", "portfolio_implications",
    "top_news_analysis", "data_gaps", "watch_triggers", "claim_audit",
    "cross_market_synthesis", "priced_in",
)


#: **每個段落自己的「有內容」判準**(第十八輪:false green)。
#: 先前一律用 `not obj.get(s)`,於是:
#:   * `data_gaps=[]` —— 證據完整的日子**合法**,卻被算成缺一段;
#:   * `priced_in={}` 內部全空 —— dict 本身是 truthy,算成有內容。
#: 兩個方向都錯,而且錯的方向相反:好報告被扣分、空報告被放行。
def _has_priced_in(v) -> bool:
    v = v if isinstance(v, dict) else {}
    return bool((v.get("already_reflected") or []) or
                (v.get("not_yet_reflected") or []))


def _has_cms(v) -> bool:
    v = v if isinstance(v, dict) else {}
    return bool(str(v.get("dominant_driver") or "").strip()
                or (v.get("tension_resolutions") or [])
                or str(v.get("net_effect_intraday") or "").strip())


#: `data_gaps` 是**允許合法為空**的那一段 —— 空代表「今天沒有缺口」,
#: 不代表「沒寫」。把它算成 missing,等於懲罰資料完整的日子。
_PRESENCE = {
    "data_gaps": lambda v: True,
    "priced_in": _has_priced_in,
    "cross_market_synthesis": _has_cms,
    "claim_audit": lambda v: bool([c for c in (v or []) if isinstance(c, dict)]),
}


def _present(obj: dict, section: str) -> bool:
    return _PRESENCE.get(section, bool)(obj.get(section))


def _claims(obj: dict) -> list:
    return [c for c in (obj.get("claim_audit") or []) if isinstance(c, dict)]


def structured_metrics(obj: Optional[dict], packet: dict,
                       rendered_text: str = "") -> dict:
    """**只有 Luna 有。** 回答「這條路徑健不健康」,不是「比 DeepSeek 好」。

    把這些數字拿去和 DeepSeek 比,比的是有沒有結構,不是模型能力 ——
    所以本模組刻意不提供把 A、B 兩類合成單一分數的函式。
    """
    if not isinstance(obj, dict):
        return {"schema_version": METRICS_SCHEMA_VERSION, "parsed": False}

    # **傳 packet 不是 ID 集合**(第十七輪 P1-9):傳集合的話,
    # 「張力沒處理完」「有新聞卻沒分析」這些規則在指標裡整個不會跑,
    # 帳本會顯示 validation_problems=0 而實際橫向沒做完。
    import analysis_depth as _ad
    import quality_metrics as _qm
    problems = _sch.validate(obj, packet)
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

    missing = [s for s in REQUIRED_SECTIONS if not _present(obj, s)]
    # `data_gaps` 空著**不一定**是失誤 —— 證據齊全的那天本來就沒有缺口。
    # 但「證據被截斷了卻說沒有缺口」是失誤,那個要抓。
    truncated = int(((packet or {}).get("truncation") or {}).get("news_dropped") or 0)
    gaps = obj.get("data_gaps") or []
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "parsed": True,
        # 第十七輪 P1-9:深度指標。**十配對要回答的正是這個**,
        # 而先前完全量不到 —— 帳本可以顯示 100% 完整而橫向沒做完、
        # 因果鏈只走到「情緒改善」。
        "depth": _ad.depth_metrics(obj, packet),
        # 第十九輪 P2-3:**存在性指標量不到「有沒有真的做到」。**
        # 駁回也算 covered、一個實體覆蓋一整天、合法但不相關的證據算
        # grounded、`asset_id="市場"` 算逐標的分析 —— 四種 false green
        # 都會讓 dashboard 接近 100% 而信裡仍然只有「偏多、情緒改善」。
        # 第二十輪 P1-3:**rendered text 不傳進來,事件指紋覆蓋率永遠是 0**
        # —— 無論信裡分析得多完整,十配對的帳本都會顯示 Luna 的事件覆蓋
        # 失敗。指標接錯線與指標不存在,對判讀的人是同一件事。
        "quality": _qm.quality_metrics(obj, packet, text=rendered_text),
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
