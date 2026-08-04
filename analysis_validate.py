# -*- coding: utf-8 -*-
"""**引用的東西存不存在**(schema v2 時從 `analysis_schema` 拆出)。

這個 repo 把「分析輸出的合法性」刻意切成三塊,各自有版本、各自能被單獨測:

  * `analysis_schema` —— **形狀**。strict Structured Outputs 保證得了的部分。
  * `analysis_grounding` —— **有話說就要說得出根據**。哪些會進信的段落
    必須帶證據。
  * 這個模組 —— **引用的 ID 是不是真的存在**,以及 schema 表達不了的
    跨欄位不變式(高重要性主張要有證據、關係要指向真的存在的條目、
    沒有證據的因果步驟不得自稱 fact)。

**編造的引用比沒有引用更危險** —— 它讓錯誤看起來有根據。這是本模組
存在的唯一理由,所有判準都繞著它。

拆出來的直接原因是 schema v2 讓 `analysis_schema.py` 逼近行數上限,
而「形狀」與「檢查」本來就是那個檔的 docstring 自己說要分開的兩件事。
"""
from __future__ import annotations

import analysis_grounding as _gr

# `STANCE_LABELS` 在函式內延遲取用 —— `analysis_schema` 的尾端會反向
# import 本模組(相容出口),頂層互相 import 會在「誰先被載入」上翻車。


