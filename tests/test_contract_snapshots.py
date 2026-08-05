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
import analysis_validate as av
import analysis_render as ar
import analysis_schema as sch
import evidence_packet as ep
import llm_experiment as lx
import llm_postprocess as lp
import prompt_profiles as pp

# ---------------------------------------------------------------- 固定輸入

#: 第十六輪:**固定輸入要撐得起被量的性質。** 先前這裡沒有任何會產生
#: `signal_tensions` 的資料,於是張力的形狀怎麼改,證據指紋都不動 ——
#: 那條判準對它自己該管的東西是真空通過的(與 legacy prompt 那次同型的洞)。
_QUOTES = {"^TWII": {"close": 23000.0, "change_pct": 0.8},
           "QQQ": {"close": 500.0, "change_pct": 1.2},
           "TAIFEX_OI": {"foreign_oi_net": -40000},
           "TAIEX_PRED": {"pred_pct": 0.5},
           "BREADTH": {"advance_ratio": 52.0},
           "MACRO": {"10Y": {"close": 4.50, "prev_close": 4.65}}}
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
#: **手寫**的最小 packet。刻意不經過 `ep.build()` —— 經過的話,
#: evidence 契約一改,這裡就跟著動,而那正是要隔開的東西。
_FIXED_PACKET = {
    "schema_version": 0, "as_of": "2026-08-02T21:00",
    "target_session_date": "2026-08-03", "trading_session": "pre_open",
    "market": {"QQQ": {"change_pct": 1.0}},
    "news": [{"source_item_id": "n1", "title": "固定標題", "summary": "固定摘要",
              "source": "固定來源", "source_grade": "A", "official": False,
              "entities": ["台積電"], "published": "2026-08-02T20:00",
              "url": "", "fulltext": "", "summary_truncated": False,
              "fulltext_truncated": False}],
    "signal_tensions": {"checks_run": [], "unavailable": [], "items": []},
}


def _profile_view(bundle: dict) -> str:
    """profile 契約自己負責的部分:指令,加上 payload 的**框架**。"""
    return bundle["developer_instructions"] + "\x00" + bundle["user_payload"]


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


def _render_case(pk: dict) -> dict:
    """渲染探針的固定輸入 —— **要示範 renderer 真的會做的事**。

    第十八輪:`fx.valid_analysis()` 沒有任何 `tension_resolutions`,
    高重要性事件的鏈也是完整的 —— 於是「逐筆張力抬頭」與「傳導未完成
    的揭露」兩段程式碼在快照裡**一行都跑不到**,改了 renderer 而指紋不動。
    探針量不到的東西,版本升降就只是在猜。
    """
    import signal_tensions as _st
    o = fx.valid_analysis()
    o["cross_market_synthesis"]["tension_resolutions"] = [
        {"tension_id": t, "resolution": "外部定價先反映在權值開盤",
         "dominant_side": "left", "why": "開盤前只有美股已定價",
         "decision_rule": "現貨量能與期貨空單是否回補",
         "evidence_ids": [t]}
        for t in sorted(_st.required_tension_ids(pk.get("signal_tensions")))]
    # 一條**停在情緒**的高重要性鏈 —— 揭露那一段才跑得到。
    o["top_news_analysis"][0]["mechanism_steps"] = [
        {"from_what": "費半收漲", "to_what": "市場關注提高", "channel": "情緒",
         "stage": "event", "step_type": "inference", "evidence_ids": []},
        {"from_what": "市場關注提高", "to_what": "投資情緒改善",
         "channel": "情緒", "stage": "sentiment", "step_type": "inference",
         "evidence_ids": []}]
    return o


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


def _legacy_prompt() -> str:
    """生產真的會送給 DeepSeek 的那份 prompt(固定輸入)。

    與 `test_deepseek_legacy_golden` 釘的是同一個東西 —— 那個檔負責
    「改了要看得見」,這裡負責「改了就必須升版」。兩者角度不同,都要有。
    """
    import morning_report as mr
    return mr._build_prompt({"QQQ": {"close": 500.0}}, {"fair_value": 100.0},
                            {"model1": 1000.0}, _NEWS, [], "")


