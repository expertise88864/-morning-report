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

import fixtures_analysis as fx
import json_contract as jc

import analysis_grounding as gr
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
_NEWS = [dict(n, summary=_LONG, fulltext=_LONG * 2) if n["source_item_id"] == "n1"
         else n for n in fx.news()]

_ANALYSIS = fx.valid_analysis()

#: 這段文字要**分辨得出後處理的行為**,不只是「跑得出答案」。
#: 第一版寫「淨分 +6」(有空格)、而且全文只有一個立場 —— 於是把容錯規則
#: 收窄、把段落錨點改掉,兩個突變都不會紅:快照形同虛設。現在:
#:   * 冒號形式的「淨分:+6」→ 少了容錯就抽不到分數;
#:   * 另一段裡放一個**相反**的立場當誘餌 → 段落錨點壞掉就會抽到它。
_REPORT_TEXT = ("## 七、昨夜三大重點\n- 空方觀點:立場:偏空(淨分:-9)\n"
                "## 我的明確立場\n立場:偏多(淨分:+6)\n"
                "理由:費半走強、量能回升。\n"
                "## 一句話總結\n維持核心部位,留意法說。")


#: grounding 的行為指紋要用**正反案例**:合格的要放行、各種不合格的要擋。
#: 少了反例,「全部放行」這個突變會隱形。
#: 全部保持 **schema 合法** —— 要量的是「根據」那一關,不是形狀那一關
#: (第十三輪 P2-3:兩關混在一起就分不出誰在作用)。
def _fabricated() -> dict:
    obj = fx.valid_analysis()
    obj["global_market"]["evidence_ids"] = ["n_fake"]
    return obj


_GROUNDING_CASES = [fx.valid_analysis(), fx.ungrounded_analysis(), _fabricated()]


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


def _versionless(obj):
    """把**版本號本身**從行為指紋裡拿掉(r1 Codex)。

    packet 帶 `schema_version`、bundle 帶 `profile_version`,而 Luna 的
    user payload 內嵌整個 packet —— 於是「只升版、行為沒變」會讓雜湊跟著變,
    看起來像是行為也改了,而那正好讓「誤升版」那條判準抓不到東西。
    (`ANALYSIS_OUTPUT_SCHEMA` 實測不帶版本欄位,不受影響。)

    **指紋要量行為,不能把版本號量進去。**
    """
    if isinstance(obj, dict):
        return {k: _versionless(v) for k, v in obj.items()
                if not str(k).endswith("_version")}
    if isinstance(obj, list):
        return [_versionless(v) for v in obj]
    return obj


def _behaviour() -> dict:
    """每個契約版本**現在**的行為指紋。"""
    pk = _packet()
    bare = _versionless(pk)
    # **走生產的組裝器,只是餵去掉版本號的 packet。**
    # r1(Codex,pass 2):為了剝掉版本號,我一度改成直接呼叫
    # `luna_user_payload(bare)` —— 於是快照不再經過 `build_luna_bundle`,
    # 而生產送出去的正是它回傳的 `user_payload`。那個組裝器日後多包一層、
    # 多附一句 profile 專屬指令,快照都看不到。
    # 兩個性質不必二選一:餵 versionless 的輸入,但仍走生產的路徑。
    luna = pp.build_luna_bundle(bare)
    return {
        "evidence_schema_version": _sha(bare),
        "output_schema_version": _sha(sch.ANALYSIS_OUTPUT_SCHEMA),
        "primary_profile_version": _sha(
            luna["developer_instructions"] + "\x00" + luna["user_payload"]),
        # 只雜湊 `profile_id` 是個**死掉的快照** —— 那是個常數,永遠不會變。
        # legacy 契約真正管的是「這條 prompt 被怎麼包裝」:profile 身分、
        # 結構化輸出開關、有沒有 developer 段。prompt 內容本身由
        # `test_deepseek_legacy_golden` 釘住,證據雜湊屬於 evidence 契約。
        "shadow_profile_version": _sha(_versionless(_contract_view(
            pp.build_deepseek_legacy_bundle(pk, "固定的 legacy prompt")))),
        "postprocess_version": _sha([lp._extract_stance(_REPORT_TEXT),
                                     lp._extract_summary(_REPORT_TEXT)]),
        "renderer_version": _sha(ar.render(_ANALYSIS)),
        # **接受契約要用正反案例量**(第十三輪 P1-3)。只餵合格輸入的話,
        # 把規則放寬到全部放行,雜湊照樣不變 —— 那種快照量不到「擋不擋」。
        "grounding_version": _sha([sch.validate(o, {"n1", "n2"})
                                   for o in _GROUNDING_CASES]),
    }


