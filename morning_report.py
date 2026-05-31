"""
美股收盤晨報自動化
=================
每日台灣時間 07:00 抓取昨晚美股 (QQQ / TSM / SPY) 收盤價，
換算 00662 公允淨值、雙模型預測 2330 開盤合理價，
並用 LLM API 產生新聞速報與分析，最後以 Gmail SMTP 寄出。

支援 LLM 提供商（環境變數 LLM_PROVIDER 控制）：
  - "gemini"    → Google Gemini 2.5 Flash（免費 1500 req/日）
  - "deepseek"  → DeepSeek V4 Pro/Flash（NT$3/月，中文超強，推薦）
  - "anthropic" → Claude Sonnet（NT$46/月，品質最佳）

執行條件 (cron 已處理)：台灣時間週二至週六 07:00。週一另判斷。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import smtplib
import ssl
import subprocess
import sys
import textwrap
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import feedparser
import pandas as pd
import requests
import yfinance as yf

# ---------- 設定 ----------
TPE = ZoneInfo("Asia/Taipei")
NY = ZoneInfo("America/New_York")

# 寄信憑證：import 時不強制存在，只有真正 send_email() 才檢查。
# 這樣 pytest / 其他 import 情境不需設 Gmail secret 也能載入模組。
GMAIL_USER = os.environ.get("GMAIL_USER", "")            # e.g. you@gmail.com
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


def _parse_recipients(raw: str) -> list[str]:
    """RECIPIENT 支援多位收件者：逗號或分號分隔，例如 'a@gmail.com,b@gmail.com'。"""
    return [r.strip() for r in (raw or "").replace(";", ",").split(",") if r.strip()]


# 收件者清單；未設 RECIPIENT 則寄給自己。RECIPIENT 字串形式保留供向後相容。
RECIPIENTS = _parse_recipients(os.environ.get("RECIPIENT", "")) or (
    [GMAIL_USER] if GMAIL_USER else [])
RECIPIENT = ", ".join(RECIPIENTS)

# SEC EDGAR 要求 User-Agent 內含聯絡 email；不寫死在原始碼，改讀環境變數。
CONTACT_EMAIL = (os.environ.get("CONTACT_EMAIL") or GMAIL_USER
                 or "morning-report-bot@users.noreply.github.com")


def _parse_portfolio(raw: str) -> dict[str, float]:
    """
    解析「我的持股」設定字串。隱私:這些是個人持股,只進記憶體與漲幅彙總,
    **絕不**寫進 HTML / LLM prompt / state 檔(信件公開寄出,僅顯示彙總 % 與金額)。

    支援兩種格式:
      JSON:  {"2330": 5, "2454": 2}            # 代號 → 張數
      簡易:  2330:5,2454:2  或  2330:5;2454:2   # 同上,逗號/分號分隔
    張數可為小數(零股以張為單位,如 0.5 = 500 股)。解析失敗回 {}。
    """
    raw = (raw or "").strip()
    if not raw:
        return {}
    out: dict[str, float] = {}
    try:
        if raw.startswith("{"):
            data = json.loads(raw)
            for k, v in (data or {}).items():
                code = str(k).strip()
                lots = float(v)
                if code and lots > 0:
                    out[code] = lots
        else:
            for pair in raw.replace(";", ",").split(","):
                if ":" not in pair:
                    continue
                code, lots_str = pair.split(":", 1)
                code = code.strip()
                lots = float(lots_str.strip())
                if code and lots > 0:
                    out[code] = lots
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        print(f"[portfolio] 設定解析失敗(將略過持股預測): {e}", file=sys.stderr)
        return {}
    return out


# 兩個倉位的持股設定(GitHub Secrets / 環境變數)。未設 → 不顯示持股欄位。
# 注意:個股代號與張數僅存記憶體,信件只顯示彙總漲幅 % 與金額,不揭露明細。
PORTFOLIO_1 = _parse_portfolio(os.environ.get("PORTFOLIO_1", ""))
PORTFOLIO_2 = _parse_portfolio(os.environ.get("PORTFOLIO_2", ""))
# 倉位顯示名稱(可自訂,如「主帳戶」「定存股」);預設「持倉1/持倉2」。
PORTFOLIO_1_NAME = os.environ.get("PORTFOLIO_1_NAME", "持倉1").strip() or "持倉1"
PORTFOLIO_2_NAME = os.environ.get("PORTFOLIO_2_NAME", "持倉2").strip() or "持倉2"

# 槓桿 ETF:今日預估漲幅 = 倍數 × 基準標的預估 %(基準 key 對應 special_preds)。
# 比「beta × 加權」更準,因為基準(0050)的預測已吃進 2330 ADR 訊號。
#   00631L 元大台灣50正2 → 2× 0050(同追台灣50指數,單日 2 倍)
#   00675L 富邦台灣加權正2 / 00663L 國泰臺灣加權正2 → 2× 加權(taiex)
LEVERAGED_ETF: dict[str, tuple] = {
    "00631L": ("0050", 2.0),
    "00675L": ("taiex", 2.0),
    "00663L": ("taiex", 2.0),
}

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
# DeepSeek 模型名：
#   deepseek-v4-pro     → V4 Pro（推薦，分析最深，支援思考模式）
#   deepseek-v4-flash   → V4 Flash（便宜版）
#   deepseek-chat       → V4 Flash 非思考模式別名（2026/07/24 後棄用）
#   deepseek-reasoner   → V4 Flash 思考模式別名（2026/07/24 後棄用）
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# 思考模式強度（high / medium / low；設 off/none 關閉）。
# 僅對 v4-pro / reasoner 生效，可顯著提升分析推理深度（成本略升）。
DEEPSEEK_REASONING_EFFORT = os.environ.get("DEEPSEEK_REASONING_EFFORT", "high").strip().lower()

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

    # === 中國對台/對美深度新聞（Opt 5）===
    "Reuters China":     "https://www.reuters.com/arc/outboundfeeds/rss/category/world/china/?outputType=xml",
    "南華早報":           "https://www.scmp.com/rss/91/feed",          # 經濟
    "南華早報-科技":      "https://www.scmp.com/rss/36/feed",           # 中國科技
    "Nikkei Asia 中國":  "https://asia.nikkei.com/rss/feed/nar",       # 日經亞洲（中國頻道）
    "BBC 中文-兩岸":      "https://feeds.bbci.co.uk/zhongwen/trad/rss.xml",
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


# ---------- 上櫃熱門股（Opt 3，與 0050 互補的高動能標的） ----------
# 主要為 OTC 上櫃 + 部分上市 AI/半導體/Apple 供應鏈熱門股
TW_OTC_HOT: dict[str, str] = {
    "6488": "環球晶 — 全球第三大晶圓代工矽晶圓廠，受惠 AI 矽晶圓需求",
    "6446": "藥華藥 — 紅血球增多症孤兒藥 Besremi，FDA 已上市銷售",
    "3008": "大立光 — 高階手機鏡頭龍頭（已在 0050，仍列供參考）",
    "8069": "元太 — 全球電子紙龍頭，蘋果/Kindle 主要供應商",
    "6669": "緯穎 — Meta/Microsoft 雲端伺服器代工，AI 伺服器二線龍頭",
    "3293": "鈊象 — 商用機台與線上遊戲，金雞母 ROE 持續高檔",
    "6781": "AES-KY — 高效能伺服器電池備援系統 (BBU)，AI 資料中心新興主力",
    "3661": "世芯-KY — IC 設計服務 (ASIC)，AI 客製晶片受惠者",
    "6504": "南六 — 不織布龍頭，內需消費",
    "1707": "葡萄王 — 益生菌與保健食品",
    "6691": "洋基工程 — 半導體無塵室與機電統包，台積電擴廠主要承包商",
    "5483": "中美晶 — 半導體矽晶圓 + 太陽能",
    "3413": "京鼎 — 半導體製程設備代工（艾司摩爾/應材的台廠夥伴）",
    "6533": "晶心科 — RISC-V 處理器 IP 設計，AI 邊緣晶片潛在受惠",
    "6515": "穎崴 — 半導體測試介面，先進封裝測試核心廠",
    "8299": "群聯 — 全球第二大 NAND 控制晶片，AI PC/SSD 受惠",
    "8210": "勤誠 — 伺服器機殼龍頭，AI 機櫃結構主力",
    "5269": "祥碩 — USB/SATA 控制晶片，蘋果/AMD 主要客戶",
    "6781": "AES-KY — 高效能伺服器 BBU（重複，AI 資料中心電池）",
}


# ---------- 工具函式 ----------
def safe_float(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def require_quote(quotes: dict, key: str) -> Optional[dict]:
    """
    取出一檔行情，若抓取失敗（error dict 或缺 close/prev_close）回傳 None。
    讓 main() 在資料缺失時走降級流程，而不是在 quotes[key]["close"] 直接 KeyError 爆掉。
    """
    q = quotes.get(key)
    if not isinstance(q, dict):
        return None
    if q.get("error") or q.get("close") is None or q.get("prev_close") is None:
        return None
    return q


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


def fetch_usdtwd_pair() -> tuple[Optional[float], Optional[float]]:
    """同時抓今日與昨日匯率，供匯率變動因子計算。"""
    try:
        d = yf.Ticker("TWD=X").history(period="10d")
        d = d.dropna(subset=["Close"])
        d = d[d["Close"] > 0]
        if len(d) < 2:
            return (safe_float(d.iloc[-1]["Close"]) if len(d) else None, None)
        return (round(safe_float(d.iloc[-1]["Close"]), 4),
                round(safe_float(d.iloc[-2]["Close"]), 4))
    except Exception:
        return (None, None)


# 硬編關鍵 CIK（TSMC ADR 及最大型科技股 — 永遠追蹤，不受 SEC ticker→CIK 對應檔變動影響）
SEC_BASE_COMPANIES: dict[str, str] = {
    "0001046179": "TSMC (台積電)",
    "0001045810": "NVIDIA",
    "0000789019": "Microsoft",
    "0000320193": "Apple",
    "0001318605": "Tesla",
    "0001730168": "Broadcom",
    "0000002488": "AMD",
    "0001326801": "Meta",
    "0001652044": "Alphabet (Google)",
    "0001018724": "Amazon",
}

# NASDAQ-100 成分股 ticker（00662 的追蹤標的）。CIK 透過 SEC 官方對照檔動態查。
# 列表每年小幅調整（~5-10 檔）；抓不到的 ticker 會被自動跳過。
NDX_TICKERS: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "AVGO", "TSLA", "COST",
    "NFLX", "TMUS", "CSCO", "PEP", "ADBE", "LIN", "AMD", "INTU", "ISRG", "TXN",
    "QCOM", "AMGN", "BKNG", "HON", "AMAT", "VRTX", "GILD", "CMCSA", "PANW", "ADP",
    "MU", "SBUX", "MDLZ", "LRCX", "KLAC", "ADI", "MELI", "CDNS", "REGN", "SNPS",
    "CRWD", "ABNB", "MAR", "ASML", "CTAS", "MNST", "ORLY", "WDAY", "PYPL", "FTNT",
    "NXPI", "ROP", "CHTR", "EXC", "ADSK", "DXCM", "ROST", "CCEP", "MRVL", "CSGP",
    "AEP", "CPRT", "FANG", "XEL", "PCAR", "AZN", "PAYX", "DDOG", "TEAM", "IDXX",
    "ZS", "MCHP", "BIIB", "ON", "FAST", "ODFL", "CTSH", "WBD", "DLTR", "ANSS",
    "GEHC", "GFS", "DASH", "WBA", "LULU", "PDD", "CDW", "TTD", "CSX", "BKR",
    "ARM", "KDP", "MRNA", "TTWO", "ILMN", "VRSK", "CEG", "EA", "APP", "SMCI",
]

# 「重點科技股」白名單:8-K 公告區塊只顯示這些(美股前 10 大市值 + 關鍵半導體/AI/設備/EDA)。
# 排除 NDX-100 裡的消費/零售/工業雜訊(Ross/Lululemon/Mondelez/Comcast/Honeywell/CDW…)。
# 注意:LLM prompt 仍吃全部 8-K(供「科技板塊脈動」取材),只有 email 顯示套用此過濾。
SEC_PRIORITY_TICKERS: set = {
    # 美股前 10 大市值(科技權值)
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "AVGO", "TSLA", "AMD",
    # 關鍵半導體 / 設備 / EDA / AI 伺服器(直接牽動 2330 / 00662 供應鏈)
    "QCOM", "MRVL", "AMAT", "LRCX", "KLAC", "ASML", "MU", "TXN", "ADI", "NXPI",
    "MCHP", "ON", "SNPS", "CDNS", "ARM", "SMCI",
}

_SEC_CIK_CACHE: dict = {}


def _load_sec_cik_map() -> dict[str, tuple[str, str]]:
    """從 SEC 官方對照檔一次性下載 ticker→(CIK, name) 對應表（~4MB JSON）。
    同一程式生命週期內只下載一次。失敗回 {}。"""
    if _SEC_CIK_CACHE:
        return _SEC_CIK_CACHE
    try:
        r = requests.get("https://www.sec.gov/files/company_tickers.json",
                         timeout=20,
                         headers={"User-Agent": f"Morning Report Bot {CONTACT_EMAIL}"})
        r.raise_for_status()
        data = r.json()
        # data 結構: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
        for v in data.values():
            t = str(v.get("ticker", "")).strip().upper()
            cik = v.get("cik_str")
            title = v.get("title", t)
            if t and cik is not None:
                _SEC_CIK_CACHE[t] = (f"{int(cik):010d}", title)
        print(f"[sec] CIK 對照表載入 {len(_SEC_CIK_CACHE)} 檔")
        return _SEC_CIK_CACHE
    except Exception as e:
        print(f"[sec] CIK 對照表載入失敗: {e}", file=sys.stderr)
        return {}


def fetch_sec_filings() -> list[dict]:
    """
    抓 SEC 8-K 重大事件公告（近 2 日）。SEC EDGAR API 完全免費，無 API key。

    覆蓋範圍：
    - 硬編：TSMC ADR + 10 大型科技股（永遠追蹤）
    - 動態：NASDAQ-100 ~100 檔（透過 SEC 官方 ticker→CIK 對照檔解析）

    用 ThreadPoolExecutor 並行 8 條，避免 110 個序列請求拖到 timeout。
    """
    item_codes = {
        "1.01": "重大協議簽署",
        "1.02": "重大協議終止",
        "2.02": "財報結果發布",
        "2.06": "重大資產減損",
        "5.02": "高層人事變動",
        "7.01": "Reg FD 揭露",
        "8.01": "其他重大事件",
    }

    # 合併硬編 + NDX-100 解析後的 CIK
    companies: dict[str, str] = dict(SEC_BASE_COMPANIES)
    cik_map = _load_sec_cik_map()
    # priority_ciks:屬於「重點科技股」白名單者(email 8-K 區塊只顯示這些;LLM 仍吃全部)
    priority_ciks: set = set(SEC_BASE_COMPANIES.keys())   # mega-cap + 台積電一律重點
    for ticker in NDX_TICKERS:
        entry = cik_map.get(ticker.upper())
        if not entry:
            continue
        cik, name = entry
        if ticker.upper() in SEC_PRIORITY_TICKERS:
            priority_ciks.add(cik)
        if cik not in companies:
            companies[cik] = f"{name} ({ticker})"

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=48)
    headers = {
        "User-Agent": f"Morning Report Bot {CONTACT_EMAIL}",
        "Accept": "application/json",
    }

    def _fetch_one(item: tuple[str, str]) -> list[dict]:
        cik, name = item
        out: list[dict] = []
        try:
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            r = requests.get(url, timeout=8, headers=headers)
            if r.status_code != 200:
                return out
            data = r.json()
            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            accessions = recent.get("accessionNumber", [])
            primary_docs = recent.get("primaryDocument", [])
            items = recent.get("items", [])
            for i, form in enumerate(forms[:10]):
                if form not in ("8-K", "8-K/A"):
                    continue
                filed_date_str = dates[i] if i < len(dates) else ""
                try:
                    filed_dt = dt.datetime.strptime(filed_date_str, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
                except ValueError:
                    continue
                if filed_dt < cutoff:
                    continue
                item_codes_str = items[i] if i < len(items) else ""
                item_labels = []
                for c in item_codes_str.split(","):
                    c = c.strip()
                    if c in item_codes:
                        item_labels.append(f"{c} {item_codes[c]}")
                accession = accessions[i] if i < len(accessions) else ""
                primary = primary_docs[i] if i < len(primary_docs) else ""
                link = ""
                if accession and primary:
                    accession_no_dash = accession.replace("-", "")
                    link = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dash}/{primary}"
                out.append({
                    "company": name,
                    "form": form,
                    "date": filed_date_str,
                    "items": item_labels or [item_codes_str],
                    "link": link,
                    # 是否屬「重點科技股」白名單(email 8-K 區塊只顯示 priority=True)
                    "priority": cik in priority_ciks,
                })
        except Exception as e:
            print(f"[sec] {name} 抓取失敗: {e}", file=sys.stderr)
        return out

    from concurrent.futures import ThreadPoolExecutor
    filings: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for sub in ex.map(_fetch_one, companies.items()):
            filings.extend(sub)

    # 依日期 desc 排序，方便 render 取前 N 筆
    filings.sort(key=lambda f: f.get("date", ""), reverse=True)
    print(f"[sec] 追蹤 {len(companies)} 家公司，抓到 {len(filings)} 筆近 2 日 8-K 公告")
    return filings


def fetch_tw_major_announcements(codes: list[str], hours: int = 48) -> list[dict]:
    """
    抓台股指定公司近 N 小時的「重大訊息」(MOPS 公開資訊觀測站，每家公司一支 RSS)。
    免費無 API key。個別公司失敗(RSS 端點偶爾不穩)會略過，不影響其他。

    回傳：[{"code","title","link","published"}, ...] 依時間 desc。整體失敗回 []。
    """
    if not codes:
        return []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    out: list[dict] = []
    for code in codes:
        try:
            url = f"https://mops.twse.com.tw/mops/web/t05st01_rss?step=0&co_id={code}"
            r = requests.get(url, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200 or len(r.text) < 100:
                continue
            feed = feedparser.parse(r.text)
            for entry in feed.entries[:10]:
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub:
                    pub_dt = dt.datetime(*pub[:6], tzinfo=dt.timezone.utc)
                    if pub_dt < cutoff:
                        continue
                out.append({
                    "code": code,
                    "title": (entry.get("title", "") or "").strip(),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                })
        except Exception as e:
            print(f"[mops] {code} 失敗: {e}", file=sys.stderr)
            continue
    out.sort(key=lambda x: x.get("published", ""), reverse=True)
    print(f"[mops] 取得 {len(out)} 筆台股重大訊息（{len(codes)} 家公司）")
    return out


# 2330 歷史法說會日期（含每季 + 重大投資人會議；可隨年度更新）
TSMC_EARNINGS_DATES = [
    # 2026 預估（依過去慣例每季第三週週四）
    "2026-01-15", "2026-04-16", "2026-07-16", "2026-10-15",
    # 2027 預估
    "2027-01-21", "2027-04-15", "2027-07-15", "2027-10-21",
]


def check_tsmc_earnings_proximity() -> dict:
    """
    Opt 7: 判斷今日是否接近 2330 法說會。
    法說會前後 ±2 天，預測信心降為「低」（市場易現劇烈波動）。
    法說會當週也降信心，前 1 週稍降。
    """
    today = dt.datetime.now(TPE).date()
    closest_days = 999
    closest_date = None
    for date_str in TSMC_EARNINGS_DATES:
        try:
            d = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
            delta = abs((d - today).days)
            if delta < closest_days:
                closest_days = delta
                closest_date = date_str
        except ValueError:
            continue

    if closest_days <= 2:
        impact = "critical"
        note = f"法說會 ±2 天（{closest_date}）— 預測信心顯著下降，2330 走勢可能脫離 ADR 連動"
    elif closest_days <= 5:
        impact = "high"
        note = f"法說會週（{closest_date}）— 預測信心略降，留意盤後新聞"
    elif closest_days <= 10:
        impact = "elevated"
        note = f"法說會前 1-2 週（{closest_date}）— 法人持倉可能調整"
    else:
        impact = "normal"
        note = f"距下次法說會 {closest_days} 天（{closest_date}）"

    return {
        "closest_date": closest_date,
        "days_to": closest_days,
        "impact": impact,
        "note": note,
    }


def fetch_weekly_momentum() -> dict:
    """
    Opt 6: 計算 QQQ/TSM/SPY/VIX/SOX/DXY/00662.TW/2330.TW
    過去 5 個交易日累積漲跌幅，給 LLM 看「一週動能」。
    """
    tickers = {
        "QQQ": "QQQ",
        "TSM": "TSM",
        "SPY": "SPY",
        "VIX": "^VIX",
        "SOX": "^SOX",
        "DXY": "DX-Y.NYB",
        "00662": "00662.TW",
        "2330": "2330.TW",
    }
    out: dict[str, dict] = {}
    for name, sym in tickers.items():
        try:
            d = yf.Ticker(sym).history(period="14d", auto_adjust=False)
            d = d.dropna(subset=["Close"])
            d = d[d["Close"] > 0]
            if len(d) < 6:
                continue
            last = safe_float(d.iloc[-1]["Close"])
            wk_ago = safe_float(d.iloc[-6]["Close"])  # 約 5 個交易日前
            pct_5d = ((last - wk_ago) / wk_ago * 100) if wk_ago else None
            out[name] = {
                "last": round(last, 3),
                "five_days_ago": round(wk_ago, 3),
                "pct_5d": round(pct_5d, 2) if pct_5d is not None else None,
            }
        except Exception as e:
            print(f"[weekly] {name} 失敗: {e}", file=sys.stderr)
    return out


def fetch_twse_margin() -> dict:
    """
    抓 TWSE 信用交易（融資融券）總額（Opt 4）。
    端點：https://www.twse.com.tw/exchangeReport/MI_MARGN

    融資增加 = 散戶積極做多（過熱反向指標）
    融券增加 = 散戶看空（軋空反向指標）
    與外資籌碼背離時為強訊號。
    """
    today = dt.datetime.now(TPE).date()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json",
    }
    for back in range(1, 8):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y%m%d")
        url = (f"https://www.twse.com.tw/exchangeReport/MI_MARGN"
               f"?response=json&date={date_str}&selectType=MS")
        try:
            r = requests.get(url, timeout=15, headers=headers)
            r.raise_for_status()
            data = r.json()
            if data.get("stat") != "OK":
                continue
            # 「融資融券彙總表」
            # tables[0] 有日期；tables[1] 才是融資融券總額表
            tables = data.get("tables") or []
            margin_table = None
            for t in tables:
                fields = t.get("fields") or t.get("title") or []
                # 找含「融資」「融券」欄位的表
                fields_str = " ".join(fields) if isinstance(fields, list) else ""
                if "融資" in fields_str and "融券" in fields_str:
                    margin_table = t
                    break
            if not margin_table:
                # 退化：用第一個 data 不為空的 table
                for t in tables:
                    if t.get("data"):
                        margin_table = t
                        break
            if not margin_table:
                continue

            # 通常第一列 = 整體市場合計
            rows = margin_table.get("data") or []
            if not rows:
                continue
            row = rows[0]
            # 欄位順序通常為：項目 / 買進 / 賣出 / 現金償還 / 前日餘額 / 今日餘額 / 限額
            try:
                # 嘗試找「今日餘額」對應欄位（在欄位 5 或 6）
                # 不同年份格式略異，用試錯
                margin_balance = None
                for idx in (5, 6, 4):
                    if idx < len(row):
                        v = _to_int(row[idx])
                        if v > 1_000_000:  # 融資餘額至少數十億張
                            margin_balance = v
                            break
                return {
                    "date": d.strftime("%Y/%m/%d"),
                    "margin_balance": margin_balance,
                    "raw_row": row,  # 給除錯用
                }
            except Exception:
                continue
        except Exception as e:
            print(f"[margin] {date_str} 失敗: {e}", file=sys.stderr)
            continue
    print("[margin] 所有日期皆失敗", file=sys.stderr)
    return {}


def fetch_taifex_night_session() -> dict:
    """
    抓 TAIFEX 台指期夜盤收盤 (Opt B)。
    夜盤交易時間：14:45 - 翌日 05:00。
    早上 6:00 自動跑時，夜盤剛收，是「大盤開盤方向最直接的訊號」。

    回傳：{ "date": "...", "night_close": N, "day_close": N, "night_pct": +X.XX }
    """
    today = dt.datetime.now(TPE).date()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml",
    }
    for back in range(0, 5):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y/%m/%d")
        try:
            # TAIFEX 期貨每日交易行情下載
            url = "https://www.taifex.com.tw/cht/3/futDataDown"
            payload = {
                "down_type": "1",
                "commodity_id": "TX",
                "queryStartDate": date_str,
                "queryEndDate": date_str,
            }
            r = requests.post(url, data=payload, timeout=15, headers=headers)
            if r.status_code != 200 or len(r.text) < 200:
                continue
            try:
                text = r.content.decode("big5", errors="replace")
            except Exception:
                text = r.text

            import csv
            from io import StringIO
            reader = csv.reader(StringIO(text))
            rows = list(reader)
            if len(rows) < 2:
                continue

            # 以表頭定位欄位（勿硬編 index：「交易時段」不一定在最後一欄，
            # 這正是夜盤長期抓不到的原因）。
            header_i = close_i = session_i = month_i = None
            for ri, row in enumerate(rows[:6]):
                for ci, cell in enumerate(row):
                    c = cell.strip()
                    if close_i is None and "收盤" in c and "結算" not in c:
                        close_i = ci
                    if session_i is None and ("交易時段" in c or c == "盤別"):
                        session_i = ci
                    if month_i is None and ("到期月份" in c or "契約月份" in c):
                        month_i = ci
                if close_i is not None and session_i is not None:
                    header_i = ri
                    break
            if close_i is None or session_i is None:
                print(f"[taifex_night] {date_str} 表頭偵測失敗，跳過", file=sys.stderr)
                continue

            # 找近月合約（無到期月 W 字樣的），分開「一般」與「盤後」
            day_close = None
            night_close = None
            for row in rows[header_i + 1:]:
                if len(row) <= max(close_i, session_i, month_i or 0):
                    continue
                session = row[session_i].strip()
                if month_i is not None and "W" in row[month_i].strip():
                    continue   # 跳過週選 / 週期貨
                close_val = safe_float(row[close_i])
                if not close_val:
                    continue
                if "盤後" in session or "夜盤" in session or "PM" in session.upper():
                    if night_close is None:
                        night_close = close_val
                else:
                    if day_close is None:
                        day_close = close_val

            if day_close and night_close:
                night_pct = (night_close - day_close) / day_close * 100
                print(f"[taifex_night] {date_str} 日盤 {day_close} → 夜盤 {night_close} ({night_pct:+.2f}%)")
                return {
                    "date": date_str,
                    "day_close": day_close,
                    "night_close": night_close,
                    "night_pct": round(night_pct, 2),
                }
        except Exception as e:
            print(f"[taifex_night] {date_str} 失敗: {e}", file=sys.stderr)
            continue
    print("[taifex_night] 所有日期皆失敗", file=sys.stderr)
    return {}


def fetch_taifex_foreign_futures() -> dict:
    """
    抓 TAIFEX 期交所三大法人台指期未平倉（Task E）。
    來源：https://www.taifex.com.tw/cht/3/futContractsDate

    這是「外資對台股當日方向最直接的領先指標」：
    - 外資台指期淨多單 增加 → 看多台股
    - 外資台指期淨多單 減少 / 轉空 → 看空台股
    - 夜盤一般 T+1 更新，故我們抓的是「昨日收盤後」資料

    回傳：{
        "date": "...",
        "foreign_oi_net": +N (口數，正多負空),
        "foreign_oi_change": +N (與前一日差異),
        "invest_oi_net": +N,
        "dealer_oi_net": +N,
    }
    """
    # TAIFEX 官方資料下載端點
    today = dt.datetime.now(TPE).date()
    for back in range(1, 10):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y/%m/%d")
        try:
            url = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
            payload = {
                "queryStartDate": date_str,
                "queryEndDate": date_str,
                "commodityId": "TXF",  # 台指期
            }
            r = requests.post(url, data=payload, timeout=15,
                              headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200 or len(r.text) < 200:
                continue
            # CSV 格式：日期、商品名稱、身份別、多方交易、空方交易、未平倉多方、未平倉空方、淨多空
            import csv
            from io import StringIO
            # TAIFEX CSV 為 Big5 編碼
            try:
                text = r.content.decode("big5", errors="replace")
            except Exception:
                text = r.text
            reader = csv.reader(StringIO(text))
            rows = list(reader)
            if len(rows) < 3:
                continue

            # 以表頭自動定位「多空淨額未平倉口數」欄。
            # 注意：絕不可硬編 index —— 該欄旁邊就是「多空淨額未平倉契約金額(千元)」，
            # 抓錯欄會把「金額」當「口數」讀，數字爆掉上萬倍。
            header_i = netoi_i = None
            role_i = 2
            for ri, row in enumerate(rows[:6]):
                for ci, cell in enumerate(row):
                    c = cell.strip()
                    if "多空淨額" in c and "未平倉" in c and "口數" in c and "金額" not in c:
                        header_i, netoi_i = ri, ci
                    if "身份別" in c:
                        role_i = ci
                if netoi_i is not None:
                    break
            if netoi_i is None:
                print(f"[taifex] {date_str} 表頭偵測失敗，跳過", file=sys.stderr)
                continue

            result = {"date": date_str}
            for row in rows[header_i + 1:]:
                if len(row) <= max(role_i, netoi_i):
                    continue
                role = row[role_i].strip()
                net_oi = _to_int(row[netoi_i])
                if "外資" in role or "外國" in role:
                    result["foreign_oi_net"] = net_oi
                elif "投信" in role:
                    result["invest_oi_net"] = net_oi
                elif "自營" in role:
                    result["dealer_oi_net"] = net_oi

            if "foreign_oi_net" in result:
                print(f"[taifex] {date_str} 外資台指期淨未平倉 = {result['foreign_oi_net']:+d} 口")
                return result
        except Exception as e:
            print(f"[taifex] {date_str} 抓取失敗: {e}", file=sys.stderr)
            continue
    print("[taifex] 所有日期皆失敗", file=sys.stderr)
    return {}


def fetch_macro_indicators() -> dict:
    """
    抓關鍵總經 + 國際連動指標 + 過去 252 日歷史百分位（Task D）：
    - VIX：恐慌指數（30 日隱含波動率）
    - VIX9D：9 日 VIX，與 VIX 比較得 term structure
    - SOX：費城半導體指數
    - 10Y：美國 10 年期公債殖利率
    - DXY：美元指數
    - 13W：3 個月國庫券殖利率
    - N225：日經 225（亞股開盤領先參考）
    - SSE：上證綜合指數（中國盤面，影響台股資金面與情緒）
    - NQ：Nasdaq-100 期貨（US 收盤後到 TW 開盤的連續訊號）
    - ES：S&P 500 期貨（同上，廣度確認）
    - WTI：原油期貨（通膨/地緣定價）
    - GOLD：黃金期貨（避險偏好）
    每項回傳：close, change_pct, prev_close, pct_rank_252d, year_high, year_low
    """
    tickers = {
        "VIX":   "^VIX",
        "VIX9D": "^VIX9D",
        "SOX":   "^SOX",
        "10Y":   "^TNX",
        "DXY":   "DX-Y.NYB",
        "13W":   "^IRX",
        "N225":  "^N225",
        "SSE":   "000001.SS",
        "NQ":    "NQ=F",
        "ES":    "ES=F",
        "WTI":   "CL=F",
        "GOLD":  "GC=F",
    }
    out: dict[str, dict] = {}
    for name, sym in tickers.items():
        try:
            d = yf.Ticker(sym).history(period="1y", auto_adjust=False)
            d = d.dropna(subset=["Close"])
            d = d[d["Close"] > 0]
            if len(d) < 2:
                out[name] = {"error": "資料不足"}
                continue
            close = safe_float(d.iloc[-1]["Close"])
            prev  = safe_float(d.iloc[-2]["Close"])
            pct = ((close - prev) / prev * 100) if prev else None

            # 歷史百分位 (252 日)
            window = d["Close"].tail(252)
            pct_rank = None
            year_high = year_low = None
            if len(window) >= 60:  # 至少 3 個月才有意義
                pct_rank = float((window <= close).sum() / len(window) * 100)
                year_high = float(window.max())
                year_low = float(window.min())

            out[name] = {
                "close": round(close, 3),
                "prev_close": round(prev, 3),
                "change_pct": round(pct, 2) if pct is not None else None,
                "pct_rank_252d": round(pct_rank, 1) if pct_rank is not None else None,
                "year_high": round(year_high, 3) if year_high else None,
                "year_low": round(year_low, 3) if year_low else None,
            }
        except Exception as e:
            print(f"[macro] {name} 抓取失敗: {e}", file=sys.stderr)
            out[name] = {"error": str(e)[:60]}

    # VIX 期限結構：VIX9D vs VIX
    # ratio > 1 = backwardation（短期波動率 > 中期）→ 市場短期恐慌升溫，偏空訊號
    # ratio < 1 = contango（正常結構）→ 中性
    try:
        v_short = (out.get("VIX9D") or {}).get("close")
        v_mid = (out.get("VIX") or {}).get("close")
        if v_short and v_mid and v_mid > 0:
            ratio = v_short / v_mid
            state = "backwardation" if ratio > 1.0 else "contango"
            out["VIX_TERM"] = {
                "ratio": round(ratio, 4),
                "spread": round(v_short - v_mid, 2),
                "state": state,
            }
            print(f"[macro] VIX 期限結構 ratio={ratio:.3f} ({state})")
    except Exception as e:
        print(f"[macro] VIX 期限結構計算失敗: {e}", file=sys.stderr)

    return out


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


def _to_float(v) -> Optional[float]:
    """容忍逗號、空字串、None、'--' 的 float 轉換（TWSE OpenAPI 欄位常見）。"""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if not s or s in ("-", "--", "NA", "null", "None"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


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


def fetch_twse_institutional_cumulative(days_back: int = 30,
                                          target_codes: Optional[set] = None,
                                          keep_recent_days: int = 5) -> dict[str, dict]:
    """
    抓取近 N 個交易日法人買賣超累積值,同時保留最近 K 天的「逐日序列」供 streak 偵測用。

    回傳：{ "2330": {"foreign_cum", "invest_cum", "dealer_cum", "days",
                       "daily": [{"date": "20260520", "foreign": +N, "invest": +N, "dealer": +N}, ...]},
            ... }
    daily 最新在最後(時間升序)。

    為避免請求量爆炸，只抓 target_codes 指定的股票（預設只給 0050 成分股用）。
    """
    today = dt.datetime.now(TPE).date()
    cum: dict[str, dict] = {}
    days_collected = 0

    # 往前抓 days_back * 1.5 個自然日（含週末）;先暫存 (date, foreign, invest, dealer) 由舊到新
    daily_buffer: dict[str, list[dict]] = {}

    for back in range(1, int(days_back * 1.7) + 1):
        if days_collected >= days_back:
            break
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y%m%d")
        try:
            rows = _twse_main_api(date_str)
            if not rows:
                continue
        except Exception:
            continue

        # 一次性偵測欄位（同 fetch_twse_institutional 的邏輯，但精簡）
        sample_keys = list(rows[0].keys())
        def find_in(keys, *needles):
            for k in keys:
                kl = k.lower()
                if all(n.lower() in kl for n in needles):
                    return k
            return None
        def find_strict(keys, *cands):
            keys_clean = {k.strip(): k for k in keys}
            for c in cands:
                if c in keys_clean:
                    return keys_clean[c]
            return None
        def find_starts(keys, prefix):
            for k in keys:
                if k.strip().startswith(prefix):
                    return k
            return None

        f_key = find_in(sample_keys, "外陸資買賣超股數") or find_in(sample_keys, "外資") or find_in(sample_keys, "foreign", "over")
        t_key = find_strict(sample_keys, "投信買賣超股數") or find_starts(sample_keys, "投信買賣超股數") or find_in(sample_keys, "trust", "over")
        d_key = find_strict(sample_keys, "自營商買賣超股數") or find_starts(sample_keys, "自營商買賣超股數") or find_in(sample_keys, "dealer", "over")
        c_key = find_strict(sample_keys, "證券代號") or find_in(sample_keys, "code") or find_in(sample_keys, "stock")
        if not c_key:
            continue

        for row in rows:
            code = (row.get(c_key) or "").strip()
            if not code:
                continue
            if target_codes is not None and code not in target_codes:
                continue
            f = _to_int(row.get(f_key)) if f_key else 0
            t = _to_int(row.get(t_key)) if t_key else 0
            de = _to_int(row.get(d_key)) if d_key else 0
            entry = cum.setdefault(code, {"foreign_cum": 0, "invest_cum": 0, "dealer_cum": 0, "days": 0})
            entry["foreign_cum"] += f
            entry["invest_cum"] += t
            entry["dealer_cum"] += de
            entry["days"] += 1
            # 最近 K 天保留逐日序列(供 streak 計算)。此處用 days_collected 索引保證遠到近
            if days_collected < keep_recent_days:
                daily_buffer.setdefault(code, []).append({
                    "date": date_str,
                    "foreign": f,
                    "invest": t,
                    "dealer": de,
                })

        days_collected += 1

    # daily_buffer 此時是「由近到遠」(因為 back=1 先處理);翻成「由遠到近」方便讀
    for code, lst in daily_buffer.items():
        cum.setdefault(code, {"foreign_cum": 0, "invest_cum": 0, "dealer_cum": 0, "days": 0})
        cum[code]["daily"] = list(reversed(lst))

    print(f"[twse] {days_back} 日累積資料 — 共聚合 {days_collected} 天，{len(cum)} 檔股票"
          f"(逐日序列保留近 {keep_recent_days} 天)")
    return cum


def _calc_inst_streaks(daily: list[dict]) -> dict:
    """
    給定逐日法人買賣超序列(由遠到近),計算外資 / 投信「最新方向的連續天數」。

    回傳:
      foreign_streak: 正數 N = 連續 N 天買超, 負數 = 連續 N 天賣超, 0 = 最新一天為 0 或無資料
      invest_streak: 同上
    僅最近 5 天內看,避免反映過久遠的資料。
    """
    if not daily:
        return {"foreign_streak": 0, "invest_streak": 0}

    def streak_of(key: str) -> int:
        # 由近到遠遍歷,先看最新一天決定方向
        seq = list(reversed(daily))   # 最新在前
        latest = seq[0].get(key, 0) or 0
        if latest == 0:
            return 0
        sign = 1 if latest > 0 else -1
        n = 0
        for d in seq:
            v = d.get(key, 0) or 0
            if v == 0:
                break
            if (v > 0 and sign > 0) or (v < 0 and sign < 0):
                n += 1
            else:
                break
        return n * sign

    return {
        "foreign_streak": streak_of("foreign"),
        "invest_streak": streak_of("invest"),
    }


def fetch_twse_margin_per_stock(target_codes: Optional[set] = None) -> dict[str, dict]:
    """
    抓 TWSE 每日「個股融資融券」(MI_MARGN selectType=ALL),用於散戶 vs 法人背離偵測。

    端點：https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&selectType=ALL&date=YYYYMMDD
    回傳：{ code: {"margin_balance": N 張, "margin_change": N 張(今-昨), "date": "YYYY/MM/DD"} }

    解讀(融資 = 散戶看多借錢買):
      - margin_change < 0 + 股價漲 + 法人買 → 散戶丟給法人(經典反轉訊號,加分)
      - margin_change > 0 + 股價跌 → 散戶逆勢加碼,容易斷頭

    失敗回傳 {}。
    """
    today = dt.datetime.now(TPE).date()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json",
    }
    out: dict[str, dict] = {}
    for back in range(1, 8):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y%m%d")
        url = (f"https://www.twse.com.tw/exchangeReport/MI_MARGN"
               f"?response=json&date={date_str}&selectType=ALL")
        try:
            r = requests.get(url, timeout=20, headers=headers)
            r.raise_for_status()
            data = r.json()
            if data.get("stat") != "OK":
                continue
            # MI_MARGN ALL 模式包多張表;個股表通常是 fields/data 結構
            # 找到含「股票代號」+「融資」欄位的那張
            tables = data.get("tables") or []
            stock_table = None
            for t in tables:
                fields = t.get("fields") or []
                fields_str = " ".join(fields) if isinstance(fields, list) else ""
                if (("股票代號" in fields_str or "證券代號" in fields_str)
                        and "融資" in fields_str):
                    stock_table = t
                    break
            if not stock_table and data.get("fields") and data.get("data"):
                # 早期格式:平鋪 fields/data
                stock_table = {"fields": data["fields"], "data": data["data"]}
            if not stock_table:
                continue

            fields: list[str] = stock_table.get("fields", [])
            # 欄位偵測
            def col_idx(*needles: str) -> Optional[int]:
                for i, f in enumerate(fields):
                    if all(n in f for n in needles):
                        return i
                return None

            i_code = col_idx("股票代號") or col_idx("證券代號") or col_idx("代號")
            # 融資餘額(今日):通常欄名「融資今日餘額」或就叫「融資-本日餘額」
            i_bal = (col_idx("融資", "今日餘額") or col_idx("融資", "本日餘額")
                     or col_idx("融資", "今日") or col_idx("融資餘額"))
            # 融資前日餘額
            i_prev = (col_idx("融資", "前日餘額") or col_idx("融資", "昨日餘額")
                      or col_idx("融資", "前日"))

            if i_code is None or i_bal is None:
                continue

            rows = stock_table.get("data") or []
            for row in rows:
                if i_code >= len(row):
                    continue
                code = str(row[i_code]).strip()
                if not (len(code) == 4 and code.isdigit()):
                    continue
                if target_codes is not None and code not in target_codes:
                    continue
                bal = _to_int(row[i_bal]) if i_bal < len(row) else 0
                prev = _to_int(row[i_prev]) if (i_prev is not None and i_prev < len(row)) else 0
                change = bal - prev if prev else 0
                out[code] = {
                    "margin_balance": bal,
                    "margin_change": change,
                    "date": d.strftime("%Y/%m/%d"),
                }
            if out:
                print(f"[margin_stock] {date_str} 取得 {len(out)} 檔個股融資")
                return out
        except Exception as e:
            print(f"[margin_stock] {date_str} 失敗: {e}", file=sys.stderr)
            continue
    print("[margin_stock] 所有日期皆失敗", file=sys.stderr)
    return {}


def calc_tdcc_wow_delta(current_tdcc: dict[str, dict],
                          history: list[dict],
                          min_gap_days: int = 5) -> dict[str, float]:
    """
    從歷史記憶找 ≥ min_gap_days 之前的 TDCC 快照,計算每檔大戶持股 Δ%。

    current_tdcc: { code: {"major_holder_pct": float, ...} }(本次 fetch 結果)
    history:      load_history_state() 回傳清單(舊到新)
    min_gap_days: 最少間隔(避免拿到同一週的)

    回傳 { code: delta_pct }, 其中 delta_pct = 本週 % − 對照週 %。
    沒有對照資料的 code 不會出現在回傳中。
    """
    if not current_tdcc or not history:
        return {}
    today = dt.datetime.now(TPE).date()
    # 從舊到新,找第一個距今 >= min_gap_days 的有 tdcc_snapshot 的紀錄
    target = None
    for h in reversed(history):
        ds = h.get("date") or ""
        try:
            d = dt.datetime.strptime(ds, "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - d).days < min_gap_days:
            continue
        snap = h.get("tdcc_snapshot")
        if snap and isinstance(snap, dict):
            target = snap
            break
    if not target:
        return {}
    deltas: dict[str, float] = {}
    for code, entry in current_tdcc.items():
        cur = entry.get("major_holder_pct")
        old = target.get(code)
        if cur is None or old is None:
            continue
        try:
            deltas[code] = round(float(cur) - float(old), 2)
        except (TypeError, ValueError):
            continue
    print(f"[tdcc_wow] 計算 {len(deltas)} 檔大戶 WoW Δ%(對照 ≥ {min_gap_days} 天前)")
    return deltas


def calc_smart_money_score(entry: dict) -> dict:
    """
    彙整「籌碼悄悄站隊」訊號,給單檔 0-100 分 + 細項。

    輸入 entry 需有以下欄位(由 fetch_tw0050_snapshot 填寫):
      foreign_streak, invest_streak: 連續天數(±)
      tdcc_wow_pct:                  大戶持股週對週 Δ%
      vol_ratio_20d:                 今日量 / 20 日均量
      high20_break, low20_break:     bool(突破/跌破 20 日新高/低)
      day_pct, pct_5d:               價格動能
      foreign_lot, invest_lot:       昨日法人買賣超(張)

    回傳 {"score": int 0-100, "components": {...}, "tag": str, "tags": list[str]}
    """
    if not entry:
        return {"score": 0, "components": {}, "tag": "—", "tags": []}

    f_streak = entry.get("foreign_streak", 0) or 0
    i_streak = entry.get("invest_streak", 0) or 0
    tdcc_wow = entry.get("tdcc_wow_pct")
    vol_ratio = entry.get("vol_ratio_20d")
    high20 = entry.get("high20_break", False)
    low20 = entry.get("low20_break", False)
    day_pct = entry.get("day_pct") or 0
    pct_5d = entry.get("pct_5d")
    foreign_lot = entry.get("foreign_lot") or 0
    invest_lot = entry.get("invest_lot") or 0
    margin_change = entry.get("margin_change_lot")

    # 40 分:法人連買天數(外資 + 投信 加權)
    # 外資連買 3 天 = 30 分, 連買 ≥4 天 = 40 分; 投信加成 ≤ 10 分
    f_score = 0.0
    if f_streak >= 4:
        f_score = 40.0
    elif f_streak == 3:
        f_score = 30.0
    elif f_streak == 2:
        f_score = 18.0
    elif f_streak == 1 and foreign_lot >= 500:    # 單日大買也算分
        f_score = 8.0
    elif f_streak <= -3:
        f_score = -25.0   # 連賣警示
    i_bonus = 0.0
    if i_streak >= 2 and f_streak > 0:
        i_bonus = 10.0   # 投信同向跟風
    elif i_streak <= -2 and f_streak < 0:
        i_bonus = -8.0

    # 30 分:大戶持股 Δ%(WoW)
    tdcc_score = 0.0
    if tdcc_wow is not None:
        # +0.5% = 15 分, +1.0% = 30 分; 負值最多扣 15 分
        if tdcc_wow >= 0.5:
            tdcc_score = min(30.0, 15.0 + (tdcc_wow - 0.5) * 30.0)
        elif tdcc_wow > 0:
            tdcc_score = tdcc_wow * 30.0
        elif tdcc_wow < -0.3:
            tdcc_score = max(-15.0, tdcc_wow * 20.0)

    # 20 分:量縮 + 收紅 = 籌碼鎖定(經典偷買訊號);量暴增 + 收紅 + 法人賣 = 警示扣分
    vol_score = 0.0
    if vol_ratio is not None:
        if vol_ratio < 0.8 and day_pct >= 0:
            vol_score = 20.0     # 量縮收紅
        elif vol_ratio < 0.9 and day_pct >= -0.5:
            vol_score = 12.0
        elif vol_ratio > 2.0 and day_pct >= 0 and foreign_lot < -500:
            vol_score = -15.0    # 暴量收紅 + 法人賣 = 散戶接刀
        elif vol_ratio > 1.5 and high20:
            vol_score = 8.0      # 放量突破

    # 10 分:5 日漲幅「偷買區間」(-2% ~ +3%) — 偷的本質是股價沒大動
    quiet_score = 0.0
    if pct_5d is not None:
        if -2.0 <= pct_5d <= 3.0:
            quiet_score = 10.0
        elif 3.0 < pct_5d <= 5.0:
            quiet_score = 6.0
        elif pct_5d > 10.0:
            quiet_score = -8.0    # 過熱反扣

    # 額外:融資減少 + 股價穩(散戶丟給法人)
    margin_score = 0.0
    if margin_change is not None and day_pct >= -0.5:
        if margin_change <= -200:
            margin_score = 5.0

    # 突破 20 日新高(放量 + 法人買) → 多頭續攻訊號(中性,不入主分,只給標籤)
    raw_score = (f_score + i_bonus + tdcc_score + vol_score + quiet_score
                 + margin_score)
    score = max(0, min(100, int(round(raw_score))))

    # 推導語意標籤
    tags: list[str] = []
    if f_streak >= 3:
        tags.append(f"外資連{f_streak}買")
    elif f_streak <= -3:
        tags.append(f"外資連{abs(f_streak)}賣")
    if i_streak >= 2:
        tags.append(f"投信連{i_streak}買")
    if tdcc_wow is not None and tdcc_wow >= 0.3:
        tags.append(f"大戶+{tdcc_wow:.2f}%")
    if vol_ratio is not None and vol_ratio < 0.8 and day_pct >= 0:
        tags.append("量縮收紅")
    if high20 and (foreign_lot > 0 or i_streak > 0):
        tags.append("突破20日高+法人買")
    if low20 and foreign_lot < 0:
        tags.append("跌破20日低+外資賣")
    if margin_change is not None and margin_change <= -200 and day_pct >= -0.5:
        tags.append("融資減散戶賣")

    # 整體標籤
    if score >= 80:
        tag = "強力偷買訊號"
    elif score >= 60:
        tag = "悄悄站隊"
    elif score >= 40:
        tag = "輕微正向"
    elif score <= -20 or f_score <= -25:
        tag = "籌碼鬆動警示"
    else:
        tag = "中性"

    return {
        "score": score,
        "components": {
            "foreign_streak_score": round(f_score, 1),
            "invest_bonus": round(i_bonus, 1),
            "tdcc_wow_score": round(tdcc_score, 1),
            "volume_score": round(vol_score, 1),
            "quiet_score": round(quiet_score, 1),
            "margin_score": round(margin_score, 1),
        },
        "tag": tag,
        "tags": tags,
    }


def _fallback_universe() -> dict[str, dict]:
    """動態 universe 抓取失敗時的退化清單：用硬編的 TW0050_CONSTITUENTS。"""
    return {
        code: {
            "name": desc.split(" — ")[0],
            "industry": "",
            "market_cap": None,
            "fallback": True,
        }
        for code, desc in TW0050_CONSTITUENTS.items()
    }


def fetch_tw_top100_universe(top_n: int = 100) -> dict[str, dict]:
    """
    動態抓「台股市值前 N 大」universe（上市）。

    用兩支 TWSE OpenAPI（免費、無需 API key、各一次請求）：
      - opendata/t187ap03_L     上市公司基本資料 → 代號 / 簡稱 / 產業別 / 已發行股數
      - exchangeReport/STOCK_DAY_ALL  上市個股日成交 → 收盤價
    市值 = 已發行普通股數 × 收盤價，排序取前 N。

    任何環節失敗 → fallback 回硬編 TW0050_CONSTITUENTS（每筆帶 "fallback": True）。
    回傳：{ code: {"name", "industry", "market_cap", ["fallback"]} }
    """
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        r1 = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
                          timeout=20, headers=headers)
        r1.raise_for_status()
        basics = r1.json() or []
        r2 = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                          timeout=20, headers=headers)
        r2.raise_for_status()
        prices = r2.json() or []
        if not basics or not prices:
            raise RuntimeError("OpenAPI 回傳空資料")

        # 自動偵測欄位名（TWSE 偶爾微調欄位字串）
        bk = list(basics[0].keys())
        code_k = next((k for k in bk if "公司代號" in k or k.strip() == "代號"), None)
        name_k = (next((k for k in bk if "簡稱" in k), None)
                  or next((k for k in bk if "公司名稱" in k or "名稱" in k), None))
        ind_k = next((k for k in bk if "產業別" in k), None)
        share_k = next((k for k in bk if "發行" in k and "股數" in k), None)

        pk = list(prices[0].keys())
        pcode_k = next((k for k in pk if k == "Code" or "證券代號" in k or "公司代號" in k
                        or k.strip() == "代號"), None)
        close_k = next((k for k in pk if "clos" in k.lower() or "收盤" in k), None)

        if not all([code_k, share_k, pcode_k, close_k]):
            raise RuntimeError(f"OpenAPI 欄位偵測失敗 basics={bk} prices={pk}")

        price_map: dict[str, float] = {}
        for row in prices:
            c = str(row.get(pcode_k, "")).strip()
            cp = _to_float(row.get(close_k))
            if c and cp:
                price_map[c] = cp

        rows: list[dict] = []
        for row in basics:
            c = str(row.get(code_k, "")).strip()
            if not (len(c) == 4 and c.isdigit()):   # 只取 4 位數字的普通股代號
                continue
            shares = _to_int(row.get(share_k))
            close = price_map.get(c)
            if not shares or not close:
                continue
            rows.append({
                "code": c,
                "name": (str(row.get(name_k, "")).strip() or c) if name_k else c,
                "industry": (str(row.get(ind_k, "")).strip() if ind_k else ""),
                "market_cap": shares * close,
            })

        rows.sort(key=lambda x: x["market_cap"], reverse=True)
        top = rows[:top_n]
        # 健康檢查：有效資料遠少於預期 → 視為抓取異常，走 fallback
        if len(top) < min(30, top_n):
            raise RuntimeError(f"有效市值資料僅 {len(top)} 檔")

        universe = {
            r["code"]: {"name": r["name"], "industry": r["industry"],
                        "market_cap": r["market_cap"]}
            for r in top
        }
        print(f"[universe] 動態取得市值前 {len(universe)} 大"
              f"（最大：{top[0]['code']} {top[0]['name']}）")
        return universe
    except Exception as e:
        print(f"[universe] 動態抓取失敗，fallback 回 0050 硬編清單: {e}", file=sys.stderr)
        return _fallback_universe()


def fetch_tw_monthly_revenue() -> dict[str, dict]:
    """
    抓上市公司「每月營業收入」（TWSE OpenAPI t187ap05_L，免費無 key，一次請求全市場）。
    這是台股個股最即時、最硬的基本面數據——讓 LLM 選股有真實營收成長率佐證，
    不再只靠先驗知識。
    回傳：{ code: {"month", "rev", "mom_pct", "yoy_pct", "cum_yoy_pct"} }
    失敗回傳 {}（不影響晨報其他區塊）。
    """
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
                         timeout=20,
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        r.raise_for_status()
        data = r.json() or []
        if not data:
            return {}
        keys = list(data[0].keys())
        code_k = next((k for k in keys if "公司代號" in k or k.strip() == "代號"), None)
        month_k = next((k for k in keys if "資料年月" in k), None)
        yoy_k = next((k for k in keys if "去年同月增減" in k), None)
        mom_k = next((k for k in keys if "上月比較增減" in k), None)
        rev_k = next((k for k in keys if "當月營收" in k and "累計" not in k), None)
        cumyoy_k = next((k for k in keys if "前期比較增減" in k), None)
        if not code_k:
            print(f"[revenue] 欄位偵測失敗 keys={keys}", file=sys.stderr)
            return {}
        out: dict[str, dict] = {}
        for row in data:
            c = str(row.get(code_k, "")).strip()
            if not (len(c) == 4 and c.isdigit()):
                continue
            out[c] = {
                "month": (str(row.get(month_k, "")).strip() if month_k else ""),
                "rev": _to_int(row.get(rev_k)) if rev_k else None,
                "mom_pct": _to_float(row.get(mom_k)) if mom_k else None,
                "yoy_pct": _to_float(row.get(yoy_k)) if yoy_k else None,
                "cum_yoy_pct": _to_float(row.get(cumyoy_k)) if cumyoy_k else None,
            }
        print(f"[revenue] 取得 {len(out)} 檔上市公司月營收")
        return out
    except Exception as e:
        print(f"[revenue] 抓取失敗: {e}", file=sys.stderr)
        return {}


def fetch_tdcc_major_holders(target_codes: Optional[set] = None) -> dict[str, dict]:
    """
    抓「集保戶股權分散表」各檔的大戶持股比例（TDCC 集保結算所開放資料，免費無 key）。
    大戶定義：持股 ≥ 400 張（分級 12-15）；比例越高代表籌碼越集中在大戶/主力手上。
    資料每週更新（約週五），是「主力進出」最穩定的免費官方來源。
    回傳：{ code: {"major_holder_pct": float, "date": str} }
    失敗回傳 {}（不影響晨報其他區塊）。
    """
    import csv
    import re as _re
    from io import StringIO
    try:
        r = requests.get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5",
                         timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        try:
            text = r.content.decode("utf-8")
        except UnicodeDecodeError:
            text = r.content.decode("big5", errors="replace")
        rows = list(csv.reader(StringIO(text)))
        if len(rows) < 2:
            return {}
        header = [h.strip() for h in rows[0]]

        def _col(*needles: str) -> Optional[int]:
            for i, h in enumerate(header):
                if any(n in h for n in needles):
                    return i
            return None

        date_i = _col("資料日期")
        code_i = _col("證券代號", "代號")
        level_i = _col("分級", "持股")
        pct_i = _col("比例", "占")
        if code_i is None or level_i is None or pct_i is None:
            print(f"[tdcc] 欄位偵測失敗 header={header}", file=sys.stderr)
            return {}

        out: dict[str, dict] = {}
        idx_max = max(code_i, level_i, pct_i)
        for row in rows[1:]:
            if len(row) <= idx_max:
                continue
            code = str(row[code_i]).strip()
            if target_codes is not None and code not in target_codes:
                continue
            m = _re.match(r"\s*(\d+)", str(row[level_i]))
            if not m:
                continue
            level = int(m.group(1))
            if not (12 <= level <= 15):   # 12-15 ＝ 持股 ≥ 400 張（大戶）
                continue
            pct = _to_float(row[pct_i])
            if pct is None:
                continue
            entry = out.setdefault(code, {"major_holder_pct": 0.0, "date": ""})
            entry["major_holder_pct"] += pct
            if date_i is not None and date_i < len(row):
                entry["date"] = str(row[date_i]).strip()
        for v in out.values():
            v["major_holder_pct"] = round(v["major_holder_pct"], 2)
        print(f"[tdcc] 取得 {len(out)} 檔大戶持股比例")
        return out
    except Exception as e:
        print(f"[tdcc] 抓取失敗: {e}", file=sys.stderr)
        return {}


def fetch_twse_close(code: str) -> Optional[float]:
    """
    從 TWSE OpenAPI STOCK_DAY_ALL 抓單一上市標的（含 ETF）的最新「官方」收盤價。

    為什麼需要：Yahoo Finance 對台股 ETF（如 00662 富邦 NASDAQ）的資料常落後一天
    或卡價不動，導致「昨收」抓到舊值、連帶汙染合理價估值與回歸 beta。
    TWSE 是台股/台股 ETF 的權威來源。失敗回傳 None（由呼叫端 fallback）。
    """
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                         timeout=20,
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        r.raise_for_status()
        data = r.json() or []
        if not data:
            return None
        keys = list(data[0].keys())
        code_k = next((k for k in keys if k == "Code" or "證券代號" in k
                       or "公司代號" in k or k.strip() == "代號"), None)
        close_k = next((k for k in keys if "clos" in k.lower() or "收盤" in k), None)
        if not code_k or not close_k:
            print(f"[twse_close] 欄位偵測失敗 keys={keys}", file=sys.stderr)
            return None
        for row in data:
            if str(row.get(code_k, "")).strip() == code:
                close = _to_float(row.get(close_k))
                if close:
                    return round(close, 2)
        print(f"[twse_close] STOCK_DAY_ALL 中找不到 {code}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[twse_close] {code} 抓取失敗: {e}", file=sys.stderr)
        return None


def fetch_twse_taiex_close() -> Optional[float]:
    """
    從 TWSE 官方抓「加權指數」(TAIEX) 最新收盤。

    為什麼需要：Yahoo Finance 的 ^TWII 偶爾會給錯值（曾誤報 40020 而非 41368，
    差 ~3.3%），整個加權指數預測、區間、自我校正 bias 都會被汙染。
    TWSE 是台股指數的權威來源。

    嘗試順序：
      1. FMTQIK（大盤每日成交資訊，含 TAIEX 收盤點數）
      2. MI_INDEX（每日收盤行情指數類）— fallback
    失敗回 None（呼叫端 fallback 回 Yahoo）。
    """
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    # 嘗試 1: FMTQIK
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK",
                         timeout=20, headers=headers)
        r.raise_for_status()
        data = r.json() or []
        if data:
            # FMTQIK 通常依日期 asc 排序，最後一筆 = 最新日。欄位含「發行量加權股價指數」
            latest = data[-1]
            for k in ("發行量加權股價指數", "TAIEX", "加權股價指數", "Closing_TAIEX"):
                v = _to_float(latest.get(k))
                if v and v > 1000:    # TAIEX 點數 > 1000 為合理區間
                    print(f"[twse_taiex] FMTQIK → {v:,.2f}")
                    return round(v, 2)
    except Exception as e:
        print(f"[twse_taiex] FMTQIK 失敗: {e}", file=sys.stderr)

    # 嘗試 2: MI_INDEX
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX",
                         timeout=20, headers=headers)
        r.raise_for_status()
        data = r.json() or []
        for row in data:
            name = str(row.get("指數") or row.get("Name") or "").strip()
            if "發行量加權股價指數" in name or name == "加權股價指數" or "TAIEX" in name.upper():
                for k in ("收盤指數", "ClosingIndex", "Close"):
                    v = _to_float(row.get(k))
                    if v and v > 1000:
                        print(f"[twse_taiex] MI_INDEX → {v:,.2f}")
                        return round(v, 2)
    except Exception as e:
        print(f"[twse_taiex] MI_INDEX 失敗: {e}", file=sys.stderr)

    print("[twse_taiex] TWSE 官方來源全失敗，將沿用 yfinance ^TWII", file=sys.stderr)
    return None


def fetch_twse_market_breadth() -> dict:
    """
    從 TWSE STOCK_DAY_ALL 計算「大盤量能 + 市場廣度」。

    回傳：
      total_value_yi: 大盤成交金額（億元，新台幣）
      total_value_raw: 成交金額（元）
      advance: 上漲家數
      decline: 下跌家數
      unchanged: 平盤家數
      total: 有效成交檔數
      advance_ratio: 上漲家數佔比（%）
      breadth_state: 'broad_rally' | 'broad_decline' | 'narrow' | 'neutral'

    失敗回 {} 不影響晨報。
    """
    try:
        r = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                         timeout=20,
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        r.raise_for_status()
        data = r.json() or []
        if not data:
            return {}

        keys = list(data[0].keys())
        # STOCK_DAY_ALL 欄位（自動偵測,以防 TWSE 改格式）
        # 重要：「ClosingPrice」「Change」「TradeValue」「Code」
        change_k = next((k for k in keys if k.lower() in ("change", "change_value") or k == "漲跌"), None)
        if change_k is None:
            # 退化：找含 change/漲跌 的欄位（不可含 "change_pct"，避免抓到百分比）
            change_k = next((k for k in keys if ("change" in k.lower() and "pct" not in k.lower())
                             or "漲跌" in k), None)
        value_k = next((k for k in keys if "tradevalue" in k.lower() or k in ("TradeValue", "成交金額")), None)
        code_k = next((k for k in keys if k == "Code" or "證券代號" in k or "代號" in k), None)
        if not change_k or not value_k or not code_k:
            print(f"[breadth] STOCK_DAY_ALL 欄位偵測失敗 keys={keys}", file=sys.stderr)
            return {}

        adv = dec = unch = 0
        total_value = 0.0
        n_total = 0
        for row in data:
            code = str(row.get(code_k, "")).strip()
            # 只算 4 位數正常上市股票，排除 5+ 位 ETF/權證
            if not (len(code) == 4 and code.isdigit()):
                continue
            ch = _to_float(row.get(change_k))
            tv = _to_float(row.get(value_k))
            if ch is None:
                continue
            n_total += 1
            if ch > 0:
                adv += 1
            elif ch < 0:
                dec += 1
            else:
                unch += 1
            if tv:
                total_value += tv

        if n_total == 0:
            return {}

        advance_ratio = adv / n_total * 100
        if advance_ratio >= 60:
            state = "broad_rally"
        elif advance_ratio <= 40:
            state = "broad_decline"
        elif 45 <= advance_ratio <= 55:
            state = "neutral"
        else:
            state = "narrow"

        out = {
            "total_value_raw": total_value,
            "total_value_yi": round(total_value / 1e8, 0),       # 億元
            "advance": adv,
            "decline": dec,
            "unchanged": unch,
            "total": n_total,
            "advance_ratio": round(advance_ratio, 1),
            "breadth_state": state,
        }
        print(f"[breadth] 大盤成交額 {out['total_value_yi']:,.0f} 億，"
              f"上漲 {adv}/{n_total} ({advance_ratio:.1f}%) → {state}")
        return out
    except Exception as e:
        print(f"[breadth] 抓取失敗: {e}", file=sys.stderr)
        return {}


def fetch_tw0050_snapshot(universe: Optional[dict] = None,
                            tdcc_wow_map: Optional[dict[str, float]] = None,
                            margin_per_stock: Optional[dict[str, dict]] = None,
                            ) -> list[dict]:
    """
    批次抓台股 universe（預設市值前 100 大）近期表現 + 籌碼悄悄站隊訊號。

    每檔回傳:代號 / 名稱 / 昨收 / 漲跌幅 / 5日均量比 / 月漲跌幅 / 法人合計買賣超 /
            30日累積法人 / 月營收年增率 / 大戶持股 / 5日動能 / 距 MA20 /
            **新增**:foreign_streak / invest_streak / vol_ratio_20d /
                     high20_break / low20_break / tdcc_wow_pct /
                     margin_change_lot / smart_money(分數 + 標籤)

    universe 由 fetch_tw_top100_universe() 提供；未傳則退化為硬編 0050 清單。
    tdcc_wow_map / margin_per_stock 若 None 則退化為「無資料」(分數計算時自動跳過)。
    """
    if universe is None:
        universe = _fallback_universe()
    if tdcc_wow_map is None:
        tdcc_wow_map = {}
    if margin_per_stock is None:
        margin_per_stock = {}

    inst = fetch_twse_institutional()
    # 三大法人單日 API 一次回傳全市場，30 日累積只是 client 端篩選，universe 變大不增加請求數
    target_codes = set(universe.keys())
    inst_30d = fetch_twse_institutional_cumulative(
        days_back=30, target_codes=target_codes, keep_recent_days=5)
    revenue = fetch_tw_monthly_revenue()              # 月營收（一次請求全市場）
    tdcc = fetch_tdcc_major_holders(target_codes)     # 大戶持股比例（一次請求全市場）
    snapshot: list[dict] = []
    codes = list(universe.keys())

    # yfinance 批次下載 (每檔加 .TW) — 100 檔仍是「一次」request
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
            # 20 日均量比(更可靠的「異常量能」訊號;5 日窗對短期波動敏感)
            avg20_vol = sub["Volume"].tail(20).mean() if len(sub) >= 5 else None
            vol_ratio_20d = (vol / avg20_vol) if avg20_vol and avg20_vol > 0 else None

            # 突破 / 跌破 20 日高 / 低(法人連買 + 突破 = 多頭續攻)
            high20_break = False
            low20_break = False
            if len(sub) >= 20:
                high20 = float(sub["Close"].tail(20).iloc[:-1].max()) if len(sub) > 20 else float(sub["Close"].head(19).max())
                low20 = float(sub["Close"].tail(20).iloc[:-1].min()) if len(sub) > 20 else float(sub["Close"].head(19).min())
                if close > high20:
                    high20_break = True
                if close < low20:
                    low20_break = True

            month_first = safe_float(sub.iloc[0]["Close"])
            month_pct = (close - month_first) / month_first * 100 if month_first else 0

            # 5 日累積動能 + 20日MA 位置（看「結構是否健康」,避免追過熱)
            pct_5d = None
            ma20_dist_pct = None
            if len(sub) >= 6:
                prev5 = safe_float(sub.iloc[-6]["Close"])
                if prev5 and prev5 > 0:
                    pct_5d = (close - prev5) / prev5 * 100
            if len(sub) >= 20:
                ma20 = float(sub["Close"].tail(20).mean())
                if ma20 > 0:
                    ma20_dist_pct = (close / ma20 - 1) * 100

            inst_data = inst.get(code, {})
            inst_30 = inst_30d.get(code, {})
            rev = revenue.get(code, {})
            tdcc_data = tdcc.get(code, {})
            info = universe[code]
            # 籌碼悄悄站隊原料:法人連買天數、大戶 WoW、個股融資變化
            streaks = _calc_inst_streaks(inst_30.get("daily") or [])
            tdcc_wow = tdcc_wow_map.get(code)
            margin_data = margin_per_stock.get(code) or {}
            # 業務簡介：優先用硬編的詳細版，否則退而用 OpenAPI 的產業別
            desc = TW0050_CONSTITUENTS.get(code) or (
                f"{info['name']} — {info.get('industry') or '（產業別未知）'}")

            entry = {
                "code": code,
                "name": info["name"],
                "desc": desc,
                "industry": info.get("industry", ""),
                "market_cap": info.get("market_cap"),
                "close": round(close, 2),
                "day_pct": round(day_pct, 2),
                "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
                "vol_ratio_20d": round(vol_ratio_20d, 2) if vol_ratio_20d else None,
                "high20_break": bool(high20_break),
                "low20_break": bool(low20_break),
                "month_pct": round(month_pct, 2),
                # 新增:5日累積動能 + 距 MA20(看是否過熱)
                "pct_5d": round(pct_5d, 2) if pct_5d is not None else None,
                "ma20_dist_pct": round(ma20_dist_pct, 2) if ma20_dist_pct is not None else None,
                "foreign_lot": round(inst_data.get("foreign", 0) / 1000, 1),
                "invest_lot": round(inst_data.get("investment", 0) / 1000, 1),
                "dealer_lot": round(inst_data.get("dealer", 0) / 1000, 1),
                "total_lot": round(inst_data.get("total", 0) / 1000, 1),
                # 30 日累積（張）— 看中期籌碼方向
                "foreign_30d_lot": round(inst_30.get("foreign_cum", 0) / 1000, 0),
                "invest_30d_lot":  round(inst_30.get("invest_cum", 0) / 1000, 0),
                "dealer_30d_lot":  round(inst_30.get("dealer_cum", 0) / 1000, 0),
                "inst_30d_days":   inst_30.get("days", 0),
                # 法人連買 / 連賣天數(±, 由近 5 日逐日序列推得)
                "foreign_streak": streaks["foreign_streak"],
                "invest_streak":  streaks["invest_streak"],
                # 大戶持股 WoW Δ%(本週 − 對照週,需有歷史快照才有值)
                "tdcc_wow_pct": tdcc_wow,
                # 個股融資餘額變化(張),負值 = 散戶融資減,通常是散戶丟給法人
                "margin_balance_lot": round((margin_data.get("margin_balance") or 0) / 1000, 0),
                "margin_change_lot": round((margin_data.get("margin_change") or 0) / 1000, 0)
                                        if margin_data.get("margin_change") is not None else None,
                # 月營收基本面
                "rev_month":   rev.get("month"),
                "rev_yoy_pct": rev.get("yoy_pct"),
                "rev_mom_pct": rev.get("mom_pct"),
                # 大戶持股比例（TDCC 集保，≥400 張，週更）
                "major_holder_pct": tdcc_data.get("major_holder_pct"),
            }
            # 籌碼悄悄站隊分數:綜合「外資連買 + 大戶 WoW + 量縮收紅 + 偷買區」
            entry["smart_money"] = calc_smart_money_score(entry)
            snapshot.append(entry)
        except (KeyError, ValueError, TypeError) as e:
            print(f"[snapshot] {code} 跳過: {e}", file=sys.stderr)
            continue

    print(f"[snapshot] 台股 universe 完成 {len(snapshot)} / {len(codes)} 檔")
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


def build_historical_calibration(hist_2330: Optional[pd.DataFrame], days: int = 7) -> str:
    """
    建立「過去 N 日 TSM 漲跌 → 2330 隔日開盤實際漲跌」對照表。
    讓 LLM 看真實的「ADR 預測 vs 台股實際」誤差，作為今日預測的校準錨點。
    """
    if hist_2330 is None or len(hist_2330) < days + 2:
        return "（歷史資料不足，無法生成校準表）"
    try:
        tsm_hist = yf.Ticker("TSM").history(period="2mo", auto_adjust=False)
        tsm_hist = tsm_hist.dropna(subset=["Close"])
        tsm_hist = tsm_hist[tsm_hist["Close"] > 0]
        if len(tsm_hist) < days + 2:
            return "（TSM 歷史資料不足）"

        # 對齊：TSM 第 T 日漲跌 vs 2330 第 T+1 日開盤漲跌
        # 因 TSM 與 2330 時區不同，先做近似對齊（用日期）
        tsm_d = tsm_hist["Close"].dropna()
        tw_open = hist_2330["Open"].dropna()
        tw_close_prev = hist_2330["Close"].shift(1).dropna()

        tsm_d.index = tsm_d.index.tz_localize(None) if tsm_d.index.tz else tsm_d.index
        tw_open.index = tw_open.index.tz_localize(None) if tw_open.index.tz else tw_open.index
        tw_close_prev.index = tw_close_prev.index.tz_localize(None) if tw_close_prev.index.tz else tw_close_prev.index

        # 取最近 N 個交易日的對照
        rows = []
        recent_dates = sorted(hist_2330.index)[-(days + 2):]
        for i in range(1, min(days + 1, len(recent_dates))):
            d_today = recent_dates[-i]
            d_today_naive = d_today.tz_localize(None) if d_today.tz else d_today

            # TSM 前一交易日（美股盤後對應台股當日開盤）
            tsm_lookup = tsm_d[tsm_d.index < d_today_naive]
            if len(tsm_lookup) < 2:
                continue
            tsm_today = float(tsm_lookup.iloc[-1])
            tsm_prev = float(tsm_lookup.iloc[-2])
            tsm_pct = (tsm_today - tsm_prev) / tsm_prev * 100

            # 2330 開盤 vs 前一日收盤
            if d_today_naive not in tw_open.index:
                continue
            tw_o = float(tw_open.loc[d_today_naive])
            cl_lookup = tw_close_prev[tw_close_prev.index <= d_today_naive]
            if cl_lookup.empty:
                continue
            tw_pc = float(cl_lookup.iloc[-1])
            tw_open_pct = (tw_o - tw_pc) / tw_pc * 100

            implied = tw_open_pct - tsm_pct  # 差值（2330 開盤實際 vs ADR 預期）
            rows.append({
                "date": d_today_naive.strftime("%m/%d"),
                "tsm_pct": tsm_pct,
                "tw_open_pct": tw_open_pct,
                "delta": implied,
            })

        if not rows:
            return "（無有效對照資料）"

        # 計算平均偏離（含絕對值平均，反映誤差大小）
        avg_delta = sum(r["delta"] for r in rows) / len(rows)
        avg_abs = sum(abs(r["delta"]) for r in rows) / len(rows)

        rows_str = "\n".join(
            f"  {r['date']}：TSM 收盤 {r['tsm_pct']:+.2f}% → 2330 開盤 {r['tw_open_pct']:+.2f}%（偏離 {r['delta']:+.2f}%）"
            for r in rows
        )
        return (f"近 {len(rows)} 個交易日 TSM 漲跌 vs 2330 開盤對照（驗證 ADR 預測準確度）：\n"
                f"{rows_str}\n"
                f"平均偏離 = {avg_delta:+.2f}% （正值 = 2330 開盤通常比 ADR 暗示偏高）\n"
                f"平均絕對偏離 = {avg_abs:.2f}% （此為預測誤差參考）")
    except Exception as e:
        return f"（對照表生成失敗: {e}）"


def calc_00662_fair_value(qqq_close: float, qqq_prev_close: float,
                           usdtwd: float, last_00662_price: Optional[float],
                           usdtwd_prev: Optional[float] = None) -> dict:
    """
    精準版 00662 公允淨值與合理價估算（V2 — 不依賴外部 NAV API）。

    新策略：用「歷史回歸 + 即時資料」三合一估算
    1. QQQ 漲跌幅 → 主因子
    2. 匯率變動 → 修正因子（USD 升 → 00662 台幣價上升）
    3. 從 yfinance 抓 QQQ 與 00662 近 60 個交易日對照，
       計算 00662 對 QQQ 的「實證 beta」與「平均偏離率」
    4. 修正後：fair_price = last_00662 × (1 + QQQ% × beta + FX%) × (1 + 平均偏離)

    這方法比抓 NAV API 更穩（不依賴第三方）且更精準（用真實對照資料）。
    """
    qqq_pct = (qqq_close - qqq_prev_close) / qqq_prev_close
    if last_00662_price is None:
        return {"error": "缺 00662 昨收"}

    # 匯率變動因子（昨 → 今）
    fx_pct = 0.0
    if usdtwd and usdtwd_prev:
        fx_pct = (usdtwd - usdtwd_prev) / usdtwd_prev

    # 用 yfinance 算 QQQ vs 00662 的歷史 beta 與偏離
    beta = 1.0          # 預設
    avg_deviation = 0.0 # 預設
    samples = 0
    premium_pct: Optional[float] = None    # 折溢價（vs NDX 隱含 NAV 的 60 日中位數）
    try:
        qqq_hist = yf.Ticker("QQQ").history(period="3mo", auto_adjust=False)
        tw_hist  = yf.Ticker("00662.TW").history(period="3mo", auto_adjust=False)
        fx_hist  = yf.Ticker("TWD=X").history(period="3mo", auto_adjust=False)

        qqq_s = qqq_hist["Close"].dropna()
        tw_s  = tw_hist["Close"].dropna()
        fx_s  = fx_hist["Close"].dropna()
        qqq_s.index = qqq_s.index.tz_localize(None) if qqq_s.index.tz else qqq_s.index
        tw_s.index  = tw_s.index.tz_localize(None)  if tw_s.index.tz  else tw_s.index
        fx_s.index  = fx_s.index.tz_localize(None)  if fx_s.index.tz  else fx_s.index

        # 計算 00662 隔日漲跌（台股對應前一夜美股）
        df = pd.DataFrame({
            "qqq_lag": qqq_s.shift(1),     # 前一交易日 QQQ 收盤（美股盤後 → 隔日台股開盤反應）
            "qqq_lag_pct": qqq_s.shift(1).pct_change(),
            "tw": tw_s,
            "tw_pct": tw_s.pct_change(),
            "fx_lag_pct": fx_s.shift(1).pct_change(),
        }).dropna()

        # 取 |QQQ 變動 > 0.3%| 的樣本（有意義的訊號）
        sig = df[df["qqq_lag_pct"].abs() > 0.003].tail(60)
        if len(sig) >= 15:
            # beta = avg(00662 變動 / QQQ 變動)
            ratios = sig["tw_pct"] / sig["qqq_lag_pct"]
            ratios = ratios[(ratios > -2) & (ratios < 3)]  # 過濾異常值
            beta = float(ratios.median())
            beta = max(0.5, min(beta, 1.5))   # 限制合理區間

            # 00662 追蹤 NASDAQ-100，對 QQQ 的 beta 在設計上必 ≈ 1。
            # 若回歸算出明顯偏離 0.85-1.15，代表來源資料異常（多半是 Yahoo 的
            # 00662.TW 歷史漏更新/卡價）→ 放棄回歸，退回 beta=1 簡化版。
            if 0.85 <= beta <= 1.15:
                # 偏離 = 實際 00662 變動 − (QQQ 變動 × beta + 匯率變動)
                sig_full = sig.copy()
                sig_full["predicted"] = sig_full["qqq_lag_pct"] * beta + sig_full["fx_lag_pct"]
                sig_full["deviation"] = sig_full["tw_pct"] - sig_full["predicted"]
                avg_deviation = float(sig_full["deviation"].median())
                samples = len(sig)
                print(f"[00662] 實證 beta={beta:.3f}, avg_deviation={avg_deviation*100:.3f}%, samples={samples}")
            else:
                print(f"[00662] 回歸 beta={beta:.3f} 偏離 0.85-1.15，研判 00662 歷史資料異常 → 退回簡化版",
                      file=sys.stderr)
                beta = 1.0   # samples 維持 0 → 下方走簡化版

        # 折溢價：00662 vs NDX 隱含 NAV (= QQQ × USD/TWD)
        # 取 60 日 (00662 / (QQQ_lag × FX)) 比值的中位數作為「公允比值」
        # 今日比值 = last_00662 / (qqq_prev_close × usdtwd_prev) — 對齊的是「驅動 last_00662 的 US 收盤」
        try:
            df_pp = pd.DataFrame({
                "tw": tw_s, "qqq_lag": qqq_s.shift(1), "fx": fx_s,
            }).dropna()
            df_pp = df_pp[(df_pp["qqq_lag"] > 0) & (df_pp["fx"] > 0)]
            if len(df_pp) >= 20:
                df_pp["ratio"] = df_pp["tw"] / (df_pp["qqq_lag"] * df_pp["fx"])
                median_ratio = float(df_pp["ratio"].tail(60).median())
                ref_fx = usdtwd_prev or usdtwd
                if median_ratio and ref_fx and qqq_prev_close:
                    implied_nav = qqq_prev_close * ref_fx * median_ratio
                    if implied_nav > 0:
                        premium_pct = (last_00662_price / implied_nav - 1) * 100
                        print(f"[00662] 折溢價 = {premium_pct:+.2f}% (n={len(df_pp)}, median_ratio={median_ratio:.6f})")
        except Exception as e:
            print(f"[00662] 折溢價計算失敗: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[00662] 歷史回歸失敗: {e}", file=sys.stderr)

    # 計算合理價
    if samples >= 15:
        # 精準版：實證 beta + 偏離修正
        adjusted_pct = qqq_pct * beta + fx_pct + avg_deviation
        fair_price = last_00662_price * (1 + adjusted_pct)
        method = f"歷史回歸 (beta={beta:.2f}, 修正={avg_deviation*100:+.2f}%, n={samples})"
    else:
        # 退化版：beta=1，無偏離修正
        adjusted_pct = qqq_pct + fx_pct
        fair_price = last_00662_price * (1 + adjusted_pct)
        method = "簡化版（歷史資料不足）"

    return {
        "qqq_pct": round(qqq_pct * 100, 2),
        "fx_pct": round(fx_pct * 100, 3),
        "last_00662_price": last_00662_price,
        "beta": round(beta, 3),
        "avg_deviation_pct": round(avg_deviation * 100, 3),
        "samples": samples,
        "fair_price": round(fair_price, 2),
        "implied_change_pct": round((fair_price / last_00662_price - 1) * 100, 2),
        "usdtwd": usdtwd,
        "usdtwd_prev": usdtwd_prev,
        "method": method,
        # 折溢價（vs NDX 隱含 NAV）：正=溢價（市價>合理NAV）；負=折價；None=資料不足
        "premium_pct": round(premium_pct, 3) if premium_pct is not None else None,
    }


def fetch_taiex_history() -> Optional[pd.DataFrame]:
    """抓加權指數 (^TWII) 過去 3 個月歷史，供大盤預測用。"""
    for attempt in range(3):
        try:
            d = yf.Ticker("^TWII").history(period="3mo", auto_adjust=False)
            d = d.dropna(subset=["Close"])
            d = d[d["Close"] > 0]
            if not d.empty:
                return d
        except Exception as e:
            print(f"[taiex] attempt {attempt+1} 失敗: {e}", file=sys.stderr)
        time.sleep(2)
    return None


def calc_taiex_prediction(taiex_hist: Optional[pd.DataFrame],
                            sox_pct: Optional[float],
                            tsm_pct: Optional[float],
                            night_pct: Optional[float]) -> dict:
    """
    Task A: 加權指數開盤預測（三訊號加權法）

    加權邏輯：
      SOX 漲跌幅 × β=1.05 × 40%（半導體與台股加權連動）
      TSM ADR 漲跌幅 × 30%（台積電佔加權 ~28-32%）
      夜盤台指期漲跌幅 × 30%（最直接領先指標）

    若任一訊號缺失，自動 reweight 剩下的權重。
    """
    if taiex_hist is None or taiex_hist.empty:
        return {"error": "缺加權指數歷史"}

    last_close = safe_float(taiex_hist.iloc[-1]["Close"])
    if not last_close:
        return {"error": "加權指數收盤無效"}

    # 收集有效訊號
    signals = []
    if sox_pct is not None:
        signals.append(("SOX", sox_pct * 1.05, 0.40))
    if tsm_pct is not None:
        signals.append(("TSM_ADR", tsm_pct, 0.30))
    if night_pct is not None:
        signals.append(("Night_TXF", night_pct, 0.30))

    if not signals:
        return {"error": "三大訊號全缺，無法預測"}

    # Reweight: 缺資料時，剩餘訊號權重重新分配
    total_weight = sum(w for _, _, w in signals)
    weighted_pct = sum(val * w / total_weight for _, val, w in signals)

    pred_open = last_close * (1 + weighted_pct / 100)

    # 信心區間：用標準差概念，三訊號發散程度
    values = [val for _, val, _ in signals]
    if len(values) >= 2:
        avg = sum(values) / len(values)
        std = (sum((v - avg) ** 2 for v in values) / len(values)) ** 0.5
        ci_lower = last_close * (1 + (weighted_pct - std) / 100)
        ci_upper = last_close * (1 + (weighted_pct + std) / 100)
    else:
        ci_lower = pred_open * 0.995
        ci_upper = pred_open * 1.005
        std = None

    # 訊號一致性判斷
    positive = sum(1 for v in values if v > 0)
    negative = sum(1 for v in values if v < 0)
    if positive == len(values):
        consensus = "全部偏多"
    elif negative == len(values):
        consensus = "全部偏空"
    elif positive > negative:
        consensus = f"偏多 ({positive}/{len(values)} 訊號)"
    elif negative > positive:
        consensus = f"偏空 ({negative}/{len(values)} 訊號)"
    else:
        consensus = "訊號分歧"

    return {
        "last_close": round(last_close, 2),
        "signals": [{"name": n, "value": round(v, 2), "weight": w} for n, v, w in signals],
        "weighted_pct": round(weighted_pct, 2),
        "pred_open": round(pred_open, 2),
        "ci_lower": round(ci_lower, 2),
        "ci_upper": round(ci_upper, 2),
        "consensus": consensus,
        "signal_std": round(std, 2) if std is not None else None,
        "signal_count": len(signals),
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

    # 模型 3：ADR 溢價衰減版（改良版）
    # 邏輯：ADR 漲跌不會 100% 反映到台股開盤，因為：
    #   (a) 台股盤後新聞已部分反映 ADR 後續走勢
    #   (b) ADR 收盤後到台股開盤有 5 小時，可能再有變動
    # 實證上，2330 開盤幅度約為 ADR 漲跌幅的 0.75 (即衰減 25%)
    # 用近 60 日「2330 開盤漲幅 / TSM 前夜漲幅」計算實際衰減係數
    decay_factor = 0.75  # 預設值
    model3 = None
    df_local = locals().get("df")
    try:
        if model2 is not None and df_local is not None and len(df_local) >= 30:
            df = df_local
            # 計算 2330 隔日相對 TSM 當日的漲跌比
            df["t2330_pct"] = df["t2330"].pct_change()
            df["tsm_pct"] = df["tsm"].pct_change()
            # 取 |TSM 變動 > 1%| 的樣本，較有意義
            sig = df[df["tsm_pct"].abs() > 0.01].tail(60)
            if len(sig) >= 10:
                # 衰減係數 = 平均 (2330 變動 / TSM 變動)
                ratio = (sig["t2330_pct"] / sig["tsm_pct"]).clip(lower=0, upper=1.5)
                decay_factor = float(ratio.mean())
                decay_factor = max(0.3, min(decay_factor, 1.2))  # 限制合理範圍
                print(f"[calc] 2330 ADR 衰減係數 (近 60 日實證)={decay_factor:.3f}")
        model3 = last_2330 * (1 + tsm_pct * decay_factor)
    except Exception as e:
        print(f"[calc] 2330 model3 失敗: {e}", file=sys.stderr)
        model3 = last_2330 * (1 + tsm_pct * 0.75)  # 退化用預設

    # 模型 4：短期動能（5 日累積）dampened —— 對開盤預測貢獻較弱(學界共識),
    # 加進 ensemble 讓 MAE-weighted calibration 自動決定權重;若無用權重自然趨近 0。
    model4 = None
    momentum_5d_pct = None
    try:
        if hist_2330 is not None and len(hist_2330) >= 6:
            prev_5d = safe_float(hist_2330.iloc[-6]["Close"])
            if prev_5d and prev_5d > 0:
                momentum_5d_pct = (last_2330 / prev_5d - 1) * 100
                # dampening 0.15:5 日累積 5% → 隔日多 0.75%;5d -5% → -0.75%
                model4 = last_2330 * (1 + (momentum_5d_pct / 100) * 0.15)
                print(f"[calc] 2330 model4 momentum(5d {momentum_5d_pct:+.2f}%, dampened 0.15) = {model4:.2f}")
    except Exception as e:
        print(f"[calc] 2330 model4 失敗: {e}", file=sys.stderr)

    res = {
        "last_2330": round(last_2330, 2),
        "tsm_pct": round(tsm_pct * 100, 2),
        "model1_1to1": round(model1, 2),
        "model2_regression": round(model2, 2) if model2 else None,
        "model3_adr_decay": round(model3, 2) if model3 else None,
        "model4_momentum": round(model4, 2) if model4 else None,
        "momentum_5d_pct": round(momentum_5d_pct, 2) if momentum_5d_pct is not None else None,
        "decay_factor": round(decay_factor, 3),
    }
    # 四個模型可用就取中位數
    valid = [v for v in [model1, model2, model3, model4] if v]
    if valid:
        res["mid"] = round(sorted(valid)[len(valid) // 2], 2)  # 中位數
        if len(valid) >= 2:
            res["range"] = (round(min(valid), 2), round(max(valid), 2))
    return res


def calc_0050_prediction(last_0050: Optional[float],
                          predictions_2330: dict,
                          taiex_pred: dict) -> dict:
    """
    0050 (元大台灣 50) 開盤預測。

    模型：0050 大約 50% 是 2330（其餘 49 檔加總約等於「加權指數扣掉 2330」），
    故 0050 的預期漲跌幅 ≈ 0.5 × 2330 預期漲跌幅 + 0.5 × 加權指數預期漲跌幅。

    任一上游缺失時退化：只用可用那一邊；兩邊都缺 → 回 error。
    失敗 / 缺資料時不影響晨報，回 {"error": ...}。
    """
    if last_0050 is None:
        return {"error": "缺 0050 昨收"}

    # 2330 預測漲跌幅
    p2_mid = predictions_2330.get("mid") if isinstance(predictions_2330, dict) else None
    p2_last = predictions_2330.get("last_2330") if isinstance(predictions_2330, dict) else None
    pct_2330 = (((p2_mid / p2_last) - 1) * 100) if (p2_mid and p2_last) else None

    # 加權指數預測漲跌幅
    pct_taiex = (taiex_pred or {}).get("weighted_pct")

    if pct_2330 is not None and pct_taiex is not None:
        pct_weighted = 0.5 * pct_2330 + 0.5 * pct_taiex
        method = "0.5 × 2330 + 0.5 × 加權指數"
    elif pct_taiex is not None:
        pct_weighted = pct_taiex
        method = "加權指數（2330 預測缺失）"
    elif pct_2330 is not None:
        pct_weighted = pct_2330
        method = "2330（加權指數預測缺失）"
    else:
        return {"error": "上游 2330 與加權指數預測皆失敗"}

    pred_open = last_0050 * (1 + pct_weighted / 100)
    return {
        "last": round(last_0050, 2),
        "pred_open": round(pred_open, 2),
        "pred_pct": round(pct_weighted, 3),
        "pct_2330": round(pct_2330, 3) if pct_2330 is not None else None,
        "pct_taiex": round(pct_taiex, 3) if pct_taiex is not None else None,
        "method": method,
    }


def _norm_ret_index(s):
    """把日報酬 series 的 index 正規化成 tz-naive 的日期,方便兩檔對齊算 beta。"""
    s = s.copy()
    try:
        s.index = s.index.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    try:
        s.index = s.index.normalize()
    except AttributeError:
        pass
    return s


def fetch_portfolio_market_data(portfolio: dict,
                                  taiex_hist=None) -> tuple[dict, dict]:
    """
    抓持股的昨收價 + 對加權指數 beta(近 ~60 日 daily-return 回歸,clamp [0.3, 2.0])。

    回傳 (close_map, beta_map):
      close_map: {code: 昨收價}
      beta_map:  {code: beta}(資料不足者不列入,呼叫端視為 1.0)
    隱私:不印出個股代號到任何使用者可見處,只在 stderr debug。失敗個股略過。
    """
    if not portfolio:
        return {}, {}
    close_map: dict = {}
    beta_map: dict = {}
    codes = list(portfolio.keys())
    try:
        tickers = " ".join(f"{c}.TW" for c in codes)
        df = yf.download(tickers, period="3mo", group_by="ticker",
                         auto_adjust=False, progress=False, threads=True)
    except Exception as e:
        print(f"[portfolio] 行情下載失敗: {e}", file=sys.stderr)
        return {}, {}
    if df is None or len(df) == 0:
        return {}, {}

    twii_ret = None
    if taiex_hist is not None and hasattr(taiex_hist, "empty") and not taiex_hist.empty:
        twii_ret = _norm_ret_index(taiex_hist["Close"].pct_change().dropna())

    for code in codes:
        try:
            if len(codes) > 1:
                sub = df[f"{code}.TW"].dropna(subset=["Close"])
            else:
                sub = df.dropna(subset=["Close"])
            sub = sub[sub["Close"] > 0]
            if sub.empty:
                continue
            close_map[code] = round(float(sub["Close"].iloc[-1]), 2)
            if twii_ret is not None and len(sub) >= 30:
                stock_ret = _norm_ret_index(sub["Close"].pct_change().dropna())
                joined = pd.concat([stock_ret, twii_ret], axis=1, join="inner").dropna()
                if len(joined) >= 20:
                    sr = joined.iloc[:, 0]
                    mr = joined.iloc[:, 1]
                    var = float(mr.var())
                    if var and var > 0:
                        beta = float(sr.cov(mr) / var)
                        beta_map[code] = max(0.3, min(2.0, beta))
        except (KeyError, ValueError, TypeError) as e:
            print(f"[portfolio] {code} 行情處理略過: {e}", file=sys.stderr)
            continue
    print(f"[portfolio] 取得 {len(close_map)}/{len(codes)} 檔昨收、{len(beta_map)} 檔 beta")
    return close_map, beta_map


def calc_portfolio_forecast(portfolio: dict,
                              taiex_pct: Optional[float],
                              special_preds: dict,
                              close_map: dict,
                              beta_map: Optional[dict] = None) -> dict:
    """
    估算單一倉位「今日預估開盤漲幅 %」與「預估損益金額」。

    portfolio:     {code: lots(張)}
    taiex_pct:     加權指數預測漲跌幅 %(非 2330/0050/00662 個股 beta 的基準)
    special_preds: {code: pct} 已有專屬模型者(2330/0050/00662)直接用該 %
    close_map:     {code: 昨收價}
    beta_map:      {code: beta vs 加權};缺漏者視為 1.0

    每檔預估漲跌幅:
      - 在 special_preds 中(2330/0050/00662)→ 用專屬模型 %
      - 其他 → beta × 加權預測 %
    倉位漲幅 = Σ(市值 × 漲跌幅) / Σ市值;金額 = Σ(市值 × 漲跌幅)。

    回傳 {pred_pct, pred_amount, last_value, n_holdings, n_priced} 或 {}（空倉/無報價）。
    隱私:回傳只有彙總值,**無任何個股代號或張數**。
    """
    if not portfolio:
        return {}
    beta_map = beta_map or {}
    total_value = 0.0
    total_gain = 0.0
    n_priced = 0
    for code, lots in portfolio.items():
        close = close_map.get(code)
        if not close or close <= 0:
            continue
        shares = lots * 1000
        if code in special_preds and special_preds.get(code) is not None:
            pct = special_preds[code]
        elif taiex_pct is not None:
            beta = beta_map.get(code)
            beta = 1.0 if beta is None else beta
            pct = beta * taiex_pct
        else:
            pct = 0.0
        value = shares * close
        total_value += value
        total_gain += value * pct / 100.0
        n_priced += 1
    if total_value <= 0:
        return {}
    return {
        "pred_pct": round(total_gain / total_value * 100, 2),
        "pred_amount": round(total_gain, 0),
        "last_value": round(total_value, 0),
        "n_holdings": len(portfolio),
        "n_priced": n_priced,
    }


def build_special_preds(predictions: dict, tw0050_pred: dict, fair: dict,
                          taiex_pct: Optional[float] = None) -> dict:
    """
    組出有「專屬模型」的標的今日預估漲幅 %(供持股漲幅用),純函式易測。

    涵蓋:
      2330  → 2330 四模型中位數 vs 昨收
      0050  → calc_0050_prediction 的 pred_pct
      00662 → calc_00662_fair_value 的 implied_change_pct
      槓桿 ETF(LEVERAGED_ETF)→ 倍數 × 基準(0050 或 加權)預估 %
    回傳 {code: pct}(只含算得出來的)。
    """
    sp: dict = {}
    p2_mid = predictions.get("mid") if isinstance(predictions, dict) else None
    p2_last = predictions.get("last_2330") if isinstance(predictions, dict) else None
    if p2_mid and p2_last:
        sp["2330"] = round((p2_mid / p2_last - 1) * 100, 4)
    if isinstance(tw0050_pred, dict) and tw0050_pred.get("pred_pct") is not None:
        sp["0050"] = tw0050_pred["pred_pct"]
    if isinstance(fair, dict) and fair.get("implied_change_pct") is not None:
        sp["00662"] = fair["implied_change_pct"]
    # 槓桿 ETF = 倍數 × 基準(0050 同追台灣50;taiex 基準用加權預測 %)
    base_vals = {"0050": sp.get("0050"), "taiex": taiex_pct}
    for lev_code, (base_key, mult) in LEVERAGED_ETF.items():
        base = base_vals.get(base_key)
        if base is not None:
            sp[lev_code] = round(mult * base, 4)
    return sp


def calc_momentum_metrics(close_series) -> dict:
    """
    從 close 序列計算動能 / 波動度 / 移動平均指標。

    回傳:
      last, pct_5d, pct_20d, ma20, ma50, ma20_dist_pct, ma50_dist_pct,
      daily_vol_pct (近 20 日 daily-return std)

    資料不足時對應欄位為 None；最低需 6 天資料才有 5d 動能。
    """
    if close_series is None:
        return {}
    s = close_series.dropna() if hasattr(close_series, "dropna") else close_series
    n = len(s) if hasattr(s, "__len__") else 0
    if n < 6:
        return {}

    last = float(s.iloc[-1])
    out: dict = {"last": round(last, 2)}

    if n >= 6:
        prev5 = float(s.iloc[-6])
        out["pct_5d"] = round((last / prev5 - 1) * 100, 2) if prev5 > 0 else None
    if n >= 21:
        prev20 = float(s.iloc[-21])
        out["pct_20d"] = round((last / prev20 - 1) * 100, 2) if prev20 > 0 else None
        ma20 = float(s.tail(20).mean())
        out["ma20"] = round(ma20, 2)
        out["ma20_dist_pct"] = round((last / ma20 - 1) * 100, 2) if ma20 > 0 else None
    if n >= 51:
        ma50 = float(s.tail(50).mean())
        out["ma50"] = round(ma50, 2)
        out["ma50_dist_pct"] = round((last / ma50 - 1) * 100, 2) if ma50 > 0 else None
    if n >= 21:
        rets = s.pct_change().dropna().tail(20)
        if len(rets):
            out["daily_vol_pct"] = round(float(rets.std()) * 100, 3)
    return out


def calc_midterm_forecast(metrics: dict,
                          horizons: tuple = (5, 20)) -> dict:
    """
    根據動能指標生成中期 range forecast。

    **重要：這不是「點預測」**——學界共識:多日點預測精度與隨機漫步相近。
    本 forecast 提供的是「**基於歷史波動度的合理區間**」(±1.5σ × √horizon),
    + 一個保守的 drift 估計(過去 20 日平均日收益,長期 horizon 加均值回歸 dampening)。

    解讀方式:「下週 2330 約 95% 機率落在 lower-upper」, 而非「下週 2330 會漲到 X」。
    """
    last = metrics.get("last")
    daily_vol = metrics.get("daily_vol_pct")
    pct_20d = metrics.get("pct_20d")
    if not last or not daily_vol:
        return {"error": "需要至少 21 天歷史"}

    avg_daily_pct = (pct_20d / 20.0) if pct_20d is not None else 0.0

    forecasts: dict = {}
    for h in horizons:
        # drift: 短期 horizon 全用,長期施加均值回歸 dampening
        dampen = 1.0 if h <= 5 else 0.5 if h <= 20 else 0.3
        expected_return_pct = avg_daily_pct * h * dampen
        # ±1σ (~68% 常態波動) 與 ±1.5σ (~87% 極端波動) 同時計算
        band_1s = daily_vol * (h ** 0.5) * 1.0
        band_15s = daily_vol * (h ** 0.5) * 1.5
        mid = last * (1 + expected_return_pct / 100)
        upper_1s = last * (1 + (expected_return_pct + band_1s) / 100)
        lower_1s = last * (1 + (expected_return_pct - band_1s) / 100)
        upper_15s = last * (1 + (expected_return_pct + band_15s) / 100)
        lower_15s = last * (1 + (expected_return_pct - band_15s) / 100)
        forecasts[f"{h}d"] = {
            "horizon_days": h,
            "expected_mid": round(mid, 2),
            # 向後相容:預設 upper/lower 仍為 ±1.5σ
            "upper": round(upper_15s, 2),
            "lower": round(lower_15s, 2),
            # ±1σ (常態 68%) 與 ±1.5σ (極端 87%) 分開列
            "upper_1s": round(upper_1s, 2),
            "lower_1s": round(lower_1s, 2),
            "upper_15s": round(upper_15s, 2),
            "lower_15s": round(lower_15s, 2),
            "band_1s_pct": round(band_1s, 2),
            "band_15s_pct": round(band_15s, 2),
            "expected_pct": round(expected_return_pct, 2),
            # 向後相容
            "band_pct": round(band_15s, 2),
        }
    return forecasts


def _trend_label(metrics: dict) -> str:
    """根據 MA20 距離給趨勢標籤(過熱/上行/盤整/下行/超賣)。"""
    d20 = metrics.get("ma20_dist_pct")
    if d20 is None:
        return "—"
    if d20 > 5:
        return "強勢(MA20 上方 >5%,過熱)"
    if d20 > 2:
        return "上行"
    if d20 < -5:
        return "弱勢(MA20 下方 >5%,超賣)"
    if d20 < -2:
        return "下行"
    return "盤整"


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
                            # 800 字而非 300 — Reuters/Bloomberg/CNBC 摘要常 500-1000 字,
                            # 切太短容易切在「公司剛被提及」就沒下文,LLM 看不到證據
                            "summary": (d.get("summary") or "")[:800],
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
                    # 800 字 — 與上方 cnyes JSON 路徑一致;讓 LLM 看到具體事實(產品/數字/引言),
                    # 避免 R12 鐵律因「沒看到具體事實」而把該公司刪掉
                    "summary": (entry.get("summary", "") or "")[:800],
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                })
        except Exception as e:
            print(f"[news] {source} 抓取失敗：{e}", file=sys.stderr)
    return items


def dedup_news(news: list[dict], similarity: float = 0.85) -> list[dict]:
    """
    去除重複 / 近似重複的新聞（同一事件常被多個 RSS 來源重貼）。
    規則：標題正規化（去空白、去標點、小寫）後完全相同 → 重複；
         或與已保留標題的 difflib 相似度 > similarity → 重複。
    保留先出現者（feed 順序在前的來源）。
    """
    import difflib
    import re as _re

    def _norm(t: str) -> str:
        t = (t or "").lower().strip()
        t = _re.sub(r"[\s　]+", "", t)
        # 只保留中英數，去掉所有標點符號
        t = _re.sub(r"[^\w一-鿿]", "", t)
        return t

    kept: list[dict] = []
    kept_norms: list[str] = []
    dropped = 0
    for n in news:
        nt = _norm(n.get("title", ""))
        if not nt:
            kept.append(n)
            continue
        is_dup = False
        for kn in kept_norms:
            if nt == kn:
                is_dup = True
                break
            # 近似比對：兩者較短長度 >= 8 才比，避免短標題誤殺
            if (min(len(nt), len(kn)) >= 8
                    and difflib.SequenceMatcher(None, nt, kn).ratio() > similarity):
                is_dup = True
                break
        if is_dup:
            dropped += 1
            continue
        kept.append(n)
        kept_norms.append(nt)
    print(f"[news] 去重：{len(news)} → {len(kept)} 則（移除 {dropped} 則重複）")
    return kept


# ===================== 重大事件自動辨識 (Task B) =====================
# 高權重關鍵字（中英對照），用於 classify_news_importance
FED_OFFICIALS = [
    "Powell", "Williams", "Jefferson", "Bowman", "Cook", "Kugler", "Waller",
    "Barr", "Brainard", "Daly", "Bostic", "Mester", "Kashkari", "Goolsbee",
    "Schmid", "Logan", "Musalem", "Hammack", "鮑爾", "鮑威爾",
    "Warsh",   # 新任聯準會主席
]
FED_EVENTS = [
    "FOMC", "聯準會", "Federal Reserve", "Fed minutes", "Fed Funds",
    "rate decision", "升息", "降息", "利率決議", "點陣圖", "dot plot",
    "Jackson Hole",
]
ECON_DATA = [
    "CPI", "PPI", "PCE", "核心通膨", "core inflation",
    "Nonfarm Payrolls", "非農", "就業數據", "失業率", "Initial Jobless Claims",
    "ADP", "JOLTS",
    "GDP", "ISM", "PMI", "零售銷售", "Retail Sales", "Consumer Confidence",
    "Durable Goods", "Industrial Production",
]
GEOPOLITICAL = [
    "出口管制", "晶片禁令", "對中制裁", "Entity List", "EAR",
    "川習會", "Trump Xi", "貿易戰", "tariff", "關稅",
    "台海", "Taiwan Strait", "封鎖", "demilitarized",
    "伊朗", "以色列", "烏克蘭", "戰爭", "war",
    # 中國政策/對台 深度
    "中共", "中國商務部", "China MOFCOM", "中國國台辦",
    "解放軍", "PLA", "海警", "軍演", "drill",
    "稀土", "rare earth", "中國新晶片", "華為", "SMIC", "Huawei",
    "禁止出口", "ban", "黑名單", "blacklist",
    "晶片補貼", "CHIPS Act",
    "央行降準", "RRR", "China stimulus", "人民幣",
]
# 直接牽動台股的重大地緣事件 —— 升級為 critical（會抓全文 + prompt 強制分析對台影響）
GEOPOLITICAL_CRITICAL = [
    "川習會", "川習", "Trump Xi", "拜習", "習拜",
    "台海", "Taiwan Strait", "對台", "台灣問題", "一個中國", "侵台", "封島",
    "軍演", "對台軍售", "解放軍", "PLA", "封鎖", "blockade",
    "出口管制", "晶片禁令", "Entity List", "對中制裁", "EAR",
    "戰爭", "war",
]
TW_POLICY = [
    "金管會", "央行", "升息", "降息", "外資匯入", "外匯存底",
    "產創條例", "新青安", "科專",
    "TSMC", "台積電", "艾司摩爾", "ASML",
]


def _matches_any(text: str, keywords: list[str]) -> Optional[str]:
    """文本是否包含任一關鍵字，回傳命中的那個。"""
    if not text:
        return None
    lower = text.lower()
    for kw in keywords:
        if kw.lower() in lower:
            return kw
    return None


def _strip_html(html: str) -> str:
    """簡單去 HTML tag，不依賴 BeautifulSoup。"""
    import re as _re
    # 移除 <script>...</script> 與 <style>...</style>
    html = _re.sub(r"<script[^>]*>.*?</script>", "", html, flags=_re.DOTALL | _re.IGNORECASE)
    html = _re.sub(r"<style[^>]*>.*?</style>", "", html, flags=_re.DOTALL | _re.IGNORECASE)
    # 移除其他 tag
    html = _re.sub(r"<[^>]+>", " ", html)
    # HTML entities
    html = html.replace("&nbsp;", " ").replace("&amp;", "&")
    html = html.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    # 壓縮空白
    html = _re.sub(r"\s+", " ", html).strip()
    return html


def fetch_news_fulltext(news: list[dict],
                          max_critical: int = 10,
                          max_high: int = 10) -> list[dict]:
    """
    對 critical / high 重要性的新聞,嘗試抓 RSS link 的網頁全文(前 2500 字)。
    寫入 news[i]["fulltext"] 欄位。

    為什麼擴大到 high:大部分個股新聞(NVDA/AMD/AVGO/TSM 法說 / 8-K 內容)
    被分類為 high 而非 critical,只有 300-800 字 RSS snippet 不夠 LLM 證明
    「發生了具體事」, 觸發 R12 鐵律把公司刪掉, 報告就變稀薄。

    Critical 永遠優先(預算用滿才輪 high)。
    """
    crit_fetched = 0
    high_fetched = 0
    # 先掃一輪 critical(優先級高,即使在 list 後段也先抓)
    for n in news:
        if crit_fetched >= max_critical:
            break
        if n.get("importance") != "critical":
            continue
        link = n.get("link", "")
        if not link or not link.startswith("http"):
            continue
        try:
            r = requests.get(link, timeout=10,
                              headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                              allow_redirects=True)
            if r.status_code != 200:
                continue
            text = _strip_html(r.text)
            if len(text) > 100:
                n["fulltext"] = text[:2500]
                crit_fetched += 1
        except Exception as e:
            print(f"[news_full] critical {link[:60]} 失敗: {e}", file=sys.stderr)
            continue
    # 再掃 high(預算用滿不再抓)
    for n in news:
        if high_fetched >= max_high:
            break
        if n.get("importance") != "high":
            continue
        if n.get("fulltext"):    # 已被 critical 路徑抓過(理論上不該發生,但保險)
            continue
        link = n.get("link", "")
        if not link or not link.startswith("http"):
            continue
        try:
            r = requests.get(link, timeout=8,    # high 用較短 timeout 避免拖慢
                              headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                              allow_redirects=True)
            if r.status_code != 200:
                continue
            text = _strip_html(r.text)
            if len(text) > 100:
                n["fulltext"] = text[:2000]    # high 全文略短(2000 vs critical 2500)
                high_fetched += 1
        except Exception as e:
            print(f"[news_full] high {link[:60]} 失敗: {e}", file=sys.stderr)
            continue
    print(f"[news_full] 抓到 {crit_fetched} 篇 critical + {high_fetched} 篇 high 全文")
    return news


# ============= 多日歷史記憶 (Opt 1) =============
STATE_FILE = Path("state/history.json")


def detect_us_holiday(quotes: dict, today_tpe_date: dt.date) -> dict:
    """
    偵測昨日美股是否休市（美國國定假日如 Memorial Day、Labor Day、Christmas...）。

    邏輯：今日 TW 為 D 日,「最近 US 交易日」期望:
      - TW Mon  → 期望 Fri (3 天前)
      - TW Sat  → 期望 Fri (1 天前)
      - TW Tue-Fri → 期望 昨天 (1 天前)
    若 QQQ 的 date 比期望日更早 → 中間有 US 假日(美股停市),所有美股資料為延續值。

    回傳 {"detected": bool, "actual_date", "expected_date", "gap_days", "weekday"}
    """
    qqq = quotes.get("QQQ", {})
    qqq_date_str = (qqq.get("date") if isinstance(qqq, dict) else None) or ""
    if not qqq_date_str:
        return {"detected": False}
    try:
        actual_date = dt.datetime.strptime(qqq_date_str, "%Y-%m-%d").date()
    except ValueError:
        return {"detected": False}

    wd = today_tpe_date.weekday()    # 0=Mon, 6=Sun
    if wd == 0:                                       # Mon TPE
        expected = today_tpe_date - dt.timedelta(days=3)
    elif wd == 5:                                     # Sat TPE
        expected = today_tpe_date - dt.timedelta(days=1)
    elif wd == 6:                                     # Sun TPE (理論上 workflow 不跑,留著保險)
        expected = today_tpe_date - dt.timedelta(days=2)
    else:                                             # Tue-Fri TPE
        expected = today_tpe_date - dt.timedelta(days=1)

    detected = actual_date < expected
    weekday_zh = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][actual_date.weekday()]
    return {
        "detected": detected,
        "actual_date": qqq_date_str,
        "actual_weekday": weekday_zh,
        "expected_date": expected.strftime("%Y-%m-%d"),
        "gap_days": (expected - actual_date).days,
    }


def detect_market_alerts(quotes: dict, fair: dict, predictions: dict, taifex_oi: dict) -> list[dict]:
    """
    Task H: 自動偵測市場過熱/恐慌訊號，回傳警告清單。
    每個警告含：level (red/orange/yellow)、title、detail
    """
    alerts: list[dict] = []

    # 0. 美股昨日休市（最優先警告 —— 影響所有美股訊號的可信度）
    us_hol = quotes.get("US_HOLIDAY") or {}
    if us_hol.get("detected"):
        alerts.append({
            "level": "red",
            "title": "美股昨日休市（國定假日）",
            "detail": (f"美股最新收盤為 {us_hol.get('actual_date')}（{us_hol.get('actual_weekday')}），"
                       f"與今日台股相隔 {us_hol.get('gap_days', 0)} 個工作天 → 所有美股相關訊號"
                       f"(QQQ/TSM/SOX/VIX/NQ/ES/WTI/黃金/10Y) 為**延續值,非昨日新資訊**。"
                       f"立場評分時應將這些維度視為 stale 給 0 分,只信任 TW 本地訊號(夜盤、外資、市場廣度)。"
                       f"預測模型仍會跑但信心應降至低。"),
        })
    macro = quotes.get("MACRO", {}) or {}

    # 1. VIX 恐慌
    vix = macro.get("VIX", {}) or {}
    vix_close = vix.get("close")
    if vix_close is not None:
        if vix_close > 30:
            alerts.append({
                "level": "red",
                "title": "VIX 恐慌指數爆表",
                "detail": f"VIX 收 {vix_close}，市場進入恐慌區（>30）。建議降低部位、避免追漲殺跌。",
            })
        elif vix_close > 25:
            alerts.append({
                "level": "orange",
                "title": "VIX 警戒級",
                "detail": f"VIX 收 {vix_close}（>25）。市場波動加劇，操作需更謹慎。",
            })

    # 2. SOX 急跌（與台股 2330 高度相關）
    sox = macro.get("SOX", {}) or {}
    sox_pct = sox.get("change_pct")
    if sox_pct is not None and sox_pct < -3:
        alerts.append({
            "level": "red",
            "title": "費半急跌",
            "detail": f"SOX 單日跌 {sox_pct:.2f}%（< -3%）。台積電與半導體類股今日開低機率 > 80%。",
        })
    elif sox_pct is not None and sox_pct > 3.5:
        alerts.append({
            "level": "orange",
            "title": "費半急漲（短期可能拉回）",
            "detail": f"SOX 單日漲 {sox_pct:.2f}%（> 3.5%）。歷史上連續急漲後常有獲利了結。",
        })

    # 3. 外資台指期淨空
    foreign_oi = taifex_oi.get("foreign_oi_net")
    if foreign_oi is not None:
        if foreign_oi < -20000:
            alerts.append({
                "level": "red",
                "title": "外資台指期重壓淨空",
                "detail": f"外資台指期未平倉 {foreign_oi:+,} 口（< -2 萬）。今日台股開低或盤中下殺機率高。",
            })
        elif foreign_oi > 30000:
            alerts.append({
                "level": "yellow",
                "title": "外資台指期極度看多（提防多殺多）",
                "detail": f"外資台指期未平倉 {foreign_oi:+,} 口（> +3 萬）。籌碼面強多，但需提防一致性過高的反向風險。",
            })

    # 4. DXY 急升
    dxy = macro.get("DXY", {}) or {}
    dxy_pct = dxy.get("change_pct")
    if dxy_pct is not None and dxy_pct > 0.8:
        alerts.append({
            "level": "orange",
            "title": "美元指數急升",
            "detail": f"DXY 漲 {dxy_pct:.2f}%（> 0.8%）。新興市場資金外流壓力大、台幣可能急貶。",
        })

    # 5. 10Y 殖利率急升（壓抑成長股估值）
    ten_y = macro.get("10Y", {}) or {}
    ten_y_change = ten_y.get("change_pct")
    if ten_y_change is not None and ten_y_change > 2:
        alerts.append({
            "level": "orange",
            "title": "10Y 殖利率急升",
            "detail": f"10Y 殖利率漲 {ten_y_change:.2f}%。成長股折現率壓力升高、估值將承壓。",
        })

    # 6. 2330 預測與實際偏離過大（從 calibration 推斷）
    # 這個由 LLM 自行判讀，警告層級給 yellow
    pred_pct = (predictions.get("mid", 0) - predictions.get("last_2330", 1)) / predictions.get("last_2330", 1) * 100 if predictions.get("last_2330") else 0
    if abs(pred_pct) > 3:
        alerts.append({
            "level": "yellow",
            "title": "2330 預測波動幅度大",
            "detail": f"三模型預測與昨收差距 {pred_pct:+.2f}%。波動較大，建議減量操作或等開盤後再進場。",
        })

    # 7. 過熱/超賣 regime 警示（5 日累積動能極端）—— 對 2330 / 00662 / 0050 三檔
    midterm = quotes.get("MIDTERM") or {}
    for name in ("2330", "0050", "00662"):
        entry = midterm.get(name) or {}
        metrics = entry.get("metrics") or {}
        pct_5d = metrics.get("pct_5d")
        d20 = metrics.get("ma20_dist_pct")
        if pct_5d is None:
            continue
        # 距 MA20 是選配資訊（資料 < 21 天時為 None）
        d20_str = f"、距 MA20 {d20:+.1f}%" if d20 is not None else ""
        # 5 日漲超過 +5% 或跌超過 -5% → orange 警示
        if pct_5d > 5:
            alerts.append({
                "level": "orange",
                "title": f"{name} 短期過熱（5 日 {pct_5d:+.1f}%）",
                "detail": (f"{name} 過去 5 日累積 {pct_5d:+.2f}%{d20_str}。"
                           f"短期超漲常伴隨回測,今日預測信心應降,關鍵價位寬度建議從 ±1% 擴大至 ±2%。"),
            })
        elif pct_5d < -5:
            alerts.append({
                "level": "orange",
                "title": f"{name} 短期超賣（5 日 {pct_5d:+.1f}%）",
                "detail": (f"{name} 過去 5 日累積 {pct_5d:+.2f}%{d20_str}。"
                           f"短期超跌常伴隨技術性反彈,今日預測信心應降,關鍵價位寬度建議從 ±1% 擴大至 ±2%。"),
            })

    return alerts


BACKTEST_DISPLAY_DAYS = 3   # 信件「預測準確度回顧」最多顯示幾筆（最近 N 個交易日）


def build_prediction_backtest(history: list[dict]) -> str:
    """
    Task F: 比對「過去 N 天我預測的開盤點位」vs「實際開盤」，
    讓 LLM 看到自己的歷史誤差並修正。

    顯示 + 平均誤差皆限於最近 BACKTEST_DISPLAY_DAYS 個交易日(預設 3),
    避免信件 backtest 區塊隨歷史累積越來越長。
    （注意：自我校正迴圈 `calibrate_predictions` 仍用 ~20 日,獨立運作不受此限制。）
    """
    if not history or len(history) < 2:
        return "（首週運行，無歷史預測可回溯）"

    rows = []
    try:
        # 抓近 7 個交易日實際開盤
        twii_hist = yf.Ticker("^TWII").history(period="10d", auto_adjust=False).dropna(subset=["Open"])
        tw2330_hist = yf.Ticker("2330.TW").history(period="10d", auto_adjust=False).dropna(subset=["Open"])
        tw0066_hist = yf.Ticker("00662.TW").history(period="10d", auto_adjust=False).dropna(subset=["Open"])
        tw0050_hist = yf.Ticker("0050.TW").history(period="10d", auto_adjust=False).dropna(subset=["Open"])

        def to_date(idx):
            return idx.tz_localize(None).strftime("%Y-%m-%d") if idx.tz else idx.strftime("%Y-%m-%d")

        # 同步把 Yahoo 的 float64 精度雜訊（如 117.55000305175781）round 掉
        twii_opens = {to_date(d): round(float(v), 2) for d, v in twii_hist["Open"].items()}
        tw2330_opens = {to_date(d): round(float(v), 2) for d, v in tw2330_hist["Open"].items()}
        tw0066_opens = {to_date(d): round(float(v), 2) for d, v in tw0066_hist["Open"].items()}
        tw0050_opens = {to_date(d): round(float(v), 2) for d, v in tw0050_hist["Open"].items()}

        # state 的 entry["date"] 是「該預測對應的台股開盤日」（main 在 6am TPE 時用 now_tpe 寫入）。
        # 比對時應該找『同一天』的實際開盤；若那天市場休市（如六、日），才往後找下一個交易日。
        # 原本 `od > pred_date` 是錯的 —— 它把週五的預測拿去比週一的開盤，把單日誤差變成 3 天的市場移動。
        # 只取最近 BACKTEST_DISPLAY_DAYS 筆（排除今天那筆，因今天的實際開盤還沒到）。
        sorted_hist = sorted(history, key=lambda h: h.get("date", ""))
        recent_hist = sorted_hist[:-1][-BACKTEST_DISPLAY_DAYS:]
        for h in recent_hist:
            pred_date = h.get("date", "")
            # 找「pred_date 當天或之後」第一個有實際開盤的交易日
            next_date = None
            for od in sorted(tw2330_opens.keys()):
                if od >= pred_date:
                    next_date = od
                    break
            if not next_date:
                continue

            pred_2330 = h.get("model3_2330")
            pred_00662 = h.get("fair_00662")
            pred_0050 = h.get("pred_0050")
            actual_2330 = tw2330_opens.get(next_date)
            actual_00662 = tw0066_opens.get(next_date)
            actual_0050 = tw0050_opens.get(next_date)

            err_2330 = err_00662 = err_0050 = None
            if pred_2330 and actual_2330:
                err_2330 = (actual_2330 - pred_2330) / pred_2330 * 100
            if pred_00662 and actual_00662:
                err_00662 = (actual_00662 - pred_00662) / pred_00662 * 100
            if pred_0050 and actual_0050:
                err_0050 = (actual_0050 - pred_0050) / pred_0050 * 100

            if any(e is not None for e in (err_2330, err_00662, err_0050)):
                e2330 = f"2330: 預測 {pred_2330} → 實際 {actual_2330} ({err_2330:+.2f}%)" if err_2330 is not None else "2330: 缺資料"
                e00662 = f"00662: 預測 {pred_00662} → 實際 {actual_00662} ({err_00662:+.2f}%)" if err_00662 is not None else "00662: 缺資料"
                e0050 = f"0050: 預測 {pred_0050} → 實際 {actual_0050} ({err_0050:+.2f}%)" if err_0050 is not None else "0050: 缺資料"
                rows.append(f"  {next_date}：{e2330} | {e00662} | {e0050}")

        if not rows:
            return "（歷史資料尚未對齊，需再多 1-2 天累積）"

        # 平均誤差也只算最近 BACKTEST_DISPLAY_DAYS 筆，跟顯示的行數一致避免混淆。
        err_2330_list = []
        err_00662_list = []
        err_0050_list = []
        for h in recent_hist:
            next_date = next((od for od in sorted(tw2330_opens.keys())
                              if od >= h.get("date", "")), None)
            if not next_date:
                continue
            p2 = h.get("model3_2330"); a2 = tw2330_opens.get(next_date)
            p6 = h.get("fair_00662"); a6 = tw0066_opens.get(next_date)
            p5 = h.get("pred_0050"); a5 = tw0050_opens.get(next_date)
            if p2 and a2:
                err_2330_list.append((a2 - p2) / p2 * 100)
            if p6 and a6:
                err_00662_list.append((a6 - p6) / p6 * 100)
            if p5 and a5:
                err_0050_list.append((a5 - p5) / p5 * 100)

        summary = ""
        for name, lst in (("2330", err_2330_list), ("00662", err_00662_list), ("0050", err_0050_list)):
            if lst:
                avg = sum(lst) / len(lst)
                # err = (actual − pred) / pred. avg > 0 表示實際 > 預測 = 預測「偏低」
                bias = "偏低" if avg > 0.2 else "偏高" if avg < -0.2 else "中性"
                summary += f"\n  {name} 平均誤差: {avg:+.2f}% (預測{bias})"

        return "\n".join(rows) + summary
    except Exception as e:
        return f"（回溯失敗: {e}）"


def calibrate_predictions(fair: dict, predictions: dict, taiex_pred: dict,
                          history: list[dict],
                          min_samples: int = 5, recent_n: int = 20,
                          max_bias: float = 0.02) -> tuple[dict, dict, dict]:
    """
    用歷史記憶對三個「數值預測」做自我校正（純 Python，不靠 LLM）：

    (A) 2330 模型加權：依 model1/2/3 近 recent_n 日的 MAE 反比給權重，產生
        weighted_final；任一模型樣本不足 → 退回等權中位數 mid。
    (B) bias 修正：對 00662 合理價、2330 weighted_final、加權指數 pred_open，
        各自算近 recent_n 日「(實際開盤 − 預測) / 預測」的平均偏誤，
        套用 corrected = raw × (1 + bias)；偏誤夾在 ±max_bias，避免離群值過度修正。

    回傳調整後 (fair, predictions, taiex_pred)，每個帶 "calibration" 欄位。
    任何環節失敗都不影響主流程：回傳原值並標記 calibration.applied = False。
    """
    fair = dict(fair) if isinstance(fair, dict) else fair
    predictions = dict(predictions) if isinstance(predictions, dict) else predictions
    taiex_pred = dict(taiex_pred) if isinstance(taiex_pred, dict) else taiex_pred

    def _mark_unapplied(reason: str) -> None:
        for obj in (fair, predictions, taiex_pred):
            if isinstance(obj, dict) and not obj.get("error"):
                obj.setdefault("calibration", {"applied": False, "reason": reason})
        # 2330：即使未校正，weighted_final 也要有值（= 等權中位數），讓信件顯示一致
        if isinstance(predictions, dict) and not predictions.get("error"):
            predictions.setdefault("weighted_final", predictions.get("mid"))
            predictions.setdefault("final_method", "等權中位數（歷史樣本不足）")
            predictions.setdefault("model_mae_pct",
                                   {"model1": None, "model2": None, "model3": None})

    if not history or len(history) < 2:
        _mark_unapplied("歷史樣本不足（< 2 天）")
        return fair, predictions, taiex_pred

    try:
        def _opens(symbol: str) -> dict:
            d = yf.Ticker(symbol).history(period="3mo", auto_adjust=False)
            d = d.dropna(subset=["Open"])
            out: dict[str, float] = {}
            for idx, v in d["Open"].items():
                key = (idx.tz_localize(None) if getattr(idx, "tz", None) else idx
                       ).strftime("%Y-%m-%d")
                # round 掉 Yahoo float64 精度雜訊（曾出現 117.55000305175781 這種值）
                out[key] = round(float(v), 2)
            return out
        twii_o = _opens("^TWII")
        t2330_o = _opens("2330.TW")
        t00662_o = _opens("00662.TW")
    except Exception as e:
        print(f"[calib] 抓實際開盤失敗，跳過校正: {e}", file=sys.stderr)
        _mark_unapplied(f"無法取得實際開盤：{e}")
        return fair, predictions, taiex_pred

    sorted_hist = sorted(history, key=lambda h: h.get("date", ""))

    def _actual_open_for(pred_date: str, opens_map: dict) -> Optional[float]:
        """state entry["date"] = 該預測對應的台股開盤日（同一天）。
        故比對時必須用『同日』(>=) 而不是『隔日』(>)；若當天市場休市才往後找。"""
        for od in sorted(opens_map):
            if od >= pred_date:
                return opens_map[od]
        return None

    # 收集相對誤差 (實際 − 預測) / 預測
    err: dict[str, list] = {"00662": [], "2330_final": [],
                            "m1": [], "m2": [], "m3": [], "m4": [], "taiex": []}
    for h in sorted_hist[:-1]:   # 最後一筆還沒有「隔日開盤」可比對
        pd_ = h.get("date", "")
        a662 = _actual_open_for(pd_, t00662_o)
        a2330 = _actual_open_for(pd_, t2330_o)
        atwii = _actual_open_for(pd_, twii_o)
        p662 = h.get("fair_00662")
        if p662 and a662:
            err["00662"].append((a662 - p662) / p662)
        if a2330:
            for hk, ek in (("model1_2330", "m1"), ("model2_2330", "m2"),
                           ("model3_2330", "m3"), ("model4_2330", "m4"),
                           ("weighted_final_2330", "2330_final")):
                pv = h.get(hk)
                if pv:
                    err[ek].append((a2330 - pv) / pv)
        ptwii = h.get("pred_taiex")
        if ptwii and atwii:
            err["taiex"].append((atwii - ptwii) / ptwii)

    def _mean(lst: list) -> tuple[float, int]:
        r = lst[-recent_n:]
        return (sum(r) / len(r), len(r)) if r else (0.0, 0)

    def _mae(lst: list) -> tuple[Optional[float], int]:
        r = lst[-recent_n:]
        return (sum(abs(x) for x in r) / len(r), len(r)) if r else (None, 0)

    def _apply_bias(obj: dict, value_key: str, err_key: str, label: str) -> dict:
        bias, n = _mean(err[err_key])
        if n < min_samples:
            return {"applied": False, "samples": n,
                    "reason": f"{label} 誤差樣本僅 {n} 筆（需 ≥ {min_samples}）"}
        raw = obj.get(value_key)
        if raw is None:
            return {"applied": False, "samples": n, "reason": f"{label} 無原始值"}
        b = max(-max_bias, min(bias, max_bias))
        obj[f"{value_key}_raw"] = raw
        obj[value_key] = round(raw * (1 + b), 2)
        return {"applied": True, "bias_pct": round(b * 100, 3),
                "samples": n, "raw": raw}

    # ---- (A) 2330 四模型 MAE 反比加權（model1/2/3 + model4 momentum） ----
    if isinstance(predictions, dict) and not predictions.get("error"):
        m1 = predictions.get("model1_1to1")
        m2 = predictions.get("model2_regression")
        m3 = predictions.get("model3_adr_decay")
        m4 = predictions.get("model4_momentum")
        mae1, n1 = _mae(err["m1"])
        mae2, n2 = _mae(err["m2"])
        mae3, n3 = _mae(err["m3"])
        mae4, n4 = _mae(err["m4"])
        cand = [(v, mae, n) for v, mae, n in
                ((m1, mae1, n1), (m2, mae2, n2), (m3, mae3, n3), (m4, mae4, n4))
                if v is not None]
        if cand and all(n >= min_samples and mae and mae > 0 for _, mae, n in cand):
            inv = [(v, 1.0 / mae) for v, mae, _ in cand]
            tot = sum(w for _, w in inv)
            predictions["weighted_final"] = round(
                sum(v * w for v, w in inv) / tot, 2)
            predictions["final_method"] = "近期 MAE 反比加權"
        else:
            predictions["weighted_final"] = predictions.get("mid")
            predictions["final_method"] = "等權中位數（模型誤差樣本不足）"
        predictions["model_mae_pct"] = {
            "model1": round(mae1 * 100, 3) if mae1 else None,
            "model2": round(mae2 * 100, 3) if mae2 else None,
            "model3": round(mae3 * 100, 3) if mae3 else None,
            "model4": round(mae4 * 100, 3) if mae4 else None,
        }
        # ---- (B) bias 修正 2330 ----
        predictions["calibration"] = _apply_bias(
            predictions, "weighted_final", "2330_final", "2330")
        # mid 同步成校正後最終值，讓既有 render 卡片直接反映
        predictions["mid_raw"] = predictions.get("mid")
        predictions["mid"] = predictions["weighted_final"]

    # ---- (B) bias 修正 00662 ----
    if isinstance(fair, dict) and not fair.get("error"):
        cal = _apply_bias(fair, "fair_price", "00662", "00662")
        if cal.get("applied") and fair.get("last_00662_price"):
            fair["implied_change_pct"] = round(
                (fair["fair_price"] / fair["last_00662_price"] - 1) * 100, 2)
        fair["calibration"] = cal

    # ---- (B) bias 修正 加權指數 ----
    if isinstance(taiex_pred, dict) and not taiex_pred.get("error"):
        taiex_pred["calibration"] = _apply_bias(
            taiex_pred, "pred_open", "taiex", "加權指數")

    n_applied = sum(1 for o in (fair, predictions, taiex_pred)
                    if isinstance(o, dict) and o.get("calibration", {}).get("applied"))
    fm = predictions.get("final_method", "—") if isinstance(predictions, dict) else "—"
    print(f"[calib] 校正完成：{n_applied}/3 套用 bias 修正；2330 final_method={fm}")
    return fair, predictions, taiex_pred


def load_history_state(days: int = 90) -> list[dict]:
    """讀取過去 N 天的歷史記憶（critical 事件 + 外資籌碼 + 立場）。"""
    if not STATE_FILE.exists():
        print("[state] 無歷史記憶檔，將從本次開始累積")
        return []
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        # 只保留過去 days 天
        cutoff = (dt.datetime.now(TPE) - dt.timedelta(days=days)).strftime("%Y-%m-%d")
        recent = [d for d in data if d.get("date", "") >= cutoff]
        print(f"[state] 載入歷史記憶 {len(recent)} 筆（過去 {days} 天）")
        return recent
    except Exception as e:
        print(f"[state] 載入失敗: {e}", file=sys.stderr)
        return []


def save_history_state(entry: dict, days_to_keep: int = 90) -> None:
    """
    新增一筆當日記憶，並維持只保留近 N 天。
    寫入後嘗試 git commit + push 回 repo。
    """
    try:
        existing = []
        if STATE_FILE.exists():
            existing = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []

        # 移除同日的舊紀錄（如有重跑）
        date_str = entry.get("date", dt.datetime.now(TPE).strftime("%Y-%m-%d"))
        existing = [d for d in existing if d.get("date") != date_str]
        existing.append(entry)

        # 只保留近 N 天
        cutoff = (dt.datetime.now(TPE) - dt.timedelta(days=days_to_keep)).strftime("%Y-%m-%d")
        existing = [d for d in existing if d.get("date", "") >= cutoff]

        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[state] 已寫入記憶（共 {len(existing)} 筆）")

        # 在 GitHub Actions 環境中 commit + push 回 repo
        if os.environ.get("GITHUB_ACTIONS") == "true":
            try:
                subprocess.run(["git", "config", "user.name", "morning-report-bot"], check=True, timeout=10)
                subprocess.run(["git", "config", "user.email", "actions@github.com"], check=True, timeout=10)
                subprocess.run(["git", "add", str(STATE_FILE)], check=True, timeout=10)
                # 若無變動就跳過
                diff = subprocess.run(["git", "diff", "--cached", "--quiet"], timeout=10)
                if diff.returncode != 0:
                    subprocess.run(
                        ["git", "commit", "-m", f"chore: update state {date_str} [skip ci]"],
                        check=True, timeout=10,
                    )
                    subprocess.run(["git", "push"], check=True, timeout=20)
                    print("[state] 已 push 回 repo")
                else:
                    print("[state] 無變動，跳過 commit")
            except subprocess.SubprocessError as e:
                print(f"[state] git push 失敗（不影響寄信）: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[state] 寫入失敗: {e}", file=sys.stderr)


def classify_news_importance(news: list[dict]) -> list[dict]:
    """
    對每則新聞自動分類與評重要性：
      importance: "critical" (★★★) / "high" (★★) / "normal"
      category:   "fed" / "econ_data" / "geo" / "tw_policy" / "general"

    Critical 事件會在 prompt 中被特別標記，並可選擇抓全文（Task A）。
    """
    for n in news:
        text = f"{n.get('title','')} {n.get('summary','')}"

        fed_hit = _matches_any(text, FED_OFFICIALS) or _matches_any(text, FED_EVENTS)
        econ_hit = _matches_any(text, ECON_DATA)
        geo_crit_hit = _matches_any(text, GEOPOLITICAL_CRITICAL)
        geo_hit = geo_crit_hit or _matches_any(text, GEOPOLITICAL)
        tw_hit = _matches_any(text, TW_POLICY)

        # 評分邏輯：Fed/數據/重大地緣 → critical；一般地緣/台灣政策 → high
        hits = [h for h in (fed_hit, econ_hit, geo_hit, tw_hit) if h]

        if fed_hit and econ_hit:
            # Fed + 經濟數據同時出現 = 政策轉向訊號
            n["importance"] = "critical"
            n["category"] = "fed_econ"
            n["keyword"] = f"{fed_hit} + {econ_hit}"
        elif fed_hit:
            n["importance"] = "critical"
            n["category"] = "fed"
            n["keyword"] = fed_hit
        elif econ_hit:
            n["importance"] = "critical"
            n["category"] = "econ_data"
            n["keyword"] = econ_hit
        elif geo_crit_hit:
            # 直接牽動台股的重大地緣事件（川習會、台海、出口管制…）→ critical
            n["importance"] = "critical"
            n["category"] = "geo_critical"
            n["keyword"] = geo_crit_hit
        elif geo_hit:
            n["importance"] = "high"
            n["category"] = "geo"
            n["keyword"] = geo_hit
        elif tw_hit:
            n["importance"] = "high"
            n["category"] = "tw_policy"
            n["keyword"] = tw_hit
        else:
            n["importance"] = "normal"
            n["category"] = "general"
            n["keyword"] = ""

    # 統計
    crit = sum(1 for n in news if n.get("importance") == "critical")
    high = sum(1 for n in news if n.get("importance") == "high")
    print(f"[news] 重要性分類完成：critical={crit}, high={high}, normal={len(news)-crit-high}")
    return news


def _build_prompt(quotes: dict, fair: dict, predictions: dict,
                   news: list[dict], tw0050: list[dict],
                   calibration: str = "") -> str:
    # === 分類整理新聞：critical/high/normal 分區呈現 ===
    def fmt_news(n: dict, with_full: bool = False) -> str:
        imp = n.get("importance", "normal")
        cat = n.get("category", "general")
        kw = n.get("keyword", "")
        prefix = ""
        if imp == "critical":
            prefix = f"★★★[{cat}:{kw}] "
        elif imp == "high":
            prefix = f"★★[{cat}:{kw}] "
        # summary 顯示 600 字(由 fetch_news 端 800 切過,這裡再做一次安全切);
        # 之前 200 切太短常切在「公司剛被提及」就沒下文,LLM 看不到具體事實
        text = f"- {prefix}[{n['source']}] {n['title']}（{n.get('summary','')[:600]}）"
        if with_full and n.get("fulltext"):
            text += f"\n  [全文摘錄]：{n['fulltext'][:1500]}"
        return text

    crit_news = [n for n in news if n.get("importance") == "critical"]
    high_news = [n for n in news if n.get("importance") == "high"]
    norm_news = [n for n in news if n.get("importance") == "normal"]

    news_block = "★★★ 重大事件（必讀，含全文摘錄）★★★\n"
    if crit_news:
        news_block += "\n".join(fmt_news(n, with_full=True) for n in crit_news[:10]) + "\n\n"
    else:
        news_block += "（昨日無自動辨識的 Fed/數據/政策重大事件）\n\n"
    # high 也帶全文(fetch_news_fulltext 對 high 也抓了,個股新聞多半在這層,
    # 不帶全文 LLM 只看到 600 字 snippet → R12 觸發 → 公司被刪)
    news_block += "★★ 高權重事件（地緣/台灣政策/個股法說 / 8-K 等）★★\n"
    if high_news:
        news_block += "\n".join(fmt_news(n, with_full=True) for n in high_news[:15]) + "\n\n"
    else:
        news_block += "（無）\n\n"
    news_block += "★ 一般新聞（參考）★\n"
    news_block += "\n".join(fmt_news(n) for n in norm_news[:30])

    # 整理台股 universe（市值前 100）法人/表現摘要表（讓 LLM 一眼掃完）
    if tw0050:
        tw0050_sorted = sorted(tw0050, key=lambda x: x.get("total_lot", 0), reverse=True)
        rows = []
        for s in tw0050_sorted:
            mcap = s.get("market_cap")
            mcap_str = f" 市值{mcap / 1e8:,.0f}億" if mcap else ""
            yoy = s.get("rev_yoy_pct")
            rev_str = f" 營收YoY{yoy:+.1f}%" if yoy is not None else " 營收YoY-"
            mh = s.get("major_holder_pct")
            mh_str = f" 大戶{mh:.1f}%" if mh is not None else " 大戶-"
            # 新增:5日累積 + 距 MA20(過熱/超賣判讀)
            p5d = s.get("pct_5d")
            d20 = s.get("ma20_dist_pct")
            p5d_str = f" 5日{p5d:+5.2f}%" if p5d is not None else " 5日-"
            d20_str = f" MA20{d20:+5.2f}%" if d20 is not None else " MA20-"
            rows.append(
                f"{s['code']} {s['name']:<6} 收{s['close']:>8} "
                f"日{s['day_pct']:+5.2f}% 月{s['month_pct']:+6.2f}%{p5d_str}{d20_str} "
                f"量比{(str(s['vol_ratio']) if s['vol_ratio'] else '-'):>5} "
                f"外資{s['foreign_lot']:+8.0f}張 "
                f"投信{s['invest_lot']:+6.0f}張 "
                f"自營{s['dealer_lot']:+6.0f}張 "
                f"總{s['total_lot']:+8.0f}張 | "
                f"30日外資{s.get('foreign_30d_lot',0):+8.0f}張 "
                f"30日投信{s.get('invest_30d_lot',0):+6.0f}張 |{mcap_str}{rev_str}{mh_str} {s['desc']}"
            )
        tw0050_block = "\n".join(rows)
    else:
        tw0050_block = "（資料抓取失敗）"

    # 籌碼悄悄站隊 Top 15(法人連買 + 大戶 WoW + 量縮收紅 綜合分數)
    if tw0050:
        scored = [s for s in tw0050
                  if (s.get("smart_money") or {}).get("score", 0) >= 40]
        scored.sort(key=lambda x: (x.get("smart_money") or {}).get("score", 0),
                    reverse=True)
        smart_top = scored[:15]
        if smart_top:
            sm_rows = []
            for s in smart_top:
                sm = s.get("smart_money") or {}
                score = sm.get("score", 0)
                tags = sm.get("tags", []) or []
                fs = s.get("foreign_streak", 0) or 0
                is_ = s.get("invest_streak", 0) or 0
                wow = s.get("tdcc_wow_pct")
                vr20 = s.get("vol_ratio_20d")
                mc = s.get("margin_change_lot")
                wow_str = f"{wow:+.2f}%" if wow is not None else "-"
                vr20_str = f"{vr20:.2f}x" if vr20 else "-"
                mc_str = f"{mc:+,.0f}張" if mc is not None else "-"
                p5 = s.get("pct_5d")
                p5_str = f"{p5:+.1f}%" if p5 is not None else "-"
                tag_str = " | ".join(tags) if tags else "—"
                sm_rows.append(
                    f"{s['code']} {s['name']:<6} 分數={score:>3} | "
                    f"外連{fs:+d} 投連{is_:+d} | 大戶ΔWoW {wow_str} | "
                    f"量比20d {vr20_str} | 5日{p5_str} | 融資Δ{mc_str} | "
                    f"訊號: {tag_str}"
                )
            smart_money_block = "\n".join(sm_rows)
        else:
            smart_money_block = "（今日無 ≥40 分的籌碼站隊候選;TDCC WoW 通常需累積 ≥ 1 週歷史才有 Δ%）"
    else:
        smart_money_block = "（資料抓取失敗,跳過籌碼站隊清單）"

    # 總經指標摘要（含 252 日百分位）
    macro = quotes.get("MACRO", {}) or {}
    def fmt_m(name: str) -> str:
        m = macro.get(name, {})
        if "error" in m or not m.get("close"):
            return f"{name}=資料缺失"
        rank = m.get("pct_rank_252d")
        rank_str = f", 1Y百分位 {rank:.0f}%" if rank is not None else ""
        return (f"{name}={m['close']} ({m.get('change_pct',0):+.2f}%{rank_str})")
    macro_block = "\n".join(
        [f"  {fmt_m(n)}" for n in
         ["VIX", "VIX9D", "SOX", "10Y", "DXY", "13W", "N225", "SSE",
          "NQ", "ES", "WTI", "GOLD"]])
    # 殖利率曲線 10Y − 13W 利差（由已抓資料推導，倒掛為衰退領先訊號）
    ten_y = macro.get("10Y", {}) or {}
    thirteen_w = macro.get("13W", {}) or {}
    if ten_y.get("close") is not None and thirteen_w.get("close") is not None:
        spread = ten_y["close"] - thirteen_w["close"]
        macro_block += (f"\n  殖利率曲線 10Y−13W 利差 = {spread:+.2f} 個百分點"
                        f"（負值=倒掛，衰退領先訊號；轉正回升=景氣回溫訊號）")
    # VIX 期限結構（VIX9D vs VIX）
    vix_term = macro.get("VIX_TERM") or {}
    if vix_term.get("ratio") is not None:
        macro_block += (f"\n  VIX 期限結構 VIX9D/VIX = {vix_term['ratio']:.3f}"
                        f"（{vix_term.get('state','')}）"
                        f"——backwardation(>1.0)=短期恐慌升溫,偏空訊號;contango(<1.0)=正常")

    # 大盤量能 + 廣度
    breadth = quotes.get("BREADTH", {}) or {}
    if breadth.get("total"):
        breadth_block = (
            f"  成交金額: {breadth.get('total_value_yi',0):,.0f} 億新台幣\n"
            f"  上漲: {breadth.get('advance',0)} 檔・下跌: {breadth.get('decline',0)} 檔・"
            f"平盤: {breadth.get('unchanged',0)} 檔（共 {breadth.get('total',0)} 檔）\n"
            f"  上漲家數佔比: {breadth.get('advance_ratio',0):.1f}%"
            f"（{breadth.get('breadth_state','neutral')}）\n"
            f"  ※ ≥ 60% 普漲、≤ 40% 普跌;若指數漲但廣度低 = 少數權值股撐盤,健康度差。"
        )
    else:
        breadth_block = "（大盤廣度資料抓取失敗）"

    # SEC 8-K 公告區塊（Task C）
    sec_filings = quotes.get("SEC_FILINGS", []) or []
    if sec_filings:
        sec_block = "\n".join(
            f"- {f['company']} [{f['form']} {f['date']}] {' / '.join(f['items'])}"
            for f in sec_filings[:25]
        )
    else:
        sec_block = "（過去 48 小時無重大 8-K 公告）"

    # 台股前 10 大公司 MOPS 重大訊息
    tw_mops = quotes.get("TW_MOPS", []) or []
    if tw_mops:
        mops_block = "\n".join(
            f"- {m.get('code','')} {m.get('title','')[:80]}"
            for m in tw_mops[:20]
        )
    else:
        mops_block = "（過去 48 小時無前 10 大公司 MOPS 重大訊息，或來源暫不可用）"

    # TAIFEX 外資台指期未平倉（Task E）
    taifex = quotes.get("TAIFEX_OI", {}) or {}
    if taifex.get("foreign_oi_net") is not None:
        taifex_block = (
            f"  日期: {taifex.get('date','—')}\n"
            f"  外資台指期未平倉淨額: {taifex.get('foreign_oi_net',0):+d} 口"
            f"（正=偏多、負=偏空，>±2 萬口為強訊號）\n"
            f"  投信淨額: {taifex.get('invest_oi_net',0):+d} 口\n"
            f"  自營商淨額: {taifex.get('dealer_oi_net',0):+d} 口"
        )
    else:
        taifex_block = "（TAIFEX 資料抓取失敗或未更新）"

    # Opt 4: 融資融券 block
    margin = quotes.get("MARGIN", {}) or {}
    if margin.get("margin_balance"):
        margin_block = f"  日期: {margin.get('date','—')}，融資餘額: {margin['margin_balance']:,} 千元（變動需與外資籌碼交叉判讀）"
    else:
        margin_block = "（融資融券資料抓取失敗）"

    # Opt 6: 一週動能 block
    weekly = quotes.get("WEEKLY", {}) or {}
    if weekly:
        weekly_rows = []
        for k in ["QQQ", "TSM", "SPY", "VIX", "SOX", "DXY", "00662", "2330"]:
            w = weekly.get(k)
            if not w:
                continue
            weekly_rows.append(f"  {k}: 5日累積 {w.get('pct_5d',0):+.2f}% (前 {w.get('five_days_ago')} → 現 {w.get('last')})")
        weekly_block = "\n".join(weekly_rows) if weekly_rows else "（資料不足）"
    else:
        weekly_block = "（一週動能資料不足）"

    # Opt 7: 法說會 block
    earn = quotes.get("EARNINGS_PROXIMITY", {}) or {}
    if earn:
        earnings_block = (
            f"  下次法說會日期: {earn.get('closest_date','—')}（距今 {earn.get('days_to','?')} 天）\n"
            f"  影響等級: {earn.get('impact','?')}\n"
            f"  說明: {earn.get('note','')}"
        )
    else:
        earnings_block = "（法說會資料缺失）"

    # Opt 1: 歷史記憶 block
    history = quotes.get("HISTORY", []) or []
    if history:
        # 安全格式化：歷史 entry 的任一欄位若是 None（前一天抓取失敗會這樣存），
        # 直接用 f-string 的格式 spec（如 :+）會炸 TypeError，需各別防護。
        def _fmt(v, default="?"):
            return default if v is None else v

        def _fmt_signed(v, suffix="", default="?"):
            if isinstance(v, (int, float)):
                return f"{int(v):+,d}{suffix}"
            return default

        h_rows = []
        for h in history[-7:]:
            crit = " / ".join(h.get("critical_news", [])[:2])
            h_rows.append(
                f"  {_fmt(h.get('date'))} ({_fmt(h.get('weekday'))}): "
                f"QQQ {_fmt(h.get('qqq_pct'))}% / TSM {_fmt(h.get('tsm_pct'))}% / "
                f"VIX {_fmt(h.get('vix'))} / "
                f"外資台指期 {_fmt_signed(h.get('taifex_foreign_oi'), ' 口', '資料缺失')} / "
                f"重大事件: {crit[:80] if crit else '無'}"
            )
        history_block = "\n".join(h_rows)
    else:
        history_block = "（首次運行，尚無歷史記憶；明日起會累積）"

    # Task B: 夜盤台指期 block
    night = quotes.get("NIGHT_TXF", {}) or {}
    if night.get("night_pct") is not None:
        night_block = (
            f"  日期: {night.get('date','—')}\n"
            f"  日盤收盤: {night.get('day_close')} → 夜盤收盤: {night.get('night_close')}\n"
            f"  夜盤漲跌: {night['night_pct']:+.2f}% （直接反映外資對今日台股開盤預期）"
        )
    else:
        night_block = "（夜盤資料抓取失敗或尚未更新）"

    # Task A: 加權指數預測 block
    pred = quotes.get("TAIEX_PRED", {}) or {}
    if pred.get("pred_open"):
        signals_str = " | ".join(
            f"{s['name']} {s['value']:+.2f}%(w={s['weight']:.0%})"
            for s in pred.get("signals", [])
        )
        taiex_pred_block = (
            f"  加權指數昨收: {pred['last_close']}\n"
            f"  訊號: {signals_str}\n"
            f"  加權預測漲跌: {pred['weighted_pct']:+.2f}%\n"
            f"  ★ 預測開盤點位: {pred['pred_open']} （信心區間 {pred['ci_lower']} ~ {pred['ci_upper']}）\n"
            f"  訊號共識: {pred['consensus']}（標準差 {pred.get('signal_std','—')}）\n"
            f"  自我校正: {_calibration_note(pred)}"
        )
    else:
        taiex_pred_block = "（資料不足，無法預測大盤）"

    # Task F: 預測回溯 block
    backtest_block = quotes.get("BACKTEST", "（無回溯資料）") or "（無回溯資料）"

    # Task H: 警告 block
    alerts_list = quotes.get("ALERTS", []) or []
    if alerts_list:
        alerts_block = "\n".join(
            f"  [{a['level'].upper()}] {a['title']}: {a['detail']}"
            for a in alerts_list
        )
    else:
        alerts_block = "（昨日市場無重大過熱/恐慌訊號）"

    # 美股休市旗標 block（單獨拉出來,確保 LLM 一定看到、必須套用 R13）
    us_hol = quotes.get("US_HOLIDAY") or {}
    if us_hol.get("detected"):
        us_holiday_block = (
            f"⚠ 美股昨日休市偵測:US 最新收盤 = {us_hol.get('actual_date')}"
            f"({us_hol.get('actual_weekday')}),距今日預期 US 交易日"
            f" {us_hol.get('expected_date')} 相差 {us_hol.get('gap_days')} 個工作天。\n"
            f"→ 所有美股資料(QQQ/TSM/SOX/VIX/VIX9D/NQ/ES/WTI/黃金/10Y/DXY/13W)為**延續值**,不是昨日新資訊。\n"
            f"→ 立場評分中所有美股維度**必須給 0 分並標 [stale]**(見 R13 鐵律),信心等級強制改「低」。"
        )
    else:
        us_holiday_block = "（美股昨日正常開盤,所有美股資料為昨日新資訊。）"

    # 資料品質 block（讓 LLM 知道哪些來源失敗，禁止據此腦補）
    dq_list = quotes.get("DATA_QUALITY", []) or []
    if dq_list:
        dq_block = "\n".join(
            f"  [{d['status'].upper()}] {d['name']}：{d.get('detail', '')}"
            for d in dq_list
        )
    else:
        dq_block = "（未提供資料品質資訊）"

    return f"""你是嚴謹但敢於下判斷的科技股財經分析師。為一位重押 00662（NASDAQ-100）與 2330（台積電）的台灣投資人寫晨報。