def _behaviour() -> dict:
    """每個契約版本**現在**的行為指紋。"""
    pk = _packet()
    bare = _versionless(pk)
    # **走生產的組裝器,只是餵去掉版本號的 packet。**
    # r1(Codex,pass 2):為了剝掉版本號,我一度改成直接呼叫
    # `luna_user_payload(bare)` —— 於是快照不再經過 `build_luna_bundle`,
    # 而生產送出去的正是它回傳的 `user_payload`。那個組裝器日後多包一層、
    # 多附一句 profile 專屬指令,快照都看不到。
    # 2026-08-05:profile 那一格改餵 `_FIXED_PACKET`(見下),
    # **evidence 那一格仍然走這條** —— 它本來就該隨 packet 變。
    return {
        # 第十七輪:evidence v4(遞迴 registry + 廣度方向/強度分離)、
    # schema v4(tension_resolutions + stage)、renderer v4(逐筆調和進信)、
    # grounding v5(深度提示再擴充)、Luna profile v10。
    "evidence_schema_version": _sha(bare),
        "output_schema_version": _sha(sch.ANALYSIS_OUTPUT_SCHEMA),
        # **profile 的指紋不該被證據契約牽動。** 餵 `luna`(由真實
        # `_packet()` 建的)時,evidence 加一個欄位就讓 prompt 契約亮紅 ——
        # 2026-08-05 又發生一次(`coverage` 加了 `raw_available`)。
        # 那種誤報會訓練出「看到紅就升版」的反射,真正的 prompt 變動反而
        # 混在裡面。改餵**手寫的固定 packet**:payload 的框架仍然量得到。
        "primary_profile_version": _sha(
            _profile_view(pp.build_luna_bundle(_FIXED_PACKET))),
        # legacy 契約管兩件事:prompt 的**內容**,以及它被怎麼包裝。
        # 2026-08-03:先前餵一段固定字串當 prompt,於是**真正的 prompt 改了、
        # 指紋卻不動** —— 那天升 `DEEPSEEK_LEGACY_VERSION` 時,「版本變了行為
        # 沒變」當場亮紅,而那個紅是對的:指紋涵蓋不到它自己該管的東西。
        # 現在餵**生產真的會送的那份**(`_build_prompt` 對固定輸入的輸出)。
        # prompt 內容**另外算一份**:`_contract_view` 排除了 `user_payload`
        # (那一格在 Luna 側屬於 evidence 契約),而 legacy 的 prompt 正好
        # 就住在那裡 —— 只餵真 prompt 而不把它算進去,指紋照樣不動。
        # 這是同一個洞的第二層:**改對了輸入,卻沒改到被雜湊的東西。**
        "shadow_profile_version": _sha([
            _versionless(_contract_view(
                pp.build_deepseek_legacy_bundle(pk, _legacy_prompt()))),
            _sha(_legacy_prompt())]),
        "postprocess_version": _sha([lp._extract_stance(_REPORT_TEXT),
                                     lp._extract_summary(_REPORT_TEXT)]),
        # **探針要用生產的呼叫形狀**(第十八輪)。先前餵 `render(obj)` ——
        # 而生產是 `render(obj, packet)`。於是逐筆張力的抬頭、傳導未完成的
        # 揭露,這些**只有在有 packet 時才存在的行為**,快照根本量不到:
        # 改了 renderer 而指紋不動,「版本升了行為沒變」就會誤報。
        # 這個 repo 已經栽過同一形狀兩次(legacy prompt 那兩層)。
        "renderer_version": _sha([ar.render(_ANALYSIS),
                                  ar.render(_render_case(pk), pk)]),
        # **接受契約要用正反案例量**(第十三輪 P1-3)。只餵合格輸入的話,
        # 把規則放寬到全部放行,雜湊照樣不變 —— 那種快照量不到「擋不擋」。
        # v3:接受政策含「深度加深」的觸發條件 —— depth_advisories 的行為
        # 也是契約的一部分(它決定要不要多跑一次、輸出分佈因此不同)。
        # 同上:**主閘門在生產吃的是 packet**,而這裡餵 ID 集合 ——
        # 於是「有張力卻沒處理」「重複調和」「證據沒涵蓋兩側」這些
        # packet-aware 的接受規則,快照一條都量不到。**兩種形狀都量**:
        # 舊呼叫端仍然合法,而新規則要看得見。
        "grounding_version": _sha([sch.validate(o, fx.ids())
                                   for o in _GROUNDING_CASES]
                                  + [sch.validate(o, pk)
                                     for o in _GROUNDING_CASES]
                                  + [av.depth_advisories(o)
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
#: 2026-08-03 第四次更新:兩個 profile 版本 2→3。r1 外審指出「維持 v2」
#: 這個決定**取決於「剛好沒有 v2 執行過」** —— 查證確實沒有(v2 未推、
#: 帳本不存在),但規則不該建立在那種查證上。prompt 位元組變了就換版本;
#: `prompt_sha` 只是溯源、不在同群鍵裡,靠它事後分辨等於讓混群先發生。
#: 雜湊不變是正確的:內容在上一步就已經改完,這一步只補版本號。
#:
#: 2026-08-03 第三次更新:r1 外審抓到「用全形」那條規則**自己用半形舉例**,
#: 而且整份指令通篇半形 —— 模型模仿它看到的東西,示範會蓋過規則。
#: 兩份 prompt 的中文散文都做了保守的全形轉換(格式模板不動)。
#: 版本仍是 2:這是同一次風格變更的修正,不是另一次變更。
#:
#: 2026-08-03 第二次更新:兩個 profile 的**風格**依使用者回饋改成敘事寫法 +
#: 全形標點,那會改變輸出,所以兩個版本號都升到 2(不是只改雜湊)。
#: 同時修好一個漏洞:shadow 指紋原本餵一段固定字串當 prompt、而且
#: `_contract_view` 又把 `user_payload` 排除掉 —— **真正的 legacy prompt
#: 改了,指紋卻兩層都攔不到**。現在 prompt 內容另外算一份算進去。
_FROZEN = {
    # v2(第十五輪 P2-1):packet 加 signal_tensions —— 橫向矛盾由 Python
    # 先算好(附數字與門檻出處),模型從「找矛盾」變成「解釋矛盾」。
    # v3(第十六輪):張力改純觀測(left/right/relationship/tension_id/
    # usable_for_inference);registry 改 typed。固定輸入同時補上會產生
    # 張力的行情 —— 先前它撐不起這個性質,指紋對自己該管的東西真空通過。
    # v5(第十八輪 P1-4):利率×科技改用**象限名**(先前的 `same_direction`
    #     內建了「折現率下行有利成長股」這條假說);衍生值改掛 `derived:`
    #     並帶來源 —— `market:MACRO.10Y.change_bps` 那個 packet 裡不存在的
    #     路徑不再是合法引用。
    # v6(第十八輪 P1-1/P1-2/P1-8):registry 擴到整個 packet 並帶 metadata
    #    (值/單位/時間/來源/能不能推論);`US_HOLIDAY` 進 packet ——
    #    先前張力看得到美股休市而 registry 看不到,同一天兩個真相;
    #    新增 `required_disclosures`(今天哪幾項沒有答案)。
    # v7(第十八輪 P1-3):`news_clusters` —— 同一件事的多家報導併成一群,
    #    並由官方來源與報導家數選出必分析清單(不採用模型自評的重要性)。
    # v8(第十九輪):root scalar 的值不再掉在空 path;`as_of_precision`
    #    與 `observed_session` 取代假精確;新聞先分群、必分析事件強制
    #    保留、再截斷(先前排第 221 的央行公告直接消失而覆蓋率 100%)。
    # v9(第二十輪 P1-2/P1-7):分群改與**代表**比對(single-link 會被
    #    橋接串起來,兩件事壓成一群);observed_session 改逐區塊政策
    #    (先前「非美即台」,公報與匯率被掛上台股交易日)。
    # v10(深度加強第二批):每則新聞抽帶單位的數字成 `fact:` 命名空間
    #     (值/單位/上下文進 registry —— 抄錯十倍終於抓得到);
    #     同源改版重發去重;事件群帶 corroboration 等級。
    # v11(2026-08-05 實機 + 第二十輪 P2-3):`coverage` 的分母改成
    #     **去重後**的可用數(一家重發十次時,去重成功先前顯示成
    #     「涵蓋不足」);原始數另外報。
    # v12(第二十輪 P2-1):事件群代表改選「官方 > 資訊量高 > 最小 ID」
    #     —— 最小 ID 會確定性 over-split(短而模糊的標題當代表)。
    "evidence_schema_version":  (12, "22475ec0c82c154a"),
    # v2(schema v2):top_news_analysis 加因果鏈/量級/關係;新增
    # cross_market_synthesis。prompt 叫模型深入而 schema 沒地方放,
    # 是使用者三次「堆疊數據」回饋在結構層的根因(第十五輪 P1-1)。
    # v3(第十六輪 P2-2):`addressed_tension_ids` + `priced_in.evidence_ids`。
    # v5(第十八輪 P1-8):`data_gaps[].gap_id` —— 缺口要對得上是哪一項。
    # v6(第十八輪 P1-3):`dismissed_events` —— 駁回必分析事件要留理由。
    # v7(第十八輪):`affected_assets`(同一件事對不同標的可以相反)、
    #    `claim_id` + 各段 `claim_ids`(閉合 claim 圖)、`alignment_readings`。
    # v8(第十九輪 P1-8):`claim_audit.asset_scope` 與頂層
    #    `executive_summary_claim_ids` —— 最可能被單獨閱讀的那一段
    #    先前完全脫離稽核。回指放頂層是**攤平**(深度已貼齊上限)。
    # v9(第二十輪 P1-6/P2-2):scenario 與 watch_triggers 接進 claim 圖
    #    (最前瞻的判斷不能是唯一不用根據的段落);dismissed_events 加
    #    revisit_trigger 與 supporting_evidence_ids。
    # v10(第二十輪 P1-5/P2-7):`key_drivers[].claim_ids`(Email 第一段
    #     先前完全在 claim 圖之外)、`corroboration_assessment` 與
    #     `source_caveat`(單一來源的揭露改成機械契約)。
    "output_schema_version":    (10, "ed72e626cf272f08"),
    # v4(2026-08-03 晚):可讀性三修——全中文轉述、術語白話化、數字要有下文。
    # v5(2026-08-04):Python 排好的表要被合起來解讀(R17)、七之二要寫得出傳導路徑。
    # v6(2026-08-04 二次):方向形容詞不是分析——量級/時間取代方向詞、
    # 至少兩條跨條連結、句式不得雷同;兩個範例整個重寫(它們自己在示範那個毛病)。
    # v7(schema v2):新欄位的填法指引(unknown 是誠實不是失敗、
    # 編造的關聯比沒有關聯更糟、五個市場各寫一句不是綜合)。
    # v8(第十五輪 P2-1):要求逐條正面處理 signal_tensions 的每個 tension。
    # v9(第十六輪):張力純觀測、typed 引用 ID、回填 addressed_tension_ids。
    # v11(第十八輪):證據引用改口徑 —— 先前開頭寫「帶上支持它的
    #      `source_item_id`」而後段才說行情用 `market:*`,前後矛盾。
    # v12(第十八輪):九個命名空間的用法、逐項揭露 `required_disclosures`、
    #      不同步的欄位不得單獨支撐高重要性判斷。
    # v13(第十八輪 P1-3):一個事件群只寫一個分析單位;
    #      必分析清單要嘛分析、要嘛說明為什麼不談。
    # v14(第十八輪):三條新規則(逐標的、同向解讀、claim 回指)。
    # 第十九輪:**探針輸入被修正,契約本身沒變** —— 先前餵
    #    `build_luna_bundle(_packet())`,而 payload 內嵌整個 packet,
    #    於是 evidence 加一個欄位就會讓 prompt 契約亮紅。改餵手寫的
    #    固定 packet。依本表既有先例:**改雜湊而不升版**。
    # v15(第十九輪):asset_scope、總結回指、時間尺度要連對。
    # v16(第二十輪+深度加強):量化錨點、橫向接行情、駁回的回頭條件。
    # v17(深度加強第二批):新聞數字用 fact: 引用;單一來源要明講。
    # 2026-08-05:**探針輸入被修正,prompt 本身沒變**
    #    (dev 指令與 payload 框架逐位元組相同,已實測)。
    #    依本表既有先例:改雜湊而不升版。
    # v18(第二十輪 P2-6):命名空間與量化錨點的說明改由
    #      `evidence_namespaces` 單一宣告生成(先前三邊各說各話);
    #      key_drivers/情境/觀察點也要回指;佐證等級照抄不自評。
    # v19:`calibration:` 與 `quality:` 不再列為量化錨點 ——
    #      它們是關於**本報自己**的數字(校準、涵蓋度),
    #      不是市場量級。用它們錨住因果鏈是把儀表板當證據。
    "primary_profile_version":  (19, "327f32830537d86e"),
    "shadow_profile_version":   (6, "27c0be1da4981f4e"),
    "postprocess_version":      (1, "5791421fb8cd7a67"),
    # v2(2026-08-04,第十五輪 P1-2/P1-3):段落語意映射修正 + 補上先前
    # 整段丟掉的 priced_in / falsification_trigger / counterevidence /
    # actions_to_consider。**渲染層丟資料時模型再深入也沒用。**
    # v3(schema v2):因果鏈/量級/驗證與失效/關係 + 橫向綜合段。
    # 第十六輪:renderer **契約沒變**,是固定輸入補了 priced_in 內容與
    # addressed_tension_ids(fixture 要示範新欄位長什麼樣)。依本表既有先例
    # (2026-08-03 那次同理):輸入被修正時**改雜湊而不升版**,理由寫在這裡。
    # v5(第十八輪):逐筆張力印出**它在調和什麼**(topic 與兩側數值,
    #    由 renderer 從 packet 回查);高重要性事件的傳導沒走完時揭露。
    #    **探針同批修好** —— 先前餵 `render(obj)` 而生產是 `render(obj, packet)`,
    #    新行為在快照裡一行都跑不到。
    # v6(第十八輪):逐標的影響與同向解讀排進信。
    # v7(第十九輪):情境觸發條件(機率仍不進信 —— 信裡的數字必須是
    #    Python 算的)、駁回事件、未完成鏈的剩餘則數。
    # v8(第二十輪 P2-2):駁回超過 4 件顯示「另有 N 件」。
    # v9(第二十輪 P2-7):單一來源/未證實的佐證等級與保留事項固定呈現。
    "renderer_version":         (9, "fe4ca290c09fc35c"),
    # v2(schema v2):cross_market_synthesis 進 RENDERED 與 EVIDENCE_BEARING。
    # v3(第十五輪):接受政策加「合法但淺 → 用剩餘額度加深一次」;
    # 指紋納入 depth_advisories 的行為。
    # v4(第十六輪 P2-4):`priced_in` 也要帶證據(高推論性判斷更需要根據)。
    # v6(第十八輪):接受規則加三條 —— 重複的張力調和、調和的證據沒有
    #    涵蓋兩側、以及**主閘門改吃 packet**(先前生產傳 ID 集合,
    #    packet-aware 的規則一條都沒跑過)。探針同批改成兩種形狀都量。
    # v7(第十八輪 P1-8/P1-2):逐 gap 揭露(先前只要 data_gaps 非空就過,
    #    於是一筆無關的缺口能替所有跑不成的檢查過關);高重要性判斷
    #    不得只靠標為不同步的證據。
    # v8(第十八輪 P1-3):必分析事件的覆蓋率;同一事件群不得分析兩次。
    # v9(第十八輪):高重要性事件要拆出標的;同向訊號逐筆解讀;
    #    各段要回指 claim,而高重要性的孤兒主張不算根據。
    # v10(第十九輪):同一則新聞不得寫兩段;標的不得是泛稱或重複;
    #     同向訊號的證據要綁在那一筆上;駁回理由不得是套語。
    # v11(第十九輪 P1-8):回指要**連對**不只是連上 —— 立場的時間
    #     尺度要有同尺度的主張撐著;asset_scope 不得是泛稱或留空。
    # v12(第二十輪):情境/觀察點要回指;駁回要引用被駁回那群自己的
    #     新聞並給回頭條件;段落內重複回指要擋;完整鏈=全程不倒退;
    #     深度加強:量化錨點與橫向接行情(advisory,不擋信)。
    # v13(深度加強第二批):量化錨點 advisory 接受 `fact:`。
    # v14(第二十輪 P1-3):量化錨點改用 `is_numeric_anchor` ——
    #     要是**這則新聞自己的、真的是數字的、今天可用的**證據。
    # v15(第二十輪 P1-5/P2-5/P2-7):段落→主張的對照表改由 `claim_map`
    #     生成(四個消費者共用一份);時間尺度要有主張撐得住;
    #     佐證等級不得往上寫、單一來源要有 caveat。
    "grounding_version":        (15, "98954f69ba68ab4d"),
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
