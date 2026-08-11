"""LLM 分析輸出的後處理純函式(A5-Step1 由 morning_report 抽出)。
皆無網路/無狀態/不依賴 morning_report 其它符號;morning_report 以 re-export 保持向後相容,
既有測試(呼叫 mr.<fn>)零修改。"""
from __future__ import annotations

import json
import sys

# ── 契約版本(2026-08-08 自 llm_experiment 遷入:實驗已拆除,
#    而這兩個版本描述的是**報告管線本身**,與實驗無關)──
#: 後處理與渲染的契約版本。**動了它們就等於換了一個系統**,
#: 樣本因此不可比 —— 所以它們在同群鍵裡。
#:
#: 這兩個數字要手動維護。自動從程式碼推導(檔案雜湊、git SHA)正是本模組
#: 要避免的東西:那會讓「改一個註解」變成「換一個系統」。
#: v2(第二十四輪 P1-10):加深選優的**身分**補上四段可見欄位(見
#: `analysis_depth._identity`)。選優規則變了 = 發表出去的那一版可能不同。
POSTPROCESS_VERSION = 2
#: v2:段落語意修正+補回四欄位;v3:schema v2 深度渲染;
#: v4(第十七輪 P1-3):逐筆張力調和進信 —— 只印「訊號互有矛盾」等於沒處理。
#: v10(Commit C):`key_drivers` 多了 `cluster_id`,渲染的欄位集合
#: 因此改變(指紋會動的是欄位,不是版面)。
#: v11(Commit E):三大重點改事件卡(帶這件事的來歷:官方/幾個獨立
#: 來源/連續追蹤第幾天)、新增「各標的合計影響」、共用驅動的說明進信。
#: v12(第二十三輪):三大重點依 Python 計分排序(不依模型自評);
#: aggregator-only 事件寫「原始發布者未解析」而非「僅單一來源」。
RENDERER_VERSION = 12

#: 契約快照追蹤的版本欄位(2026-08-08 自 llm_experiment.COHORT_FIELDS 遷入:
#: 實驗已拆,但「哪些契約版本要被凍結追蹤」這份登錄簿必須活著 ——
#: 新增版本欄位而沒有快照,漏掉不會有任何人發現)。
#: `fallback_profile_version` 舊名 shadow_profile_version:影子已拆,
#: legacy prompt 現在是特化失敗時的**備援寫手**,版本仍要追蹤。
CONTRACT_VERSION_FIELDS = (
    "evidence_schema_version", "output_schema_version",
    "primary_profile_version", "fallback_profile_version",
    "postprocess_version", "renderer_version", "grounding_version")


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


#: 診斷裡帶多長的回應開頭。夠看出「這是不是 JSON」就好 ——
#: 這個欄位會進 run manifest(公開 repo),不放整份回應。
PARSE_HEAD_CHARS = 120