【資料品質（最優先閱讀）】
{dq_block}
※ status=OK：資料正常，可正常引用。
※ status=FALLBACK：降級資料（樣本不足或部分來源失敗），引用時須說明「資料有限」。
※ status=ERROR：該來源今日抓取失敗 ＝「資料未提供」。對應段落必須明寫「資料未提供」，
   嚴禁腦補、嚴禁編造數字 / 新聞 / 法人買賣超 / 公司財報。寧可少寫，不可瞎掰。

【昨日美股收盤】
- QQQ：{quotes['QQQ']}
- TSM (台積電 ADR)：{quotes['TSM']}
- SPY：{quotes['SPY']}
- USD/TWD：今 {quotes.get('USDTWD')} / 昨 {quotes.get('USDTWD_prev')}

【總經指標（昨日收盤值、變動%、252 日歷史百分位）】
{macro_block}

判讀規則：
- VIX < 15 樂觀、15-20 中性、20-25 警戒、>25 恐慌
- 百分位 < 30% 為低檔（偏多訊號）、> 70% 為高檔（偏空訊號）
- SOX 與 2330 高度連動（β≈1.1），SOX 是最重要的單一指標
- 10Y 殖利率上升 → 成長股估值壓力（折現率↑）
- DXY 升 → 美元強 → 新興市場資金流出
- 13W (3M 國庫券) 殖利率變動反映 Fed 短期利率預期
- N225 (日經 225) 與台股同屬亞股、開盤時間相近，是台股開盤情緒的同步參考
- SSE (上證綜指) 反映中國盤面，影響台股資金面與兩岸題材；中國重挫常壓抑台股風險偏好
- 殖利率曲線倒掛（10Y−13W 為負）是經典衰退領先訊號；由負轉正回升則為景氣回溫訊號
- **NQ 期貨**（NQ=F）反映美股收盤後到 TW 開盤之間的「夜盤美股」變動。NQ > 0 表示 US 收盤後資金續強、會帶動 TW 開高;NQ < 0 反向。是美股 cash market 已收後最重要的領先訊號之一。
- **ES 期貨**（ES=F）同 NQ，反映 S&P 廣度。若 NQ 與 ES 同向 → 訊號確認;若分歧（如 NQ 漲、ES 跌）→ 純粹 AI/半導體題材在帶,而非市場整體
- **VIX9D vs VIX 期限結構**：VIX9D > VIX（backwardation）表示「短期波動率預期高於中期」,等於市場認為「現在很怕,但很快會過去」——對成長股是短線偏空訊號;VIX9D < VIX（contango,正常）= 中性。
- **WTI 原油**單日 > +3% = 通膨壓力訊號（壓抑 Fed 寬鬆預期）→ 偏空成長股;< -3% = 減壓 → 偏多。地緣戰爭風險升溫常推升油價。
- **黃金**急漲（單日 > +2%）= 系統性避險升溫,通常伴隨美元走弱與股市修正