#: `(版本欄位) → (版本號, 行為雜湊)`。**2026-08-02 於 f5645cd 量測。**
#:
#: 改了任何一個契約的行為時:升版號 **並且** 更新這裡的雜湊,在 commit
#: 說明改了什麼、為什麼。**不要為了讓測試變綠而改** —— 那等於把
#: 「這一群樣本不可比」這件事偷偷抹掉。
#:
#: 2026-08-03 更新四個雜湊,而版本號**維持 1**:原因是**固定輸入被修正**
#: (先前的 `_ANALYSIS` 不合乎 strict schema,見第十三輪 P2-3),
#: 不是契約行為改變。這是這張表少數該「改雜湊而不升版」的情形,
#: 所以理由寫在這裡,不是寫在 commit 就算。
_FROZEN = {
    "evidence_schema_version":  (1, "5f0ae11e554371ad"),
    "output_schema_version":    (1, "be7237cf1d4f5ed8"),
    "primary_profile_version":  (1, "748a46d19a2b2cee"),
    "shadow_profile_version":   (1, "1beef7f63a8ee083"),
    "postprocess_version":      (1, "5791421fb8cd7a67"),
    "renderer_version":         (1, "617fabcde1df42ac"),
    "grounding_version":        (1, "ea7c1800b2d0c032"),
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
        "grounding_version": gr.GROUNDING_VERSION,
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


def test_each_contract_matches_its_frozen_version_and_behaviour():
    """**`(版本號, 行為雜湊)` 這一對要完全相等。**

    r1(Codex):原本拆成兩條判準 ——「行為變了**且**版本沒變」與
    「版本變了**且**行為沒變」—— **兩個都變時,兩條都不報**。
    而版本一旦升過、`_FROZEN` 沒跟著更新,第一條的 `declared == ver` 就
    永遠是 False:**那個契約從此不再被檢查,而且沒有任何訊號。**

    修一個守衛的缺口不該靠再加一條判準去補;**判準本身不能有縫**。
    合成一對之後三種情況都會紅,而診斷訊息分得出是哪一種。
    """
    now, declared = _behaviour(), _declared_versions()
    problems = []
    for k, (ver, want) in _FROZEN.items():
        got, dv = now[k], declared[k]
        if (dv, got) == (ver, want):
            continue
        if dv == ver:
            problems.append(f"{k}: 行為變了({want[:8]}→{got[:8]})但版本仍是 "
                            f"{ver} —— 樣本會混群,請升版並更新 _FROZEN")
        elif got == want:
            problems.append(f"{k}: 版本 {ver}→{dv} 但行為沒變 —— 誤升會把可比"
                            "的樣本切成兩半;若是刻意的請更新 _FROZEN")
        else:
            problems.append(f"{k}: 版本 {ver}→{dv} 且行為 {want[:8]}→{got[:8]}"
                            " —— 兩個都變,請更新 _FROZEN(這一格原本是漏洞)")
    assert not problems, "\n  ".join(["契約快照對不上:"] + problems)


def test_the_snapshot_inputs_are_not_empty():
    """**空輸入會讓每個雜湊都變成「空的雜湊」** —— 那時這個檔恆綠。

    固定輸入本身要有內容,否則四條判準全部真空通過。
    """
    pk = _packet()
    assert (pk.get("news") or []), "固定 packet 沒有新聞"
    assert ar.render(_ANALYSIS).strip(), "固定分析渲染不出東西"
    assert lp._extract_stance(_REPORT_TEXT).get("label"), "固定文字抽不出立場"
    assert pp.build_luna_bundle(pk)["user_payload"].strip()


def test_the_snapshot_fixture_is_schema_valid():
    """**固定輸入自己要合法**(第十三輪 P2-3)。

    快照量的是「契約對某個輸入怎麼反應」。輸入若是真實 API 不會產出的
    形狀,量到的就是一個與生產無關的行為 —— 而它照樣會穩定、照樣會通過。
    """
    assert jc.violations(_ANALYSIS, sch.ANALYSIS_OUTPUT_SCHEMA) == []
    for i, case in enumerate(_GROUNDING_CASES):
        assert jc.violations(case, sch.ANALYSIS_OUTPUT_SCHEMA) == [], (
            f"grounding 案例 {i} 形狀就不合法 —— 那一關會先擋掉它,"
            "量不到「根據」那一關")
