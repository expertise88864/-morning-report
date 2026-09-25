"""Validate model stance against the Python authority when it is available."""


def problems(obj: dict, packet) -> list:
    stance = obj.get("stance")
    if not isinstance(stance, dict):
        return []  # Required shape is checked by json_contract.
    score, label = stance.get("score"), stance.get("label")
    out = []
    if "score" in stance and score is None and label != "資料不足":
        out.append("stance.score 為 null 時 label 必須為資料不足,不得宣稱多空或中性")
    market = packet.get("market") if isinstance(packet, dict) else None
    # 舊離線樣本/僅 ID 的呼叫端沒有 Python 計分欄,無法驗當日權威是否存在。
    # 生產 phase 一律寫 STANCE_PY:計算成功寫結果,失敗明確寫 {}。
    if not isinstance(market, dict) or "STANCE_PY" not in market:
        return out
    sp = market["STANCE_PY"]
    available = (isinstance(sp, dict) and type(sp.get("total")) is int
                 and bool(sp.get("label")))
    if not available and (score is not None or label != "資料不足"):
        out.append("系統計分缺席:stance.score 必須為 null 且 label 為資料不足,禁止 LLM 補算")
    elif available and score is None:
        out.append(f"Python 計分可得:stance.score 不可為 null,"
                   f"必須抄錄系統分數 {sp['total']}")
    elif available:
        if type(score) is not int:
            out.append(f"stance.score 必須為整數且與 Python 權威分數一致:"
                       f"應為 {sp['total']},不可採用模型重算值")
        elif score != sp["total"]:
            out.append(f"stance.score 與 Python 權威分數不一致:"
                       f"應為 {sp['total']},不可採用模型重算值")
        if label != sp["label"]:
            out.append(f"stance.label 與 Python 權威立場不一致:"
                       f"應為 {sp['label']},不可採用模型方向")
    return out
