"""
美股收盤晨報自動化
=================
每日台灣時間 07:00 抓取昨晚美股 (QQQ / TSM / SPY) 收盤價，
換算 00662 公允淨值、雙模型預測 2330 開盤合理價，
並用 LLM API 產生新聞速報與分析，最後以 Gmail SMTP 寄出。

支援 LLM 提供商（環境變數 LLM_PROVIDER 控制）：
  - "gemini"    → Google Gemini 2.5 Flash（免費 1500 req/日，預設）
  - "anthropic" → Claude Sonnet（付費，品質略勝）

執行條件 (cron 已處理)：台灣時間週二至週六 07:00。週一另判斷。
"""

from __future__ import annotations

import datetime as dt
import os
import smtplib
import ssl
import sys
import textwrap
import time
from email.message import EmailMessage
from typing import Optional
from zoneinfo import ZoneInfo

import feedparser
import pandas as pd
import requests
import yfinance as yf

# ---------- 設定 ----------
TPE = ZoneInfo("Asia/Taipei")
NY = ZoneInfo("America/New_York")

GMAIL_USER = os.environ["GMAIL_USER"]            # e.g. expertise88864@gmail.com
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT = os.environ.get("RECIPIENT", GMAIL_USER)

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# RSS 新聞來源（中、英、Fed）
RSS_FEEDS = {
    # === 國際財經 ===
    "Reuters Tech":      "https://www.reuters.com/arc/outboundfeeds/rss/category/technology/?outputType=xml",
    "Reuters Markets":   "https://www.reuters.com/arc/outboundfeeds/rss/category/markets/?outputType=xml",
    "Reuters World":     "https://www.reuters.com/arc/outboundfeeds/rss/category/world/?outputType=xml",
    "CNBC Top News":     "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "CNBC Tech":         "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910",
    "CNBC Economy":      "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    "Bloomberg Markets": "https://feeds.bloomberg.com/markets/news.rss",
    "Bloomberg Politics":"https://feeds.bloomberg.com/politics/news.rss",
    "Yahoo Finance":     "https://finance.yahoo.com/news/rssindex",

    # === 央行 / 政策 ===
    "Federal Reserve":   "https://www.federalreserve.gov/feeds/press_all.xml",
    "Fed Monetary":      "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "Treasury":          "https://home.treasury.gov/news/press-releases/feed",

    # === 台灣財經（中文）===
    "鉅亨台股":           "https://news.cnyes.com/rss/cat/tw_stock",
    "鉅亨美股":           "https://news.cnyes.com/rss/cat/wd_stock",
    "鉅亨頭條":           "https://news.cnyes.com/rss/cat/headline",
    "工商時報財經":       "https://www.chinatimes.com/rss/realtimenews-finance.xml",
    "工商科技":           "https://www.chinatimes.com/rss/realtimenews-tech.xml",
    "經濟日報財經":       "https://money.udn.com/rssfeed/news/1001/5589?ch=money",
    "經濟日報國際":       "https://money.udn.com/rssfeed/news/1001/5599/12937?ch=money",
    "聯合新聞兩岸":       "https://udn.com/rssfeed/news/2/6638?ch=news",
    "中央社財經":         "https://feeds.feedburner.com/rsscna/finance",
    "中央社政治":         "https://feeds.feedburner.com/rsscna/politics",
}

# ---------- 0050 成分股清單（含業務簡介） ----------
# 資料以元大投信 0050 ETF 公開月報為基準，每季可能小幅調整
TW0050_CONSTITUENTS: dict[str, str] = {
    "2330": "台積電 — 全球晶圓代工龍頭，先進製程 (3nm/5nm) 市佔超過 90%",
    "2317": "鴻海 — 全球最大 EMS 代工，AI 伺服器與電動車 (Foxtron) 雙引擎",
    "2454": "聯發科 — 全球第二大 IC 設計，主力天璣手機晶片與汽車/AI 邊緣晶片",
    "2382": "廣達 — 全球最大 NB 代工 + AI 伺服器代工龍頭 (NVDA H100/B200 主力)",
    "2308": "台達電 — 電源供應與工業自動化龍頭，AI 資料中心電源題材火熱",
    "2891": "中信金 — 大型金控，銀行+證券+人壽綜合營運",
    "2412": "中華電 — 電信龍頭，5G 與 IDC 業務穩定",
    "2881": "富邦金 — 金控含人壽 (富邦人壽) 與證券，受惠美股投資收益",
    "3711": "日月光投控 — 全球最大封測廠，CoWoS/SoIC 先進封裝受惠 AI 浪潮",
    "2882": "國泰金 — 金控龍頭，人壽 + 銀行 + 證券，受惠美股+股債雙利",
    "2002": "中鋼 — 國內最大鋼鐵廠，傳產循環與基建題材",
    "1303": "南亞 — 塑化、電子材料 (BT/ABF 載板) 雙主軸",
    "1301": "台塑 — 石化龍頭，傳統景氣循環",
    "2303": "聯電 — 全球第三大晶圓代工，特殊製程 (28nm/22nm) 為主",
    "3231": "緯創 — NB/伺服器代工，AI 伺服器二線受惠者",
    "2357": "華碩 — 全球前三大 NB/PC 品牌，AI PC 題材",
    "2880": "華南金 — 公股金控，銀行業務為主",
    "1216": "統一 — 食品龍頭，內需消費代表",
    "5871": "中租-KY — 國內最大租賃公司，受惠中小企業融資與綠能設備租賃",
    "5880": "合庫金 — 公股金控，銀行業務主導",
    "2884": "玉山金 — 民營金控，數位金融領先",
    "3008": "大立光 — 高階手機鏡頭龍頭，VCSEL/車用鏡頭題材",
    "2886": "兆豐金 — 公股金控，外匯業務專長",
    "3034": "聯詠 — 顯示器驅動 IC + SoC 雙核心",
    "2207": "和泰車 — Toyota 台灣總代理，受惠新車交車與電動車布局",
    "2885": "元大金 — 證券+銀行+投信，市場成交量受益者",
    "2892": "第一金 — 公股金控",
    "2912": "統一超 — 7-ELEVEN 經營者，零售龍頭",
    "5876": "上海商銀 — 中型銀行，財富管理優勢",
    "2890": "永豐金 — 民營金控，海外布局積極",
    "1101": "台泥 — 水泥龍頭，跨足儲能/綠能轉型",
    "1326": "台化 — 台塑集團石化原料",
    "2883": "開發金 — 金控含人壽 (中壽)",
    "2887": "台新金 — 民營金控，銀行+人壽 (新光金合併中)",
    "2379": "瑞昱 — 網通晶片龍頭 (乙太網路/Wi-Fi/藍牙 IC)",
    "2395": "研華 — 工業電腦 (IPC) 全球龍頭，AI Edge 應用題材",
    "1590": "亞德客-KY — 氣動元件龍頭，自動化設備題材",
    "2603": "長榮 — 全球第七大貨櫃航商，受惠歐美補貨與紅海航線",
    "2615": "萬海 — 亞洲區間貨櫃航運",
    "2609": "陽明 — 國營背景貨櫃航商",
    "1102": "亞泥 — 水泥次大廠",
    "2801": "彰銀 — 公股銀行",
    "1605": "華新 — 線纜與不鏽鋼，受惠電網與 AI 資料中心電力建設",
    "2345": "智邦 — 高階交換器/網通設備，AI 資料中心 800G 交換器受惠者",
    "2327": "國巨 — 全球第三大被動元件廠，併購 KEMET 後布局車用/工業利基",
    "1102": "亞泥 — 水泥次大廠",   # （重複代號保險用）
    "3045": "台灣大 — 電信第二大，併購台灣之星後 5G 規模擴大",
    "4938": "和碩 — Apple iPhone 主要組裝代工，多角化布局伺服器與電動車",
    "2301": "光寶科 — 電源/光電/雲端，AI 伺服器電源代工",
    "3037": "欣興 — ABF 載板龍頭，受惠 AI 晶片高階載板需求",
    "2356": "英業達 — 伺服器代工，AI 伺服器二線受惠者",
}


