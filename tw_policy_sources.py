# -*- coding: utf-8 -*-
"""台灣政策的**一手**資料源:行政院公報每日 XML。

**為什麼需要這支模組**

晨報的政策資訊先前只靠新聞 RSS 間接取得,於是有兩個結構性缺陷:
1. 拿到的是**媒體轉述**而非一手文件,適用條件、金額級距、上路日期常缺漏或錯誤;
2. 新政策名詞(如「台灣未來帳戶」)會被預先寫死的關鍵字白名單漏掉——
   白名單只認得舊詞,新政策在評分前就被整條剔除。

行政院公報同時解掉這兩個:
- `HTMLContent` 是**法令原文全文**(實測 1,118~8,985 字),含主旨、依據、
  公告事項逐點、法條全文 → 「先完整詳述措施」的素材直接就在這裡,不必讓 LLM
  從新聞轉述裡猜。
- `Category` 自帶**中文分類碼**(510 財政稅務、550 產業管理、890 勞健保、
  140 地政…)→ 以分類碼過濾取代關鍵字白名單,結構上不可能漏詞。
- `Keyword` 是**政府自己標註的政策名詞**。實測 2026-07-24 的院會那筆標出
  「青年安心成家方案;優惠貸款;補貼利息;貸款額度」——這正是「新青安為什麼被
  白名單漏掉」的解答:政策名詞自動發現,答案在政府自己標的欄位裡。
- `Comment_Deadline` 是法規草案的意見徵詢截止日 → 政策的**領先指標**
  (實測到經濟部預告「虛擬通貨商業」業別修正草案)。

**已知限制**(實測,不是推測)
- 端點只回傳**最新一個出刊日**;`?date=1150723` 參數無效(回傳完全相同的內容)。
  必須每工作日抓一次並自行累積,漏掉的日子只能逐筆補。
- 站方憑證缺 Subject Key Identifier,Python 3.13 預設的 VERIFY_X509_STRICT
  會拒絕握手(curl 正常)。故必須走 relaxed-strict 抓取——那只放寬 RFC 5280
  的選用欄位檢查,**仍完整驗證簽章鏈與主機名**,不是 verify=False。
- 端點會 302 轉址到 /old/ 路徑,抓取端必須跟隨轉址。
- 法規資料庫查無「青年安心成家」——它是行政院核定的**方案**不是法規。
  故公報/院會決議不可被法規資料庫取代。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

GAZETTE_XML_URL = "https://gazette.nat.gov.tw/egFront/OpenData/downloadXML.jsp"

# 與本報讀者相關的公報分類碼(取碼前綴比對,因 Category 可能多值如
# "550(產業管理);1Z0(其他)")。刻意用**分類碼**而非關鍵字:政策名詞會變,
# 分類碼是政府自己的固定本體。
FOCUS_CATEGORY_CODES = {
    "510": "財政稅務",
    "520": "金融",
    "550": "產業管理",
    "890": "勞健保",
    "140": "地政",
    "150": "營建",
    "710": "農業",
    "770": "環境保護",
    "910": "國家發展",
    "920": "行政管理",
}

_CATEGORY_CODE_RE = re.compile(r"(\d[0-9A-Z]{2})")

# 公報每日筆數不多(實測 12 筆/日),取全量不設上限;此值只是防呆上界。
MAX_RECORDS = 60


# 公報 Keyword 欄位混了兩種東西:具體政策名詞(「青年安心成家方案」)與通用
# 行政動作詞(「審查」「申報」「稽查」)。後者每天都出現、對「新政策偵測」是純雜訊。
# 兩道過濾:①長度門檻(中文政策名多為 4 字以上)②通用詞停用表。
# 另外novelty 本身會自我修正——通用詞幾天內就全進歷史庫,之後只剩真正的新詞。
_KEYWORD_MIN_LEN = 4
_KEYWORD_STOPWORDS = {
    "審查", "申報", "稽查", "監視", "錄影", "備查", "核定", "公告", "通知",
    "罰則", "附則", "施行", "生效", "廢止", "修正", "訂定", "程序", "作業",
    "管理", "輔導", "獎勵", "補助", "資格", "文件", "表格", "期限",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t　]+")
_NL_RE = re.compile(r"\n{2,}")


def _strip_tags(html_text: str) -> str:
    """HTMLContent 是原始 HTML(含 div/p/span 與註解)。去標籤後才可進 prompt,
    否則標籤本身會佔掉素材預算、也會干擾模型閱讀。"""
    import html as _html
    s = re.sub(r"<!--.*?-->", " ", html_text or "", flags=re.S)
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)
    s = _TAG_RE.sub("\n", s)
    s = _html.unescape(s)
    s = _WS_RE.sub(" ", s)
    s = "\n".join(ln.strip() for ln in s.splitlines())
    return _NL_RE.sub("\n", s).strip()


def _text(node, tag: str) -> str:
    return (node.findtext(tag) or "").strip()


class GazetteUnavailable(RuntimeError):
    """公報抓取或解析失敗。

    r3(Codex F2):**刻意讓失敗浮出來**。原本 fetch/parse 各自把例外吞成空清單,
    於是 main 裡那個 try/except 對這兩種失敗是**死碼**——來源整個消失時,程式只會
    印「公報 0 筆」,和「今日公報沒有關注分類」長得一模一樣,`_DEGRADED_STEPS`
    也不會有紀錄,run manifest 完全看不出來。

    這是同一個錯誤模式的第三次(批#32 的 _DEGRADED_STEPS、批#33 的
    save_history_state 都是「在自己吞例外的函式外面包 try」)。正解是讓呼叫端
    拿得到失敗事實,再由呼叫端決定降級——降級與靜默是兩回事。
    """


def parse_gazette_xml(raw: bytes | str) -> list[dict]:
    """把公報 XML 轉成記錄清單。XML 壞掉時拋 GazetteUnavailable。"""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise GazetteUnavailable(f"公報 XML 解析失敗: {type(e).__name__}") from e
    out: list[dict] = []
    for rec in list(root)[:MAX_RECORDS]:
        title = _text(rec, "Title")
        if not title:
            continue
        category_raw = _text(rec, "Category")
        out.append({
            "meta_id": _text(rec, "MetaId"),
            "title": title,
            "publisher": _text(rec, "PubGovName"),
            "date_published": _text(rec, "Date_Published"),
            "comment_deadline": _text(rec, "Comment_Deadline"),
            "category_raw": category_raw,
            "category_codes": _CATEGORY_CODE_RE.findall(category_raw),
            "keywords": [k.strip() for k in _text(rec, "Keyword").split(";") if k.strip()],
            "theme_subject": _text(rec, "ThemeSubject"),
            "explain": _text(rec, "Explain"),
            "content": _strip_tags(_text(rec, "HTMLContent")),
            "link": _text(rec, "PreviewStageURL") or _text(rec, "GazetteHTML"),
        })
    return out


def is_focus_record(rec: dict) -> bool:
    """是否落在本報關注的分類碼。Category 可多值,任一命中即算。"""
    return any(code in FOCUS_CATEGORY_CODES for code in (rec.get("category_codes") or []))


def focus_labels(rec: dict) -> list[str]:
    """命中的分類中文名(供顯示與 LLM 素材標註)。"""
    return [FOCUS_CATEGORY_CODES[c] for c in (rec.get("category_codes") or [])
            if c in FOCUS_CATEGORY_CODES]


def fetch_gazette(fetch) -> list[dict]:
    """抓取並解析當日公報。

    `fetch` 是注入的抓取函式(url, timeout) -> bytes,由呼叫端提供
    ——刻意用依賴注入而非 import morning_report:避免循環匯入,也讓測試不必碰網路。

    失敗時拋 GazetteUnavailable(見該類別說明):降級是呼叫端的決定,本函式
    只負責把「拿不到」與「拿到但今天沒東西」區分開來。空清單只代表後者。
    """
    try:
        raw = fetch(GAZETTE_XML_URL, timeout=30)
    except Exception as e:
        raise GazetteUnavailable(f"公報抓取失敗: {type(e).__name__}") from e
    return parse_gazette_xml(raw)


def discover_new_keywords(records: list[dict], seen: set[str],
                          max_new: int = 12) -> list[str]:
    """找出**首次出現**的政策名詞。

    這是取代關鍵字白名單的機制:不預先寫死要找什麼詞,而是拿政府自己標註的
    Keyword 與歷史庫比對,沒見過的就是新政策名詞候選。

    只看關注分類的記錄——否則國防、文化等分類的專有名詞會把清單灌爆。
    """
    fresh: list[str] = []
    for rec in records:
        if not is_focus_record(rec):
            continue
        for kw in rec.get("keywords") or []:
            if not _is_candidate_policy_term(kw) or kw in seen or kw in fresh:
                continue
            fresh.append(kw)
            if len(fresh) >= max_new:
                return fresh
    return fresh


def _is_candidate_policy_term(kw: str) -> bool:
    """通用行政動作詞不算新政策名詞。

    比對長度時先剝掉括號別名(「土石方資源堆置處理場(土資場)」的主名才是重點),
    並以主名比對停用表——否則「審查(初審)」這種寫法會繞過停用表。
    """
    if not kw:
        return False
    main = re.split(r"[（(]", kw, maxsplit=1)[0].strip()
    if not main or main in _KEYWORD_STOPWORDS:
        return False
    return len(main) >= _KEYWORD_MIN_LEN


def format_gazette_block(records: list[dict], sanitize, limit: int = 6,
                         content_chars: int = 1200) -> str:
    """組給 LLM 的一手法令素材塊。

    `sanitize` 由呼叫端注入(morning_report 的 _external_text),確保所有外部
    字串都過同一個消毒入口——這是本專案的既有鐵律,模組不得自行繞過。
    回傳空字串代表今日無關注分類的公報,呼叫端應整段省略而非寫「無」。
    """
    picked = [r for r in records if is_focus_record(r)][:limit]
    if not picked:
        return ""
    lines: list[str] = []
    for rec in picked:
        labels = "、".join(focus_labels(rec)) or "其他"
        head = f"■【{labels}】{sanitize(rec.get('publisher'), 30)}:{sanitize(rec.get('title'), 120)}"
        lines.append(head)
        # **引用 id 要印出來**(2026-08-24 生產:`taiwan_policy` 連兩天在
        # 同一筆公報上被 schema 擋掉)。prompt 要求公報項目填
        # `source_item_id`,但這個素材塊先前**沒有印過任何 id 欄位** ——
        # 模型只能從內文或連結猜一個數字出來,必然對不上 registry。
        # 印**完整字串**而不是裸 id:讓模型照抄,而不是自己組合前綴。
        if rec.get("meta_id"):
            lines.append(f"  引用 id:gazette:{sanitize(rec.get('meta_id'), 40)}")
        if rec.get("date_published"):
            lines.append(f"  發布日:{sanitize(rec.get('date_published'), 30)}")
        if rec.get("comment_deadline"):
            # 草案預告 = 政策領先指標,明確標出讓 LLM 知道這是「還沒定案」
            lines.append(f"  意見徵詢截止:{sanitize(rec.get('comment_deadline'), 30)}"
                         f"(法規草案預告,尚未定案)")
        if rec.get("theme_subject"):
            lines.append(f"  官方摘要:{sanitize(rec.get('theme_subject'), 200)}")
        if rec.get("explain"):
            lines.append(f"  修正說明:{sanitize(rec.get('explain'), 400)}")
        if rec.get("content"):
            lines.append(f"  法令原文:{sanitize(rec.get('content'), content_chars)}")
        if rec.get("keywords"):
            lines.append("  政策名詞:" + "、".join(
                sanitize(k, 20) for k in rec["keywords"][:10]))
    return "\n".join(lines)