【SEC 8-K 主要公司公告（近 48 小時，涵蓋 NASDAQ-100 + TSMC ADR）】
{sec_block}
※ 8-K Item 1.01=重大協議、2.02=財報、5.02=高層異動、8.01=其他重大事件

【台股市值前 10 大公司 MOPS 重大訊息（近 48 小時）】
{mops_block}
※ MOPS（公開資訊觀測站）是台灣上市公司法定即時揭露的重大訊息來源；任何具體事件（合約、財報、人事、配股、訴訟）都會在此公告

【TAIFEX 三大法人台指期未平倉（領先指標）】
{taifex_block}
※ 外資台指期未平倉是「外資對今日台股方向的最直接表態」，比現貨買賣超更領先

【TWSE 融資融券（散戶情緒，Opt 4）】
{margin_block}
※ 融資增加=散戶積極做多（過熱反向指標）；融券增加=散戶看空（軋空反向指標）
※ 與外資籌碼背離時為強訊號：外資買+散戶賣=強多 / 外資賣+散戶買=強空

【一週動能對比（Opt 6）】
{weekly_block}
※ 看 5 日累積漲跌幅，判斷昨日是「延續」或「逆轉」

【2330 法說會狀態（Opt 7）】
{earnings_block}
※ 法說會 ±2 天：預測信心降為「低」、走勢可能脫離 ADR 連動、不建議重壓
※ 法說會週：預測信心略降

