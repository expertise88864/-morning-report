"""Deterministic display labels; never modify source evidence or model scores."""
import re

WRITING = """
## 事實、時間與推論界線
- 每個行事曆事件的日期時間各自對應當期日曆；不可把 PPI 的時間套給 ECB。
  合併分析仍分別列時間，已發生事件不能寫成未來待公布。
- 交易金額／市值不能推導 EPS；需收益、入帳方式與股數，缺少就寫尚無法量化。
  區分事實、條件推論及未知，不無證據保證股價會撐住或政策不會改變盤勢。
- ET 是本態性血小板增多症（essential thrombocythemia），PV 是真性紅血球
  增多症（polycythemia vera）；不可互換。歧義縮寫不要自行展開。
- 結論整合淨影響及失效條件，新聞卡說明細節，跨日回顧只寫增量。
  不跨段重複總經背景；歷史證據支撐當時背景，當期主張另引當期來源。
"""


def medical_terms(text: str) -> str:
    # Verified PharmaEssentia pipeline: ET=platelets; PV=red blood cells.
    return re.sub(r"(?<![A-Za-z])ET\s*[（(](?:真性)?紅血球增多症[）)]",
                  "ET（本態性血小板增多症）", text)


def scenario_time(event: str, fallback: str, packet: dict) -> str:
    """Use per-event calendar times only when every identified event matches uniquely."""
    aliases = {"PPI": r"PPI|Producer Price", "CPI": r"CPI|Consumer Price",
               "ECB": r"ECB|歐洲央行|Main Refinancing Rate|主要再融資利率",
               "FOMC": r"FOMC|Federal Funds Rate"}
    keys = [key for key, pattern in aliases.items() if re.search(pattern, event, re.I)]
    if not keys:
        return fallback
    rows = (packet.get("market") or {}).get("EVENT_CALENDAR") or []
    found = []
    for key in keys:
        matches = {(str(r.get("date") or ""), str(r.get("time") or ""))
                   for r in rows if isinstance(r, dict)
                   and re.search(aliases[key], str(r.get("title") or ""), re.I)
                   and bool(re.search(r"Press Conference|記者會", event, re.I))
                   == bool(re.search(r"Press Conference|記者會", str(r.get("title") or ""), re.I))}
        if len(matches) != 1:
            return fallback
        day, clock = next(iter(matches))
        if not day or not re.fullmatch(r"\d{2}:\d{2}", clock):
            return fallback
        found.append(f"{key} {day} {clock}")
    return "；".join(found) + "（台北）"
