"""官方營收與財報數字(使用者 2026-11-10:「財報和營收數字用官方資料核對」)。

證交所與櫃買中心的開放資料:每月營收(t187ap05)、最新一季綜合損益(t187ap14)。
用途有二,都只在備援分析的證據核對裡用:

  * 佐證:句子裡的營收、年增率、EPS、稅後淨利對得上官方數字,就不算「查不到來源」
    (11-10 實信:藥華藥前三季稅後淨利 76 億元被標成查無來源,官方是 76.03 億)。
  * 更正:同一個月份的單月營收或年增率與官方不符,在該條後面附上官方數字。
    官方資料比新聞晚幾天更新 —— 月份對不上就不比。

本班的資料放在模組裡(不進 quotes:兩千家公司的數字會灌爆 prompt 與行情比對)。
純函式加一份本班的表;抓取用呼叫端給的 `get`,任何一個來源失敗都只少那一份。
"""
from __future__ import annotations

import re

_TWSE = "https://openapi.twse.com.tw/v1/opendata/"
_TPEX = "https://www.tpex.org.tw/openapi/v1/mopsfin_"
SOURCES = (("revenue", _TWSE + "t187ap05_L"), ("revenue", _TPEX + "t187ap05_O"),
           ("income", _TWSE + "t187ap14_L"), ("income", _TPEX + "t187ap14_O"))
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
_CODE = re.compile(r"(?<![\d.,])(\d{4})(?![\d.,%])")

_DATA: dict[str, dict] = {}


def reset() -> None:
    _DATA.clear()


def _num(value) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _field(row: dict, *needles: str):
    """欄位名兩個市場不完全一樣(「基本每股盈餘(元)」/「基本每股盈餘」):用關鍵字找。"""
    return next((v for k, v in row.items() if all(n in str(k) for n in needles)), None)


def _yi(thousands) -> float | None:
    value = _num(thousands)
    return None if value is None else value / 1e5   # 千元 → 億元


def _revenue(row: dict) -> dict:
    month = str(_field(row, "資料年月") or "").strip()
    return {"month": int(month[-2:]) if month[-2:].isdigit() else None,
            "rev": _yi(_field(row, "當月營收")), "yoy": _num(_field(row, "去年同月增減")),
            "mom": _num(_field(row, "上月比較增減")), "cum": _yi(_field(row, "當月累計營收")),
            "cum_yoy": _num(_field(row, "前期比較增減"))}


def _income(row: dict) -> dict:
    return {"eps": _num(_field(row, "基本每股盈餘")), "net": _yi(_field(row, "稅後淨利")),
            "op": _yi(_field(row, "營業利益")), "q_rev": _yi(_field(row, "營業收入"))}


def load(get, log=print) -> int:
    """抓四份官方資料進本班的表,回傳公司數。任何一份失敗只記一行,不拋例外。"""
    reset()
    for kind, url in SOURCES:
        try:
            response = get(url, timeout=20, headers=_HEADERS)
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list):
                raise ValueError("回應不是清單")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                code = str(_field(row, "公司代號") or row.get("SecuritiesCompanyCode") or "").strip()
                if len(code) == 4 and code.isdigit():
                    _DATA.setdefault(code, {}).update(_revenue(row) if kind == "revenue" else _income(row))
        except Exception as exc:   # noqa: BLE001 - 少一份佐證不可讓晨報斷
            log(f"[official] {url.rsplit('/', 1)[-1]} 抓取失敗(該份不用於核對): {type(exc).__name__}")
    log(f"[official] 官方營收/財報 {len(_DATA)} 家")
    return len(_DATA)


def _codes(block: str) -> list[str]:
    return [c for c in dict.fromkeys(_CODE.findall(str(block or ""))) if c in _DATA]


def facts(block: str) -> str:
    """這一條提到的公司的官方數字,寫成一段文字(交給證據核對的數字解析)。"""
    parts = []
    for code in _codes(block):
        d = _DATA[code]
        parts += [f"{d[k]:.2f} 億元" for k in ("rev", "cum", "net", "op", "q_rev") if d.get(k) is not None]
        parts += [f"{abs(d[k]):.2f}%" for k in ("yoy", "mom", "cum_yoy") if d.get(k) is not None]
        parts += [f"{d['eps']:.2f} 元"] if d.get("eps") is not None else []
    return " ".join(parts)


def correction(block: str) -> str:
    """只提到一家公司、月份與官方相同、單月營收或年增率卻對不上 → 一句官方數字;其餘回空。"""
    codes = _codes(block)
    if len(codes) != 1:
        return ""
    d = _DATA[codes[0]]
    month, rev, yoy = d.get("month"), d.get("rev"), d.get("yoy")
    if not month or rev is None:
        return ""
    # 「前 10 月營收」「累計 10 月營收」是累計,不拿來比單月(外審 PR #50)
    stated = re.search(rf"(?<![\d前至到計~～\-])(?<![前至到計]\s){month}\s*月(?:份)?(?:合併)?營收\s*(?:為|達|約)?\s*"
                       r"([\d,]+(?:\.\d+)?)\s*億", str(block))
    if not stated:
        return ""
    said = _num(stated.group(1))
    tail = str(block)[stated.end():stated.end() + 40]
    # 年增率要緊接在這筆單月營收後面(中間只容標點與月增率):隔著累計、EPS 等其他指標的不是它的(外審 PR #50)
    growth = re.match(r"元?\s*[，、；;,]?\s*(?:月增\s*[\d.]+\s*%\s*[，、；;,]?\s*)?年增\s*([\d.]+)\s*%", tail)
    wrong_rev = said is not None and abs(said - rev) > max(0.01 * rev, 0.06)
    wrong_yoy = bool(growth) and yoy is not None and abs(float(growth.group(1)) - yoy) > (
        0.06 if "." in growth.group(1) else 0.6)
    if not (wrong_rev or wrong_yoy):
        return ""
    official = f"{month} 月營收 {rev:,.2f} 億元" + (f"、年增 {yoy:.1f}%" if yoy is not None else "")
    return f"（官方公告：{official}，與上述數字不同）"