# ---------- 工具函式 ----------
def safe_float(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_quote(ticker: str, period: str = "1mo") -> dict:
    """
    抓最新收盤、前一日收盤、漲跌幅、成交量。
    新增：自動 dropna 並往前找有效收盤，避開 Yahoo 偶發 nan 問題（特別是 .TW 標的）。
    """
    last_err = None
    for attempt in range(3):
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period=period, auto_adjust=False)
            # 過濾無效列：Close 必須是有效數字
            hist = hist.dropna(subset=["Close"])
            hist = hist[hist["Close"] > 0]
            if not hist.empty:
                break
        except Exception as e:
            last_err = e
            print(f"[quote] {ticker} attempt {attempt+1} 失敗: {e}", file=sys.stderr)
        time.sleep(2)
    else:
        return {"ticker": ticker, "error": f"no valid data: {last_err}"}

    if hist.empty:
        return {"ticker": ticker, "error": "all rows were nan"}

    last = hist.iloc[-1]
    prev = hist.iloc[-2] if len(hist) >= 2 else None
    close = safe_float(last["Close"])
    prev_close = safe_float(prev["Close"]) if prev is not None else None
    pct = ((close - prev_close) / prev_close * 100) if (close and prev_close) else None
    return {
        "ticker": ticker,
        "date": last.name.strftime("%Y-%m-%d"),
        "close": round(close, 4) if close else None,
        "prev_close": round(prev_close, 4) if prev_close else None,
        "change_pct": round(pct, 2) if pct is not None else None,
        "high": round(safe_float(last["High"]) or 0, 4),
        "low": round(safe_float(last["Low"]) or 0, 4),
        "volume": int(last["Volume"]) if not pd.isna(last["Volume"]) else None,
        "history": hist,
    }


def fetch_usdtwd() -> Optional[float]:
    """USD/TWD 即期匯率 (Yahoo Finance: TWD=X)。已過濾 nan。"""
    try:
        d = yf.Ticker("TWD=X").history(period="10d")
        d = d.dropna(subset=["Close"])
        d = d[d["Close"] > 0]
        if d.empty:
            return None
        return round(safe_float(d.iloc[-1]["Close"]), 4)
    except Exception:
        return None


def _to_int(v) -> int:
    """容忍逗號、空字串、None、float 字串"""
    if v is None:
        return 0
    s = str(v).replace(",", "").strip()
    if not s or s in ("-", "NA"):
        return 0
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _twse_main_api(date_str: str) -> list[dict]:
    """
    主要端點：TWSE 主站 fund/T86 (response=json)。
    這個端點欄位名固定為中文格式：證券代號、外陸資買賣超股數、投信買賣超股數、自營商買賣超股數
    """
    url = (f"https://www.twse.com.tw/fund/T86?response=json"
           f"&date={date_str}&selectType=ALLBUT0999")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.twse.com.tw/zh/trading/foreign/t86.html",
    }
    r = requests.get(url, timeout=15, headers=headers)
    r.raise_for_status()
    payload = r.json()
    if payload.get("stat") != "OK":
        return []
    fields = payload.get("fields", [])
    data = payload.get("data", [])
    return [dict(zip(fields, row)) for row in data]


