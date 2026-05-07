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


def fetch_twse_institutional() -> dict[str, dict]:
    """
    從 TWSE OpenAPI 抓昨日三大法人買賣超。
    回傳：{ "2330": {"foreign": +123456, "investment": -2000, "dealer": +500, "total": ...}, ... }
    單位：股數（負為賣超）。

    TWSE 欄位名近年多次變更，此函式採「自動偵測」策略：
    從 row 的 keys 中找包含 Foreign/Investment/Dealer 的欄位，分別找出買、賣、買賣超。
    """
    url = "https://openapi.twse.com.tw/v1/fund/T86"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        print(f"[twse] 法人 API 失敗: {e}", file=sys.stderr)
        return {}

    if not rows:
        print("[twse] API 回傳空陣列（可能今天非交易日或資料尚未更新）", file=sys.stderr)
        return {}

    # === 自動偵測欄位名 ===
    sample_keys = list(rows[0].keys())
    print(f"[twse] 樣本欄位：{sample_keys}")

    def find_key(*needles: str) -> Optional[str]:
        """找出欄位名包含所有 needles（大小寫無關）的第一個 key。"""
        for k in sample_keys:
            kl = k.lower()
            if all(n.lower() in kl for n in needles):
                return k
        return None

    # 找買、賣、買賣超欄位（優先用「買賣超」）
    # 已知歷年版本：
    #   2024 前: ForeignInvestorsBuySellOver / InvestmentTrustBuySellOver / DealerBuySellOver
    #   2025+:   ForeignInvestorBuy/ForeignInvestorSell/ForeignInvestorOver
    #            或 ForeignInstitutionalInvestorsBuySellOver 等
    # 策略：先找含 "BuySellOver" 或 "Over" 結尾，找不到才用 Buy-Sell 計算
    f_over = find_key("foreign", "over") or find_key("foreign", "buysell")
    t_over = find_key("invest", "trust", "over") or find_key("invest", "trust", "buysell") \
            or find_key("trust", "over")
    d_over = find_key("dealer", "over") or find_key("dealer", "buysell")

    # 若沒找到 Over 欄位，試找 Buy / Sell 欄位
    f_buy  = find_key("foreign", "buy") if not f_over else None
    f_sell = find_key("foreign", "sell") if not f_over else None
    t_buy  = find_key("invest", "trust", "buy") if not t_over else None
    t_sell = find_key("invest", "trust", "sell") if not t_over else None
    d_buy  = find_key("dealer", "buy") if not d_over else None
    d_sell = find_key("dealer", "sell") if not d_over else None

    print(f"[twse] 偵測欄位 外資={f_over or (f_buy, f_sell)} "
          f"投信={t_over or (t_buy, t_sell)} 自營={d_over or (d_buy, d_sell)}")

    # 找代號欄位
    code_key = find_key("code") or find_key("symbol") or find_key("stock")
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
            "temperature": 0.7,
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