【歷史記憶：過去 7 日（Opt 1）】
{history_block}
※ 看「敘事流」：Fed 是否從鴿轉鷹、外資是否連續買超、川習會議題演進

【夜盤台指期（Task B，最直接領先指標）】
{night_block}
※ 夜盤交易 14:45 - 翌日 05:00。早上跑報時夜盤剛收，是大盤開盤方向的最強訊號。

【加權指數預測（Task A，三訊號加權法）】
{taiex_pred_block}
※ 用 SOX 40% + TSM ADR 30% + 夜盤台指期 30% 加權預測。訊號分歧時信心降低。

【大盤量能與市場廣度（TWSE STOCK_DAY_ALL 統計）】
{breadth_block}

【預測準確度回溯（Task F，自我修正用）】
{backtest_block}
※ 如過去平均誤差偏高（>+0.2%）→ 今日預測應略下修；偏低（<-0.2%）→ 略上修。

【市場警告訊號（Task H）】
{alerts_block}
※ 如有 red 級警告，必須在「我的明確立場」段顯著提及並反映在操作建議中。

【美股交易日狀態（影響全部美股訊號可信度）】
{us_holiday_block}

【今日 00662 估值（Python 已算）】
{fair}
（fair_price 已是「自我校正後」的合理價；calibration 欄位說明校正幅度，fair_price_raw 為校正前原值）