def _twse_openapi(_unused: str) -> list[dict]:
    """備援端點：OpenAPI（無日期參數，回傳最新一日）。"""
    r = requests.get("https://openapi.twse.com.tw/v1/fund/T86",
                      timeout=15,
                      headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json() or []


def fetch_twse_institutional() -> dict[str, dict]:
    """
    從 TWSE 抓昨日三大法人買賣超。
    多端點 + 多日期備援，先試主站，再試 OpenAPI；日期從昨天往前找最近交易日。
    回傳：{ "2330": {"foreign": +N, "investment": +N, "dealer": +N, "total": +N}, ... }
    單位：股數（負為賣超）。
    """
    # 嘗試最近 5 天，跳過週末
    candidates: list[str] = []
    today = dt.datetime.now(TPE).date()
    for back in range(1, 8):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:  # 週六/日跳過
            continue
        candidates.append(d.strftime("%Y%m%d"))
        if len(candidates) >= 4:
            break

    rows: list[dict] = []
    used_endpoint = ""
    used_date = ""
    # 先試主站 (依日期往前)，主站不行再試 OpenAPI
    for date_str in candidates:
        try:
            rows = _twse_main_api(date_str)
            if rows:
                used_endpoint = "main"
                used_date = date_str
                break
        except Exception as e:
            print(f"[twse] 主站 {date_str} 失敗: {e}", file=sys.stderr)

    if not rows:
        try:
            rows = _twse_openapi("")
            if rows:
                used_endpoint = "openapi"
                used_date = "latest"
        except Exception as e:
            print(f"[twse] OpenAPI 也失敗: {e}", file=sys.stderr)

    if not rows:
        print("[twse] 所有端點皆無資料", file=sys.stderr)
        return {}

    print(f"[twse] 使用端點={used_endpoint} 日期={used_date} 取得 {len(rows)} 筆原始資料")

    # === 自動偵測欄位名（中英文都支援） ===
    sample_keys = list(rows[0].keys())
    print(f"[twse] 樣本欄位：{sample_keys}")

    def find_key(*needles: str) -> Optional[str]:
        """找出欄位名包含所有 needles（大小寫無關）的第一個 key。"""
        for k in sample_keys:
            kl = k.lower()
            if all(n.lower() in kl for n in needles):
                return k
        return None

    def find_any(*candidates: str) -> Optional[str]:
        """直接找完全匹配（中文用）。"""
        for cand in candidates:
            for k in sample_keys:
                if cand in k:
                    return k
        return None

    def find_startswith(prefix: str) -> Optional[str]:
        """嚴格用 startswith 匹配，避免「外資自營商」誤抓。"""
        for k in sample_keys:
            if k.strip().startswith(prefix):
                return k
        return None

    def find_exact(*candidates: str) -> Optional[str]:
        """精準匹配（去空白後相等）。"""
        keys_clean = {k.strip(): k for k in sample_keys}
        for cand in candidates:
            if cand in keys_clean:
                return keys_clean[cand]
        return None

    # === 中文欄位（主站 API）===
    # 主站欄位名實際格式：
    #   證券代號、證券名稱、
    #   外陸資買賣超股數(不含外資自營商)、外資自營商買賣超股數、
    #   投信買賣超股數、
    #   自營商買賣超股數(自行買賣)、自營商買賣超股數(避險)、自營商買賣超股數、
    #   三大法人買賣超股數
    # 重點：「自營商」要嚴格用 startswith，否則會抓到「外資自營商」
    f_over_cn = find_any("外陸資買賣超股數", "外資及陸資買賣超股數", "外資買賣超股數")
    t_over_cn = find_startswith("投信買賣超股數") or find_any("投信買賣超股數")
    # 優先抓「自營商買賣超股數」(合計)；找不到才用「自營商買賣超股數(自行買賣)」
    d_over_cn = find_exact("自營商買賣超股數") or find_startswith("自營商買賣超股數")
    code_cn   = find_any("證券代號")

    # === 英文欄位（OpenAPI）===
    f_over_en = find_key("foreign", "over") or find_key("foreign", "buysell")
    t_over_en = find_key("invest", "trust", "over") or find_key("invest", "trust", "buysell") \
                or find_key("trust", "over")
    d_over_en = find_key("dealer", "over") or find_key("dealer", "buysell")
    code_en   = find_key("code") or find_key("symbol") or find_key("stock")

    f_over = f_over_cn or f_over_en
    t_over = t_over_cn or t_over_en
    d_over = d_over_cn or d_over_en
    code_key = code_cn or code_en

    # 若還沒找到，試 Buy / Sell 兩欄相減
    f_buy = f_sell = t_buy = t_sell = d_buy = d_sell = None
    if not f_over:
        f_buy  = find_key("foreign", "buy")
        f_sell = find_key("foreign", "sell")
    if not t_over:
        t_buy  = find_key("invest", "trust", "buy")
        t_sell = find_key("invest", "trust", "sell")
    if not d_over:
        d_buy  = find_key("dealer", "buy")
        d_sell = find_key("dealer", "sell")

    print(f"[twse] 偵測欄位 外資={f_over or (f_buy, f_sell)} "
          f"投信={t_over or (t_buy, t_sell)} 自營={d_over or (d_buy, d_sell)} "
          f"代號={code_key}")

    if not code_key:
        print(f"[twse] 找不到代號欄位，sample_keys={sample_keys}", file=sys.stderr)
        return {}

    result: dict[str, dict] = {}
    for row in rows:
        code = (row.get(code_key) or "").strip()
        if not code:
            continue

        if f_over:
            foreign = _to_int(row.get(f_over))
        elif f_buy and f_sell:
            foreign = _to_int(row.get(f_buy)) - _to_int(row.get(f_sell))
        else:
            foreign = 0

        if t_over:
            invest = _to_int(row.get(t_over))
        elif t_buy and t_sell:
            invest = _to_int(row.get(t_buy)) - _to_int(row.get(t_sell))
        else:
            invest = 0

        if d_over:
            dealer = _to_int(row.get(d_over))
        elif d_buy and d_sell:
            dealer = _to_int(row.get(d_buy)) - _to_int(row.get(d_sell))
        else:
            dealer = 0

        total = foreign + invest + dealer
        result[code] = {
            "foreign": foreign,
            "investment": invest,
            "dealer": dealer,
            "total": total,
        }

    # 健康檢查：抓到的資料是否多數為 0
    nonzero = sum(1 for v in result.values() if v["total"] != 0)
    print(f"[twse] 抓到 {len(result)} 檔，其中 {nonzero} 檔有非零法人買賣超")
    if len(result) > 0 and nonzero == 0:
        print(f"[twse] ⚠️ 全部 0 — 欄位偵測可能失敗。Sample row: {rows[0]}", file=sys.stderr)

    return result


def fetch_tw0050_snapshot() -> list[dict]:
    """
    批次抓 0050 成分股近期表現。
    每檔回傳：代號、名稱、昨收、漲跌幅、5日均量比、月漲跌幅、法人合計買賣超。
    """
    inst = fetch_twse_institutional()
    snapshot: list[dict] = []
    codes = list(TW0050_CONSTITUENTS.keys())

    # yfinance 批次下載 (每檔加 .TW)
    tickers = " ".join(f"{c}.TW" for c in codes)
    try:
        df_all = yf.download(tickers, period="1mo", group_by="ticker",
                              auto_adjust=False, progress=False, threads=True)
    except Exception as e:
        print(f"[snapshot] 批次下載失敗: {e}", file=sys.stderr)
        return []

    for code in codes:
        try:
            sub = df_all[f"{code}.TW"].dropna(subset=["Close"])
            sub = sub[sub["Close"] > 0]
            if len(sub) < 5:
                continue
            last = sub.iloc[-1]
            prev = sub.iloc[-2]
            close = safe_float(last["Close"])
            prev_close = safe_float(prev["Close"])
            day_pct = (close - prev_close) / prev_close * 100 if prev_close else 0

            vol = safe_float(last["Volume"])
            avg5_vol = sub["Volume"].tail(5).mean()
            vol_ratio = (vol / avg5_vol) if avg5_vol else None

            month_first = safe_float(sub.iloc[0]["Close"])
            month_pct = (close - month_first) / month_first * 100 if month_first else 0

            inst_data = inst.get(code, {})

            snapshot.append({
                "code": code,
                "name": TW0050_CONSTITUENTS[code].split(" — ")[0],
                "desc": TW0050_CONSTITUENTS[code],
                "close": round(close, 2),
                "day_pct": round(day_pct, 2),
                "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
                "month_pct": round(month_pct, 2),
                "foreign_lot": round(inst_data.get("foreign", 0) / 1000, 1),    # 轉張
                "invest_lot": round(inst_data.get("investment", 0) / 1000, 1),
                "dealer_lot": round(inst_data.get("dealer", 0) / 1000, 1),
                "total_lot": round(inst_data.get("total", 0) / 1000, 1),
            })
        except (KeyError, ValueError, TypeError) as e:
            print(f"[snapshot] {code} 跳過: {e}", file=sys.stderr)
            continue

    print(f"[snapshot] 0050 完成 {len(snapshot)} 檔")
    return snapshot


def fetch_2330_recent() -> Optional[pd.DataFrame]:
    """抓 2330.TW 近 60 日收盤，供回歸用。已過濾 nan。"""
    for attempt in range(3):
        try:
            d = yf.Ticker("2330.TW").history(period="6mo", auto_adjust=False)
            d = d.dropna(subset=["Close"])
            d = d[d["Close"] > 0]
            if not d.empty:
                return d
        except Exception as e:
            print(f"[quote] 2330.TW attempt {attempt+1} 失敗: {e}", file=sys.stderr)
        time.sleep(2)
    return None


def calc_00662_fair_value(qqq_close: float, qqq_prev_close: float,
                           usdtwd: float, last_00662_price: Optional[float]) -> dict:
    """
    估 00662 公允淨值與合理價。
    由於 00662 追蹤 NASDAQ-100，QQQ 漲跌幅 + 匯率變化 → 00662 NAV 漲跌幅。
    無法直接得知今日 NAV (T+1 公布)，因此用「昨日收盤 × (1 + QQQ%) × 匯率調整」估值。
    """
    qqq_pct = (qqq_close - qqq_prev_close) / qqq_prev_close
    if last_00662_price is None:
        return {"error": "缺 00662 昨收"}
    fair_price = last_00662_price * (1 + qqq_pct)
    return {
        "qqq_pct": round(qqq_pct * 100, 2),
        "last_00662_price": last_00662_price,
        "fair_price": round(fair_price, 2),
        "implied_change_pct": round(qqq_pct * 100, 2),
        "usdtwd": usdtwd,
    }


def calc_2330_predictions(tsm_close: float, tsm_prev_close: float,
                            usdtwd: float, hist_2330: pd.DataFrame) -> dict:
    """
    雙模型 2330 預測：
    1. 漲跌幅 1:1 對應法 — 用昨日 2330 收盤 × (1 + TSM%)
    2. 60日比值回歸法 — 平均 (2330 / (TSM × FX × 0.2)) → 套用今日 TSM × FX × 0.2
       註：1 ADR = 5 普通股，故 ADR 美元價 × 匯率 ÷ 5 = 對應台股理論價
    """
    if hist_2330 is None or hist_2330.empty:
        return {"error": "缺 2330 歷史價"}

    last_2330 = safe_float(hist_2330.iloc[-1]["Close"])
    tsm_pct = (tsm_close - tsm_prev_close) / tsm_prev_close

    # 模型 1：漲跌幅 1:1
    model1 = last_2330 * (1 + tsm_pct)

    # 模型 2：比值回歸（近 60 日）
    # 需要 TSM 與 USD/TWD 同期歷史，皆需過濾 nan
    model2 = None
    try:
        tsm_hist = yf.Ticker("TSM").history(period="6mo", auto_adjust=False)
        fx_hist = yf.Ticker("TWD=X").history(period="6mo", auto_adjust=False)
        # 各自過濾 nan
        tsm_close_s = tsm_hist["Close"].dropna()
        fx_close_s = fx_hist["Close"].dropna()
        t2330_s = hist_2330["Close"].dropna()
        # 將時區拿掉以利對齊
        tsm_close_s.index = tsm_close_s.index.tz_localize(None) if tsm_close_s.index.tz else tsm_close_s.index
        fx_close_s.index  = fx_close_s.index.tz_localize(None)  if fx_close_s.index.tz  else fx_close_s.index
        t2330_s.index     = t2330_s.index.tz_localize(None)     if t2330_s.index.tz     else t2330_s.index
        df = pd.DataFrame({
            "tsm":   tsm_close_s,
            "fx":    fx_close_s,
            "t2330": t2330_s,
        }).dropna()
        if len(df) >= 20:
            df["theo_tw"] = df["tsm"] * df["fx"] / 5.0   # 1 ADR = 5 股
            df["ratio"] = df["t2330"] / df["theo_tw"]
            avg_ratio = df["ratio"].tail(60).mean()
            today_theo = tsm_close * usdtwd / 5.0
            model2 = today_theo * avg_ratio
            print(f"[calc] 2330 model2 ratio={avg_ratio:.3f} samples={len(df)}")
        else:
            print(f"[calc] 2330 model2 樣本不足 ({len(df)} 筆)")
    except Exception as e:
        print(f"[calc] 2330 model2 失敗: {e}", file=sys.stderr)

    res = {
        "last_2330": round(last_2330, 2),
        "tsm_pct": round(tsm_pct * 100, 2),
        "model1_1to1": round(model1, 2),
        "model2_regression": round(model2, 2) if model2 else None,
    }
    if model1 and model2:
        lo, hi = sorted([model1, model2])
        res["range"] = (round(lo, 2), round(hi, 2))
        res["mid"] = round((lo + hi) / 2, 2)
    return res


def fetch_news() -> list[dict]:
    """抓 RSS 摘要，回傳最近 24 小時內的新聞。"""
    items: list[dict] = []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=30)
    for source, url in RSS_FEEDS.items():
        try:
            if url.endswith("&page=1"):  # 鉅亨美股 JSON 特例
                r = requests.get(url, timeout=10,
                                 headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200:
                    payload = r.json() or {}
                    items_obj = payload.get("items") or {}
                    data = items_obj.get("data") if isinstance(items_obj, dict) else None
                    if not isinstance(data, list):
                        data = []
                    for d in data[:10]:
                        if not isinstance(d, dict):
                            continue
                        items.append({
                            "source": source,
                            "title": d.get("title", ""),
                            "summary": (d.get("summary") or "")[:300],
                            "link": f"https://news.cnyes.com/news/id/{d.get('newsId')}",
                            "published": d.get("publishAt", ""),
                        })
                continue

            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub:
                    pub_dt = dt.datetime(*pub[:6], tzinfo=dt.timezone.utc)
                    if pub_dt < cutoff:
                        continue
                items.append({
                    "source": source,
                    "title": entry.get("title", ""),
                    "summary": (entry.get("summary", "") or "")[:300],
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                })
        except Exception as e:
            print(f"[news] {source} 抓取失敗：{e}", file=sys.stderr)
    return items


def _build_prompt(quotes: dict, fair: dict, predictions: dict,
                   news: list[dict], tw0050: list[dict]) -> str:
    news_block = "\n".join(
        f"- [{n['source']}] {n['title']}（{n.get('summary','')[:200]}）"
        for n in news[:60]
    )

    # 整理 0050 法人/表現摘要表（讓 LLM 一眼掃完）
    if tw0050:
        # 排序：法人合計買超由大到小（負則是賣超）
        tw0050_sorted = sorted(tw0050, key=lambda x: x.get("total_lot", 0), reverse=True)
        rows = []
        for s in tw0050_sorted:
            rows.append(
                f"{s['code']} {s['name']:<6} 收{s['close']:>8} "
                f"日{s['day_pct']:+5.2f}% 月{s['month_pct']:+6.2f}% "
                f"量比{(str(s['vol_ratio']) if s['vol_ratio'] else '-'):>5} "
                f"外資{s['foreign_lot']:+8.0f}張 "
                f"投信{s['invest_lot']:+6.0f}張 "
                f"自營{s['dealer_lot']:+6.0f}張 "
                f"總{s['total_lot']:+8.0f}張 | {s['desc']}"
            )
        tw0050_block = "\n".join(rows)
    else:
        tw0050_block = "（資料抓取失敗）"

    return f"""你是嚴謹但敢於下判斷的科技股財經分析師。為一位重押 00662（NASDAQ-100）與 2330（台積電）的台灣投資人寫晨報。

【昨日美股收盤】
- QQQ：{quotes['QQQ']}
- TSM (台積電 ADR)：{quotes['TSM']}
- SPY：{quotes['SPY']}
- USD/TWD：{quotes.get('USDTWD')}

【今日 00662 估值（Python 已算）】
{fair}

【今日 2330 雙模型預測（Python 已算）】
{predictions}

【近 24-30 小時新聞清單（含國際財經、Fed、台灣財經、政府政策）】
{news_block}

【0050 成分股昨日表現與三大法人買賣超（單位：張，正為買超、負為賣超）】
{tw0050_block}

# 寫作要求（必讀）

1. **零客套，不寫「親愛的投資人」「以下是」這類開場白**，直接進主題
2. **語氣精煉、敢下判斷**，不要三方並陳逃避立場
3. 每提到「公司名」必附 (一句話講這間公司在做什麼 + 近期關鍵動向)
4. 全部繁體中文
5. 估值欄位若是 None / nan，直接寫「資料缺失」，不要瞎掰數字
6. **避免重複條列**，每條只寫一件事
7. 嚴禁 emoji 與表情符號

# 輸出結構（必須完全照此順序與標題）

## 一、昨夜三大重點
僅 3 條 bullet。直接點出最影響 00662 / 2330 的關鍵事件。

## 二、科技板塊脈動（5–8 條）
每條格式：**公司名（一句話業務簡介）**：發生什麼 + 為何重要。
範例：**AMD（全球第二大 x86 CPU 與 AI GPU 廠，MI300X 為主力資料中心晶片）**：Q3 資料中心營收年增 122%，MI300X 出貨優於預期，AI 算力競賽中與 NVDA 差距縮小。

## 三、總體經濟與政策環境
分三小段：
**(A) 美國利率/美元/VIX/通膨**：列出昨日 10Y 殖利率、DXY、VIX、CPI/PPI/就業數據（如有）。
**(B) Fed/美國政府重大政策**：FOMC 紀要、Fed 官員談話、白宮對中政策、半導體出口管制等。明確寫出對台灣科技業的影響。
**(C) 全球其他國家政策（若有）**：日本央行、ECB、中國刺激政策、地緣政治等。

## 四、台灣本地動態（必寫，不可略）
聚焦昨日對台灣資本市場有影響的事：
- 台灣央行/金管會動向
- 台積電供應鏈動態（艾司摩爾、東京威力、SUMCO、信驊、力旺等）
- 台灣總經數據（出口、外銷訂單、CPI）
- 政府政策（產創條例、科專、台美 21 世紀貿易倡議等）
若新聞清單中沒有相關內容，寫「無重大本地新聞」，不要編造。

## 五、我的明確立場（**最重要**）
**先給單一立場標籤**，再解釋為什麼。**不要列出樂觀/中性/悲觀三選一**——直接告訴投資人你選哪一個。

格式：
> **立場：[偏多 / 偏空 / 中性]**（任選一個，不可模糊）
>
> 理由（3-5 句）：……
>
> **2330 開盤關鍵價位**：守穩 XXX 元為強，跌破 XXX 元轉弱
> **00662 操作建議**：（明確寫加碼 / 觀望 / 減碼，給一個價位門檻）
> **主要風險**：1 句話

## 六、今日台股關注三檔（**必寫，0050 成分股限定**）
從上方「0050 成分股」表格中，**結合基本面（公司營運/題材）+ 消息面（昨日新聞）+ 法人面（外資/投信買超強度）** 三角度，選出**今日預期漲幅最高的三檔**。**不限制漲幅大小**、**不要用技術面（K 線、均線、MACD）**。

每檔必須包含：
- **代號 + 公司名**（H4 標題）
- **業務簡介**（1-2 句，這間公司在做什麼）
- **近期營收/獲利動向**（最近一季營收表現、年增率，若新聞清單沒提到可用先驗知識）
- **昨日法人動向**：外資/投信/自營買超張數，重點解讀（外資是否連續買超？投信籌碼集中？）
- **挑選理由**：為什麼是今天會漲（消息催化 + 籌碼結構 + 基本面定位）
- **信心等級**：高 / 中 / 低（不可省略，且必須誠實）
- **目標關注幅度**：給一個合理區間（例如「漲幅 2-4%」）

**禁止事項**：
- 不可用技術面分析（不能提 K 線、均線、MACD、KD、RSI、布林通道）
- 不可選 0050 以外的股票
- 若三檔都信心低，照寫，不要勉強說都很有把握

## 七、一句話總結
20 字內。給一句具體可執行的結論。

# 重要警示
你不是真神。**第六段最後務必加一行小字風險警示**：「以上分析基於昨日法人籌碼與新聞消息推論，實際走勢受開盤瞬間外資掛單、突發新聞、台美匯率波動影響，僅供參考不構成投資建議」。
"""


def _call_gemini_once(model: str, prompt: str) -> str:
    """單次呼叫 Gemini REST。失敗時直接 raise，由外層處理重試/降級。"""
    if not GEMINI_API_KEY:
        raise RuntimeError("缺 GEMINI_API_KEY 環境變數")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={GEMINI_API_KEY}")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 8192,
        },
    }
    r = requests.post(url, json=payload, timeout=90)
    r.raise_for_status()
    data = r.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini 回應無 candidates: {data}")
    parts = candidates[0].get("content", {}).get("parts") or []
    if not parts:
        raise RuntimeError(f"Gemini 回應無 parts: {data}")
    return parts[0].get("text", "")