def _parse_llm_event_json(text: str, diag=None) -> list[dict]:
    """Accept a strict JSON array, with a small fence-tolerant recovery path.

    **回空陣列有五種完全不同的原因**(2026-08-11 生產):回應是空的、
    裡面根本沒有陣列(模型在講話)、**開了括號沒有收**(被截斷)、
    陣列解不開、包了 `{"events": …}` 但那不是陣列。
    上一版四種都回 `[]`,而呼叫端記下的是 `parsed=0, outcome="ok"` ——
    「抽取器吃了 35 筆、活到下游 0 筆」連續多天,而離線分不出是哪一段。
    `diag` 給呼叫端一個 dict,這裡把原因與回應形狀填進去
    (**處置不同的原因要分得開**:沒回應是 provider 問題、解不開是
    格式問題、沒有陣列多半是模型在講話而不是輸出 JSON)。

    批#95(第九輪 P1-4):也接受 Structured Outputs 的 `{"events": [...]}`。
    OpenAI strict 模式要求根節點是 **object**,所以那條路徑回的是包了一層的
    物件。下面的括號掃描其實「剛好」也能從它裡面挖出陣列 —— 但那是巧合,
    而巧合會在某天有人在 events 之前多放一個含 `[` 的欄位時安靜地壞掉。
    先正式解析,失敗才退回掃描。
    """
    raw = (text or "").strip()
    _d = diag if isinstance(diag, dict) else {}
    _d.update({"chars": len(raw), "head": raw[:PARSE_HEAD_CHARS],
               "kind": "empty_response" if not raw else "unknown"})
    if not raw:
        return []
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and isinstance(obj.get("events"), list):
            _d["kind"] = "ok_object"
            return [it for it in obj["events"] if isinstance(it, dict)][:40]
        if obj is None:
            _d["kind"] = "bad_json_object"
        else:
            _d["kind"] = "object_without_events"
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < start:
        # **開了括號卻沒有收 = 被截斷**,不是「模型在講話」(外審 r1)。
        # 兩者的處置不同:截斷要減量重試或調高輸出額度,而模型講話要
        # 改 prompt/schema —— 壓成同一個名字就等於沒有診斷。
        if start >= 0:
            _d["kind"] = "truncated_array"
        elif _d.get("kind") in ("unknown", "", None):
            _d["kind"] = "no_array_found"
        return []
    try:
        parsed = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        _d["kind"] = "bad_json_array"
        _d["error"] = str(e)[:80]
        return []
    if not isinstance(parsed, list):
        _d["kind"] = "array_is_not_a_list"
        return []
    out = [item for item in parsed if isinstance(item, dict)][:40]
    _d["kind"] = "ok_array" if out else "array_without_objects"
    return out


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


#: 立場段的**結構鷹架**:prompt 用「第 N 行 — 用途」交代格式,而模型會把
#: 那些標題**原樣抄進輸出**(2026-08-10 實信:結論卡出現「第 1 行 — 11 維
#: 計分行」「第 2 行 — 立場標籤」「第 3 行 — 理由」三行,而 11 維計分行
#: 本身早被 `_strip_stance_calculation` 拿掉 —— 讀者看到的是三個空標題)。
#: 這是批#29「指令回音」的同一個形狀,處置也一樣:prompt 明講不要抄,
#: 再加一道確定性的移除(模型的輸出不歸我們管,prompt 只降低機率)。
#: 鷹架的**標題**:行首的「第 N 行」(或「第 4-6 行」)一路到收尾的冒號
#: 或行尾。**辨識與移除是同一條** —— 兩條各寫一份會漂移:第一版辨識端
#: 要求「行」後面緊接破折號或冒號,於是「第 4-6 行(每行獨立成段):」
#: 這種全形括號開頭的整條漏掉(外審 r1 的測試當場抓到)。
#: 行首錨定才是判準;長度上限只是不讓它吞掉一整段。
_STANCE_SCAFFOLD_RE = (r"^[>\s]*[*_]{0,3}\s*第\s*\d+(?:\s*[-–—]\s*\d+)?"
                       r"\s*行[^:：\n]{0,60}(?:[:：]|$)")
#: 只印一次立場:頂端 KPI 條已經有「立場 中性」,結論卡再寫一行是重複
#: (使用者 2026-08-10)。**只砍「標籤本身自成一行」**的那種 ——
#: 帶理由的句子(「立場：中性,因為…」)是內容,不是重複。
#: 這道移除在 `_extract_stance` **之後**才跑(它讀的是整份 analysis,
#: 不是這一段)—— 與 `_strip_stance_internals` 同一個先後理由。
_STANCE_LABEL_ONLY_RE = (r"(?m)^[>\s]*[*_]{0,3}\s*立場\s*[*_]{0,3}\s*[:：]"
                         r"\s*[*_]{0,3}\s*(?:偏多|偏空|中性|資料不足)"
                         r"\s*[*_]{0,3}\s*[。.]?\s*$")


#: 一段文字要多長才值得當成「指令回音」比對。太短的片段(「觀望」)在
#: prompt 裡也找得到,那不是回音,是正常用詞。
_ECHO_MIN_CHARS = 12


def _norm_echo(s) -> str:
    """比對用的正規化:排版差異(空白、markdown 強調、引號)不算差異。"""
    import re as _re
    return _re.sub(r"[\s*_>`\"'“”「」【】]", "", str(s or ""))


