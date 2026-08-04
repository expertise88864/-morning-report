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
from analysis_schema import CLAIM_TYPES, STANCE_LABELS   # noqa: F401


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

    label = ((obj.get("stance") or {}) if isinstance(obj.get("stance"), dict)
             else {}).get("label")
    if label is not None and label not in STANCE_LABELS:
        problems.append(f"立場詞彙不合法:{label!r}")
    return problems
