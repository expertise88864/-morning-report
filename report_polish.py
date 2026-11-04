"""信件內容的確定性整理(2026-11-04 使用者看實信後逐項要求)。

指令不是守衛:用語統一、段落順序與編號、昨天寫過的舊聞、已公布的財報,都由 Python 決定。
只讀昨日存檔、不做網路呼叫;呼叫端各自 try,任何失敗退回原值 —— 晨報不可斷。
"""
from __future__ import annotations

import datetime as dt
import gzip
import html as htmllib
import re

_TERMS = re.compile("荷莫茲|霍爾木茲")
_HEADING = re.compile(r"(?m)^(?=#{1,3}\s*[一二三四五六七八九十]+(?:之[一二三四五六七八九十]+)?、)")


def normalize_terms(text: str) -> str:
    """同一個地名全信一種寫法(實信:同一條裡「荷姆茲」「荷莫茲」並存)。"""
    return _TERMS.sub("荷姆茲", str(text or ""))


def order_sections(text: str) -> str:
    """七之四固定排在七之二後面(實信:11-03 在信末、11-04 在七之二後,位置由模型決定)。"""
    parts = _HEADING.split(str(text or ""))
    delta = next((p for p in parts if re.match(r"#{1,3}\s*七之四、", p)), None)
    anchor = next((i for i, p in enumerate(parts) if re.match(r"#{1,3}\s*七之二、", p)), None)
    if delta is None or anchor is None:
        return text
    parts.remove(delta)
    block = delta if delta.endswith("\n") else delta + "\n\n"
    parts[anchor] = parts[anchor] if parts[anchor].endswith("\n") else parts[anchor] + "\n\n"
    parts.insert(anchor + 1, block)
    return "".join(parts).rstrip("\n") + ("\n" if str(text).endswith("\n") else "")


#: 隱藏段落後編號跳號(二、五、七…)。只改寄出前的顯示,內部段落名稱不動。
_DISPLAY = {"二、總經指標": "一、總經指標", "五、行情與開盤展望": "二、行情與開盤展望",
            "七、昨夜三大重點": "三、昨夜三大重點", "七之二、世界大事速覽": "四、世界大事速覽",
            "七之四、敘事變化": "五、敘事變化", "八、科技板塊脈動": "六、科技板塊脈動",
            "九、其他類股資訊": "七、其他類股資訊", "十、台灣本地動態": "八、台灣本地動態",
            "十之二、重大政策深度解析": "九、重大政策深度解析"}
_DISPLAY_RE = re.compile(r"(?<=>)(\s*)(" + "|".join(map(re.escape, _DISPLAY)) + ")")
_GRADE_NOTE = ('<div style="font-size:12px;color:#64748b;margin:0 0 8px;">'
               "※ A 級＝有具體數字或正式公告；B 級＝只有方向性訊號、資訊有限。信心＝對這個判斷的把握。</div>")
_TECH_H = re.compile(r"(<h[1-6]\b[^>]*>\s*六、科技板塊脈動[^<]*</h[1-6]>)")


def display_pass(html: str) -> str:
    """只用在寄出去的那份(存檔與跨日回顧靠內部段落名,外審 PR #41):重新編號、換掉寫稿用語、
    補 A/B 級說明。失敗就寄原信。"""
    try:
        out = _DISPLAY_RE.sub(lambda m: m[1] + _DISPLAY[m[2]], str(html or ""))
        out = out.replace("新聞清單", "新聞")
        return _TECH_H.sub(lambda m: m[1] + _GRADE_NOTE, out, count=1)
    except Exception as exc:  # noqa: BLE001 - 晨報不可斷
        print(f"::warning::display_pass_failed:{type(exc).__name__}", flush=True)
        return html


_TENNIS_500 = ("brisbane", "abu dhabi", "adelaide", "linz", "charleston", "stuttgart", "queen's", "berlin",
               "bad homburg", "eastbourne", "washington", "dc open", "seoul", "ningbo", "tokyo", "pan pacific",
               "strasbourg", "monterrey", "merida", "mérida")


def tennis_extra_tier(low_name: str) -> tuple | None:
    """年終賽與 500 級也要認得:級別空白才會被當低級別不列(外審 PR #41)。"""
    if "wta finals" in low_name or "atp finals" in low_name:
        return (1, "年終賽")
    return (2, "500 級") if any(k in low_name for k in _TENNIS_500) else None


def session_pct(closes: list) -> float | None:
    """最近兩個收盤算漲跌幅;台股單日上限 10%,超出視為資料有誤。"""
    try:
        prev, last = float(closes[-2]), float(closes[-1])
        pct = (last - prev) / prev * 100
    except (IndexError, TypeError, ValueError, ZeroDivisionError):
        return None
    return round(pct, 2) if abs(pct) <= 11 else None


def taiex_card_text(pred: dict | None) -> str:
    """廣度卡第一句:昨日加權收盤與漲跌(實信:跌 3.6% 的隔天,信裡找不到這個數字)。"""
    pct, close = (pred or {}).get("last_session_pct"), (pred or {}).get("last_close")
    if pct is None or not close:
        return ""
    color = "#dc2626" if pct > 0 else "#16a34a" if pct < 0 else "#64748b"
    return f'加權指數 <b>{close:,.0f}</b> <b style="color:{color};">{pct:+.2f}%</b>　｜ '