def _instruction_chunks(instructions) -> tuple:
    """把 prompt 的指令原文切成可比對的靜態片段。

    **判準是「這句話是不是我們寫的」,而那有唯一答案:去 prompt 原文裡找。**
    外審連五輪指的是同一件事的不同長度(只砍標題 → 砍到冒號 → 逐句 →
    逐片語),而每一次都有更長的一段漏掉 —— 因為「片語清單」本來就不是
    要量的東西。改成拿**原文本身**比對之後,任意長度的逐字抄回都涵蓋,
    而且沒有一張會漂移的清單。

    佔位符(`{…}`)的值每天不同,所以只拿它兩側的靜態片段比對。
    """
    import re as _re
    out = []
    for chunk in _re.split(r"\{[^{}]*\}", str(instructions or "")):
        n = _norm_echo(chunk)
        if len(n) >= _ECHO_MIN_CHARS:
            out.append(n)
    return tuple(out)


#: **句內**的指令片語:同一句裡混著指令與真數據時(「原樣引用 2,396 元,
#: 站上偏強」),整句不是原文的子字串,上面那條比不到 —— 而把價位一起丟掉
#: 比留著四個字更糟(外審 r4)。這張表只處理這種混合句,而且每一條都被
#: 守衛釘在 `_STANCE_FORMAT_BLOCK` 上(`tests/test_markdown.py`):
#: prompt 改寫而它沒跟上,測試當場紅。
_STANCE_PROMPT_ECHOES = (
    "說明為什麼是這個立場",
    "每句必附數據",
    "每行獨立成段",
    "原樣引用",
    "不可自行更動",
    "不可改用 ADR 美元價",
    "不要抄進輸出",
    "是給你的指令",
    "禁止只寫",
)


def _strip_stance_scaffolding(text: str, instructions: str = "") -> str:
    """移除立場段裡「prompt 的格式說明」與重複的立場標籤行。

    兩者都不是分析內容:前者是給模型的指令被抄了回來,後者在頂端 KPI 條
    已經出現過。理由句、關鍵價位、操作建議、風險一律不動 —— 鷹架與正文
    寫在同一行時**只砍鷹架**(外審 r1:整行刪會把理由一起帶走)。

    `instructions` 是 prompt 的指令原文(`_STANCE_FORMAT_BLOCK`)。給了它
    才比得出「這句話是我們寫的」;不給只做鷹架與標籤的移除 —— 那是
    降級不是失效(舊呼叫端與單元測試仍然可用)。
    """
    import re as _re
    if not isinstance(text, str) or not text:
        return text
    chunks = _instruction_chunks(instructions)

    def _is_quoted(seg: str) -> bool:
        """整句(或整行)就是 prompt 原文的一段 —— 任意長度都涵蓋。"""
        n = _norm_echo(seg)
        return len(n) >= _ECHO_MIN_CHARS and any(n in c for c in chunks)

    def _has_phrase(s: str) -> bool:
        return any(e in s for e in _STANCE_PROMPT_ECHOES)

    out = []
    for ln in text.split("\n"):
        if _re.search(_STANCE_LABEL_ONLY_RE, ln):
            continue                    # 立場標籤自成一行 = 重複,不留
        m = _re.match(_STANCE_SCAFFOLD_RE, ln)
        rest = ln[m.end():] if m else ln
        if _is_quoted(rest) or _has_phrase(rest):
            kept = []
            for c in _re.findall(r"[^。！？!?]*[。！？!?]|[^。！？!?]+", rest):
                if _is_quoted(c):
                    continue            # 整句是我們寫的字
                if not _has_phrase(c):
                    kept.append(c)
                    continue
                # **句內把片語拿掉,剩下的還有東西就留**(外審 r4):
                # 「原樣引用 2,396 元,站上偏強」—— 逐句丟會把 Python
                # 算出來的價位一起帶走。拿掉片語後只剩標點的才整句不要。
                for e in _STANCE_PROMPT_ECHOES:
                    c = c.replace(e, "")
                if _re.sub(r"[\s，、。；：,.;:！？!?*_>（）()「」【】\-—–]", "", c):
                    kept.append(c)
            rest = "".join(kept)
        elif not m:
            out.append(ln)              # 沒鷹架也沒回音 → 原樣保留
            continue
        rest = rest.strip().strip("*_> 　").lstrip("，,、；;。.:：")
        if rest:                        # 還有正文 → 只砍鷹架與指令
            out.append(rest)
    return "\n".join(out).strip("\n")