def validate(obj, evidence_ids) -> list:
    """回傳問題清單(空 = 通過)。**不拋例外**:呼叫端決定要修還是降級。

    只驗「schema 管不到」的:
      - 證據 ID 是否真的存在於本日 packet(**編造的 ID 比沒有 ID 更危險**,
        它看起來有根據)
      - 高重要性的 fact/inference 有沒有帶證據
      - **會進到信裡的段落有沒有帶得出根據**(第十二輪 P1-3)
      - 立場詞彙是否合法

    ## 第十二輪 P1-3:strict schema 保證形狀,不保證根據

    「有話說就要說得出根據」那一半在 `analysis_grounding`(緣由寫在那裡)。
    這裡只保留「ID 存不存在」與立場詞彙 —— 形狀與根據刻意分成兩個模組。
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
    news = [n for n in (obj.get("top_news_analysis") or []) if isinstance(n, dict)]
    own_ids = {str(n.get("source_item_id") or "") for n in news}
    for i, n in enumerate(news):
        where = f"top_news_analysis[{i}]"
        _check_ids([n.get("source_item_id")], where)
        # v2:因果鏈。**沒有證據的那一步不得自稱 fact** —— 那正是
        # 「看起來有根據」的來源,而它比完全沒有分析更難察覺。
        for j, st in enumerate(n.get("mechanism_steps") or []):
            if not isinstance(st, dict):
                problems.append(f"{where}.mechanism_steps[{j}] 不是物件")
                continue
            _check_ids(st.get("evidence_ids"), f"{where}.mechanism_steps[{j}]")
            if st.get("step_type") == "fact" and not (st.get("evidence_ids") or []):
                problems.append(
                    f"{where}.mechanism_steps[{j}] 自稱 fact 卻沒有證據 ——"
                    "沒有證據的那一步要標成 inference 或 unknown")
        # v2:**`unknown` 不是免費的逃生口。** 選它就要說出缺哪些資料,
        # 否則它只是「小幅利多」換一個寫法。
        if (n.get("magnitude_band") == "unknown"
                and not str(n.get("why_this_magnitude") or "").strip()):
            problems.append(
                f"{where} 的量級選了 unknown,卻沒有說缺哪些資料")
        # v2:關係要指向**今天真的存在的另一則**,而且不能指向自己。
        for j, rel in enumerate(n.get("relates_to") or []):
            if not isinstance(rel, dict):
                problems.append(f"{where}.relates_to[{j}] 不是物件")
                continue
            other = str(rel.get("other_source_item_id") or "")
            _check_ids(rel.get("evidence_ids"), f"{where}.relates_to[{j}]")
            if other == str(n.get("source_item_id") or ""):
                problems.append(f"{where}.relates_to[{j}] 指向自己")
            elif other not in own_ids:
                problems.append(
                    f"{where}.relates_to[{j}] 指向 {other!r},"
                    "而本報今天沒有分析那一則 —— 關係不得指向不存在的東西")
    cms = obj.get("cross_market_synthesis")
    if isinstance(cms, dict):
        _check_ids(cms.get("evidence_ids"), "cross_market_synthesis")
    # r1(Codex,P1):「要求非空」本身在鼓勵模型隨便填一個 —— 新守衛因此
    # 製造了開頭那句話說的風險:編造的 ID 比沒有 ID 更危險。
    for sec in _gr.EVIDENCE_BEARING:
        node = obj.get(sec)
        if isinstance(node, dict):
            _check_ids(node.get("evidence_ids"), sec)

    # 進信的段落要帶得出根據(`analysis_grounding`)。**空著不算過** ——
    # 迴圈跑不到不等於沒問題,而那正是這條缺陷活下來的方式。
    problems.extend(_gr.problems(obj))

    from analysis_schema import STANCE_LABELS as _labels   # 延遲:避免循環
    label = ((obj.get("stance") or {}) if isinstance(obj.get("stance"), dict)
             else {}).get("label")
    if label is not None and label not in _labels:
        problems.append(f"立場詞彙不合法:{label!r}")
    return problems


# ------------------------------------------------------------ 深度(不擋信)

def depth_advisories(obj) -> list:
    """**合法但淺**的地方(空 = 夠深)。與 `validate()` 刻意分開:

    這裡的每一條都**不會**讓輸出被拒絕 —— 淺而正確的分析落回 legacy
    只會換來一封更淺的信。它們的用途是:第一次輸出合法但淺的時候,
    把**還沒用掉的那次修補額度**拿來加深(見 `deepen_input`),
    最壞情況仍是兩次呼叫,與修補相同 —— 多的是深度,不是新的失敗模式。

    判準全部是結構性的(數步數、查空欄),不是關鍵詞 —— 第十五輪 P1-5
    說對了:關鍵詞當門檻,一句「兩者同向,預計明年影響 5%」就能騙過。
    """
    out: list = []
    if not isinstance(obj, dict):
        return out
    news = [n for n in (obj.get("top_news_analysis") or []) if isinstance(n, dict)]
    for i, n in enumerate(news):
        where = f"top_news_analysis[{i}]"
        steps = [s for s in (n.get("mechanism_steps") or []) if isinstance(s, dict)]
        if n.get("materiality") == "high" and len(steps) < 2:
            out.append(f"{where} 是高重要性,因果鏈卻只有 {len(steps)} 步 —— "
                       "至少走到「事件 → 營運 → 財務或股價」兩步;"
                       "不確定的步驟標 inference/scenario,不要省略")
        if (n.get("magnitude_band") in ("negligible", "small", "moderate", "large")
                and not str(n.get("why_this_magnitude") or "").strip()):
            out.append(f"{where} 給了量級卻沒有說為什麼是這個量級")
    cms = obj.get("cross_market_synthesis")
    if isinstance(cms, dict):
        has_content = any(str(v or "").strip() if isinstance(v, str) else v
                          for k, v in cms.items() if k != "evidence_ids")
        if has_content:
            if not [x for x in (cms.get("conflicting_signals") or [])
                    if str(x).strip()]:
                out.append("cross_market_synthesis 沒有列任何互相抵銷的訊號 —— "
                           "確實沒有衝突時要寫一條「今日無明顯互相抵銷的訊號」明講,"
                           "不得留空")
            if not str(cms.get("dominant_driver") or "").strip():
                out.append("cross_market_synthesis 沒有指出今天的主導因子")
            if not str(cms.get("what_would_flip_it") or "").strip():
                out.append("cross_market_synthesis 沒有說什麼情況會讓主導因子失效")
    if len(news) >= 3 and not any((n.get("relates_to") or []) for n in news):
        out.append(f"{len(news)} 則新聞裡沒有任何一則指出與其他條目的關係 —— "
                   "確認它們是否真的全部獨立;**沒有根據的關係不要硬湊**,"
                   "但搶同一段產能或同一個底層驅動的要指出來")
    return out


def deepen_input(user_payload: str, advisories: list) -> str:
    """加深那一次呼叫的 user 輸入。**加深是把已有的證據走完因果鏈,
    不是編內容** —— 這句話要放在指令裡,否則加深會誘發編造。"""
    return (user_payload + "\n\nDEEPEN\n上一版輸出合法,但深度不足。"
            "請針對下列各點加深後重新輸出**完整** JSON。"
            "沒有根據的關係與證據**不得硬湊** —— 加深是把已有的證據"
            "走完因果鏈與量級判斷,不是編造新內容;真的判斷不出量級就選 "
            "unknown 並寫缺哪些資料:\n"
            + "\n".join(f"- {a}" for a in advisories[:6]))
