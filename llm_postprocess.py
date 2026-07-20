"""LLM 分析輸出的後處理純函式(A5-Step1 由 morning_report 抽出)。
皆無網路/無狀態/不依賴 morning_report 其它符號;morning_report 以 re-export 保持向後相容,
既有測試(呼叫 mr.<fn>)零修改。"""
from __future__ import annotations

import json
import sys


def _strip_llm_watchlist_section(text: str) -> str:
    """Remove duplicated LLM-written Taiwan Top5; Python renders the canonical card."""
    if not isinstance(text, str):
        return ""
    import re as _re
    pattern = (
        r"\n*#{1,6}\s*"
        r"(?:[一二三四五六七八九十零\d]+、)?"
        r"(?:今日台股(?:客觀)?關注五檔|台股關注五檔)"
        r".*?"
        r"(?=\n#{1,6}\s*(?:[一二三四五六七八九十零\d]+、)?一句話(?:總結|結論)|\Z)"
    )
    return _re.sub(pattern, "\n", text, flags=_re.S).strip()


def _parse_llm_event_json(text: str) -> list[dict]:
    """Accept a strict JSON array, with a small fence-tolerant recovery path."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        parsed = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, dict)][:40] if isinstance(parsed, list) else []


def _strip_stance_calculation(text: str) -> str:
    """隱藏「我的明確立場」的 11 維加減分計算行(使用者回饋:內部計算不需顯示)。

    LLM 仍被要求顯式輸出計算(強迫算數 = 品質保證,且 _extract_stance 依賴「淨分」),
    只在渲染前移除。規則:含「淨分」且含「[」的行 = 計算行(如「QQQ +1.2% [+1]、…= 淨分 -5」);
    「立場:中性(淨分 +3…)」這種結論行不含 [,保留。順帶清掉殘留的 ``` 圍欄。
    """
    if not isinstance(text, str) or "淨分" not in text:
        return text
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if "淨分" in s and "[" in s:
            continue
        if s in ("```", "``` "):
            continue
        out.append(line)
    return "\n".join(out)


def _strip_stance_internals(text: str) -> str:
    """散文層安全網(批#26 使用者:理由不要出現計分內部):在「我的明確立場」
    理由句裡移除「11 維中 X 項偏空」「N 項偏多」「淨分 ±N」「距門檻…」等
    計分細節子句(以中文標點切段,丟含關鍵詞的子句)。prompt 已禁,此為雙保險。
    **必須在 _extract_stance 之後才呼叫**(擷取依賴「淨分」)。"""
    import re as _re
    if not isinstance(text, str) or not any(
            k in text for k in ("維中", "項偏", "淨分", "門檻")):
        return text
    _bad = _re.compile(r"(維中|項偏空|項偏多|淨分|距.{0,6}門檻)")

    def _clean_line(line: str) -> str:
        if not _bad.search(line):
            return line
        # 前綴(如「理由：」「> **理由**：」)保留;冒號後的內容切子句過濾
        head, sep, body = line.partition("：")
        if not sep:
            head, sep, body = line, "", ""
            body, head = head, ""
        # 中文+ASCII 標點皆為子句分隔(Codex 批#26 r2:LLM 常混用半形逗號,
        # 只認全形會把整句當一段而全數丟掉,連傳導鏈一起被誤刪)。
        # 千分位「1,234」不受害:分隔後兩側皆非壞子句 → 保留並補回逗號。
        segs = _re.split(r"([，、；。,;])", body)
        kept = []
        for i in range(0, len(segs), 2):
            seg = segs[i]
            delim = segs[i + 1] if i + 1 < len(segs) else ""
            if seg and not _bad.search(seg):
                kept.append(seg + delim)
        cleaned = "".join(kept).strip("，、；。,; ")
        return (head + sep + cleaned) if (head or sep) else cleaned

    return "\n".join(_clean_line(ln) for ln in text.split("\n"))


def _strip_score_phrases(text: str) -> str:
    """外科式移除計分片語,保留立場標籤與動作(Codex 批#26 r2/r4:一句話總結是
    「立場+動作」單行)。只挖「淨分 ±N 距…門檻」「N 維中 X 項」「N 項偏空/多」;
    括號組只清內部計分片語,**清完仍有內容(如動作建議)則保留括號**,只有清
    到空才連括號刪。"""
    import re as _re
    if not isinstance(text, str) or not any(
            k in text for k in ("維中", "項偏", "淨分", "門檻")):
        return text

    def _clean_inner(s: str) -> str:
        # 到下一個標點或括號止(不吃過括號邊界)
        s = _re.sub(r"淨分\s*[:：=為]?\s*[+\-]?\d+(?:\s*距[^，、；。,;）)]*)?", "", s)
        s = _re.sub(r"\d+\s*維中[^，、；。,;）)]*", "", s)
        s = _re.sub(r"\d+\s*項偏[空多]", "", s)
        return s

    def _paren(m: "_re.Match") -> str:
        open_c, inner, close_c = m.group(0)[0], m.group(1), m.group(0)[-1]
        cleaned = _clean_inner(inner)
        if not _re.sub(r"[，、；。,;\s]", "", cleaned):   # 清完只剩標點/空白 → 整組刪
            return ""
        return open_c + cleaned.strip("，、；。,; ") + close_c
    text = _re.sub(r"[（(]([^）)]*)[）)]", _paren, text)   # 括號組
    text = _clean_inner(text)                              # 括號外裸片語
    text = _re.sub(r"([，、；。,;])\s*(?=[，、；。,;])", "", text)   # 連續標點去重
    return text.strip("，、；。,; ")


def _extract_stance(text: str) -> dict:
    """從 LLM markdown 分析中擷取「立場」與「淨分」，用於頂部 KPI 條。失敗回 {}。"""
    import re as _re
    out: dict = {"label": None, "score": None}
    if not isinstance(text, str):
        return out
    section_match = _re.search(
        r"#{1,6}\s*(?:[一二三四五六七八九十零\d]+、)?我的明確立場\b"
        r".*?(?=\n#{1,6}\s*(?:[一二三四五六七八九十零\d]+、)?|\Z)",
        text,
        _re.S,
    )
    scoped = section_match.group(0) if section_match else text
    # 淨分容錯:「淨分 +7」「淨分:+7」「淨分為 +7」「= 淨分 +7」皆吃
    m = _re.search(r"淨分\s*[:：=為]?\s*([+\-]?\d+)", scoped)
    if m:
        try:
            out["score"] = int(m.group(1))
        except ValueError:
            pass
    # 立場容錯:「立場：偏多」「> **立場**：偏多」「立場 ：偏多」皆吃。錨定行首(可有 >/空白/**)
    # 才匹配,避免吃到「## 我的明確立場：…」標題行裡的「立場」而誤抓後面的「淨分」等字。
    m = _re.search(r"(?m)^[>\s]*\**\s*立場\s*\**\s*[：:]\s*\**\s*([一-鿿/]+)", scoped)
    if m:
        label = m.group(1).strip()
        # 取「/」或標點前的第一個有效詞，避免吃到後面括號的解釋
        label = _re.split(r"[，,（()\s/]", label)[0].strip("*")
        out["label"] = label or None
    return out


def _extract_summary(text: str) -> str:
    """從 LLM markdown 分析中擷取「一句話總結」段落，用於頂部結論橫條。失敗回空字串。"""
    import re as _re
    if not isinstance(text, str):
        return ""
    # 匹配「## 一句話總結」或「## 十四、一句話總結」後的第一行
    m = _re.search(r"#+\s*[一二三四五六七八九十零\d]*、?\s*一句話(?:總結|結論)\s*\n+([^\n#]+)", text)
    if m:
        return m.group(1).strip().lstrip("*").rstrip("*").strip()
    return ""


def _extract_stance_section(text: str) -> str:
    """抽出「我的明確立場」段 body(理由/關鍵價位/操作建議/風險),供頂端結論卡使用。"""
    import re as _re
    if not isinstance(text, str):
        return ""
    m = _re.search(
        r"#{1,6}\s*(?:[一-十\d]+、)?我的明確立場[^\n]*\n"
        r"(.*?)(?=\n#{1,6}\s|\Z)", text, _re.S)
    return m.group(1).strip() if m else ""


def _strip_llm_sections(text: str, section_names: tuple) -> str:
    """把指定的 LLM 章節(含標題)整段自渲染文字移除(內容已上移到頂端結論卡)。"""
    import re as _re
    if not isinstance(text, str):
        return text
    for name in section_names:
        text = _re.sub(
            rf"#{{1,6}}\s*(?:[一-十\d]+、)?{_re.escape(name)}[^\n]*\n"
            rf".*?(?=\n#{{1,6}}\s|\Z)", "", text, flags=_re.S)
    return text.strip()


def _mask_malformed_numbers(text: str) -> str:
    """遮蔽 LLM 產出的畸形千分位數字(如「3,2424」——逗號後接 ≥4 位,絕非合法千分位)。
    這類多為 LLM 排版幻覺;寧可遮蔽也不留錯誤數字。合法「1,234」「12,345,678」不受影響。"""
    if not isinstance(text, str) or "," not in text:
        return text
    import re as _re
    pat = _re.compile(r"(?<![\d.])\d{1,3},\d{4,}(?:\.\d+)?")
    n = {"c": 0}

    def _sub(_m):
        n["c"] += 1
        return "(數值異常已略)"

    out = pat.sub(_sub, text)
    if n["c"]:
        print(f"[render] 遮蔽 {n['c']} 個畸形數字(LLM 千分位幻覺)", file=sys.stderr)
    return out


def _sanitize_llm_2330_prices(text: str, predictions: dict) -> str:
    """最後防線:LLM 若把 2330 寫成台積電 ADR 的美元價(約 400-500),用 Python 中樞值改回。
    2330 本地價約數千元(mid);台積電 ADR 美元價 ≈ mid 的 ~19%(約 mid×0.10~0.45 區間)。
    只在「同一行有提到 2330 / 台積電」且數字落在該離譜區間時保守改寫,避免誤傷 00662(約120)、
    0050(約100,落在更低、不在區間)或其他正常 2330 價(約 mid,落在區間之上)。"""
    import re as _re
    if not isinstance(text, str) or not isinstance(predictions, dict):
        return text
    mid = predictions.get("mid")
    if not isinstance(mid, (int, float)) or mid <= 0:
        return text
    lo, hi = mid * 0.10, mid * 0.45
    target = str(round(mid))
    # lookbehind 必須連逗號一起擋:2330 漲破 2000 後是四位數,LLM 常寫千分位「2,400 元」,
    # 若只擋 [\d.] 會匹配到逗號後的「400 元」(落在 ADR 區間)→ 誤修成「2,2392」→ 再被
    # _mask_malformed_numbers 遮成「(數值異常已略)」(2026-07 實際回歸)。加逗號即根治。
    num_re = _re.compile(r"(?<![\d.,])(\d{2,4}(?:\.\d+)?)\s*元")
    # 畸形千分位:合法格式逗號後必為恰 3 位(如 22,182);「2,2182」這種是 LLM 排版幻覺
    malformed_re = _re.compile(r"(?<![\d.])(\d{1,3},\d{4,}(?:\.\d+)?)\s*元")

    def _fix_line(line: str) -> str:
        if ("2330" not in line and "台積電" not in line) or "元" not in line:
            return line
        line = malformed_re.sub(f"{target} 元", line)

        def _sub(m):
            try:
                v = float(m.group(1))
            except ValueError:
                return m.group(0)
            if lo <= v <= hi:
                return m.group(0).replace(m.group(1), target)
            return m.group(0)
        return num_re.sub(_sub, line)

    return "\n".join(_fix_line(ln) for ln in text.split("\n"))