def big_move_rule(pred: dict | None, breadth: dict | None) -> str:
    """大波動日才加的寫稿規則:結論要交代,不可淡化。門檻由 Python 判,不交給模型。"""
    pct, ratio = (pred or {}).get("last_session_pct"), (breadth or {}).get("advance_ratio")
    big = (pct is not None and abs(pct) >= 2) or (ratio is not None and (ratio <= 20 or ratio >= 80))
    if not big:
        return ""
    move = "、".join(([f"加權 {pct:+.2f}%"] if pct is not None else [])
                    + ([f"上漲佔比 {ratio:.1f}%"] if ratio is not None else []))
    return (f"\n  ⚠ 昨日台股大幅波動({move}):「今日結論」必須寫出這個幅度與主因,"
            "「十、台灣本地動態」第一條交代哪些族群領漲或領跌。原因只能引用新聞或行情資料,查不到就寫"
            "「新聞未見單一原因」;不可用「不是全面崩潰」這類說法淡化。")


#: 財報日曆(yfinance)的代號 → 新聞裡的寫法。
_EARNINGS_NAMES = {"NVDA": "NVDA|輝達|NVIDIA", "AAPL": "AAPL|蘋果|Apple", "MSFT": "MSFT|微軟|Microsoft",
                   "AVGO": "AVGO|博通|Broadcom", "TSLA": "TSLA|特斯拉|Tesla", "AMD": "AMD|超微",
                   "GOOGL": "GOOGL|Alphabet|谷歌|Google", "META": "Meta", "MU": "美光|Micron", "QCOM": "QCOM|高通|Qualcomm"}
_RESULT = re.compile(r"財報|營收|EPS|earnings|results", re.I)
_OUT = re.compile(r"超預期|優於|不如預期|低於預期|年增|季增|beats?\b|miss(?:es)?\b|tops?\b", re.I)
_PREVIEW = re.compile(r"將公布|將於|今晚|今夜|明晚|明日|前瞻|即將|本週|下週|登場|預告|等待|市場預期|分析師預期|預估|料將|ahead of|preview|before earnings|will report", re.I)


def drop_reported_earnings(events: list, news: list, today: dt.date) -> list:
    """實信:七段已寫 AMD 財報大漲,風險事件還列「11/04 盤後 AMD 財報」。
    只拿掉今明兩天、新聞標題已看得出結果的那一筆;預告型標題不算。"""
    out = []
    for ev in events or []:
        ticker = str(ev.get("title") or "").removesuffix(" 財報")
        names = _EARNINGS_NAMES.get(ticker)
        near = isinstance(ev.get("date"), dt.date) and ev["date"] <= today + dt.timedelta(days=1)
        if names and near and any(
                re.search(rf"(?<![A-Za-z])(?:{names})(?![A-Za-z])", t) and _RESULT.search(t)
                and _OUT.search(t) and not _PREVIEW.search(t)
                for t in (str(n.get("title") or "") for n in news or [])):
            continue
        out.append(ev)
    return out


#: 分析段落之後的區塊(排名標題依分數有兩種;排名可能整段缺席):之後是行情、體育,不能拿來比對。
_ANALYSIS_ENDS = ("台股波段觀察", "台股客觀關注", "近 5 日資金輪動", "個股排名", "Podcast 重點", "體育快訊", "在地快訊")


def load_previous(directory, today: dt.date, lookback_days: int = 4) -> dict:
    """前一封信寫過什麼:連結 + 分析段落(不含行情表與個股卡,那些數字每天都會再出現)。"""
    for back in range(1, lookback_days + 1):
        path = directory / f"{today - dt.timedelta(days=back)}.html.gz"
        if not path.exists():
            continue
        raw = gzip.open(path, "rt", encoding="utf-8").read()
        links = [htmllib.unescape(u) for u in re.findall(r'href="([^"]+)"', raw)]
        text = htmllib.unescape(re.sub(r"<[^>]+>", "", re.sub(r"</(?:p|li|div|tr|h\d)>", "\n", raw)))
        start = text.find("昨夜三大重點")
        ends = [i for i in (text.find(m, start) for m in _ANALYSIS_ENDS) if i > 0]
        body = text[start:min(ends) if ends else 0] if start >= 0 else ""   # 找不到終點就不比,寧可少擋
        return {"links": links, "paragraphs": [p.strip() for p in body.split("\n") if len(p.strip()) >= 40]}
    return {}


def _marks(text) -> set:
    found = {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text or ""))}
    return {n for n in found if not re.fullmatch(r"20\d\d", n)}


def fresh_news(news: list, previous: dict | None) -> list:
    """實信:創意 EPS 上修、BDI 2,648 點連兩天都寫。同一則連結,或同一段裡有兩個相同數字
    (至少一個帶小數,排除股票代號)就算昨天寫過;後續有新數字的報導不受影響。"""
    if not previous:
        return news
    links = set(previous.get("links") or [])
    paragraphs = [_marks(p) for p in previous.get("paragraphs") or []]

    def told(item: dict) -> bool:
        if str(item.get("link") or item.get("url") or "") in links:
            return True
        marks = _marks(str(item.get("title") or "") + " " + str(item.get("summary") or "")[:300])
        return any(len(c) >= 2 and any("." in n for n in c) for c in (marks & p for p in paragraphs))

    kept = [n for n in news or [] if not told(n)]
    print(f"[report-polish] 昨日已寫過的新聞略過 {len(news or []) - len(kept)} 則", flush=True)
    return kept


def fresh_model_rows(rows: list, previous: dict | None) -> list:
    """OpenRouter 近 14 日新模型每天都在清單上;昨天寫過的那一筆不再給。"""
    text = "\n".join((previous or {}).get("paragraphs") or [])

    def told(row) -> bool:
        match = re.search(r"上架\s+(\S+?):", str(row))
        return bool(match and match[1] in text)
    return [r for r in rows or [] if not told(r)]