# 模型降級鏈：主模型不穩時依序往下試
GEMINI_FALLBACK_MODELS = [
    GEMINI_MODEL,                    # 通常是 gemini-2.5-flash
    "gemini-2.5-flash-lite",         # 更輕量，較少 503
    "gemini-2.0-flash",              # 上一代穩定版
]
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


def _call_gemini(prompt: str) -> str:
    """
    Gemini 完整呼叫流程：
    對每個候選模型重試 3 次（指數退避 5s/15s/45s），
    任何模型成功就回傳；全部失敗才 raise。
    """
    last_err: Optional[Exception] = None
    for model in GEMINI_FALLBACK_MODELS:
        for attempt in range(1, 4):
            try:
                print(f"[llm] 嘗試 Gemini model={model} attempt={attempt}")
                return _call_gemini_once(model, prompt)
            except requests.exceptions.HTTPError as e:
                code = e.response.status_code if e.response is not None else None
                last_err = e
                if code in RETRY_STATUS_CODES and attempt < 3:
                    wait = 5 * (3 ** (attempt - 1))   # 5, 15, 45
                    print(f"[llm] HTTP {code} 暫時故障，{wait}s 後重試", file=sys.stderr)
                    time.sleep(wait)
                    continue
                print(f"[llm] {model} 最終失敗: {e}", file=sys.stderr)
                break  # 進入下一個 fallback 模型
            except Exception as e:
                last_err = e
                print(f"[llm] {model} 異常: {e}", file=sys.stderr)
                if attempt < 3:
                    time.sleep(5)
                    continue
                break
    raise RuntimeError(f"Gemini 所有降級模型皆失敗: {last_err}")