【今日 2330 三模型預測（Python 已算）】
{predictions}
（model3 是 ADR 衰減版，decay_factor 是近 60 日實證係數，越接近 1 代表 2330 跟 ADR 越緊密。
 weighted_final = 依各 model 近期 MAE 反比加權後、再經 bias 自我校正的「最終合理價」，
 應以 weighted_final 為今日 2330 的主要參考；model_mae_pct 是各模型近期平均絕對誤差。
 calibration.applied=true 代表已用歷史偏誤修正，false 代表樣本仍在累積、暫用未校正值。）

【歷史校準資料】
{calibration}

【近 24-30 小時新聞清單（含國際財經、Fed、台灣財經、政府政策）】
{news_block}

【台股市值前 100 大昨日表現 + 三大法人買賣超 + 30日累積法人（張，正為買超）+ 月營收年增率 + 大戶持股 + 5日動能 + 距 MA20】
{tw0050_block}
※「營收YoY」為該公司最新月營收的去年同月年增率（真實數據，TWSE 月營收彙總）；「-」代表無資料
※「大戶」為持股 ≥ 400 張的大戶占集保總數比例（TDCC 集保股權分散表，週更）；比例高 = 籌碼集中在大戶/主力手上

【★籌碼悄悄站隊 Top 15(分數 ≥ 40，挑「外資/投信偷買 + 大戶 WoW 增加 + 量縮收紅」的股票)】
{smart_money_block}
※ 分數 = 法人連買(40) + 大戶 WoW Δ%(30) + 量縮收紅 / 突破量(20) + 偷買區間(10) + 融資減(5);≥80 強訊號,≥60 站隊,≥40 輕微正向
※ 外/投連買 = 近 5 日法人連續同向天數(正 = 連續買、負 = 連續賣);≥3 天是強訊號
※ 大戶ΔWoW = 本週 TDCC 大戶持股 %（≥400 張）− 對照週(≥5 天前)的同欄;**正值 = 主力默默吸籌**,需累積歷史才有值
※ 量比20d &lt; 0.8 + 法人買 + 收紅 = **「量縮鎖籌」經典偷買訊號**(沒人注意但籌碼集中)
※ 量比20d &gt; 2.0 + 收紅 + 法人賣 = **「散戶接刀」警示**(扣分)
※ 此分數**為輔助參考、不取代基本面與消息面分析**;選股時仍以營收 / 催化消息 / 結構健康為主、此清單為「籌碼面 second opinion」

═══════════════════════════════════════════════════════════
# 寫作鐵律（必讀，違反任一條都是失敗報告）
═══════════════════════════════════════════════════════════

R1. **零客套**：不寫「親愛的投資人」「以下是」「希望這份報告有幫助」這類話
R2. **必須單一立場**：禁止「樂觀/中性/悲觀」三選一並陳，必須選邊
R3. **每個論點必附數據**：禁止「市場樂觀」「資金充沛」這種空話。改寫成「VIX 13.2 處低檔、外資 30 日累積買超 2330 共 42,300 張」
R4. **公司名必附簡介**：「**AMD（全球第二大 x86 CPU + AI GPU 廠，MI300X 為主力）**」
R5. **估值若 None/nan 直接寫「資料缺失」**，不可瞎掰
R6. **每條只寫一件事**：避免一句話塞三個論點
R7. **嚴禁 emoji**：包括 ✅ ❌ 📈 等所有圖示
R8. **嚴禁使用技術面術語**：不可提 K 線、均線、MACD、KD、RSI、黃金交叉、死亡交叉、布林通道
R9. **不可用全形冒號之外的全形標點**（書名號、感嘆號除外）
R10. **繁體中文，台灣財經用語**：寫「漲跌幅」不寫「涨幅」，寫「成交量」不寫「成交额」
R11. **重大地緣政治事件強制分析**：若上方新聞清單的 ★★★ 重大事件中出現 [geo_critical] 類別（川習會、台海、晶片出口管制、軍演、戰爭等），**必須**在「昨夜三大重點」**且**「總體經濟與政策環境 (C)」段明確點名該事件、引用新聞中的具體內容（人物、發言、數字），並分析其對 2330 / 00662 / 台股開盤的傳導影響。**禁止省略、禁止只用一句話帶過**。若清單中確實沒有此類事件，才可略過。
R12. **個股動態以「具體事實 + 透明標記」為原則**:「科技板塊脈動」與「今日台股關注三檔」每一條敘述,**優先用具體事實**(明確產品/合約/數字/法說發言/SEC 表單編號 / MOPS 公告)。
- **A 級(有具體事實)**:照寫,信心可給「中-高」。範例:「Broadcom 宣布 Anthropic 80 億美元 ASIC 合約,盤後 +4.5%」
- **B 級(只有方向性訊號,如分析師喊買 / 動能標題 / 法人買超)**:**可寫,但須明確標註「資訊有限」並降為「低-中」信心**。範例:「NVIDIA 昨日外資買超 12,000 張(籌碼面正向,但今日無具體公司消息,信心:中-低)」
- **C 級(只有「揭露意外真相」「迎來轉折」「市場關注」這類沒內容的標題)**:不要寫。
- **重點:不要把 B 級當 C 級砍掉** — 籌碼 / 分析師動向也是有用的訊號,只是要透明標記。
- 輸出前自我檢查:每句話的「資訊強度」(A/B 級)是否與信心等級相符;若寫了 B 級卻給高信心 = 失敗報告。
R13. **美股休市日 → 美股訊號必須標 stale 給 0 分**:若【市場警告】中出現「美股昨日休市」警告,代表 QQQ/TSM/SOX/VIX/VIX9D/NQ/ES/WTI/黃金/10Y/DXY/13W 全部都是**上一個美股交易日的延續值,不是昨日新資訊**。在「我的明確立場」段的 11 維加減分中:
- 所有美股相關維度(QQQ/SOX/VIX/TSM ADR/NQ/VIX9D/WTI/10Y)的分數**強制給 0**,並在該維度後加 `[stale]` 標籤
- 僅信任 TW 本地維度(外資 0050 前 10、外資台指期、市場廣度)
- 信心等級**強制改為「低」**,「我的明確立場」段的理由必須首句明寫「**今日美股休市,美股訊號 stale**」
- 預測模型仍會跑但「2330/00662/加權」的開盤關鍵價位建議寬度應加大 (±1.5% 而非 ±1%)
違反此規則 = 失敗報告。

═══════════════════════════════════════════════════════════
# 分析框架（按此順序在腦中執行，但不寫進報告）
═══════════════════════════════════════════════════════════

## A. 籌碼面三步驗證
**步驟 1：外資方向**
- 昨日外資 + 30日累積外資都正 → 強多（外資中長線看多）
- 昨日正 + 30日負 → 短彈（不可信，逢高賣壓）
- 昨日負 + 30日正 → 中期支撐仍在
- 都負 → 強空（避開）

**步驟 2：投信跟風**
- 投信跟外資同方向 → 確認訊號（強度加倍）
- 投信反向 → 訊號減弱

**步驟 3：規模門檻**
- 外資+投信合計 < 3000 張 → 籌碼面**無明確訊號**，當沒看到
- 外資+投信合計 > 10000 張 → 強訊號
- 外資+投信合計 > 30000 張 → 主力強力進駐

## B. 總經連動五規則
**規則 1**：SOX 漲 > 1.5% + QQQ 漲 > 1% → 2330 開高機率 ≥ 70%
**規則 2**：SOX 跌 > 2% → 2330 開低機率 ≥ 80%（即使 TSM ADR 紅也通常開低）
**規則 3**：VIX > 20 + DXY 升 + 10Y 升 → 三殺成長股，避免重壓 00662
**規則 4**：13W 殖利率明顯下降 → 降息預期升溫，有利成長股
**規則 5**：DXY 升 0.5% 以上 → 外資匯出壓力，台股當日易現賣壓

