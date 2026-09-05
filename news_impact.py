"""Shared causal-writing contract; no model scoring or external facts."""

WRITING = """
## 新聞縱向分析（兩條分析路徑共用）
- 高重要性新聞先交代「今天比先前多知道什麼」，再把新增事實接到可觀察的
  營運變數（訂單、產能、價格或成本），最後說明可能如何影響營收、利潤或現金流。
  區分已發生的事實與有條件的推論；缺中間證據就停在能支持的位置，不硬湊完整鏈。
- 不把單日股價漲跌當成市場已完全定價的證明。沒有預期、估值或持續反應的證據，
  就明說無法判斷已反映多少；同一通訊社的轉載不算多個獨立確認。
- 分開「市場可能反應的時窗」與「營運／財報實現的時點」，不可把 1–5 個交易日
  的交易反應寫成幾天內就會增加獲利。量級沒有分母、金額或時程就揭露缺口，不造數字。
- 每則重要新聞給一個能驗證的後續觀察條件和一個推翻條件；指出要看哪項公告或數據，
  避免只寫「持續關注」。結構化路徑填入 confirmation_signal、invalidation_signal、
  why_this_magnitude 與 horizon；一般文字路徑融入同一小段散文，不增加子項清單。
- 有昨日觀點或多日事件線時，對照今天新證據說明「增強、削弱或仍待驗證」及原因；
  舊敘事只能作背景，不能冒充今天的佐證。缺歷史或新證據就明講，禁止補造事件進展。
"""

HORIZON_LABELS = {"intraday": "當日", "1-5d": "1–5 個交易日", "1-4w": "1–4 週"}


def readout(item: dict, clean) -> str:
    """Expose existing validated fields as prose, without inventing estimates."""
    parts = []
    horizon = HORIZON_LABELS.get(item.get("horizon"))
    magnitude = clean(item.get("why_this_magnitude"))
    confirmation = clean(item.get("confirmation_signal"))
    if horizon:
        parts.append(f"影響觀察窗：{horizon}。")
    if magnitude and magnitude != "無":
        parts.append("量級依據：" + magnitude.rstrip("。.") + "。")
    if confirmation and confirmation != "無":
        parts.append("後續驗證：" + confirmation.rstrip("。.") + "。")
    return " ".join(parts)