def _call_anthropic(prompt: str) -> str:
    """Claude Sonnet 付費 API。"""
    import anthropic  # 延後 import，未用就不需安裝
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("缺 ANTHROPIC_API_KEY 環境變數")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def _fallback_analysis_text(news: list[dict], err: Exception) -> str:
    """LLM 完全失敗時的備援文字。仍提供原始新聞清單與錯誤說明。"""
    top_news = "\n".join(
        f"- [{n['source']}] {n['title']}"
        for n in news[:20]
    )
    return f"""## ⚠️ LLM 服務暫時不可用

今日早晨 LLM API 多次重試均失敗，已自動降級寄出基本版報告。錯誤訊息：
`{type(err).__name__}: {str(err)[:200]}`

## 一、原始新聞清單（供你自行判讀）

{top_news}

## 二、提示

請直接看上方「美股收盤行情」「00662 公允價」「2330 雙模型預測」三個區塊做判斷。
若情況持續，可考慮：
- 切換 LLM_PROVIDER 為 anthropic（Claude 付費版較穩）
- 等待數小時後 Gemini 服務恢復
"""


def call_llm_analysis(quotes: dict, fair: dict, predictions: dict,
                       news: list[dict], tw0050: list[dict] | None = None) -> str:
    """根據 LLM_PROVIDER 環境變數選擇 LLM。預設 gemini。失敗回傳備援文字而非 raise。"""
    prompt = _build_prompt(quotes, fair, predictions, news, tw0050 or [])
    try:
        if LLM_PROVIDER == "anthropic":
            return _call_anthropic(prompt)
        return _call_gemini(prompt)
    except Exception as e:
        print(f"[llm] 全部失敗，使用備援文字: {e}", file=sys.stderr)
        return _fallback_analysis_text(news, e)


