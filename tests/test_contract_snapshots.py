# -*- coding: utf-8 -*-
"""**同群鍵裡的每個版本號,都要對得上一份凍結的行為**(第十二輪 P2-2)。

## 問題

`COHORT_FIELDS` 用「語意契約版本」判定兩天的樣本可不可以相加 ——
那個方向是對的(git SHA 會讓一個 README commit 把十筆樣本清零)。
但那些版本號**全部靠人工維護**:改了 developer instructions、renderer 的
段落邏輯、stance 抽取、證據截斷或修補提示,卻忘了升版,樣本照樣被算進
同一群。每筆有 `prompt_sha` 可供事後追查,但**自動排除不會發生**,
而判讀的人只會看到一個混了兩種契約的平均值。

## 這個檔的做法

不是改用檔案雜湊 —— `llm_experiment` 的註解已經說明為什麼不:那會讓
「改一個註解」變成「換一個系統」。改成**凍結可觀測行為**:

    每個版本號 → 一份固定輸入下的輸出雜湊

內容變了、版本沒變 → 紅(該升版)。
版本變了、內容沒變 → 也紅(要嘛是誤升,要嘛是忘了更新這裡)。

這與本 repo 既有的 `test_deepseek_legacy_golden` 同一個做法,而那個檔已經
證明它抓得到東西。**雜湊不是「不准改」**:改是可以的,但要是一個刻意的、
看得見的動作 —— 升版號、更新這裡的常數、在 commit 說明改了什麼。

## 涵蓋範圍不是我挑的

應該有快照的版本欄位**從 `COHORT_FIELDS` 推導**,不是手寫一份清單 ——
否則新增一個版本欄位時,漏掉它不會有任何人發現(而漏掉的症狀正是這條
finding 描述的那種:混群而不自知)。
"""
import hashlib
import json

import analysis_render as ar
import analysis_schema as sch
import evidence_packet as ep
import llm_experiment as lx
import llm_postprocess as lp
import prompt_profiles as pp

# ---------------------------------------------------------------- 固定輸入

_QUOTES = {"^TWII": {"close": 23000.0, "change_pct": 0.8},
           "QQQ": {"close": 500.0, "change_pct": 1.2}}
_FAIR = {"fair_value": 22500.0}
_PRED = {"model1": 23100.0}
#: `n1` 的摘要與全文**刻意寫得夠長**:證據契約管的一部分是截斷長度
#: (`MAX_SUMMARY_CHARS` / `MAX_FULLTEXT_CHARS`),而輸入短於門檻時
#: 那些常數怎麼改都不會反映在快照上 —— 那條判準就成了真空通過。
_LONG = "台積電先進製程需求維持強勁,CoWoS 產能持續吃緊,客戶追加訂單。" * 40
_NEWS = [{"source_item_id": "n1", "title": "費城半導體指數收漲 2.1%",
          "summary": _LONG, "fulltext": _LONG * 2, "source": "Reuters",
          "entities": ["費半"], "published_at": "2026-08-02T20:00:00+08:00"},
         {"source_item_id": "n2", "title": "央行理監事會維持利率不變",
          "summary": "利率按兵不動。", "source": "中央銀行",
          "entities": ["央行"], "official": True,
          "published_at": "2026-08-02T16:00:00+08:00"}]

_ANALYSIS = {
    "executive_summary": "今日偏多,留意台積電法說。",
    "stance": {"label": "偏多", "score": 6, "confidence": 0.7,
               "time_horizon": "1-5d", "rationale": "多數訊號同向。"},
    "key_drivers": [{"statement": "費半走強", "claim_type": "fact",
                     "direction": "bullish", "materiality": "high",
                     "confidence": 0.8, "horizon": "intraday",
                     "evidence_ids": ["n1"], "counterevidence_ids": [],
                     "falsification_trigger": "夜盤翻黑"}],
    "taiwan_market": {"summary": "量能回升。", "taiex_view": "偏多",
                      "tsmc_view": "守月線", "evidence_ids": ["n1"]},
    "global_market": {"summary": "美股收紅。", "us_to_tw_linkage": "費半傳導",
                      "evidence_ids": ["n1"]},
    "top_news_analysis": [{"source_item_id": "n2", "why_it_matters": "利率",
                           "affected": ["金融股"]}],
    "claim_audit": [{"claim_id": "c1", "statement": "費半走強",
                     "claim_type": "fact", "materiality": "high",
                     "evidence_ids": ["n1"], "counterevidence_ids": [],
                     "falsification_trigger": "夜盤翻黑"}],
    "market_regime": {"label": "偏多", "evidence_ids": ["n1"]},
}