def _strip_stance_internals(text: str, extra_bad: str = "") -> str:
    """散文層安全網(批#26 使用者:理由不要出現計分內部):在「我的明確立場」
    理由句裡移除「11 維中 X 項偏空」「N 項偏多」「淨分 ±N」「距門檻…」等
    計分細節子句(以中文標點切段,丟含關鍵詞的子句)。prompt 已禁,此為雙保險。
    **必須在 _extract_stance 之後才呼叫**(擷取依賴「淨分」)。
    extra_bad:額外壞詞 regex(OR 進 _bad),供呼叫端加段落專屬禁詞而不影響其他段
    ——批#28 r2:多空交鋒段另禁獨立「11 維」(_strip_stance_internals 原本只認「維中」)。"""
    import re as _re
    if not isinstance(text, str) or not any(
            k in text for k in ("維", "項偏", "淨分", "門檻")):
        return text
    _pat = r"(維中|項偏空|項偏多|淨分|距.{0,6}門檻"
    _pat += ("|" + extra_bad) if extra_bad else ""
    _bad = _re.compile(_pat + ")")
    # 立場標籤行(**立場:偏空**、立場:中性…)必須外科式移除計分片語——整段
    # 刪除會把標籤本身丟掉、留下畸形「**立場:」(Codex 批#26 r8)
    _label = _re.compile(r"立場\**\s*[：:]")

    def _clean_line(line: str) -> str:
        if not _bad.search(line):
            return line
        if _label.search(line):
            return _strip_score_phrases(line)
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


def _sanitize_debate_section(text: str) -> str:
    """批#28(Codex r1):多空交鋒段(七之五)的計分內部安全網——只抽出「多空交鋒」
    段套 _strip_stance_internals(clause 刪除計分子句),**其餘段落不動**(八段的
    正當「距突破門檻」等語言要保留,批#26 F2)。prompt 已禁 LLM 在此段寫計分
    內部,此為 render 端雙保險:若 LLM 違規寫「淨分 +6」「11 維中 7 項偏多」即移除。"""
    import re as _re
    if not isinstance(text, str) or "多空交鋒" not in text:
        return text
    # 抽「## …多空交鋒…」標頭到下一個標頭(或文末)的整段,只過濾這段
    m = _re.search(r"(?ms)^(#{1,6}[^\n]*多空交鋒.*?)(?=^#{1,6}\s|\Z)", text)
    if not m:
        return text
    # 辯論段另禁獨立「11 維(度/模型/計分…)」(prompt 禁詞;基本組只認「維中」);
    # 負向前瞻排除「11 維持」(如「VIX 11 維持低檔」為正當論點,勿誤刪)
    cleaned = _strip_stance_internals(m.group(1), extra_bad=r"11\s*維(?!持)")
    return text[:m.start(1)] + cleaned + text[m.end(1):]


def _strip_score_phrases(text: str) -> str:
    """外科式移除計分片語,保留立場標籤與動作(Codex 批#26 r2/r4:一句話總結是
    「立場+動作」單行)。只挖「淨分 ±N 距…門檻」「N 維中 X 項」「N 項偏空/多」;
    括號組只清內部計分片語,**清完仍有內容(如動作建議)則保留括號**,只有清
    到空才連括號刪。"""
    import re as _re
    if not isinstance(text, str) or not any(
            k in text for k in ("維中", "項偏", "淨分", "門檻")):
        return text

    # 計分片語尾巴:一路吃到「標點/括號」或「動作詞」為止——無長度上限
    # (Codex r6:8 字硬限會把「維中共有 7 項指標偏空」截成碎片;r6:遇動作詞
    # 即止,不吞緊接的操作建議「門檻 2 分建議減碼」)
    _tail = (r"(?:(?!建議|加碼|減碼|觀望|買|賣|布局|進場|逢低|接刀|留意|操作)"
             r"[^，、；。,;）)])*")

    def _clean_inner(s: str) -> str:
        s = _re.sub(r"淨分\s*[:：=為]?\s*[+\-]?\d+", "", s)
        # 獨立「距…門檻…」子句(逗號分隔或直接接淨分皆可,Codex r5;尾巴遇動作詞止)
        s = _re.sub(r"距(?:(?!門檻)[^，、；。,;）)]){0,10}門檻" + _tail, "", s)
        s = _re.sub(r"\d+\s*維中" + _tail, "", s)
        s = _re.sub(r"\d+\s*項偏[空多]", "", s)
        # 清完後段內連續標點去重(避免留下「，，」)
        return _re.sub(r"([，、；。,;])\s*(?=[，、；。,;])", "", s)

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