# 向後相容別名（test_with_mock.py 等舊程式仍可運作）
call_claude_analysis = call_llm_analysis


# ---------- HTML 組版（Email 友善版） ----------
def _md_to_html(text: str) -> str:
    """
    自製 minimal Markdown → HTML 轉譯器，只用 stdlib `re`，不依賴第三方套件。
    支援：H1-H4 標題、**粗體**、*斜體*、- 與 * 列表、> 引用、空行分段。
    """
    import re
    import html as html_lib

    # 1. HTML escape（避免 LLM 輸出的 < > & 變成標籤）
    text = html_lib.escape(text)

    # 2. 一次處理一行
    lines = text.split("\n")
    out: list[str] = []
    in_ul = False
    in_blockquote = False
    para_buffer: list[str] = []

    def flush_para():
        nonlocal para_buffer
        if para_buffer:
            joined = " ".join(para_buffer).strip()
            if joined:
                out.append(f"<p>{joined}</p>")
            para_buffer = []

    def close_lists():
        nonlocal in_ul, in_blockquote
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_blockquote:
            out.append("</blockquote>")
            in_blockquote = False

    for raw in lines:
        line = raw.rstrip()
        # 空行 → 段落結束
        if not line.strip():
            flush_para()
            close_lists()
            continue

        # 標題 #### / ### / ## / #
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            flush_para(); close_lists()
            level = len(m.group(1))
            content = m.group(2).strip()
            out.append(f"<h{level}>{content}</h{level}>")
            continue

        # 引用 >
        if line.lstrip().startswith("&gt;") or line.lstrip().startswith(">"):
            flush_para()
            if not in_blockquote:
                out.append("<blockquote>")
                in_blockquote = True
            content = re.sub(r"^\s*(?:&gt;|>)\s?", "", line)
            out.append(f"{content}<br>")
            continue
        elif in_blockquote:
            out.append("</blockquote>")
            in_blockquote = False

        # 列表 - 或 *
        m = re.match(r"^\s*[-*]\s+(.+)$", line)
        if m:
            flush_para()
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{m.group(1)}</li>")
            continue
        elif in_ul:
            out.append("</ul>")
            in_ul = False

        # 一般段落內容（累積）
        para_buffer.append(line)

    flush_para()
    close_lists()
    html = "\n".join(out)

    # 3. 行內樣式：**粗體** 與 *斜體*（粗體優先）
    html = re.sub(r"\*\*([^*\n]+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", html)

    return html


def _style_analysis_html(html: str) -> str:
    """為 markdown 轉出的 HTML 加 inline style（email client 不支援 <style>）。"""
    replacements = [
        # 標題層次
        ("<h2>", "<h2 style=\"color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;\">"),
        ("<h3>", "<h3 style=\"color:#0f172a;font-size:17px;margin:24px 0 10px;padding-bottom:6px;border-bottom:2px solid #cbd5e1;\">"),
        ("<h4>", "<h4 style=\"color:#0c4a6e;font-size:15px;margin:18px 0 6px;\">"),
        ("<h1>", "<h1 style=\"color:#0f172a;font-size:24px;margin:24px 0 12px;\">"),
        # 段落
        ("<p>", "<p style=\"margin:10px 0;line-height:1.85;color:#1f2937;\">"),
        # 列表
        ("<ul>", "<ul style=\"margin:10px 0 14px;padding-left:24px;line-height:1.85;color:#1f2937;\">"),
        ("<ol>", "<ol style=\"margin:10px 0 14px;padding-left:24px;line-height:1.85;color:#1f2937;\">"),
        ("<li>", "<li style=\"margin:6px 0;\">"),
        # 強調
        ("<strong>", "<strong style=\"color:#0c4a6e;font-weight:700;\">"),
        ("<em>", "<em style=\"color:#475569;\">"),
        # 引用塊（用於「我的明確立場」）
        ("<blockquote>",
         "<blockquote style=\"border-left:5px solid #0284c7;background:#f0f9ff;margin:14px 0;padding:14px 18px;border-radius:4px;color:#0c4a6e;\">"),
        # 水平線
        ("<hr>", "<hr style=\"border:none;border-top:1px solid #e2e8f0;margin:24px 0;\">"),
        ("<hr />", "<hr style=\"border:none;border-top:1px solid #e2e8f0;margin:24px 0;\">"),
    ]
    for old, new in replacements:
        html = html.replace(old, new)
    return html