#: 這段文字要**分辨得出後處理的行為**,不只是「跑得出答案」。
#: 第一版寫「淨分 +6」(有空格)、而且全文只有一個立場 —— 於是把容錯規則
#: 收窄、把段落錨點改掉,兩個突變都不會紅:快照形同虛設。現在:
#:   * 冒號形式的「淨分:+6」→ 少了容錯就抽不到分數;
#:   * 另一段裡放一個**相反**的立場當誘餌 → 段落錨點壞掉就會抽到它。
_REPORT_TEXT = ("## 七、昨夜三大重點\n- 空方觀點:立場:偏空(淨分:-9)\n"
                "## 我的明確立場\n立場:偏多(淨分:+6)\n"
                "理由:費半走強、量能回升。\n"
                "## 一句話總結\n維持核心部位,留意法說。")


def _sha(obj) -> str:
    blob = obj if isinstance(obj, str) else json.dumps(
        obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _packet() -> dict:
    return ep.build(_QUOTES, _FAIR, _PRED, _NEWS, [], {},
                    as_of="2026-08-02T21:00",
                    target_session_date="2026-08-03",
                    sanitize=lambda s: s)


# ---------------------------------------------------------------- 行為快照

#: 屬於**別的**契約版本的欄位 —— 混進來會讓一個契約的變動誤觸另一個的快照。
_OTHER_CONTRACTS = ("evidence_sha", "core_evidence_sha", "evidence_coverage",
                    "evidence_schema_version", "output_schema_version",
                    "truncation_summary", "estimated_input_tokens",
                    "user_payload", "prompt_sha", "response_schema")


def _contract_view(bundle: dict) -> dict:
    """這個 profile 契約自己負責的部分。"""
    return {k: v for k, v in bundle.items() if k not in _OTHER_CONTRACTS}


def _behaviour() -> dict:
    """每個契約版本**現在**的行為指紋。"""
    pk = _packet()
    luna = pp.build_luna_bundle(pk)
    return {
        "evidence_schema_version": _sha(pk),
        "output_schema_version": _sha(sch.ANALYSIS_OUTPUT_SCHEMA),
        "primary_profile_version": _sha(
            luna["developer_instructions"] + "\x00" + luna["user_payload"]),
        # 只雜湊 `profile_id` 是個**死掉的快照** —— 那是個常數,永遠不會變。
        # legacy 契約真正管的是「這條 prompt 被怎麼包裝」:profile 身分、
        # 版本、結構化輸出開關、有沒有 developer 段。prompt 內容本身由
        # `test_deepseek_legacy_golden` 釘住,證據雜湊屬於 evidence 契約。
        "shadow_profile_version": _sha(_contract_view(
            pp.build_deepseek_legacy_bundle(pk, "固定的 legacy prompt"))),
        "postprocess_version": _sha([lp._extract_stance(_REPORT_TEXT),
                                     lp._extract_summary(_REPORT_TEXT)]),
        "renderer_version": _sha(ar.render(_ANALYSIS)),
    }


#: `(版本欄位) → (版本號, 行為雜湊)`。**2026-08-02 於 f5645cd 量測。**
#:
#: 改了任何一個契約的行為時:升版號 **並且** 更新這裡的雜湊,在 commit
#: 說明改了什麼、為什麼。**不要為了讓測試變綠而改** —— 那等於把
#: 「這一群樣本不可比」這件事偷偷抹掉。
_FROZEN = {
    "evidence_schema_version":  (1, "2a30ff6c6d8453e0"),
    "output_schema_version":    (1, "be7237cf1d4f5ed8"),
    "primary_profile_version":  (1, "e2bea660d422847a"),
    "shadow_profile_version":   (1, "3c855543ade5867d"),
    "postprocess_version":      (1, "5791421fb8cd7a67"),
    "renderer_version":         (1, "e0cacdffc2d8162c"),
}


def _declared_versions() -> dict:
    """同群鍵裡**目前**的版本號。"""
    return {
        "evidence_schema_version": ep.EVIDENCE_SCHEMA_VERSION,
        "output_schema_version": sch.ANALYSIS_SCHEMA_VERSION,
        "primary_profile_version": pp.LUNA_XHIGH_VERSION,
        "shadow_profile_version": pp.DEEPSEEK_LEGACY_VERSION,
        "postprocess_version": lx.POSTPROCESS_VERSION,
        "renderer_version": lx.RENDERER_VERSION,
    }


# ---------------------------------------------------------------- 判準

def test_every_version_field_in_the_cohort_key_has_a_snapshot():
    """**涵蓋範圍從 `COHORT_FIELDS` 推導,不是手寫清單。**

    新增一個版本欄位卻沒有快照,漏掉不會有任何人發現 —— 而漏掉的症狀
    正是這條 finding 描述的那種:混群而不自知。
    """
    fields = {f for f in lx.COHORT_FIELDS if f.endswith("_version")}
    assert fields, "COHORT_FIELDS 裡找不到任何版本欄位 —— 掃描器壞了"
    assert set(_FROZEN) == fields, (
        f"沒有快照的版本欄位:{fields - set(_FROZEN)};"
        f"多出來的:{set(_FROZEN) - fields}")
    assert set(_declared_versions()) == fields


def test_the_behaviour_matches_the_frozen_snapshot():
    """**內容變了、版本沒變 → 紅。**

    這是這條 finding 的核心:改了 prompt / renderer / 抽取邏輯卻忘了升版,
    不同契約的樣本會被算進同一群,而判讀的人只看得到一個混合平均。
    """
    now, declared = _behaviour(), _declared_versions()
    drift = [f"{k}: 行為變了({got[:8]}≠{want[:8]})但版本仍是 {declared[k]}"
             for k, (ver, want) in _FROZEN.items()
             if (got := now[k]) != want and declared[k] == ver]
    assert not drift, (
        "以下契約的行為改了卻沒有升版 —— 樣本會混群:\n  "
        + "\n  ".join(drift)
        + "\n升版號並更新 _FROZEN 的雜湊,在 commit 說明改了什麼。")


def test_a_bumped_version_must_come_with_a_new_snapshot():
    """**版本變了、內容沒變 → 也紅。**

    誤升版會把一群本來可比的樣本切成兩半(十配對重新起算);
    而「升了版卻忘了更新這裡」會讓下一次真的漂移逃過檢查。
    """
    now, declared = _behaviour(), _declared_versions()
    stale = [f"{k}: 版本 {_FROZEN[k][0]}→{declared[k]},但行為沒變"
             for k in _FROZEN
             if declared[k] != _FROZEN[k][0] and now[k] == _FROZEN[k][1]]
    assert not stale, "\n  ".join(["版本升了但行為一樣:"] + stale)


def test_the_snapshot_inputs_are_not_empty():
    """**空輸入會讓每個雜湊都變成「空的雜湊」** —— 那時這個檔恆綠。

    固定輸入本身要有內容,否則四條判準全部真空通過。
    """
    pk = _packet()
    assert (pk.get("news") or []), "固定 packet 沒有新聞"
    assert ar.render(_ANALYSIS).strip(), "固定分析渲染不出東西"
    assert lp._extract_stance(_REPORT_TEXT).get("label"), "固定文字抽不出立場"
    assert pp.build_luna_bundle(pk)["user_payload"].strip()