## C. 立場判斷 11 維加減分（強制執行）

**原 7 維**：
1. QQQ 漲幅 > 0.5%: +1；< -0.5%: -1
2. SOX 漲幅 > 1%: +1；< -1%: -1
3. VIX < 18 或百分位 < 30%: +1；> 22 或百分位 > 70%: -1
4. TSM ADR 漲幅 > 0%: +1；< 0%: -1
5. 外資 0050 前 10 大昨日合計買超 > 0: +1；< 0: -1
6. 外資台指期未平倉 > +5000 口: +1; < -5000 口: -1; 否則 0
7. 10Y 殖利率變動 < -2 bps (降息預期升溫): +1; > +2 bps: -1

**新增 4 維（市場機制訊號）**：
8. **NQ 期貨**單日 > +0.5%: +1；< -0.5%: -1（補美股盤後到 TW 開盤之間的訊號）
9. **VIX 期限結構**：backwardation (VIX9D/VIX > 1.0) = -1（短期恐慌升溫）；contango = 0
10. **WTI 油價**單日 > +3%: -1（通膨/Fed 壓力）；< -3%: +1（壓力減）；否則 0
11. **市場廣度**：上漲家數佔比 ≥ 60%（普漲）= +1；≤ 40%（普跌）= -1；其他 0

**判斷規則（11 維新門檻）**：
- 淨分 ≥ +5 → **偏多**
- 淨分 ≤ -5 → **偏空**
- −4 ~ +4 → **中性**（門檻提高是因為訊號變多,需更高一致性才下重判)

**必須在「我的明確立場」段顯式寫出全部 11 個維度的加減分計算過程**。
**禁止憑感覺給分,每個訊號的值必須引用上方資料區塊的真實數字**。

═══════════════════════════════════════════════════════════
# 輸出結構（嚴格按此順序與標題，不可增減段落）
═══════════════════════════════════════════════════════════

## 七、加權指數盤勢推演與關鍵守關

**重要：預測點位、合理區間、訊號共識、訊號值與權重，已由 Python 算好並以表格呈現在 email 中（標題「五、加權指數開盤預測」），你「絕對不可」再重複列出這些數字。** 此段只寫下方 LLM 才能加值的「信心判讀 + 情境推演 + 守關位」，**禁止重述 pred_open / ci_lower / ci_upper / consensus / 訊號百分比**。

格式（**強制**，每行獨立成段）：

> **信心等級**：高 / 中 / 低 — 一句話說明信心來源（用上方 taiex_pred 的 signal_std 判斷：< 0.5% 高、0.5-1.5% 中、> 1.5% 低；亦可考慮夜盤是否與 SOX/ADR 同向）

> **盤勢推演（必須各一句，引用上方 Python 預測點位但不重複數字本身）**：
> - 樂觀情境：若站穩 XXXX 點以上，動能延續至 XXXX 點
> - 中性情境：XXXX ~ XXXX 點區間震盪
> - 悲觀情境：若失守 XXXX 點，下探 XXXX 點

> **關鍵守關**：XXXX 點為今日多空分水嶺（取昨日加權收盤 ± 0.5%）

## 八、昨夜三大重點

**用 3 條 bullet，每條 ≤ 50 字**。
必須涵蓋（按優先序）：
1. **最影響 00662 的事件**（美股科技股 / Fed / 半導體政策）
2. **最影響 2330 的事件**（TSM 動向 / 台積電供應鏈消息 / 半導體出口管制）
3. **第三個總經或地緣風險事件**

每條必須附上**具體數據或來源**（例：「Nvidia 盤後 +2.3% 因 Mag7 ASIC 訂單超預期 [CNBC]」）

## 九、科技板塊脈動（**8–12 條,最多 15 條**;有料就寫滿,沒料 8 條也可)

**重要**:條目數**不再強制壓低**;若新聞清單(含 ★★ 高權重段的全文摘錄)有 12 家公司可寫,就寫到 12 條。R12 已放寬:B 級資訊也可寫但須明確標註信心降級。

每條格式（嚴格遵守）：
**公司中英文名（一句話業務簡介）**：發生什麼（含數字 / 來源）+ 為何重要（對 00662/2330 的傳導）+ **資訊強度(A/B)+ 信心(高/中/低)**

範例 A 級(具體事實):
**Broadcom（AVGO，全球前三大半導體 IP 設計商，主導 AI ASIC 客製晶片）**：宣布獲 Anthropic 80 億美元算力訂單，AVGO 盤後 +4.5%。為 2330 先進製程訂單能見度再添確認（CoWoS 2026 產能持續吃緊）。**[A 級・信心:高]**

範例 B 級(只有方向性訊號):
**NVIDIA(NVDA,GPU/AI 加速器龍頭)**:無重大公司消息,但鉅亨頭條提及「華爾街上修目標價」(分析師動向、無原始數字);盤後 +0.8%。對 2330 影響中性偏正。**[B 級・信心:中-低,資訊有限]**

## 十、總體經濟與政策環境

分三小段（每段 3-5 句，禁止超過）：

**(A) 美國利率/美元/VIX/通膨**：
列出 VIX、10Y、DXY、SOX 的**昨日收盤值與變動%**（用上方資料）。如有 CPI/PPI/就業數據釋出，必列數字。

**(B) Fed/美國政府重大政策**：
FOMC 紀要、Fed 官員談話、白宮對中政策、半導體出口管制等。**明確寫出對台灣科技業的影響**。

**(C) 重大地緣政治與全球政策**：
日本央行、ECB、中國刺激政策等。**若新聞清單有 [geo_critical] 事件（川習會、台海、晶片出口管制、軍演、戰爭），此段必寫，且須：(1) 點名事件 (2) 引用新聞具體內容 (3) 明確分析對 2330 / 00662 / 台股的影響與風險**——這是 R11 鐵律，違反即為失敗報告。確實無此類事件才寫「無重大地緣事件」。

## 十一、台灣本地動態（必寫，不可略）

聚焦昨日對台灣資本市場有影響的事：
- 台灣央行 / 金管會動向
- 台積電供應鏈動態（艾司摩爾、東京威力、SUMCO、信驊、力旺等）
- 台灣總經數據（出口、外銷訂單、CPI）
- 政府政策（產創條例、科專、台美 21 世紀貿易倡議等）

若新聞清單中沒有相關內容，**直接寫「昨日無重大本地新聞」**，不要編造。

## 十二、我的明確立場（**最重要段**）

**第 1 行 — 11 維加減分計算**（強制顯示全部 11 維，不可省略,不可憑感覺給分):
```
QQQ X.X% [±1/0]、SOX X.X% [±1/0]、VIX X [±1/0]、TSM ADR X.X% [±1/0]、外資 0050前10合計 [±1/0]、外資台指期 [±1/0]、10Y X bps [±1/0]、NQ X.X% [±1/0]、VIX9D/VIX X.XX [±1/0]、WTI X.X% [±1/0]、市場廣度 X% [±1/0] = 淨分 X
```

**第 2 行 — 立場標籤**：
> **立場：偏多 / 偏空 / 中性**（按淨分自動判定）

**第 3 行 — 理由（3-5 句）**：說明為什麼是這個立場，每句必附數據。

**第 4-6 行**（**每行獨立成段，中間空行**）：

> **2330 開盤關鍵價位**：守穩 XXX 元為強，跌破 XXX 元轉弱（用三模型預測中位數 ± 1% 為觀察價位）

> **00662 操作建議**：明確寫「加碼 / 觀望 / 減碼」，並給具體價位（例：「合理估值 116.5 元，若開盤 < 116 元可加碼」）

> **主要風險**：1 句話點出最可能讓今日預測失效的單一事件

## 十三、今日台股關注三檔（**必寫，從上方「台股市值前 100 大」清單中選**）

**選股優先序**（必須符合至少 3 項，**包含「結構健康」一項**;**強烈優先「籌碼悄悄站隊」清單中的個股**）：
1. 昨日法人買超強（外資 + 投信合計 > +5000 張）**或上方「籌碼悄悄站隊」分數 ≥ 60**
2. 30 日累積外資買超 > 0 且加速
3. 新聞清單有具體催化消息（不是空話）
4. 業務直接受惠當前主軸（AI 算力 / 半導體 / 電動車 / 散熱 / 記憶體）
5. 月營收年增率（營收YoY）正成長，最好 > +10%（用上表「營收YoY」欄位，禁止瞎掰）
6. 大戶持股比例偏高或結構穩固（用上表「大戶」欄位）;**若上方有大戶ΔWoW ≥ +0.3%（主力默默吸籌），加重權重**
7. **【新】結構健康度標籤**(用上表「5日」「MA20」兩欄判讀,作為**信心評等與風險警示**的依據,**不是排除條件**):
   - **健康上行**：`5日 +1~+5%` 且 `MA20 +0~+5%`(理想,信心可給「高」)
   - **強勢但偏熱**：`5日 +5~+10%` 或 `MA20 +5~+10%`(信心降為「中」,挑選理由須註明追高風險)
   - **暴衝過熱**：`5日 > +10%` 或 `MA20 > +10%`(信心降為「低」,目標關注幅度收窄至 ±3% 以內,挑選理由須明寫「短期暴衝、追高風險高、僅適合短線」)
   - **超賣反彈候選**：`5日 < -5%` 且 `MA20 < -3%`(信心「中-低」,須有止跌訊號才入榜)

**選股禁止事項**：
- 不可用技術面分析（K 線、均線、MACD 等;但 5日/MA20 統計數字可用,當作「結構強弱」描述,**不是技術分析,而是趨勢健康度**)
- **只能選上方「台股市值前 100 大」清單裡有列出的股票**，不可選清單外或杜撰代號
- 不可只看「昨日漲幅」就選，**漲停只是參考，籌碼、消息、營收成長才是主因**
- 營收 YoY 大幅衰退（< −15%）的個股，除非有極強催化消息，否則不選
- **暴衝股可選但必須誠實標示**：若 5日 > +10%,信心一律給「低」,目標幅度 ±3% 以內,並在挑選理由首句寫「短期累積漲幅過大,追高風險高」。不可掩飾為「強勢買進」。
- 若三檔都信心低，**照寫,不要勉強拉高**

每檔用 **### 代號 公司名** 作為三級標題（例：`### 2330 台積電`），下方接以下 6 個 bullet：

- **業務簡介**：1-2 句，這間公司在做什麼 + 主力產品
- **近期營收/獲利動向**：**優先引用上表「營收YoY」的真實月營收年增率數字**；可再補法說會重點（先驗知識），但月營收以上表為準
- **昨日法人動向**：外資 X 張、投信 X 張、自營 X 張（**精準引用上表數字**）；30 日累積外資 X 張（**強度判讀**）;**若此檔在「籌碼悄悄站隊」清單中,額外寫出分數、外/投連買天數、大戶ΔWoW、量比20d 4 項數據**
- **挑選理由**：消息催化 + 籌碼結構 + 基本面定位（三者都要提）
- **信心等級**：高 / 中 / 低（**禁止省略**；說明信心來自哪一面）;**若入榜「籌碼悄悄站隊」且分數 ≥ 60,信心等級可提升一級**
- **目標關注幅度**：給合理區間（例「漲幅 2-4%」），**不可超過 ±10%**

第三檔分析完後**獨立成段**寫風險警示：
> 以上分析基於昨日法人籌碼與新聞消息推論，實際走勢受開盤瞬間外資掛單、突發新聞、台美匯率波動影響，僅供參考不構成投資建議。

## 十四、一句話總結

20 字內。給一句**具體可執行**的結論（含立場 + 動作）。

範例：「偏多操作 00662，2330 守穩 1180 元逢回加碼」
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
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def _call_deepseek(prompt: str) -> str:
    """
    DeepSeek API (OpenAI 相容 chat completions 介面)。
    支援重試與降級：deepseek-chat → deepseek-reasoner。
    每月成本估算（22 次/月、5000 tokens 輸入、3500 輸出）：
      - deepseek-chat (V3.2/V4 Flash): 約 NT$1-3
      - deepseek-reasoner (V4 Pro):   約 NT$4-6
    """
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("缺 DEEPSEEK_API_KEY 環境變數")

    # 模型降級鏈：主模型不穩時依序往下試
    # v4-pro (旗艦) → v4-flash (輕量) → deepseek-chat (V3.2 一般，最穩)
    fallback_models = [DEEPSEEK_MODEL]
    for alt in ("deepseek-v4-flash", "deepseek-chat"):
        if alt not in fallback_models:
            fallback_models.append(alt)

    last_err: Optional[Exception] = None
    for model in fallback_models:
        for attempt in range(1, 4):
            try:
                print(f"[llm] 嘗試 DeepSeek model={model} attempt={attempt}")
                url = f"{DEEPSEEK_BASE_URL}/v1/chat/completions"
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 4000,
                    "stream": False,
                }
                # v4-pro / reasoner 支援思考模式：開啟可顯著提升分析推理深度
                if (DEEPSEEK_REASONING_EFFORT not in ("", "off", "none", "disabled")
                        and ("pro" in model or "reasoner" in model)):
                    payload["thinking"] = {"type": "enabled"}
                    payload["reasoning_effort"] = DEEPSEEK_REASONING_EFFORT
                    print(f"[llm] DeepSeek 思考模式啟用 (reasoning_effort={DEEPSEEK_REASONING_EFFORT})")
                headers = {
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                }
                r = requests.post(url, json=payload, headers=headers, timeout=120)
                r.raise_for_status()
                data = r.json()
                choices = data.get("choices") or []
                if not choices:
                    raise RuntimeError(f"DeepSeek 回應無 choices: {data}")
                content = choices[0].get("message", {}).get("content")
                if not content:
                    raise RuntimeError(f"DeepSeek 回應無 content: {data}")
                # 記錄成本（usage 通常含 prompt_tokens / completion_tokens / 快取資訊）
                usage = data.get("usage", {})
                print(f"[llm] DeepSeek 成功 — tokens: prompt={usage.get('prompt_tokens')} "
                      f"completion={usage.get('completion_tokens')} "
                      f"cache_hit={usage.get('prompt_cache_hit_tokens', 0)}")
                return content
            except requests.exceptions.HTTPError as e:
                code = e.response.status_code if e.response is not None else None
                last_err = e
                if code in RETRY_STATUS_CODES and attempt < 3:
                    wait = 5 * (3 ** (attempt - 1))
                    print(f"[llm] DeepSeek HTTP {code}，{wait}s 後重試", file=sys.stderr)
                    time.sleep(wait)
                    continue
                print(f"[llm] DeepSeek {model} 最終失敗: {e}", file=sys.stderr)
                break
            except Exception as e:
                last_err = e
                print(f"[llm] DeepSeek {model} 異常: {e}", file=sys.stderr)
                if attempt < 3:
                    time.sleep(5)
                    continue
                break
    raise RuntimeError(f"DeepSeek 所有模型皆失敗: {last_err}")


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
                       news: list[dict], tw0050: list[dict] | None = None,
                       calibration: str = "") -> str:
    """根據 LLM_PROVIDER 環境變數選擇 LLM。預設 gemini。任何環節失敗都回傳備援文字而非 raise，
    確保 main() 一定能寄出基本版晨報。"""
    try:
        prompt = _build_prompt(quotes, fair, predictions, news, tw0050 or [], calibration)
    except Exception as e:
        # prompt 組裝崩了（例：歷史記憶欄位格式化錯誤）—— 仍寄信，但用備援文字
        print(f"[llm] prompt 組裝失敗，改用備援文字: {type(e).__name__}: {e}", file=sys.stderr)
        return _fallback_analysis_text(news, e)
    try:
        if LLM_PROVIDER == "anthropic":
            return _call_anthropic(prompt)
        if LLM_PROVIDER == "deepseek":
            return _call_deepseek(prompt)
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
        # H2（章節標題：四、五、六、七、八、九、十）— 同首頁三大區塊風格
        ("<h2>", "<h2 style=\"color:#0f172a;font-size:21px;font-weight:700;margin:36px 0 14px;padding:10px 16px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;letter-spacing:0.5px;\">"),
        # H3（個股代號 + 公司名）— 大字 + 漸層背景
        ("<h3>", "<h3 style=\"color:#92400e;font-size:19px;font-weight:700;margin:22px 0 12px;padding:10px 14px;background:linear-gradient(90deg,#fef3c7,#fde68a);border-radius:6px;border-left:4px solid #f59e0b;\">"),
        # H4
        ("<h4>", "<h4 style=\"color:#0c4a6e;font-size:16px;font-weight:700;margin:20px 0 8px;\">"),
        ("<h1>", "<h1 style=\"color:#0f172a;font-size:24px;margin:24px 0 12px;\">"),
        # 段落
        ("<p>", "<p style=\"margin:14px 0;line-height:1.95;color:#1f2937;font-size:15px;\">"),
        # 列表
        ("<ul>", "<ul style=\"margin:14px 0 18px;padding-left:24px;line-height:1.95;color:#1f2937;font-size:15px;\">"),
        ("<ol>", "<ol style=\"margin:14px 0 18px;padding-left:24px;line-height:1.95;color:#1f2937;font-size:15px;\">"),
        ("<li>", "<li style=\"margin:8px 0;padding-left:4px;\">"),
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
    def card_repl(m: "re.Match[str]") -> str:
        block = m.group(0)
        return ("<div style=\"background:#ffffff;border:1px solid #fcd34d;border-radius:10px;"
                "padding:18px 22px;margin:18px 0;box-shadow:0 2px 6px rgba(245,158,11,0.12);\">"
                + block + "</div>")

    pattern = re.compile(r"<h3[^>]*>.*?(?=<h3|$)", re.DOTALL)
    mid_cards = pattern.sub(card_repl, mid)

    box = ("<div style=\"background:#fffbeb;border:2px solid #f59e0b;border-radius:14px;"
           "padding:22px 24px;margin:28px 0;\">"
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
           "border:2px solid #0284c7;border-radius:14px;"
           "padding:22px 26px;margin:28px 0;box-shadow:0 2px 8px rgba(2,132,199,0.10);\">"
           + mid + "</div>")
    return pre + box + post


def _calibration_note(obj: dict) -> str:
    """把 calibration 欄位轉成一句人類可讀說明（純文字，render 與 prompt 共用）。"""
    if not isinstance(obj, dict):
        return ""
    cal = obj.get("calibration")
    if not isinstance(cal, dict):
        return ""
    if cal.get("applied"):
        b = cal.get("bias_pct", 0) or 0
        sign = "+" if b >= 0 else ""
        return (f"已自我校正（近 {cal.get('samples')} 日平均偏誤 {sign}{b}%，"
                f"原值 {cal.get('raw')}）")
    return f"自我校正未套用：{cal.get('reason', '樣本累積中')}"


def _calibration_note_compact(obj: dict) -> str:
    """同 _calibration_note，但前期可預期的「樣本累積中」狀態回空字串，
    避免在 email 卡片每天印一行雜訊。"""
    note = _calibration_note(obj)
    if not note:
        return ""
    if "未套用" in note and ("樣本" in note or "累積" in note):
        return ""
    return note


def _extract_stance(text: str) -> dict:
    """從 LLM markdown 分析中擷取「立場」與「淨分」，用於頂部 KPI 條。失敗回 {}。"""
    import re as _re
    out: dict = {"label": None, "score": None}
    if not isinstance(text, str):
        return out
    m = _re.search(r"淨分\s*([+\-]?\d+)", text)
    if m:
        try:
            out["score"] = int(m.group(1))
        except ValueError:
            pass
    # 「立場：偏多」「立場: 中性偏多（...」「立場：偏空 / 防守為主」皆吃
    m = _re.search(r"立場\s*[：:]\s*\**\s*([一-鿿/]+)", text)
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


def _render_kpi_strip(quotes: dict, fair: dict, predictions: dict, stance: dict) -> str:
    """頂部 KPI 一覽條（dark bg，緊接 HERO 下方）。
    內容：立場 / 2330 / 00662 / 0050 / 加權，2 秒掃完今天重點。
    若有設定個人持股,第二行顯示 持倉1/持倉2 今日預估漲幅 + 金額(僅彙總,不揭露明細)。
    (VIX 移到「總經指標」表內，騰出 KPI 位置給 0050。)"""
    import html as _htmllib_kpi   # 持倉名稱可能是 user 自訂字串,需 escape
    # === 立場 ===
    score = stance.get("score")
    label = stance.get("label") or "—"
    if score is None:
        stance_color = "#94a3b8"
        score_str = ""
    elif score >= 4:
        stance_color = "#fb7185"   # 偏多 → 暖紅（TW 慣例）
        score_str = f" {score:+d}"
    elif score <= -4:
        stance_color = "#86efac"   # 偏空 → 綠
        score_str = f" {score:+d}"
    else:
        stance_color = "#fcd34d"   # 中性 → 黃
        score_str = f" {score:+d}"

    # === 2330 ===
    mid_2330 = predictions.get("mid") if isinstance(predictions, dict) else None
    last_2330 = predictions.get("last_2330") if isinstance(predictions, dict) else None
    pct_2330 = ((mid_2330 / last_2330 - 1) * 100) if (mid_2330 and last_2330) else None

    # === 00662 ===
    fair_price = fair.get("fair_price") if isinstance(fair, dict) else None
    last_00662 = fair.get("last_00662_price") if isinstance(fair, dict) else None
    pct_00662 = ((fair_price / last_00662 - 1) * 100) if (fair_price and last_00662) else None

    # === 加權 ===
    taiex = quotes.get("TAIEX_PRED", {}) or {}
    pred_taiex = taiex.get("pred_open")
    last_taiex = taiex.get("last_close")
    pct_taiex = ((pred_taiex / last_taiex - 1) * 100) if (pred_taiex and last_taiex) else None

    # === 0050 ===
    tw0050p = quotes.get("TW0050_PRED", {}) or {}
    pred_0050 = tw0050p.get("pred_open")
    last_0050 = tw0050p.get("last")
    pct_0050 = ((pred_0050 / last_0050 - 1) * 100) if (pred_0050 and last_0050) else None

    def fmt(v, dec=2):
        return f"{v:.{dec}f}" if v is not None else "—"

    def fmt_int(v):
        return f"{v:,.0f}" if v is not None else "—"

    def color_pct(p):
        if p is None:
            return "rgba(255,255,255,0.55)"
        return "#fb7185" if p >= 0 else "#86efac"   # TW: 紅漲綠跌（在 dark bg 上用較柔的色)

    def fmt_pct(p):
        if p is None:
            return ""
        sign = "+" if p >= 0 else ""
        return f"{sign}{p:.2f}%"

    cell = ("text-align:center;padding:12px 6px 14px;vertical-align:middle;"
            "border-right:1px solid rgba(255,255,255,0.10);")
    cell_last = "text-align:center;padding:12px 6px 14px;vertical-align:middle;"
    lbl = ("font-size:10px;letter-spacing:2px;color:rgba(255,255,255,0.60);"
           "text-transform:uppercase;font-weight:600;line-height:1.2;")
    val = ("font-size:18px;font-weight:700;color:#ffffff;line-height:1.2;"
           "margin-top:6px;font-variant-numeric:tabular-nums;")
    delta = ("font-size:11px;font-weight:500;line-height:1.2;margin-top:3px;"
             "font-variant-numeric:tabular-nums;")

    def _kpi_tile_numeric(label_txt: str, value_str: str, pct: float | None,
                          is_last: bool = False) -> str:
        c = cell_last if is_last else cell
        if pct is None:
            delta_line = ""
        else:
            delta_line = (f'<div style="{delta};color:{color_pct(pct)};">'
                          f'{fmt_pct(pct)}</div>')
        return (f'<td style="{c}">'
                f'<div style="{lbl}">{label_txt}</div>'
                f'<div style="{val}">{value_str}</div>'
                f'{delta_line}'
                f'</td>')

    stance_tile = (f'<td style="{cell}">'
                   f'<div style="{lbl}">立場</div>'
                   f'<div style="{val};color:{stance_color};">{label}{score_str}</div>'
                   f'</td>')

    # === 個人持股列(第二行,僅在有設定時顯示;只秀彙總漲幅 + 金額,不揭露持股明細)===
    pf = quotes.get("PORTFOLIO_FORECAST", {}) or {}

    def _fmt_amount(amt):
        if amt is None:
            return ""
        sign = "+" if amt >= 0 else "−"
        a = abs(amt)
        if a >= 10000:
            return f"{sign}NT${a/10000:.1f}萬"
        return f"{sign}NT${a:,.0f}"

    def _portfolio_tile(name, data, is_last):
        c = cell_last if is_last else cell
        if not data or data.get("pred_pct") is None:
            return (f'<td style="{c}">'
                    f'<div style="{lbl}">{_htmllib_kpi.escape(name)}</div>'
                    f'<div style="{val};color:rgba(255,255,255,0.55);">—</div>'
                    f'<div style="{delta};color:rgba(255,255,255,0.45);">未設定</div>'
                    f'</td>')
        p = data["pred_pct"]
        amt = data.get("pred_amount")
        return (f'<td style="{c}">'
                f'<div style="{lbl}">{_htmllib_kpi.escape(name)} 今日預估</div>'
                f'<div style="{val};color:{color_pct(p)};">{fmt_pct(p)}</div>'
                f'<div style="{delta};color:{color_pct(amt)};">{_fmt_amount(amt)}</div>'
                f'</td>')

    portfolio_row = ""
    p1 = pf.get("p1") or {}
    p2 = pf.get("p2") or {}
    if p1 or p2:
        p1_name = pf.get("p1_name", "持倉1")
        p2_name = pf.get("p2_name", "持倉2")
        # 兩格各佔一半;若只設一個,另一格顯示「未設定」佔位以維持版面
        portfolio_row = f"""
          <tr>
            <td style="background:#0a3f5e;padding:0;border-top:1px solid rgba(255,255,255,0.12);">
              <table role="presentation" style="width:100%;border-collapse:collapse;">
                <tr>
                  {_portfolio_tile(p1_name, p1, is_last=False)}
                  {_portfolio_tile(p2_name, p2, is_last=True)}
                </tr>
              </table>
            </td>
          </tr>"""

    return f"""
          <tr>
            <td style="background:#0c4a6e;padding:0;">
              <table role="presentation" style="width:100%;border-collapse:collapse;">
                <tr>
                  {stance_tile}
                  {_kpi_tile_numeric("2330 預測", fmt(mid_2330), pct_2330)}
                  {_kpi_tile_numeric("00662 預測", fmt(fair_price), pct_00662)}
                  {_kpi_tile_numeric("0050 預測", fmt(pred_0050), pct_0050)}
                  {_kpi_tile_numeric("加權預測", fmt_int(pred_taiex), pct_taiex, is_last=True)}
                </tr>
              </table>
            </td>
          </tr>{portfolio_row}"""


def _render_summary_bar(summary: str, htmllib) -> str:
    """LLM 一句話結論釘到頂部（HERO/KPI 下方第一行可見的人話）。失敗 → 空字串。"""
    if not summary:
        return ""
    safe = htmllib.escape(summary)
    return f"""
          <tr>
            <td style="background:#fef3c7;border-top:3px solid #f59e0b;padding:16px 24px;">
              <div style="font-size:10px;letter-spacing:2px;color:#92400e;font-weight:700;text-transform:uppercase;margin-bottom:6px;">今日結論</div>
              <div style="font-size:16px;color:#0f172a;font-weight:600;line-height:1.55;">{safe}</div>
            </td>
          </tr>"""