def _wrap_tw_picks(html: str) -> str:
    """把『今日台股關注三檔』段落包成琥珀色卡片，每檔個股做成獨立子卡片。"""
    if "今日台股關注三檔" not in html:
        return html

    # 找第六段開始（h2 含「今日台股關注三檔」）
    idx_six = html.find("今日台股關注三檔")
    # 從這位置往前找最近的 <h2
    h2_start = html.rfind("<h2", 0, idx_six)
    # 找第七段開始
    idx_seven = html.find("一句話總結")
    if idx_seven == -1:
        idx_seven = len(html)
    h2_end = html.rfind("<h2", 0, idx_seven)
    if h2_end <= h2_start:
        h2_end = len(html)

    pre  = html[:h2_start]
    mid  = html[h2_start:h2_end]
    post = html[h2_end:]

    # mid 內的每一個 <h3>...</h3> 是一檔股票，把每檔包成卡片
    import re
    # 把 h3 開始到下一個 h3（或 mid 結尾）的內容，包成卡片
    def card_repl(m: "re.Match[str]") -> str:
        block = m.group(0)
        return ("<div style=\"background:#ffffff;border:1px solid #f59e0b;border-radius:8px;"
                "padding:14px 18px;margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,0.04);\">"
                + block + "</div>")

    pattern = re.compile(r"<h3[^>]*>.*?(?=<h3|$)", re.DOTALL)
    mid_cards = pattern.sub(card_repl, mid)

    box = ("<div style=\"background:#fffbeb;border:2px solid #f59e0b;border-radius:12px;"
           "padding:18px 20px;margin:24px 0;\">"
           + mid_cards + "</div>")
    return pre + box + post


def _wrap_stance(html: str) -> str:
    """把『我的明確立場』段做更醒目的藍色 callout box。"""
    marker = "我的明確立場"
    if marker not in html:
        return html
    idx = html.find(marker)
    h2_start = html.rfind("<h2", 0, idx)
    # 找下一個 h2 即立場段結束
    h2_end = html.find("<h2", idx)
    if h2_end == -1:
        return html
    pre  = html[:h2_start]
    mid  = html[h2_start:h2_end]
    post = html[h2_end:]

    box = ("<div style=\"background:linear-gradient(135deg,#dbeafe,#e0f2fe);"
           "border:2px solid #0284c7;border-radius:12px;"
           "padding:18px 22px;margin:24px 0;\">"
           + mid + "</div>")
    return pre + box + post