#: 「解釋為什麼寫這一則」的措辭 —— 那類句子的功能就是交代入選緣由,
#: 而入選緣由正是不該出現在信裡的東西(R15/R15b)。
#: **不能只寫「使用者」**:「使用者付費/使用者體驗/使用者數」在科技新聞極常見,
#: 那樣會把合法內容整句刪掉(自測:四則正常新聞全被誤刪)。
#: 這裡要抓的是「指涉**這封信的讀者**」的用法,所以綁定後綴詞。
_RATIONALE_MARKERS = (
    "使用者要求", "使用者關注", "使用者指定", "使用者核心", "使用者高度",
    "使用者持股", "使用者需求", "讀者要求", "讀者關注", "讀者持股",
    "本報固定", "本報高度", "本報核心", "本報追蹤", "本報關注",
    "為您", "依您", "您的",
)


def _strip_selection_rationale(text: str) -> str:
    """移除揭露入選緣由/關注清單的句子。

    r1(Codex,P1):render 防線先前是把「使用者」整詞替換成「本報」,結果
    「使用者核心觀察」→「**本報核心觀察**」——**防線自己製造出 R15b 禁止的
    揭露**(等於公開一份關注清單)。而既有測試還斷言了那個結果,把缺陷釘成規格。

    正解是**整句移除**而不是換個說法:這類句子不含任何事實,拿掉不會損失資訊。
    以中文句界切句,只丟含標記的那一句,其餘原樣保留。
    """
    import re as _re
    raw = str(text or "")
    if not any(m in raw for m in _RATIONALE_MARKERS):
        return raw
    # r2(Codex,P2):**字元類必須用碼位寫**。我上一版直接打全形括號,
    # 但字元在編輯管線中被轉成 ASCII —— 於是全形括號的註記完全不被辨識,
    # 整句(連同事實)被丟掉。批#54 才因為同一個原因(子句分隔符全形被轉成
    # 半形)踩過一次,當時的結論就是「用碼位,不要直接打字元」。
    _LP, _RP = chr(0xFF08), chr(0xFF09)          # 全形左右括號
    _open, _close = "[(" + _LP + "]", "[)" + _RP + "]"
    _inner = "[^()" + _LP + _RP + "]*"
    _markers = "|".join(_re.escape(m) for m in _RATIONALE_MARKERS)
    _aside = _re.compile(_open + _inner + "(?:" + _markers + ")"
                         + _inner + _close)

    out, dropped = [], 0
    for line in raw.split("\n"):
        parts = _re.split(r"(?<=[。;；!！?？])", line)
        kept = []
        for seg in parts:
            # 先剝掉**括號註記**裡的緣由(如「(2882,使用者核心觀察)」)。
            # 這類措辭多半寄生在括號裡,整句丟會把同一句的事實一起丟掉
            # ——自測踩到:「國泰金(2882,使用者核心觀察):子公司公告。」整句消失。
            cleaned = _aside.sub("", seg)
            if any(m in cleaned for m in _RATIONALE_MARKERS):
                dropped += 1          # 剝完仍違規 → 整句丟(它本身就是一句緣由)
                continue
            kept.append(cleaned)
        out.append("".join(kept))
    if dropped:
        import sys as _sys
        print(f"[render] ⚠ LLM 輸出含入選緣由措辭(違反 R15/R15b),"
              f"已移除 {dropped} 句", file=_sys.stderr)
    return "\n".join(out)
