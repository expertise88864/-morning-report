"""Conservative stance matching for the reader-facing conclusion, without scoring."""
import re

_LABEL = re.compile(r"資料不足|偏多|偏空|中性")
# Only a complete, explicitly named signal clause is background. Unknown subjects
# remain checked; an overall report label later in the sentence is never skipped.
_SIGNAL = re.compile(
    r"(?:^|[，,、；;。！？\n：:（(]|但|與|而|及)\s*"
    r"(?:外部定價|外部訊號|本地籌碼)\s*"
    r"(?:仍|目前|短線|略|轉為|呈現|維持|為|是)?\s*[:：（(]?\s*$"
)


def summary_word(summary: str, authority: str) -> str:
    """Prefer any conflicting report claim, not merely the earliest label."""
    text = re.sub(r"[*_`]+", "", str(summary or ""))
    claims = []
    for match in _LABEL.finditer(text):
        if match.group() != "資料不足" and _SIGNAL.search(text[:match.start()]):
            continue
        claims.append(match.group())
    return next((label for label in claims if label != authority),
                claims[0] if claims else "")


def fallback(authority: str) -> str:
    """Do not repeat rejected advice or claim two matching labels disagree."""
    actions = {
        "中性": "方向尚未形成一致訊號，先等待量能與後續事件確認。",
        "偏多": "留意後續行情是否延續；開盤預測不代表盤中走勢或買進訊號。",
        "偏空": "留意下行風險；開盤預測不代表盤中走勢或賣出訊號。",
    }
    if authority not in actions:
        return "目前資料不足，暫不提供方向性結論；請留意後續資料更新。"
    return f"今日維持{authority}。{actions[authority]}價位估算見下方預測表。"