def render_html(quotes: dict, fair: dict, predictions: dict, analysis: str,
                report_date: str, mode: str) -> str:
    # ===== 1. 行情表格 =====
    def fmt_quote(q: dict) -> str:
        if "error" in q:
            return (f"<tr><td style='padding:10px 14px;border-bottom:1px solid #e2e8f0;'>{q['ticker']}</td>"
                    f"<td colspan='4' style='padding:10px 14px;border-bottom:1px solid #e2e8f0;color:#dc2626'>{q['error']}</td></tr>")
        pct = q.get("change_pct") or 0
        color = "#16a34a" if pct >= 0 else "#dc2626"
        sign = "+" if pct >= 0 else ""
        vol = q.get("volume")
        vol_str = f"{vol:,}" if vol else "—"
        return (
            f"<tr>"
            f"<td style='padding:12px 14px;border-bottom:1px solid #e2e8f0;font-weight:700;color:#0f172a;'>{q['ticker']}</td>"
            f"<td style='padding:12px 14px;border-bottom:1px solid #e2e8f0;text-align:right;font-variant-numeric:tabular-nums;'>{q['close']}</td>"
            f"<td style='padding:12px 14px;border-bottom:1px solid #e2e8f0;text-align:right;color:{color};font-weight:700;'>{sign}{pct}%</td>"
            f"<td style='padding:12px 14px;border-bottom:1px solid #e2e8f0;text-align:right;color:#475569;font-size:13px;'>{q['high']} / {q['low']}</td>"
            f"<td style='padding:12px 14px;border-bottom:1px solid #e2e8f0;text-align:right;color:#64748b;font-size:13px;'>{vol_str}</td>"
            f"</tr>"
        )

    quote_rows = "".join(fmt_quote(q) for k, q in quotes.items() if k != "USDTWD")

    # ===== 2. KPI 卡片 (00662) =====
    if "error" not in fair:
        sign = "+" if fair["implied_change_pct"] >= 0 else ""
        change_color = "#16a34a" if fair["implied_change_pct"] >= 0 else "#dc2626"
        fair_html = f"""
        <table style="width:100%;border-collapse:collapse;margin:12px 0;">
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;border-radius:6px 0 0 6px;color:#475569;width:55%;">QQQ 漲跌幅</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-weight:700;color:{change_color};font-variant-numeric:tabular-nums;">{sign}{fair['qqq_pct']}%</td>
          </tr>
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">00662 昨收參考</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-variant-numeric:tabular-nums;">{fair['last_00662_price']}</td>
          </tr>
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:14px;background:linear-gradient(135deg,#0284c7,#0ea5e9);color:#fff;font-weight:700;border-radius:6px 0 0 6px;">★ 00662 今日合理價估值</td>
            <td style="padding:14px;background:linear-gradient(135deg,#0284c7,#0ea5e9);color:#fff;text-align:right;font-size:22px;font-weight:700;border-radius:0 6px 6px 0;font-variant-numeric:tabular-nums;">{fair['fair_price']}</td>
          </tr>
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">USD/TWD</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-variant-numeric:tabular-nums;">{fair['usdtwd']}</td>
          </tr>
        </table>
        <p style="font-size:12px;color:#94a3b8;margin:8px 0;">註：估值未計入溢/折價、申購贖回價差、隔日匯率變動。</p>
        """
    else:
        fair_html = f"<p style='color:#dc2626'>{fair.get('error','資料缺失')}</p>"

    # ===== 3. 2330 預測卡片 =====
    if "error" not in predictions:
        m2 = predictions.get("model2_regression")
        m2_str = m2 if m2 is not None else "資料缺失"
        rng = predictions.get("range")
        tsm_pct = predictions.get("tsm_pct", 0)
        tsm_color = "#16a34a" if tsm_pct >= 0 else "#dc2626"
        tsm_sign = "+" if tsm_pct >= 0 else ""

        rows_html = f"""
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;width:55%;">2330 昨收</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-variant-numeric:tabular-nums;">{predictions['last_2330']}</td>
          </tr>
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">TSM ADR 漲跌幅</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-weight:700;color:{tsm_color};font-variant-numeric:tabular-nums;">{tsm_sign}{tsm_pct}%</td>
          </tr>
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">模型1（1:1 漲跌對應）</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-variant-numeric:tabular-nums;">{predictions['model1_1to1']}</td>
          </tr>
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">模型2（60日比值回歸）</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-variant-numeric:tabular-nums;">{m2_str}</td>
          </tr>
        """
        if rng:
            rows_html += f"""
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:14px;background:linear-gradient(135deg,#0284c7,#0ea5e9);color:#fff;font-weight:700;border-radius:6px 0 0 6px;">★ 合理區間（中值）</td>
            <td style="padding:14px;background:linear-gradient(135deg,#0284c7,#0ea5e9);color:#fff;text-align:right;font-size:18px;font-weight:700;border-radius:0 6px 6px 0;font-variant-numeric:tabular-nums;">
              {rng[0]} ~ {rng[1]}<br>
              <span style="font-size:13px;font-weight:400;opacity:0.85;">（中值 {predictions['mid']}）</span>
            </td>
          </tr>
            """
        pred_html = f'<table style="width:100%;border-collapse:collapse;margin:12px 0;">{rows_html}</table>'
    else:
        pred_html = f"<p style='color:#dc2626'>{predictions.get('error','資料缺失')}</p>"

    # ===== 4. LLM 分析（Markdown → HTML 後加樣式 + 三檔卡片化） =====
    analysis_html = _md_to_html(analysis)
    analysis_html = _style_analysis_html(analysis_html)
    analysis_html = _wrap_stance(analysis_html)
    analysis_html = _wrap_tw_picks(analysis_html)

    llm_label = (LLM_PROVIDER + "/" +
                 (GEMINI_MODEL if LLM_PROVIDER == "gemini" else CLAUDE_MODEL))

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>美股晨報 {report_date}</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang TC','Microsoft JhengHei',sans-serif;">
  <table role="presentation" style="width:100%;border-collapse:collapse;background:#f1f5f9;">
    <tr>
      <td align="center" style="padding:20px 12px;">
        <table role="presentation" style="max-width:680px;width:100%;border-collapse:collapse;background:#ffffff;border-radius:12px;box-shadow:0 4px 20px rgba(15,23,42,0.06);overflow:hidden;">

          <!-- HERO -->
          <tr>
            <td style="background:linear-gradient(135deg,#0c4a6e,#0284c7);padding:28px 28px 22px;color:#ffffff;">
              <div style="font-size:13px;letter-spacing:2px;opacity:0.85;margin-bottom:6px;">MORNING MARKET BRIEF</div>
              <h1 style="margin:0;font-size:26px;font-weight:700;color:#ffffff;line-height:1.3;">美股晨報</h1>
              <div style="margin-top:6px;font-size:15px;opacity:0.92;">{report_date} ・ <span style="background:rgba(255,255,255,0.18);padding:2px 10px;border-radius:12px;font-size:13px;">{mode}</span></div>
            </td>
          </tr>

          <!-- BODY -->
          <tr><td style="padding:28px 28px 8px;">

            <h2 style="color:#0f172a;font-size:20px;margin:0 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">一、美股收盤行情</h2>
            <table style="width:100%;border-collapse:collapse;margin:12px 0;font-size:14px;">
              <tr style="background:#f1f5f9;">
                <th style="padding:10px 14px;text-align:left;color:#475569;font-size:12px;letter-spacing:1px;">標的</th>
                <th style="padding:10px 14px;text-align:right;color:#475569;font-size:12px;letter-spacing:1px;">收盤</th>
                <th style="padding:10px 14px;text-align:right;color:#475569;font-size:12px;letter-spacing:1px;">漲跌</th>
                <th style="padding:10px 14px;text-align:right;color:#475569;font-size:12px;letter-spacing:1px;">高/低</th>
                <th style="padding:10px 14px;text-align:right;color:#475569;font-size:12px;letter-spacing:1px;">成交量</th>
              </tr>
              {quote_rows}
            </table>

            <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">二、00662 公允淨值換算</h2>
            {fair_html}

            <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">三、2330 開盤合理價預測</h2>
            {pred_html}

            <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">四、市場速報與分析</h2>
            <div>{analysis_html}</div>

          </td></tr>

          <!-- FOOTER -->
          <tr>
            <td style="padding:18px 28px;background:#f8fafc;border-top:1px solid #e2e8f0;color:#94a3b8;font-size:11px;line-height:1.7;">
              本信件由自動化腳本於 GitHub Actions 產生。<br>
              資料來源：Yahoo Finance、TWSE OpenAPI、Reuters、CNBC、Bloomberg、Federal Reserve、鉅亨網、經濟日報、工商時報、中央社。<br>
              分析由 LLM ({llm_label}) 生成，僅供參考，不構成投資建議。
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def send_email(html: str, subject: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT
    msg.set_content("此郵件需以 HTML 模式檢視。")
    msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        s.send_message(msg)
    print(f"[mail] 已寄出 → {RECIPIENT}")


def determine_mode(now_tpe: dt.datetime) -> str:
    """判斷今日為一般報 (週二~週六) 還是週末綜合報 (週一)。"""
    wd = now_tpe.weekday()  # Mon=0
    return "週末綜合" if wd == 0 else "每日報"


# ---------- 主流程 ----------
def main() -> int:
    now_tpe = dt.datetime.now(TPE)
    mode = determine_mode(now_tpe)
    report_date = now_tpe.strftime("%Y-%m-%d (%a)")

    print(f"[main] 開始產生 {mode} 報告 — {report_date}")

    # 1. 抓行情
    quotes = {
        "QQQ": fetch_quote("QQQ"),
        "TSM": fetch_quote("TSM"),
        "SPY": fetch_quote("SPY"),
    }
    quotes["USDTWD"] = fetch_usdtwd()

    # 2. 抓 00662 昨收
    q662 = fetch_quote("00662.TW")
    last_00662 = q662.get("close")

    # 3. 抓 2330 歷史
    hist_2330 = fetch_2330_recent()

    # 4. 計算
    fair = calc_00662_fair_value(
        quotes["QQQ"]["close"], quotes["QQQ"]["prev_close"],
        quotes["USDTWD"], last_00662,
    )
    predictions = calc_2330_predictions(
        quotes["TSM"]["close"], quotes["TSM"]["prev_close"],
        quotes["USDTWD"], hist_2330,
    )

    # 5. 抓新聞
    print("[main] 抓新聞中…")
    news = fetch_news()
    print(f"[main] 抓到 {len(news)} 則新聞")

    # 6. 抓 0050 成分股法人/表現
    print("[main] 抓 0050 成分股法人買賣超與近期表現…")
    try:
        tw0050 = fetch_tw0050_snapshot()
    except Exception as e:
        print(f"[main] 0050 抓取失敗: {e}", file=sys.stderr)
        tw0050 = []

    # 7. LLM 分析
    print(f"[main] 呼叫 LLM 分析… (provider={LLM_PROVIDER})")
    analysis = call_llm_analysis(quotes, fair, predictions, news, tw0050)

    # 8. 組信
    html = render_html(quotes, fair, predictions, analysis, report_date, mode)

    # 8. dry-run 模式：只輸出檔案
    if os.environ.get("DRY_RUN") == "1":
        out = "/tmp/morning_report_preview.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[main] DRY_RUN — 預覽寫入 {out}")
        return 0

    # 9. 寄信
    subject = f"📈 美股晨報 {report_date} | QQQ {quotes['QQQ'].get('change_pct','?')}% / TSM {quotes['TSM'].get('change_pct','?')}%"
    send_email(html, subject)
    return 0


if __name__ == "__main__":
    sys.exit(main())