def render_html(quotes: dict, fair: dict, predictions: dict, analysis: str,
                report_date: str, mode: str) -> str:
    import html as _htmllib   # 整個 render_html 共用：用於各段 user-supplied 字串 escape

    # ===== 1. 行情表格 =====
    def fmt_quote(q: dict) -> str:
        if "error" in q:
            return (f"<tr><td style='padding:10px 14px;border-bottom:1px solid #e2e8f0;'>{q['ticker']}</td>"
                    f"<td colspan='4' style='padding:10px 14px;border-bottom:1px solid #e2e8f0;color:#dc2626'>{q['error']}</td></tr>")
        pct = q.get("change_pct") or 0
        # 台股慣例：紅漲綠跌
        color = "#dc2626" if pct >= 0 else "#16a34a"
        sign = "+" if pct >= 0 else ""
        vol = q.get("volume")
        vol_str = f"{vol:,}" if vol else "—"
        return (
            f"<tr>"
            f"<td style='padding:12px 14px;border-bottom:1px solid #e2e8f0;font-weight:700;color:#0f172a;'>{q['ticker']}</td>"
            f"<td style='padding:12px 14px;border-bottom:1px solid #e2e8f0;text-align:right;font-variant-numeric:tabular-nums;'>{q['close']:.2f}</td>"
            f"<td style='padding:12px 14px;border-bottom:1px solid #e2e8f0;text-align:right;color:{color};font-weight:700;'>{sign}{pct}%</td>"
            f"<td style='padding:12px 14px;border-bottom:1px solid #e2e8f0;text-align:right;color:#475569;font-size:13px;'>{q['high']} / {q['low']}</td>"
            f"<td style='padding:12px 14px;border-bottom:1px solid #e2e8f0;text-align:right;color:#64748b;font-size:13px;'>{vol_str}</td>"
            f"</tr>"
        )

    # 只渲染真正的美股行情標的；quotes 字典還塞了 SEC_FILINGS / TAIFEX_OI / BACKTEST
    # 等非行情資料（list / str / 巢狀 dict），不能丟給 fmt_quote。
    quote_rows = "".join(
        fmt_quote(quotes[k]) for k in ("QQQ", "TSM", "SPY")
        if isinstance(quotes.get(k), dict)
    )

    # 總經指標表
    macro = quotes.get("MACRO", {}) or {}
    def fmt_macro_row(label: str, key: str, hint: str) -> str:
        m = macro.get(key, {})
        if "error" in m or not m.get("close"):
            return ""
        pct = m.get("change_pct") or 0
        color = "#dc2626" if pct >= 0 else "#16a34a"  # 紅漲綠跌
        sign = "+" if pct >= 0 else ""
        # 252 日百分位顏色：低位綠（買訊）、高位紅（賣訊）
        rank = m.get("pct_rank_252d")
        if rank is None:
            rank_cell = "—"
        else:
            if rank < 30:
                bg = "#dcfce7"; tcolor = "#15803d"  # 低位（綠）
            elif rank > 70:
                bg = "#fee2e2"; tcolor = "#b91c1c"  # 高位（紅）
            else:
                bg = "#f1f5f9"; tcolor = "#475569"  # 中位
            rank_cell = (f"<span style='background:{bg};color:{tcolor};"
                          f"padding:2px 8px;border-radius:10px;font-size:12px;font-weight:700;'>"
                          f"{rank:.0f}%</span>")
        return (f"<tr>"
                f"<td style='padding:10px 14px;border-bottom:1px solid #e2e8f0;font-weight:700;color:#0f172a;'>{label}</td>"
                f"<td style='padding:10px 14px;border-bottom:1px solid #e2e8f0;text-align:right;font-variant-numeric:tabular-nums;'>{m['close']:,.2f}</td>"
                f"<td style='padding:10px 14px;border-bottom:1px solid #e2e8f0;text-align:right;color:{color};font-weight:700;'>{sign}{pct:.2f}%</td>"
                f"<td style='padding:10px 14px;border-bottom:1px solid #e2e8f0;text-align:center;'>{rank_cell}</td>"
                f"<td style='padding:10px 14px;border-bottom:1px solid #e2e8f0;color:#64748b;font-size:12px;'>{hint}</td>"
                f"</tr>")
    # 信件只顯示「一般投資人看得懂」的指標;艱澀的 VIX9D / NQ・ES 期貨 / 10Y・13W 殖利率
    # 已從 email 移除,但仍在 MACRO dict + LLM prompt 內(後台保留餵立場評分與模型,品質不降)。
    macro_rows = (
        fmt_macro_row("VIX 恐慌指數", "VIX", "<15樂觀 / >25恐慌") +
        fmt_macro_row("SOX 費半指數", "SOX", "美國半導體,與台積電連動最高") +
        fmt_macro_row("DXY 美元指數", "DXY", "升→外資易匯出、台股偏壓") +
        fmt_macro_row("日經 225", "N225", "亞股開盤情緒參考") +
        fmt_macro_row("上證綜指", "SSE", "中國盤面→台股資金面") +
        fmt_macro_row("WTI 原油", "WTI", "通膨/地緣風險定價") +
        fmt_macro_row("黃金", "GOLD", "避險情緒,漲多代表避險升溫")
    )
    # === TAIFEX 外資台指期未平倉區塊 ===
    taifex = quotes.get("TAIFEX_OI", {}) or {}
    taifex_html = ""
    if taifex.get("foreign_oi_net") is not None:
        f_oi = taifex.get("foreign_oi_net", 0)
        f_color = "#dc2626" if f_oi > 0 else "#16a34a"
        f_sign = "+" if f_oi > 0 else ""
        if abs(f_oi) > 20000:
            strength = "強烈訊號"
            bg = "#fef3c7"; border = "#f59e0b"
        elif abs(f_oi) > 5000:
            strength = "明確訊號"
            bg = "#dbeafe"; border = "#3b82f6"
        else:
            strength = "中性"
            bg = "#f1f5f9"; border = "#94a3b8"
        direction = "偏多" if f_oi > 0 else "偏空" if f_oi < 0 else "中性"
        taifex_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">外資台指期未平倉（領先指標）</h2>
        <div style="background:{bg};border:2px solid {border};border-radius:10px;padding:14px 18px;margin:12px 0;">
          <div style="font-size:13px;color:#475569;margin-bottom:6px;">資料日期：{taifex.get('date','—')}</div>
          <div style="font-size:16px;color:#0f172a;line-height:1.8;">
            <b>外資台指期未平倉淨額：<span style="color:{f_color};font-size:22px;font-weight:700;">{f_sign}{f_oi:,} 口</span> （{direction}・{strength}）</b><br>
            投信淨額：{taifex.get('invest_oi_net',0):+,d} 口　|
            自營商淨額：{taifex.get('dealer_oi_net',0):+,d} 口
          </div>
          <div style="font-size:12px;color:#64748b;margin-top:8px;">
            ※ 外資台指期未平倉是領先指標，比現貨買賣超更直接反映法人對今日台股的方向預期。
            正值=偏多倉位、負值=偏空倉位。&gt;±2萬口為強烈訊號。
          </div>
        </div>
        """

    # === SEC 8-K 公告區塊（只顯示「重點科技股」白名單:美股前 10 大市值 + 關鍵半導體 + 台積電）===
    sec_filings = quotes.get("SEC_FILINGS", []) or []
    # 過濾:只留 priority(消費/零售/工業雜訊不顯示);舊資料無 priority 欄位時退化為全顯示
    sec_priority = [f for f in sec_filings if f.get("priority")]
    if not sec_priority and sec_filings and not any("priority" in f for f in sec_filings):
        sec_priority = sec_filings    # 向後相容:state 來的舊 filing 沒有 priority 欄
    sec_html = ""
    if sec_priority:
        sec_rows = "\n".join(
            f"<tr>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #e2e8f0;font-weight:700;font-size:13px;'>{_htmllib.escape(str(f['company']))}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #e2e8f0;color:#0284c7;font-size:13px;white-space:nowrap;'>{f['form']}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #e2e8f0;color:#64748b;font-size:12px;white-space:nowrap;'>{f['date']}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #e2e8f0;font-size:12px;'>{_htmllib.escape(' / '.join(f['items']))}</td>"
            f"</tr>"
            for f in sec_priority[:15]
        )
        sec_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">美股重點科技股 8-K 公告（近 48 小時）</h2>
        <table style="width:100%;border-collapse:collapse;margin:12px 0;font-size:14px;">
          <tr style="background:#f1f5f9;">
            <th style="padding:8px 12px;text-align:left;color:#475569;font-size:12px;">公司</th>
            <th style="padding:8px 12px;text-align:left;color:#475569;font-size:12px;">表單</th>
            <th style="padding:8px 12px;text-align:left;color:#475569;font-size:12px;">日期</th>
            <th style="padding:8px 12px;text-align:left;color:#475569;font-size:12px;">事件類型</th>
          </tr>
          {sec_rows}
        </table>
        <p style="font-size:12px;color:#94a3b8;margin:4px 0;">※ 只列美股前 10 大市值 + 關鍵半導體/AI/設備/EDA（NVDA/AVGO/AMD/MRVL/AMAT/ASML/SNPS/ARM 等）+ 台積電;台股其餘公司的重大訊息見上方「MOPS 重大訊息」段。8-K 是 SEC 規定的「重大事件即時揭露」表單。</p>
        """

    # === 台股前 10 大公司 MOPS 重大訊息 ===
    tw_mops = quotes.get("TW_MOPS", []) or []
    mops_html = ""
    if tw_mops:
        mops_rows = "\n".join(
            f"<tr>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #e2e8f0;font-weight:700;color:#0f172a;font-size:13px;'>{_htmllib.escape(str(m.get('code','')))}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #e2e8f0;color:#475569;font-size:13px;'>{_htmllib.escape(str(m.get('title',''))[:120])}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #e2e8f0;color:#94a3b8;font-size:12px;white-space:nowrap;'>{_htmllib.escape(str(m.get('published',''))[:16])}</td>"
            f"</tr>"
            for m in tw_mops[:20]
        )
        mops_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">台股前 10 大公司 MOPS 重大訊息（近 48 小時）</h2>
        <table style="width:100%;border-collapse:collapse;margin:12px 0;font-size:14px;">
          <tr style="background:#f1f5f9;">
            <th style="padding:8px 12px;text-align:left;color:#475569;font-size:12px;">代號</th>
            <th style="padding:8px 12px;text-align:left;color:#475569;font-size:12px;">標題</th>
            <th style="padding:8px 12px;text-align:left;color:#475569;font-size:12px;">時間</th>
          </tr>
          {mops_rows}
        </table>
        <p style="font-size:12px;color:#94a3b8;margin:4px 0;">※ MOPS（公開資訊觀測站）為台灣上市公司法定即時揭露來源。</p>
        """

    # === 警告 Banner (Task H) ===
    alerts = quotes.get("ALERTS", []) or []
    alerts_html = ""
    if alerts:
        level_colors = {
            "red":    {"bg": "#fef2f2", "border": "#dc2626", "text": "#991b1b", "icon": "▲"},
            "orange": {"bg": "#fff7ed", "border": "#ea580c", "text": "#9a3412", "icon": "▲"},
            "yellow": {"bg": "#fefce8", "border": "#ca8a04", "text": "#854d0e", "icon": "▲"},
        }
        alert_items = []
        for a in alerts:
            c = level_colors.get(a["level"], level_colors["yellow"])
            alert_items.append(
                f'<div style="background:{c["bg"]};border-left:5px solid {c["border"]};'
                f'padding:12px 16px;margin:8px 0;border-radius:4px;">'
                f'<div style="color:{c["text"]};font-weight:700;font-size:14px;">'
                f'{c["icon"]} {a["title"]}</div>'
                f'<div style="color:{c["text"]};font-size:13px;margin-top:4px;line-height:1.6;">{a["detail"]}</div>'
                f'</div>'
            )
        alerts_html = (
            '<div style="margin:24px 0;">'
            '<div style="font-size:13px;color:#475569;font-weight:700;letter-spacing:1px;margin-bottom:8px;">'
            'MARKET ALERTS ・ 市場警告</div>'
            + "\n".join(alert_items) +
            '</div>'
        )

    # === 加權指數預測卡 (Task A) ===
    taiex_pred = quotes.get("TAIEX_PRED", {}) or {}
    taiex_html = ""
    if taiex_pred.get("pred_open"):
        signal_rows = "".join(
            f"<tr><td style='padding:6px 12px;color:#475569;font-size:13px;'>{s['name']}</td>"
            f"<td style='padding:6px 12px;text-align:right;font-variant-numeric:tabular-nums;'>{s['value']:+.2f}%</td>"
            f"<td style='padding:6px 12px;text-align:right;color:#94a3b8;font-size:12px;'>權重 {s['weight']:.0%}</td></tr>"
            for s in taiex_pred.get("signals", [])
        )
        # 顯示用的「最終漲跌幅」必須從『校正後 pred_open』回推,跟頭條數字一致;
        # 否則信件會出現「漲跌 +0.18%」但「預測點位 -0.01%」的怪現象（校正改了 pred_open 卻沒改 weighted_pct）。
        raw_pct = taiex_pred.get("weighted_pct")
        last_close_val = taiex_pred.get("last_close")
        final_pred = taiex_pred.get("pred_open")
        if last_close_val and final_pred:
            final_pct = (final_pred / last_close_val - 1) * 100
        else:
            final_pct = raw_pct if raw_pct is not None else 0
        pct_color = "#dc2626" if final_pct >= 0 else "#16a34a"
        pct_sign = "+" if final_pct >= 0 else ""
        # 若校正讓 raw 與 final 顯著不同(>0.05 pct point),括號內附原始訊號值供參考
        raw_note = ""
        if raw_pct is not None and abs(raw_pct - final_pct) > 0.05:
            raw_sign = "+" if raw_pct >= 0 else ""
            raw_note = (f' <span style="color:#94a3b8;font-size:12px;font-weight:400;">'
                        f'(原始訊號 {raw_sign}{raw_pct:.2f}%)</span>')
        taiex_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">五、加權指數開盤預測</h2>
        <table style="width:100%;border-collapse:collapse;margin:12px 0;background:#f8fafc;border-radius:8px;overflow:hidden;">
          {signal_rows}
        </table>
        <table style="width:100%;border-collapse:collapse;margin:12px 0;">
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;width:55%;">加權昨收</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-variant-numeric:tabular-nums;">{taiex_pred['last_close']}</td>
          </tr>
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">加權預測漲跌</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-weight:700;color:{pct_color};font-variant-numeric:tabular-nums;">{pct_sign}{final_pct:.2f}%{raw_note}</td>
          </tr>
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:14px;background:linear-gradient(135deg,#0284c7,#0ea5e9);color:#fff;font-weight:700;border-radius:6px 0 0 6px;">★ 預測開盤點位</td>
            <td style="padding:14px;background:linear-gradient(135deg,#0284c7,#0ea5e9);color:#fff;text-align:right;font-size:24px;font-weight:700;border-radius:0 6px 6px 0;font-variant-numeric:tabular-nums;">{taiex_pred['pred_open']:,.0f}</td>
          </tr>
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">合理區間</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-variant-numeric:tabular-nums;">{taiex_pred['ci_lower']:,.0f} ~ {taiex_pred['ci_upper']:,.0f}</td>
          </tr>
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">訊號共識</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-weight:700;">{taiex_pred['consensus']}</td>
          </tr>
        </table>
        {(lambda c: f'<p style="font-size:11px;color:#94a3b8;margin:6px 0;">{c}</p>' if c else "")(_calibration_note_compact(taiex_pred))}
        """

    # === 0050 ETF 開盤預測卡 ===
    tw0050p_data = quotes.get("TW0050_PRED", {}) or {}
    tw0050_card_html = ""
    if tw0050p_data.get("pred_open") and tw0050p_data.get("last"):
        p50 = tw0050p_data["pred_open"]
        l50 = tw0050p_data["last"]
        pct50 = ((p50 / l50) - 1) * 100
        c50 = "#dc2626" if pct50 >= 0 else "#16a34a"
        s50 = "+" if pct50 >= 0 else ""
        tw0050_card_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">六、0050 ETF 開盤預測</h2>
        <table style="width:100%;border-collapse:collapse;margin:12px 0;">
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;width:55%;">0050 昨收</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-variant-numeric:tabular-nums;">{l50}</td>
          </tr>
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">預測漲跌幅</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-weight:700;color:{c50};font-variant-numeric:tabular-nums;">{s50}{pct50:.2f}%</td>
          </tr>
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:14px;background:linear-gradient(135deg,#0284c7,#0ea5e9);color:#fff;font-weight:700;border-radius:6px 0 0 6px;">★ 0050 今日合理價</td>
            <td style="padding:14px;background:linear-gradient(135deg,#0284c7,#0ea5e9);color:#fff;text-align:right;font-size:26px;font-weight:700;border-radius:0 6px 6px 0;font-variant-numeric:tabular-nums;">{p50}</td>
          </tr>
        </table>
        <p style="font-size:11px;color:#94a3b8;margin:6px 0;">預測方法：{tw0050p_data.get('method','—')}（0050 約 50% 為 2330）</p>
        """

    # === 籌碼悄悄站隊 Top 5(法人連買 + 大戶 WoW + 量縮收紅 綜合分數)===
    # 手機版面:改成「每檔一列、列內 2 欄(分數 chip + 堆疊明細)」,避免 8 欄寬表在
    # 手機 Gmail 擠爆跑版。
    smart_money_html = ""
    universe_snapshot = quotes.get("TW_UNIVERSE_SNAPSHOT", []) or []
    if universe_snapshot:
        scored = [s for s in universe_snapshot
                  if (s.get("smart_money") or {}).get("score", 0) >= 40]
        scored.sort(key=lambda x: (x.get("smart_money") or {}).get("score", 0),
                    reverse=True)
        top5 = scored[:5]
        if top5:
            rows_html = []
            for s in top5:
                sm = s.get("smart_money") or {}
                score = sm.get("score", 0)
                tags = sm.get("tags", []) or []
                if score >= 80:
                    score_bg, score_fg = "#fee2e2", "#b91c1c"   # 紅:強訊號
                elif score >= 60:
                    score_bg, score_fg = "#fef3c7", "#92400e"   # 橘:站隊
                else:
                    score_bg, score_fg = "#dbeafe", "#1e40af"   # 藍:輕微
                tag_chips = "".join(
                    f'<span style="display:inline-block;background:#f1f5f9;color:#475569;'
                    f'padding:1px 7px;border-radius:8px;font-size:11px;margin:0 3px 3px 0;">'
                    f'{_htmllib.escape(str(t))}</span>'
                    for t in tags[:6]
                )
                tag_chips_line = tag_chips or '<span style="color:#94a3b8;font-size:11px;">無特別標籤</span>'
                fs = s.get("foreign_streak", 0) or 0
                is_ = s.get("invest_streak", 0) or 0
                day_pct = s.get("day_pct") or 0
                day_color = "#dc2626" if day_pct >= 0 else "#16a34a"
                day_sign = "+" if day_pct >= 0 else ""
                wow = s.get("tdcc_wow_pct")
                wow_str = f"{wow:+.2f}%" if wow is not None else "—"
                vr20 = s.get("vol_ratio_20d")
                vr20_str = f"{vr20:.2f}x" if vr20 else "—"
                # 數據明細(第三行小字):外連 / 投連 / 大戶ΔWoW / 量比20d
                streak_bits = []
                if fs:
                    streak_bits.append(f"外資連{abs(fs)}{'買' if fs > 0 else '賣'}")
                if is_:
                    streak_bits.append(f"投信連{abs(is_)}{'買' if is_ > 0 else '賣'}")
                metrics_line = (
                    f"{' ・ '.join(streak_bits) if streak_bits else '法人無連續動向'}"
                    f" ・ 大戶ΔWoW {wow_str} ・ 量比20d {vr20_str}")
                rows_html.append(
                    f"<tr>"
                    f"<td style='padding:12px 8px 12px 0;border-bottom:1px solid #e2e8f0;"
                    f"vertical-align:top;width:48px;text-align:center;'>"
                    f"<span style='display:inline-block;background:{score_bg};color:{score_fg};"
                    f"padding:5px 0;width:42px;border-radius:8px;font-size:16px;font-weight:700;'>{score}</span></td>"
                    f"<td style='padding:12px 0;border-bottom:1px solid #e2e8f0;vertical-align:top;'>"
                    # 第 1 行:代號 名稱 + 日%
                    f"<div style='font-size:15px;font-weight:700;color:#0f172a;'>"
                    f"{s['code']} {_htmllib.escape(s.get('name',''))}"
                    f"<span style='color:{day_color};font-weight:700;font-size:13px;margin-left:8px;'>"
                    f"昨收 {s.get('close','—')} ({day_sign}{day_pct:.2f}%)</span></div>"
                    # 第 2 行:訊號標籤 chips
                    f"<div style='margin-top:5px;'>{tag_chips_line}</div>"
                    # 第 3 行:數據明細小字
                    f"<div style='margin-top:5px;font-size:11px;color:#94a3b8;'>{metrics_line}</div>"
                    f"</td>"
                    f"</tr>"
                )
            smart_money_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#fff7ed;border-left:5px solid #ea580c;border-radius:4px;">籌碼悄悄站隊 Top {len(top5)}（分數 ≥ 40）</h2>
        <table role="presentation" style="width:100%;border-collapse:collapse;margin:12px 0;">
          {''.join(rows_html)}
        </table>
        <p style="font-size:11px;color:#94a3b8;margin:6px 0;line-height:1.6;">
          ※ <b>分數 = 法人連買 (40) + 大戶 WoW Δ%(30) + 量縮收紅 / 突破量(20) + 偷買區間 (10) + 融資減 (5)</b>;
          <b>≥80 強訊號(紅)</b>、≥60 悄悄站隊(橘)、≥40 輕微正向(藍)。<br>
          ※ 大戶 ΔWoW = TDCC 集保 ≥400 張持股比例「本週 − 上週」(需累積 ≥ 1 週歷史才有值);
          量比20d = 今日量 / 近 20 日均量(&lt; 0.8 量縮、&gt; 1.5 放量)。<br>
          ※ 此分數**為輔助參考、不是買進訊號**;仍須結合新聞催化、營收基本面、結構健康度綜合判讀。
        </p>
        """

    # === 大盤成交額 + 市場廣度卡 ===
    breadth = quotes.get("BREADTH", {}) or {}
    breadth_html = ""
    if breadth.get("total"):
        adv = breadth.get("advance", 0)
        dec = breadth.get("decline", 0)
        unch = breadth.get("unchanged", 0)
        total = breadth.get("total", 0)
        adv_ratio = breadth.get("advance_ratio", 0)
        state = breadth.get("breadth_state", "neutral")
        # 顏色：上漲多 = 紅 (台股慣例); 下跌多 = 綠
        if adv_ratio >= 60:
            b_color, b_label = "#dc2626", "普漲（強勢）"
        elif adv_ratio <= 40:
            b_color, b_label = "#16a34a", "普跌（弱勢）"
        elif 45 <= adv_ratio <= 55:
            b_color, b_label = "#64748b", "多空均衡"
        else:
            b_color, b_label = "#a16207", "窄幅（少數股撐盤）"
        breadth_html = f"""
        <div style="background:#f1f5f9;border-radius:10px;padding:14px 18px;margin:12px 0;">
          <div style="font-size:13px;color:#475569;font-weight:700;margin-bottom:6px;">大盤成交額與市場廣度</div>
          <div style="font-size:14px;color:#0f172a;line-height:1.7;">
            成交金額 <b>{breadth.get('total_value_yi',0):,.0f} 億</b>　｜
            上漲 <b style="color:#dc2626;">{adv}</b> 檔・下跌 <b style="color:#16a34a;">{dec}</b> 檔・平盤 {unch} 檔　|
            上漲佔比 <b style="color:{b_color};">{adv_ratio:.1f}%</b>
            <span style="font-size:12px;color:{b_color};margin-left:8px;">（{b_label}）</span>
          </div>
          <div style="font-size:11px;color:#94a3b8;margin-top:6px;">※ 上漲家數 ≥ 60% 為普漲、≤ 40% 為普跌；若指數漲但廣度低 = 少數權值股撐盤、健康度差。</div>
        </div>
        """

    # === 中期展望卡（1 週 / 1 月 統計區間，非點預測）===
    midterm = quotes.get("MIDTERM", {}) or {}
    midterm_html = ""
    if midterm:
        midterm_rows = []
        for name in ("2330", "0050", "00662"):
            entry = midterm.get(name) or {}
            metrics = entry.get("metrics") or {}
            fc = entry.get("forecast") or {}
            f5 = fc.get("5d") or {}
            f20 = fc.get("20d") or {}
            trend = entry.get("trend", "—")
            # 趨勢顏色
            if "強勢" in trend or "上行" in trend:
                trend_color = "#dc2626"   # 紅 (TW 漲)
            elif "弱勢" in trend or "下行" in trend:
                trend_color = "#16a34a"   # 綠 (TW 跌)
            else:
                trend_color = "#64748b"
            pct_5d = metrics.get("pct_5d")
            d20 = metrics.get("ma20_dist_pct")
            pct_5d_color = "#dc2626" if (pct_5d or 0) >= 0 else "#16a34a"
            d20_color = "#dc2626" if (d20 or 0) >= 0 else "#16a34a"
            if not f5 or not f20:
                continue
            # 兩個範圍：±1σ(常態 68%) / ±1.5σ(極端 87%),都顯示
            def _range_cell(fc: dict) -> str:
                lo1 = fc.get("lower_1s") or fc.get("lower")
                up1 = fc.get("upper_1s") or fc.get("upper")
                lo15 = fc.get("lower_15s") or fc.get("lower")
                up15 = fc.get("upper_15s") or fc.get("upper")
                return (f"<div style='font-size:13px;color:#0f172a;'>"
                        f"<b>{lo1}–{up1}</b> <span style='font-size:10px;color:#94a3b8;'>常態±1σ</span></div>"
                        f"<div style='font-size:12px;color:#94a3b8;margin-top:2px;'>"
                        f"{lo15}–{up15} <span style='font-size:10px;'>極端±1.5σ</span></div>")

            midterm_rows.append(
                f"<tr>"
                f"<td style='padding:10px;border-bottom:1px solid #e2e8f0;font-weight:700;color:#0f172a;'>{name}</td>"
                f"<td style='padding:10px;border-bottom:1px solid #e2e8f0;text-align:right;font-size:13px;color:{pct_5d_color};font-variant-numeric:tabular-nums;'>"
                f"{('+' if (pct_5d or 0) >= 0 else '')}{pct_5d if pct_5d is not None else '—'}%</td>"
                f"<td style='padding:10px;border-bottom:1px solid #e2e8f0;text-align:right;font-size:13px;color:{d20_color};font-variant-numeric:tabular-nums;'>"
                f"{('+' if (d20 or 0) >= 0 else '')}{d20 if d20 is not None else '—'}%</td>"
                f"<td style='padding:10px;border-bottom:1px solid #e2e8f0;text-align:right;font-variant-numeric:tabular-nums;'>{_range_cell(f5)}</td>"
                f"<td style='padding:10px;border-bottom:1px solid #e2e8f0;text-align:right;font-variant-numeric:tabular-nums;'>{_range_cell(f20)}</td>"
                f"<td style='padding:10px;border-bottom:1px solid #e2e8f0;font-size:12px;color:{trend_color};font-weight:600;'>{trend}</td>"
                f"</tr>"
            )
        if midterm_rows:
            midterm_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">中期展望（統計區間，非點預測）</h2>
        <table style="width:100%;border-collapse:collapse;margin:12px 0;font-size:13px;">
          <tr style="background:#f1f5f9;">
            <th style="padding:8px 10px;text-align:left;color:#475569;font-size:12px;">標的</th>
            <th style="padding:8px 10px;text-align:right;color:#475569;font-size:12px;">5日累積</th>
            <th style="padding:8px 10px;text-align:right;color:#475569;font-size:12px;">距 MA20</th>
            <th style="padding:8px 10px;text-align:right;color:#475569;font-size:12px;">1週區間</th>
            <th style="padding:8px 10px;text-align:right;color:#475569;font-size:12px;">1月區間</th>
            <th style="padding:8px 10px;text-align:left;color:#475569;font-size:12px;">趨勢</th>
          </tr>
          {''.join(midterm_rows)}
        </table>
        <p style="font-size:11px;color:#94a3b8;margin:6px 0;">※ <b>常態±1σ</b> = 約 68% 機率落在此區間（一般波動）;<b>極端±1.5σ</b> = 約 87%（含中等劇烈日）。<b>這是統計區間,不是「會漲到 X」的點預測</b>。</p>
        """

    # === 夜盤台指期卡 (Task B) ===
    night = quotes.get("NIGHT_TXF", {}) or {}
    night_html = ""
    if night.get("night_pct") is not None:
        n_pct = night["night_pct"]
        n_color = "#dc2626" if n_pct >= 0 else "#16a34a"
        n_sign = "+" if n_pct >= 0 else ""
        night_html = f"""
        <div style="background:#f1f5f9;border-radius:10px;padding:14px 18px;margin:12px 0;">
          <div style="font-size:13px;color:#475569;font-weight:700;margin-bottom:6px;">夜盤台指期（{night.get('date','—')}）</div>
          <div style="font-size:16px;color:#0f172a;">
            日盤 {night.get('day_close')} → 夜盤 {night.get('night_close')}
            <span style="color:{n_color};font-weight:700;margin-left:8px;">({n_sign}{n_pct}%)</span>
          </div>
        </div>
        """

    macro_table_html = ""
    if macro_rows:
        macro_table_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">二、總經指標</h2>
        <table style="width:100%;border-collapse:collapse;margin:12px 0;font-size:14px;">
          <tr style="background:#f1f5f9;">
            <th style="padding:10px 14px;text-align:left;color:#475569;font-size:12px;letter-spacing:1px;">指標</th>
            <th style="padding:10px 14px;text-align:right;color:#475569;font-size:12px;letter-spacing:1px;">收盤</th>
            <th style="padding:10px 14px;text-align:right;color:#475569;font-size:12px;letter-spacing:1px;">變動</th>
            <th style="padding:10px 14px;text-align:center;color:#475569;font-size:12px;letter-spacing:1px;">1Y 百分位</th>
            <th style="padding:10px 14px;text-align:left;color:#475569;font-size:12px;letter-spacing:1px;">判讀提示</th>
          </tr>
          {macro_rows}
        </table>
        """

    # ===== 2. KPI 卡片 (00662) =====
    if "error" not in fair:
        sign = "+" if fair["implied_change_pct"] >= 0 else ""
        # 台股慣例：紅漲綠跌
        change_color = "#dc2626" if fair["implied_change_pct"] >= 0 else "#16a34a"
        # 新欄位：歷史回歸的 beta + 平均偏離
        beta_row = ""
        if fair.get("samples", 0) >= 15:
            beta_row = f"""
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">00662 對 QQQ Beta（近 60 日實證）</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-variant-numeric:tabular-nums;">{fair.get('beta','—')}</td>
          </tr>"""
        dev_row = ""
        if fair.get("avg_deviation_pct") is not None and fair.get("samples", 0) >= 15:
            d_color = "#dc2626" if fair["avg_deviation_pct"] >= 0 else "#16a34a"
            d_sign = "+" if fair["avg_deviation_pct"] >= 0 else ""
            dev_row = f"""
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">歷史平均偏離（中位數）</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;color:{d_color};font-variant-numeric:tabular-nums;">{d_sign}{fair['avg_deviation_pct']}%</td>
          </tr>"""
        fx_row = ""
        if fair.get("fx_pct") is not None:
            fx_color = "#dc2626" if fair["fx_pct"] >= 0 else "#16a34a"
            fx_sign = "+" if fair["fx_pct"] >= 0 else ""
            fx_row = f"""
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">USD/TWD 變動</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;color:{fx_color};font-variant-numeric:tabular-nums;">{fx_sign}{fair['fx_pct']}% ({fair.get('usdtwd_prev','—')}→{fair.get('usdtwd','—')})</td>
          </tr>"""

        # 折溢價列（00662 市價 vs NDX 隱含 NAV 的 60 日中位數比較）
        premium_row = ""
        if fair.get("premium_pct") is not None:
            pp = fair["premium_pct"]
            if pp > 0.5:
                pp_color = "#dc2626"; pp_label = "溢價"          # 偏貴
            elif pp < -0.5:
                pp_color = "#16a34a"; pp_label = "折價"          # 偏便宜
            else:
                pp_color = "#64748b"; pp_label = "接近合理"
            pp_sign = "+" if pp >= 0 else ""
            premium_row = f"""
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">折溢價（vs NDX 隱含 NAV，60 日基準）</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;color:{pp_color};font-weight:700;font-variant-numeric:tabular-nums;">{pp_sign}{pp:.2f}% <span style="font-weight:500;font-size:12px;color:{pp_color};">({pp_label})</span></td>
          </tr>"""

        method_label = fair.get("method", "")
        calib_extra = _calibration_note_compact(fair)
        fair_foot = (f'<p style="font-size:11px;color:#94a3b8;margin:6px 0;">'
                     f'計算方式：{method_label}'
                     + (f'　｜　{calib_extra}' if calib_extra else '')
                     + '</p>')

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
          {beta_row}
          {dev_row}
          {fx_row}
          {premium_row}
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:14px;background:linear-gradient(135deg,#0284c7,#0ea5e9);color:#fff;font-weight:700;border-radius:6px 0 0 6px;">★ 00662 今日合理價估值</td>
            <td style="padding:14px;background:linear-gradient(135deg,#0284c7,#0ea5e9);color:#fff;text-align:right;font-size:22px;font-weight:700;border-radius:0 6px 6px 0;font-variant-numeric:tabular-nums;">{fair['fair_price']}</td>
          </tr>
        </table>
        {fair_foot}
        """
    else:
        fair_html = f"<p style='color:#dc2626'>{fair.get('error','資料缺失')}</p>"

    # ===== 3. 2330 預測卡片 =====
    if "error" not in predictions:
        m1 = predictions.get("model1_1to1")
        m2 = predictions.get("model2_regression")
        m3 = predictions.get("model3_adr_decay")
        m4 = predictions.get("model4_momentum")
        decay = predictions.get("decay_factor", "—")
        momentum_5d = predictions.get("momentum_5d_pct")
        rng = predictions.get("range")
        tsm_pct = predictions.get("tsm_pct", 0)
        # 台股慣例：紅漲綠跌
        tsm_color = "#dc2626" if tsm_pct >= 0 else "#16a34a"
        tsm_sign = "+" if tsm_pct >= 0 else ""

        def _fmt(v): return f"{v}" if v is not None else "—"
        if m4 is not None:
            models_compact = f"{_fmt(m1)} / {_fmt(m2)} / {_fmt(m3)} / {_fmt(m4)}"
            mom_str = f"{momentum_5d:+.2f}%" if momentum_5d is not None else "—"
            models_label = (f"四模型估值<br><span style=\"color:#94a3b8;font-size:11px;\">"
                            f"1:1 / 60日比值 / ADR衰減{decay} / 5日動能 {mom_str} ×0.15</span>")
        else:
            models_compact = f"{_fmt(m1)} / {_fmt(m2)} / {_fmt(m3)}"
            models_label = (f"三模型估值<br><span style=\"color:#94a3b8;font-size:11px;\">"
                            f"1:1 / 60日比值 / ADR衰減{decay}</span>")

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
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;font-size:13px;">{models_label}</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-variant-numeric:tabular-nums;color:#64748b;font-size:13px;">{models_compact}</td>
          </tr>
        """
        if rng:
            rows_html += f"""
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:14px;background:linear-gradient(135deg,#0284c7,#0ea5e9);color:#fff;font-weight:700;border-radius:6px 0 0 6px;">★ 2330 今日合理價</td>
            <td style="padding:14px;background:linear-gradient(135deg,#0284c7,#0ea5e9);color:#fff;text-align:right;font-size:26px;font-weight:700;border-radius:0 6px 6px 0;font-variant-numeric:tabular-nums;">
              {predictions['mid']}<br>
              <span style="font-size:12px;font-weight:400;opacity:0.80;">區間 {rng[0]} ~ {rng[1]}</span>
            </td>
          </tr>
            """
        # 只在「有東西可講」時才印 footer：校正啟動 或 final_method 不是預設值
        final_method = predictions.get("final_method", "")
        calib_extra = _calibration_note_compact(predictions)
        notes = []
        if final_method and "近期" in final_method:   # 加權啟動了
            notes.append(final_method)
        if calib_extra:
            notes.append(calib_extra)
        wf_line = ""
        if notes:
            wf_line = (f'<p style="font-size:11px;color:#94a3b8;margin:6px 0;">'
                       f'{"　｜　".join(notes)}</p>')
        pred_html = (f'<table style="width:100%;border-collapse:collapse;margin:12px 0;">'
                     f'{rows_html}</table>{wf_line}')
    else:
        pred_html = f"<p style='color:#dc2626'>{predictions.get('error','資料缺失')}</p>"

    # ===== 3.4 預測準確度回顧區塊 =====
    backtest_text = (quotes.get("BACKTEST") or "").strip()
    backtest_html = ""
    if backtest_text:
        backtest_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">預測準確度回顧</h2>
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:14px 16px;margin:12px 0;font-size:12px;line-height:1.75;color:#475569;white-space:pre-wrap;font-family:'Consolas','Menlo','Courier New',monospace;">{_htmllib.escape(backtest_text)}</div>
        <p style="font-size:12px;color:#94a3b8;margin:4px 0;">※ 比對「當日預測 vs 隔日實際開盤」。平均誤差為正＝預測偏低、為負＝預測偏高；此誤差會回饋進隔日的自我校正。</p>
        """

    # ===== 3.5 資料品質區塊 =====
    dq_list = quotes.get("DATA_QUALITY", []) or []
    dq_html = ""
    if dq_list:
        status_style = {
            "ok":       ("#dcfce7", "#15803d", "正常"),
            "fallback": ("#fef9c3", "#a16207", "降級"),
            "error":    ("#fee2e2", "#b91c1c", "失敗"),
        }
        dq_rows = []
        for d in dq_list:
            bg, tc, label = status_style.get(d.get("status", "fallback"), status_style["fallback"])
            name = _htmllib.escape(str(d.get("name", "")))
            detail = _htmllib.escape(str(d.get("detail", "")))
            dq_rows.append(
                f"<tr>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #e2e8f0;font-size:13px;color:#0f172a;'>{name}</td>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #e2e8f0;text-align:center;'>"
                f"<span style='background:{bg};color:{tc};padding:2px 10px;border-radius:10px;font-size:12px;font-weight:700;'>{label}</span></td>"
                f"<td style='padding:8px 12px;border-bottom:1px solid #e2e8f0;font-size:12px;color:#64748b;'>{detail}</td>"
                f"</tr>"
            )
        n_err = sum(1 for d in dq_list if d.get("status") == "error")
        n_fb = sum(1 for d in dq_list if d.get("status") == "fallback")
        summary = f"全部正常" if (n_err == 0 and n_fb == 0) else f"{n_err} 項失敗、{n_fb} 項降級"
        dq_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">資料品質（{summary}）</h2>
        <table style="width:100%;border-collapse:collapse;margin:12px 0;">
          <tr style="background:#f1f5f9;">
            <th style="padding:8px 12px;text-align:left;color:#475569;font-size:12px;">資料來源</th>
            <th style="padding:8px 12px;text-align:center;color:#475569;font-size:12px;">狀態</th>
            <th style="padding:8px 12px;text-align:left;color:#475569;font-size:12px;">說明</th>
          </tr>
          {''.join(dq_rows)}
        </table>
        <p style="font-size:12px;color:#94a3b8;margin:4px 0;">※「失敗」代表該來源今日抓不到資料，對應分析以「資料未提供」呈現，非市場無訊號。</p>
        """

    # ===== 3.7 頂部 KPI 一覽條 + 結論橫條（從 LLM markdown 擷取後渲染） =====
    stance = _extract_stance(analysis)
    summary_text = _extract_summary(analysis)
    kpi_strip = _render_kpi_strip(quotes, fair, predictions, stance)
    summary_bar = _render_summary_bar(summary_text, _htmllib)

    # ===== 4. LLM 分析（Markdown → HTML 後加樣式 + 三檔卡片化） =====
    analysis_html = _md_to_html(analysis)
    analysis_html = _style_analysis_html(analysis_html)
    analysis_html = _wrap_stance(analysis_html)
    analysis_html = _wrap_tw_picks(analysis_html)

    if LLM_PROVIDER == "gemini":
        llm_label = f"gemini/{GEMINI_MODEL}"
    elif LLM_PROVIDER == "deepseek":
        llm_label = f"deepseek/{DEEPSEEK_MODEL}"
    else:
        llm_label = f"anthropic/{CLAUDE_MODEL}"

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
            <td style="background:linear-gradient(135deg,#0c4a6e,#0284c7);padding:26px 28px 20px;color:#ffffff;">
              <div style="font-size:13px;letter-spacing:2px;opacity:0.85;margin-bottom:6px;">MORNING MARKET BRIEF</div>
              <h1 style="margin:0;font-size:26px;font-weight:700;color:#ffffff;line-height:1.3;">美股晨報</h1>
              <div style="margin-top:6px;font-size:15px;opacity:0.92;">{report_date} ・ <span style="background:rgba(255,255,255,0.18);padding:2px 10px;border-radius:12px;font-size:13px;">{mode}</span></div>
            </td>
          </tr>

          <!-- KPI STRIP (2 秒掃完今日重點) -->
          {kpi_strip}

          <!-- TODAY'S TAKEAWAY (LLM 一句話結論釘頂) -->
          {summary_bar}

          <!-- BODY -->
          <tr><td style="padding:24px 28px 8px;">

            {alerts_html}

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

            {macro_table_html}

            <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">三、00662 公允淨值換算</h2>
            {fair_html}

            <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">四、2330 開盤合理價預測</h2>
            {pred_html}

            {taiex_html}

            {tw0050_card_html}

            {breadth_html}

            {midterm_html}

            {night_html}

            {taifex_html}

            {mops_html}

            <div style="margin-top:32px;">{analysis_html}</div>

            <!-- 籌碼悄悄站隊:排在 LLM「今日台股關注三檔」之後 -->
            {smart_money_html}

            <!-- 美股重點科技股 8-K:排在籌碼站隊之後、準確度回顧之前 -->
            {sec_html}

            {backtest_html}

            {dq_html}

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
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise RuntimeError(
            "缺 GMAIL_USER / GMAIL_APP_PASSWORD 環境變數，無法寄信。"
            "（本機測試請設 DRY_RUN=1 改為輸出預覽檔）"
        )
    if not RECIPIENTS:
        raise RuntimeError("無收件者：請設定 RECIPIENT 環境變數，或確認 GMAIL_USER 不為空。")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = ", ".join(RECIPIENTS)   # 多位收件者：以逗號分隔，send_message 會全部寄送
    msg.set_content("此郵件需以 HTML 模式檢視。")
    msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        s.send_message(msg)
    print(f"[mail] 已寄出 → {', '.join(RECIPIENTS)}")


def determine_mode(now_tpe: dt.datetime) -> str:
    """判斷今日為一般報 (週二~週六) 還是週末綜合報 (週一)。"""
    wd = now_tpe.weekday()  # Mon=0
    return "週末綜合" if wd == 0 else "每日報"


def build_data_quality(quotes: dict, fair: dict, predictions: dict,
                        news: list[dict], tw0050: list[dict]) -> list[dict]:
    """
    彙整各資料來源今日的抓取狀態，供 HTML「資料品質」區塊與 LLM prompt 使用。
    讓 LLM 不會把「抓取失敗」誤判成「市場沒有訊號」。
    每筆：{ "name": 來源名, "status": "ok"/"fallback"/"error", "detail": 說明 }
    """
    dq: list[dict] = []

    def add(name: str, status: str, detail: str = "") -> None:
        dq.append({"name": name, "status": status, "detail": str(detail)[:80]})

    # 美股是否休市（國定假日)
    us_hol = quotes.get("US_HOLIDAY") or {}
    if us_hol.get("detected"):
        add("美股交易日", "fallback",
            f"昨日休市:最新收盤 {us_hol.get('actual_date')}({us_hol.get('actual_weekday')}),"
            f"延續值非新資訊")
    elif us_hol:
        add("美股交易日", "ok",
            f"{us_hol.get('actual_date','')} ({us_hol.get('actual_weekday','')})")

    # 美股行情
    for key, label in (("QQQ", "QQQ"), ("TSM", "TSM ADR"), ("SPY", "SPY")):
        q = quotes.get(key, {})
        if isinstance(q, dict) and not q.get("error") and q.get("close") is not None:
            # 若休市,降級標 fallback 提醒「資料延續但非新」
            status = "fallback" if us_hol.get("detected") else "ok"
            note = "(休市,延續值)" if us_hol.get("detected") else ""
            add(f"美股行情 {label}", status,
                f"{q.get('date','')} 收 {q.get('close')}{note}")
        else:
            err = q.get("error", "資料缺失") if isinstance(q, dict) else "資料缺失"
            add(f"美股行情 {label}", "error", err)

    # USD/TWD
    if quotes.get("USDTWD") is not None:
        add("USD/TWD 匯率", "ok", str(quotes.get("USDTWD")))
    else:
        add("USD/TWD 匯率", "error", "TWD=X 抓取失敗")

    # 總經 + 國際指標 + 期貨/商品 (12 項)
    macro = quotes.get("MACRO", {}) or {}
    # VIX_TERM 是 derived，不算實際抓取項目
    countable = {k: v for k, v in macro.items() if k != "VIX_TERM"}
    ok_n = sum(1 for v in countable.values()
               if isinstance(v, dict) and not v.get("error") and v.get("close") is not None)
    tot = len(countable) or 12
    macro_label = "總經/國際/期貨/商品 (VIX/VIX9D/SOX/10Y/DXY/13W/日經/上證/NQ/ES/WTI/黃金)"
    if ok_n >= tot:
        add(macro_label, "ok", f"{ok_n}/{tot} 項")
    elif ok_n == 0:
        add(macro_label, "error", "全部抓取失敗")
    else:
        add(macro_label, "fallback", f"{ok_n}/{tot} 項成功")

    # 大盤成交額 + 市場廣度
    breadth = quotes.get("BREADTH", {}) or {}
    if breadth.get("total"):
        add("大盤成交額 + 市場廣度",
            "ok",
            f"{breadth.get('total_value_yi',0):,.0f} 億・上漲 {breadth.get('advance_ratio',0)}%")
    else:
        add("大盤成交額 + 市場廣度", "error", "TWSE STOCK_DAY_ALL 抓取失敗")

    # 00662 估值
    if isinstance(fair, dict) and not fair.get("error"):
        if fair.get("samples", 0) >= 15:
            add("00662 估值", "ok", fair.get("method", ""))
        else:
            add("00662 估值", "fallback", fair.get("method", "簡化版（歷史資料不足）"))
    else:
        add("00662 估值", "error", (fair or {}).get("error", "資料缺失"))

    # 2330 三模型預測
    if isinstance(predictions, dict) and not predictions.get("error"):
        if predictions.get("model2_regression") is not None:
            add("2330 三模型預測", "ok", f"最終 {predictions.get('weighted_final', predictions.get('mid', '—'))}")
        else:
            add("2330 三模型預測", "fallback", "model2 比值回歸資料不足，僅 model1/model3")
    else:
        add("2330 三模型預測", "error", (predictions or {}).get("error", "資料缺失"))

    # 預測自我校正（bias 修正 + 模型加權）
    cal_objs = [fair, predictions, quotes.get("TAIEX_PRED", {})]
    n_cal = sum(1 for o in cal_objs
                if isinstance(o, dict) and o.get("calibration", {}).get("applied"))
    if n_cal == 3:
        add("預測自我校正", "ok", "00662 / 2330 / 加權指數 均已套用歷史偏誤修正")
    elif n_cal > 0:
        add("預測自我校正", "fallback", f"{n_cal}/3 已套用，其餘歷史樣本累積中")
    else:
        add("預測自我校正", "fallback", "尚未套用（歷史樣本累積中，約需 5+ 個交易日）")

    # 加權指數預測
    taiex = quotes.get("TAIEX_PRED", {}) or {}
    if taiex.get("pred_open"):
        n = taiex.get("signal_count", 0)
        add("加權指數預測", "ok" if n >= 3 else "fallback",
            f"{n}/3 訊號・{taiex.get('consensus', '')}")
    else:
        add("加權指數預測", "error", taiex.get("error", "三訊號全缺"))

    # 夜盤台指期
    night = quotes.get("NIGHT_TXF", {}) or {}
    if night.get("night_pct") is not None:
        add("夜盤台指期", "ok", f"{night.get('night_pct'):+}%")
    else:
        add("夜盤台指期", "error", "抓取失敗或尚未更新")

    # TAIFEX 外資台指期未平倉
    taifex = quotes.get("TAIFEX_OI", {}) or {}
    if taifex.get("foreign_oi_net") is not None:
        add("TAIFEX 外資台指期未平倉", "ok", f"{taifex.get('foreign_oi_net'):+} 口")
    else:
        add("TAIFEX 外資台指期未平倉", "error", "抓取失敗")

    # TWSE 融資融券
    margin = quotes.get("MARGIN", {}) or {}
    if margin.get("margin_balance"):
        add("TWSE 融資融券", "ok", str(margin.get("date", "")))
    else:
        add("TWSE 融資融券", "error", "抓取失敗")

    # SEC 8-K（涵蓋 NASDAQ-100 + TSMC ADR；空清單也算 ok）
    sec = quotes.get("SEC_FILINGS", []) or []
    add("SEC 8-K 公告 (NDX-100 + TSMC)", "ok", f"{len(sec)} 筆")

    # 台股前 10 大公司 MOPS 重大訊息（空清單 = 真無公告 OR 來源不可用，視為 fallback 不算 error）
    mops = quotes.get("TW_MOPS", []) or []
    if mops:
        add("MOPS 重大訊息 (台股前 10)", "ok", f"{len(mops)} 筆")
    else:
        add("MOPS 重大訊息 (台股前 10)", "fallback", "近 48h 無公告或來源暫不可用")

    # RSS 新聞
    n_news = len(news or [])
    if n_news >= 10:
        add("RSS 新聞", "ok", f"{n_news} 則")
    elif n_news > 0:
        add("RSS 新聞", "fallback", f"僅 {n_news} 則（部分來源失敗）")
    else:
        add("RSS 新聞", "error", "全部來源失敗")

    # 台股 universe（市值前 100）籌碼
    # 注意：snapshot 即使三大法人 fetch 失敗也會有 100 檔（全填 0），
    # 故除了數量，還要檢查「真有非零法人買賣超的檔數」。
    n_uni = len(tw0050 or [])
    n_inst = sum(1 for s in (tw0050 or [])
                 if (s.get("foreign_lot") or s.get("invest_lot") or s.get("dealer_lot")))
    uni_fallback = bool(quotes.get("TW_UNIVERSE_FALLBACK"))
    uni_src = "0050 硬編清單（動態抓取失敗）" if uni_fallback else "市值前 100 動態"
    inst_ratio = (n_inst / n_uni) if n_uni else 0
    if n_uni == 0:
        add("台股 universe 籌碼", "error", "抓取失敗")
    elif inst_ratio < 0.3:
        # snapshot 有 100 檔但三大法人都是 0 —— TWSE 端點抓失敗的徵狀
        add("台股 universe 籌碼", "error",
            f"{n_uni} 檔但僅 {n_inst} 檔有法人買賣超 → 三大法人端點抓取失敗")
    elif inst_ratio < 0.7 or uni_fallback:
        add("台股 universe 籌碼", "fallback",
            f"{n_uni} 檔・{n_inst} 檔有法人資料・{uni_src}")
    else:
        add("台股 universe 籌碼", "ok",
            f"{n_uni} 檔・{n_inst} 檔有法人資料・{uni_src}")

    # 台股月營收（基本面）
    n_rev = sum(1 for s in (tw0050 or []) if s.get("rev_yoy_pct") is not None)
    if n_rev >= 50:
        add("台股月營收 YoY", "ok", f"{n_rev} 檔有營收年增率")
    elif n_rev > 0:
        add("台股月營收 YoY", "fallback", f"僅 {n_rev} 檔有營收資料")
    else:
        add("台股月營收 YoY", "error", "TWSE 月營收抓取失敗")

    # 大戶持股比例（TDCC 集保股權分散表）
    n_mh = sum(1 for s in (tw0050 or []) if s.get("major_holder_pct") is not None)
    if n_mh >= 50:
        add("大戶持股比例 (TDCC)", "ok", f"{n_mh} 檔有大戶籌碼資料")
    elif n_mh > 0:
        add("大戶持股比例 (TDCC)", "fallback", f"僅 {n_mh} 檔有資料")
    else:
        add("大戶持股比例 (TDCC)", "error", "TDCC 集保資料抓取失敗")

    n_err = sum(1 for d in dq if d["status"] == "error")
    n_fb = sum(1 for d in dq if d["status"] == "fallback")
    print(f"[data_quality] {len(dq)} 項來源：ok={len(dq)-n_err-n_fb}, fallback={n_fb}, error={n_err}")
    return dq


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
    usdtwd_today, usdtwd_prev = fetch_usdtwd_pair()
    quotes["USDTWD"] = usdtwd_today
    quotes["USDTWD_prev"] = usdtwd_prev

    # 1.5 抓 4+1 個總經指標
    print("[main] 抓總經指標…")
    macro = fetch_macro_indicators()
    quotes["MACRO"] = macro

    # 2. 抓 00662 昨收 —— 以 TWSE 官方收盤價為準。
    #    Yahoo 對 00662.TW 常落後一天/卡價，會把錯誤昨收一路汙染到合理價估值。
    q662 = fetch_quote("00662.TW")
    last_00662 = q662.get("close")
    twse_662_close = fetch_twse_close("00662")
    if twse_662_close:
        if last_00662 and abs(twse_662_close - last_00662) / twse_662_close > 0.003:
            print(f"[main] 00662 昨收以 TWSE 官方為準：Yahoo {last_00662} → TWSE {twse_662_close}",
                  file=sys.stderr)
        last_00662 = twse_662_close
    elif last_00662 is None:
        print("[main] 00662 昨收 Yahoo + TWSE 皆失敗", file=sys.stderr)

    # 3. 抓 2330 歷史
    hist_2330 = fetch_2330_recent()

    # 4. 計算（升級版：NAV + 折溢價 + 匯率變動 + ADR 衰減）
    #    QQQ / TSM 任一抓取失敗時走降級：回傳 error dict，render_html 會顯示「資料缺失」而非整包爆掉。
    qqq_q = require_quote(quotes, "QQQ")
    tsm_q = require_quote(quotes, "TSM")
    if qqq_q is not None:
        fair = calc_00662_fair_value(
            qqq_q["close"], qqq_q["prev_close"],
            usdtwd_today, last_00662, usdtwd_prev=usdtwd_prev,
        )
    else:
        fair = {"error": "QQQ 行情抓取失敗，無法估算 00662 合理價"}
        print("[main] QQQ 行情缺失 → 00662 估值降級", file=sys.stderr)
    if tsm_q is not None:
        predictions = calc_2330_predictions(
            tsm_q["close"], tsm_q["prev_close"],
            usdtwd_today, hist_2330,
        )
    else:
        predictions = {"error": "TSM ADR 行情抓取失敗，無法預測 2330 開盤價"}
        print("[main] TSM 行情缺失 → 2330 預測降級", file=sys.stderr)

    # 5. 抓新聞
    print("[main] 抓新聞中…")
    news = fetch_news()
    print(f"[main] 抓到 {len(news)} 則新聞")

    # 5.05 新聞去重（同事件常被多個 RSS 重貼，去重後 LLM 訊號更乾淨）
    news = dedup_news(news)

    # 5.1 (Task B) 新聞重要性分類
    news = classify_news_importance(news)

    # 5.2 (Task A) 對 critical 事件抓全文
    print("[main] 對重大事件擷取全文…")
    try:
        # 同時對 critical 與 high 級新聞抓全文(個股新聞多半屬 high,只有 RSS snippet
        # 會讓 LLM 因「沒有具體事實」而把該公司刪掉,報告變稀薄)
        news = fetch_news_fulltext(news, max_critical=10, max_high=10)
    except Exception as e:
        print(f"[main] 全文擷取失敗: {e}", file=sys.stderr)

    # 5.3 (Task C) SEC 8-K 主要公司公告
    print("[main] 抓 SEC 8-K 主要公司公告…")
    try:
        sec_filings = fetch_sec_filings()
    except Exception as e:
        print(f"[main] SEC 抓取失敗: {e}", file=sys.stderr)
        sec_filings = []

    # 5.4 (Task E) TAIFEX 外資台指期未平倉
    print("[main] 抓 TAIFEX 三大法人台指期未平倉…")
    try:
        taifex_oi = fetch_taifex_foreign_futures()
    except Exception as e:
        print(f"[main] TAIFEX 抓取失敗: {e}", file=sys.stderr)
        taifex_oi = {}

    # 5.5 (Opt 4) TWSE 融資融券
    print("[main] 抓 TWSE 融資融券…")
    try:
        margin = fetch_twse_margin()
    except Exception as e:
        print(f"[main] 融資融券抓取失敗: {e}", file=sys.stderr)
        margin = {}

    # 5.6 (Opt 6) 一週動能對比
    print("[main] 計算一週動能…")
    try:
        weekly = fetch_weekly_momentum()
    except Exception as e:
        print(f"[main] 週動能失敗: {e}", file=sys.stderr)
        weekly = {}

    # 5.7 (Opt 7) 2330 法說會週判斷
    earnings_proximity = check_tsmc_earnings_proximity()
    print(f"[main] 法說會狀態: {earnings_proximity['note']}")

    # 5.8 (Opt 1) 載入歷史記憶（90 天，供預測校準與回溯；prompt 仍只顯示近 7 天敘事流）
    history = load_history_state(days=90)

    # 5.9 (Task B) 抓 TAIFEX 夜盤台指期
    print("[main] 抓 TAIFEX 夜盤台指期…")
    try:
        night_txf = fetch_taifex_night_session()
    except Exception as e:
        print(f"[main] 夜盤抓取失敗: {e}", file=sys.stderr)
        night_txf = {}

    # 5.10 (Task A) 加權指數預測 —— TAIEX 昨收以 TWSE 官方為準，避免 Yahoo ^TWII 偶發錯值
    print("[main] 計算加權指數預測…")
    try:
        taiex_hist = fetch_taiex_history()
        twse_taiex_close = fetch_twse_taiex_close()
        if twse_taiex_close and taiex_hist is not None and not taiex_hist.empty:
            yahoo_last = safe_float(taiex_hist.iloc[-1]["Close"]) or 0
            if yahoo_last and abs(twse_taiex_close - yahoo_last) / twse_taiex_close > 0.003:
                print(f"[main] TAIEX 昨收以 TWSE 為準：Yahoo {yahoo_last:.2f} → TWSE {twse_taiex_close:.2f}",
                      file=sys.stderr)
                # 用 .loc 覆寫最後一筆 Close（pandas 不喜歡 iloc 賦值）
                last_idx = taiex_hist.index[-1]
                taiex_hist.loc[last_idx, "Close"] = twse_taiex_close
        macro = quotes.get("MACRO", {}) or {}
        sox_pct = (macro.get("SOX", {}) or {}).get("change_pct")
        tsm_pct = quotes["TSM"].get("change_pct")
        night_pct = night_txf.get("night_pct")
        taiex_pred = calc_taiex_prediction(taiex_hist, sox_pct, tsm_pct, night_pct)
    except Exception as e:
        print(f"[main] 加權預測失敗: {e}", file=sys.stderr)
        taiex_pred = {}

    # 5.105 預測自我校正：2330 模型誤差加權 + 三個預測的 bias 修正
    print("[main] 套用預測自我校正（模型加權 + bias 修正）…")
    try:
        fair, predictions, taiex_pred = calibrate_predictions(
            fair, predictions, taiex_pred, history)
    except Exception as e:
        print(f"[main] 預測校正失敗（沿用未校正值）: {e}", file=sys.stderr)

    # 5.106 0050 ETF 開盤預測（0.5 × 2330 + 0.5 × 加權指數）
    print("[main] 計算 0050 開盤預測…")
    last_0050 = fetch_twse_close("0050")
    try:
        tw0050_pred = calc_0050_prediction(last_0050, predictions, taiex_pred)
    except Exception as e:
        print(f"[main] 0050 預測失敗: {e}", file=sys.stderr)
        tw0050_pred = {"error": str(e)[:80]}

    # 5.107 大盤成交額 + 市場廣度（從 STOCK_DAY_ALL 計算上漲/下跌家數比）
    print("[main] 計算大盤成交額 + 市場廣度…")
    try:
        breadth = fetch_twse_market_breadth()
    except Exception as e:
        print(f"[main] 廣度抓取失敗: {e}", file=sys.stderr)
        breadth = {}

    # 5.11 (Task F) 預測準確度回溯
    print("[main] 計算預測準確度回溯…")
    backtest_block = build_prediction_backtest(history)

    # 6. 抓台股市值前 100 大 universe + 法人/表現（含 30 日累積）
    print("[main] 抓台股市值前 100 大 universe…")
    try:
        tw_universe = fetch_tw_top100_universe(top_n=100)
    except Exception as e:
        print(f"[main] universe 抓取失敗，用 fallback: {e}", file=sys.stderr)
        tw_universe = _fallback_universe()
    quotes["TW_UNIVERSE_FALLBACK"] = any(
        v.get("fallback") for v in tw_universe.values())

    # 6.1 (籌碼悄悄站隊) 個股融資餘額(MI_MARGN ALL)+ TDCC 大戶 WoW 變化
    print("[main] 抓個股融資餘額(MI_MARGN ALL)…")
    try:
        margin_per_stock = fetch_twse_margin_per_stock(set(tw_universe.keys()))
    except Exception as e:
        print(f"[main] 個股融資抓取失敗: {e}", file=sys.stderr)
        margin_per_stock = {}
    # TDCC WoW Δ%(對照 history 中 ≥ 5 天前的快照)
    try:
        current_tdcc = fetch_tdcc_major_holders(set(tw_universe.keys()))
        tdcc_wow_map = calc_tdcc_wow_delta(current_tdcc, history, min_gap_days=5)
        # 同時準備本次 TDCC 快照,寫進 state 供下次 WoW 比較
        tdcc_snapshot_for_state = {
            c: round(v.get("major_holder_pct", 0), 2)
            for c, v in current_tdcc.items()
            if v.get("major_holder_pct") is not None
        }
    except Exception as e:
        print(f"[main] TDCC WoW 計算失敗: {e}", file=sys.stderr)
        tdcc_wow_map = {}
        tdcc_snapshot_for_state = {}

    print("[main] 抓台股 universe 法人買賣超與近期表現…")
    try:
        tw0050 = fetch_tw0050_snapshot(tw_universe,
                                          tdcc_wow_map=tdcc_wow_map,
                                          margin_per_stock=margin_per_stock)
    except Exception as e:
        print(f"[main] universe snapshot 抓取失敗: {e}", file=sys.stderr)
        tw0050 = []

    # 6.2 台股前 10 大公司 MOPS 重大訊息
    print("[main] 抓台股前 10 大公司 MOPS 重大訊息…")
    try:
        top10_codes = [c for c, _ in sorted(
            tw_universe.items(),
            key=lambda kv: kv[1].get("market_cap") or 0, reverse=True)[:10]]
        tw_mops = fetch_tw_major_announcements(top10_codes)
    except Exception as e:
        print(f"[main] MOPS 抓取失敗: {e}", file=sys.stderr)
        tw_mops = []

    # 6.5 建立歷史校準資料（TSM vs 2330 開盤實證對照）
    calibration = build_historical_calibration(hist_2330, days=7)
    print(f"[main] 歷史校準資料已生成（{len(calibration)} 字）")

    # 6.55 偵測美股是否昨日休市（Memorial Day / Labor Day / 聖誕等）
    quotes["US_HOLIDAY"] = detect_us_holiday(quotes, now_tpe.date())
    if quotes["US_HOLIDAY"].get("detected"):
        print(f"[main] ⚠ 偵測到美股休市:QQQ.date={quotes['US_HOLIDAY'].get('actual_date')} "
              f"(預期 {quotes['US_HOLIDAY'].get('expected_date')},gap={quotes['US_HOLIDAY'].get('gap_days')} 天)",
              file=sys.stderr)

    # 6.58 中期動能指標 + 1週/1月波動度區間（2330/00662/0050）
    #      必須先算好,detect_market_alerts 才能看到 5d 動能觸發過熱/超賣警示。
    print("[main] 計算 2330/00662/0050 中期展望…")
    midterm: dict = {}
    try:
        if hist_2330 is not None and not hist_2330.empty:
            m = calc_momentum_metrics(hist_2330["Close"])
            if m:
                midterm["2330"] = {"metrics": m,
                                   "forecast": calc_midterm_forecast(m),
                                   "trend": _trend_label(m)}
        for code, name in (("00662.TW", "00662"), ("0050.TW", "0050")):
            try:
                d = yf.Ticker(code).history(period="3mo", auto_adjust=False)
                d = d.dropna(subset=["Close"])
                d = d[d["Close"] > 0]
                if not d.empty:
                    m = calc_momentum_metrics(d["Close"])
                    if m:
                        midterm[name] = {"metrics": m,
                                         "forecast": calc_midterm_forecast(m),
                                         "trend": _trend_label(m)}
            except Exception as e:
                print(f"[midterm] {code} 失敗: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[midterm] 整體失敗: {e}", file=sys.stderr)
    quotes["MIDTERM"] = midterm

    # 6.6 (Task H) 偵測過熱警告（含 US_HOLIDAY + 過熱/超賣警示）
    alerts = detect_market_alerts(quotes, fair, predictions, taifex_oi)
    print(f"[main] 偵測到 {len(alerts)} 個警告訊號")

    # 把 SEC + TAIFEX + 新增資料包進 quotes
    quotes["SEC_FILINGS"] = sec_filings
    quotes["TW_MOPS"] = tw_mops
    quotes["TAIFEX_OI"] = taifex_oi
    quotes["MARGIN"] = margin
    quotes["WEEKLY"] = weekly
    quotes["EARNINGS_PROXIMITY"] = earnings_proximity
    quotes["HISTORY"] = history
    quotes["NIGHT_TXF"] = night_txf
    quotes["TAIEX_PRED"] = taiex_pred
    quotes["TW0050_PRED"] = tw0050_pred
    # 把 universe snapshot 也塞進 quotes,讓 render_html 可以畫「籌碼悄悄站隊 Top 10」
    quotes["TW_UNIVERSE_SNAPSHOT"] = tw0050
    quotes["BREADTH"] = breadth

    # 6.65 個人持股今日預估漲幅(隱私:只算彙總 % + 金額,不揭露明細)
    if PORTFOLIO_1 or PORTFOLIO_2:
        print("[main] 計算個人持股今日預估漲幅…")
        try:
            # 加權預測 %(其他個股 beta 基準 + 槓桿ETF taiex 基準);用校正後 pred_open 回推
            tp_open = (taiex_pred or {}).get("pred_open")
            tp_last = (taiex_pred or {}).get("last_close")
            taiex_pct = ((tp_open / tp_last - 1) * 100) if (tp_open and tp_last) else None
            # 專屬模型預測 %(2330/0050/00662 + 槓桿 ETF 如 00631L=2×0050)
            special_preds = build_special_preds(predictions, tw0050_pred, fair, taiex_pct)
            # 抓持股昨收 + beta;加權歷史供 beta 回歸(可能未定義 → 防護)
            _taiex_hist = locals().get("taiex_hist")
            all_codes = {**PORTFOLIO_1, **PORTFOLIO_2}
            close_map, beta_map = fetch_portfolio_market_data(all_codes, _taiex_hist)
            # 大三標的昨收用權威值覆寫(Yahoo 對 ETF 常落後)
            p2_last = predictions.get("last_2330") if isinstance(predictions, dict) else None
            if p2_last:
                close_map["2330"] = p2_last
            if isinstance(tw0050_pred, dict) and tw0050_pred.get("last"):
                close_map["0050"] = tw0050_pred["last"]
            if isinstance(fair, dict) and fair.get("last_00662_price"):
                close_map["00662"] = fair["last_00662_price"]
            # 槓桿 ETF(00631L 等)昨收用 TWSE 官方覆寫(Yahoo 對槓桿 ETF 也落後)
            for lev_code in LEVERAGED_ETF:
                if lev_code in all_codes:
                    tc = fetch_twse_close(lev_code)
                    if tc:
                        close_map[lev_code] = tc
            quotes["PORTFOLIO_FORECAST"] = {
                "p1": calc_portfolio_forecast(PORTFOLIO_1, taiex_pct, special_preds, close_map, beta_map),
                "p2": calc_portfolio_forecast(PORTFOLIO_2, taiex_pct, special_preds, close_map, beta_map),
                "p1_name": PORTFOLIO_1_NAME,
                "p2_name": PORTFOLIO_2_NAME,
            }
        except Exception as e:
            print(f"[main] 持股預測失敗(不影響晨報): {e}", file=sys.stderr)
            quotes["PORTFOLIO_FORECAST"] = {}
    quotes["BACKTEST"] = backtest_block
    quotes["ALERTS"] = alerts

    # 6.7 彙整資料品質（讓 LLM 與 HTML 都知道哪些來源失敗 / 降級）
    quotes["DATA_QUALITY"] = build_data_quality(quotes, fair, predictions, news, tw0050)

    # 7. LLM 分析
    print(f"[main] 呼叫 LLM 分析… (provider={LLM_PROVIDER})")
    analysis = call_llm_analysis(quotes, fair, predictions, news, tw0050, calibration)

    # 8. 組信
    html = render_html(quotes, fair, predictions, analysis, report_date, mode)

    # 8.5 (Opt 1) 寫入今日記憶到 state file
    try:
        crit_titles = [n["title"] for n in news if n.get("importance") == "critical"][:5]
        top10_inst_total = sum(
            (s.get("foreign_lot", 0) + s.get("invest_lot", 0))
            for s in (tw0050[:10] if tw0050 else [])
        )
        new_entry = {
            "date": dt.datetime.now(TPE).strftime("%Y-%m-%d"),
            "weekday": dt.datetime.now(TPE).strftime("%a"),
            "qqq_pct": quotes["QQQ"].get("change_pct"),
            "tsm_pct": quotes["TSM"].get("change_pct"),
            "spy_pct": quotes["SPY"].get("change_pct"),
            "vix": (quotes.get("MACRO", {}) or {}).get("VIX", {}).get("close"),
            "sox_pct": (quotes.get("MACRO", {}) or {}).get("SOX", {}).get("change_pct"),
            "usdtwd": quotes.get("USDTWD"),
            "fair_00662": fair.get("fair_price"),
            # 三個 model 的「原始」預測值（供 calibrate_predictions 算各模型 MAE 與權重）
            "model1_2330": predictions.get("model1_1to1"),
            "model2_2330": predictions.get("model2_regression"),
            "model3_2330": predictions.get("model3_adr_decay"),
            "model4_2330": predictions.get("model4_momentum"),
            "momentum_5d_pct_2330": predictions.get("momentum_5d_pct"),
            # 經誤差加權 + bias 校正後的最終 2330 預測（供下次算 bias）
            "weighted_final_2330": predictions.get("weighted_final"),
            "foreign_top10_total": round(top10_inst_total, 0),
            "pred_taiex": taiex_pred.get("pred_open"),
            # 0050 開盤預測（供下次 backtest 對比）
            "pred_0050": tw0050_pred.get("pred_open") if isinstance(tw0050_pred, dict) else None,
            "last_0050": tw0050_pred.get("last") if isinstance(tw0050_pred, dict) else None,
            "night_txf_pct": night_txf.get("night_pct"),
            "taifex_foreign_oi": taifex_oi.get("foreign_oi_net"),
            "critical_news": crit_titles,
            "earnings_proximity": earnings_proximity.get("impact"),
            # 籌碼悄悄站隊:本次 TDCC 大戶持股快照,供下次 WoW Δ% 比較
            "tdcc_snapshot": tdcc_snapshot_for_state if 'tdcc_snapshot_for_state' in locals() else {},
        }
        save_history_state(new_entry, days_to_keep=90)
    except Exception as e:
        print(f"[main] 寫入歷史記憶失敗（不影響寄信）: {e}", file=sys.stderr)

    # 9. dry-run 模式：只輸出檔案
    if os.environ.get("DRY_RUN") == "1":
        out = "/tmp/morning_report_preview.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[main] DRY_RUN — 預覽寫入 {out}")
        return 0

    # 10. 寄信
    subject = f"📈 美股晨報 {report_date} | QQQ {quotes['QQQ'].get('change_pct','?')}% / TSM {quotes['TSM'].get('change_pct','?')}%"
    send_email(html, subject)
    return 0


if __name__ == "__main__":
    sys.exit(main())
