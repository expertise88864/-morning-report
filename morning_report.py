"""
美股收盤晨報自動化
=================
每日台灣時間 07:00 抓取昨晚美股 (QQQ / TSM / SPY) 收盤價，
換算 00662 公允淨值、雙模型預測 2330 開盤合理價，
並用 Claude API 產生新聞速報與分析，最後以 Gmail SMTP 寄出。

執行條件 (cron 已處理)：台灣時間週二至週六 07:00。週一另判斷。
"""

from __future__ import annotations

import datetime as dt
import os
import smtplib
import ssl
import sys
import textwrap
from email.message import EmailMessage
from typing import Optional
from zoneinfo import ZoneInfo

import anthropic
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
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

# RSS 新聞來源（中、英、Fed）
RSS_FEEDS = {
    "Reuters Tech":     "https://www.reuters.com/arc/outboundfeeds/rss/category/technology/?outputType=xml",
    "Reuters Markets":  "https://www.reuters.com/arc/outboundfeeds/rss/category/markets/?outputType=xml",
    "CNBC Top News":    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "CNBC Tech":        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910",
    "Bloomberg Markets":"https://feeds.bloomberg.com/markets/news.rss",
    "Federal Reserve":  "https://www.federalreserve.gov/feeds/press_all.xml",
    "鉅亨美股":          "https://api.cnyes.com/media/api/v1/newslist/category/us_stock?limit=20&page=1",  # JSON
    "鉅亨台股":          "https://news.cnyes.com/rss/cat/tw_stock",
    "工商時報財經":      "https://www.chinatimes.com/rss/realtimenews-finance.xml",
}

# ---------- 工具函式 ----------
def safe_float(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_quote(ticker: str) -> dict:
    """抓最新收盤、前一日收盤、漲跌幅、成交量。yfinance 為主。"""
    t = yf.Ticker(ticker)
    hist = t.history(period="10d", auto_adjust=False)
    if hist.empty:
        return {"ticker": ticker, "error": "no data"}
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
    """USD/TWD 即期匯率 (Yahoo Finance: TWD=X)。"""
    try:
        d = yf.Ticker("TWD=X").history(period="5d")
        if d.empty:
            return None
        return round(safe_float(d.iloc[-1]["Close"]), 4)
    except Exception:
        return None


def fetch_2330_recent() -> Optional[pd.DataFrame]:
    """抓 2330.TW 近 60 日收盤，供回歸用。"""
    try:
        d = yf.Ticker("2330.TW").history(period="3mo", auto_adjust=False)
        return d if not d.empty else None
    except Exception:
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
    # 我們需要 TSM 與 USD/TWD 同期歷史
    try:
        tsm_hist = yf.Ticker("TSM").history(period="3mo", auto_adjust=False)
        fx_hist = yf.Ticker("TWD=X").history(period="3mo", auto_adjust=False)
        # 對齊日期
        df = pd.DataFrame({
            "tsm": tsm_hist["Close"],
            "fx":  fx_hist["Close"],
            "t2330": hist_2330["Close"],
        }).dropna()
        if len(df) < 20:
            model2 = None
        else:
            df["theo_tw"] = df["tsm"] * df["fx"] / 5.0   # 1 ADR = 5 股
            df["ratio"] = df["t2330"] / df["theo_tw"]
            avg_ratio = df["ratio"].tail(60).mean()
            today_theo = tsm_close * usdtwd / 5.0
            model2 = today_theo * avg_ratio
    except Exception:
        model2 = None

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
                    data = r.json().get("items", {}).get("data", [])
                    for d in data[:10]:
                        items.append({
                            "source": source,
                            "title": d.get("title", ""),
                            "summary": d.get("summary", "")[:300],
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


def call_claude_analysis(quotes: dict, fair: dict, predictions: dict, news: list[dict]) -> str:
    """請 Claude 寫 5 分鐘版科技財經速報。"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    news_block = "\n".join(
        f"- [{n['source']}] {n['title']}（{n.get('summary','')[:150]}）"
        for n in news[:40]
    )

    prompt = f"""你是專業科技股財經分析師。請依下列資料，用繁體中文寫一份 5 分鐘可讀完的早晨速報，給一位重押 00662（NASDAQ-100）與 2330（台積電）的投資人。

【昨日美股收盤】
- QQQ：{quotes['QQQ']}
- TSM (台積電 ADR)：{quotes['TSM']}
- SPY：{quotes['SPY']}
- USD/TWD：{quotes.get('USDTWD')}

【今日 00662 估值】
{fair}

【今日 2330 雙模型預測】
{predictions}

【近 24 小時新聞清單】
{news_block}

請依以下結構撰寫，務求精煉，不要客套：

## 一、昨夜重點（3 行內）
直接點出影響 00662 / 2330 最關鍵的 3 件事。

## 二、科技板塊脈動（5-8 條）
每條 2 句以內：發生什麼 + 為何重要。聚焦半導體、AI、雲端、Mag7。

## 三、總體環境（利率 / 美元 / VIX / 通膨）
精準摘要。如有 Fed 官員談話、CPI、PPI、就業數據，必列。

## 四、我的分析與見解
- 今日台股開盤情境推演（樂觀 / 中性 / 悲觀 各一句話）
- 2330 開盤該關注什麼價位
- 00662 是否有套利空間
- 風險提示

## 五、一句話結論
給一個明確、不模糊的市場立場。
"""

    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


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

    return f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, "Microsoft JhengHei", sans-serif; max-width: 720px; margin: 0 auto; color: #111; line-height: 1.6; }}
  h1 {{ color: #1e3a8a; border-bottom: 3px solid #1e3a8a; padding-bottom: 8px; }}
  h2 {{ color: #1e40af; margin-top: 24px; border-left: 4px solid #1e40af; padding-left: 8px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 8px 0; }}
  td, th {{ border: 1px solid #ddd; padding: 6px 10px; }}
  th {{ background: #f3f4f6; }}
  .badge {{ background: #1e3a8a; color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
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
  <p style="font-size:11px;color:#888">本信件由自動化腳本於 GitHub Actions 產生。資料來源：Yahoo Finance、Reuters、CNBC、Bloomberg、Federal Reserve、鉅亨網、工商時報。分析由 Claude {CLAUDE_MODEL} 生成，僅供參考，不構成投資建議。</p>
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

    # 6. Claude 分析
    print("[main] 呼叫 Claude 分析…")
    analysis = call_claude_analysis(quotes, fair, predictions, news)

    # 7. 組信
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