# ---------- HTML 組版 ----------
def render_html(quotes: dict, fair: dict, predictions: dict, analysis: str,
                report_date: str, mode: str) -> str:
    def fmt_quote(q: dict) -> str:
        if "error" in q:
            return f"<tr><td>{q['ticker']}</td><td colspan='4'>{q['error']}</td></tr>"
        color = "#16a34a" if (q["change_pct"] or 0) >= 0 else "#dc2626"
        sign = "+" if (q["change_pct"] or 0) >= 0 else ""
        return (
            f"<tr>"
            f"<td><b>{q['ticker']}</b></td>"
            f"<td>{q['close']}</td>"
            f"<td style='color:{color}'>{sign}{q['change_pct']}%</td>"
            f"<td>{q['high']} / {q['low']}</td>"
            f"<td>{q.get('volume','')}</td>"
            f"</tr>"
        )

    quote_rows = "".join(fmt_quote(q) for k, q in quotes.items() if k != "USDTWD")

    fair_html = ""
    if "error" not in fair:
        sign = "+" if fair["implied_change_pct"] >= 0 else ""
        fair_html = f"""
        <table>
          <tr><td>QQQ 漲跌幅</td><td>{sign}{fair['qqq_pct']}%</td></tr>
          <tr><td>00662 昨收參考</td><td>{fair['last_00662_price']}</td></tr>
          <tr><td><b>00662 今日合理價估值</b></td><td><b>{fair['fair_price']}</b></td></tr>
          <tr><td>USD/TWD</td><td>{fair['usdtwd']}</td></tr>
        </table>
        <p style="font-size:12px;color:#666">註：估值未計入溢/折價、申購贖回價差、隔日匯率變動。</p>
        """

    pred_html = ""
    if "error" not in predictions:
        m2 = predictions.get("model2_regression", "—")
        rng = predictions.get("range")
        rng_html = f"<tr><td>合理區間</td><td>{rng[0]} ~ {rng[1]} (中值 {predictions['mid']})</td></tr>" if rng else ""
        pred_html = f"""
        <table>
          <tr><td>2330 昨收</td><td>{predictions['last_2330']}</td></tr>
          <tr><td>TSM ADR 漲跌幅</td><td>{predictions['tsm_pct']}%</td></tr>
          <tr><td>模型1（1:1 漲跌對應）</td><td>{predictions['model1_1to1']}</td></tr>
          <tr><td>模型2（60日比值回歸）</td><td>{m2}</td></tr>
          {rng_html}
        </table>
        """

    analysis_html = analysis.replace("\n", "<br>") \
                            .replace("##", "<h3 style='color:#1e40af;margin-top:16px'>") \
                            .replace("<br><h3", "</h3><h3")

    # 將「## 六、今日台股關注三檔」段落特別包成黃色強調框（如有）
    if "今日台股關注三檔" in analysis_html:
        analysis_html = analysis_html.replace(
            "<h3 style='color:#1e40af;margin-top:16px'> 六、今日台股關注三檔",
            "</div><div class='tw-pick-box'><h3 style='color:#92400e;margin-top:0'>★ 六、今日台股關注三檔",
            1,
        )
        # 在第七段標題前關閉黃色框
        analysis_html = analysis_html.replace(
            "<h3 style='color:#1e40af;margin-top:16px'> 七、",
            "</div><h3 style='color:#1e40af;margin-top:16px'> 七、",
            1,
        )

    return f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, "Microsoft JhengHei", sans-serif; max-width: 760px; margin: 0 auto; color: #111; line-height: 1.7; padding: 8px; }}
  h1 {{ color: #1e3a8a; border-bottom: 3px solid #1e3a8a; padding-bottom: 8px; }}
  h2 {{ color: #1e40af; margin-top: 24px; border-left: 4px solid #1e40af; padding-left: 8px; }}
  h3 {{ color: #1e40af; }}
  h4 {{ color: #b45309; margin-top: 14px; margin-bottom: 4px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 14px; }}
  td, th {{ border: 1px solid #ddd; padding: 6px 10px; }}
  th {{ background: #f3f4f6; }}
  .badge {{ background: #1e3a8a; color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
  blockquote {{ border-left: 4px solid #b45309; background: #fffbeb; margin: 8px 0; padding: 8px 14px; }}
  .tw-pick-box {{ background: #fef3c7; border: 2px solid #f59e0b; border-radius: 8px; padding: 12px 18px; margin: 16px 0; }}
  .tw-pick-box h3 {{ color: #92400e !important; }}
  .tw-pick-box h4 {{ color: #b45309; border-bottom: 1px dashed #f59e0b; padding-bottom: 4px; }}
  strong {{ color: #1e3a8a; }}
</style></head>
<body>
  <h1>📈 美股晨報 {report_date} <span class="badge">{mode}</span></h1>

  <h2>一、美股收盤行情</h2>
  <table>
    <tr><th>標的</th><th>收盤</th><th>漲跌</th><th>高/低</th><th>成交量</th></tr>
    {quote_rows}
  </table>

  <h2>二、00662 公允淨值換算</h2>
  {fair_html}

  <h2>三、2330 開盤合理價預測</h2>
  {pred_html}

  <h2>四、市場速報與分析</h2>
  <div>{analysis_html}</div>

  <hr>
  <p style="font-size:11px;color:#888">本信件由自動化腳本於 GitHub Actions 產生。資料來源：Yahoo Finance、Reuters、CNBC、Bloomberg、Federal Reserve、鉅亨網、工商時報。分析由 LLM ({LLM_PROVIDER}/{GEMINI_MODEL if LLM_PROVIDER=='gemini' else CLAUDE_MODEL}) 生成，僅供參考，不構成投資建議。</p>
</body></html>
"""


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
