"""
美股收盤晨報自動化
=================
每日台灣時間 06:00 抓取昨晚美股 (QQQ / TSM / SPY) 收盤價，
換算 00662 公允淨值、雙模型預測 2330 開盤合理價，
並用 LLM API 產生新聞速報與分析，最後以 Gmail SMTP 寄出。

支援 LLM 提供商（環境變數 LLM_PROVIDER 控制）：
  - "gemini"    → Google Gemini 2.5 Flash（免費 1500 req/日）
  - "deepseek"  → DeepSeek V4 Pro/Flash（NT$3/月，中文超強，推薦）
  - "anthropic" → Claude Sonnet（NT$46/月，品質最佳）

執行條件 (cron 已處理)：台灣時間週一至週六 06:00。週一另判斷為週末綜合報。
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import smtplib
import ssl
import statistics
import subprocess
import sys
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urljoin, urlparse

import feedparser
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from num_utils import (  # A5-B1:數值基礎工具已抽出(僅依 stdlib),re-export 保相容
    safe_float,
    _to_int,
    _safe_number,
    _sigmoid,
)
from llm_postprocess import (  # A5-Step1:LLM 後處理純函式已抽出,此處 re-export 保相容
    _mask_malformed_numbers,
    _sanitize_llm_2330_prices,
    _strip_llm_watchlist_section,
    _strip_llm_sections,
    _strip_stance_calculation,
    _extract_stance,
    _extract_summary,
    _extract_stance_section,
    _parse_llm_event_json,
)
from render_utils import (  # A5-Step2/B2:渲染純函式已抽出,re-export 保相容
    _format_macro_line,
    _md_to_html,
    _style_analysis_html,
    _wrap_stance,
    _render_kpi_strip,
    _render_model_evidence_html,
    _render_event_calendar_html,
    _podcast_ticker_crosscheck,  # noqa: F401 — re-export:test_podcast 經 mr.* 呼叫,morning_report 本體未直接用
    _render_podcast_html,
    _render_sports_html,
    _mlb_zh,  # noqa: F401 — re-export:tests 經 mr.* 驗證 MLB 中文隊名
)
from news_rules import (  # A5-B3:新聞分類/降噪規則+關鍵字常數已抽出。只 re-export morning_report
    # 本體/測試實際引用者;另 20 個常數與 2 個內部函式僅 news_rules 內部使用,不外露(已驗證零外部引用)。
    NEWS_POSITIVE_TERMS,
    NEWS_NEGATIVE_TERMS,
    TECH_GATE_CATALYST,  # noqa: F401 — re-export:test_news 經 mr.* 讀取
    classify_news_importance,
    dedup_news,
    _matches_any,
    _news_source_grade,
    _credibility_tag,
    _news_keep_score,
    _strip_html,
    _is_low_value_tech_headline,
    _tw_intelligence_topic,
    _tw_intelligence_importance,
    _tw_intelligence_recall_hit,
    _tw_intelligence_timeline_key,
)
from session_calendar import (  # A5-B4:交易日/預測日期工具已抽出。只 re-export 本體/測試引用者;
    # _session_distance/_next_tw_weekday/_actual_open_date_for/_weekday_session_distance 僅內部用,不外露。
    _infer_target_session_date,
    _target_session_date,
    _normalize_history_entries,
    _resolved_prediction_history,
    evaluate_breakout_forecasts,
    build_breakout_tracking,
)
from portfolio_risk import (  # G1:持倉曝險引擎的純數學(合成序列可精確單測)。re-export 供測試以 mr.* 呼叫。
    aligned_returns,
    ols_beta,
    value_weights,
    portfolio_beta,
    scenario_rows,
    stress_rows,
    phrase_multiple,
)

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

    單位為「股數」(直接填股數,零股亦同;非以張為單位)。支援兩種格式:
      JSON:  {"2330": 5000, "2454": 2000}        # 代號 → 股數
      簡易:  2330:5000,2454:2000  或  2330:5000;2454:2000   # 同上,逗號/分號分隔
    股數通常為整數(亦接受小數)。解析失敗回 {}。
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
                shares = float(v)
                if code and shares > 0:
                    out[code] = shares
        else:
            for pair in raw.replace(";", ",").split(","):
                if ":" not in pair:
                    continue
                code, shares_str = pair.split(":", 1)
                code = code.strip()
                shares = float(shares_str.strip())
                if code and shares > 0:
                    out[code] = shares
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        print(f"[portfolio] 設定解析失敗(將略過持股預測): {e}", file=sys.stderr)
        return {}
    return out


# 兩個倉位的持股設定(GitHub Secrets / 環境變數;單位=股數)。未設 → 不顯示持股欄位。
# 注意:個股代號與股數僅存記憶體,信件只顯示彙總漲幅 % 與金額,不揭露明細。
PORTFOLIO_1 = _parse_portfolio(os.environ.get("PORTFOLIO_1", ""))
PORTFOLIO_2 = _parse_portfolio(os.environ.get("PORTFOLIO_2", ""))
# 倉位顯示名稱(可自訂,如「主帳戶」「定存股」);預設「持倉1/持倉2」。
PORTFOLIO_1_NAME = os.environ.get("PORTFOLIO_1_NAME", "持倉1").strip() or "持倉1"
PORTFOLIO_2_NAME = os.environ.get("PORTFOLIO_2_NAME", "持倉2").strip() or "持倉2"

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
# DeepSeek 模型名：
#   deepseek-v4-pro     → V4 Pro（推薦，分析最深，支援思考模式）
#   deepseek-v4-flash   → V4 Flash（便宜版）
# 舊別名 deepseek-chat / deepseek-reasoner 將棄用，不放進降級鏈。
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_EXTRACTOR_MODEL = os.environ.get("DEEPSEEK_EXTRACTOR_MODEL", "deepseek-v4-flash")
# 思考模式強度（high / medium / low；設 off/none 關閉）。
# 僅對 v4-pro / reasoner 生效，可顯著提升分析推理深度（成本略升）。
DEEPSEEK_REASONING_EFFORT = os.environ.get("DEEPSEEK_REASONING_EFFORT", "high").strip().lower()
LLM_REPORT_MAX_TOKENS = int(os.environ.get("LLM_REPORT_MAX_TOKENS", "7000"))
LLM_TOTAL_TIMEOUT_SECONDS = float(os.environ.get("LLM_TOTAL_TIMEOUT_SECONDS", "180"))
LLM_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("LLM_REQUEST_TIMEOUT_SECONDS", "75"))
_LLM_DEADLINE: Optional[float] = None

# ── P0-2 寄信保命時間預算 ──────────────────────────────────────────────
# GitHub Actions job 有 timeout-minutes(本專案 25 分);2026-07-08 曾因 Google News
# 大量 503 × 逐源重試把 job 拖到 25 分被強制取消 → 整封信沒寄出。保命機制:main() 起點
# 記整體 deadline,昂貴且「非核心」的步驟(全文擷取、LLM 事件抽取)在動工前檢查剩餘時間,
# 不足就跳過、用當次已有資料組信寄出——寧可少一塊資料,不可整封信被 timeout 吞掉。
# 核心(行情/預測/LLM 主分析/寄信)永遠執行;主分析本身另有 LLM_TOTAL_TIMEOUT 保護。
RUN_BUDGET_SECONDS = float(os.environ.get("RUN_BUDGET_SECONDS", "1140"))   # 19 分(25 分留 6 分緩衝)
# P0-1 新聞抓取平行度(依 host 分組,不同 host 平行、同 host 序列);設 1 退回序列(逃生門)。
NEWS_FETCH_WORKERS = int(os.environ.get("NEWS_FETCH_WORKERS", "8"))

# ── P1-4 觀測性 run manifest ──────────────────────────────────────────
# 記錄每階段耗時、時間預算降級、各來源抓取結果 → state/run_manifest.json + GitHub Actions
# Step Summary(在 Actions 執行頁直接看得到「時間花在哪、平行化有沒有幫助、哪個來源在掛」)。
# 純市場中性資料(耗時/計數/來源健康),不含任何個人化內容。失敗不影響晨報。
RUN_MANIFEST_FILE = Path("state/run_manifest.json")
_RUN_MANIFEST: dict = {"marks": []}


def _mark_phase(label: str) -> None:
    """在 main() 階段邊界插一個時間標記(相鄰標記差=該階段耗時)。純觀測,不影響流程。"""
    _RUN_MANIFEST["marks"].append((label, time.monotonic()))


def _write_run_manifest(now_tpe) -> None:
    """把本次執行的階段耗時等寫成 manifest + 附到 GitHub Actions Step Summary。失敗不影響晨報。"""
    try:
        marks = _RUN_MANIFEST.get("marks") or []
        phases = [{"label": marks[i][0], "seconds": round(marks[i + 1][1] - marks[i][1], 1)}
                  for i in range(len(marks) - 1)]
        total = round(marks[-1][1] - marks[0][1], 1) if len(marks) >= 2 else 0.0
        feeds = {h: {"ok": int((s or {}).get("ok", 0)), "fail": int((s or {}).get("fail", 0))}
                 for h, s in (_FEED_STATS or {}).items()}
        manifest = {
            "date": now_tpe.strftime("%Y-%m-%d %H:%M"),
            "total_seconds": total,
            "budget_seconds": RUN_BUDGET_SECONDS,
            "news_workers": NEWS_FETCH_WORKERS,
            "degraded_steps": list(dict.fromkeys(_DEGRADED_STEPS)),
            "phases": phases,
            "feeds": feeds,
        }
        RUN_MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        RUN_MANIFEST_FILE.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        _append_actions_summary(manifest)
        print(f"[manifest] 總耗時 {total:.0f}s / 預算 {RUN_BUDGET_SECONDS:.0f}s"
              f"({len(phases)} 階段);manifest → {RUN_MANIFEST_FILE}")
    except Exception as e:
        print(f"[manifest] 寫入失敗(不影響晨報): {e}", file=sys.stderr)


def _append_actions_summary(manifest: dict) -> None:
    """GitHub Actions Step Summary(環境變數 GITHUB_STEP_SUMMARY 指向的檔)。非 Actions 環境則 no-op。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        f"## 晨報執行摘要 {manifest['date']}",
        f"總耗時 **{manifest['total_seconds']:.0f}s** / 預算 {manifest['budget_seconds']:.0f}s"
        f"・新聞平行度 {manifest['news_workers']}",
        "",
        "| 階段 | 耗時(s) |", "|---|---:|",
    ]
    for p in sorted(manifest["phases"], key=lambda x: -x["seconds"]):
        lines.append(f"| {p['label']} | {p['seconds']:.0f} |")
    if manifest["degraded_steps"]:
        lines += ["", "⚠ 時間預算降級跳過:" + "、".join(manifest["degraded_steps"])]
    slow = [f"{h}(失敗 {s['fail']})" for h, s in manifest["feeds"].items() if s["fail"]]
    if slow:
        lines += ["", "抓取有失敗的來源:" + "、".join(sorted(slow))]
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[manifest] Step Summary 附加失敗: {e}", file=sys.stderr)
_RUN_DEADLINE: Optional[float] = None
_DEGRADED_STEPS: list[str] = []


def _run_seconds_left() -> float:
    """距整體保命 deadline 的剩餘秒數;未設定(如測試/本機)回一個大值=不限制。"""
    if _RUN_DEADLINE is None:
        return 1e9
    return _RUN_DEADLINE - time.monotonic()


def _run_budget_ok(need_seconds: float, step_label: str) -> bool:
    """昂貴非核心步驟的時間閘:剩餘時間 < 該步驟+後續寄信所需 → 跳過並記錄降級。
    回 True=可執行。need_seconds 應含「本步驟估時 + 後續核心(LLM 主分析~180s + 渲染寄信~40s)」。"""
    left = _run_seconds_left()
    if left >= need_seconds:
        return True
    _DEGRADED_STEPS.append(step_label)
    print(f"[budget] 剩餘 {left:.0f}s < {need_seconds:.0f}s → 跳過「{step_label}」保寄信",
          file=sys.stderr)
    return False


def _llm_remaining_seconds() -> float:
    if _LLM_DEADLINE is None:
        return max(1.0, LLM_REQUEST_TIMEOUT_SECONDS)
    return max(0.0, _LLM_DEADLINE - time.monotonic())


def _llm_request_timeout(cap: Optional[float] = None) -> float:
    remaining = _llm_remaining_seconds()
    if remaining < 1.0:
        raise TimeoutError("LLM 總時間預算已耗盡")
    return max(1.0, min(remaining, cap or LLM_REQUEST_TIMEOUT_SECONDS))


def _llm_sleep(seconds: float) -> None:
    remaining = _llm_remaining_seconds()
    if remaining <= 1.0:
        raise TimeoutError("LLM 總時間預算已耗盡")
    time.sleep(min(seconds, max(0.0, remaining - 1.0)))


def _redact_secret_text(text: str) -> str:
    """Remove configured secrets and common API-key query params from diagnostic text."""
    if not text:
        return ""
    out = str(text)
    for secret in (GEMINI_API_KEY, DEEPSEEK_API_KEY, ANTHROPIC_API_KEY, GMAIL_APP_PASSWORD):
        if secret and len(secret) >= 6:
            out = out.replace(secret, "[REDACTED]")
    import re as _re
    out = _re.sub(r"([?&](?:key|api_key|token)=)[^&\s]+", r"\1[REDACTED]", out,
                  flags=_re.I)
    out = _re.sub(r"(Authorization:\s*Bearer\s+)[^\s]+", r"\1[REDACTED]", out,
                  flags=_re.I)
    return out


def _http_error_summary(err: requests.exceptions.HTTPError) -> str:
    """Return an HTTP error summary that is useful in logs without leaking request secrets."""
    response = err.response
    code = response.status_code if response is not None else None
    body = ""
    try:
        body = (response.text or "")[:400] if response is not None else ""
    except Exception:
        body = ""
    if body:
        return _redact_secret_text(f"HTTP {code}: {body}")
    return _redact_secret_text(f"HTTP {code}" if code is not None else str(err))

# RSS 新聞來源（中、英、Fed）
def _gnews_rss(query: str, when: str = "2d") -> str:
    """組 Google News RSS 搜尋 URL(繁中/台灣)。Google News RSS 免費、穩定、即時,
    且回傳中文個股新聞,正好補「公司資訊太少」的缺口。when:2d = 近 2 天。"""
    from urllib.parse import quote
    q = quote(f"{query} when:{when}")
    return f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"


RSS_FEEDS = {
    # === 國際財經 ===
    # 註:Reuters 公開 RSS 已於近年停止對外服務(連線被擋)→ 移除,改用 Google News 主題補。
    "CNBC Top News":     "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "CNBC Tech":         "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910",
    "CNBC Economy":      "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    "Bloomberg Markets": "https://feeds.bloomberg.com/markets/news.rss",   # 偶 403,失敗自動略過
    "Yahoo Finance":     "https://finance.yahoo.com/news/rssindex",

    # === Google News 主題(取代已停的 Reuters,廣度覆蓋)===
    "Google-半導體":      _gnews_rss("半導體 AI晶片 台積電 輝達"),
    "Google-美股科技":    _gnews_rss("美股 那斯達克 科技股 財報"),
    "Google-Fed利率":     _gnews_rss("Fed 聯準會 利率 通膨 CPI"),
    "Google-台股大盤":    _gnews_rss("台股 加權指數 外資 三大法人"),
    "Google-地緣":        _gnews_rss("台海 晶片管制 美中 關稅"),
    # === 科技二線族群主題(讓「科技板塊脈動」不再只有 2330/2454;純取材、不掛個股標籤、不進計分)===
    "Google-散熱":        _gnews_rss("散熱 水冷 液冷 AI伺服器"),
    "Google-先進封裝":    _gnews_rss("CoWoS 先進封裝 台積電 日月光"),
    "Google-載板PCB":     _gnews_rss("ABF載板 PCB CCL 銅箔基板"),
    "Google-光通訊":      _gnews_rss("光通訊 CPO 矽光子 800G"),
    # === 世界大事(非市場導向;供「世界大事速覽」取材)===
    # 使用者需求(2026-07-16):晨報升級為「一封信掌握昨日世界」——股市之外的重大
    # 地緣/災難/科學/AI 事件也要看得到。查詢經實測校準(召回 46-100 則/2d);
    # 這些來源不掛 company_label、不進任何計分,純供 LLM「世界大事速覽」段取材。
    "世界-國際大事":      _gnews_rss("戰爭 OR 停火 OR 大選 OR 政變 OR 峰會 OR 制裁"),
    "世界-災難極端":      _gnews_rss("地震 OR 颱風 OR 洪災 OR 熱浪 OR 空難"),
    "世界-科學太空":      _gnews_rss("NASA OR SpaceX OR 諾貝爾 OR 核融合 OR 太空任務"),
    "世界-AI大事":        _gnews_rss("OpenAI OR Anthropic OR DeepMind OR AI模型 發布"),
    "中央社國際":         "https://feeds.feedburner.com/rsscna/intworld",

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

    # === 中國對台/對美深度新聞 ===
    "南華早報":           "https://www.scmp.com/rss/91/feed",          # 經濟
    "南華早報-科技":      "https://www.scmp.com/rss/36/feed",           # 中國科技
    "Nikkei Asia 中國":  "https://asia.nikkei.com/rss/feed/nar",       # 日經亞洲（中國頻道）
    "BBC 中文-兩岸":      "https://feeds.bbci.co.uk/zhongwen/trad/rss.xml",
}

# 其他(非科技)類股新聞來源:供「九、其他類股資訊」段落取材。
# 核心四類(金融/航運/生技/汽車)台股+全球雙軌;另補傳產原物料/營建資產/重電綠能/觀光內需
# (以台灣在地事件為主),補齊非科技結構性缺口。哪些類股當日在動由 SECTOR_HEAT 熱度表判斷。
# key = 類股標籤(同時用於 prompt 依類股分組);科技類股不在此,由上方半導體/美股科技覆蓋。
# 注意:Google News RSS 把多個關鍵字當 AND 處理,塞太多字會抓到 0 則。
# 以下查詢經實測校準(近 30h 各有 ~11–88 則):用 OR 群組或 1–2 個關鍵字才有足夠量。
OTHER_SECTOR_QUERIES: dict[str, str] = {
    # === 核心四類(每日必查;台灣+全球雙軌)===
    # 金融類股催化(壽險投資收益/淨息差);0050 重成分,供類股均衡;實測召回 ~78 則
    "金融-台股": "壽險 OR 金控 OR 淨息差 OR 投資收益",
    # 全球金融精準化:原「美股 金融」太泛,收斂到會傳導台股壽險/銀行的具體題材
    "金融-全球": "Fed 銀行股 OR 美債殖利率 OR 壽險 投資收益",
    "航運-台股": "長榮 OR 陽明 OR 萬海 OR 貨櫃航運",
    "航運-全球": "運價 OR BDI OR SCFI OR 塞港 OR 紅海航運",
    # 生技收斂到個股+催化(新藥/臨床/健保),去政策雜訊;實測召回 ~48 則、命中臨床/個股
    "生技-台股": "藥華藥 OR 新藥 OR 臨床 OR 解盲 OR 健保給付",
    # 全球生技精準化:原「美股 生技」太泛,收斂到有事件性的核准/里程碑
    "生技-全球": "FDA 核准 OR EMA OR 新藥 臨床 OR 併購 生技",
    "汽車-台股": "和泰車 OR 裕隆 OR 車用 OR 電動車 供應鏈",
    "汽車-全球": "特斯拉 OR 電動車 OR 車市 銷量",
    # === 新增四類(補齊傳產/營建/重電/觀光的結構性缺口;以台股在地事件為主)===
    # 傳產原物料:鋼鐵/塑化/水泥的景氣循環與報價
    "傳產-台股": "中鋼 OR 台塑 OR 南亞 OR 台泥 OR 鋼價 OR 塑化 報價",
    # 營建資產:房市/預售/都更/資產股題材
    "營建-台股": "營建股 OR 房市 OR 預售屋 OR 資產股 OR 都更",
    # 房市在地(使用者 2026-07-15 指定:台中/彰化/南投草屯為主的房市+重大建設;
    # 供「九、營建資產」寫全台+在地雙軌,含買氣/交易量/公共建設)
    "房市-中彰投": "台中 房市 OR 彰化 房市 OR 斗六 房市 OR 草屯 OR 台中 建案",
    "建設-中彰投": "中友百貨 OR 台中捷運 OR 彰化市 建設 OR 斗六 建設",
    # 重電綠能:電網強韌/台電/儲能/離岸風電(近年主升段族群,原本完全沒覆蓋)
    "重電-台股": "重電 OR 電網 OR 台電 強韌 OR 儲能 OR 離岸風電",
    # 觀光內需:旅遊/航空客運/零售內需
    "觀光-台股": "觀光 旅遊 OR 航空 客運 OR 內需 零售",
}
# 併入 RSS_FEEDS(來源名前綴「類股-」,便於 fetch_news 抓取與 prompt 依類股分組)。
RSS_FEEDS.update({f"類股-{label}": _gnews_rss(query)
                  for label, query in OTHER_SECTOR_QUERIES.items()})

# 重點公司:每天用 Google News 查各自最新新聞(直接補「個股資訊太少」)。
# 涵蓋 00662(NASDAQ-100)與 2330 供應鏈最相關的美股 + 台股名稱。
# 格式 (查詢字串, 顯示用代號/標籤)。查詢字串用中英並列,提高命中率。
OTHER_SECTOR_LABELS: tuple[str, ...] = tuple(OTHER_SECTOR_QUERIES.keys())


def _other_sector_label_from_source(source: str) -> str:
    source_text = str(source or "")
    for label in OTHER_SECTOR_LABELS:
        if source_text.endswith(label):
            return label
    return ""


GOOGLE_NEWS_COMPANIES: list[tuple] = [
    # --- 美股權值/AI/半導體龍頭 ---
    ("輝達 NVIDIA", "NVDA"), ("超微 AMD", "AMD"), ("博通 Broadcom", "AVGO"),
    ("美光 Micron 記憶體", "MU"), ("台積電", "2330"), ("艾司摩爾 ASML", "ASML"),
    ("蘋果 Apple", "AAPL"), ("微軟 Microsoft AI", "MSFT"),
    ("特斯拉 Tesla", "TSLA"), ("高通 Qualcomm", "QCOM"), ("邁威爾 Marvell", "MRVL"),
    ("應用材料 Applied Materials", "AMAT"), ("安謀 Arm", "ARM"),
    ("美超微 Supermicro", "SMCI"), ("Alphabet Google AI", "GOOGL"), ("Meta", "META"),
    # --- 2330 深度主題查詢(使用者要求 2026-07-14:台積電財報/法說/製程要更深)。
    #     同 label 疊加查詢 → 2330 素材池擴大;dedup 會吸收跨查詢重複,OR 語法已實測可用 ---
    ("台積電 財報 OR 法說 OR 資本支出", "2330"),
    ("台積電 先進製程 OR CoWoS OR 擴產", "2330"),
    # --- 台股 2330 供應鏈 / AI 伺服器 / 半導體 ---
    ("鴻海", "2317"), ("聯發科", "2454"), ("廣達 AI伺服器", "2382"),
    ("台達電", "2308"), ("聯電 UMC", "2303"), ("日月光 ASE", "3711"),
    ("緯創 AI伺服器", "3231"), ("緯穎 AI伺服器", "6669"), ("世芯-KY ASIC", "3661"),
    # --- 非科技類股代表(金融/航運/生技,類股均衡;皆為 0050 成分或市值前 100,當日有新聞才取) ---
    # 兩大金控加 OR 子公司名(使用者要求 2026-07-14:人事異動/重大投資/財報要更完整;
    # 壽險/銀行子公司新聞常不含母公司名,OR 已實測命中率大增)
    ("藥華藥", "6446"), ("富邦金", "2881"),
    ("國泰金 OR 國泰人壽 OR 國泰世華 OR 國泰產險 OR 國泰投信 OR 國泰證券", "2882"),
    ("中信金 OR 中國信託 OR 台灣人壽 OR 中信銀 OR 中信證券", "2891"),
    # 兩金控深度主題查詢(使用者 2026-07-15:財報/政策/重大決策要更多)——
    # 名稱查詢抓日常新聞,主題查詢補「決策面」(併購/投資/裁罰/增資/法說)
    ("國泰金 併購 OR 投資 OR 裁罰 OR 法說 OR 增資", "2882"),
    ("中信金 併購 OR 投資 OR 裁罰 OR 法說 OR 增資", "2891"),
    ("長榮 航運", "2603"),
]

# 美股公司消息只對具體、長期穩定的台股供應鏈做弱連動；分數低於直接命中。
# ⚠ 此 map 同時驅動 news_catalyst_score / Top5 排名(見 enrich 個股催化),
#   屬「計分模型」的一部分;新增/修改條目等於改模型,須先離線回測。勿為了顯示標籤而動它。
TW_SUPPLY_CHAIN_BY_US_LABEL: dict[str, set[str]] = {
    "NVDA": {"2330", "2382", "3231", "2308", "3711"},
    "AMD": {"2330"},
    "AVGO": {"2330"},
    "MU": {"3711"},
    "ASML": {"2330"},
    "AAPL": {"2317", "3008"},
}

# 純「顯示標籤」用的美股→2330 供應鏈關聯(只影響 prompt 上的 [對2330供應鏈] 標記,
# 不進任何計分;故可自由擴充而不需回測,與上方計分用的 map 刻意分離)。
_TAG_2330_SUPPLYCHAIN_US: set[str] = {
    "NVDA", "AMD", "AVGO", "ASML", "QCOM", "MRVL", "AMAT", "ARM",
}


def _supply_chain_2330_tag(label) -> str:
    """美股新聞若屬 2330 供應鏈,回顯示標籤(僅 prompt 顯示用,不改 importance/計分)。
    刻意使用獨立的 _TAG_2330_SUPPLYCHAIN_US,不碰計分用的 TW_SUPPLY_CHAIN_BY_US_LABEL。"""
    return "[對2330供應鏈] " if str(label or "") in _TAG_2330_SUPPLYCHAIN_US else ""

# 台股產業級事件只給更弱的保守連動，避免未點名公司新聞過度灌分。
TW_INDUSTRY_EVENT_MAP: dict[str, dict[str, set[str]]] = {
    "memory": {
        "terms": {"記憶體", "DRAM", "NAND", "HBM", "美光", "Micron"},
        "codes": {"2344", "2408", "2451", "3711"},
    },
    "passive_components": {
        "terms": {"被動元件", "MLCC", "電阻", "電容", "國巨", "華新科"},
        "codes": {"2327", "2492"},
    },
    "ai_server": {
        "terms": {"AI伺服器", "AI 伺服器", "伺服器", "資料中心", "GB200", "B200"},
        "codes": {"2317", "2382", "3231", "2308", "3711", "2345"},
    },
    "semiconductor_equipment": {
        "terms": {"半導體設備", "EUV", "ASML", "先進製程", "CoWoS", "封裝"},
        "codes": {"2330", "3037", "3711"},
    },
}


# 科技脈動品質閘門用詞:純分析師喊價、純籌碼流向、具體催化。
# 用於過濾「重點公司新聞」餵 LLM 的取材厚度,不影響任何計分(計分仍吃全部新聞)。
# 科技脈動閘門的「具體催化」白名單:刻意只放難以在純喊價/籌碼文中出現的具體事件詞,
# 不沿用 NEWS_POSITIVE/NEGATIVE_TERMS(那組為計分召回而設,含成長/增加/獲利等泛詞,
# 會讓「調升目標價,預估獲利成長」這類純喊價漏網)。

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
}


# ---------- 工具函式 ----------
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


def _last_known_usdtwd(max_age_days: int = 7,
                       now_tpe: Optional["dt.datetime"] = None) -> Optional[dict]:
    """即時匯率抓取失敗時,從 history 讀最近一筆非空 usdtwd 當昨值降級。
    USD/TWD 日波動通常 <0.5%,昨值遠勝「資料缺失」。僅供顯示/LLM prompt 情境——
    calc_00662_fair_value 用自己的 fx_hist,不吃此值,故非排名/計分輸入。
    回 {"value","date","age_days"};無快取或距今 >max_age_days 回 None。"""
    try:
        if not STATE_FILE.exists():
            return None
        rows = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            return None
    except Exception:
        return None
    best_v = None
    best_date = None
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("usdtwd_stale"):   # 跳過本身就是昨值降級的筆,只採真觀測(否則昨值會自我延續、護欄失效)
            continue
        v = _safe_number(r.get("usdtwd"))
        if v is None or not r.get("date"):
            continue
        try:   # 只採用可解析的 YYYY-MM-DD,壞日期直接跳過(勿讓它在字串比較中勝出)
            dd = dt.datetime.strptime(str(r.get("date")), "%Y-%m-%d").date()
        except ValueError:
            continue
        if best_date is None or dd > best_date:
            best_date, best_v = dd, v
    if best_v is None:
        return None
    today = (now_tpe or dt.datetime.now(TPE)).date()
    age = (today - best_date).days
    if age < 0 or age > max_age_days:
        return None
    return {"value": best_v, "date": best_date.strftime("%Y-%m-%d"), "age_days": age}


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
        r = _http_get("https://www.sec.gov/files/company_tickers.json",
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
    # cik → ticker(filing 帶 ticker,供 8-K 公司動態新聞查詢歸因)
    cik_ticker: dict[str, str] = {
        "0001046179": "TSM", "0001045810": "NVDA", "0000789019": "MSFT",
        "0000320193": "AAPL", "0001318605": "TSLA", "0001730168": "AVGO",
        "0000002488": "AMD", "0001326801": "META", "0001652044": "GOOGL",
        "0001018724": "AMZN",
    }
    # priority_ciks:屬於「重點科技股」白名單者(email 8-K 區塊只顯示這些;LLM 仍吃全部)
    priority_ciks: set = set(SEC_BASE_COMPANIES.keys())   # mega-cap + 台積電一律重點
    for ticker in NDX_TICKERS:
        entry = cik_map.get(ticker.upper())
        if not entry:
            continue
        cik, name = entry
        cik_ticker.setdefault(cik, ticker.upper())
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
            r = _http_get(url, timeout=8, headers=headers)
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
                    "ticker": cik_ticker.get(cik, ""),
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


def _http_get(url, *, retries=2, backoff=1.2,
              retry_status=(429, 500, 502, 503, 504), **kwargs):
    """帶重試/退避的 GET(沿用 requests.get 介面、回傳 Response)。
    連線例外或 retry_status(429/5xx)才重試(指數退避);404 等其餘直接回;
    全數失敗則拋最後一次例外(呼叫端沿用既有 try/except)。
    內部走 requests.get(而非獨立 Session),讓既有 monkeypatch(mr.requests.get)測試仍可攔截;
    以 getattr 取 status_code,測試假物件無此屬性時視為 200(直接回、不重試)。"""
    kwargs.setdefault("timeout", 20)
    last_exc = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, **kwargs)
        except requests.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            raise
        if getattr(r, "status_code", 200) in retry_status and attempt < retries:
            time.sleep(backoff * (attempt + 1))
            continue
        return r
    if last_exc:
        raise last_exc


def _mops_roc_datetime(roc_date, hhmmss):
    """MOPS 民國發言日期(yyyymmdd)+ 發言時間(HHMMSS,可能省略前導 0)→ 台北時區 datetime;失敗回 None。"""
    s = str(roc_date or "").strip()
    if len(s) != 7 or not s.isdigit():
        return None
    try:
        t = str(hhmmss or "0").strip().zfill(6)[:6]
        return dt.datetime(int(s[:3]) + 1911, int(s[3:5]), int(s[5:7]),
                           min(int(t[:2]), 23), min(int(t[2:4]), 59), min(int(t[4:6]), 59),
                           tzinfo=TPE)
    except (ValueError, TypeError):
        return None


def fetch_tw_major_announcements(codes: list[str], hours: int = 48) -> list[dict]:
    """
    抓台股指定公司近 N 小時的「重大訊息」。
    **改用 TWSE OpenAPI 全市場當日重大訊息**(opendata/t187ap04_L,免金鑰、一次取回、再依代號過濾);
    原每公司 MOPS RSS(t05st01_rss)已於 2026-07 停用(連線重置 / 導回 SPA,實測失效)。

    回傳:[{"code","title","summary","link","published"}, ...] 依時間 desc。整體失敗回 []。
    """
    if not codes:
        return []
    want = {str(c).strip() for c in codes}
    cutoff = dt.datetime.now(TPE) - dt.timedelta(hours=hours)
    try:
        data = _http_get(
            "https://openapi.twse.com.tw/v1/opendata/t187ap04_L",
            timeout=20, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        ).json() or []
    except Exception as e:
        print(f"[mops] OpenAPI t187ap04_L 失敗: {e}", file=sys.stderr)
        return []
    if not isinstance(data, list):   # 非預期形狀(錯誤 payload/物件)→ 降級為空,不拋例外
        print(f"[mops] t187ap04_L 回傳非清單({type(data).__name__}),略過", file=sys.stderr)
        return []
    out: list[dict] = []
    seen_keys: set = set()
    for row in data:
        if not isinstance(row, dict):
            continue
        code = str(row.get("公司代號", "")).strip()
        if code not in want:
            continue
        pub_dt = _mops_roc_datetime(row.get("發言日期"), row.get("發言時間"))
        if pub_dt is not None and pub_dt < cutoff:
            continue
        # 欄名「主旨 」帶尾空白;清掉換行
        title = str(row.get("主旨 ") or row.get("主旨") or "").replace("\r", "").replace("\n", " ").strip()
        if not title:
            continue
        summary_raw = str(row.get("說明") or "").replace("\r", "").strip()
        # 去重:鍵 = 整列原始資料的正規化序列化 → 只移除「完全相同的重複列」。
        # 任何欄位不同(說明後段、事實發生日、條款、無法解析的發言時間…)都視為不同公告,絕不合併。
        # 刻意不用 (代號,主旨) 或截斷後的 summary:同公司可同時發多筆主旨相同但內容不同的公告
        # (如 3711 多筆「取得營業用機器設備達十億元」實為不同設備採購),那樣會藏掉真實揭露(Codex review)。
        dedup_key = json.dumps(row, sort_keys=True, ensure_ascii=False)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        out.append({
            "code": code,
            "title": title,
            "summary": summary_raw[:600],
            "link": "https://mops.twse.com.tw/mops/#/web/t05st01",
            "published": pub_dt.isoformat() if pub_dt else "",
        })
    out.sort(key=lambda x: x.get("published", ""), reverse=True)
    print(f"[mops] 取得 {len(out)} 筆台股重大訊息（OpenAPI t187ap04_L,目標 {len(want)} 家）")
    return out


# 2330 法說會日期預估（依過去慣例；正式日期仍須以 TSMC IR 公告為準）
TSMC_FINANCIAL_CALENDAR_URL = "https://investor.tsmc.com/english/financial-calendar"
TSMC_EARNINGS_ESTIMATES = [
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
    for date_str in TSMC_EARNINGS_ESTIMATES:
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
        note = f"預估法說會 ±2 天（{closest_date}）— 預測信心顯著下降，2330 走勢可能脫離 ADR 連動"
    elif closest_days <= 5:
        impact = "high"
        note = f"預估法說會週（{closest_date}）— 預測信心略降，留意 TSMC IR 正式公告"
    elif closest_days <= 10:
        impact = "elevated"
        note = f"距預估法說會 1-2 週（{closest_date}）— 法人持倉可能調整"
    else:
        impact = "normal"
        note = f"距預估法說會 {closest_days} 天（{closest_date}；正式日期以 TSMC IR 為準）"

    return {
        "closest_date": closest_date,
        "days_to": closest_days,
        "impact": impact,
        "note": note,
        "is_estimate": True,
        "source_url": TSMC_FINANCIAL_CALENDAR_URL,
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
            r = _http_get(url, timeout=15, headers=headers)
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
            header_i = close_i = session_i = month_i = chgpct_i = chgprice_i = None
            for ri, row in enumerate(rows[:6]):
                for ci, cell in enumerate(row):
                    c = cell.strip()
                    if close_i is None and "收盤" in c and "結算" not in c:
                        close_i = ci
                    if session_i is None and ("交易時段" in c or c == "盤別"):
                        session_i = ci
                    if month_i is None and ("到期月份" in c or "契約月份" in c):
                        month_i = ci
                    if chgpct_i is None and "漲跌" in c and "%" in c:
                        chgpct_i = ci            # 官方漲跌%(夜盤訊號的正確基準)
                    if chgprice_i is None and "漲跌" in c and ("價" in c or "點" in c):
                        chgprice_i = ci          # 官方漲跌價(無 % 欄時推回基準用)
                if close_i is not None and session_i is not None:
                    header_i = ri
                    break
            if close_i is None or session_i is None:
                print(f"[taifex_night] {date_str} 表頭偵測失敗，跳過", file=sys.stderr)
                continue

            # 找近月合約（無到期月 W 字樣的），分開「一般」與「盤後」。
            # 夜盤訊號改用 TAIFEX「盤後」官方漲跌%（GPT-5.5 複審):該值以該合約正確參考價計算,
            # 自動避開「日盤期貨收盤被正價差/除息灌高」造成的假性反向(舊式 (夜盤收-日盤收)/日盤收
            # 在除息/正價差暴衝日會把『夜盤上漲』誤算成下跌)。day_close 僅留作診斷,不再當基準。
            def _pct_clean(s):
                return safe_float(str(s).replace("%", "").replace("+", "").replace(",", ""))
            day_close = None
            night_close = None
            night_chg_pct = None        # 盤後官方漲跌%
            night_chg_price = None      # 盤後官方漲跌價(備援)
            _need = max(close_i, session_i, month_i or 0, chgpct_i or 0, chgprice_i or 0)
            for row in rows[header_i + 1:]:
                if len(row) <= _need:
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
                        if chgpct_i is not None:
                            night_chg_pct = _pct_clean(row[chgpct_i])
                        if chgprice_i is not None:
                            night_chg_price = _pct_clean(row[chgprice_i])
                else:
                    if day_close is None:
                        day_close = close_val

            if night_close:
                # 主:官方漲跌%；備:用漲跌價推回參考價算%
                if night_chg_pct is not None:
                    night_pct = night_chg_pct
                elif night_chg_price is not None and (night_close - night_chg_price):
                    night_pct = night_chg_price / (night_close - night_chg_price) * 100
                else:
                    night_pct = None
                if night_pct is not None:
                    if abs(night_pct) > 8:
                        print(f"[taifex_night] {date_str} ⚠ 夜盤官方漲跌 {night_pct:+.2f}% 異常大,請查證",
                              file=sys.stderr)
                    print(f"[taifex_night] {date_str} 夜盤官方漲跌 {night_pct:+.2f}% "
                          f"(夜盤收 {night_close};日盤收 {day_close} 僅診斷)")
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


def _fmtqik_taiex_by_roc_date() -> dict:
    """TWSE FMTQIK → {民國日期字串(yyyMMdd): 加權指數收盤}。供台指期價差同日對齊。失敗回 {}。"""
    out: dict[str, float] = {}
    try:
        r = _http_get("https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK",
                      timeout=20, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        r.raise_for_status()
        for row in (r.json() or []):
            date_k = next((k for k in row if "日期" in k or k == "Date"), None)
            if not date_k:
                continue
            roc = str(row.get(date_k) or "").strip().replace("/", "")
            for k in ("發行量加權股價指數", "TAIEX", "加權股價指數", "Closing_TAIEX"):
                v = _to_float(row.get(k))
                if v and v > 1000:
                    out[roc] = round(v, 2)
                    break
    except Exception as e:
        print(f"[taifex_basis] FMTQIK 對齊表失敗: {e}", file=sys.stderr)
    return out


def fetch_taifex_basis() -> dict:
    """台指期近月「與現貨價差」(純事實,不下情緒結論)。

    使用者要求:純事實版——只呈現「期貨 vs 現貨相差幾點」,**不**寫「法人樂觀/避險」。
    原因:台股 7-8 月除息旺季本就逆價差(期貨低於現貨),那是除息造成、非看空;
    直接解讀成情緒訊號會每天誤導。故只給數字+季節性說明,判讀留給讀者。不進任何計分。

    同日對齊:期貨結算與現貨收盤取「同一交易日」(FMTQIK 逐日表 match),避免跨日錯配。
    回 {"date","fut_month","fut_settle","spot","diff","div_season"} 或 {}(失敗/無法對齊)。
    """
    today = dt.datetime.now(TPE).date()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "text/html"}
    taiex_map = _fmtqik_taiex_by_roc_date()
    for back in range(0, 6):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y/%m/%d")
        roc = f"{d.year - 1911:03d}{d.month:02d}{d.day:02d}"
        spot = taiex_map.get(roc)
        if spot is None:
            continue   # 沒有同日現貨收盤 → 不硬湊(避免跨日錯配)
        try:
            r = requests.post("https://www.taifex.com.tw/cht/3/futDataDown",
                              data={"down_type": "1", "commodity_id": "TX",
                                    "queryStartDate": date_str, "queryEndDate": date_str},
                              timeout=15, headers=headers)
            if r.status_code != 200 or len(r.text) < 200:
                continue
            import csv
            from io import StringIO
            rows = list(csv.reader(StringIO(r.content.decode("big5", errors="replace"))))
            if len(rows) < 2:
                continue
            hdr = [c.strip() for c in rows[0]]
            month_i = next((i for i, c in enumerate(hdr) if "到期月份" in c or "契約月份" in c), None)
            close_i = next((i for i, c in enumerate(hdr) if "收盤" in c and "結算" not in c), None)
            settle_i = next((i for i, c in enumerate(hdr) if "結算" in c), None)
            session_i = next((i for i, c in enumerate(hdr) if "交易時段" in c or c == "盤別"), None)
            if month_i is None or close_i is None:
                continue
            fut_settle = fut_month = None
            _need = max(x for x in (month_i, close_i, settle_i or 0, session_i or 0))
            for row in rows[1:]:
                if len(row) <= _need:
                    continue
                mon = row[month_i].strip()
                if "W" in mon or "/" in mon:
                    continue   # 跳過週選/價差組合列
                if session_i is not None and ("盤後" in row[session_i] or "夜盤" in row[session_i]):
                    continue   # 只取日盤(與現貨收盤同時點)
                val = safe_float(row[settle_i]) if settle_i is not None else None
                val = val or safe_float(row[close_i])
                if val and val > 1000:
                    fut_settle, fut_month = round(val, 0), mon
                    break   # 第一筆日盤非週約 = 近月
            if fut_settle is None:
                continue
            diff = round(fut_settle - spot, 0)
            div_season = d.month in (6, 7, 8, 9)   # 台股除息旺季:期貨天生偏低,屬季節性
            print(f"[taifex_basis] {date_str} 近月{fut_month} 期 {fut_settle:.0f} vs 現貨 {spot:.0f} "
                  f"= {diff:+.0f} 點(除息季={div_season})")
            return {"date": date_str, "fut_month": fut_month, "fut_settle": fut_settle,
                    "spot": spot, "diff": diff, "div_season": div_season}
        except Exception as e:
            print(f"[taifex_basis] {date_str} 失敗: {e}", file=sys.stderr)
            continue
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
                    # TAIFEX 曾將欄名由「多空淨額未平倉口數」改成
                    # 「多空未平倉口數淨額」。不要依賴詞序，只鎖定口數而非契約金額。
                    if ("未平倉" in c and "口數" in c and "淨額" in c
                            and "契約金額" not in c):
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


def fetch_taifex_options_pc_ratio() -> dict:
    """TAIFEX 台指選擇權 Put/Call ratio(本土選擇權情緒;TAIFEX OpenAPI JSON)。

    借鏡 node-twstock txoPutCallRatio,改用官方 OpenAPI。P/C(OI)>100% = 未平倉偏 Put、
    避險/偏空部位濃;極端高常是散戶過度避險 → 反向(contrarian)偏多訊號。補晨報缺的
    『台股本土情緒』(現只有美股 VIX)。失敗回 {}(fail-safe,不影響晨報)。
    """
    try:
        r = _http_get("https://openapi.taifex.com.tw/v1/PutCallRatio",
                         timeout=(5, 10), headers={"User-Agent": "Mozilla/5.0"})
        data = r.json() or []
        if not data:
            return {}
        latest = max(data, key=lambda x: str(x.get("Date", "")))   # 取最新交易日
        vol = _to_float(latest.get("PutCallVolumeRatio%"))
        oi = _to_float(latest.get("PutCallOIRatio%"))
        if oi is None and vol is None:
            return {}
        out = {"date": str(latest.get("Date", "")), "pc_vol_ratio": vol, "pc_oi_ratio": oi}
        print(f"[taifex] TXO Put/Call OI ratio = {oi}% (vol {vol}%)")
        return out
    except Exception as e:
        print(f"[taifex] PCR 抓取失敗(不影響晨報): {e}", file=sys.stderr)
        return {}


def fetch_taifex_large_traders(contract: str = "TX") -> dict:
    """TAIFEX 大額交易人未沖銷部位(台指期 TX,所有契約合計 SettlementMonth=999912)。

    借鏡 node-twstock largeTraders 配方,改用 TAIFEX OpenAPI。前 10 大交易人淨部位(買−賣)
    與集中度,反映『主力方向 + 籌碼集中』;特定法人(TypeOfTraders=1)更貼近機構動向。
    補晨報缺的『大額交易人定位』(現只有三大法人淨額)。失敗回 {}(fail-safe)。

    回 {date, top10_net(正=偏多), top10_buy, top10_sell, oi_market,
        top10_long_pct, top10_short_pct, concentration_pct, spec_top10_net}。
    """
    try:
        r = _http_get("https://openapi.taifex.com.tw/v1/OpenInterestOfLargeTradersFutures",
                         timeout=(5, 12), headers={"User-Agent": "Mozilla/5.0"})
        data = r.json() or []
        rows = [x for x in data if x.get("Contract") == contract
                and str(x.get("SettlementMonth")) == "999912"]      # 所有契約合計
        if not rows:
            return {}
        latest = max(str(x.get("Date", "")) for x in rows)
        allt = next((x for x in rows if str(x.get("Date")) == latest
                     and str(x.get("TypeOfTraders")) == "0"), None)
        spec = next((x for x in rows if str(x.get("Date")) == latest
                     and str(x.get("TypeOfTraders")) == "1"), None)
        if not allt:
            return {}

        def _strict_int(v):
            # 嚴格解析:缺欄位/空/壞值回 None(不可用 _to_int,它壞值回 0 會算出假部位)
            if v is None or str(v).strip() in ("", "-", "NA"):
                return None
            try:
                return int(float(str(v).replace(",", "").strip()))
            except (TypeError, ValueError):
                return None
        b, s, oi = (_strict_int(allt.get("Top10Buy")),
                    _strict_int(allt.get("Top10Sell")),
                    _strict_int(allt.get("OIOfMarket")))
        if b is None or s is None or not oi:      # 缺欄位/壞值/OI=0 → fail-safe 回 {}
            return {}
        out = {
            "date": latest, "top10_buy": b, "top10_sell": s, "top10_net": b - s,
            "oi_market": oi,
            "top10_long_pct": round(b / oi * 100, 1),
            "top10_short_pct": round(s / oi * 100, 1),
            "concentration_pct": round(max(b, s) / oi * 100, 1),
        }
        if spec:
            sb, ss = _strict_int(spec.get("Top10Buy")), _strict_int(spec.get("Top10Sell"))
            if sb is not None and ss is not None:
                out["spec_top10_net"] = sb - ss
        print(f"[taifex] TX 大額交易人 Top10 淨 = {out['top10_net']:+d} 口"
              f"(集中度 {out['concentration_pct']}%)")
        return out
    except Exception as e:
        print(f"[taifex] 大額交易人抓取失敗(不影響晨報): {e}", file=sys.stderr)
        return {}


_ANALYST_MOMENTUM_TICKERS = ("TSM", "NVDA", "AVGO", "AMD", "ASML")


def fetch_analyst_rating_momentum(tickers=_ANALYST_MOMENTUM_TICKERS, days: int = 30) -> dict:
    """分析師評等/目標價動能(借鏡 yfinance upgrades_downgrades;前瞻共識轉向訊號)。

    台股本地代號(2330.TW)Yahoo 多無分析師資料 → 改用 ADR/美股(TSM≈2330 + AI/半導體龍頭)。
    近 days 日:淨動能 = (升評 up + 調高目標價 Raises) − (降評 down + 調低目標價 Lowers)。
    Yahoo 為非官方 API 易壞 → 每檔包 try/except,任何錯誤跳過(fail-safe,不影響晨報)。
    回 {ticker: {net, up, down, tgt_raise, tgt_cut, n, latest}}。
    """
    out: dict = {}
    try:
        cutoff = pd.Timestamp(dt.datetime.now(TPE).date()) - pd.Timedelta(days=days)
    except Exception:
        return {}
    for tk in tickers:
        try:
            ud = yf.Ticker(tk).upgrades_downgrades
            if ud is None or len(ud) == 0:
                continue
            ud = ud.reset_index()
            gd = pd.to_datetime(ud["GradeDate"], errors="coerce")
            if getattr(gd.dt, "tz", None) is not None:
                gd = gd.dt.tz_localize(None)
            ud = ud[gd >= cutoff]
            if not len(ud):
                continue
            ud = ud.sort_values("GradeDate", ascending=False)   # 自行排序,latest 不依賴來源順序
            act = ud["Action"].astype(str).str.lower() if "Action" in ud else None
            up = int((act == "up").sum()) if act is not None else 0
            down = int((act == "down").sum()) if act is not None else 0
            if "priceTargetAction" in ud:
                pta = ud["priceTargetAction"].astype(str).str.lower()
                raises = int((pta == "raises").sum())
                cuts = int((pta == "lowers").sum())
            else:
                raises = cuts = 0
            row0 = ud.iloc[0]      # yfinance 以 GradeDate 由新到舊 → 第一列為最新
            latest = f"{row0.get('Firm', '')} {row0.get('priceTargetAction', '') or row0.get('Action', '')}".strip()
            out[tk] = {"net": (up + raises) - (down + cuts), "up": up, "down": down,
                       "tgt_raise": raises, "tgt_cut": cuts, "n": int(len(ud)), "latest": latest}
        except Exception:
            continue
    if out:
        print(f"[analyst] 評等動能 {len(out)} 檔(net: "
              + ", ".join(f"{k}{v['net']:+d}" for k, v in out.items()) + ")")
    return out


def _basis_line_html(basis: dict) -> str:
    """台指期與現貨價差 → 純事實一行(不下情緒結論;隱藏基差/逆價差術語)。無資料回空。"""
    if not basis or basis.get("diff") is None or basis.get("spot") is None:
        return ""
    diff = basis["diff"]
    hl = "高" if diff > 0 else ("低" if diff < 0 else "持平")
    season = ("　7-8 月除息旺季期貨常低於現貨,屬季節性、非看空訊號"
              if basis.get("div_season") else "")
    return (
        "<div style='background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;"
        "padding:12px 18px;margin:12px 0;font-size:13px;color:#334155;line-height:1.7;'>"
        "<b style='color:#0f172a;'>台指期 vs 大盤現貨</b>　"
        f"近月期貨 {basis['fut_settle']:,.0f}、大盤現貨 {basis['spot']:,.0f}"
        f"(期貨{hl} <b>{abs(diff):,.0f}</b> 點)"
        f"<span style='color:#94a3b8;'>{season}</span></div>")


def _yield_curve_read(macro: dict) -> dict:
    """美債殖利率曲線 → 白話結論(隱藏「倒掛/2s10s」等術語,使用者只要結果)。
    回 {"detail": 顯示字串, "flag": warn|caution|normal} 或 {}(無資料)。不進計分。"""
    m3 = (macro.get("13W") or {}).get("close")
    m10 = (macro.get("10Y") or {}).get("close")
    m30 = (macro.get("30Y") or {}).get("close")
    if m3 is None or m10 is None:
        return {}
    spread = m10 - m3
    if spread < -0.05:
        flag, tail = "warn", "長期利率低於短期,歷史上常領先景氣轉弱,值得留意"
    elif spread < 0.25:
        flag, tail = "caution", "長短期利率差距偏小,市場對後續成長偏保守"
    else:
        flag, tail = "normal", "利率結構正常,市場預期成長穩定"
    parts = f"短期 {m3:.2f}%、10 年 {m10:.2f}%"
    if m30 is not None:
        parts += f"、30 年 {m30:.2f}%"
    return {"detail": f"美債利率{parts};{tail}", "flag": flag}


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
        "5Y":    "^FVX",     # 美債 5 年期(完整化殖利率曲線判讀,白話呈現)
        "30Y":   "^TYX",     # 美債 30 年期
        "N225":  "^N225",
        "SSE":   "000001.SS",
        "NQ":    "NQ=F",
        "ES":    "ES=F",
        "WTI":   "CL=F",
        "GOLD":  "GC=F",
        "BTC":   "BTC-USD",   # 風險偏好即時溫度計(24h 交易,凌晨也有訊號)
        "COPPER": "HG=F",     # 銅:景氣領先指標,與台股出口連動
        # G3 世界證據增項(門檻式白話顯示,平日不出現;全列 _MACRO_OPTIONAL 抓不到不降級):
        "MOVE":  "^MOVE",     # 美債市場波動率(債市的 VIX);急升常伴隨股市震盪
        "RSP":   "RSP",       # S&P 500 等權重 ETF;與市值加權 SPY 比較看「漲勢廣度」
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


def _world_evidence_signals(macro: dict, spy: Optional[dict] = None) -> list:
    """G3|世界證據「門檻式」白話警示:平日回空 list,只有指標明顯異常時才回一行白話。

    純函式(吃 macro dict + SPY 報價),供渲染層在超門檻時掛一則提醒——顯示層啟發式、
    僅供參考,不進計分。閾值刻意保守(避免天天觸發破壞「異常才出現」的用意)。"""
    macro = macro or {}
    spy = spy or {}
    out: list[str] = []

    def _num(d, k):
        v = (d or {}).get(k) if isinstance(d, dict) else None
        return v if isinstance(v, (int, float)) else None

    # 1) 美債市場波動率(MOVE):單日急升 / 逼近一年高 / 絕對水位偏高
    mv = macro.get("MOVE") if isinstance(macro.get("MOVE"), dict) else {}
    mv_chg = _num(mv, "change_pct")
    mv_rank = _num(mv, "pct_rank_252d")
    mv_close = _num(mv, "close")
    if (mv_chg is not None and mv_chg > 10) or (mv_rank is not None and mv_rank > 90) \
            or (mv_close is not None and mv_close > 130):
        out.append("債市波動明顯升溫(美債波動率偏高),歷史上常伴隨股市震盪加大——僅供留意,非賣出訊號。")

    # 2) 漲勢廣度:市值加權(SPY)上漲但等權(RSP)明顯落後 → 只靠少數大型股撐盤
    rsp = macro.get("RSP") if isinstance(macro.get("RSP"), dict) else {}
    rsp_chg = _num(rsp, "change_pct")
    spy_chg = _num(spy, "change_pct")
    if spy_chg is not None and rsp_chg is not None and spy_chg > 0 \
            and (rsp_chg - spy_chg) < -1.0:
        out.append("美股上漲主要靠少數權值股撐盤、廣度偏弱(等權重指數明顯落後),漲勢基礎較不穩——僅供留意。")

    # 3) 銅金比:銅(景氣)相對金(避險)當日明顯轉弱 → 景氣訊號偏保守
    cop_chg = _num(macro.get("COPPER"), "change_pct")
    gold_chg = _num(macro.get("GOLD"), "change_pct")
    if cop_chg is not None and gold_chg is not None and (cop_chg - gold_chg) < -3.0:
        out.append("工業金屬走弱、避險偏好升溫(銅金比明顯下滑),景氣訊號轉為保守——僅供留意。")

    return out


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
    r = _http_get(url, timeout=15, headers=headers)
    r.raise_for_status()
    payload = r.json()
    if payload.get("stat") != "OK":
        return []
    fields = payload.get("fields", [])
    data = payload.get("data", [])
    return [dict(zip(fields, row)) for row in data]


def _twse_openapi(_unused: str) -> list[dict]:
    """備援端點：OpenAPI（無日期參數，回傳最新一日）。"""
    r = _http_get("https://openapi.twse.com.tw/v1/fund/T86",
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
            r = _http_get(url, timeout=20, headers=headers)
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
                groups = t.get("groups") or []
                group_titles = " ".join(str(g.get("title", "")) for g in groups
                                        if isinstance(g, dict))
                has_code = any(f in fields for f in ("代號", "股票代號", "證券代號"))
                if has_code and ("融資" in fields_str or "融資" in group_titles):
                    stock_table = t
                    break
            if not stock_table and data.get("fields") and data.get("data"):
                # 早期格式:平鋪 fields/data
                stock_table = {"fields": data["fields"], "data": data["data"]}
            if not stock_table:
                continue

            fields: list[str] = stock_table.get("fields", [])
            groups: list[dict] = stock_table.get("groups", []) or []
            # 欄位偵測
            def col_idx(*needles: str,
                        start: int = 0,
                        end: Optional[int] = None) -> Optional[int]:
                stop = len(fields) if end is None else min(end, len(fields))
                for i in range(start, stop):
                    f = fields[i]
                    if all(n in f for n in needles):
                        return i
                return None

            def first_idx(*values: Optional[int]) -> Optional[int]:
                return next((v for v in values if v is not None), None)

            i_code = first_idx(col_idx("股票代號"), col_idx("證券代號"), col_idx("代號"))

            # 現行 TWSE payload 用 groups 表達「融資」區段，區段內欄名只有
            # 「前日餘額 / 今日餘額」。舊 payload 則可能將「融資」直接寫進欄名。
            margin_start = margin_end = None
            offset = 0
            for group in groups:
                span = _to_int(group.get("span")) if isinstance(group, dict) else 0
                if isinstance(group, dict) and "融資" in str(group.get("title", "")):
                    margin_start, margin_end = offset, offset + span
                    break
                offset += span
            if margin_start is not None:
                i_bal = first_idx(
                    col_idx("今日餘額", start=margin_start, end=margin_end),
                    col_idx("本日餘額", start=margin_start, end=margin_end),
                )
                i_prev = first_idx(
                    col_idx("前日餘額", start=margin_start, end=margin_end),
                    col_idx("昨日餘額", start=margin_start, end=margin_end),
                )
            else:
                i_bal = first_idx(
                    col_idx("融資", "今日餘額"), col_idx("融資", "本日餘額"),
                    col_idx("融資", "今日"), col_idx("融資餘額"),
                )
                i_prev = first_idx(
                    col_idx("融資", "前日餘額"), col_idx("融資", "昨日餘額"),
                    col_idx("融資", "前日"),
                )

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
    elif raw_score <= -20 or f_score <= -25:
        tag = "籌碼鬆動警示"
    else:
        tag = "中性"

    return {
        "score": score,
        "raw_score": round(raw_score, 1),
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


def calc_breakout_score(entry: dict) -> dict:
    """
    「短線爆發力結構分」(篩選未來 3-5 工作天關注候選),多因子複合 0-90:
      籌碼 35% (smart_money 分數,法人連買+大戶吸籌+量能)
      動能 25% (5日漲幅 + 距MA20 + 突破20日高;**動能優先,不懲罰過熱**)
      營收 20% (月營收 YoY + MoM)
      EPS  10% (最新季度 EPS;>0 有獲利加分)
      新聞事件另由 _attention_ranking_breakdown 在 Python 中客觀整合

    回傳 {"score": 0-90, "components": {...}}。資料缺漏的因子以 0 計。
    """
    if not entry:
        return {"score": 0, "components": {}}

    def _clip01(x, lo, hi):
        if x is None:
            return 0.0
        return max(0.0, min(1.0, (x - lo) / (hi - lo))) if hi > lo else 0.0

    # 籌碼:直接用 smart_money 分數(0-100)
    chips = (entry.get("smart_money") or {}).get("score", 0) or 0
    chips_score = chips * 0.35

    # 動能:5日漲幅(0~+15% → 0~70 分)+ 距MA20(0~+10% → 0~20)+ 突破新高(10)
    #       動能優先 → 不對高漲幅懲罰(漲越多分越高,封頂)
    p5 = entry.get("pct_5d")
    d20 = entry.get("ma20_dist_pct")
    mom_raw = (_clip01(p5, 0, 15) * 70
               + _clip01(d20, 0, 10) * 20
               + (10 if entry.get("high20_break") else 0))
    mom_score = min(100.0, mom_raw) * 0.25

    # 營收:YoY(0~+50% → 0~70)+ MoM(0~+20% → 0~30)
    yoy = entry.get("rev_yoy_pct")
    mom_rev = entry.get("rev_mom_pct")
    rev_raw = _clip01(yoy, 0, 50) * 70 + _clip01(mom_rev, 0, 20) * 30
    rev_score = min(100.0, rev_raw) * 0.20

    # EPS:優先用同一股票池的正 EPS 百分位，避免跨產業直接比較絕對值。
    eps = entry.get("eps")
    eps_percentile = entry.get("eps_percentile")
    eps_raw = eps_percentile if eps_percentile is not None else _clip01(eps, 0, 5) * 100
    eps_score = eps_raw * 0.10

    total = chips_score + mom_score + rev_score + eps_score
    return {
        "score": int(round(max(0.0, min(100.0, total)))),
        "components": {
            "chips": round(chips_score, 1),
            "momentum": round(mom_score, 1),
            "revenue": round(rev_score, 1),
            "eps": round(eps_score, 1),
        },
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


# TWSE 上市公司「產業別」代碼 → 名稱(t187ap03_L OpenAPI 的「產業別」欄位回傳 2 碼代碼,非名稱)。
# 代碼經 2026-06 以已知產業代表股交叉驗證(24=半導體/15=航運/17=金融/26=光電/28=電子零組件/31=其他電子…)。
TWSE_INDUSTRY_CODES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業",
    "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造",
    "15": "航運業", "16": "觀光餐旅", "17": "金融保險業", "18": "貿易百貨",
    "19": "綜合", "20": "其他", "21": "化學工業", "22": "生技醫療業",
    "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業",
    "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
    "31": "其他電子業", "32": "文化創意業", "33": "農業科技業", "34": "電子商務",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活",
    "80": "管理股票", "91": "存託憑證",
}


def _industry_name(raw) -> str:
    """產業別代碼 → 名稱;已是名稱(或未知代碼)則原樣返回(向下相容測試/未來新代碼)。
    自行安全 coerce:None / JSON null → ''(不會變成字串 'None')。"""
    s = str(raw).strip() if raw is not None else ""
    return TWSE_INDUSTRY_CODES.get(s, s)


def _fetch_twse_listing_basics() -> dict[str, dict]:
    """Fetch current TWSE listing metadata and issued shares for ranking/backfill."""
    r = _http_get(
        "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    r.raise_for_status()
    basics = r.json() or []
    if not basics:
        raise RuntimeError("上市公司基本資料為空")
    keys = list(basics[0].keys())
    code_k = next((k for k in keys if "公司代號" in k or k.strip() == "代號"), None)
    name_k = (next((k for k in keys if "簡稱" in k), None)
              or next((k for k in keys if "公司名稱" in k or "名稱" in k), None))
    ind_k = next((k for k in keys if "產業別" in k), None)
    share_k = next((k for k in keys if "發行" in k and "股數" in k), None)
    if not code_k or not share_k:
        raise RuntimeError(f"上市公司基本資料欄位偵測失敗: {keys}")
    output = {}
    for row in basics:
        code = str(row.get(code_k, "")).strip()
        shares = _to_int(row.get(share_k))
        if len(code) == 4 and code.isdigit() and shares:
            output[code] = {
                "name": (str(row.get(name_k, "")).strip() or code) if name_k else code,
                "industry": _industry_name(row.get(ind_k)) if ind_k else "",
                "shares": shares,
            }
    if not output:
        raise RuntimeError("沒有有效上市公司基本資料")
    _TWSE_LISTING_BASICS_CACHE["data"] = output   # 供容錯快取版共用(類股熱度免重抓)
    return output


_TWSE_LISTING_BASICS_CACHE: dict = {"data": None, "failed": False}


def _get_twse_listing_basics_cached() -> dict[str, dict]:
    """`_fetch_twse_listing_basics` 的容錯快取版:同一次執行只抓一次,失敗回 {}(不拋)。
    universe 走原本會拋例外的版本(它需要 raise 來分流 fallback);其餘唯讀取用點走這支。
    universe 若已先跑過,其成功結果已寫入 _TWSE_LISTING_BASICS_CACHE,這裡直接命中、零新請求。"""
    c = _TWSE_LISTING_BASICS_CACHE
    if c["data"] is not None:
        return c["data"]
    if c.get("failed"):
        return {}
    try:
        return _fetch_twse_listing_basics()   # 成功時會自行寫入快取
    except Exception as e:
        print(f"[twse] 上市基本資料抓取失敗(類股熱度略過): {e}", file=sys.stderr)
        c["failed"] = True
        return {}


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
    try:
        basics = _fetch_twse_listing_basics()
        prices = _fetch_twse_stock_day_all()
        if not prices:
            raise RuntimeError("OpenAPI 回傳空資料")

        # 自動偵測欄位名（TWSE 偶爾微調欄位字串）
        pk = list(prices[0].keys())
        pcode_k = next((k for k in pk if k == "Code" or "證券代號" in k or "公司代號" in k
                        or k.strip() == "代號"), None)
        close_k = next((k for k in pk if "clos" in k.lower() or "收盤" in k), None)

        if not all([pcode_k, close_k]):
            raise RuntimeError(f"OpenAPI 欄位偵測失敗 prices={pk}")

        price_map: dict[str, float] = {}
        for row in prices:
            c = str(row.get(pcode_k, "")).strip()
            cp = _to_float(row.get(close_k))
            if c and cp:
                price_map[c] = cp

        rows: list[dict] = []
        for c, basic in basics.items():
            shares = basic["shares"]
            close = price_map.get(c)
            if not shares or not close:
                continue
            rows.append({
                "code": c,
                "name": basic["name"],
                "industry": basic["industry"],
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
        r = _http_get("https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
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


def load_revenue_consensus() -> dict[str, dict]:
    """
    Load an optional point-in-time revenue consensus file.

    The file is intentionally external: TWSE publishes actual revenue, but a free official
    analyst-consensus feed is not available. Expected format:
    {"2330":{"month":"11505","expected_rev":300000000000,"source":"vendor"}}.
    """
    if not REVENUE_CONSENSUS_FILE.exists():
        return {}
    try:
        payload = json.loads(REVENUE_CONSENSUS_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception as e:
        print(f"[revenue_consensus] 載入失敗: {e}", file=sys.stderr)
        return {}


def _revenue_expectation_feature(actual: dict,
                                 consensus: Optional[dict] = None) -> dict:
    """Prefer real consensus; otherwise use a conservative disclosed-growth baseline."""
    actual_rev = _safe_number(actual.get("rev"))
    expected_rev = _safe_number((consensus or {}).get("expected_rev"))
    if actual_rev and expected_rev:
        surprise = (actual_rev / expected_rev - 1) * 100
        return {
            "rev_expected": expected_rev,
            "rev_surprise_pct": round(max(-50.0, min(50.0, surprise)), 3),
            "rev_expectation_method": "external_consensus",
            "rev_expectation_source": (consensus or {}).get("source") or "configured vendor",
        }
    yoy = actual.get("yoy_pct")
    cum_yoy = actual.get("cum_yoy_pct")
    if isinstance(yoy, (int, float)) and isinstance(cum_yoy, (int, float)):
        return {
            "rev_expected": None,
            "rev_surprise_pct": round(max(-50.0, min(50.0, yoy - cum_yoy)), 3),
            "rev_expectation_method": "cumulative_yoy_baseline",
            "rev_expectation_source": "TWSE actual revenue trend proxy",
        }
    return {
        "rev_expected": None,
        "rev_surprise_pct": None,
        "rev_expectation_method": "missing",
        "rev_expectation_source": None,
    }


def fetch_tw_eps() -> dict[str, dict]:
    """
    抓上市公司最新季度「基本每股盈餘 EPS」(TWSE OpenAPI 綜合損益表,免費無 key)。

    多個產業別端點(一般業/金融/證券/保險/金控),逐一嘗試合併。
    回傳：{ code: {"eps": float, "quarter": "11501" 之類} }。
    EPS 年增需跨年同期比較(snapshot 無歷史)→ 由 state 累積後另算;此處先給「絕對 EPS」
    當獲利能力 / 品質訊號。全部失敗回 {}(不影響晨報)。
    """
    endpoints = [
        "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",     # 一般業
        "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_basi",   # 金融
        "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_bd",     # 證券
        "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_mim",    # 金控
    ]
    out: dict[str, dict] = {}
    for url in endpoints:
        try:
            r = _http_get(url, timeout=20,
                             headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            r.raise_for_status()
            data = r.json() or []
            if not data:
                continue
            keys = list(data[0].keys())
            code_k = next((k for k in keys if "公司代號" in k or k.strip() == "代號"), None)
            eps_k = next((k for k in keys if "每股盈餘" in k), None)
            q_k = next((k for k in keys if ("年度" in k or "季別" in k or "資料年月" in k)), None)
            if not code_k or not eps_k:
                continue
            for row in data:
                c = str(row.get(code_k, "")).strip()
                if not (len(c) == 4 and c.isdigit()):
                    continue
                eps = _to_float(row.get(eps_k))
                if eps is None:
                    continue
                out[c] = {"eps": eps,
                          "quarter": (str(row.get(q_k, "")).strip() if q_k else "")}
        except Exception as e:
            print(f"[eps] {url.rsplit('/', 1)[-1]} 抓取失敗(略過): {e}", file=sys.stderr)
            continue
    print(f"[eps] 取得 {len(out)} 檔季度 EPS")
    return out


def _attach_listing_fundamentals(snapshot: list[dict]) -> None:
    """為每日快照就地補上『估值/獲利率/ROE』(TWSE OpenAPI 全市場各一次取,免金鑰),
    供 model_history 累積、日後回測基本面/估值因子(這是鋪路:先存,夠長再驗 IC)。
    任一來源失敗只跳過該欄,絕不影響晨報主流程。"""
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    def _j(url: str) -> list:
        try:
            r = _http_get(url, timeout=20, headers=headers)
            r.raise_for_status()
            return r.json() or []
        except Exception as e:
            print(f"[fundamentals] {url.rsplit('/', 1)[-1]} 失敗(略過): {e}", file=sys.stderr)
            return []

    def _ok(c: str) -> bool:
        return len(c) == 4 and c.isdigit()

    val, margin, net_income, eq_asset = {}, {}, {}, {}
    for row in _j("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"):
        c = str(row.get("Code", "")).strip()
        if _ok(c):
            val[c] = {"per": _to_float(row.get("PEratio")),
                      "yield_pct": _to_float(row.get("DividendYield")),
                      "pbr": _to_float(row.get("PBratio"))}
    for row in _j("https://openapi.twse.com.tw/v1/opendata/t187ap17_L"):
        c = str(row.get("公司代號", "")).strip()
        if _ok(c):
            margin[c] = {"gross_margin": _to_float(row.get("毛利率(%)(營業毛利)/(營業收入)")),
                         "op_margin": _to_float(row.get("營業利益率(%)(營業利益)/(營業收入)")),
                         "net_margin": _to_float(row.get("稅後純益率(%)(稅後純益)/(營業收入)"))}
    for row in _j("https://openapi.twse.com.tw/v1/opendata/t187ap14_L"):
        c = str(row.get("公司代號", "")).strip()
        if _ok(c):
            net_income[c] = _to_float(row.get("稅後淨利"))
    for row in _j("https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci"):
        c = str(row.get("公司代號", "")).strip()
        if _ok(c):
            eq_asset[c] = (_to_float(row.get("權益總額")), _to_float(row.get("資產總額")))
    attached = 0
    for e in snapshot or []:
        c = str(e.get("code", "")).strip()
        if c in val:
            e.update(val[c])
        if c in margin:
            e.update(margin[c])
        n = net_income.get(c)
        eq, asset = eq_asset.get(c, (None, None))
        if n is not None and eq and eq > 0:
            e["roe_q"] = round(n / eq * 100, 1)
        if n is not None and asset and asset > 0:
            e["roa_q"] = round(n / asset * 100, 1)
        if c in val or c in margin:
            attached += 1
    print(f"[fundamentals] 估值/獲利率附加 {attached} 檔(供 model_history 累積)")


def _finmind_top5_extras(codes: list[str], prices: dict | None = None) -> dict:
    """為每日 Top5(少量代號)補 FinMind 的 EPS 年增率 + 外資持股比率 + 財報品質評分
    (Piotroski F-score / Altman Z-score)+ DCF 內在價值 gap(估值三法,只取 DCF;教育/非商業用途)。
    token 選填(FINMIND_TOKEN),任何代號/欄位失敗都略過,絕不影響晨報。F/Z 與估值共用同一次三表抓取。
    回 {code: {eps_latest, eps_yoy_pct, foreign_hold_pct, fscore, fscore_denom, zscore, zscore_zone,
              val_dcf_gap_pct, val_dcf_zone}}。"""
    token = os.getenv("FINMIND_TOKEN", "").strip()
    today = dt.datetime.now(TPE).date()
    eps_start = (today - dt.timedelta(days=550)).isoformat()    # 至少 5 季
    fh_start = (today - dt.timedelta(days=45)).isoformat()

    def _finmind(dataset: str, sid: str, start: str) -> list:
        params = {"dataset": dataset, "data_id": sid, "start_date": start}
        if token:
            params["token"] = token
        r = _http_get("https://api.finmindtrade.com/api/v4/data",
                         params=params, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        return (r.json() or {}).get("data") or []

    out = {}
    for c in codes:
        rec = {}
        try:
            data = _finmind("TaiwanStockFinancialStatements", c, eps_start)
            eps = sorted(((str(row.get("date")), _to_float(row.get("value")))
                          for row in data
                          if row.get("type") == "EPS" and _to_float(row.get("value")) is not None),
                         key=lambda x: x[0])
            if eps:
                latest_d, latest_v = eps[-1]
                rec["eps_latest"] = latest_v
                yago = (str(int(latest_d[:4]) - 1) + latest_d[4:]) if latest_d[:4].isdigit() else ""
                prior = next((v for d, v in eps if d == yago), None)
                if prior:
                    rec["eps_yoy_pct"] = round((latest_v - prior) / abs(prior) * 100, 1)
        except Exception:
            pass
        try:
            data = _finmind("TaiwanStockShareholding", c, fh_start)
            if data:
                fhp = _to_float(data[-1].get("ForeignInvestmentSharesRatio"))
                if fhp is not None:
                    rec["foreign_hold_pct"] = fhp
        except Exception:
            pass
        try:
            import fz_score   # 同目錄獨立模組;Piotroski F + Altman Z(吃 FinMind 三表)
            _px = (prices or {}).get(c)
            _stmts = fz_score.fetch_statements(c, token)   # 抓一次,餵 F/Z 與估值兩邊
            fz = fz_score.compute(c, _px, token, stmts=_stmts)
            if fz.get("fscore") is not None:
                rec["fscore"] = fz["fscore"]
                rec["fscore_denom"] = fz.get("fscore_denom")
            if fz.get("zscore") is not None:
                rec["zscore"] = fz["zscore"]
                rec["zscore_zone"] = fz.get("zscore_zone")
            if fz.get("mscore") is not None:                 # Beneish M-score(盈餘操弄)
                rec["mscore"] = fz["mscore"]
                rec["mscore_flag"] = fz.get("mscore_flag")
            # 估值:只取 DCF gap(持續經營內在價值;辨別度最高)。獨立 try:估值出錯不拖累 F/Z。
            try:
                import valuation   # 估值三法(移植 ai-hedge-fund);與 F/Z 共用上面的 _stmts
                val = valuation.compute(c, _px, token, stmts=_stmts)
                dcf_gap = (val.get("per_method_gap_pct") or {}).get("dcf")
                if dcf_gap is not None:
                    rec["val_dcf_gap_pct"] = dcf_gap
                    rec["val_dcf_zone"] = ("偏低估" if dcf_gap > 15 else ("偏高估" if dcf_gap < -15 else "合理"))
            except Exception:
                pass
        except Exception:
            pass
        if rec:
            out[c] = rec
    return out


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
        r = _http_get("https://opendata.tdcc.com.tw/getOD.ashx?id=1-5",
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


def fetch_twse_recent_closes(code: str, want: int = 3) -> list:
    """
    用 TWSE STOCK_DAY 抓某代號最近 want 個交易日的官方收盤(由舊到新)。

    為什麼用 TWSE 而非 Yahoo:個人持股多為 ETF(00662/0050/00631L),Yahoo 對 ETF
    常落後一天 → 算「昨日漲跌」會抓到錯的兩天。TWSE STOCK_DAY 是權威日線來源。
    跨月(月初)時自動補抓上個月。失敗 / 不足回傳 []（呼叫端自行略過該檔）。

    隱私:log 不印代號(repo 若公開,Actions log 也公開)。
    """
    today = dt.datetime.now(TPE).date()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
               "Accept": "application/json"}
    rows: list = []   # (date_str, close) 由舊到新
    d = today
    for _ in range(2):   # 本月 + 上月(處理月初資料不足)
        ym = d.strftime("%Y%m%d")
        url = (f"https://www.twse.com.tw/exchangeReport/STOCK_DAY"
               f"?response=json&date={ym}&stockNo={code}")
        month_rows: list = []
        try:
            r = _http_get(url, timeout=15, headers=headers)
            r.raise_for_status()
            data = r.json()
            if data.get("stat") == "OK":
                fields = data.get("fields", []) or []
                close_i = next((i for i, f in enumerate(fields) if "收盤" in f), 6)
                for row in data.get("data", []) or []:
                    if close_i < len(row):
                        c = _to_float(row[close_i])
                        if c:
                            month_rows.append((str(row[0]), c))
        except Exception as e:
            print(f"[recent_close] STOCK_DAY {ym} 失敗(略過): {e}", file=sys.stderr)
        rows = month_rows + rows          # 較早的月份接在前面 → 維持升序
        if len(rows) >= want:
            break
        d = d.replace(day=1) - dt.timedelta(days=1)   # 上個月最後一天
    return [c for _, c in rows][-want:]


_TWSE_STOCK_DAY_ALL_CACHE: dict = {"data": None}
_TWSE_RETRY_SLEEP_BASE = 3.0   # 測試中設 0 避免退避拖慢


def _fetch_twse_stock_day_all() -> list:
    """STOCK_DAY_ALL 共用 getter:重試 3 次(退避)+ 程序內快取。

    此 API 被「個股收盤價 / 市場廣度 / 外資彙整」三處各自請求且回應 ~MB 級;
    2026-06-12 單次失敗即導致 0050 預測、市場廣度、11 維兩維全缺。
    改為共用快取(同一次執行只抓一次)+ 重試,單點故障機率大幅下降。
    """
    if _TWSE_STOCK_DAY_ALL_CACHE["data"] is not None:
        return _TWSE_STOCK_DAY_ALL_CACHE["data"]
    if _TWSE_STOCK_DAY_ALL_CACHE.get("failed"):
        # 失敗哨兵:本次執行已重試耗盡,4 個呼叫點不重複各自重試(最壞省 ~3 分鐘)
        return []
    for attempt in range(3):
        try:
            r = _http_get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                             timeout=20,
                             headers={"User-Agent": "Mozilla/5.0",
                                      "Accept": "application/json",
                                      "Accept-Language": "zh-TW,zh;q=0.9",
                                      "Referer": "https://www.twse.com.tw/"})
            r.raise_for_status()
            data = r.json() or []
            if data:
                _TWSE_STOCK_DAY_ALL_CACHE["data"] = data
                return data
        except Exception as e:
            print(f"[twse] STOCK_DAY_ALL 第 {attempt + 1} 次失敗: {e}", file=sys.stderr)
            # 2026-06-12 實測:三連敗多為同一波過載/限流,間隔拉長到 10/20 秒
            if attempt < 2 and _TWSE_RETRY_SLEEP_BASE > 0:
                time.sleep(_TWSE_RETRY_SLEEP_BASE * (attempt + 1) * 3.3)
    _TWSE_STOCK_DAY_ALL_CACHE["failed"] = True
    return []


def fetch_twse_close(code: str) -> Optional[float]:
    """
    從 TWSE OpenAPI STOCK_DAY_ALL 抓單一上市標的（含 ETF）的最新「官方」收盤價。

    為什麼需要：Yahoo Finance 對台股 ETF（如 00662 富邦 NASDAQ）的資料常落後一天
    或卡價不動，導致「昨收」抓到舊值、連帶汙染合理價估值與回歸 beta。
    TWSE 是台股/台股 ETF 的權威來源。失敗回傳 None（由呼叫端 fallback）。
    """
    try:
        data = _fetch_twse_stock_day_all()
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
        r = _http_get("https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK",
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
        r = _http_get("https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX",
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
        data = _fetch_twse_stock_day_all()
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


def _third_wednesday(yyyymm: str) -> Optional[dt.date]:
    """台指選擇權月合約結算日=該月第三個星期三。"""
    try:
        y, m = int(yyyymm[:4]), int(yyyymm[4:6])
        d = dt.date(y, m, 1)
        offset = (2 - d.weekday()) % 7          # 週三 weekday=2
        return d + dt.timedelta(days=offset + 14)
    except (ValueError, IndexError):
        return None


def fetch_txo_magnet() -> dict:
    """台指選擇權近月籌碼 → 結算磁吸參考價(白話呈現,不進計分)。

    TAIFEX OpenAPI 選擇權日行情(免金鑰,實測 11,947 列):取 TXO 近月(六碼月份,
    排除週別 W/F 合約),對每個可能結算價計算全體賣方總賠付,最低點=籌碼最集中的
    「磁吸參考價」;另取買權/賣權未平倉最大的履約價當上下參考。失敗回 {}。
    """
    try:
        r = _http_get("https://openapi.taifex.com.tw/v1/DailyMarketReportOpt",
                      timeout=30, headers={"User-Agent": "Mozilla/5.0",
                                           "Accept": "application/json"})
        r.raise_for_status()
        rows = [x for x in (r.json() or []) if x.get("Contract") == "TXO"]
        months = sorted({str(x.get("ContractMonth(Week)") or "") for x in rows
                         if len(str(x.get("ContractMonth(Week)") or "")) == 6})
        if not months:
            return {}
        front = months[0]
        strikes: dict[float, list] = {}    # K -> [call_oi, put_oi]
        for x in rows:
            if str(x.get("ContractMonth(Week)")) != front:
                continue
            oi = _to_float(x.get("OpenInterest"))
            k = _to_float(x.get("StrikePrice"))
            if not k or oi is None or oi <= 0:
                continue   # 盤後列 OI 為 "-"、零 OI 履約價都略過
            side = 0 if "買" in str(x.get("CallPut", "")) else 1
            strikes.setdefault(k, [0.0, 0.0])[side] += oi
        if len(strikes) < 5:
            return {}
        ks = sorted(strikes)
        # 磁吸價=令「全體選擇權賣方總賠付」最小的結算價(籌碼最集中處)
        def _payout(s: float) -> float:
            return sum(c * max(0.0, s - k) + p * max(0.0, k - s)
                       for k, (c, p) in strikes.items())
        magnet = min(ks, key=_payout)
        # 壓力/支撐牆只在磁吸價 ±6% 內找:深價外(如 ±10%)履約價常掛最大未平倉
        # (避險/樂透倉),對隔日盤勢毫無參考性(實測:全域最大 OI 落在 50,000/40,000)。
        # 且該側 OI 必須 >0:履約價可能只掛另一側(如僅有賣權),零 OI 不得當牆(Codex review)
        near_up = [k for k in ks if magnet < k <= magnet * 1.06 and strikes[k][0] > 0]
        near_dn = [k for k in ks if magnet * 0.94 <= k < magnet and strikes[k][1] > 0]
        call_wall = max(near_up, key=lambda k: strikes[k][0]) if near_up else None
        put_wall = max(near_dn, key=lambda k: strikes[k][1]) if near_dn else None
        settle = _third_wednesday(front)
        out = {"magnet": magnet, "call_wall": call_wall, "put_wall": put_wall,
               "month": front,
               "settle": settle.strftime("%m/%d") if settle else ""}
        print(f"[txo] 近月 {front} 磁吸參考 {magnet:,.0f}"
              f"(壓力 {call_wall}/支撐 {put_wall},結算 {out['settle']})")
        return out
    except Exception as e:
        print(f"[txo] 選擇權磁吸價計算失敗(不影響晨報): {e}", file=sys.stderr)
        return {}


def fetch_market_valuation() -> dict:
    """台股估值溫度(白話,不進計分):全市場本益比/殖利率中位數 + 2330 個股估值。

    TWSE BWIBBU_ALL(免金鑰,實測 1,078 檔):PEratio/DividendYield/PBratio。
    溫度標籤用長期經驗區間(PE 中位 <13 偏便宜、13~18 合理、>18 偏貴)——啟發式
    顯示判讀,非計分訊號。失敗回 {}。
    """
    try:
        r = _http_get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
                      timeout=20, headers={"User-Agent": "Mozilla/5.0",
                                           "Accept": "application/json"})
        r.raise_for_status()
        rows = r.json() or []
        pes, yields = [], []
        tsmc = {}
        for x in rows:
            pe = _to_float(x.get("PEratio"))
            dy = _to_float(x.get("DividendYield"))
            if pe and 0 < pe < 500:
                pes.append(pe)
            if dy and 0 < dy < 30:
                yields.append(dy)
            if str(x.get("Code")) == "2330":
                tsmc = {"pe": pe, "yield": dy, "pb": _to_float(x.get("PBratio"))}
        if len(pes) < 100:
            return {}
        med_pe = statistics.median(pes)
        med_dy = statistics.median(yields) if yields else None
        label = ("偏便宜" if med_pe < 13 else ("合理區間" if med_pe <= 18 else "偏貴"))
        out = {"median_pe": round(med_pe, 1),
               "median_yield": round(med_dy, 2) if med_dy else None,
               "n": len(pes), "label": label, "tsmc": tsmc}
        print(f"[valuation] 全市場 PE 中位 {out['median_pe']}、"
              f"殖利率中位 {out['median_yield']}% → {label}")
        return out
    except Exception as e:
        print(f"[valuation] 估值溫度計算失敗(不影響晨報): {e}", file=sys.stderr)
        return {}


def fetch_sector_heat(top_leaders: int = 3, min_names: int = 3) -> dict:
    """按 TWSE 產業別彙整當日「類股熱度」——純計算,重用已快取的 STOCK_DAY_ALL(當日成交)
    與上市公司基本資料(產業別),**不新增網路請求、不進任何計分**。

    用途:(1) 給「九、其他類股」LLM 硬數據背景(哪些類股在動、領漲股是誰),讓分析有行情
    佐證而非只憑標題;(2) 動態決定要補查哪些非科技類股的個股新聞(見 fetch_sector_leader_news)。
    任何環節失敗回 {}(晨報不可斷)。

    回傳 {
      "sectors": { 產業名稱: {"n","up","down","median_pct","value_yi","value_share_pct",
                             "leaders":[{"code","name","pct","value_yi"}, ...]} },
      "ranked": [產業名稱, ...],          # 依成交值降序
      "total_value_yi": 全市場成交值(億),
    }
    """
    try:
        rows = _fetch_twse_stock_day_all()          # 已快取:與市場廣度共用同一份
        basics = _get_twse_listing_basics_cached()  # 已快取:與 universe 共用同一份
        if not rows or not basics:
            return {}
        keys = list(rows[0].keys())
        code_k = next((k for k in keys if k == "Code" or "證券代號" in k or "代號" in k), None)
        close_k = next((k for k in keys if "clos" in k.lower() or "收盤" in k), None)
        change_k = next((k for k in keys if k.lower() in ("change", "change_value") or k == "漲跌"), None)
        if change_k is None:
            change_k = next((k for k in keys if ("change" in k.lower() and "pct" not in k.lower())
                             or "漲跌" in k), None)
        value_k = next((k for k in keys if "tradevalue" in k.lower() or k in ("TradeValue", "成交金額")), None)
        if not all([code_k, close_k, change_k, value_k]):
            print(f"[sector] STOCK_DAY_ALL 欄位偵測失敗 keys={keys}", file=sys.stderr)
            return {}

        agg: dict[str, dict] = {}
        total_value = 0.0
        for row in rows:
            code = str(row.get(code_k, "")).strip()
            if not (len(code) == 4 and code.isdigit()):   # 只算普通上市股,排除 ETF/權證
                continue
            b = basics.get(code)
            if not b:
                continue
            industry = b.get("industry") or "其他"
            close = _to_float(row.get(close_k))
            change = _to_float(row.get(change_k))
            tv = _to_float(row.get(value_k)) or 0.0
            if close is None or change is None:
                continue
            prev = close - change          # 昨收 = 今收 − 漲跌
            pct = (change / prev * 100) if prev else 0.0
            total_value += tv
            s = agg.setdefault(industry, {"n": 0, "up": 0, "down": 0,
                                          "pcts": [], "value": 0.0, "members": []})
            s["n"] += 1
            if change > 0:
                s["up"] += 1
            elif change < 0:
                s["down"] += 1
            s["pcts"].append(pct)
            s["value"] += tv
            s["members"].append({"code": code, "name": b.get("name") or code,
                                 "pct": round(pct, 2), "value": tv})

        if not agg or total_value <= 0:
            return {}

        sectors: dict[str, dict] = {}
        for industry, s in agg.items():
            if s["n"] < min_names:         # 樣本太少的類別(存託憑證/管理股票)略過
                continue
            leaders = sorted(s["members"], key=lambda m: m["value"], reverse=True)[:top_leaders]
            sectors[industry] = {
                "n": s["n"], "up": s["up"], "down": s["down"],
                "median_pct": round(statistics.median(s["pcts"]), 2),
                "value_yi": round(s["value"] / 1e8, 0),
                "value_share_pct": round(s["value"] / total_value * 100, 1),
                "leaders": [{"code": m["code"], "name": m["name"], "pct": m["pct"],
                             "value_yi": round(m["value"] / 1e8, 1)} for m in leaders],
            }
        ranked = sorted(sectors, key=lambda k: sectors[k]["value_yi"], reverse=True)
        if ranked:
            print(f"[sector] 類股熱度:{len(sectors)} 個產業,最熱 {ranked[0]}"
                  f"(成交 {sectors[ranked[0]]['value_yi']:,.0f} 億)")
        return {"sectors": sectors, "ranked": ranked,
                "total_value_yi": round(total_value / 1e8, 0)}
    except Exception as e:
        print(f"[sector] 類股熱度計算失敗: {e}", file=sys.stderr)
        return {}


def _format_sector_heat_block(sector_heat: dict, top_n: int = 12) -> str:
    """把 fetch_sector_heat 的結果排成精簡文字表,供 LLM「九、其他類股」當硬數據背景。
    純行情數據(非新聞),不含任何持股資訊。無資料回空字串。"""
    sectors = (sector_heat or {}).get("sectors") or {}
    ranked = (sector_heat or {}).get("ranked") or []
    if not sectors or not ranked:
        return ""
    lines = []
    for name in ranked[:top_n]:
        s = sectors.get(name) or {}
        leaders = "、".join(
            f"{m['code']}{m['name']}{m['pct']:+.1f}%" for m in (s.get("leaders") or [])[:3])
        lines.append(
            f"- {name}:成交 {s.get('value_yi', 0):,.0f} 億"
            f"(佔 {s.get('value_share_pct', 0):.1f}%)、中位 {s.get('median_pct', 0):+.1f}%、"
            f"漲 {s.get('up', 0)}/跌 {s.get('down', 0)} | 領先:{leaders or '-'}")
    total = (sector_heat or {}).get("total_value_yi") or 0
    return ("\n\n【類股熱度表(今日 TWSE 全市場,依成交值排序;純行情數據非新聞,"
            f"供「九、其他類股」判斷哪些類股在動、誰領漲。全市場成交約 {total:,.0f} 億)】\n"
            + "\n".join(lines))


def fetch_twse_short_balance(target_codes: Optional[set] = None) -> dict[str, dict]:
    """
    抓 TWSE「融券借券賣出餘額」(TWT93U,全市場一次請求),算空方餘額與日變化。

    為什麼有用:借券賣出餘額 = 機構放空部位(類似 short interest)。
      - 餘額**驟降(還券/回補)** → 空方認輸,短線常見軋空 / 反彈訊號(偏多)
      - 餘額**續增 + 股價漲** → 空方加碼但被軋,潛在軋空燃料
    融券(散戶放空)+ 借券賣出(機構放空)皆為**股數**,合計為總空方餘額。

    端點欄位(兩個區塊各有「前日餘額/今日餘額」,故用「第一次/第二次出現」定位):
      代號 / 名稱 / [融券] 前日餘額,賣出,買進,現券,今日餘額,限額 / [借券] 前日餘額,當日賣出,當日還券,當日調整,當日餘額,可限額,備註
    回傳 {code: {short_balance, short_balance_prev, short_balance_chg, margin_short, sbl_short}}。
    失敗回 {}(不影響晨報)。
    """
    today = dt.datetime.now(TPE).date()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
               "Accept": "application/json"}
    for back in range(1, 8):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y%m%d")
        url = (f"https://www.twse.com.tw/exchangeReport/TWT93U"
               f"?response=json&date={date_str}")
        try:
            r = _http_get(url, timeout=20, headers=headers)
            r.raise_for_status()
            data = r.json()
            if data.get("stat") != "OK":
                continue
            rows = data.get("data") or []
            fields = [str(f).strip() for f in (data.get("fields") or [])]
            if not rows:
                continue
            # 欄位定位:前日餘額 出現兩次(融券、借券);今日餘額=融券、當日餘額=借券
            prev_idxs = [i for i, f in enumerate(fields) if f == "前日餘額"]
            code_i = next((i for i, f in enumerate(fields) if "代號" in f), 0)
            mshort_now_i = next((i for i, f in enumerate(fields) if f == "今日餘額"), 6)
            sbl_now_i = next((i for i, f in enumerate(fields) if f == "當日餘額"), 12)
            mshort_prev_i = prev_idxs[0] if prev_idxs else 2
            sbl_prev_i = prev_idxs[1] if len(prev_idxs) >= 2 else 8
            max_i = max(mshort_now_i, sbl_now_i, mshort_prev_i, sbl_prev_i, code_i)
            out: dict[str, dict] = {}
            for row in rows:
                if len(row) <= max_i:
                    continue
                code = str(row[code_i]).strip()
                if not (len(code) == 4 and code.isdigit()):    # 只取上市普通股 4 碼
                    continue
                if target_codes is not None and code not in target_codes:
                    continue
                m_now = _to_int(row[mshort_now_i])
                m_prev = _to_int(row[mshort_prev_i])
                s_now = _to_int(row[sbl_now_i])
                s_prev = _to_int(row[sbl_prev_i])
                total_now = m_now + s_now
                total_prev = m_prev + s_prev
                out[code] = {
                    "short_balance": total_now,
                    "short_balance_prev": total_prev,
                    "short_balance_chg": total_now - total_prev,
                    "margin_short": m_now,
                    "sbl_short": s_now,
                }
            if out:
                print(f"[short_bal] {date_str} 取得 {len(out)} 檔融券+借券賣出餘額")
                return out
        except Exception as e:
            print(f"[short_bal] {date_str} 失敗: {e}", file=sys.stderr)
            continue
    print("[short_bal] 所有日期皆失敗", file=sys.stderr)
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
    revenue_consensus = load_revenue_consensus()       # 選填：外部市場預期基準
    eps_map = fetch_tw_eps()                           # 季度 EPS（綜合損益表，全市場）
    tdcc = fetch_tdcc_major_holders(target_codes)     # 大戶持股比例（一次請求全市場）
    short_bal = fetch_twse_short_balance(target_codes)  # 融券+借券賣出餘額（空方,全市場）
    snapshot: list[dict] = []
    codes = list(universe.keys())

    # yfinance 批次下載 (每檔加 .TW) — 100 檔仍是「一次」request
    tickers = " ".join(f"{c}.TW" for c in codes)
    try:
        df_all = yf.download(tickers, period="3mo", group_by="ticker",
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
            open_price = safe_float(last.get("Open"))
            prev_close = safe_float(prev["Close"])
            day_pct = (close - prev_close) / prev_close * 100 if prev_close else 0

            vol = safe_float(last["Volume"])
            trade_value = (vol or 0) * close
            avg5_vol = sub["Volume"].tail(5).mean()
            vol_ratio = (vol / avg5_vol) if avg5_vol else None
            # 20 日均量比(更可靠的「異常量能」訊號;5 日窗對短期波動敏感)
            avg20_vol = sub["Volume"].iloc[-21:-1].mean() if len(sub) >= 21 else None
            vol_ratio_20d = (vol / avg20_vol) if avg20_vol and avg20_vol > 0 else None

            # 突破 / 跌破 20 日高 / 低(法人連買 + 突破 = 多頭續攻)
            high20_break = False
            low20_break = False
            if len(sub) >= 21:
                prior20 = sub["Close"].iloc[-21:-1]
                high20 = float(prior20.max())
                low20 = float(prior20.min())
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
            daily_vol_pct = None
            if len(sub) >= 21:
                rets = sub["Close"].pct_change().dropna().tail(20)
                if len(rets):
                    daily_vol_pct = float(rets.std()) * 100

            inst_data = inst.get(code, {})
            inst_30 = inst_30d.get(code, {})
            rev = revenue.get(code, {})
            rev_expectation = _revenue_expectation_feature(
                rev, revenue_consensus.get(code))
            eps_data = eps_map.get(code, {})
            tdcc_data = tdcc.get(code, {})
            sb_data = short_bal.get(code, {})
            info = universe[code]
            # 籌碼悄悄站隊原料:法人連買天數、大戶 WoW、個股融資變化
            streaks = _calc_inst_streaks(inst_30.get("daily") or [])
            tdcc_wow = tdcc_wow_map.get(code)
            # 空方回補比:-(空方餘額日變化)/近20日均量 ×100
            #   正 = 淨回補/還券(空方認輸,短線偏多);負 = 空方加碼(壓力或軋空燃料)
            _short_chg = sb_data.get("short_balance_chg")
            short_cover_ratio = (round(-_short_chg / avg20_vol * 100, 2)
                                 if (_short_chg is not None and avg20_vol and avg20_vol > 0)
                                 else None)
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
                "open": round(open_price, 2) if open_price else None,
                "day_pct": round(day_pct, 2),
                "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
                "vol_ratio_20d": round(vol_ratio_20d, 2) if vol_ratio_20d else None,
                "trade_value": round(trade_value, 0) if trade_value else None,
                "volume": round(vol, 0) if vol else None,
                "slippage_bps": _estimate_slippage_bps(trade_value, daily_vol_pct),
                "liquidity_eligible": bool(trade_value and trade_value >= TW_LIQUIDITY_MIN_TWD),
                "high20_break": bool(high20_break),
                "low20_break": bool(low20_break),
                "month_pct": round(month_pct, 2),
                # 新增:5日累積動能 + 距 MA20(看是否過熱)
                "pct_5d": round(pct_5d, 2) if pct_5d is not None else None,
                "ma20_dist_pct": round(ma20_dist_pct, 2) if ma20_dist_pct is not None else None,
                "daily_vol_pct": round(daily_vol_pct, 3) if daily_vol_pct is not None else None,
                "foreign_lot": round(inst_data.get("foreign", 0) / 1000, 1),
                "invest_lot": round(inst_data.get("investment", 0) / 1000, 1),
                "dealer_lot": round(inst_data.get("dealer", 0) / 1000, 1),
                "total_lot": round(inst_data.get("total", 0) / 1000, 1),
                # 法人單日淨買占近 20 日均量 %(標準化法人信心;+20% = 淨買達日均量 1/5)
                "inst_buy_vol_ratio": (round(inst_data.get("total", 0) / avg20_vol * 100, 2)
                                       if avg20_vol and avg20_vol > 0 else None),
                # 空方餘額(融券+借券賣出,股)+ 回補比(正=空方還券回補,短線偏多)
                "short_balance": sb_data.get("short_balance"),
                "short_balance_chg": sb_data.get("short_balance_chg"),
                "short_cover_ratio": short_cover_ratio,
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
                "margin_balance_lot": round(margin_data.get("margin_balance") or 0, 0),   # 已是「張」,勿再除(舊版誤 /1000 → 信中少 1000 倍)
                # 已是「張」,勿再 /1000:calc_smart_money_score 用 <=-200 張當「散戶丟給法人」門檻,
                # 舊版 /1000 後值剩 ~0 → 此籌碼訊號從未觸發(failed-silent);移除後恢復作用。
                "margin_change_lot": round(margin_data.get("margin_change") or 0, 0)
                                        if margin_data.get("margin_change") is not None else None,
                # 月營收基本面
                "rev_month":   rev.get("month"),
                "rev_yoy_pct": rev.get("yoy_pct"),
                "rev_mom_pct": rev.get("mom_pct"),
                **rev_expectation,
                # 季度 EPS(綜合損益表)
                "eps": eps_data.get("eps"),
                "eps_quarter": eps_data.get("quarter"),
                # 大戶持股比例（TDCC 集保，≥400 張，週更）
                "major_holder_pct": tdcc_data.get("major_holder_pct"),
            }
            # 籌碼悄悄站隊分數:綜合「外資連買 + 大戶 WoW + 量縮收紅 + 偷買區」
            entry["smart_money"] = calc_smart_money_score(entry)
            snapshot.append(entry)
        except (KeyError, ValueError, TypeError) as e:
            print(f"[snapshot] {code} 跳過: {e}", file=sys.stderr)
            continue

    eps_values = sorted({
        float(s["eps"]) for s in snapshot
        if isinstance(s.get("eps"), (int, float)) and s["eps"] > 0
    })
    eps_rank = {
        value: (50.0 if len(eps_values) == 1 else index / (len(eps_values) - 1) * 100)
        for index, value in enumerate(eps_values)
    }
    # 相對強度 vs 同業:pct_5d − 該產業中位數(>0 = 比同業強,短線輪動領先指標)
    industry_p5: dict[str, list] = {}
    for entry in snapshot:
        p5 = entry.get("pct_5d")
        if isinstance(p5, (int, float)):
            industry_p5.setdefault(str(entry.get("industry") or "未分類"), []).append(p5)
    industry_median = {}
    for ind, vals in industry_p5.items():
        sv = sorted(vals)
        n = len(sv)
        industry_median[ind] = (sv[n // 2] if n % 2 else (sv[n // 2 - 1] + sv[n // 2]) / 2) if n else 0.0

    for entry in snapshot:
        eps = entry.get("eps")
        entry["eps_percentile"] = eps_rank.get(float(eps), 0.0) if isinstance(eps, (int, float)) else None
        p5 = entry.get("pct_5d")
        med = industry_median.get(str(entry.get("industry") or "未分類"), 0.0)
        entry["rel_strength_5d"] = (round(p5 - med, 2) if isinstance(p5, (int, float)) else None)
        # 短線爆發力複合分數(籌碼+動能+營收+EPS),供「關注五檔」排序
        entry["breakout"] = calc_breakout_score(entry)

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
                           usdtwd_prev: Optional[float] = None,
                           ex_div_amt: float = 0.0) -> dict:
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
    if ex_div_amt:
        fair_price -= ex_div_amt

    result = {
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
    if ex_div_amt:
        result["ex_div_amt"] = round(ex_div_amt, 4)
    return result


def _merge_share_dicts(*portfolios: dict) -> dict:
    """把多個帳戶的 {code: 股數} 合併,同代號跨帳戶「相加」(非覆蓋)。
    {**a, **b} 會丟掉 a 的重複代號股數 → 市值權重/曝險全錯,故需逐檔加總(Codex review)。"""
    out: dict = {}
    for pf in portfolios:
        for code, shares in (pf or {}).items():
            out[code] = out.get(code, 0) + shares
    return out


def _history_close_by_date(ticker: str, period: str = "6mo") -> dict:
    """抓單一 ticker 的日收盤 → {'YYYY-MM-DD': close}(去 tz、剔非正值)。失敗回 {}。"""
    try:
        h = yf.Ticker(ticker).history(period=period, auto_adjust=False)
        s = h["Close"].dropna()
        if getattr(s.index, "tz", None) is not None:
            s.index = s.index.tz_localize(None)
        return {d.strftime("%Y-%m-%d"): float(c)
                for d, c in s.items() if c and c > 0}
    except Exception:
        return {}


def fetch_portfolio_risk(portfolio: dict, latest_prices: Optional[dict] = None) -> dict:
    """G1|持倉曝險引擎(白話)。由持股(記憶體 {code: 股數})+ 公開歷史價,估算組合對
    台股大盤 / 美股科技(那斯達克)/ 匯率的連動、情境變動與壓力測試。

    隱私鐵律:回傳**只有比例(%)與相對敏感度**,絕無代號/股數/金額/個股權重
    (權重只活在本函式內、彙總後即丟)。不進 LLM prompt、不進 state、不記 log 明細。
    方法:各持股單因子 OLS beta(對 QQQ 隔夜 lag、對台股大盤/匯率同日),再以市值權重彙總。
    coverage < 0.5 的因子視為資料不足以 None 隱藏。任何失敗回 {}(晨報不可斷)。"""
    if not portfolio:
        return {}
    latest_prices = latest_prices or {}
    try:
        qqq = _history_close_by_date("QQQ")
        twii = _history_close_by_date("^TWII")
        fx = _history_close_by_date("TWD=X")
        if not twii and not qqq:
            return {}   # 兩個主要驅動都抓不到 → 放棄

        values: dict = {}
        betas_tw: dict = {}
        betas_qqq: dict = {}
        betas_fx: dict = {}
        n_priced = 0
        n_samples = 0
        for code in portfolio:
            hist = _history_close_by_date(f"{code}.TW")
            if not hist:
                continue
            # 市值 = 股數 × 最新價(優先 TWSE 官方 last,退回 Yahoo 歷史末值)
            last = latest_prices.get(code) or hist[max(hist)]
            if not last or last <= 0:
                continue
            values[code] = portfolio[code] * last
            n_priced += 1
            a_tw, d_tw = aligned_returns(hist, twii, lag_driver=False)
            betas_tw[code] = ols_beta(a_tw, d_tw)
            a_q, d_q = aligned_returns(hist, qqq, lag_driver=True)
            betas_qqq[code] = ols_beta(a_q, d_q)
            a_f, d_f = aligned_returns(hist, fx, lag_driver=False)
            betas_fx[code] = ols_beta(a_f, d_f)
            # 樣本數取三因子最大值:即使台股大盤資料缺失、僅那斯達克可算,白話卡也不會誤標「近 0 日」
            n_samples = max(n_samples, len(a_tw), len(a_q), len(a_f))

        weights = value_weights(values)
        if not weights:
            return {}
        pf_tw, cov_tw = portfolio_beta(weights, betas_tw)
        pf_qqq, cov_qqq = portfolio_beta(weights, betas_qqq)
        pf_fx, cov_fx = portfolio_beta(weights, betas_fx)

        # 涵蓋率(有有效 beta 的持股權重和)< 0.5 → 資料不足,對應項隱藏
        tw_beta = round(pf_tw, 2) if cov_tw >= 0.5 else None
        qqq_beta = round(pf_qqq, 2) if cov_qqq >= 0.5 else None
        fx_beta = round(pf_fx, 2) if cov_fx >= 0.5 else None
        if tw_beta is None and qqq_beta is None:
            return {}   # 主要曝險都算不出 → 不顯示

        pf_betas: dict = {}
        if tw_beta is not None:
            pf_betas["tw"] = pf_tw
        if qqq_beta is not None:
            pf_betas["qqq"] = pf_qqq
        if fx_beta is not None:
            pf_betas["fx"] = pf_fx

        scen = scenario_rows(pf_betas, [
            ("qqq", -3.0, "美股科技(那斯達克)跌 3%"),
            ("qqq", -5.0, "美股科技(那斯達克)跌 5%"),
            ("tw", -3.0, "台股大盤跌 3%"),
            ("fx", 1.0, "台幣貶值 1%(美元走強)"),
        ])
        stress = stress_rows(pf_betas.get("qqq"), [10, 20, 30])

        cov_shown = max(cov_tw, cov_qqq)   # 兩主因子中涵蓋較高者當「涵蓋部位」白話值
        print(f"[risk] 持倉曝險:台股≈{tw_beta} 那斯達克≈{qqq_beta} 匯率≈{fx_beta} "
              f"(涵蓋 {cov_shown*100:.0f}%, {n_samples} 日, {n_priced} 檔計價)")
        return {
            "tw_beta": tw_beta, "qqq_beta": qqq_beta, "fx_beta": fx_beta,
            "tw_cov": round(cov_tw, 2), "qqq_cov": round(cov_qqq, 2),
            "fx_cov": round(cov_fx, 2), "cov_shown": round(cov_shown, 2),
            "scenarios": scen, "stress": stress, "n_samples": n_samples,
        }
    except Exception as e:
        print(f"[risk] 持倉曝險計算略過(不影響晨報): {e}", file=sys.stderr)
        return {}


def fetch_ma200_status() -> dict:
    """核心持股的 200 日均線(波段長線參考)。定位為「抗回撤/控波動」而非「增報酬」工具:
    回測 5–10 年「站上才持有、跌破轉中性」能把最大回撤砍約 1/3、Sharpe 升;但長多市場(15 年窗)
    0050/2330 的 CAGR 反輸買進持有(離場成本+鋸齒洗刷),未計交易成本/證交稅。失敗逐檔略過,回 {}。
    用未還原收盤(與券商看到的報價一致);最新收盤優先用 TWSE 官方現值(與第六點同源、
    避免 yfinance 落後 1 日造成信中同檔兩個收盤),MA200 仍用 yfinance trailing(同為未還原收盤,基準一致)。"""
    out: dict = {}
    # 對齊使用者實際持股(ETF 為主);00631L 為 2x 槓桿,長抱波動耗損大、回測中
    # 趨勢紀律對它最關鍵(15 年買進持有最大回撤 -96.9%),故特別納入。leveraged 旗標供渲染加註。
    for sym, name, leveraged in (("00662.TW", "00662 富邦NASDAQ", False),
                                 ("0050.TW", "0050 元大台灣50", False),
                                 ("00631L.TW", "00631L 台灣50正2", True),
                                 ("2330.TW", "2330 台積電", False)):
        try:
            d = yf.Ticker(sym).history(period="15mo", auto_adjust=False)
            closes = [float(c) for c in d["Close"].dropna().tolist() if c == c and c > 0]
            if len(closes) < 200:
                continue
            ma200 = sum(closes[-200:]) / 200
            last = closes[-1]
            # 最新收盤改用 TWSE 官方現值(與第六點一致、消除 yfinance 落後 1 日);取不到才用 yfinance
            try:
                official = fetch_twse_close(sym.replace(".TW", ""))
                if official and official > 0:
                    last = float(official)
            except Exception:
                pass
            out[sym] = {"name": name, "close": round(last, 2), "ma200": round(ma200, 2),
                        "above": last >= ma200, "dist_pct": round((last / ma200 - 1) * 100, 1),
                        "leveraged": leveraged}
        except Exception as e:
            print(f"[ma200] {sym} 失敗: {e}", file=sys.stderr)
    return out


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


def _taiex_conflict_adjustment(weighted_pct: float,
                               signal_std: Optional[float],
                               context: Optional[dict]) -> tuple[float, float, list[str]]:
    """Shrink directional TAIEX forecasts when strong cross-signals disagree."""
    if not context or not weighted_pct:
        return weighted_pct, 1.0, []
    macro = context.get("MACRO") or context.get("macro") or {}
    taifex = context.get("TAIFEX_OI") or context.get("taifex_oi") or {}
    reasons = []
    shrink_penalty = 0.0
    foreign_oi = _safe_number(taifex.get("foreign_oi_net"))
    if foreign_oi <= -20000 and weighted_pct > 0:
        shrink_penalty += min(0.35, abs(foreign_oi) / 120000 * 0.35)
        reasons.append("foreign_oi_short")
    elif foreign_oi >= 30000 and weighted_pct < 0:
        shrink_penalty += min(0.25, abs(foreign_oi) / 140000 * 0.25)
        reasons.append("foreign_oi_long")
    wti_pct = _safe_number((macro.get("WTI") or {}).get("change_pct"))
    if wti_pct >= 3.0 and weighted_pct > 0:
        shrink_penalty += 0.12
        reasons.append("oil_inflation")
    sox_pct = _safe_number((macro.get("SOX") or {}).get("change_pct"))
    if sox_pct >= 3.5 and weighted_pct > 0:
        shrink_penalty += 0.10
        reasons.append("sox_overheat")
    vix = _safe_number((macro.get("VIX") or {}).get("close"))
    vix9d = _safe_number((macro.get("VIX9D") or {}).get("close"))
    if vix and vix9d and vix9d / vix > 1.02 and weighted_pct > 0:
        shrink_penalty += 0.10
        reasons.append("vix_backwardation")
    if signal_std is not None and signal_std >= 2.0:
        shrink_penalty += min(0.12, signal_std / 40)
        reasons.append("signal_disagreement")
    shrink = max(0.55, min(1.0, 1.0 - shrink_penalty))
    return weighted_pct * shrink, round(shrink, 3), reasons[:5]


TAIEX_US_BETA_PRIOR = 0.31   # 482 日「全合成」回測(0.70US/0.30夜盤 含 TAIFEX 夜盤)定案:該真實模型下 beta 0.31 的 MAE(0.384%)優於 0.23(0.417%)。US-only 回測曾誤導為 0.23(忽略 0.70 權重+夜盤),含夜盤的真實模型 0.31 較佳。動態 live OLS 暫停(見 _taiex_us_beta);日後改殘差式規格 + 回測才重啟
TAIEX_US_BETA_BOUNDS = (0.15, 0.60)   # 動態 beta 重啟後的夾擠範圍(目前未使用)


def _taiex_us_beta(context: Optional[dict]) -> tuple[float, str]:
    """美股合成訊號 → 加權開盤跳空的縮放係數 k(目前固定回傳回測先驗 0.31)。

    台股日內盤已先消化大部分美股重疊資訊,開盤跳空對前夜美股的真實 beta 偏低。
    482 日「全合成」回測(0.70×us + 0.30×夜盤,含 TAIFEX 夜盤史)顯示:在實際使用的混合模型下,
    beta 0.31 的 MAE(0.384%)優於 0.23(0.417%)——因 0.70 權重已先縮放 US 端,beta 需較高才補足。
    (純 US-only 迴歸曾估得 ~0.23,但那忽略了 0.70 權重與夜盤,屬誤導;以全合成回測為準 → 0.31。)

    ⚠ 動態 live OLS 已停用(2026-06 釘回先驗,經程式碼追蹤 + panel.csv 重現確認):
    舊版用 (us_combo, 原始開盤 gap) 過原點 OLS,擬合目標「未扣夜盤、也未除以 0.70 權重」,
    學到的其實是 US-only beta ≈0.19;直接當成 blend 的美股腿係數,有效 beta 只剩 0.70×0.19≈0.13,
    系統性低估開盤對美股的反應(blend 內正確規格的 beta 重現為 ~0.375,與 0.31 同區)。
    在改以「殘差式」規格(對 (gap−0.30×night) 除以 0.70×us_combo 迴歸)並通過全合成回測前,
    一律回傳回測先驗 0.31,避免 ≥30 樣本後自動向 ~0.19 漂移而劣化。us_beta_samples 仍由
    main() 回填(供日後殘差式動態 beta + 回測使用),此處暫不消費。
    """
    return TAIEX_US_BETA_PRIOR, "回測先驗(0.31;動態暫停)"


def calc_taiex_prediction(taiex_hist: Optional[pd.DataFrame],
                          sox_pct: Optional[float],
                          tsm_pct: Optional[float],
                          night_pct: Optional[float],
                          context: Optional[dict] = None) -> dict:
    """
    Task A: 加權指數開盤預測（美股訊號重縮放 + 夜盤台指期）

    邏輯（2021-2026 回測選定,離線 MAE -73%）：
      us_combo = SOX×1.05×(0.4/0.7) + TSM_ADR×(0.3/0.7)   ← 美股合成訊號
      us_pred  = k × us_combo                              ← k≈0.31(有效 beta,可由 live 樣本動態估)
      最終     = 0.70 × us_pred + 0.30 × 夜盤台指期         ← 夜盤直接定價開盤(beta≈1),不縮放
    任一邊缺失時用另一邊;全缺回 error。
    """
    if taiex_hist is None or taiex_hist.empty:
        return {"error": "缺加權指數歷史"}

    last_close = safe_float(taiex_hist.iloc[-1]["Close"])
    if not last_close:
        return {"error": "加權指數收盤無效"}

    us_beta, us_beta_source = _taiex_us_beta(context)

    # 美股合成訊號(舊 40/30 重正規化)
    us_parts = []
    if sox_pct is not None:
        us_parts.append(("SOX", sox_pct * 1.05, 0.40))
    if tsm_pct is not None:
        us_parts.append(("TSM_ADR", tsm_pct, 0.30))
    us_total_w = sum(w for _, _, w in us_parts)
    us_combo = (sum(v * w for _, v, w in us_parts) / us_total_w) if us_parts else None
    us_pred = us_beta * us_combo if us_combo is not None else None

    if us_pred is None and night_pct is None:
        return {"error": "三大訊號全缺，無法預測"}

    # 合成:夜盤台指期直接定價 09:00 開盤(beta≈1),不可與美股訊號一起縮放
    if us_pred is not None and night_pct is not None:
        raw_weighted_pct = 0.70 * us_pred + 0.30 * night_pct
    elif us_pred is not None:
        raw_weighted_pct = us_pred
    else:
        raw_weighted_pct = night_pct
    weighted_pct = raw_weighted_pct

    # signals 帶「有效權重」(實際乘進 weighted_pct 的係數),供信件表格誠實呈現;
    # 加總 <1 正是「美股訊號縮放」的體現。
    us_leg_w = (0.70 if night_pct is not None else 1.0)
    signals = [(name, val, round(us_leg_w * us_beta * w / us_total_w, 3))
               for name, val, w in us_parts]
    if night_pct is not None:
        signals.append(("Night_TXF", night_pct, 0.30 if us_pred is not None else 1.0))

    # 歷史樣本不足時的暫定參考區間：三訊號發散程度。
    # calibrate_predictions 累積足夠 walk-forward 殘差後，會覆寫成歷史殘差分位區間。
    values = [val for _, val, _ in signals]
    if len(values) >= 2:
        avg = sum(values) / len(values)
        std = (sum((v - avg) ** 2 for v in values) / len(values)) ** 0.5
        weighted_pct, conflict_shrink, conflict_reasons = _taiex_conflict_adjustment(
            weighted_pct, std, context)
        pred_open = last_close * (1 + weighted_pct / 100)
        ci_lower = last_close * (1 + (weighted_pct - std) / 100)
        ci_upper = last_close * (1 + (weighted_pct + std) / 100)
    else:
        weighted_pct, conflict_shrink, conflict_reasons = _taiex_conflict_adjustment(
            weighted_pct, None, context)
        pred_open = last_close * (1 + weighted_pct / 100)
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
        "raw_weighted_pct": round(raw_weighted_pct, 2),
        "weighted_pct": round(weighted_pct, 2),
        "pred_open": round(pred_open, 2),
        "ci_lower": round(ci_lower, 2),
        "ci_upper": round(ci_upper, 2),
        "consensus": consensus,
        "signal_std": round(std, 2) if std is not None else None,
        "conflict_shrink_factor": conflict_shrink,
        "conflict_reasons": conflict_reasons,
        "signal_count": len(signals),
        "us_rescale_k": round(us_beta, 3),
        "us_beta_source": us_beta_source,
        "interval_method": "訊號分歧近似區間（歷史殘差樣本不足）",
    }


def _taiex_us_beta_samples(history: list[dict]) -> list[tuple[float, float]]:
    """從已回填的 live 歷史組出 (美股合成訊號%, 實際開盤跳空%) 配對,供動態 beta 估計。"""
    samples = []
    for h in history or []:
        sox, tsm = h.get("sox_pct"), h.get("tsm_pct")
        op, prev = h.get("actual_open_taiex"), h.get("actual_taiex_prev_close")
        if None in (sox, tsm, op, prev) or not prev:
            continue
        us_combo = (0.40 * sox * 1.05 + 0.30 * tsm) / 0.70
        gap = (op / prev - 1) * 100
        samples.append((us_combo, gap))
    return samples


def _previous_market_values(series: pd.Series, target_index) -> pd.Series:
    """將海外市場序列對齊到每個台股交易日前一個可用值，避免同日 close look-ahead。"""
    out = []
    series = series.sort_index()
    for target_date in target_index:
        prior = series[series.index < target_date]
        out.append(float(prior.iloc[-1]) if len(prior) else float("nan"))
    return pd.Series(out, index=target_index, dtype=float)


def calc_2330_predictions(tsm_close: float, tsm_prev_close: float,
                            usdtwd: float, hist_2330: pd.DataFrame,
                            ex_div_amt: float = 0.0) -> dict:
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
        # 2330 的同日收盤早於 TSM ADR 同日收盤。歷史比值必須使用台股交易日
        # 前一個可用 ADR / FX 值，否則會把尚未發生的美股收盤偷渡進訓練集。
        df = pd.DataFrame({"t2330": t2330_s})
        df["tsm"] = _previous_market_values(tsm_close_s, df.index)
        df["fx"] = _previous_market_values(fx_close_s, df.index)
        df = df.dropna()
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

    # 模型 3：ADR 溢價衰減版（M2,500 日回測選定)
    # 邏輯：ADR 漲跌不會 100% 反映到台股開盤(台股盤後已部分反映 + ADR 收盤到台股開盤有 5 小時)。
    # decay 估計:近 60 個台股交易日全樣本「open_gap% ~ TSM前夜%」OLS 過原點斜率。
    # (2021-2026 回測:OLS 斜率版 MAE 比「|TSM|>1% 比值中位數」版的四模型 ensemble 低 13.6%,
    #  p=4.7e-9、方向命中不變;實測斜率範圍 0.297~0.709,故夾限下緣放寬至 0.25。)
    decay_factor = 0.75  # 樣本不足時的預設值
    model3 = None
    try:
        # target = 台股開盤 / 前一日台股收盤；feature = 前夜 ADR 漲跌。
        # 這與晨報真正要預測的量一致，不再拿 close-to-close 代替 opening gap。
        if "Open" in hist_2330.columns and len(hist_2330) >= 30:
            tw = hist_2330[["Open", "Close"]].dropna().copy()
            tw.index = tw.index.tz_localize(None) if tw.index.tz else tw.index
            tw["open_gap_pct"] = tw["Open"] / tw["Close"].shift(1) - 1
            tsm_returns = tsm_close_s.pct_change().dropna()
            tw["tsm_prev_night_pct"] = _previous_market_values(tsm_returns, tw.index)
            sig = tw[["open_gap_pct", "tsm_prev_night_pct"]].dropna().tail(60)
            if len(sig) >= 30:
                x = sig["tsm_prev_night_pct"]
                y = sig["open_gap_pct"]
                sxx = float((x * x).sum())
                if sxx > 0:
                    decay_factor = float((x * y).sum() / sxx)
                    decay_factor = max(0.25, min(decay_factor, 1.2))  # 限制合理範圍
                    print(f"[calc] 2330 ADR 衰減係數 (近 60 日 OLS 過原點)={decay_factor:.3f}")
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
                # dampening 0.25:5 日累積 5% → 隔日多 1.25%;5d -5% → -1.25%
                # (0.15→0.25:讓 ensemble 在趨勢盤更跟得上,減少對 bias 校正的依賴;
                #  MAE 反比加權仍會在 model4 失準時自動降權,風險可控)
                model4 = last_2330 * (1 + (momentum_5d_pct / 100) * 0.25)
                print(f"[calc] 2330 model4 momentum(5d {momentum_5d_pct:+.2f}%, dampened 0.25) = {model4:.2f}")
    except Exception as e:
        print(f"[calc] 2330 model4 失敗: {e}", file=sys.stderr)

    if ex_div_amt:
        model1 -= ex_div_amt
        model2 = model2 - ex_div_amt if model2 is not None else None
        model3 = model3 - ex_div_amt if model3 is not None else None
        model4 = model4 - ex_div_amt if model4 is not None else None

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
    if ex_div_amt:
        res["ex_div_amt"] = round(ex_div_amt, 4)
    # 四個模型可用就取中位數
    valid = [v for v in [model1, model2, model3, model4] if v]
    if valid:
        res["mid"] = round(sorted(valid)[len(valid) // 2], 2)  # 中位數
        if len(valid) >= 2:
            res["range"] = (round(min(valid), 2), round(max(valid), 2))
    return res


def calc_0050_prediction(last_0050: Optional[float],
                          predictions_2330: dict,
                          taiex_pred: dict,
                          ex_div_amt: float = 0.0,
                          weight_2330_in_0050: float = 0.50,
                          weight_2330_in_taiex: float = 0.30) -> dict:
    """
    0050 (元大台灣 50) 開盤預測。

    模型：0050 大約 50% 是 2330；其餘成分用「加權指數扣掉 2330」近似。
    不可直接混合 2330 + 加權指數，因為加權指數本身已含約 30% 的 2330，會重複曝險。

    任一上游缺失時退化：只用可用那一邊；兩邊都缺 → 回 error。
    失敗 / 缺資料時不影響晨報，回 {"error": ...}。
    """
    if last_0050 is None:
        return {"error": "缺 0050 昨收"}

    # 2330 預測漲跌幅(mid 已是校正後最終值)
    p2_mid = predictions_2330.get("mid") if isinstance(predictions_2330, dict) else None
    p2_last = predictions_2330.get("last_2330") if isinstance(predictions_2330, dict) else None
    pct_2330 = (((p2_mid / p2_last) - 1) * 100) if (p2_mid and p2_last) else None

    # 加權指數預測漲跌幅:優先用「校正後 pred_open」回推(吃到加權的 bias 修正);
    # 否則退回原始 weighted_pct。修正前 bug:0050 只用 weighted_pct → 漏掉加權校正。
    tp_open = (taiex_pred or {}).get("pred_open")
    tp_last = (taiex_pred or {}).get("last_close")
    if tp_open and tp_last:
        pct_taiex = (tp_open / tp_last - 1) * 100
    else:
        pct_taiex = (taiex_pred or {}).get("weighted_pct")

    if pct_2330 is not None and pct_taiex is not None:
        rest_weight = max(0.01, 1.0 - weight_2330_in_taiex)
        pct_taiex_ex_2330 = (
            pct_taiex - weight_2330_in_taiex * pct_2330) / rest_weight
        pct_weighted = (
            weight_2330_in_0050 * pct_2330
            + (1.0 - weight_2330_in_0050) * pct_taiex_ex_2330)
        method = "0050 台積電權重 + 加權指數扣除台積電後的其餘市場"
    elif pct_taiex is not None:
        pct_weighted = pct_taiex
        method = "加權指數（2330 預測缺失）"
    elif pct_2330 is not None:
        pct_weighted = pct_2330
        method = "2330（加權指數預測缺失）"
    else:
        return {"error": "上游 2330 與加權指數預測皆失敗"}

    pred_open = last_0050 * (1 + pct_weighted / 100) - ex_div_amt
    pred_pct = (pred_open / last_0050 - 1) * 100
    result = {
        "last": round(last_0050, 2),
        "pred_open": round(pred_open, 2),
        "pred_pct": round(pred_pct, 3),
        "pct_2330": round(pct_2330, 3) if pct_2330 is not None else None,
        "pct_taiex": round(pct_taiex, 3) if pct_taiex is not None else None,
        "method": method,
    }
    if pct_2330 is not None and pct_taiex is not None:
        result["pct_taiex_ex_2330"] = round(pct_taiex_ex_2330, 3)
    if ex_div_amt:
        result["ex_div_amt"] = round(ex_div_amt, 4)
    return result


def calibrate_0050_bias(tw0050_pred: dict, history: list[dict],
                          min_samples: int = 5, recent_n: int = 20,
                          max_bias: float = 0.03, ewm_span: int = 8) -> dict:
    """
    對 0050 開盤預測做獨立 bias 校正(原本 0050 完全沒校正,殘差最大 +1.77%)。

    0050 雖用「校正後 2330 + 校正後加權」當輸入,但仍有自身結構性殘差
    (折溢價、配息、0.5/0.5 權重近似誤差)。這裡用歷史 pred_0050 vs 實際 0050 開盤
    的 EMA 加權偏誤,在最終 pred_open 上再修一層。

    就地修改並回傳 tw0050_pred(帶 "calibration" 欄位)。失敗不影響晨報。
    """
    if not isinstance(tw0050_pred, dict) or tw0050_pred.get("error"):
        return tw0050_pred
    if not history or len(history) < 2:
        tw0050_pred.setdefault("calibration", {"applied": False, "reason": "歷史樣本不足"})
        return tw0050_pred
    try:
        opens = _fetch_open_map("0050.TW")
    except Exception as e:
        tw0050_pred.setdefault("calibration", {"applied": False, "reason": f"無法取得 0050 開盤:{e}"})
        return tw0050_pred

    errs: list = []
    today = dt.datetime.now(TPE).strftime("%Y-%m-%d")
    for open_date, h in _resolved_prediction_history(history, opens, before_date=today):
        if h.get("ex_div_today"):
            continue
        a = opens.get(open_date)
        p = h.get("pred_0050")
        if p and a:
            errs.append((a - p) / p)

    bias, n = _ewm_bias(errs, recent_n, ewm_span)
    if n < min_samples:
        tw0050_pred["calibration"] = {"applied": False, "samples": n,
                                       "reason": f"0050 誤差樣本僅 {n} 筆(需 ≥ {min_samples})"}
        return tw0050_pred
    raw = tw0050_pred.get("pred_open")
    if raw is None:
        tw0050_pred["calibration"] = {"applied": False, "samples": n, "reason": "0050 無原始預測"}
        return tw0050_pred
    b = max(-max_bias, min(bias, max_bias))
    tw0050_pred["pred_open_raw"] = raw
    tw0050_pred["pred_open"] = round(raw * (1 + b), 2)
    last = tw0050_pred.get("last")
    if last:
        tw0050_pred["pred_pct"] = round((tw0050_pred["pred_open"] / last - 1) * 100, 3)
    tw0050_pred["calibration"] = {"applied": True, "bias_pct": round(b * 100, 3),
                                   "samples": n, "raw": raw}
    print(f"[calib] 0050 bias 修正 {b*100:+.3f}%(EMA,{n} 樣本):{raw} → {tw0050_pred['pred_open']}")
    return tw0050_pred


def calc_portfolio_actual(portfolio: dict, closes_map: dict) -> dict:
    """
    計算單一倉位「昨日帳上漲跌」%與金額(用 前天收盤 vs 昨天收盤,非預測)。

    portfolio:   {code: shares(股)}   # 單位為股數
    closes_map:  {code: (前天收盤, 昨天收盤)}(TWSE 官方,避開 Yahoo 對 ETF 落後)

    倉位昨日漲跌 = Σ(昨天市值 − 前天市值) / Σ前天市值;金額 = Σ(股×(昨−前))。
    回傳 {gain_pct, gain_amount, prev_value, last_value, n_holdings, n_priced} 或 {}。
    隱私:回傳只有彙總值,**無任何個股代號或股數**。
    """
    if not portfolio:
        return {}
    total_prev = 0.0
    total_last = 0.0
    n_priced = 0
    for code, shares in portfolio.items():
        pair = closes_map.get(code)
        if not pair:
            continue
        prev, last = pair
        if not prev or not last or prev <= 0:
            continue
        total_prev += shares * prev
        total_last += shares * last
        n_priced += 1
    if total_prev <= 0:
        return {}
    gain_amount = total_last - total_prev
    return {
        "gain_pct": round(gain_amount / total_prev * 100, 2),
        "gain_amount": round(gain_amount, 0),
        "prev_value": round(total_prev, 0),
        "last_value": round(total_last, 0),
        "n_holdings": len(portfolio),
        "n_priced": n_priced,
    }


def detect_ex_dividend_today(codes: list, today_tpe_date) -> dict:
    """
    偵測今日是否為某台股標的的除息日(best-effort,用 yfinance 配息 ex-date)。

    codes:  台股代號 list(自動補 .TW)。
    回傳 {code: 每股配息金額} —— 只含「今日除息」者;查無 / 失敗則不列入。

    用途:
      - 公開預測卡(2330/0050/00662):除息日實際開盤會少掉配息 → 預測開盤要還原(減息)
      - 個人持倉:除息日股價跌≈配息,但持有人領到現金 → 財富約持平,漲幅%不調整,只標註
    """
    out: dict = {}
    for code in codes:
        try:
            tkr = code if (code.endswith(".TW") or code.isalpha()) else f"{code}.TW"
            divs = yf.Ticker(tkr).dividends
            if divs is None or len(divs) == 0:
                continue
            for ex_ts, amt in divs.items():
                try:
                    d = ex_ts.date()
                except AttributeError:
                    continue
                if d == today_tpe_date and amt and float(amt) > 0:
                    out[code] = round(float(amt), 4)
                    break
        except Exception:
            continue
    return out


def _ewma_vol_pct(returns, lam: float = 0.94) -> Optional[float]:
    """RiskMetrics EWMA 日波動度(%):σ²_t = λ·σ²_{t-1} + (1−λ)·r²_{t-1},λ=0.94。

    近期報酬權重指數遞減 → 波動叢聚:恐慌期 σ 自動變寬、平靜期收窄,
    比「近 20 日等權 std」更能反映當前 regime。借鏡 GARCH 思路但零相依、不會擬合失敗。
    報酬筆數 < 10 或結果非正/NaN → 回 None(由呼叫端退回固定 σ)。
    """
    arr = np.asarray([x for x in returns if x is not None and x == x], dtype=float)
    if arr.size < 10:
        return None
    var = float(np.var(arr))                      # 以全期變異數(ddof=0)起始(夠多步後會被洗掉)
    for r2 in arr * arr:
        var = lam * var + (1.0 - lam) * float(r2)
    if not (var > 0) or var != var:               # 非正 / NaN
        return None
    return float(np.sqrt(var)) * 100.0


def calc_momentum_metrics(close_series) -> dict:
    """
    從 close 序列計算動能 / 波動度 / 移動平均指標。

    回傳:
      last, pct_5d, pct_20d, ma20, ma50, ma20_dist_pct, ma50_dist_pct,
      daily_vol_pct (近 20 日 daily-return std)、ewma_vol_pct (RiskMetrics EWMA 條件波動度)

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
        rets_all = s.pct_change().dropna()
        rets = rets_all.tail(20)
        if len(rets):
            out["daily_vol_pct"] = round(float(rets.std()) * 100, 3)   # 近 20 日等權(其他消費者沿用)
        ewma = _ewma_vol_pct(rets_all.to_numpy())                       # 全序列 EWMA 條件波動度
        if ewma is not None:
            out["ewma_vol_pct"] = round(ewma, 3)
    return out


def calc_midterm_forecast(metrics: dict,
                          horizons: tuple = (5, 20)) -> dict:
    """
    根據動能指標生成中期 range forecast。

    **重要：這不是「點預測」**——學界共識:多日點預測精度與隨機漫步相近。
    本 forecast 提供的是「**基於歷史波動度的合理區間**」(±1.5σ × √horizon),
    + 一個保守的 drift 估計(過去 20 日平均日收益,長期 horizon 加均值回歸 dampening)。

    波動度優先用 EWMA 條件波動度(ewma_vol_pct,反映當前 regime:恐慌期變寬、
    平靜期收窄),取不到才退回近 20 日等權 std(daily_vol_pct)。

    解讀方式:「下週 2330 在常態近似下約 87% 機率落在 lower-upper」,
    而非「下週 2330 會漲到 X」。
    """
    last = metrics.get("last")
    ewma_vol = metrics.get("ewma_vol_pct")
    daily_vol = ewma_vol if (ewma_vol and ewma_vol > 0) else metrics.get("daily_vol_pct")
    vol_basis = "EWMA" if (ewma_vol and ewma_vol > 0) else "20d-std"
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
            "vol_basis": vol_basis,        # EWMA 或 20d-std(供 debug,不渲染)
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


def _process_feed_item(w: dict, cutoff: dt.datetime) -> list[dict]:
    """處理單一 feed 工作項 → 該 feed 的 news 清單。本體逐字沿用舊 fetch_news 兩迴圈,
    行為不變;抽出以便依 host 分組平行(P0-1)。同 host 由單一執行緒序列處理,
    故 _FEED_STATS/斷路器/RSS 快取天生執行緒安全、無需鎖。"""
    source, url, kind = w["source"], w["url"], w["kind"]
    out: list[dict] = []
    try:
        if kind == "cnyes_json":       # 鉅亨美股 JSON 特例
            r = _http_get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                payload = r.json() or {}
                items_obj = payload.get("items") or {}
                data = items_obj.get("data") if isinstance(items_obj, dict) else None
                if not isinstance(data, list):
                    data = []
                for d in data[:10]:
                    if not isinstance(d, dict):
                        continue
                    out.append({
                        "source": source,
                        "title": d.get("title", ""),
                        "summary": (d.get("summary") or "")[:800],
                        "link": f"https://news.cnyes.com/news/id/{d.get('newsId')}",
                        "published": d.get("publishAt", ""),
                    })
            return out
        if kind == "company":          # 重點公司 Google News 查詢(補個股新聞)
            feed = _feedparser_parse_url_with_timeout(url)
            label = w["label"]
            kept = 0
            for entry in feed.entries:
                if kept >= 6:
                    break
                pub_dt = _entry_published_dt(entry)
                if pub_dt and pub_dt < cutoff:
                    continue
                source_name, source_url = _tw_entry_source(entry)
                out.append({
                    "source": f"Google:{label}",
                    "title": entry.get("title", ""),
                    "summary": (entry.get("summary", "") or "")[:800],
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "company_label": label,
                    "source_name": source_name,
                    "source_url": source_url,
                })
                kept += 1
            return out
        # kind == "rss"
        feed = _feedparser_parse_url_with_timeout(url)
        # 類股與世界大事來源都要求有發布時間:這兩類直接餵專屬 prompt 段,
        # 無日期的舊聞混進「昨日」會誤導(一般來源仍容忍缺日期,僅標記 date_missing)。
        _src_s = str(source)
        world_cat = (_src_s[3:] if _src_s.startswith("世界-")
                     else (_src_s if _src_s == "中央社國際" else ""))
        requires_date = (bool(_other_sector_label_from_source(_src_s)) or bool(world_cat))
        for entry in feed.entries[:10]:
            source_name, source_url = _tw_entry_source(entry)
            pub_dt = _entry_published_dt(entry)
            if pub_dt and pub_dt < cutoff:
                continue
            if requires_date and pub_dt is None:
                continue
            item = {
                "source": source,
                "title": entry.get("title", ""),
                "summary": (entry.get("summary", "") or "")[:800],
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "source_name": source_name,
                "source_url": source_url,
            }
            if world_cat:
                item["world_cat"] = world_cat
            out.append(_mark_news_date_quality(item, pub_dt))
        return out
    except Exception as e:
        print(f"[news] {source} 抓取失敗：{e}", file=sys.stderr)
        return out


def fetch_news() -> list[dict]:
    """抓 RSS 摘要,回最近 30 小時內的新聞(涵蓋跨日凌晨的 Fed/美股盤後)。

    P0-1:依 host 分組平行抓取——不同 host 平行(消除 2026-07-08 的序列瓶頸),
    同 host 序列(讓 per-host 斷路器仍能 fail-fast、且天生執行緒安全)。
    NEWS_FETCH_WORKERS=1 退回序列(行為與平行化前完全相同,為安全逃生門)。"""
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urlparse
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=30)
    # 組工作清單(保留原順序:RSS_FEEDS 先、公司查詢後),各項帶 idx 供重組回原序
    work: list[dict] = []
    for source, url in RSS_FEEDS.items():
        kind = "cnyes_json" if url.endswith("&page=1") else "rss"
        work.append({"idx": len(work), "source": source, "url": url, "kind": kind})
    for query, label in GOOGLE_NEWS_COMPANIES:
        work.append({"idx": len(work), "source": f"Google:{label}",
                     "url": _gnews_rss(query, when="2d"), "kind": "company", "label": label})
    merged: dict[int, list[dict]] = {}
    if NEWS_FETCH_WORKERS <= 1:
        # 逃生門:完全退回舊序列行為——依「原始 work 順序」逐項處理(非 host 分組順序),
        # 送出請求的順序與平行化前逐項相同(Codex review:分組後的序列會把 Google 擠成一團,
        # 不等於舊行為)。
        for w in work:
            merged[w["idx"]] = _process_feed_item(w, cutoff)
    else:
        # 依 host 分組:不同 host 平行、同 host 序列(見 docstring)
        groups: dict[str, list[dict]] = {}
        for w in work:
            host = urlparse(w["url"]).netloc or str(w["url"])
            groups.setdefault(host, []).append(w)

        def _run_group(items_in_group: list[dict]) -> dict:
            return {w["idx"]: _process_feed_item(w, cutoff) for w in items_in_group}

        workers = max(1, min(NEWS_FETCH_WORKERS, len(groups)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for part in ex.map(_run_group, list(groups.values())):
                merged.update(part)

    items: list[dict] = []
    for w in work:                     # 依原始工作順序重組,輸出穩定
        items.extend(merged.get(w["idx"], []))
    company_hit = sum(len(merged.get(w["idx"], [])) for w in work if w["kind"] == "company")
    print(f"[news] 共 {len(items)} 則(含 {company_hit} 則重點公司 Google News)")
    for item in items:
        if "date_missing" not in item:
            _mark_news_date_quality(item, _parse_news_time_required(item.get("published")))
    return items


def fetch_candidate_company_news(snapshot: list[dict],
                                 top_n: int = 20,
                                 per_query: int = 3,
                                 exclude_codes: Optional[set] = None) -> list[dict]:
    """
    對「爆發力分數前 N 檔候選股」用 Google News 查各自最新新聞,並 tag company_label=code。

    為什麼:五檔候選常是 10 名外的中型股(緯創/群創/南亞科…),固定 12 檔權值股查詢
    抓不到它們的自家催化 → news_catalyst_score 多為 0。針對「正在被預測的候選」動態查新聞,
    讓催化分數與排名/股價預測都吃得到個股消息面。

    tag company_label=code → extract_structured_events 會把 entity 設為該 code → 直接歸因。
    回傳已 tag 的 news 清單(失敗個股略過)。
    """
    if not snapshot:
        return []
    exclude = {str(c) for c in (exclude_codes or set())}
    ranked = sorted(snapshot,
                    key=lambda s: (s.get("breakout") or {}).get("score", 0),
                    reverse=True)
    picks = [s for s in ranked
             if s.get("code") and (s.get("breakout") or {}).get("score", 0) > 0][:top_n]
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=54)
    items: list[dict] = []
    hit = 0
    queried = 0
    for s in picks:
        code = str(s.get("code"))
        name = str(s.get("name") or "")
        if code in exclude:        # 固定 12 檔已在 fetch_news 查過,不重複
            continue
        query = f"{name} {code}" if name else code
        queried += 1
        try:
            feed = _feedparser_parse_url_with_timeout(_gnews_rss(query, when="2d"))
            for entry in feed.entries[:per_query]:
                pub_dt = _entry_published_dt(entry)
                if pub_dt and pub_dt < cutoff:
                    continue
                # source_name/source_url 比照正規 Google 路徑抽取:G6 的「獨立來源數」
                # 以 source_name 辨識發布者,缺了會把同查詢下不同媒體都當同一來源(Codex review)
                source_name, source_url = _tw_entry_source(entry)
                item = {
                    "source": f"Google:{code}",
                    "title": entry.get("title", ""),
                    "summary": (entry.get("summary", "") or "")[:800],
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "company_label": code,
                    "code": code,
                    "source_name": source_name,
                    "source_url": source_url,
                }
                items.append(_mark_news_date_quality(item, pub_dt))
                hit += 1
        except Exception as e:
            print(f"[cand_news] 候選 {code} 查詢失敗: {e}", file=sys.stderr)
    print(f"[cand_news] 候選個股新聞 {hit} 則(查詢 {queried} 檔爆發力候選)")
    return items


# 已由固定重點清單 / 爆發力候選 / 8-K 充分覆蓋的「電子科技」產業;動態非科技公司池排除之,
# 避免重複查詢並把查詢額度留給真正缺乏個股新聞的非科技類股。
_TECH_INDUSTRIES_FOR_SECTOR_NEWS: set[str] = {
    "半導體業", "電腦及週邊設備業", "光電業", "通信網路業", "電子零組件業",
    "電子通路業", "資訊服務業", "其他電子業", "數位雲端",
}


def fetch_sector_leader_news(sector_heat: dict,
                             exclude_codes: Optional[set] = None,
                             leaders_per_sector: int = 2,
                             per_query: int = 2,
                             max_queries: int = 10) -> list[dict]:
    """對「今日成交熱度高的非科技類股」的領先個股補查 Google News,tag company_label=code。

    為什麼:固定重點清單多為科技股,fetch_candidate_company_news 又只查爆發力排序前段
    (多為電子股),所以金融/傳產/航運/生技以外的個股催化長期抓不到。本函式依 SECTOR_HEAT
    由「當日最熱的非科技類股」挑成交值領先股(排除已被固定清單/候選查過者),補其自家新聞。
    與候選機制同一路徑(擴充新聞「輸入」,不改任何計分係數)。任何個股失敗略過(晨報不可斷)。
    """
    sectors = (sector_heat or {}).get("sectors") or {}
    ranked = (sector_heat or {}).get("ranked") or []
    if not sectors or not ranked:
        return []
    exclude = {str(c) for c in (exclude_codes or set())}
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=54)
    items: list[dict] = []
    queried = hit = 0
    for sector in ranked:
        if queried >= max_queries:
            break
        if sector in _TECH_INDUSTRIES_FOR_SECTOR_NEWS:
            continue
        picked = 0
        for m in (sectors[sector].get("leaders") or []):
            if picked >= leaders_per_sector or queried >= max_queries:
                break
            code = str(m.get("code") or "")
            name = str(m.get("name") or "")
            if not code or code in exclude:
                continue
            exclude.add(code)      # 同一次執行不重複查同一檔
            picked += 1
            queried += 1
            query = f"{name} {code}" if name else code
            try:
                feed = _feedparser_parse_url_with_timeout(_gnews_rss(query, when="2d"))
                for entry in feed.entries[:per_query]:
                    pub_dt = _entry_published_dt(entry)
                    if pub_dt and pub_dt < cutoff:
                        continue
                    source_name, source_url = _tw_entry_source(entry)   # G6:發布者身分(同上)
                    item = {
                        "source": f"Google:{code}",
                        "title": entry.get("title", ""),
                        "summary": (entry.get("summary", "") or "")[:800],
                        "link": entry.get("link", ""),
                        "published": entry.get("published", ""),
                        "company_label": code,
                        "code": code,
                        "sector": sector,
                        "source_name": source_name,
                        "source_url": source_url,
                    }
                    items.append(_mark_news_date_quality(item, pub_dt))
                    hit += 1
            except Exception as e:
                print(f"[sector_news] {sector} {code} 查詢失敗: {e}", file=sys.stderr)
    print(f"[sector_news] 非科技類股領先股新聞 {hit} 則(查詢 {queried} 檔,涵蓋熱門非科技類股)")
    return items


# 8-K 動態查詢用 ticker → 查詢字串(中英並列收斂歧義;短 ticker 如 ON/MU/ARM
# 直接查會撈到大量無關結果)。不在表內的 ticker 用「{t} stock」退化查詢。
_8K_QUERY_BY_TICKER: dict[str, str] = {
    "QCOM": "高通 Qualcomm", "MRVL": "邁威爾 Marvell", "AMAT": "應用材料 Applied Materials",
    "LRCX": "科林研發 Lam Research", "KLAC": "科磊 KLA", "MU": "美光 Micron",
    "TXN": "德州儀器 Texas Instruments", "ADI": "亞德諾 Analog Devices",
    "NXPI": "恩智浦 NXP", "MCHP": "微芯 Microchip", "ON": "安森美 onsemi",
    "SNPS": "新思 Synopsys", "CDNS": "益華 Cadence", "ARM": "安謀 Arm",
    "SMCI": "美超微 Supermicro", "GOOG": "Alphabet Google",
}


def fetch_8k_company_news(sec_filings: list[dict],
                          exclude_labels: Optional[set] = None,
                          per_query: int = 3,
                          max_tickers: int = 8) -> list[dict]:
    """對「今日有 8-K 的重點科技股」動態查 Google News,tag company_label=ticker。

    動態宇宙的美股側:固定清單外的公司(TXN/ON/MRVL/AMAT…)平常不查新聞,
    但它一旦發 8-K 就是當日重點 — 此時自動把它加進新聞宇宙,
    讓「科技板塊脈動」吃得到 8-K 背後的脈絡報導,而非只有表單編號。
    (註:TSMC/ASML 等外國發行人申報 6-K 非 8-K,不會出現在此路徑。)
    """
    if not sec_filings:
        return []
    exclude = {str(t).upper() for t in (exclude_labels or set())}
    tickers: list[str] = []
    for f in sec_filings:
        t = str(f.get("ticker") or "").upper()
        if t and t not in exclude and t not in tickers and t in SEC_PRIORITY_TICKERS:
            tickers.append(t)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=54)
    items: list[dict] = []
    hit = 0
    for t in tickers[:max_tickers]:
        try:
            query = _8K_QUERY_BY_TICKER.get(t, f"{t} stock")
            feed = _feedparser_parse_url_with_timeout(_gnews_rss(query, when="2d"))
            for entry in feed.entries[:per_query]:
                pub_dt = _entry_published_dt(entry)
                if pub_dt and pub_dt < cutoff:
                    continue
                source_name, source_url = _tw_entry_source(entry)   # G6:發布者身分(同上)
                item = {
                    "source": f"Google:{t}",
                    "title": entry.get("title", ""),
                    "summary": (entry.get("summary", "") or "")[:800],
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "company_label": t,
                    "source_name": source_name,
                    "source_url": source_url,
                }
                items.append(_mark_news_date_quality(item, pub_dt))
                hit += 1
        except Exception as e:
            print(f"[8k_news] {t} 查詢失敗: {e}", file=sys.stderr)
    print(f"[8k_news] 8-K 公司新聞 {hit} 則(動態查 {len(tickers[:max_tickers])} 檔)")
    return items


TW_INTELLIGENCE_QUERIES = {
    "policy": (
        "台灣 政策 行政院 補助 津貼 房貸 社福 產業 site:gov.tw",
        "台灣 政策 行政院 立法院 金管會 內政部 勞動部 經濟部",
        "台灣 政策 金管會 央行 內政部 房市 信用管制 site:gov.tw",
        "台灣 政策 勞動部 勞保 基本工資 就業 補助 site:gov.tw",
        "台灣 政策 經濟部 能源 電價 產業 補助 site:gov.tw",
        "台灣 政策 教育部 托育 育兒 少子化 補助 site:gov.tw",
        "台灣 新青安 育兒津貼 長照 電價 租屋 補助 政策",
        "台灣 新青安 房貸 鬆綁 信用管制 青年安心成家",
        "台灣 少子化 育兒津貼 托育補助 長照 社福 政策",
        "台灣 政策 修法 草案 預告 上路 補貼 近月",
        # 房貸利率追蹤(2026-07-15 使用者拍板;央行數值端點憑證/nid 未驗 → 新聞式,
        # 央行決議/銀行調整=可行動訊號;實測 50 則)+ 托育/教育政策(實測 19 則)
        "房貸利率 OR 五大銀行 房貸 OR 央行 理監事",
        "托育補助 OR 育兒津貼 OR 公幼 OR 幼兒園 補助",
    ),
    "medical": (
        # 通用事件查詢(原本 3 條中榮專屬查詢使同一事件天天洗版 → 改廣);
        # OR 語法經實測召回較佳(Google News 把多關鍵字當 AND)
        "醫院 裁罰 OR 停約 OR 處分",
        "醫療糾紛 OR 醫療疏失 OR 醫療事故",
        "缺藥 OR 藥品短缺 OR 藥價調整",
        "醫院 疫情 OR 群聚感染 OR 院內感染",
        "醫師 罷工 OR 出走 OR 人力荒",
        "健保署 OR 衛福部 重大 OR 改革",
        "台灣 醫療 醫院 衛福部 健保署 疾管署 食藥署 site:gov.tw",
        "台灣 醫院 暫停 門診 住院 急診 醫療 人力 病安",
    ),
}

# 政策區「財經相關」白名單:召回必須命中其一,否則一律剔除
# (使用者回饋:宗教宣導/毒駕修法/性平等與投資無關的政策造成版面雜亂)。

TW_OFFICIAL_SOURCE_TOKENS = (
    "gov.tw", "行政院", "衛福部", "健保署", "疾管署", "食藥署",
    "金管會", "內政部", "勞動部", "經濟部", "財政部", "中央銀行",
    "立法院", "衛生局", "醫院公告",
)
TW_OFFICIAL_SOURCE_DOMAINS = (
    "gov.tw", "ey.gov.tw", "mohw.gov.tw", "nhi.gov.tw", "cdc.gov.tw",
    "hpa.gov.tw", "fda.gov.tw", "sfaa.gov.tw", "mol.gov.tw", "moi.gov.tw",
    "moe.gov.tw", "moea.gov.tw", "ndc.gov.tw", "fsc.gov.tw", "cbc.gov.tw",
    "ly.gov.tw", "vghtpe.gov.tw", "vghtc.gov.tw", "vghks.gov.tw",
    "ntuh.gov.tw", "nckuh.hosp.ncku.edu.tw", "tpech.gov.taipei",
    "cgmh.org.tw", "cmuh.cmu.edu.tw", "kmuh.org.tw",
)

# \u91ab\u754c\u300c\u6a5f\u69cb\u9375\u300d:per-entity \u6d17\u7248\u4e0a\u9650\u5c08\u7528(\u540c\u4e00\u6a5f\u69cb\u6bcf\u5929\u6700\u591a 1 \u689d)\u3002
# \u8207 timeline entity \u5206\u958b:timeline \u7528\u4e3b\u984c\u8a5e\u5229\u65bc policy \u805a\u5408,
# \u6d17\u7248\u4e0a\u9650\u5fc5\u9808\u8a8d\u300c\u6a5f\u69cb\u300d,\u5426\u5247\u4e2d\u69ae\u4e8b\u4ef6\u7684\u591a\u89d2\u5ea6\u5831\u5c0e\u6703\u5404\u62ff\u4e0d\u540c key\u3002
TW_MEDICAL_ORG_TERMS = (
    "\u53f0\u4e2d\u69ae\u7e3d", "\u81fa\u4e2d\u69ae\u7e3d", "\u4e2d\u69ae", "\u5317\u69ae", "\u9ad8\u69ae", "\u53f0\u5927\u91ab\u9662", "\u81fa\u5927\u91ab\u9662",
    "\u9577\u5e9a", "\u99ac\u5055", "\u5947\u7f8e", "\u5f70\u57fa", "\u4e2d\u570b\u9644\u91ab", "\u65b0\u5149\u91ab\u9662", "\u570b\u6cf0\u91ab\u9662",
    "\u885b\u798f\u90e8", "\u5065\u4fdd\u7f72", "\u75be\u7ba1\u7f72", "\u98df\u85e5\u7f72",
)


# \u91ab\u9662\u5225\u540d \u2192 \u6b63\u540d\u9375:\u5168\u540d/\u7c21\u7a31\u90fd\u6536\u6582\u5230\u540c\u4e00\u9375,\u300c\u6bcf\u65e5\u4e00\u6a5f\u69cb\u300dcap \u624d\u64cb\u5f97\u4f4f\u540c\u9662\u591a\u5831\u5c0e
# (\u300c\u5f70\u5316\u57fa\u7763\u6559\u91ab\u9662\u300d\u4e0d\u542b\u300c\u5f70\u57fa\u300d\u5b50\u5b57\u4e32;\u4e2d\u570b\u91ab\u56db\u7a2e\u5beb\u6cd5\u5404\u81ea\u6210 key \u6703\u7e5e\u904e cap\u2014\u2014Codex review)\u3002
_TW_MEDICAL_ORG_ALIASES: dict[str, str] = {
    "\u5f70\u5316\u57fa\u7763\u6559\u91ab\u9662": "\u5f70\u57fa", "\u5f70\u57fa": "\u5f70\u57fa",
    "\u4e2d\u570b\u91ab\u85e5\u5927\u5b78\u9644\u8a2d\u91ab\u9662": "\u4e2d\u570b\u9644\u91ab", "\u4e2d\u570b\u91ab\u85e5\u5927\u5b78": "\u4e2d\u570b\u9644\u91ab",
    "\u4e2d\u91ab\u5927\u9644\u91ab": "\u4e2d\u570b\u9644\u91ab", "\u4e2d\u570b\u9644\u91ab": "\u4e2d\u570b\u9644\u91ab",
}


def _tw_medical_org_key(title: str) -> str:
    text = str(title or "")
    # \u5148\u6bd4\u5225\u540d\u8868(\u9577\u5b57\u4e32\u512a\u5148,\u5168\u540d\u5148\u65bc\u7c21\u7a31),\u518d\u9000\u56de\u4e00\u822c\u6a5f\u69cb\u8a5e
    for alias in sorted(_TW_MEDICAL_ORG_ALIASES, key=len, reverse=True):
        if alias in text:
            return _TW_MEDICAL_ORG_ALIASES[alias]
    for term in TW_MEDICAL_ORG_TERMS:
        if term in text:
            # \u4e2d\u69ae\u7684\u5404\u7a2e\u5beb\u6cd5\u7d71\u4e00\u6210\u540c\u4e00\u9375
            return "\u4e2d\u69ae" if "\u69ae\u7e3d" in term or term == "\u4e2d\u69ae" else term
    return ""
TW_INTELLIGENCE_DIRECT_SOURCES = {
    "policy": (
        {"name": "EY News", "url": "https://www.ey.gov.tw/RSS_Content.aspx?ModuleType=1",
         "html_url": "https://www.ey.gov.tw/Page/6485009ABEC1CB9C"},
        {"name": "EY Ministries", "url": "https://www.ey.gov.tw/RSS_Content.aspx?ModuleType=3",
         "html_url": "https://www.ey.gov.tw/Page/B31C61707D4FEEEF"},
        {"name": "MOHW News", "url": "https://www.mohw.gov.tw/rss-16-1.html",
         "html_url": "https://www.mohw.gov.tw/www/lp-16-1.html"},
        {"name": "NHI Regulations", "url": "https://www.nhi.gov.tw/ch/rss-3258-1.html",
         "html_url": "https://www.nhi.gov.tw/ch/lp-3258-1.html"},
        {"name": "FSC News", "url": "https://www.fsc.gov.tw/ch/home.jsp?id=2&parentpath=0",
         "html_url": "https://www.fsc.gov.tw/ch/home.jsp?id=2&parentpath=0"},
        {"name": "CBC News", "url": "https://www.cbc.gov.tw/tw/lp-302-1.html",
         "html_url": "https://www.cbc.gov.tw/tw/lp-302-1.html"},
        {"name": "MOI News", "url": "https://www.moi.gov.tw/News.aspx?n=4",
         "html_url": "https://www.moi.gov.tw/News.aspx?n=4"},
        {"name": "MOL News", "url": "https://www.mol.gov.tw/1607/1632/1633/",
         "html_url": "https://www.mol.gov.tw/1607/1632/1633/"},
        {"name": "MOEA News", "url": "https://www.moea.gov.tw/Mns/populace/news/News.aspx?kind=1",
         "html_url": "https://www.moea.gov.tw/Mns/populace/news/News.aspx?kind=1"},
    ),
    "medical": (
        {"name": "MOHW News", "url": "https://www.mohw.gov.tw/rss-16-1.html",
         "html_url": "https://www.mohw.gov.tw/www/lp-16-1.html"},
        {"name": "MOHW Notices", "url": "https://www.mohw.gov.tw/rss-18-1.html",
         "html_url": "https://www.mohw.gov.tw/www/lp-18-1.html"},
        {"name": "NHI Regulations", "url": "https://www.nhi.gov.tw/ch/rss-3258-1.html",
         "html_url": "https://www.nhi.gov.tw/ch/lp-3258-1.html"},
        {"name": "CDC News", "url": "https://www.cdc.gov.tw/RSS/RssXml/Hh094B49-DRwe2RR4eFQFA",
         "html_url": "https://www.cdc.gov.tw/Category/ListContent/EmXW9Z9G5lXnKcSMacP7Mw"},
        # G8 探活(2026-07-14):TFDA 有真 RSS(rssNews/rssAnnouncement .ashx 皆 200、
        # 含日期與標題)→ 升級為主路徑,原 HTML 頁降為退化備援;另補「本署公告」
        # (法規預告/下架/回收,對醫師讀者高相關)。健保署 rss 403 bot-block、
        # 衛福部憑證缺 SKI 對 requests 驗證失敗 → 維持既有條目靠 HTML 退化,不新增。
        # org_key:兩條 TFDA feed 共用「食藥署」機構鍵——公告標題常不含機關名,
        # 僅靠標題的每日一機構 cap 會漏,靠此鍵補上(Codex review)。
        {"name": "FDA News", "url": "https://www.fda.gov.tw/TC/rssNews.ashx",
         "html_url": "https://www.fda.gov.tw/TC/news.aspx?cid=4", "org_key": "食藥署"},
        {"name": "FDA Announcements", "url": "https://www.fda.gov.tw/TC/rssAnnouncement.ashx",
         "html_url": "https://www.fda.gov.tw/TC/news.aspx?cid=5", "org_key": "食藥署"},
        {"name": "VGHTC News", "url": "https://www.vghtc.gov.tw/News.aspx?n=56",
         "html_url": "https://www.vghtc.gov.tw/News.aspx?n=56"},
        {"name": "NTUH News", "url": "https://www.ntuh.gov.tw/News.aspx?n=2576",
         "html_url": "https://www.ntuh.gov.tw/News.aspx?n=2576"},
    ),
}

TW_INTELLIGENCE_GOOGLE_ENTRY_LIMIT = {"policy": 36, "medical": 24}
TW_INTELLIGENCE_OFFICIAL_ENTRY_LIMIT = {"policy": 28, "medical": 24}


# 醫界「重大事件」詞:真正值得進晨報的硬新聞(裁罰、停約、糾紛、缺藥、疫情爆發…)。
# 醫界區只召回標題含這類事件詞的新聞,藉此擋掉例行公告(空床數、招考、義診、衛教)。
# 醫界「例行/行政/衛教」雜訊:住院數、招考、義診、衛教、免費篩檢等,不進晨報。
# 這類即使來自官方、含「公告」,也不是投資人需要的醫界大事。


def _tw_intelligence_window(now_tpe: dt.datetime) -> tuple[dt.datetime, dt.datetime, str]:
    """Use yesterday, with a weekend catch-up window for Monday reports."""
    local_now = now_tpe.astimezone(TPE)
    end = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    lookback_days = 2 if local_now.weekday() == 0 else 1
    start = end - dt.timedelta(days=lookback_days)
    label = f"{start:%Y-%m-%d} 至 {(end - dt.timedelta(seconds=1)):%Y-%m-%d}"
    return start, end, label


def _tw_policy_intelligence_window(now_tpe: dt.datetime) -> tuple[dt.datetime, dt.datetime, str]:
    """Track still-developing Taiwan policy items for the past month."""
    local_now = now_tpe.astimezone(TPE)
    end = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - dt.timedelta(days=30)
    label = f"{start:%Y-%m-%d} 至 {(end - dt.timedelta(seconds=1)):%Y-%m-%d}"
    return start, end, label


def _tw_intelligence_status(text: str) -> str:
    if any(token in text for token in ("公告", "核定", "通過", "上路", "生效", "發布")):
        return "已公告"
    if any(token in text for token in ("研議", "擬", "規劃", "預告", "將推", "草案")):
        return "研議中"
    return "媒體報導"


def _host_from_url(url: str) -> str:
    try:
        parsed = urlparse(str(url or ""))
        host = (parsed.netloc or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _extract_google_news_target(link: str) -> str:
    """Return embedded publisher URL when Google News exposes one, else blank."""
    try:
        parsed = urlparse(str(link or ""))
        if "news.google." not in (parsed.netloc or "").lower():
            return ""
        query = parse_qs(parsed.query or "")
        for key in ("url", "u"):
            values = query.get(key) or []
            if values:
                return values[0]
    except Exception:
        return ""
    return ""


def _tw_source_is_official(link: str,
                           source_url: str = "",
                           source_name: str = "") -> bool:
    """Only publisher/agency domains count as official; title mentions do not."""
    del source_name  # kept for call-site readability and future source allowlists
    candidates = [link, source_url, _extract_google_news_target(link)]
    for candidate in candidates:
        host = _host_from_url(candidate)
        if any(host == domain or host.endswith(f".{domain}")
               for domain in TW_OFFICIAL_SOURCE_DOMAINS):
            return True
    return False


def _tw_mentions_official_agency(text: str) -> bool:
    return any(token.lower() in str(text or "").lower()
               for token in TW_OFFICIAL_SOURCE_TOKENS)


def _tw_entry_source(entry: dict) -> tuple[str, str]:
    source = entry.get("source") or {}
    if isinstance(source, dict):
        return str(source.get("title") or ""), str(source.get("href") or "")
    return str(source or ""), ""


def _parse_tw_roc_date(value: str, default_year: Optional[int] = None) -> str:
    """Parse Taiwan official-list dates such as 115-06-03 into ISO strings."""
    import re as _re
    text = str(value or "")
    match = _re.search(r"(?<!\d)(\d{2,4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)", text)
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    if year < 1911:
        year += 1911
    elif year < 100:
        year += (default_year or dt.datetime.now(TPE).year) // 100 * 100
    try:
        return dt.datetime(year, month, day, tzinfo=TPE).isoformat()
    except ValueError:
        return ""


def _parse_news_time_required(value) -> Optional[dt.datetime]:
    """Parse a timestamp only when the source provides one; never assume 'now'."""
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        parsed = None
        raw = str(value or "").strip()
        if raw:
            try:
                parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                try:
                    from email.utils import parsedate_to_datetime
                    parsed = parsedate_to_datetime(raw)
                except (TypeError, ValueError):
                    parsed = None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _entry_published_dt(entry) -> Optional[dt.datetime]:
    """Return feed entry time only when the publisher exposes a real timestamp."""
    parsed_struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed_struct:
        try:
            return dt.datetime(*parsed_struct[:6], tzinfo=dt.timezone.utc)
        except (TypeError, ValueError):
            pass
    return _parse_news_time_required(entry.get("published") or entry.get("updated"))


def _mark_news_date_quality(item: dict, published_dt: Optional[dt.datetime]) -> dict:
    item["date_missing"] = published_dt is None
    if published_dt is not None:
        item["published_dt"] = published_dt.isoformat()
    return item


def _official_html_entries(html_text: str,
                           base_url: str,
                           source_name: str,
                           limit: int = 20,
                           stats: Optional[dict] = None) -> list[dict]:
    """Fallback parser for official list pages when RSS is blocked or malformed."""
    import html as _html
    import re as _re

    seen_links: set[str] = set()
    seen_undated: set[str] = set()

    def _record_undated(title_value: str) -> None:
        key = title_value[:120]
        if key in seen_undated:
            return
        seen_undated.add(key)
        if stats is not None:
            stats["html_undated"] = stats.get("html_undated", 0) + 1
            rejected = stats.setdefault("rejected_samples", [])
            if len(rejected) < 5:
                rejected.append({
                    "title": key,
                    "reason": "missing_date",
                    "source": source_name,
                })

    def _append(entries: list[dict], title: str, href: str, block_text: str) -> None:
        title = _html.unescape(_strip_html(title)).strip()
        if len(title) < 8:
            return
        link = urljoin(base_url, _html.unescape(str(href or "")).strip())
        if link in seen_links:
            return
        seen_links.add(link)
        if not _tw_source_is_official(link, base_url, source_name):
            return
        published = _parse_tw_roc_date(f"{title} {block_text}")
        if not published:
            _record_undated(title)
            return
        entries.append({
            "title": title[:180],
            "link": link,
            "published": published,
            "source": {"title": source_name, "href": base_url},
        })

    entries = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_text or "", "html.parser")
        for noisy in soup.select("script, style, nav, header, footer, aside"):
            noisy.decompose()
        blocks = soup.select("li, tr, article, div")
        if not blocks:
            blocks = [soup]
        for block in blocks:
            link_tags = block.find_all("a", href=True)
            if not link_tags:
                continue
            date_bits = []
            time_tag = block.find("time")
            if time_tag:
                date_bits.append(str(time_tag.get("datetime") or ""))
                date_bits.append(time_tag.get_text(" ", strip=True))
            for attr in ("data-date", "data-time", "datetime"):
                date_bits.append(str(block.get(attr) or ""))
            block_text = " ".join(
                bit for bit in [block.get_text(" ", strip=True), *date_bits] if bit)
            for link_tag in link_tags[:8]:
                _append(entries, link_tag.get_text(" ", strip=True),
                        str(link_tag.get("href") or ""), block_text)
                if len(entries) >= limit:
                    return entries
    except Exception as e:
        if stats is not None:
            stats.setdefault("errors", []).append(f"BeautifulSoup:{type(e).__name__}")

    block_pattern = _re.compile(
        r"<(?P<tag>li|tr|article|div)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
        _re.I | _re.S,
    )
    link_pattern = _re.compile(
        r"<a\b[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<title>.*?)</a>",
        _re.I | _re.S,
    )
    blocks = [match.group("body") for match in block_pattern.finditer(html_text or "")]
    if not blocks:
        blocks = [html_text or ""]
    for block in blocks:
        matches = list(link_pattern.finditer(block))
        if not matches:
            continue
        block_text = _strip_html(block)
        for match in matches[:8]:
            _append(entries, match.group("title"), match.group("href"), block_text)
            if len(entries) >= limit:
                break
        if len(entries) >= limit:
            break
    return entries


# feedparser 對「HTTP 宣告編碼 ≠ XML 內宣告」「content-type 非 XML」會設 bozo=True,
# 但這兩種其實是「警告」——feedparser 仍成功解析出 entries。視為良性,有 entries 就採用。
_BENIGN_FEED_BOZO = {"CharacterEncodingOverride", "NonXMLContentType"}


# 完整瀏覽器式 headers:部分官方站(如健保署 NHI)會擋非瀏覽器 UA 回 403,
# 補 Accept-Language / Referer 可降低被擋機率。
_OFFICIAL_HTTP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/rss+xml, application/xml, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}


def _fetch_official_response(url: str, stats: dict, timeout: int = 12):
    """抓官方來源,回傳 response 物件(呼叫端可取 .content 餵 feedparser 或 .text 解 HTML)。"""
    from urllib.parse import urlsplit
    headers = dict(_OFFICIAL_HTTP_HEADERS)
    parts = urlsplit(url)
    if parts.scheme and parts.netloc:
        headers["Referer"] = f"{parts.scheme}://{parts.netloc}/"   # 帶同站 Referer 降低被擋
    try:
        response = _http_get(url, timeout=timeout, headers=headers)
    except requests.exceptions.SSLError:
        stats["ssl_error"] = stats.get("ssl_error", 0) + 1
        if os.environ.get("ALLOW_INSECURE_OFFICIAL_SSL") != "1":
            raise
        stats["ssl_relaxed"] = stats.get("ssl_relaxed", 0) + 1
        import warnings
        import urllib3
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
            response = _http_get(url, timeout=timeout, headers=headers, verify=False)
    stats["http_status"] = response.status_code
    stats["content_type"] = response.headers.get("content-type", "")
    response.raise_for_status()
    return response


_RSS_CONTENT_CACHE: dict = {}   # N5:同一 run 內同一 RSS URL 只抓一次(內容位元組快取);測試間由 conftest 清空
_FEED_STATS: dict = {}          # V2-N1:本 run 各來源 host 的 ok/fail 次數(供 N4 歷史逐 host 追蹤);測試間清空
# 同一 host 本 run 連續失敗達此數且從未成功 → 熔斷:後續同 host 查詢直接快速失敗、不再送 HTTP+重試。
# 起因:2026-07-08 Google News 整批 503,幾十條查詢 × 重試/退避耗光 job 的 25 分 timeout → 整份晨報未寄出。
_FEED_HOST_CIRCUIT_BREAK = 4


def _feed_label(url: str) -> str:
    """把 RSS URL 聚合成 host 標籤(如 news.google.com);Google News 各查詢併為同一 host,避免 state 膨脹。"""
    try:
        return (str(url).split("/", 3)[2] or "unknown").lower()
    except IndexError:
        return "unknown"


def _feedparser_parse_url_with_timeout(url: str, timeout: int = 12):
    """Fetch RSS with a real requests timeout, then parse bytes locally.
    N5:同一 run 內同一 URL 的內容只抓一次(快取位元組、每次仍重新 parse 給獨立物件,
    避免呼叫端共用可變 feed 物件),減少重複的 Google News RSS 請求。
    V2-N1:每次『實際抓取』記錄該 host 的 ok/fail 到 _FEED_STATS(快取命中不重複計)。"""
    content = _RSS_CONTENT_CACHE.get(url)
    if content is None:
        stat = _FEED_STATS.setdefault(_feed_label(url), {"ok": 0, "fail": 0, "streak": 0})
        # 熔斷:此 host 本 run「連續」失敗達門檻(streak,任一次成功即歸零)→ 判定當下不可用,
        # 直接快速失敗、不再送 HTTP+重試。用連續而非「零成功」,是為了同時涵蓋「一開始就整批 503」
        # 與「跑到一半才被限流」(Google News rate-limit 可能在數次成功後才觸發)。
        # 避免幾十條查詢把 job timeout 預算耗光導致整份晨報不寄(晨報不可斷;2026-07-08 事故)。
        if stat.get("streak", 0) >= _FEED_HOST_CIRCUIT_BREAK:
            raise RuntimeError(
                f"{_feed_label(url)} 本 run 已連續 {stat['streak']} 次失敗 → 熔斷跳過")
        try:
            response = _http_get(
                url,
                timeout=timeout,
                headers={
                    "User-Agent": _OFFICIAL_HTTP_HEADERS["User-Agent"],
                    "Accept-Language": _OFFICIAL_HTTP_HEADERS["Accept-Language"],
                },
            )
            response.raise_for_status()
            content = getattr(response, "content", None)
            if content is None:
                content = str(getattr(response, "text", "")).encode("utf-8")
        except Exception:
            stat["fail"] += 1
            stat["streak"] = stat.get("streak", 0) + 1
            raise
        stat["ok"] += 1
        stat["streak"] = 0                # 成功即重置連續失敗計數
        if content:                       # 成功且非空才快取;失敗(例外)不快取、下次重試
            _RSS_CONTENT_CACHE[url] = content
    return feedparser.parse(content)


def _feed_usable(feed) -> tuple[list, bool]:
    """回傳 (entries, usable)。良性 bozo(編碼/content-type 警告)只要有 entries 就算可用。"""
    entries = list(getattr(feed, "entries", []) or [])
    bozo = bool(getattr(feed, "bozo", False))
    if not bozo:
        return entries, bool(entries)
    exc = getattr(feed, "bozo_exception", None)
    benign = (type(exc).__name__ in _BENIGN_FEED_BOZO) if exc is not None else False
    return entries, bool(entries and benign)


def _official_source_entries(source: dict, stats: dict) -> list[dict]:
    """Read official RSS, then fall back to the public HTML list page."""
    url = str(source.get("url") or "")
    html_url = str(source.get("html_url") or url)
    source_name = str(source.get("name") or "Official")

    # 1) feedparser 直接抓 URL
    feed = _feedparser_parse_url_with_timeout(url)
    entries, usable = _feed_usable(feed)
    if bool(getattr(feed, "bozo", False)):
        stats["bozo"] = stats.get("bozo", 0) + 1
        exc = getattr(feed, "bozo_exception", None)
        if exc and not usable:    # 良性警告(已採用)不記為 error,避免噪音
            stats.setdefault("errors", []).append(type(exc).__name__)
    if usable:
        stats["feed_ok"] = stats.get("feed_ok", 0) + 1
        return entries

    # 2) 用 requests 抓「bytes」再餵 feedparser(bytes 比 str 更能正確判斷編碼,修 CharacterEncodingOverride)
    try:
        resp = _fetch_official_response(url, stats)
        parsed = feedparser.parse(resp.content)
        entries, usable = _feed_usable(parsed)
        if usable:
            stats["requests_feed_ok"] = stats.get("requests_feed_ok", 0) + 1
            return entries
    except Exception as e:
        stats.setdefault("errors", []).append(type(e).__name__)

    # 3) 最後退化:把公開 HTML 列表頁當清單解析
    try:
        resp = _fetch_official_response(html_url, stats)
        entries = _official_html_entries(resp.text, html_url, source_name, stats=stats)
        if entries:
            stats["html_fallback_ok"] = stats.get("html_fallback_ok", 0) + 1
        return entries
    except Exception as e:
        stats.setdefault("errors", []).append(type(e).__name__)
        return []


def fetch_tw_daily_intelligence(now_tpe: Optional[dt.datetime] = None,
                                per_kind_limit: int = 8) -> dict:
    """Fetch policy and medical headlines for awareness only; never feed stock models."""
    now_tpe = now_tpe or dt.datetime.now(TPE)
    daily_start, daily_end, daily_label = _tw_intelligence_window(now_tpe)
    policy_start, policy_end, policy_label = _tw_policy_intelligence_window(now_tpe)
    output = {
        "window": f"政策近一月：{policy_label}；醫界昨日：{daily_label}",
        "policy_window": policy_label,
        "medical_window": daily_label,
        "policy": [],
        "medical": [],
        "diagnostics": {},
    }

    def _empty_stats() -> dict:
        return {
            "entries": 0, "in_window": 0, "recalled": 0, "kept": 0,
            "failed": 0, "official_kept": 0,
            "google_sources": 0, "official_sources": 0,
            "official_entries": 0, "official_empty": 0,
            "date_missing": 0, "date_parse_failed": 0, "html_undated": 0,
        }

    def _append_candidate(kind: str, entry: dict, source: dict,
                          start: dt.datetime, end: dt.datetime,
                          candidates: list[dict], stats: dict) -> None:
        def _reject(reason: str, title_value: str = "") -> None:
            rejected = stats.setdefault("rejected_samples", [])
            if len(rejected) < 5:
                rejected.append({
                    "title": str(title_value or entry.get("title") or "")[:120],
                    "reason": reason,
                    "source": source.get("name", ""),
                })

        stats["entries"] += 1
        raw_time = entry.get("published") or entry.get("updated")
        if not raw_time:
            stats["date_missing"] = stats.get("date_missing", 0) + 1
            _reject("missing_date")
            return
        parsed_time = _parse_news_time_required(raw_time)
        if parsed_time is None:
            stats["date_parse_failed"] = stats.get("date_parse_failed", 0) + 1
            _reject("invalid_date")
            return
        published = parsed_time.astimezone(TPE)
        if not start <= published < end:
            _reject("outside_window")
            return
        stats["in_window"] += 1
        title = str(entry.get("title") or "").strip()
        if not title:
            _reject("missing_title")
            return
        link = str(entry.get("link") or source.get("url") or "")
        source_name, source_url = _tw_entry_source(entry)
        text = f"{title} {link} {source_name} {source_url}"
        if not _tw_intelligence_recall_hit(kind, text):
            _reject("recall_filter", title)
            return
        stats["recalled"] += 1
        official = bool(source.get("official_hint")) or _tw_source_is_official(
            link, source_url, source_name)
        mentions_official = _tw_mentions_official_agency(text)
        scope = (
            "\u6628\u65e5\u65b0\u8a0a"
            if daily_start <= published < daily_end
            else "\u8fd1\u6708\u767c\u9175"
        )
        status = _tw_intelligence_status(title)
        importance, reasons = _tw_intelligence_importance(
            kind, title, official, scope, status)
        if mentions_official and not official:
            reasons = (reasons + ["mentions official agency"])[:4]
        if importance < (2.0 if kind == "policy" else 2.2):
            _reject(f"low_importance:{importance}", title)
            return
        stats["kept"] += 1
        if official:
            stats["official_kept"] += 1
        candidates.append({
            "title": title[:180],
            "link": link,
            "published": published.strftime("%Y-%m-%d %H:%M"),
            "scope": scope,
            "timeline_key": _tw_intelligence_timeline_key(kind, title, link),
            "importance": importance,
            "why": reasons,
            "topic": _tw_intelligence_topic(kind, title),
            "status": status,
            "source_grade": "官方" if official else "媒體",
            "official": official,
            "mentions_official_agency": mentions_official,
            "source_name": source_name or source.get("name", ""),
            "source_url": source_url or source.get("url", ""),
            # 來源設定的機構鍵(如 TFDA 兩 feed 共用「食藥署」):
            # 標題不含機關名時,每日一機構 cap 靠它辨識(Codex review)
            "org_key": source.get("org_key"),
        })

    for kind, queries in TW_INTELLIGENCE_QUERIES.items():
        candidates = []
        diagnostics = {"sources": {}, **_empty_stats()}
        start, end = (
            (policy_start, policy_end) if kind == "policy"
            else (daily_start, daily_end)
        )
        rss_when = "30d" if kind == "policy" else "7d"
        for idx, query in enumerate(queries):
            stats = diagnostics["sources"].setdefault(f"Google:{idx + 1}", _empty_stats())
            stats["source_type"] = "google"
            diagnostics["google_sources"] += 1
            def _google_reject(reason: str, title_value: str = "") -> None:
                rejected = stats.setdefault("rejected_samples", [])
                if len(rejected) < 5:
                    rejected.append({
                        "title": str(title_value or "")[:120],
                        "reason": reason,
                        "source": f"Google:{idx + 1}",
                    })
            try:
                feed = _feedparser_parse_url_with_timeout(
                    _gnews_rss(query, when=rss_when))
                for entry in feed.entries[:TW_INTELLIGENCE_GOOGLE_ENTRY_LIMIT.get(kind, 20)]:
                    stats["entries"] += 1
                    raw_time = entry.get("published") or entry.get("updated")
                    if not raw_time:
                        stats["date_missing"] = stats.get("date_missing", 0) + 1
                        _google_reject("missing_date", entry.get("title", ""))
                        continue
                    parsed_time = _parse_news_time_required(raw_time)
                    if parsed_time is None:
                        stats["date_parse_failed"] = stats.get("date_parse_failed", 0) + 1
                        _google_reject("invalid_date", entry.get("title", ""))
                        continue
                    published = parsed_time.astimezone(TPE)
                    if not start <= published < end:
                        _google_reject("outside_window", entry.get("title", ""))
                        continue
                    stats["in_window"] += 1
                    title = str(entry.get("title") or "").strip()
                    if not title:
                        _google_reject("missing_title")
                        continue
                    link = str(entry.get("link") or "")
                    source_name, source_url = _tw_entry_source(entry)
                    text = f"{title} {link} {source_name} {source_url}"
                    if not _tw_intelligence_recall_hit(kind, text):
                        _google_reject("recall_filter", title)
                        continue
                    stats["recalled"] += 1
                    official = _tw_source_is_official(link, source_url, source_name)
                    mentions_official = _tw_mentions_official_agency(text)
                    scope = (
                        "昨日新訊"
                        if daily_start <= published < daily_end
                        else "近月發酵"
                    )
                    status = _tw_intelligence_status(title)
                    importance, reasons = _tw_intelligence_importance(
                        kind, title, official, scope, status)
                    if importance < (2.0 if kind == "policy" else 2.2):
                        _google_reject(f"low_importance:{importance}", title)
                        continue
                    stats["kept"] += 1
                    if official:
                        stats["official_kept"] += 1
                    candidates.append({
                        "title": title[:180],
                        "link": link,
                        "published": published.strftime("%Y-%m-%d %H:%M"),
                        "scope": scope,
                        "timeline_key": _tw_intelligence_timeline_key(kind, title, link),
                        "importance": importance,
                        "why": reasons,
                        "topic": _tw_intelligence_topic(kind, title),
                        "status": status,
                        "source_grade": "官方" if official else "媒體",
                        "official": official,
                        "mentions_official_agency": mentions_official,
                        "source_name": source_name,
                        "source_url": source_url,
                        # Google 查詢路徑無來源設定 → 無 org_key(媒體報導標題
                        # 本就含機關名,靠 _tw_medical_org_key 標題辨識即可)
                    })
            except Exception as e:
                stats["failed"] += 1
                print(f"[tw-intelligence] {kind} query failed: {e}", file=sys.stderr)
            for key in (
                "entries", "in_window", "recalled", "kept", "failed",
                "official_kept", "date_missing", "date_parse_failed", "html_undated",
            ):
                diagnostics[key] += stats[key]
        for source in TW_INTELLIGENCE_DIRECT_SOURCES.get(kind, ()):
            source_name = str(source.get("name") or source.get("url") or "Direct")
            stats = diagnostics["sources"].setdefault(source_name, _empty_stats())
            stats["source_type"] = "official"
            diagnostics["official_sources"] += 1
            try:
                entries = _official_source_entries(source, stats)
                stats["official_entries"] += len(entries)
                diagnostics["official_entries"] += len(entries)
                if not entries:
                    stats["official_empty"] += 1
                    diagnostics["official_empty"] += 1
                for entry in entries[:TW_INTELLIGENCE_OFFICIAL_ENTRY_LIMIT.get(kind, 20)]:
                    _append_candidate(kind, entry, {
                        **source, "official_hint": True,
                    }, start, end, candidates, stats)
            except Exception as e:
                stats["failed"] += 1
                print(f"[tw-intelligence] {kind} direct source failed: {source_name}: {e}",
                      file=sys.stderr)
            for key in (
                "entries", "in_window", "recalled", "kept", "failed",
                "official_kept", "date_missing", "date_parse_failed", "html_undated",
            ):
                diagnostics[key] += stats[key]
        deduped = {}
        for item in candidates:
            key = item.get("timeline_key") or "".join(
                ch.lower() for ch in item["title"] if ch.isalnum())[:90]
            previous = deduped.get(key)
            if previous is None or (
                item.get("importance", 0),
                item.get("scope") == "昨日新訊",
                item["official"],
                item["published"],
            ) > (
                previous.get("importance", 0),
                previous.get("scope") == "昨日新訊",
                previous["official"],
                previous["published"],
            ):
                deduped[key] = item
        ranked = sorted(
            deduped.values(),
            key=lambda item: (
                item.get("importance", 0),
                item.get("scope") == "昨日新訊",
                item["official"],
                item["published"],
            ),
            reverse=True,
        )
        if kind == "medical":
            # 同一機構每天最多 1 條:中榮代刀這類延燒事件的多角度報導
            # timeline_key 不同(anchor 不同)而躲過 dedup,曾連日洗版整個醫界區。
            seen_orgs: set = set()
            capped = []
            for item in ranked:
                # 標題辨識優先;標題不含機關名(如 TFDA 公告)退回來源設定的 org_key,
                # 否則官方 feed 的多則公告會繞過 cap 佔滿醫界區(Codex review)。
                org = (_tw_medical_org_key(item.get("title", ""))
                       or item.get("org_key") or "")
                if org and org in seen_orgs:
                    continue
                if org:
                    seen_orgs.add(org)
                capped.append(item)
            ranked = capped
        output[kind] = ranked[:per_kind_limit]
        diagnostics["deduped"] = len(deduped)
        diagnostics["returned"] = len(output[kind])
        output["diagnostics"][kind] = diagnostics
    return output


# ===================== 重大事件自動辨識 (Task B) =====================
# 高權重關鍵字（中英對照），用於 classify_news_importance
# 直接牽動台股的重大地緣事件 —— 升級為 critical（會抓全文 + prompt 強制分析對台影響）


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

    def _target_link(item: dict) -> str:
        link = str(item.get("link") or "")
        if "news.google.com" in link:
            return (
                _extract_google_news_target(link)
                or str(item.get("source_url") or "")
                or str(item.get("publisher_url") or "")
            )
        return link

    # P0-2 內層保命:即使本步驟已通過時間閘,大量逐篇失敗×重試仍可能吃掉整個緩衝
    # (Codex review)。故每篇動工前也檢查全域剩餘時間,低於地板(120s,留給主分析/寄信)
    # 就提前停止抓取、回傳已抓到的——閘只擋「開始」,這裡擋「中途拖過頭」。
    _FULLTEXT_FLOOR = 120.0
    # 先掃一輪 critical(優先級高,即使在 list 後段也先抓)
    for n in news:
        if crit_fetched >= max_critical or _run_seconds_left() < _FULLTEXT_FLOOR:
            break
        if n.get("importance") != "critical":
            continue
        if n.get("fulltext"):    # 冪等:晚到的新聞(候選股/8-K)併入後會再補跑一次,別重抓
            continue
        link = _target_link(n)
        if not link or not link.startswith("http"):
            continue
        if "news.google.com" in link:
            continue
        try:
            r = _http_get(link, timeout=10,
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
    # 再掃 high(預算用滿不再抓;剩餘時間跌破地板也停)
    for n in news:
        if high_fetched >= max_high or _run_seconds_left() < _FULLTEXT_FLOOR:
            break
        if n.get("importance") != "high":
            continue
        if n.get("fulltext"):    # 已被 critical 路徑抓過(理論上不該發生,但保險)
            continue
        link = _target_link(n)
        if not link or not link.startswith("http"):
            continue
        if "news.google.com" in link:
            continue
        try:
            r = _http_get(link, timeout=8,    # high 用較短 timeout 避免拖慢
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
MODEL_HISTORY_FILE = Path("state/model_history.json")
TWSE_TOP100_ARCHIVE_FILE = Path(os.environ.get(
    "TWSE_TOP100_ARCHIVE_FILE", "state/twse_top100_archive.json"))
REVENUE_CONSENSUS_FILE = Path(os.environ.get(
    "REVENUE_CONSENSUS_FILE", "state/revenue_consensus.json"))
MODEL_HISTORY_SESSIONS = 520
MODEL_HISTORY_MAX_BYTES = 14_000_000
MODEL_BACKFILL_TARGET_SESSIONS = 180
MODEL_BACKFILL_BATCH_DAYS = int(os.environ.get("MODEL_BACKFILL_BATCH_DAYS", "12"))
MODEL_VERSION = "tw-top100-decay-regime-ridge-platt-quantile-v4"
MODEL_TIME_DECAY_HALFLIFE_SESSIONS = int(os.environ.get(
    "MODEL_TIME_DECAY_HALFLIFE_SESSIONS", "45"))
MODEL_REGIME_BLEND_WEIGHT = float(os.environ.get("MODEL_REGIME_BLEND_WEIGHT", "0.35"))
MODEL_PURGE_GAP = 2
TW_LIQUIDITY_MIN_TWD = 50_000_000


def _parse_twse_date(value: str) -> Optional[str]:
    """將 TWSE 民國年或西元日期轉成 YYYY-MM-DD。"""
    import re as _re
    parts = _re.findall(r"\d+", str(value or ""))
    if len(parts) < 3:
        return None
    year, month, day = map(int, parts[:3])
    if year < 1911:
        year += 1911
    try:
        return dt.date(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def fetch_tw_trading_sessions(months: int = 18) -> list[str]:
    """從 TWSE FMTQIK 取得真實交易日；失敗時退回 ^TWII 歷史索引。"""
    sessions: set[str] = set()
    try:
        r = _http_get(
            "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK",
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        r.raise_for_status()
        for row in r.json() or []:
            date_value = (
                row.get("Date") or row.get("日期") or row.get("date")
                or next((v for k, v in row.items() if "日期" in str(k)), None)
            )
            parsed = _parse_twse_date(date_value)
            if parsed:
                sessions.add(parsed)
    except Exception as e:
        print(f"[calendar] TWSE FMTQIK 失敗，退回 ^TWII: {e}", file=sys.stderr)
    try:
        period = f"{months}mo" if months <= 12 else "2y"
        hist = yf.Ticker("^TWII").history(period=period, auto_adjust=False)
        for idx in hist.index:
            sessions.add(
                (idx.tz_localize(None) if getattr(idx, "tz", None) else idx
                 ).strftime("%Y-%m-%d"))
    except Exception as e:
        print(f"[calendar] ^TWII 交易日曆失敗: {e}", file=sys.stderr)
    return sorted(sessions)


def _parse_twse_historical_market_day(payload: dict) -> list[dict]:
    """Parse TWSE MI_INDEX daily quote payload into compact stock rows."""
    tables = payload.get("tables") or []
    table = next((
        item for item in reversed(tables)
        if any("證券代號" in str(field) for field in item.get("fields", []))
        and any("收盤價" in str(field) for field in item.get("fields", []))
    ), None)
    if not table:
        return []
    fields = [str(field) for field in table.get("fields", [])]

    def index_of(*tokens: str) -> Optional[int]:
        return next((index for index, field in enumerate(fields)
                     if any(token in field for token in tokens)), None)

    code_i = index_of("證券代號")
    name_i = index_of("證券名稱")
    volume_i = index_of("成交股數")
    trade_value_i = index_of("成交金額")
    open_i = index_of("開盤價")
    close_i = index_of("收盤價")
    if code_i is None or close_i is None:
        return []
    rows = []
    for raw in table.get("data", []):
        code = str(raw[code_i]).strip() if code_i < len(raw) else ""
        close = _to_float(raw[close_i]) if close_i < len(raw) else None
        if not (len(code) == 4 and code.isdigit() and close):
            continue
        rows.append({
            "code": code,
            "name": str(raw[name_i]).strip() if name_i is not None and name_i < len(raw) else code,
            "volume": _to_float(raw[volume_i]) if volume_i is not None and volume_i < len(raw) else None,
            "trade_value": _to_float(raw[trade_value_i])
                           if trade_value_i is not None and trade_value_i < len(raw) else None,
            "open": _to_float(raw[open_i]) if open_i is not None and open_i < len(raw) else None,
            "close": close,
        })
    return rows


def fetch_twse_historical_market_day(session_date: str) -> list[dict]:
    """Fetch one official TWSE all-stock historical daily quote page."""
    r = _http_get(
        "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
        params={"response": "json", "date": session_date.replace("-", ""),
                "type": "ALLBUT0999"},
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    r.raise_for_status()
    payload = r.json() or {}
    if payload.get("stat") not in (None, "OK"):
        raise RuntimeError(f"TWSE MI_INDEX {session_date}: {payload.get('stat')}")
    return _parse_twse_historical_market_day(payload)


def load_twse_top100_archive() -> list[dict]:
    """Load optional licensed daily TAIEX constituent snapshots with true shares in issue."""
    if not TWSE_TOP100_ARCHIVE_FILE.exists():
        return []
    try:
        payload = json.loads(TWSE_TOP100_ARCHIVE_FILE.read_text(encoding="utf-8"))
        records = payload if isinstance(payload, list) else payload.get("records", [])
        return [record for record in records if isinstance(record, dict)
                and record.get("session_date") and record.get("stocks")]
    except Exception as e:
        print(f"[model_backfill] 正式 archive 載入失敗: {e}", file=sys.stderr)
        return []


def _historical_taiex_closes() -> dict[str, float]:
    """Fetch a compact TAIEX close map for historical labels."""
    try:
        hist = yf.Ticker("^TWII").history(period="6mo", auto_adjust=False)
        return {
            (idx.tz_localize(None) if getattr(idx, "tz", None) else idx).strftime("%Y-%m-%d"):
            float(row["Close"])
            for idx, row in hist.iterrows()
            if _safe_number(row.get("Close"))
        }
    except Exception as e:
        print(f"[model_backfill] ^TWII 歷史收盤抓取失敗: {e}", file=sys.stderr)
        return {}


def _backfill_records_from_market_days(days: dict[str, list[dict]],
                                       basics: dict[str, dict],
                                       taiex_closes: dict[str, float],
                                       seed_records: Optional[list[dict]] = None) -> list[dict]:
    """
    Build historical top-100 records from official quotes and current issued shares.

    Free TWSE endpoints do not expose historical daily shares in issue. The method is
    explicitly tagged as estimated_current_shares so it cannot be mistaken for licensed
    point-in-time market capitalization data.
    """
    price_history: dict[str, list[dict]] = {}
    first_new_session = min(days) if days else ""
    for record in sorted(seed_records or [], key=lambda item: item.get("session_date", "")):
        if first_new_session and str(record.get("session_date") or "") >= first_new_session:
            continue
        for code, stock in (record.get("stocks") or {}).items():
            close = _safe_number(stock.get("close"))
            if close:
                price_history.setdefault(str(code), []).append({
                    "close": close,
                    "volume": _safe_number(stock.get("volume")),
                })
    price_history = {code: rows[-20:] for code, rows in price_history.items()}
    output = []
    for session_date in sorted(days):
        ranked = []
        for raw in days[session_date]:
            code = str(raw.get("code") or "")
            basic = basics.get(code) or {}
            shares = _safe_number(basic.get("shares"))
            close = _safe_number(raw.get("close"))
            if not shares or not close:
                continue
            prior = price_history.setdefault(code, [])
            prior_closes = [row["close"] for row in prior]
            open_price = _safe_number(raw.get("open")) or None
            trade_value = _safe_number(raw.get("trade_value"))
            pct_5d = ((close / prior_closes[-5] - 1) * 100) if len(prior_closes) >= 5 else None
            ma20 = (sum(prior_closes[-19:] + [close]) / 20
                    if len(prior_closes) >= 19 else None)
            returns = [
                prior_closes[index] / prior_closes[index - 1] - 1
                for index in range(max(1, len(prior_closes) - 19), len(prior_closes))
                if prior_closes[index - 1]
            ]
            daily_vol = float(np.std(returns)) * 100 if len(returns) >= 5 else None
            avg20_volume = (
                sum(_safe_number(row.get("volume")) for row in prior[-20:]) / min(20, len(prior))
                if prior else None
            )
            volume = _safe_number(raw.get("volume"))
            stock = {
                "code": code,
                "name": basic.get("name") or raw.get("name") or code,
                "industry": basic.get("industry") or "",
                "market_cap": shares * close,
                "open": open_price,
                "close": close,
                "day_pct": (
                    (close / prior_closes[-1] - 1) * 100 if prior_closes else None),
                "pct_5d": pct_5d,
                "ma20_dist_pct": ((close / ma20 - 1) * 100) if ma20 else None,
                "daily_vol_pct": daily_vol,
                "vol_ratio_20d": (
                    volume / avg20_volume if avg20_volume and volume else None),
                "trade_value": trade_value or None,
                "volume": volume or None,
                "slippage_bps": _estimate_slippage_bps(trade_value, daily_vol),
                "liquidity_eligible": bool(trade_value >= TW_LIQUIDITY_MIN_TWD),
            }
            ranked.append(stock)
            prior.append({"close": close, "volume": volume})
        ranked.sort(key=lambda item: _safe_number(item.get("market_cap")), reverse=True)
        output.append({
            "session_date": session_date,
            "model_version": MODEL_VERSION,
            "taiex_close": taiex_closes.get(session_date),
            "universe_method": "estimated_current_shares",
            "stocks": {stock["code"]: stock for stock in ranked[:100]},
        })
    return output


def _market_day_label_prices(rows: list[dict], needed_codes: set[str]) -> dict[str, dict]:
    """Extract compact open/close labels for stocks held by prior universes."""
    output = {}
    for row in rows or []:
        code = str(row.get("code") or "")
        if code not in needed_codes:
            continue
        close = _safe_number(row.get("close"))
        if not close:
            continue
        open_price = _safe_number(row.get("open"))
        output[code] = {
            "close": close,
            "open": open_price or None,
        }
    return output


def _attach_historical_label_prices(records: dict[str, dict],
                                    fetched_days: dict[str, list[dict]]) -> int:
    """Attach labels for prior constituents without changing each day's universe."""
    attached = 0
    ordered_dates = sorted(records)
    for session_date, rows in fetched_days.items():
        if session_date not in records:
            continue
        prior_dates = [day for day in ordered_dates if day < session_date][-5:]
        needed_codes = {
            str(code)
            for day in prior_dates
            for code in (records[day].get("stocks") or {})
        }
        label_prices = _market_day_label_prices(rows, needed_codes)
        records[session_date]["label_prices"] = label_prices
        records[session_date]["label_prices_complete"] = (
            len(label_prices) == len(needed_codes))
        records[session_date]["label_prices_attempts"] = (
            int(records[session_date].get("label_prices_attempts", 0) or 0) + 1
        )
        attached += 1
    return attached


def save_model_history_records(records: list[dict],
                               sessions_to_keep: int = MODEL_HISTORY_SESSIONS) -> None:
    """Merge and persist compact model snapshots in one bounded write."""

    def _compact_record(record: dict) -> dict:
        keep_record = {
            "session_date", "model_version", "market_regime", "taiex_close",
            "universe_method", "structured_events", "label_prices",
            "label_prices_complete", "label_prices_attempts",
        }
        keep_stock = {
            "code", "name", "industry", "open", "close", "day_pct", "pct_5d",
            "ma20_dist_pct", "daily_vol_pct", "vol_ratio_20d", "trade_value",
            "volume", "slippage_bps", "liquidity_eligible", "rev_yoy_pct",
            "rev_mom_pct", "rev_surprise_pct", "eps_percentile", "foreign_lot",
            "invest_lot", "dealer_lot", "foreign_streak", "invest_streak",
            "tdcc_wow_pct", "margin_change_lot", "ranking_score",
            "attention_score", "industry_neutral_score", "news_catalyst_score",
            "price_forecast", "news_catalysts",
            # 基本面/估值/市值因子(鋪路:壓縮後也要保留,否則因子序列會被砍斷)
            "market_cap", "per", "yield_pct", "pbr", "gross_margin", "op_margin",
            "net_margin", "roe_q", "roa_q", "eps", "foreign_30d_lot",
            "inst_buy_vol_ratio", "short_cover_ratio", "major_holder_pct",
        }
        compact = {key: record.get(key) for key in keep_record if key in record}
        stocks = {}
        for code, stock in (record.get("stocks") or {}).items():
            row = {key: stock.get(key) for key in keep_stock if key in stock}
            if row.get("news_catalysts"):
                row["news_catalysts"] = row["news_catalysts"][:3]
            stocks[str(code)] = row
        compact["stocks"] = stocks
        compact["compact"] = True
        return compact

    try:
        merged = {
            item.get("session_date"): item for item in load_model_history()
            if item.get("session_date")
        }
        for record in records or []:
            if record.get("session_date"):
                merged[record["session_date"]] = record
        history = sorted(merged.values(), key=lambda item: item.get("session_date", "")
                         )[-sessions_to_keep:]
        payload = json.dumps(history, ensure_ascii=False, separators=(",", ":"))
        compact_index = 0
        while len(payload.encode("utf-8")) > MODEL_HISTORY_MAX_BYTES and compact_index < len(history):
            if not history[compact_index].get("compact"):
                history[compact_index] = _compact_record(history[compact_index])
                payload = json.dumps(history, ensure_ascii=False, separators=(",", ":"))
            compact_index += 1
        while len(payload.encode("utf-8")) > MODEL_HISTORY_MAX_BYTES and len(history) > 1:
            history = history[1:]
            payload = json.dumps(history, ensure_ascii=False, separators=(",", ":"))
        MODEL_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        MODEL_HISTORY_FILE.write_text(payload, encoding="utf-8")
        print(f"[model_state] 已寫入完整股票池快照（共 {len(history)} 個交易日）")
    except Exception as e:
        print(f"[model_state] 寫入失敗: {e}", file=sys.stderr)


def backfill_model_history(model_history: list[dict],
                           sessions: list[str],
                           max_days: int = MODEL_BACKFILL_BATCH_DAYS) -> tuple[list[dict], dict]:
    """Incrementally backfill model history without exceeding the daily Actions budget."""
    existing = {
        item.get("session_date"): item for item in model_history or []
        if item.get("session_date")
    }
    licensed = load_twse_top100_archive()
    for record in licensed:
        row = dict(record)
        row.setdefault("universe_method", "licensed_point_in_time_archive")
        existing[row["session_date"]] = row

    desired = sorted(set(sessions))[-MODEL_BACKFILL_TARGET_SESSIONS:]
    missing = [day for day in desired if day not in existing]
    label_refresh = [
        day for day in desired
        if (
            day in existing
            and existing[day].get("label_prices_complete") is not True
            and int(existing[day].get("label_prices_attempts", 0) or 0) < 3
        )
    ][::-1]
    # Repair recent biased labels first; time-decay makes these the most influential.
    fetch_targets = (label_refresh + missing)[:max(0, max_days)]
    fetched_days: dict[str, list[dict]] = {}
    estimated: list[dict] = []
    errors = []
    if fetch_targets:
        try:
            for session_date in fetch_targets:
                try:
                    rows = fetch_twse_historical_market_day(session_date)
                    if rows:
                        fetched_days[session_date] = rows
                except Exception as e:
                    errors.append(f"{session_date}: {e}")
            missing_days = {
                day: rows for day, rows in fetched_days.items() if day in missing
            }
            if missing_days:
                estimated = _backfill_records_from_market_days(
                    missing_days,
                    _fetch_twse_listing_basics(),
                    _historical_taiex_closes(),
                    seed_records=list(existing.values()),
                )
            for record in estimated:
                existing[record["session_date"]] = record
            labels_attached = _attach_historical_label_prices(existing, fetched_days)
        except Exception as e:
            errors.append(str(e))
            labels_attached = 0
    else:
        labels_attached = 0
    merged = sorted(existing.values(), key=lambda item: item.get("session_date", ""))
    if licensed or fetched_days:
        save_model_history_records(merged)
    report = {
        "licensed_records": len(licensed),
        "estimated_records_added": len(estimated) if fetch_targets else 0,
        "label_records_refreshed": labels_attached,
        "total_records": len(merged),
        "remaining_sessions": max(0, len(desired) - len({
            item.get("session_date") for item in merged})),
        "method": (
            "licensed_point_in_time_archive" if licensed
            else "estimated_current_shares" if fetched_days
            else "daily_accumulation"
        ),
        "limitations": (
            [] if licensed else [
                "免費 TWSE 歷史行情未含每日發行股數；市值使用目前發行股數估算",
                "下市公司可能不在目前公司基本資料內，免費回填不能完全消除倖存者偏誤",
            ]
        ),
        "errors": errors[:3],
    }
    print(f"[model_backfill] {report}")
    return merged, report


def _current_label_prices(model_history: list[dict]) -> tuple[dict[str, dict], bool]:
    """Capture today's prices for prior universes, including stocks that left Top 100."""
    needed_codes = {
        str(code)
        for record in (model_history or [])[-5:]
        for code in (record.get("stocks") or {})
    }
    if not needed_codes:
        return {}, True
    rows = []
    for raw in _fetch_twse_stock_day_all():
        code = str(raw.get("Code") or raw.get("證券代號") or "").strip()
        if code not in needed_codes:
            continue
        rows.append({
            "code": code,
            "open": _to_float(raw.get("OpeningPrice") or raw.get("開盤價")),
            "close": _to_float(raw.get("ClosingPrice") or raw.get("收盤價")),
        })
    label_prices = _market_day_label_prices(rows, needed_codes)
    return label_prices, len(label_prices) == len(needed_codes)


def _latest_completed_session(sessions: list[str], target_session_date: str) -> Optional[str]:
    """晨報在開盤前執行，最近完成交易日必須早於預測目標日。"""
    eligible = [day for day in sessions if day < target_session_date]
    return eligible[-1] if eligible else None


def _estimate_slippage_bps(trade_value,
                           daily_vol_pct=None) -> float:
    """Estimate one-way slippage conservatively from daily traded value and volatility."""
    value = _safe_number(trade_value)
    volatility = max(0.0, _safe_number(daily_vol_pct))
    if value <= 0:
        return 80.0
    if value >= 5_000_000_000:
        base = 3.0
    elif value >= 1_000_000_000:
        base = 5.0
    elif value >= 300_000_000:
        base = 8.0
    elif value >= TW_LIQUIDITY_MIN_TWD:
        base = 15.0
    else:
        base = 35.0
    return round(min(80.0, base + min(20.0, volatility * 1.5)), 2)


MODEL_FEATURES = (
    "pct_5d", "ma20_dist_pct", "daily_vol_pct", "vol_ratio_20d",
    "foreign_lot", "invest_lot", "foreign_30d_lot", "invest_30d_lot",
    "foreign_streak", "invest_streak", "tdcc_wow_pct", "margin_change_lot",
    "rev_yoy_pct", "rev_mom_pct", "rev_surprise_pct", "eps_percentile",
    "news_catalyst_score", "trade_value", "slippage_bps",
    # 新增高訊號特徵:相對同業強度、法人單日淨買占均量(標準化法人信心)、空方回補比
    "rel_strength_5d", "inst_buy_vol_ratio", "short_cover_ratio",
)

MODEL_TARGETS = {
    "1d_open": {"horizon": 1, "target": "future_open_return_pct"},
    "1d_close": {"horizon": 1, "target": "future_close_return_pct"},
    "3d": {"horizon": 3, "target": "future_close_return_pct"},
    "5d": {"horizon": 5, "target": "future_close_return_pct"},
}


def calc_absorption_ratio(model_history: list[dict], window: int = 60,
                          factor_frac: float = 0.2, short_win: int = 15) -> dict:
    """Absorption Ratio(Kritzman & Li 2010 系統性風險指標)。

    AR = universe 日報酬共變異矩陣「前 N 主成分」解釋的變異佔比(N = 資產數 × factor_frac)。
    AR 高 → 報酬高度同步、相關結構壓縮 → 市場脆弱(小衝擊易全面擴散);AR 低 → 分散、有緩衝。
    借鏡 TommasoBelluzzo/SystemicRisk(MATLAB)之演算法,純 numpy 重寫;資料用 model_history 逐日 close。

    標準化偏移 ΔAR_z =(近 short_win 日 AR 均值 − 全期 AR 均值)/ 全期 AR 標準差。
    ΔAR_z 顯著為正 = 相關結構近期快速壓縮 → 系統性風險上升『早警』(Kritzman 實證常領先回檔)。

    回 {ar, ar_shift_z, fragile(z≥1), severe(z≥2), n_assets, n_factors, asof, sample_days};資料不足回 {}。
    """
    snaps = [s for s in (model_history or [])
             if s.get("session_date") and isinstance(s.get("stocks"), dict)]
    snaps.sort(key=lambda s: s["session_date"])
    if len(snaps) < window + short_win + 5:
        return {}
    panel = [(s["session_date"],
              {c: v.get("close") for c, v in s["stocks"].items()
               if isinstance(v.get("close"), (int, float)) and v.get("close") > 0})
             for s in snaps]

    def _ar_at(end_idx: int):
        seg = panel[end_idx - window: end_idx + 1]      # window+1 個收盤 → window 個報酬
        common = set(seg[0][1])
        for _, d in seg[1:]:
            common &= set(d)
        if len(common) < 20:                            # 共同成分太少不可靠
            return None
        codes = sorted(common)
        prices = np.array([[d[c] for c in codes] for _, d in seg], dtype=float)
        rets = np.diff(np.log(prices), axis=0)          # log 報酬 (window, M)
        if rets.shape[0] < 20 or not np.all(np.isfinite(rets)):
            return None
        cov = np.cov(rets, rowvar=False)                # PSD (M, M)
        evals = np.clip(np.linalg.eigvalsh(cov), 0.0, None)
        total = float(evals.sum())
        if total <= 0:
            return None
        n_fac = max(1, int(np.ceil(len(codes) * factor_frac)))
        top = float(np.sort(evals)[::-1][:n_fac].sum())
        return (top / total, len(codes), n_fac)

    last = _ar_at(len(panel) - 1)
    if last is None:                       # 最新視窗無有效讀數 → 不給(避免回 stale 值卻標最新日期)
        return {}
    ar_series = []
    for end_idx in range(window, len(panel)):
        r = _ar_at(end_idx)
        if r is not None:
            ar_series.append(r[0])
    if len(ar_series) < short_win + 5:
        return {}
    arr = np.asarray(ar_series, dtype=float)   # arr[-1] 即最新視窗(last[0]),與 asof 一致
    long_mean, long_std = float(arr.mean()), float(arr.std())
    short_mean = float(arr[-short_win:].mean())
    shift_z = (short_mean - long_mean) / long_std if long_std > 1e-9 else 0.0
    return {
        "ar": round(float(last[0]), 4),
        "ar_shift_z": round(shift_z, 2),
        "fragile": bool(shift_z >= 1.0),       # 偏高早警
        "severe": bool(shift_z >= 2.0),        # 強烈(觸發 regime risk_off)
        "n_assets": last[1],
        "n_factors": last[2],
        "asof": panel[-1][0],
        "sample_days": len(ar_series),
    }


def _market_regime(quotes: dict) -> str:
    """依當日風險環境切換模型曝險。"""
    macro = quotes.get("MACRO", {}) or {}
    vix = _safe_number((macro.get("VIX") or {}).get("close"), 0.0)
    breadth = _safe_number((quotes.get("BREADTH") or {}).get("advance_ratio"), 50.0)
    sox = _safe_number((macro.get("SOX") or {}).get("change_pct"), 0.0)
    severe_absorption = bool((quotes.get("ABSORPTION") or {}).get("severe"))   # 系統性風險早警(ΔAR_z≥2)
    if (quotes.get("US_HOLIDAY") or {}).get("detected"):
        return "stale_us"
    if vix >= 25 or breadth <= 35 or sox <= -3 or severe_absorption:
        return "risk_off"
    if vix and vix <= 18 and breadth >= 60 and sox >= 1:
        return "risk_on"
    return "neutral"


REGIME_WEIGHTS = {
    "risk_on": {"model": 1.00, "structure": 0.80, "news": 1.00},
    "neutral": {"model": 0.80, "structure": 1.00, "news": 0.80},
    "risk_off": {"model": 0.55, "structure": 0.85, "news": 0.55},
    "stale_us": {"model": 0.45, "structure": 0.90, "news": 0.45},
}


def _industry_neutral_scores(snapshot: list[dict], score_key: str = "attention_score") -> dict[str, float]:
    """在產業內做 z-score，降低單一熱門產業壟斷 Top 5。"""
    groups: dict[str, list[tuple[str, float]]] = {}
    for item in snapshot or []:
        code = str(item.get("code") or "")
        industry = str(item.get("industry") or "未分類")
        if code:
            groups.setdefault(industry, []).append((code, _safe_number(item.get(score_key))))
    out: dict[str, float] = {}
    for values in groups.values():
        scores = np.asarray([score for _, score in values], dtype=float)
        mean = float(scores.mean()) if len(scores) else 0.0
        std = float(scores.std()) if len(scores) >= 2 else 0.0
        for code, score in values:
            out[code] = round((score - mean) / std, 4) if std > 1e-9 else 0.0
    return out


def _ridge_fit_predict(rows: list[dict], current: dict, target_key: str,
                       alpha: float = 8.0, min_rows: int = 120) -> Optional[float]:
    """純 numpy 標準化 Ridge；樣本不足或數值異常時回 None。"""
    usable = [row for row in rows if row.get(target_key) is not None]
    if len(usable) < min_rows:
        return None
    model = _ridge_fit_model(usable, target_key, alpha=alpha, min_rows=min_rows)
    return _linear_model_predict(model, current)


def _purge_recent_rows(rows: list[dict],
                       sessions: list[str],
                       gap: int = MODEL_PURGE_GAP) -> list[dict]:
    """Drop labels nearest the forecast boundary to reduce event overlap leakage."""
    ordered = sorted(set(sessions))
    if not ordered or gap <= 0:
        return list(rows)
    cutoff_index = max(0, len(ordered) - gap)
    cutoff = ordered[cutoff_index] if cutoff_index < len(ordered) else ordered[-1]
    return [row for row in rows if str(row.get("future_session_date") or "") < cutoff]


def _time_decay_weights(rows: list[dict],
                        half_life_sessions: int = MODEL_TIME_DECAY_HALFLIFE_SESSIONS
                        ) -> np.ndarray:
    """Weight recent sessions more heavily while keeping old labels useful."""
    if not rows:
        return np.asarray([], dtype=float)
    sessions = sorted({str(row.get("session_date") or "") for row in rows})
    session_rank = {session: index for index, session in enumerate(sessions)}
    latest_rank = len(sessions) - 1
    half_life = max(1, int(half_life_sessions or 1))
    weights = []
    for row in rows:
        distance = latest_rank - session_rank.get(str(row.get("session_date") or ""), latest_rank)
        weights.append(max(0.15, 0.5 ** (distance / half_life)))
    return np.asarray(weights, dtype=float)


def _model_feature_raw(row: dict, feature: str) -> float:
    value = row.get(feature)
    if value is None:
        return float("nan")
    number = _safe_number(value)
    return number if math.isfinite(number) else float("nan")


def _nan_weighted_mean_std(x: np.ndarray,
                           sample_weights: Optional[np.ndarray] = None
                           ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    missing_rates = np.mean(~np.isfinite(x), axis=0) if len(x) else np.zeros(x.shape[1])
    if sample_weights is not None and len(sample_weights) == len(x):
        weights = np.asarray(sample_weights, dtype=float)
        weights = np.where(np.isfinite(weights) & (weights > 0), weights, 1.0)
    else:
        weights = np.ones(len(x), dtype=float)
    mean = np.zeros(x.shape[1], dtype=float)
    std = np.ones(x.shape[1], dtype=float)
    for col in range(x.shape[1]):
        values = x[:, col]
        mask = np.isfinite(values)
        if not np.any(mask):
            continue
        w = weights[mask]
        v = values[mask]
        mean[col] = float(np.average(v, weights=w))
        var = float(np.average((v - mean[col]) ** 2, weights=w))
        std[col] = math.sqrt(max(var, 0.0))
    std[std < 1e-9] = 1.0
    return mean, std, missing_rates


def _feature_matrix(rows: list[dict], current: Optional[dict] = None,
                    sample_weights: Optional[np.ndarray] = None
                    ) -> tuple[np.ndarray, Optional[np.ndarray], np.ndarray, np.ndarray]:
    x = np.asarray([
        [_model_feature_raw(row, feature) for feature in MODEL_FEATURES]
        for row in rows
    ], dtype=float)
    mean, std, _ = _nan_weighted_mean_std(x, sample_weights)
    x_filled = np.where(np.isfinite(x), x, mean)
    z = (x_filled - mean) / std
    current_z = None
    if current is not None:
        current_raw = np.asarray([
            _model_feature_raw(current, feature) for feature in MODEL_FEATURES
        ], dtype=float)
        current_filled = np.where(np.isfinite(current_raw), current_raw, mean)
        current_z = (
            current_filled - mean) / std
    return z, current_z, mean, std


def _ridge_fit_model(rows: list[dict],
                     target_key: str,
                     alpha: float = 8.0,
                     min_rows: int = 120,
                     sample_weights: Optional[np.ndarray] = None) -> Optional[dict]:
    usable = [row for row in rows if row.get(target_key) is not None]
    if len(usable) < min_rows:
        return None
    weights = (
        np.asarray(sample_weights, dtype=float)
        if sample_weights is not None and len(sample_weights) == len(usable)
        else _time_decay_weights(usable)
    )
    z, _, mean, std = _feature_matrix(usable, sample_weights=weights)
    design = np.column_stack([np.ones(len(z)), z])
    y = np.asarray([_safe_number(row.get(target_key)) for row in usable], dtype=float)
    sqrt_w = np.sqrt(np.where(np.isfinite(weights) & (weights > 0), weights, 1.0))
    design_w = design * sqrt_w[:, None]
    y_w = y * sqrt_w
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    try:
        beta = np.linalg.solve(design_w.T @ design_w + penalty, design_w.T @ y_w)
    except np.linalg.LinAlgError:
        return None
    x_raw = np.asarray([
        [_model_feature_raw(row, feature) for feature in MODEL_FEATURES]
        for row in usable
    ], dtype=float)
    _, _, missing_rates = _nan_weighted_mean_std(x_raw, weights)
    return {
        "beta": beta, "mean": mean, "std": std, "weighted": True,
        "feature_imputation": "train_mean",
        "missing_rates": missing_rates,
    }


def _linear_model_predict(model: Optional[dict], current: dict) -> Optional[float]:
    if not model:
        return None
    current_raw = np.asarray([
        _model_feature_raw(current, feature) for feature in MODEL_FEATURES
    ], dtype=float)
    current_filled = np.where(np.isfinite(current_raw), current_raw, model["mean"])
    current_z = (current_filled - model["mean"]) / model["std"]
    prediction = float(np.r_[1.0, current_z] @ model["beta"])
    return prediction if math.isfinite(prediction) else None


def _quantile_ridge_fit_model(rows: list[dict],
                              target_key: str,
                              quantile: float,
                              alpha: float = 0.02,
                              min_rows: int = 120,
                              steps: int = 220,
                              sample_weights: Optional[np.ndarray] = None) -> Optional[dict]:
    usable = [row for row in rows if row.get(target_key) is not None]
    if len(usable) < min_rows:
        return None
    weights = (
        np.asarray(sample_weights, dtype=float)
        if sample_weights is not None and len(sample_weights) == len(usable)
        else _time_decay_weights(usable)
    )
    weights = np.where(np.isfinite(weights) & (weights > 0), weights, 1.0)
    z, _, mean, std = _feature_matrix(usable, sample_weights=weights)
    design = np.column_stack([np.ones(len(z)), z])
    beta = np.zeros(design.shape[1], dtype=float)
    y = np.asarray([_safe_number(row.get(target_key)) for row in usable], dtype=float)
    weight_sum = max(float(weights.sum()), 1e-9)
    for _ in range(steps):
        residual = y - design @ beta
        grad = -(design.T @ (weights * (quantile - (residual < 0).astype(float)))) / weight_sum
        grad[1:] += alpha * beta[1:]
        beta -= 0.06 * grad
    x_raw = np.asarray([
        [_model_feature_raw(row, feature) for feature in MODEL_FEATURES]
        for row in usable
    ], dtype=float)
    _, _, missing_rates = _nan_weighted_mean_std(x_raw, weights)
    return {
        "beta": beta, "mean": mean, "std": std, "weighted": True,
        "feature_imputation": "train_mean",
        "missing_rates": missing_rates,
    }


def _quantile_ridge_fit_predict(rows: list[dict],
                                current: dict,
                                target_key: str,
                                quantile: float,
                                alpha: float = 0.02,
                                min_rows: int = 120,
                                steps: int = 220) -> Optional[float]:
    """Fit a small regularized linear quantile model with pinball loss."""
    model = _quantile_ridge_fit_model(
        rows, target_key, quantile, alpha=alpha, min_rows=min_rows, steps=steps)
    return _linear_model_predict(model, current)


def _platt_fit(scores: list[float],
               labels: list[float],
               min_rows: int = 30) -> Optional[tuple[float, float]]:
    """Fit sigmoid(a * score + b) on held-out historical probabilities."""
    if len(scores) < min_rows or len(set(labels)) < 2:
        return None
    x = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=float)
    a, b = 1.0, 0.0
    for _ in range(300):
        p = 1.0 / (1.0 + np.exp(-np.clip(a * x + b, -30, 30)))
        a -= 0.08 * float(np.mean((p - y) * x))
        b -= 0.08 * float(np.mean(p - y))
    return float(a), float(b)


def _platt_params_for_rows(rows: list[dict]) -> Optional[tuple[float, float]]:
    """Fit reusable Platt parameters on a time-ordered validation tail."""
    ordered = sorted(rows, key=lambda row: str(row.get("session_date") or ""))
    session_dates = sorted({str(row.get("session_date") or "") for row in ordered})
    if len(session_dates) < 5:
        return None
    cutoff = session_dates[max(1, int(len(session_dates) * 0.8))]
    train = [row for row in ordered if str(row.get("session_date") or "") < cutoff]
    validation = [row for row in ordered if str(row.get("session_date") or "") >= cutoff]
    if len(validation) < 30:
        return None
    model = _ridge_fit_model(train, "beat_market", min_rows=120)
    if model is None:
        return None
    scores, labels = [], []
    for row in validation:
        score = _linear_model_predict(model, row)
        if score is not None:
            scores.append(score)
            labels.append(_safe_number(row.get("beat_market")))
    return _platt_fit(scores, labels)


def _platt_params_for_blended_rows(rows: list[dict],
                                   market_regime: str,
                                   regime_weight: float) -> Optional[tuple[float, float]]:
    """Calibrate the final blended global/regime score, not only the global model."""
    ordered = sorted(rows, key=lambda row: str(row.get("session_date") or ""))
    session_dates = sorted({str(row.get("session_date") or "") for row in ordered})
    if len(session_dates) < 5:
        return None
    cutoff = session_dates[max(1, int(len(session_dates) * 0.8))]
    train = [row for row in ordered if str(row.get("session_date") or "") < cutoff]
    validation = [row for row in ordered if str(row.get("session_date") or "") >= cutoff]
    if len(validation) < 30:
        return None
    train_regime = [
        row for row in train
        if str(row.get("market_regime") or "neutral") == str(market_regime or "neutral")
    ]
    if len(train_regime) < 120 or regime_weight <= 0:
        return _platt_params_for_rows(rows)
    global_model = _ridge_fit_model(train, "beat_market", min_rows=120)
    regime_model = _ridge_fit_model(train_regime, "beat_market", min_rows=120)
    if global_model is None:
        return None
    scores, labels = [], []
    for row in validation:
        global_score = _linear_model_predict(global_model, row)
        regime_score = _linear_model_predict(regime_model, row)
        if global_score is None:
            continue
        if regime_score is not None:
            score = global_score * (1 - regime_weight) + regime_score * regime_weight
        else:
            score = global_score
        scores.append(score)
        labels.append(_safe_number(row.get("beat_market")))
    return _platt_fit(scores, labels)


def _calibrated_beat_probability(raw_probability: Optional[float],
                                 params: Optional[tuple[float, float]]
                                 ) -> tuple[Optional[float], bool]:
    if raw_probability is None:
        return None, False
    if params is None:
        return max(0.05, min(0.95, raw_probability)), False
    a, b = params
    return max(0.05, min(0.95, _sigmoid(a * raw_probability + b))), True


def _recent_direction_hit_pct(rows: list[dict],
                              target_key: str,
                              limit: int = 80) -> Optional[float]:
    """Expose recent realized directional quality without claiming false precision."""
    usable = [row for row in rows if row.get(target_key) is not None][-limit:]
    if not usable:
        return None
    hits = []
    for row in usable:
        prediction = ((row.get("price_forecast") or {}).get(row.get("forecast_key", "")) or {}
                      ).get("expected_return_pct")
        if prediction is not None:
            hits.append((_safe_number(prediction) >= 0) == (_safe_number(row[target_key]) >= 0))
    return round(sum(hits) / len(hits) * 100, 1) if hits else None


def _probability_calibration_metrics(values: list[tuple[float, float]],
                                     bins: int = 10) -> dict:
    """Return Brier score and expected calibration error for realized probabilities."""
    usable = [
        (max(0.0, min(1.0, _safe_number(probability))), _safe_number(label))
        for probability, label in values
        if probability is not None and label is not None
    ]
    if not usable:
        return {"probability_samples": 0, "brier_score": None, "ece_pct": None}
    brier = sum((probability - label) ** 2 for probability, label in usable) / len(usable)
    ece = 0.0
    for bin_index in range(bins):
        lower, upper = bin_index / bins, (bin_index + 1) / bins
        bucket = [
            (probability, label) for probability, label in usable
            if lower <= probability < upper or (bin_index == bins - 1 and probability == 1.0)
        ]
        if bucket:
            avg_probability = sum(item[0] for item in bucket) / len(bucket)
            observed = sum(item[1] for item in bucket) / len(bucket)
            ece += len(bucket) / len(usable) * abs(avg_probability - observed)
    return {
        "probability_samples": len(usable),
        "brier_score": round(brier, 4),
        "ece_pct": round(ece * 100, 2),
    }


def evaluate_model_rolling_origin(model_history: list[dict],
                                  sessions: list[str],
                                  max_origins: int = 16,
                                  min_train_rows: int = 180) -> dict:
    """Offline purged rolling-origin backtest using only prior realized rows."""
    output = {
        "model_version": MODEL_VERSION,
        "max_origins": max_origins,
        "min_train_rows": min_train_rows,
        "purge_gap_sessions": MODEL_PURGE_GAP,
    }
    for forecast_key, config in MODEL_TARGETS.items():
        horizon = config["horizon"]
        target_key = config["target"]
        rows = build_model_training_rows(model_history, sessions, horizon)
        by_session: dict[str, list[dict]] = {}
        for row in rows:
            by_session.setdefault(str(row.get("session_date") or ""), []).append(row)
        ordered_sessions = sorted(by_session)
        session_rank = {session: index for index, session in enumerate(ordered_sessions)}
        validation_sessions = ordered_sessions[-max_origins:]
        errors = []
        direction_hits = []
        probability_values = []
        top5_returns = []
        top5_net_returns = []
        top5_excess = []
        ranking_top5_returns = []
        ranking_top5_net_returns = []
        ranking_top5_excess = []
        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        evaluated_origins = 0
        for session_date in validation_sessions:
            origin_rank = session_rank.get(session_date, 0)
            train_future_cutoff = max(0, origin_rank - MODEL_PURGE_GAP)
            train = [
                row for row in rows
                if session_rank.get(str(row.get("future_session_date") or ""), 10**9)
                < train_future_cutoff
            ]
            if len(train) < min_train_rows:
                continue
            return_model = _ridge_fit_model(train, target_key, min_rows=min_train_rows)
            beat_model = _ridge_fit_model(train, "beat_market", min_rows=min_train_rows)
            platt_params = _platt_params_for_rows(train)
            if return_model is None:
                continue
            evaluated_origins += 1
            scored = []
            for row in by_session.get(session_date, []):
                expected = _linear_model_predict(return_model, row)
                actual = row.get(target_key)
                if expected is None or actual is None:
                    continue
                errors.append(_safe_number(actual) - expected)
                direction_hits.append((_safe_number(actual) >= 0) == (expected >= 0))
                beat_raw = _linear_model_predict(beat_model, row)
                probability, _ = _calibrated_beat_probability(beat_raw, platt_params)
                if probability is not None:
                    probability_values.append((probability, row.get("beat_market")))
                scored.append((expected, row))
            tradable = [
                item for item in scored
                if item[1].get("liquidity_eligible") is not False
            ]
            top = sorted(tradable, key=lambda item: item[0], reverse=True)[:5]
            realized = [row.get(target_key) for _, row in top if row.get(target_key) is not None]
            if realized:
                avg_return = sum(realized) / len(realized)
                avg_cost = sum(
                    _safe_number(row.get("slippage_bps"), 80.0) * 2 / 100
                    for _, row in top) / len(top)
                avg_net_return = avg_return - avg_cost
                top5_returns.append(avg_return)
                top5_net_returns.append(avg_net_return)
                top5_excess.append(sum(_safe_number(row.get("future_excess_pct")) for _, row in top) / len(top))
                equity *= 1 + avg_net_return / 100
                peak = max(peak, equity)
                max_drawdown = min(max_drawdown, equity / peak - 1)
            rankable = [
                row for _, row in tradable
                if row.get("ranking_score", row.get("attention_score")) is not None
            ]
            ranked_top = sorted(
                rankable,
                key=lambda row: _safe_number(row.get("ranking_score", row.get("attention_score"))),
                reverse=True,
            )[:5]
            ranked_realized = [
                row.get(target_key) for row in ranked_top if row.get(target_key) is not None]
            if ranked_realized:
                ranked_return = sum(ranked_realized) / len(ranked_realized)
                ranked_cost = sum(
                    _safe_number(row.get("slippage_bps"), 80.0) * 2 / 100
                    for row in ranked_top) / len(ranked_top)
                ranking_top5_returns.append(ranked_return)
                ranking_top5_net_returns.append(ranked_return - ranked_cost)
                ranking_top5_excess.append(sum(
                    _safe_number(row.get("future_excess_pct")) for row in ranked_top) / len(ranked_top))
        output[forecast_key] = {
            "samples": len(errors),
            "origins": evaluated_origins,
            "forecast_mae_pct": round(sum(abs(e) for e in errors) / len(errors), 3) if errors else None,
            "direction_hit_pct": round(sum(direction_hits) / len(direction_hits) * 100, 1) if direction_hits else None,
            "top5_avg_return_pct": round(sum(top5_returns) / len(top5_returns), 3) if top5_returns else None,
            "top5_avg_net_return_pct": round(sum(top5_net_returns) / len(top5_net_returns), 3)
                                       if top5_net_returns else None,
            "top5_avg_excess_pct": round(sum(top5_excess) / len(top5_excess), 3) if top5_excess else None,
            "top5_max_drawdown_pct": round(max_drawdown * 100, 3) if top5_net_returns else None,
            "ranking_top5_avg_return_pct": (
                round(sum(ranking_top5_returns) / len(ranking_top5_returns), 3)
                if ranking_top5_returns else None),
            "ranking_top5_avg_net_return_pct": (
                round(sum(ranking_top5_net_returns) / len(ranking_top5_net_returns), 3)
                if ranking_top5_net_returns else None),
            "ranking_top5_avg_excess_pct": (
                round(sum(ranking_top5_excess) / len(ranking_top5_excess), 3)
                if ranking_top5_excess else None),
            **_probability_calibration_metrics(probability_values),
        }
    return output


def build_feature_drift_report(model_history: list[dict],
                               snapshot: list[dict],
                               min_history_rows: int = 120) -> dict:
    """Detect cross-sectional feature shifts and missing-data spikes."""
    historical = []
    for record in (model_history or [])[-60:]:
        historical.extend((record.get("stocks") or {}).values())
    if len(historical) < min_history_rows or not snapshot:
        return {
            "status": "fallback",
            "penalty": 1.0,
            "history_rows": len(historical),
            "alerts": ["歷史特徵樣本不足，漂移監控仍在累積"],
        }
    alerts = []
    for feature in MODEL_FEATURES:
        old_values = [_safe_number(row.get(feature)) for row in historical
                      if row.get(feature) is not None]
        new_values = [_safe_number(row.get(feature)) for row in snapshot
                      if row.get(feature) is not None]
        if len(old_values) < 20 or not new_values:
            continue
        old_mean = float(np.mean(old_values))
        old_std = float(np.std(old_values))
        shift_z = abs(float(np.mean(new_values)) - old_mean) / max(old_std, 1e-9)
        old_missing = 1 - len(old_values) / len(historical)
        new_missing = 1 - len(new_values) / len(snapshot)
        if shift_z >= 2.5 or new_missing - old_missing >= 0.25:
            alerts.append({
                "feature": feature,
                "mean_shift_z": round(shift_z, 2),
                "missing_pct": round(new_missing * 100, 1),
            })
    penalty = min(4.0, len(alerts) * 0.75)
    return {
        "status": "error" if penalty >= 3 else "fallback" if alerts else "ok",
        "penalty": round(penalty, 2),
        "history_rows": len(historical),
        "alerts": alerts[:8],
    }


def build_source_health_report(snapshot: list[dict],
                               news: list[dict],
                               structured_events: list[dict],
                               tw_intelligence: Optional[dict] = None) -> dict:
    """Convert market data availability into a conservative ranking penalty.

    Taiwan policy/medical intelligence is awareness-only: its diagnostics are reported,
    but outages must not change stock ranking scores.
    """
    total = len(snapshot or [])
    quality_news = [
        item for item in (news or [])
        if not item.get("date_missing")
        and not _other_sector_label_from_source(str(item.get("source", "")))
    ]
    strong_news = [
        item for item in quality_news
        if (item.get("source_grade") or _news_source_grade(item)) in ("A", "B")
    ]
    tw_diag = (tw_intelligence or {}).get("diagnostics") or {}
    policy_diag = tw_diag.get("policy") or {}
    medical_diag = tw_diag.get("medical") or {}

    def _tw_diag_healthy(diag: dict) -> bool:
        if not tw_intelligence:
            return True
        source_count = len(diag.get("sources") or {})
        return (
            diag.get("entries", 0) > 0
            and diag.get("failed", 0) < max(3, source_count)
        )

    def _tw_official_diag_healthy(diag: dict) -> bool:
        if not tw_intelligence:
            return True
        official_sources = int(diag.get("official_sources") or 0)
        if official_sources <= 0:
            return False
        official_empty = int(diag.get("official_empty") or 0)
        official_entries = int(diag.get("official_entries") or 0)
        return official_entries > 0 and official_empty < official_sources

    market_checks = {
        "universe": total >= 70,
        "institutional": bool(total and sum(bool(
            item.get("foreign_lot") or item.get("invest_lot") or item.get("dealer_lot"))
            for item in snapshot) / total >= 0.3),
        "revenue": bool(total and sum(item.get("rev_yoy_pct") is not None
                                     for item in snapshot) / total >= 0.5),
        "liquidity": bool(total and sum(item.get("trade_value") is not None
                                       for item in snapshot) / total >= 0.7),
        "news": len(quality_news) >= 10 and len(strong_news) >= 5,
        "structured_events": bool(structured_events),
    }
    awareness_checks = {
        "tw_policy_intelligence": _tw_diag_healthy(policy_diag),
        "tw_medical_intelligence": _tw_diag_healthy(medical_diag),
        "tw_policy_official_sources": _tw_official_diag_healthy(policy_diag),
        "tw_medical_official_sources": _tw_official_diag_healthy(medical_diag),
    }
    failures = [name for name, healthy in market_checks.items() if not healthy]
    awareness_failures = [
        name for name, healthy in awareness_checks.items() if not healthy]
    score = max(0.0, 1.0 - len(failures) * 0.12)
    return {
        "status": "error" if score < 0.55 else "fallback" if failures else "ok",
        "awareness_status": "fallback" if awareness_failures else "ok",
        "score": round(score, 3),
        "ranking_penalty": round(min(4.0, len(failures) * 0.65), 2),
        "checks": {**market_checks, **awareness_checks},
        "market_checks": market_checks,
        "awareness_checks": awareness_checks,
        "failures": failures,
        "awareness_failures": awareness_failures,
    }


SOURCE_HEALTH_HISTORY_FILE = Path("state/source_health_history.json")


def update_source_health_history(report: dict, today: str, keep_days: int = 30,
                                  feed_stats: Optional[dict] = None) -> list[str]:
    """把每日『類別檢查(checks)』與『各來源 host 健康(V2-N1)』累積到滾動 30 天歷史,
    回傳『連續 ≥3 天失敗』的項目(檢查項 + 個別 host)。純 JSON、失敗不影響晨報(回空)。
    host 當日健康:有成功即健康;只有失敗=False;完全沒抓該 host=不列(不算失敗)。"""
    checks = (report or {}).get("checks") or {}
    if not checks or not today:
        return []
    feeds_today = {}
    for host, s in (feed_stats or {}).items():
        ok, fail = int((s or {}).get("ok", 0)), int((s or {}).get("fail", 0))
        if ok or fail:
            feeds_today[host] = bool(ok)
    hist: list = []
    try:
        if SOURCE_HEALTH_HISTORY_FILE.exists():
            hist = json.loads(SOURCE_HEALTH_HISTORY_FILE.read_text(encoding="utf-8")) or []
    except Exception:
        hist = []
    hist = [h for h in hist if isinstance(h, dict) and h.get("date") != today]
    hist.append({"date": today, "checks": {k: bool(v) for k, v in checks.items()},
                 "feeds": feeds_today})
    hist.sort(key=lambda h: h.get("date", ""))
    hist = hist[-keep_days:]
    try:
        SOURCE_HEALTH_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        SOURCE_HEALTH_HISTORY_FILE.write_text(
            json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[health] 歷史寫入失敗: {e}", file=sys.stderr)

    def _persist(field: str) -> list[str]:
        # 連續失敗 streak(從最近往回數,遇 True 或缺該項即中斷)
        out = []
        for name in sorted({n for h in hist for n in (h.get(field) or {})}):
            streak = 0
            for h in reversed(hist):
                v = (h.get(field) or {}).get(name)
                if v is False:
                    streak += 1
                else:
                    break
            if streak >= 3:
                out.append(f"{name}({streak}天)")
        return out

    return _persist("checks") + _persist("feeds")


def load_model_history() -> list[dict]:
    """讀取 point-in-time 股票池歷史。"""
    if not MODEL_HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(MODEL_HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[model_state] 載入失敗: {e}", file=sys.stderr)
        return []


def _snapshot_for_model(snapshot: list[dict]) -> dict[str, dict]:
    """縮減每日股票池欄位，保留可訓練特徵、事件與當日預測。"""
    keep = {
        "code", "name", "industry", "market_cap", "open", "close", "day_pct", "pct_5d",
        "ma20_dist_pct", "daily_vol_pct", "vol_ratio_20d", "high20_break",
        "foreign_lot", "invest_lot", "dealer_lot", "total_lot", "foreign_30d_lot",
        "invest_30d_lot", "foreign_streak", "invest_streak", "tdcc_wow_pct",
        "margin_change_lot", "rev_yoy_pct", "rev_mom_pct", "eps_percentile",
        "rel_strength_5d", "inst_buy_vol_ratio", "short_cover_ratio",
        "short_balance", "short_balance_chg",
        "rev_expected", "rev_surprise_pct", "rev_expectation_method",
        "trade_value", "volume", "slippage_bps", "liquidity_eligible",
        "feature_drift_penalty", "source_health_penalty", "model_monitor_penalty",
        "attention_score", "ranking_score", "ranking_components", "attention_rank",
        "industry_neutral_score", "news_catalyst_score",
        "price_forecast",
        # 基本面/估值因子(鋪路:供日後回測,_attach_listing_fundamentals 附加)
        "per", "yield_pct", "pbr", "gross_margin", "op_margin", "net_margin",
        "roe_q", "roa_q", "eps", "major_holder_pct", "inst_buy_vol_ratio",
    }
    output = {}
    for item in snapshot or []:
        if not item.get("code") or not item.get("close"):
            continue
        row = {k: item.get(k) for k in keep if k in item}
        row["news_catalysts"] = [{
            key: evidence.get(key)
            for key in ("event_id", "event_type", "direction", "relation",
                        "score_delta", "source_grade", "surprise_score",
                        "revenue_surprise_pct", "lifecycle", "lifecycle_weight",
                        "scope_company", "scope_industry", "scope_supply_chain",
                        "timeline_key")
            if evidence.get(key) is not None
        } for evidence in (item.get("news_catalysts") or [])[:4]]
        output[str(item["code"])] = row
    return output


def save_model_history(record: dict, sessions_to_keep: int = MODEL_HISTORY_SESSIONS) -> None:
    """保存完整股票池 point-in-time 快照；一般 state writer 會合併 push。"""
    if record.get("session_date"):
        save_model_history_records([record], sessions_to_keep=sessions_to_keep)


def build_model_training_rows(model_history: list[dict],
                              sessions: list[str],
                              horizon: int) -> list[dict]:
    """從完整快照建立 point-in-time 標籤：未來報酬與是否勝過大盤。"""
    by_session = {
        item.get("session_date"): item for item in model_history or []
        if item.get("session_date")
    }
    ordered = sorted(set(sessions))
    rows = []
    for index, session_date in enumerate(ordered):
        if session_date not in by_session:
            continue
        if index + horizon >= len(ordered):
            continue
        future_date = ordered[index + horizon]
        if future_date not in by_session:
            continue
        current = by_session[session_date]
        future = by_session[future_date]
        production_universe = (
            bool(current.get("generated_at"))
            or current.get("model_version") == MODEL_VERSION
            or current.get("universe_method") in {
                "estimated_current_shares",
                "licensed_point_in_time_archive",
                "daily_point_in_time_top100",
            }
        )
        if production_universe and future.get("label_prices_complete") is not True:
            # Using only future Top-100 members would drop losers that leave the index
            # and make training/backtests optimistically biased.
            continue
        current_market = _safe_number(current.get("taiex_close"))
        future_market = _safe_number(future.get("taiex_close"))
        if not current_market or not future_market:
            continue
        market_return = (future_market / current_market - 1) * 100
        future_stocks = future.get("stocks") or {}
        future_labels = future.get("label_prices") or {}
        for code, stock in (current.get("stocks") or {}).items():
            future_stock = future_stocks.get(code) or future_labels.get(code) or {}
            close = _safe_number(stock.get("close"))
            future_close = _safe_number(future_stock.get("close"))
            if not close or not future_close:
                continue
            future_open = _safe_number(future_stock.get("open"))
            stock_return = (future_close / close - 1) * 100
            row = dict(stock)
            row.update({
                "session_date": session_date,
                "future_session_date": future_date,
                "model_version": current.get("model_version") or "legacy",
                "market_regime": current.get("market_regime") or "neutral",
                "code": code,
                "future_return_pct": stock_return,
                "future_close_return_pct": stock_return,
                "future_open_return_pct": (
                    (future_open / close - 1) * 100 if future_open else None),
                "future_excess_pct": stock_return - market_return,
                "beat_market": 1.0 if stock_return > market_return else 0.0,
            })
            rows.append(row)
    return rows


def _model_predictions(model_history: list[dict], sessions: list[str],
                       snapshot: list[dict], horizon: int,
                       target_key: str = "future_close_return_pct",
                       forecast_key: Optional[str] = None,
                       market_regime: str = "neutral") -> dict[str, dict]:
    """分類與報酬雙模型：勝過大盤機率 + 預期報酬。"""
    rows = _purge_recent_rows(
        build_model_training_rows(model_history, sessions, horizon), sessions)
    forecast_key = forecast_key or f"{horizon}d"
    for row in rows:
        row["forecast_key"] = forecast_key
    regime_rows = [
        row for row in rows
        if str(row.get("market_regime") or "neutral") == str(market_regime or "neutral")
    ]
    regime_weight = max(0.0, min(0.75, MODEL_REGIME_BLEND_WEIGHT))
    if len(regime_rows) < 120:
        regime_weight = 0.0
    beat_model = _ridge_fit_model(rows, "beat_market")
    return_model = _ridge_fit_model(rows, target_key)
    lower_model = _quantile_ridge_fit_model(rows, target_key, 0.10)
    upper_model = _quantile_ridge_fit_model(rows, target_key, 0.90)
    regime_beat_model = _ridge_fit_model(regime_rows, "beat_market") if regime_weight else None
    regime_return_model = _ridge_fit_model(regime_rows, target_key) if regime_weight else None
    regime_lower_model = (
        _quantile_ridge_fit_model(regime_rows, target_key, 0.10)
        if regime_weight else None)
    regime_upper_model = (
        _quantile_ridge_fit_model(regime_rows, target_key, 0.90)
        if regime_weight else None)
    platt_params = (
        _platt_params_for_blended_rows(rows, market_regime, regime_weight)
        if regime_weight else _platt_params_for_rows(rows)
    )
    calibration_method = "blended_platt" if regime_weight else "global_platt"

    def _blend(global_value: Optional[float], regime_value: Optional[float]) -> Optional[float]:
        if global_value is None:
            return regime_value
        if regime_value is None or regime_weight <= 0:
            return global_value
        return global_value * (1 - regime_weight) + regime_value * regime_weight

    out = {}
    for item in snapshot or []:
        code = str(item.get("code") or "")
        beat_raw = _blend(
            _linear_model_predict(beat_model, item),
            _linear_model_predict(regime_beat_model, item),
        )
        beat_probability, calibrated = _calibrated_beat_probability(beat_raw, platt_params)
        return_raw = _blend(
            _linear_model_predict(return_model, item),
            _linear_model_predict(regime_return_model, item),
        )
        lower = _blend(
            _linear_model_predict(lower_model, item),
            _linear_model_predict(regime_lower_model, item),
        )
        upper = _blend(
            _linear_model_predict(upper_model, item),
            _linear_model_predict(regime_upper_model, item),
        )
        fallback = beat_raw is None or return_raw is None
        method = (
            "time-decayed ridge + regime blend + Platt + quantile"
            if not fallback and regime_weight
            else "time-decayed ridge + Platt + quantile" if not fallback
            else "heuristic fallback"
        )
        out[code] = {
            "training_rows": len(rows),
            "regime_training_rows": len(regime_rows),
            "market_regime": market_regime,
            "regime_blend_weight": round(regime_weight, 3),
            "beat_market_probability": (
                round(beat_probability, 3) if beat_probability is not None else None),
            "expected_return_pct": (
                round(max(-12.0, min(12.0, return_raw)), 3)
                if return_raw is not None else None),
            "quantile_lower_pct": round(lower, 3) if lower is not None else None,
            "quantile_upper_pct": round(upper, 3) if upper is not None else None,
            "recent_direction_hit_pct": _recent_direction_hit_pct(rows, target_key),
            "probability_calibrated": calibrated,
            "probability_calibration_method": calibration_method if calibrated else "fallback",
            "fallback_enabled": fallback,
            "model_version": MODEL_VERSION,
            "method": method,
        }
    return out


def evaluate_model_walk_forward(model_history: list[dict],
                                sessions: list[str]) -> dict:
    """完整 walk-forward 指標：MAE、方向、超額報酬、Top5、區間涵蓋與回撤。"""
    output: dict = {
        "model_version": MODEL_VERSION,
        "purge_gap_sessions": MODEL_PURGE_GAP,
        "versions": {},
    }
    for forecast_key, config in MODEL_TARGETS.items():
        horizon = config["horizon"]
        target_key = config["target"]
        rows = build_model_training_rows(model_history, sessions, horizon)
        errors = []
        direction_hits = []
        interval_hits = []
        probability_values = []
        top5_returns = []
        top5_net_returns = []
        top5_excess = []
        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        by_session: dict[str, list[dict]] = {}
        for row in rows:
            by_session.setdefault(row["session_date"], []).append(row)
            forecast = (row.get("price_forecast") or {}).get(forecast_key) or {}
            expected = forecast.get("expected_return_pct")
            if expected is not None:
                actual = row.get(target_key)
                if actual is None:
                    continue
                errors.append(actual - _safe_number(expected))
                direction_hits.append((actual >= 0) == (_safe_number(expected) >= 0))
                probability = forecast.get("beat_market_probability")
                if probability is not None:
                    probability_values.append((probability, row.get("beat_market")))
                lower = forecast.get("lower")
                upper = forecast.get("upper")
                close = _safe_number(row.get("close"))
                if lower and upper and close:
                    actual_price = close * (1 + actual / 100)
                    interval_hits.append(float(lower) <= actual_price <= float(upper))
                version = str(row.get("model_version") or "legacy")
                version_stats = output["versions"].setdefault(version, {}).setdefault(
                    forecast_key, {"errors": [], "direction_hits": []})
                version_stats["errors"].append(actual - _safe_number(expected))
                version_stats["direction_hits"].append(
                    (actual >= 0) == (_safe_number(expected) >= 0))
        for values in by_session.values():
            tradable = [row for row in values if row.get("liquidity_eligible") is not False]
            rankable = [row for row in tradable if row.get(
                "ranking_score", row.get("attention_score")) is not None]
            top = sorted(rankable, key=lambda row: _safe_number(
                row.get("ranking_score", row.get("attention_score"))), reverse=True)[:5]
            if top:
                realized = [row.get(target_key) for row in top if row.get(target_key) is not None]
                if not realized:
                    continue
                avg_return = sum(realized) / len(realized)
                avg_cost = sum(
                    _safe_number(row.get("slippage_bps"), 80.0) * 2 / 100
                    for row in top) / len(top)
                avg_net_return = avg_return - avg_cost
                avg_excess = sum(row["future_excess_pct"] for row in top) / len(top)
                top5_returns.append(avg_return)
                top5_net_returns.append(avg_net_return)
                top5_excess.append(avg_excess)
                equity *= 1 + avg_net_return / 100
                peak = max(peak, equity)
                max_drawdown = min(max_drawdown, equity / peak - 1)
        output[forecast_key] = {
            "samples": len(rows),
            "sessions": len(by_session),
            "forecast_mae_pct": round(sum(abs(e) for e in errors) / len(errors), 3) if errors else None,
            "direction_hit_pct": round(sum(direction_hits) / len(direction_hits) * 100, 1) if direction_hits else None,
            "interval_coverage_pct": round(sum(interval_hits) / len(interval_hits) * 100, 1) if interval_hits else None,
            "interval_samples": len(interval_hits),
            "top5_avg_return_pct": round(sum(top5_returns) / len(top5_returns), 3) if top5_returns else None,
            "top5_avg_net_return_pct": round(sum(top5_net_returns) / len(top5_net_returns), 3)
                                       if top5_net_returns else None,
            "top5_avg_excess_pct": round(sum(top5_excess) / len(top5_excess), 3) if top5_excess else None,
            "top5_max_drawdown_pct": round(max_drawdown * 100, 3) if top5_returns else None,
            **_probability_calibration_metrics(probability_values),
        }
    # Backward-compatible aliases used by the existing report text.
    for version, targets in output["versions"].items():
        for forecast_key, stats in targets.items():
            errors = stats.pop("errors")
            hits = stats.pop("direction_hits")
            stats.update({
                "samples": len(errors),
                "forecast_mae_pct": round(sum(abs(value) for value in errors) / len(errors), 3)
                                    if errors else None,
                "direction_hit_pct": round(sum(hits) / len(hits) * 100, 1) if hits else None,
            })
    output[3] = output["3d"]
    output[5] = output["5d"]
    output["rolling_origin"] = evaluate_model_rolling_origin(model_history, sessions)
    return output


def _model_monitoring_for_key(walk_forward: dict, forecast_key: str) -> dict:
    metrics = (walk_forward or {}).get(forecast_key) or {}
    rolling_metrics = ((walk_forward or {}).get("rolling_origin") or {}).get(forecast_key) or {}
    samples = int(metrics.get("probability_samples") or 0)
    brier = metrics.get("brier_score")
    ece = metrics.get("ece_pct")
    coverage = metrics.get("interval_coverage_pct")
    rolling_samples = int(rolling_metrics.get("samples") or 0)
    rolling_origins = int(rolling_metrics.get("origins") or 0)
    alerts = []
    if samples < 30:
        alerts.append("calibration samples < 30")
    if isinstance(brier, (int, float)) and brier > 0.25:
        alerts.append(f"Brier score high: {brier}")
    if isinstance(ece, (int, float)) and ece > 15:
        alerts.append(f"ECE high: {ece}%")
    if isinstance(coverage, (int, float)) and not 65 <= coverage <= 95:
        alerts.append(f"80pct interval coverage abnormal: {coverage}%")
    if rolling_metrics:
        rolling_brier = rolling_metrics.get("brier_score")
        rolling_direction = rolling_metrics.get("direction_hit_pct")
        rolling_net = rolling_metrics.get("top5_avg_net_return_pct")
        ranking_net = rolling_metrics.get("ranking_top5_avg_net_return_pct")
        if rolling_origins < 3 or rolling_samples < 30:
            alerts.append(
                f"rolling-origin samples low: origins={rolling_origins}, samples={rolling_samples}")
        if isinstance(rolling_brier, (int, float)) and rolling_brier > 0.28:
            alerts.append(f"rolling-origin Brier high: {rolling_brier}")
        if isinstance(rolling_direction, (int, float)) and rolling_direction < 45:
            alerts.append(f"rolling-origin direction weak: {rolling_direction}%")
        if isinstance(rolling_net, (int, float)) and rolling_net < 0:
            alerts.append(f"rolling-origin top5 net negative: {rolling_net}%")
        if isinstance(ranking_net, (int, float)) and ranking_net < 0:
            alerts.append(f"rolling-origin ranking top5 net negative: {ranking_net}%")
    severe = any(
        "Brier score high" in alert or "coverage abnormal" in alert
        or "Brier high" in alert or "direction weak" in alert
        or "top5 net negative" in alert
        for alert in alerts)
    # 熔斷訊號需「樣本夠格」:小樣本(單一 origin / 幾筆)的負淨報酬是雜訊,
    # 不可觸發熔斷,否則 ML 排名會被長期靜默關閉(5d horizon 樣本天然最少、最易誤觸)。
    suppress_signal = (
        rolling_origins >= 3 and rolling_samples >= 30
        and any("net negative" in alert for alert in alerts))
    return {
        "status": "error" if severe else "fallback" if alerts else "ok",
        "ranking_penalty": 3.0 if severe else 1.0 if alerts else 0.0,
        "suppress_signal": suppress_signal,
        "forecast_key": forecast_key,
        "metrics": metrics,
        "rolling_origin_metrics": rolling_metrics,
        "alerts": alerts,
    }


def build_model_monitoring_report(walk_forward: dict,
                                  forecast_key: str = "3d") -> dict:
    """Turn calibration metrics into a conservative quality gate for ranking."""
    keys = tuple(MODEL_TARGETS.keys())
    by_target = {
        key: _model_monitoring_for_key(walk_forward or {}, key)
        for key in keys
    }
    primary = by_target.get(forecast_key) or _model_monitoring_for_key(
        walk_forward or {}, forecast_key)
    worst_penalty = max(
        (_safe_number(item.get("ranking_penalty")) for item in by_target.values()),
        default=_safe_number(primary.get("ranking_penalty")),
    )
    if any(item.get("status") == "error" for item in by_target.values()):
        status = "error"
    elif any(item.get("status") == "fallback" for item in by_target.values()):
        status = "fallback"
    else:
        status = "ok"
    alerts = []
    for key, item in by_target.items():
        alerts.extend(f"{key}: {alert}" for alert in item.get("alerts", []))
    # 熔斷:任一 horizon 的 Top5(模型或排名公式)回測淨報酬為負「且樣本夠格」
    # (origins≥3、samples≥30,見 _model_monitoring_for_key.suppress_signal)→
    # 排名的 ML 組件不可信,降級為「純結構觀察」(audit rank 9)。
    # 刻意不用 status=="error":coverage/Brier 異常在小樣本下極易越界,會誤觸熔斷。
    suppress = any(item.get("suppress_signal") for item in by_target.values())
    return {
        **primary,
        "status": status,
        "ranking_penalty": worst_penalty,
        "suppress_ranking": suppress,
        "forecast_key": forecast_key,
        "by_target": by_target,
        "alerts": alerts,
    }


def _news_event_direction(text: str) -> int:
    """用明確事件詞判斷消息方向；同時有多空詞或沒有方向時不加分。"""
    positive = bool(_matches_any(text, NEWS_POSITIVE_TERMS))
    negative = bool(_matches_any(text, NEWS_NEGATIVE_TERMS))
    if positive == negative:
        return 0
    return 1 if positive else -1


def _event_type(text: str) -> str:
    """Map noisy headlines to a small, learnable event taxonomy."""
    lower = (text or "").lower()
    rules = (
        ("guidance_raise", ("raises guidance", "raise guidance", "上修財測", "調高財測")),
        ("guidance_cut", ("cuts guidance", "cut guidance", "下修財測", "調降財測")),
        ("orders", ("order", "訂單", "接單", "合約", "contract")),
        ("earnings", ("earnings", "eps", "財報", "獲利", "盈餘")),
        ("revenue_growth", ("revenue", "營收", "sales growth")),
        ("export_controls", ("export control", "出口管制", "制裁", "sanction")),
        ("litigation", ("lawsuit", "litigation", "訴訟", "裁罰")),
        ("geopolitical", ("war", "missile", "attack", "戰爭", "飛彈", "攻擊")),
    )
    for event_type, tokens in rules:
        if any(token in lower for token in tokens):
            return event_type
    return "general"


def _parse_news_time(value, now: Optional[dt.datetime] = None) -> dt.datetime:
    """Parse RSS and ISO dates; missing timestamps are treated as fresh but explicit."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        parsed = None
        raw = str(value or "").strip()
        if raw:
            try:
                parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                try:
                    from email.utils import parsedate_to_datetime
                    parsed = parsedate_to_datetime(raw)
                except (TypeError, ValueError):
                    parsed = None
    parsed = parsed or now
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _freshness_weight(age_hours: float) -> float:
    """Fresh events matter most; old duplicates fade quickly."""
    if age_hours <= 12:
        return 1.0
    if age_hours <= 24:
        return 0.75
    if age_hours <= 48:
        return 0.45
    return 0.20


def _event_cluster_key(event: dict) -> tuple:
    import re as _re
    title = _re.sub(r"\W+", "", str(event.get("title") or "").lower())[:48]
    if event.get("event_type") != "general":
        title = ""
    return (
        str(event.get("entity") or ""),
        str(event.get("event_type") or "general"),
        int(_safe_number(event.get("direction"))),
        title,
    )


def _event_surprise_score(event: dict) -> float:
    """Estimate how much genuinely new information an event carries."""
    explicit = event.get("surprise_score")
    if explicit is not None:
        return round(max(0.1, min(1.0, _safe_number(explicit, 0.5))), 3)
    text = f"{event.get('title', '')} {event.get('summary', '')}".lower()
    if any(token in text for token in (
            "unexpected", "surprise", "beats estimates", "misses estimates",
            "優於預期", "低於預期", "意外", "突發", "緊急")):
        return 0.95
    if any(token in text for token in (
            "as expected", "in line with", "符合預期", "市場預期", "早已預期")):
        return 0.25
    return {
        "guidance_raise": 0.90, "guidance_cut": 0.90, "orders": 0.70,
        "earnings": 0.60, "revenue_growth": 0.50, "export_controls": 0.85,
        "litigation": 0.75, "geopolitical": 0.90, "general": 0.35,
    }.get(str(event.get("event_type")), 0.35)


def extract_structured_events(news: list[dict],
                              mops: list[dict],
                              llm_events: Optional[list[dict]] = None,
                              now: Optional[dt.datetime] = None) -> list[dict]:
    """Extract, merge and cluster events with official-source priority and decay."""
    import hashlib
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    now = now.astimezone(dt.timezone.utc)
    candidates = []

    def append(item: dict, official: bool = False) -> None:
        title = str(item.get("title") or item.get("summary") or "").strip()
        if not title:
            return
        source = str(item.get("source") or ("MOPS" if official else "unknown"))
        grade = "A" if official else (item.get("source_grade") or _news_source_grade(item))
        text = f"{title} {item.get('summary', '')}"
        raw_published = item.get("published")
        parsed_published = _parse_news_time_required(raw_published)
        published = parsed_published or (now - dt.timedelta(days=7))
        age_hours = max(0.0, (now - published).total_seconds() / 3600)
        event = {
            "entity": str(item.get("entity") or item.get("code")
                          or item.get("company_label") or ""),
            "event_type": str(item.get("event_type") or _event_type(text)),
            "direction": int(_safe_number(
                item.get("direction"), _news_event_direction(text))),
            "confidence": round(max(0.05, min(1.0, _safe_number(
                item.get("confidence"), 0.90 if official else 0.65))), 3),
            "source": source,
            "source_grade": grade,
            "title": title[:180],
            "published": published.isoformat(),
            "date_missing": parsed_published is None,
            "age_hours": round(age_hours, 1),
            "freshness_weight": _freshness_weight(age_hours),
            "lifecycle": item.get("lifecycle"),
        }
        event["surprise_score"] = _event_surprise_score(
            dict(event, surprise_score=item.get("surprise_score"), summary=item.get("summary")))
        raw_id = "|".join(str(v) for v in _event_cluster_key(event))
        event["event_id"] = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:12]
        candidates.append(event)

    for item in mops or []:
        append(dict(item, source=item.get("source") or "MOPS"), official=True)
    for item in news or []:
        append(item)
    for item in llm_events or []:
        if isinstance(item, dict):
            append(dict(item, source=item.get("source") or "LLM extractor"))

    clustered: dict[tuple, dict] = {}
    for event in candidates:
        key = _event_cluster_key(event)
        quality = {"A": 1.0, "B": 0.8, "C": 0.55}.get(event["source_grade"], 0.5)
        event["quality_score"] = round(
            quality * event["freshness_weight"] * event["confidence"], 4)
        previous = clustered.get(key)
        if previous is None or event["quality_score"] > previous["quality_score"]:
            replacement = dict(event)
            replacement["sources"] = sorted(set(
                (previous or {}).get("sources", []) + [event["source"]]))
            clustered[key] = replacement
        else:
            previous["sources"] = sorted(set(previous.get("sources", []) + [event["source"]]))
    output = list(clustered.values())
    for event in output:
        event["corroboration_count"] = len(event.get("sources") or [])
    return sorted(output, key=lambda event: event["quality_score"], reverse=True)


def _event_lifecycle(event: dict) -> str:
    """Classify event progression so repeated coverage does not repeatedly add score."""
    explicit = str(event.get("lifecycle") or event.get("status") or "").lower()
    text = f"{explicit} {event.get('title', '')} {event.get('summary', '')}".lower()
    if any(token in text for token in (
            "withdrawn", "withdraw", "cancelled", "canceled", "撤回", "取消", "暫緩")):
        return "withdrawn"
    if any(token in text for token in (
            "implemented", "effective", "takes effect", "上路", "生效", "實施")):
        return "implemented"
    if any(token in text for token in (
            "confirmed", "announced", "approved", "公告", "核定", "通過", "證實")):
        return "confirmed"
    if any(token in text for token in (
            "rumor", "reportedly", "may", "considering", "傳聞", "擬", "可能", "研議")):
        return "rumor"
    return "confirmed" if event.get("source_grade") == "A" else "rumor"


def _event_timeline_key(event: dict) -> tuple[str, str]:
    """Use a stable lineage key across rumor, confirmation and implementation coverage."""
    entity = str(event.get("entity") or "").strip()
    event_type = str(event.get("event_type") or "general").strip() or "general"
    if not entity or event_type == "general":
        import hashlib
        cluster = "|".join(str(part) for part in _event_cluster_key(event))
        if not cluster.strip("|"):
            cluster = str(event.get("title") or event.get("summary") or "")
        digest = hashlib.sha1(cluster.encode("utf-8")).hexdigest()[:10]
        return entity or f"cluster:{digest}", event_type
    return entity, event_type


def apply_event_timeline(model_history: list[dict],
                         events: list[dict]) -> list[dict]:
    """Annotate incremental lifecycle transitions and suppress repeated event scoring."""
    previous: dict[tuple[str, str], str] = {}
    for record in sorted(model_history or [], key=lambda item: item.get("session_date", "")):
        for event in record.get("structured_events") or []:
            previous[_event_timeline_key(event)] = str(
                event.get("lifecycle") or _event_lifecycle(event))
    order = {"rumor": 1, "confirmed": 2, "implemented": 3, "withdrawn": 4}
    base_weight = {"rumor": 0.35, "confirmed": 1.0, "implemented": 0.55, "withdrawn": 1.0}
    transitions = {("rumor", "confirmed"): 0.65, ("confirmed", "implemented"): 0.45}
    output = []
    for raw in events or []:
        event = dict(raw)
        key = _event_timeline_key(event)
        status = _event_lifecycle(event)
        prior = previous.get(key)
        is_incremental = prior != status and (
            prior is None or status == "withdrawn"
            or order.get(status, 0) > order.get(prior, 0))
        event["lifecycle"] = status
        event["previous_lifecycle"] = prior
        event["timeline_key"] = "|".join(key)
        event["is_incremental"] = is_incremental
        event["lifecycle_weight"] = (
            transitions.get((prior, status), base_weight.get(status, 0.0))
            if is_incremental else 0.0
        )
        previous[key] = status if is_incremental or prior is None else prior
        output.append(event)
    return output


def _event_study_dedupe_key(row: dict, evidence: dict) -> tuple:
    event_type = str(evidence.get("event_type") or "")
    direction = int(_safe_number(evidence.get("direction")))
    code = str(row.get("code") or "")
    event_id = str(evidence.get("event_id") or "").strip()
    if event_id:
        return ("event_id", event_id, code, event_type, direction)
    timeline_key = str(evidence.get("timeline_key") or "").strip()
    if timeline_key:
        return ("timeline", timeline_key, code, event_type, direction)
    return (
        "fallback",
        str(row.get("session_date") or ""),
        code,
        event_type,
        direction,
        str(evidence.get("scope_company") or ""),
        str(evidence.get("scope_industry") or ""),
        str(evidence.get("scope_supply_chain") or ""),
        str(evidence.get("lifecycle") or ""),
        str(evidence.get("relation") or ""),
    )


def build_event_study(model_history: list[dict],
                      sessions: list[str],
                      horizon: int = 3) -> dict[tuple, dict]:
    """Estimate company, industry, supply-chain and global post-event excess returns."""
    grouped: dict[tuple, list[float]] = {}
    seen_events = set()
    rows = _purge_recent_rows(
        build_model_training_rows(model_history, sessions, horizon), sessions)
    for row in rows:
        for evidence in row.get("news_catalysts") or []:
            event_type = str(evidence.get("event_type") or "")
            direction = int(_safe_number(evidence.get("direction")))
            if event_type and direction:
                event_key = _event_study_dedupe_key(row, evidence)
                if event_key in seen_events:
                    continue
                seen_events.add(event_key)
                value = _safe_number(row.get("future_excess_pct"))
                keys = [
                    ("global", "", event_type, direction),
                    (event_type, direction),  # backward-compatible alias
                ]
                if evidence.get("scope_company"):
                    keys.append(("company", str(evidence["scope_company"]), event_type, direction))
                if evidence.get("scope_industry"):
                    keys.append(("industry", str(evidence["scope_industry"]), event_type, direction))
                if evidence.get("scope_supply_chain"):
                    keys.append(("supply_chain", str(evidence["scope_supply_chain"]), event_type, direction))
                for key in keys:
                    grouped.setdefault(key, []).append(value)
    output = {}
    for key, values in grouped.items():
        output[key] = {
            "samples": len(values),
            "avg_excess_pct": round(sum(values) / len(values), 4),
            "win_rate_pct": round(sum(value > 0 for value in values) / len(values) * 100, 1),
        }
    return output


def _shrunk_event_impact(event_study: dict[tuple, dict],
                         code: str,
                         industry: str,
                         supply_chain: str,
                         event_type: str,
                         direction: int) -> tuple[float, int, str]:
    """Shrink sparse company studies toward industry, supply-chain and global priors."""
    levels = [
        ("company", code, 10.0),
        ("industry", industry, 18.0),
        ("supply_chain", supply_chain, 18.0),
        ("global", "", 30.0),
    ]
    weighted, total_weight, samples, used = 0.0, 0.0, 0, []
    for scope, scope_id, prior_strength in levels:
        if scope != "global" and not scope_id:
            continue
        stats = event_study.get((scope, scope_id, event_type, direction)) or {}
        n = int(stats.get("samples", 0))
        if not n:
            continue
        weight = n / (n + prior_strength)
        weighted += _safe_number(stats.get("avg_excess_pct")) * weight
        total_weight += weight
        samples += n
        used.append(scope)
    if not total_weight:
        return 0.0, 0, "conservative_fallback"
    impact = max(-3.0, min(3.0, weighted / total_weight))
    return impact, samples, "hierarchical_event_study:" + "+".join(used)


def _stock_news_catalysts(snapshot: list[dict],
                          news: list[dict],
                          mops: list[dict],
                          events: Optional[list[dict]] = None,
                          event_study: Optional[dict[tuple[str, int], dict]] = None
                          ) -> dict[str, dict]:
    """Score clustered events; learn impact from event studies once labels exist."""
    import re as _re
    results = {
        str(item["code"]): {"score": 0.0, "evidence": []}
        for item in snapshot or [] if item.get("code")
    }
    events = events if events is not None else extract_structured_events(news, mops)
    event_study = event_study or {}

    stock_by_code = {str(item.get("code")): item for item in snapshot or []}

    def add(code: str, event: dict, relation: str, relation_weight: float) -> None:
        result = results.get(code)
        if result is None:
            return
        direction = int(_safe_number(event.get("direction")))
        if not direction:
            return
        stock = stock_by_code.get(code) or {}
        industry = str(stock.get("industry") or "")
        supply_chain = str(event.get("entity") or "") if "supply-chain" in relation else ""
        base, study_samples, score_method = _shrunk_event_impact(
            event_study, code, industry, supply_chain,
            str(event.get("event_type")), direction)
        if study_samples >= 5:
            delta = base * relation_weight
        else:
            base = {
                "guidance_raise": 3.0, "guidance_cut": -3.0, "orders": 2.0,
                "earnings": 2.0, "revenue_growth": 1.5, "export_controls": -2.0,
                "litigation": -1.5, "geopolitical": -1.5, "general": 1.0,
            }.get(str(event.get("event_type")), 1.0)
            delta = abs(base) * direction * relation_weight
            score_method = "conservative_fallback"
        surprise = _event_surprise_score(event)
        revenue_surprise = stock.get("rev_surprise_pct")
        if (event.get("event_type") == "revenue_growth"
                and isinstance(revenue_surprise, (int, float))):
            # Numeric surprise beats prose heuristics when a real consensus/proxy exists.
            surprise = round(max(0.1, min(1.0, 0.2 + abs(revenue_surprise) / 20)), 3)
        lifecycle_weight = _safe_number(event.get("lifecycle_weight"), 1.0)
        if lifecycle_weight <= 0:
            return
        delta *= (_safe_number(event.get("quality_score"), 0.5)
                  * (0.5 + surprise) * lifecycle_weight)
        result["score"] += delta
        result["evidence"].append({
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "relation": relation,
            "title": event.get("title"),
            "source": event.get("source"),
            "source_grade": event.get("source_grade"),
            "direction": direction,
            "score_method": score_method,
            "surprise_score": surprise,
            "revenue_surprise_pct": revenue_surprise,
            "lifecycle": event.get("lifecycle"),
            "lifecycle_weight": lifecycle_weight,
            "event_study_samples": study_samples,
            "scope_company": code,
            "scope_industry": industry,
            "scope_supply_chain": supply_chain,
            "score_delta": round(delta, 2),
        })

    for event in events:
        entity = str(event.get("entity") or "")
        title = str(event.get("title") or "")
        event_text = f"{title} {event.get('summary', '')}"
        direct_codes = set()
        for stock in snapshot or []:
            code = str(stock.get("code") or "")
            name = str(stock.get("name") or "")
            if (entity == code
                    or bool(code and _re.search(rf"(?<!\d){_re.escape(code)}(?!\d)", title))
                    or bool(len(name) >= 3 and name in title)):
                add(code, event, "direct", 1.0)
                direct_codes.add(code)
        for code in TW_SUPPLY_CHAIN_BY_US_LABEL.get(entity, set()):
            if code not in direct_codes:
                add(code, event, f"{entity} supply-chain", 0.35)
        for industry_key, mapping in TW_INDUSTRY_EVENT_MAP.items():
            terms = mapping.get("terms") or set()
            if not any(str(term) and str(term) in event_text for term in terms):
                continue
            for code in mapping.get("codes") or set():
                if code in direct_codes:
                    continue
                add(code, event, f"{industry_key} industry", 0.25)

    for result in results.values():
        result["score"] = round(max(-10.0, min(10.0, result["score"])), 2)
        result["evidence"] = sorted(
            result["evidence"],
            key=lambda evidence: abs(_safe_number(evidence.get("score_delta"))),
            reverse=True,
        )[:4]
    return results


# ── Conformal 區間校準(借鏡 Angelopoulos "Conformal PID Control",MIT;inline ~單一純量更新)──
# 80% 區間實際命中率 < 80%(台股肥尾/跳空常見)→ 把 band 加寬;> 80% → 收窄。
# P-control:q_{t+1} = clamp(q_t + η·(目標覆蓋 − 實際覆蓋)/100);q 為 band 的加性調整(%)。
CONFORMAL_STATE_FILE = Path("state/conformal_intervals.json")
CONFORMAL_TARGET_COV = 80.0
CONFORMAL_LR = 2.0
CONFORMAL_Q_LO, CONFORMAL_Q_HI = -2.0, 6.0


def _load_conformal_state() -> dict:
    try:
        if CONFORMAL_STATE_FILE.exists():
            data = json.loads(CONFORMAL_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):          # JSON 合法但非 dict → 視為無狀態
                return data
    except Exception:
        pass
    return {}


def _save_conformal_state(state: dict) -> None:
    try:
        CONFORMAL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFORMAL_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[conformal] 寫入失敗(不影響晨報): {e}", file=sys.stderr)


def _update_conformal_q(prev_q: float, coverage_pct) -> float:
    """P-control 一步更新:覆蓋率不足→q 增(加寬);過高→q 減(收窄)。缺/壞覆蓋率→不動。"""
    cov = _safe_number(coverage_pct, None)        # 壞值 → None(不當成 0% 覆蓋而暴衝)
    if cov is None:
        return prev_q
    q = prev_q + CONFORMAL_LR * (CONFORMAL_TARGET_COV - cov) / 100.0
    return max(CONFORMAL_Q_LO, min(CONFORMAL_Q_HI, q))


def compute_conformal_adjustments(walk_forward: Optional[dict], save: bool = True) -> dict:
    """每日一次:讀上次 q、依 walk-forward 各 horizon 的 interval_coverage_pct 更新、(非 DRY_RUN 才)存回。
    回 {forecast_key: q_pct}(加到 80% band 的加性調整)。"""
    prev = _load_conformal_state()
    out = {}
    for key in MODEL_TARGETS:
        cov = ((walk_forward or {}).get(key) or {}).get("interval_coverage_pct")
        prev_q = _safe_number(prev.get(key), 0.0)      # 壞 state → 0(fail-safe)
        out[key] = round(_update_conformal_q(prev_q, cov), 3)
    if save and os.environ.get("DRY_RUN") != "1":
        _save_conformal_state(out)
    return out


def calc_stock_price_forecast(entry: dict,
                              evaluation: Optional[dict[int, dict]] = None,
                              model_predictions: Optional[dict[int, dict]] = None,
                              regime: str = "neutral",
                              model_monitoring: Optional[dict] = None,
                              conformal_adj: Optional[dict] = None) -> dict:
    """
    產生個股 3 日 / 5 日保守點預測與 80% 波動區間。

    點預測使用收縮後的 5 日動能、關注分數、新聞催化與已累積的歷史偏誤；
    區間使用近 20 日波動度。這是可回測的啟發式預測，不保證達標。
    """
    close = safe_float(entry.get("close"))
    daily_vol = safe_float(entry.get("daily_vol_pct"))
    if not close or not daily_vol:
        return {"error": "近 20 日價格資料不足"}
    attention_score = float(entry.get("attention_score") or 0)
    news_score = float(entry.get("news_catalyst_score") or 0)
    momentum_daily = float(entry.get("pct_5d") or 0) / 5.0
    evaluation = evaluation or {}
    model_predictions = model_predictions or {}
    monitor_status = (model_monitoring or {}).get("status", "ok")
    monitor_penalty = _safe_number((model_monitoring or {}).get("ranking_penalty"))
    monitor_band_multiplier = (
        1.55 if monitor_status == "error"
        else 1.3 if monitor_status == "fallback" or monitor_penalty >= 2.0
        else 1.0
    )
    regime_weight = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["neutral"])["model"]
    forecasts = {}
    forecast_specs = (
        ("1d_open", 1, "隔日開盤"),
        ("1d_close", 1, "隔日收盤"),
        ("3d", 3, "3日收盤"),
        ("5d", 5, "5日收盤"),
    )
    for forecast_key, horizon, label in forecast_specs:
        learned = evaluation.get(horizon) or {}
        learned_bias = (
            float(learned.get("forecast_bias_pct") or 0)
            if learned.get("forecast_samples", 0) >= 5 else 0.0
        )
        score_tilt = ((attention_score - 50.0) / 50.0) * daily_vol * 0.20
        # 新聞 tilt 0.30→0.10:同 _attention_ranking_breakdown 的 IC 依據(新聞股短線負超額)
        news_tilt = (news_score / 10.0) * daily_vol * 0.10
        heuristic_return = (
            momentum_daily * horizon * 0.25
            + (score_tilt + news_tilt) * (horizon ** 0.5)
            + max(-2.0, min(2.0, learned_bias))
        )
        model = model_predictions.get(forecast_key) or {}
        model_return = model.get("expected_return_pct")
        expected_return = (
            heuristic_return if model_return is None
            else _safe_number(model_return) * regime_weight
                 + heuristic_return * (1 - regime_weight)
        )
        expected_return = max(-12.0, min(12.0, expected_return))
        quantile_lower = model.get("quantile_lower_pct")
        quantile_upper = model.get("quantile_upper_pct")
        band = max(1.5, min(15.0, daily_vol * (horizon ** 0.5) * 1.28))
        adjusted_band = min(22.0, band * monitor_band_multiplier)
        lower_return = (
            _safe_number(quantile_lower) if quantile_lower is not None
            else expected_return - adjusted_band
        )
        upper_return = (
            _safe_number(quantile_upper) if quantile_upper is not None
            else expected_return + adjusted_band
        )
        if monitor_band_multiplier > 1.0 and quantile_lower is not None and quantile_upper is not None:
            spread = max(0.0, upper_return - lower_return)
            extra = min(8.0, spread * (monitor_band_multiplier - 1.0) / 2.0)
            lower_return -= extra
            upper_return += extra
        # Conformal-PID 校準:對稱套用到「實際」lower/upper(quantile 與啟發式兩條路徑都生效,
        # 確保覆蓋率回饋迴路真正閉合;不足→加寬、過高→收窄)。
        q_adj = _safe_number((conformal_adj or {}).get(forecast_key), 0.0)
        lower_return -= q_adj
        upper_return += q_adj
        if lower_return > upper_return:
            lower_return, upper_return = upper_return, lower_return
        lower_return = min(lower_return, expected_return)
        upper_return = max(upper_return, expected_return)
        expected_price = close * (1 + expected_return / 100)
        forecasts[forecast_key] = {
            "label": label,
            "horizon_days": horizon,
            "expected_price": round(expected_price, 2),
            "expected_return_pct": round(expected_return, 2),
            "lower": round(close * (1 + lower_return / 100), 2),
            "upper": round(close * (1 + upper_return / 100), 2),
            "interval_pct": round((upper_return - lower_return) / 2.0, 2),   # 實際半寬(含 conformal)
            "conformal_adj_pct": round(q_adj, 2),
            "beat_market_probability": model.get("beat_market_probability"),
            "model_method": model.get("method", "heuristic fallback"),
            "quality": {
                "model_version": model.get("model_version", MODEL_VERSION),
                "training_rows": model.get("training_rows", 0),
                "recent_direction_hit_pct": model.get("recent_direction_hit_pct"),
                "probability_calibrated": bool(model.get("probability_calibrated")),
                "fallback_enabled": model.get("fallback_enabled", True),
                "model_monitoring_status": monitor_status,
                "model_monitoring_penalty": round(monitor_penalty, 2),
                "interval_method": (
                    "quantile regression"
                    if quantile_lower is not None and quantile_upper is not None
                    else "volatility fallback"
                ),
            },
        }
    samples = sum((evaluation.get(h) or {}).get("forecast_samples", 0) for h in (3, 5))
    if samples >= 30 and attention_score >= 60:
        confidence = "中"
    elif attention_score >= 50 and daily_vol <= 4:
        confidence = "中低"
    else:
        confidence = "低"
    return {
        "method": "收縮動能 + 結構分數 + 已驗證新聞催化 + 歷史偏誤",
        "regime": regime,
        "confidence": (
            "\u4f4e" if monitor_status == "error"
            else "\u4e2d" if monitor_status == "fallback"
            and samples >= 30 and attention_score >= 60
            else confidence
        ),
        **forecasts,
    }


def _overheat_penalty(item: dict) -> float:
    """Penalize crowded short-term moves so Top5 is not just a chase list."""
    penalty = 0.0
    pct_5d = _safe_number(item.get("pct_5d"))
    ma20_dist = _safe_number(item.get("ma20_dist_pct"))
    day_pct = _safe_number(item.get("day_pct"))
    vol_ratio = _safe_number(item.get("vol_ratio_20d"))
    daily_vol = _safe_number(item.get("daily_vol_pct"))
    if pct_5d >= 18:
        penalty += min(4.0, (pct_5d - 18) / 5.0)
    if ma20_dist >= 12:
        penalty += min(3.0, (ma20_dist - 12) / 4.0)
    if day_pct >= 8 and 0 < vol_ratio < 0.8:
        penalty += 2.0
    if daily_vol >= 8:
        penalty += min(2.0, (daily_vol - 8) / 2.0)
    return round(min(8.0, penalty), 2)


def _attention_ranking_breakdown(item: dict,
                                 model3: dict,
                                 weights: dict,
                                 suppress_model: bool = False) -> dict:
    """Build a transparent, bounded 0-100 ranking score for the Taiwan watchlist.

    suppress_model=True(熔斷):rolling-origin 回測顯示 Top5 淨報酬為負時,
    ML 組件(beat_market / expected_return)不可信 → 歸零,排名降級為純結構觀察。
    """
    base_score = _safe_number((item.get("breakout") or {}).get("score"))
    news_score = max(-10.0, min(10.0, _safe_number(item.get("news_catalyst_score"))))
    industry_z = max(-2.0, min(2.0, _safe_number(item.get("industry_neutral_score"))))
    probability = None if suppress_model else model3.get("beat_market_probability")
    expected_return = None if suppress_model else model3.get("expected_return_pct")

    components = {
        # calc_breakout_score tops out at 90: chips 35 + momentum 25 + revenue 20 + EPS 10.
        "structure": base_score / 90.0 * 70.0 * _safe_number(weights.get("structure"), 1.0),
        # 新聞分維持降權 0.3:IC 回測(backtest_data/ic_news_score.py,2026-06 重跑)顯示
        # 1d IC≈-0.064、IC_IR=-1.0、「有新聞」股票次日平均 -0.86%(p=0.076,尚不顯著);
        # 3d 殘差版 IC≈-0.081(p=0.016)。整體仍為「注意力效應、新聞股短線易追高」的負向,
        # 但統計力不足(model_history 目前僅 ~6-9 場真正含 news_catalyst_score 欄位)。
        # 因此維持降權不全刪、也不自動調權;待 live 累積 ≥30 場後重跑腳本再決定去留。
        "news_event": news_score * 0.3 * _safe_number(weights.get("news"), 1.0),
        "industry_neutral": industry_z * 3.0,
        "beat_market": (
            (_safe_number(probability, 0.5) - 0.5) * 20.0
            * _safe_number(weights.get("model"), 1.0)
            if probability is not None else 0.0
        ),
        "expected_return": (
            max(-6.0, min(6.0, _safe_number(expected_return)))
            * _safe_number(weights.get("model"), 1.0)
            if expected_return is not None else 0.0
        ),
        "quality_penalty": (
            # 熔斷時 ML 已不計分,不再以「模型品質」差異化懲罰個股(避免引入模型雜訊)
            0.0 if suppress_model
            else -4.0 if model3.get("fallback_enabled", True)
            else -1.0 if not model3.get("probability_calibrated") else 0.0
        ),
        "liquidity_penalty": (
            -4.0 if item.get("liquidity_eligible") is False
            else -min(2.0, _safe_number(item.get("slippage_bps")) / 40.0)
            if item.get("slippage_bps") is not None else 0.0
        ),
        "feature_drift_penalty": -max(
            0.0, min(4.0, _safe_number(item.get("feature_drift_penalty")))),
        "source_health_penalty": -max(
            0.0, min(4.0, _safe_number(item.get("source_health_penalty")))),
        "model_monitor_penalty": -max(
            0.0, min(4.0, _safe_number(item.get("model_monitor_penalty")))),
        "overheat_penalty": -_overheat_penalty(item),
    }
    components = {key: round(value, 2) for key, value in components.items()}
    raw_score = round(sum(components.values()), 2)
    return {
        "score": round(max(0.0, min(100.0, raw_score)), 2),
        "raw_score": raw_score,
        "components": components,
        "inputs": {
            "base_score": round(base_score, 2),
            "news_catalyst_score": round(news_score, 2),
            "industry_neutral_z": round(industry_z, 3),
            "beat_market_probability": probability,
            "expected_return_3d_pct": expected_return,
            "trade_value": item.get("trade_value"),
            "slippage_bps": item.get("slippage_bps"),
            "overheat_penalty": _overheat_penalty(item),
            "market_regime": item.get("market_regime") or "neutral",
            "model_version": model3.get("model_version", MODEL_VERSION),
        },
    }


def enrich_stock_attention_candidates(snapshot: list[dict],
                                      news: list[dict],
                                      mops: list[dict],
                                      history: list[dict],
                                      target_session_date: str,
                                      model_history: Optional[list[dict]] = None,
                                      sessions: Optional[list[str]] = None,
                                      quotes: Optional[dict] = None,
                                      structured_events: Optional[list[dict]] = None,
                                      feature_drift: Optional[dict] = None,
                                      source_health: Optional[dict] = None,
                                      model_monitoring: Optional[dict] = None,
                                      ) -> list[dict]:
    """將新聞催化、最終關注分數與可回測價格預測加入台股快照。"""
    evaluation = evaluate_breakout_forecasts(
        history, snapshot, target_session_date, sessions=sessions)
    model_history = model_history or []
    sessions = sessions or []
    regime = _market_regime(quotes or {})
    event_study = build_event_study(model_history, sessions) if sessions else {}
    catalysts = _stock_news_catalysts(
        snapshot, news, mops, events=structured_events, event_study=event_study)
    predictions = {
        forecast_key: _model_predictions(
            model_history, sessions, snapshot,
            config["horizon"], config["target"], forecast_key, regime)
        for forecast_key, config in MODEL_TARGETS.items()
    } if sessions else {forecast_key: {} for forecast_key in MODEL_TARGETS}
    weights = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["neutral"])
    # Conformal-PID:依 walk-forward 區間命中率,每日更新 band 加性校準(一次/run)
    conformal_adj = compute_conformal_adjustments((quotes or {}).get("MODEL_WALK_FORWARD") or {})
    for item in snapshot or []:
        item["feature_drift_penalty"] = _safe_number((feature_drift or {}).get("penalty"))
        item["source_health_penalty"] = _safe_number((source_health or {}).get("ranking_penalty"))
        item["model_monitor_penalty"] = _safe_number((model_monitoring or {}).get("ranking_penalty"))
        catalyst = catalysts.get(item.get("code"), {})
        base_score = float((item.get("breakout") or {}).get("score", 0))
        news_score = float(catalyst.get("score", 0))
        item["news_catalyst_score"] = news_score
        item["news_catalysts"] = catalyst.get("evidence", [])
        item["attention_score_raw"] = round(max(
            0.0, min(100.0, base_score * weights["structure"] + news_score * weights["news"])), 2)
    neutral_scores = _industry_neutral_scores(snapshot, "attention_score_raw")
    for item in snapshot or []:
        code = str(item.get("code") or "")
        item["industry_neutral_score"] = neutral_scores.get(code, 0.0)
        model3 = (predictions.get("3d") or {}).get(code) or {}
        item["market_regime"] = regime
        ranking = _attention_ranking_breakdown(
            item, model3, weights,
            suppress_model=bool((model_monitoring or {}).get("suppress_ranking")))
        item["ranking_score"] = ranking["score"]
        item["ranking_score_raw"] = ranking["raw_score"]
        item["ranking_components"] = ranking["components"]
        item["ranking_inputs"] = ranking["inputs"]
        # Backward-compatible alias used by existing snapshots and templates.
        item["attention_score"] = item["ranking_score"]
        item["price_forecast"] = calc_stock_price_forecast(
            item,
            evaluation,
            {forecast_key: (predictions.get(forecast_key) or {}).get(code, {})
             for forecast_key in MODEL_TARGETS},
            regime,
            model_monitoring,
            conformal_adj=conformal_adj,
        )
    return snapshot


def _rank_attention_candidates(snapshot: list[dict]) -> list[dict]:
    """排序五檔候選；營收明顯衰退且沒有正面催化者先排除。"""
    eligible = []
    for item in snapshot or []:
        score = item.get("ranking_score", item.get(
            "attention_score", (item.get("breakout") or {}).get("score", 0)))
        yoy = item.get("rev_yoy_pct")
        if not score or score <= 0:
            continue
        if item.get("liquidity_eligible") is False:
            continue
        if isinstance(yoy, (int, float)) and yoy < -15 and item.get("news_catalyst_score", 0) <= 0:
            continue
        eligible.append(item)
    ranked = sorted(
        eligible,
        key=lambda item: (
            -_safe_number(item.get("ranking_score", item.get("attention_score"))),
            -_safe_number((item.get("breakout") or {}).get("score")),
            str(item.get("code") or ""),
        ),
    )
    for rank, item in enumerate(ranked, 1):
        item["attention_rank"] = rank
    return ranked


def _breakout_candidates_for_state(snapshot: list[dict], limit: int = 5) -> list[dict]:
    """保存每日啟發式排序候選，累積未來可用的 3 日 / 5 日實證。"""
    ranked = _rank_attention_candidates(snapshot)
    return [{
        "code": item.get("code"),
        "name": item.get("name"),
        "score": (item.get("breakout") or {}).get("score", 0),
        "attention_score": item.get("attention_score"),
        "ranking_score": item.get("ranking_score"),
        "ranking_components": item.get("ranking_components"),
        "attention_rank": item.get("attention_rank"),
        "news_catalyst_score": item.get("news_catalyst_score"),
        "close": item.get("close"),
        "price_forecast": item.get("price_forecast"),
    } for item in ranked[:limit] if item.get("code") and item.get("close")]


def _foreign_top10_total(snapshot: list[dict]) -> Optional[float]:
    """計算市值前 10 大外資合計；市值資料不完整時不冒充有效訊號。"""
    ranked = sorted(
        snapshot or [], key=lambda item: item.get("market_cap") or 0, reverse=True)
    top10 = ranked[:10]
    if len(top10) < 10 or any(not item.get("market_cap") for item in top10):
        return None
    return round(sum(item.get("foreign_lot", 0) for item in top10), 0)


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

    # 2.5 系統性風險:Absorption Ratio 標準化偏移(類股相關結構快速壓縮 = 脆弱早警;只在偏高時出現)
    absorp = quotes.get("ABSORPTION") or {}
    z = absorp.get("ar_shift_z")
    if z is not None and absorp.get("fragile"):
        alerts.append({
            "level": "red" if absorp.get("severe") else "orange",
            "title": "系統性風險上升（類股相關結構壓縮）",
            "detail": (f"Absorption Ratio 近期標準化偏移 +{z:.1f}σ"
                       f"(AR={absorp.get('ar')}、{absorp.get('n_assets')}檔取前{absorp.get('n_factors')}主成分)。"
                       f"前幾主成分吃下的變異佔比快速上升 → 全市場連動性增強、分散避險效果下降,"
                       f"小衝擊易全面擴散;Kritzman 實證此訊號常領先大盤回檔,操作宜降槓桿、提防多殺多。"),
        })

    # 3. 外資台指期淨空 —— 看「方向(日變化)+ 現貨對照」而非只看「水位」。
    #    重要:外資現貨大買時的期貨淨空多為「避險」,不是看空(故大淨空也可能上漲);
    #    只有空單『較前日新增』且現貨同步調節,才是真正的偏空壓力。
    foreign_oi = taifex_oi.get("foreign_oi_net")
    oi_chg = taifex_oi.get("foreign_oi_chg")            # 日變化(口),負=空單增加
    spot_net = taifex_oi.get("foreign_spot_net_lot")    # 外資現貨買超合計(張)
    if foreign_oi is not None:
        if foreign_oi < -20000:
            chg_str = f"、較前日 {oi_chg:+,.0f} 口" if isinstance(oi_chg, (int, float)) else ""
            increasing = isinstance(oi_chg, (int, float)) and oi_chg <= -5000   # 空單明顯增加
            hedge = isinstance(spot_net, (int, float)) and spot_net > 3000        # 現貨明顯買超
            if increasing and not hedge:
                alerts.append({
                    "level": "red",
                    "title": "外資台指期淨空『再增』(實空壓)",
                    "detail": (f"外資台指期未平倉 {foreign_oi:+,} 口{chg_str}(空單較前日新增),"
                               f"且現貨未見明顯買超 → 偏空壓力較實,今日易開低或盤中下殺。"),
                })
            elif hedge:
                alerts.append({
                    "level": "yellow",
                    "title": "外資台指期淨空(多為避險,方向參考性低)",
                    "detail": (f"外資台指期未平倉 {foreign_oi:+,} 口{chg_str},但外資現貨同步買超 "
                               f"{spot_net:+,.0f} 張 → 期貨淨空多為『避險現貨多單』而非看空,"
                               f"不宜單憑此判定開低(近期同樣大淨空但台股上漲即為此故)。"),
                })
            else:
                alerts.append({
                    "level": "orange",
                    "title": "外資台指期淨空(既有部位,非新增壓力)",
                    "detail": (f"外資台指期未平倉 {foreign_oi:+,} 口{chg_str},水位雖大但"
                               f"{'大致持平' if isinstance(oi_chg,(int,float)) else '日變化不明'}"
                               f" → 屬既有空單,方向訊號偏弱,僅供參考。"),
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
        # 抓近 7 個交易日實際開盤。量 > 0 過濾:排除 yfinance 在颱風臨時休市日回傳的
        # 假持平 bar(開=收=前收、量 0),否則「不存在的開盤」會以 0.00% 誤差進入
        # MAE/bias 統計,把自我校正帶偏(理由詳 backfill_actual_opens)。
        def _hist_open(sym):
            h = yf.Ticker(sym).history(period="10d", auto_adjust=False).dropna(subset=["Open"])
            if "Volume" in h.columns:
                h = h[h["Volume"] > 0]
            return h
        tw2330_hist = _hist_open("2330.TW")
        tw0066_hist = _hist_open("00662.TW")
        tw0050_hist = _hist_open("0050.TW")

        def to_date(idx):
            return idx.tz_localize(None).strftime("%Y-%m-%d") if idx.tz else idx.strftime("%Y-%m-%d")

        # 同步把 Yahoo 的 float64 精度雜訊（如 117.55000305175781）round 掉
        tw2330_opens = {to_date(d): round(float(v), 2) for d, v in tw2330_hist["Open"].items()}
        tw0066_opens = {to_date(d): round(float(v), 2) for d, v in tw0066_hist["Open"].items()}
        tw0050_opens = {to_date(d): round(float(v), 2) for d, v in tw0050_hist["Open"].items()}

        # target_session_date 是預測真正對應的台股開盤日。只納入今天以前已成熟的
        # 實際開盤，並依實際交易日去重，避免週六 / 週一或國定假日重複計分。
        today = dt.datetime.now(TPE).strftime("%Y-%m-%d")
        recent_hist = _resolved_prediction_history(
            history, tw2330_opens, before_date=today)[-BACKTEST_DISPLAY_DAYS:]
        err_2330_list = []
        err_00662_list = []
        err_0050_list = []
        for next_date, h in recent_hist:

            # 回溯要衡量「實際拿來預測的值」= weighted_final;舊紀錄沒存才退回 model3。
            # 否則回溯準確度衡量的是 ADR 衰減模型(model3)、與報告實際給的預測不一致。
            pred_2330 = h.get("weighted_final_2330") or h.get("model3_2330")
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
            if err_2330 is not None:
                err_2330_list.append(err_2330)
            if err_00662 is not None:
                err_00662_list.append(err_00662)
            if err_0050 is not None:
                err_0050_list.append(err_0050)

            if any(e is not None for e in (err_2330, err_00662, err_0050)):
                e2330 = f"2330: 預測 {pred_2330} → 實際 {actual_2330} ({err_2330:+.2f}%)" if err_2330 is not None else "2330: 缺資料"
                e00662 = f"00662: 預測 {pred_00662} → 實際 {actual_00662} ({err_00662:+.2f}%)" if err_00662 is not None else "00662: 缺資料"
                e0050 = f"0050: 預測 {pred_0050} → 實際 {actual_0050} ({err_0050:+.2f}%)" if err_0050 is not None else "0050: 缺資料"
                rows.append(f"  {next_date}：{e2330} | {e00662} | {e0050}")

        if not rows:
            return "（歷史資料尚未對齊，需再多 1-2 天累積）"

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


def _fetch_open_map(symbol: str) -> dict:
    """抓單一標的近 3 月「開盤價」對照表 {YYYY-MM-DD: open}。供自我校正比對用。"""
    d = yf.Ticker(symbol).history(period="3mo", auto_adjust=False)
    d = d.dropna(subset=["Open"])
    out: dict[str, float] = {}
    for idx, v in d["Open"].items():
        key = (idx.tz_localize(None) if getattr(idx, "tz", None) else idx
               ).strftime("%Y-%m-%d")
        # round 掉 Yahoo float64 精度雜訊（曾出現 117.55000305175781 這種值）
        out[key] = round(float(v), 2)
    return out


def _ewm_bias(errors: list, recent_n: int = 20, span: int = 8) -> tuple[float, int]:
    """
    指數加權平均偏誤(近期權重高),取代等權平均——關鍵修正:
    在「加速上漲」的盤勢,等權近 20 日會被早期平靜日稀釋,導致校正落後、長期偏低。
    EMA(span=8)讓最近約 1 週的偏誤主導,校正能更快跟上趨勢。

    errors: (實際−預測)/預測 的序列(舊→新)。回傳 (加權偏誤, 樣本數)。
    """
    r = errors[-recent_n:]
    n = len(r)
    if n == 0:
        return 0.0, 0
    alpha = 2.0 / (span + 1)
    num = 0.0
    den = 0.0
    for i, x in enumerate(r):                 # i=0 最舊, i=n-1 最新
        w = (1.0 - alpha) ** (n - 1 - i)      # 最新權重=1,往前指數衰減
        num += w * x
        den += w
    return (num / den if den else 0.0), n


def calibrate_predictions(fair: dict, predictions: dict, taiex_pred: dict,
                          history: list[dict],
                          min_samples: int = 5, recent_n: int = 20,
                          max_bias: float = 0.03, ewm_span: int = 8) -> tuple[dict, dict, dict]:
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
        twii_o = _fetch_open_map("^TWII")
        t2330_o = _fetch_open_map("2330.TW")
        t00662_o = _fetch_open_map("00662.TW")
    except Exception as e:
        print(f"[calib] 抓實際開盤失敗，跳過校正: {e}", file=sys.stderr)
        _mark_unapplied(f"無法取得實際開盤：{e}")
        return fair, predictions, taiex_pred

    # 收集相對誤差 (實際 − 預測) / 預測
    err: dict[str, list] = {"00662": [], "2330_final": [],
                            "m1": [], "m2": [], "m3": [], "m4": [], "taiex": []}
    today = dt.datetime.now(TPE).strftime("%Y-%m-%d")
    resolved_hist = _resolved_prediction_history(history, t2330_o, before_date=today)
    for open_date, h in resolved_hist:
        # corporate action 的調整品質依 Yahoo 配息資料而定，不拿來學 bias。
        if h.get("ex_div_today"):
            continue
        a662 = t00662_o.get(open_date)
        a2330 = t2330_o.get(open_date)
        atwii = twii_o.get(open_date)
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

    def _mae(lst: list) -> tuple[Optional[float], int]:
        r = lst[-recent_n:]
        return (sum(abs(x) for x in r) / len(r), len(r)) if r else (None, 0)

    def _apply_bias(obj: dict, value_key: str, err_key: str, label: str,
                    baseline: Optional[float] = None) -> dict:
        # EMA 加權偏誤(近期主導),取代等權平均 → 趨勢盤校正不落後
        bias, n = _ewm_bias(err[err_key], recent_n, ewm_span)
        if n < min_samples:
            return {"applied": False, "samples": n,
                    "reason": f"{label} 誤差樣本僅 {n} 筆（需 ≥ {min_samples}）"}
        raw = obj.get(value_key)
        if raw is None:
            return {"applied": False, "samples": n, "reason": f"{label} 無原始值"}
        b = max(-max_bias, min(bias, max_bias))
        corrected = raw * (1 + b)
        # 方向翻轉防護:bias 是近期殘差(regime 轉折時為 stale)。若套用後讓「相對昨收的
        # 漲跌方向」翻轉(例:原始偏空 -0.11% 被 +0.5pp stale 多頭偏移翻成 +0.42%),
        # 夾到中性(=昨收),只允許 bias 把預測往中性拉、不可反轉方向。只在真翻轉日生效。
        flip_guarded = False
        if baseline and baseline > 0:
            if (raw - baseline) * (corrected - baseline) < 0:
                corrected = baseline
                flip_guarded = True
        obj[f"{value_key}_raw"] = raw
        obj[value_key] = round(corrected, 2)
        return {"applied": True, "bias_pct": round(b * 100, 3),
                "samples": n, "raw": raw, "flip_guarded": flip_guarded}

    # ---- (A) 2330 四模型 MAE 反比加權（model1/2/3 + model4 momentum） ----
    if isinstance(predictions, dict) and not predictions.get("error"):
        m3 = predictions.get("model3_adr_decay")
        mae1, n1 = _mae(err["m1"])
        mae2, n2 = _mae(err["m2"])
        mae3, n3 = _mae(err["m3"])
        mae4, n4 = _mae(err["m4"])
        # 2021-2026 共 500 個交易日 rolling-origin 回測:純 model3(OLS decay) MAE 0.940%
        # < 四模型中位數 0.946% < MAE 反比加權 0.972%(且加權版方向命中 -1.34pp)。
        # → 停用 MAE 反比加權,weighted_final 直接採 model3;model3 缺值才退回中位數。
        if m3 is not None:
            predictions["weighted_final"] = m3
            predictions["final_method"] = "純 ADR 衰減 model3(500 日回測選定)"
        else:
            predictions["weighted_final"] = predictions.get("mid")
            predictions["final_method"] = "等權中位數（model3 缺值退化）"
        predictions["model_mae_pct"] = {
            "model1": round(mae1 * 100, 3) if mae1 else None,
            "model2": round(mae2 * 100, 3) if mae2 else None,
            "model3": round(mae3 * 100, 3) if mae3 else None,
            "model4": round(mae4 * 100, 3) if mae4 else None,
        }
        # ---- (B) bias 修正 2330(帶昨收做方向翻轉防護)----
        predictions["calibration"] = _apply_bias(
            predictions, "weighted_final", "2330_final", "2330",
            baseline=predictions.get("last_2330"))
        # mid 同步成校正後最終值，讓既有 render 卡片直接反映
        predictions["mid_raw"] = predictions.get("mid")
        predictions["mid"] = predictions["weighted_final"]

    # ---- (B) bias 修正 00662(帶昨收做方向翻轉防護)----
    if isinstance(fair, dict) and not fair.get("error"):
        cal = _apply_bias(fair, "fair_price", "00662", "00662",
                          baseline=fair.get("last_00662_price"))
        if cal.get("applied") and fair.get("last_00662_price"):
            fair["implied_change_pct"] = round(
                (fair["fair_price"] / fair["last_00662_price"] - 1) * 100, 2)
        fair["calibration"] = cal

    # ---- (B) bias 修正 加權指數(帶昨收做方向翻轉防護)----
    if isinstance(taiex_pred, dict) and not taiex_pred.get("error"):
        taiex_pred["calibration"] = _apply_bias(
            taiex_pred, "pred_open", "taiex", "加權指數",
            baseline=taiex_pred.get("last_close"))
        # 累積足夠樣本後，以 walk-forward 絕對殘差 90% 分位建立參考區間。
        # 這比「三訊號彼此很接近」可靠：訊號可能一致但同時判錯方向。
        recent_residuals = err["taiex"][-recent_n:]
        if len(recent_residuals) >= min_samples and taiex_pred.get("pred_open"):
            band = float(pd.Series([abs(x) for x in recent_residuals]).quantile(0.90))
            center = taiex_pred["pred_open"]
            taiex_pred["ci_lower"] = round(center * (1 - band), 2)
            taiex_pred["ci_upper"] = round(center * (1 + band), 2)
            taiex_pred["interval_method"] = (
                f"walk-forward 絕對殘差 90% 分位（n={len(recent_residuals)}）")

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
        recent = _normalize_history_entries(
            [d for d in data if isinstance(d, dict) and d.get("date", "") >= cutoff])
        print(f"[state] 載入歷史記憶 {len(recent)} 筆（過去 {days} 天）")
        return recent
    except Exception as e:
        print(f"[state] 載入失敗: {e}", file=sys.stderr)
        return []


def backfill_actual_opens(history: list[dict]) -> int:
    """把已成熟交易日的『實際開盤』寫回歷史記錄。

    晨報於開盤前產生,當下拿不到「今日實際開盤」;但**過去**預測對應的開盤日早已成熟。
    這裡為每筆 target_session_date 已過的記錄補上 actual_open_2330/00662/0050/taiex,
    讓 (訊號 + 預測 + 實際) 成為自含資料集 → 預測回測可離線重現、且是誠實的 live 紀錄。
    純資料記錄,不影響當日預測或信件內容。回傳補了幾個欄位。
    """
    if not history:
        return 0
    try:
        def _ohlc(sym: str, require_volume: bool = False) -> tuple[dict, dict]:
            h = yf.Ticker(sym).history(period="1mo", auto_adjust=False).dropna(subset=["Open"])
            # 個股/ETF 要求成交量 > 0:yfinance 在台股「臨時休市日」(颱風)會回一根
            # 開=收=前收、量 0 的假持平 bar(Yahoo 行事曆不知道臨時停市)。不濾掉會把
            # 「不存在的開盤」寫進 history → 回顧表出現 +0.00% 幽靈列、且污染 MAE/bias
            # 自我校正(讓校正以為預測完美)。2026-07-10 颱風日實際發生。
            # ^TWII 指數的量值語意不同(可為 0/NaN),不套此濾(實測指數源不產生假 bar)。
            if require_volume and "Volume" in h.columns:
                h = h[h["Volume"] > 0]

            def _to_date(idx):
                return idx.tz_localize(None).strftime("%Y-%m-%d") if idx.tz else idx.strftime("%Y-%m-%d")
            opens = {_to_date(d): round(float(v), 2) for d, v in h["Open"].items()}
            closes = {_to_date(d): round(float(v), 2) for d, v in h["Close"].items()}
            return opens, closes

        opens_2330, _ = _ohlc("2330.TW", require_volume=True)
        opens_00662, _ = _ohlc("00662.TW", require_volume=True)
        opens_0050, _ = _ohlc("0050.TW", require_volume=True)
        opens_taiex, closes_taiex = _ohlc("^TWII")
        series = {
            "actual_open_2330": opens_2330,
            "actual_open_00662": opens_00662,
            "actual_open_0050": opens_0050,
            "actual_open_taiex": opens_taiex,
        }
        taiex_dates = sorted(closes_taiex)
    except Exception as e:
        print(f"[backfill] 實際開盤回填略過(取數失敗): {e}", file=sys.stderr)
        return 0

    today = dt.datetime.now(TPE).strftime("%Y-%m-%d")
    # 自癒視窗:逐欄位用「該標的自己」抓到的最早日期當下限,且空地圖不做任何刪除——
    # 不能用跨標的全域下限:某一檔 yfinance 回空/被截短(無例外)時,會拿別檔的視窗
    # 當授權、把這檔在視窗內的合法回填全數誤刪(Codex review P2)。視窗外的舊紀錄不動
    # (yfinance 只回 1 個月,更早的合法回填不能因為不在本次 map 就被誤刪)。
    _floor_by_field = {f: (min(m) if m else None) for f, m in series.items()}
    # 刪除的第二道佐證:該日也不在 ^TWII 的 session 地圖(= 大盤當天確實沒交易)。
    # 若大盤有交易而單一標的地圖缺此日,那是 Yahoo 對該檔的漏抓,不是休市 → 不得刪
    # (Codex review 第二輪:單檔視窗「中間」漏抓仍會誤刪合法值)。^TWII 空則一律不刪。
    _taiex_floor = min(opens_taiex) if opens_taiex else None
    filled = 0
    for rec in history:
        tgt = rec.get("target_session_date")
        if not tgt or tgt >= today:        # 只回填已成熟(過去)的交易日
            continue
        for field, omap in series.items():
            if field not in rec and tgt in omap:
                rec[field] = omap[tgt]
                filled += 1
            elif (field != "actual_open_taiex" and field in rec and omap
                  and _floor_by_field[field] <= tgt and tgt not in omap
                  and _taiex_floor and _taiex_floor <= tgt and tgt not in opens_taiex):
                # 自癒:該日在「該標的」(量>0 過濾後)與「^TWII」都查無 → 確為臨時休市
                # (颱風),先前寫入的是假持平 bar,移除之。回顧表該列隨即消失
                # (與「當日無開盤」一致)。
                del rec[field]
                filled += 1
                print(f"[backfill] 移除 {tgt} 的 {field}(臨時休市日假 bar 誤填,已自癒)",
                      file=sys.stderr)
        # 加權「前收」:供 live 動態估計美股訊號 → 開盤跳空的有效 beta(us_beta_samples)
        if "actual_taiex_prev_close" not in rec and tgt in opens_taiex:
            prior = [d for d in taiex_dates if d < tgt]
            if prior:
                rec["actual_taiex_prev_close"] = closes_taiex[prior[-1]]
                filled += 1
    if filled:
        print(f"[backfill] 回填 {filled} 個實際開盤欄位至歷史記錄")
    return filled


def _git_commit_and_push_state(paths: list, message: str) -> None:
    """在 GitHub Actions 上把指定 state 檔 commit + push 回 repo(本機/DRY_RUN 不動作)。

    晨報與週日綜合共用:週日只 push podcast 狀態,不寫入預測歷史(避免與週六的
    週一預測筆記撞 target_session_date 而互相覆蓋)。
    """
    if not (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("DRY_RUN") != "1"):
        return
    # 只 add 實際存在的路徑:某 state 檔本次未產生(如 run_manifest 寫入失敗、或該檔為
    # 尚未追蹤的新檔而本次沒建)時,`git add <不存在路徑>` 會以 check=True 拋錯,連帶
    # 讓「整個 state push」被跳過(history/podcast/校準都不落地)——比少一個檔嚴重(Codex review)。
    existing = [p for p in paths if os.path.exists(p)]
    if not existing:
        print("[state] 無任何存在的 state 檔可 push,跳過", file=sys.stderr)
        return
    try:
        subprocess.run(["git", "config", "user.name", "morning-report-bot"], check=True, timeout=10)
        subprocess.run(["git", "config", "user.email", "actions@github.com"], check=True, timeout=10)
        subprocess.run(["git", "add", *existing], check=True, timeout=10)
        # 若無變動就跳過
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], timeout=10)
        if diff.returncode != 0:
            subprocess.run(["git", "commit", "-m", message], check=True, timeout=10)
            try:
                subprocess.run(["git", "push"], check=True, timeout=25)
            except subprocess.SubprocessError:
                print("[state] initial push failed; retrying after rebase", file=sys.stderr)
                subprocess.run(["git", "fetch", "origin"], check=True, timeout=30)
                subprocess.run(["git", "pull", "--rebase", "--autostash"], check=True, timeout=45)
                subprocess.run(["git", "push"], check=True, timeout=30)
            print("[state] 已 push 回 repo")
        else:
            print("[state] 無變動，跳過 commit")
    except subprocess.SubprocessError as e:
        print(f"[state] git push 失敗（不影響寄信）: {e}", file=sys.stderr)


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

        # 同一個 target session 只保留最後產生的版本。週六與週一晨報都指向
        # 週一開盤，週一版會自然覆蓋週六版，不再重複污染 bias / MAE。
        date_str = entry.get("date", dt.datetime.now(TPE).strftime("%Y-%m-%d"))
        entry = dict(entry)
        entry.setdefault("target_session_date", _infer_target_session_date(date_str))
        target_date = _target_session_date(entry)
        existing = _normalize_history_entries(existing)
        existing = [d for d in existing if _target_session_date(d) != target_date]
        existing.append(entry)

        # 只保留近 N 天
        cutoff = (dt.datetime.now(TPE) - dt.timedelta(days=days_to_keep)).strftime("%Y-%m-%d")
        existing = _normalize_history_entries(
            [d for d in existing if d.get("date", "") >= cutoff])

        # 回填已成熟交易日的實際開盤(累積離線可重現的預測準確度資料集);取數失敗不影響存檔
        try:
            backfill_actual_opens(existing)
        except Exception as e:
            print(f"[state] 實際開盤回填略過: {e}", file=sys.stderr)

        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[state] 已寫入記憶（共 {len(existing)} 筆）")

        # 在 GitHub Actions 環境中 commit + push 回 repo
        _git_commit_and_push_state(
            [str(STATE_FILE), str(MODEL_HISTORY_FILE),
             str(EVENT_TIMELINE_FILE), str(PODCAST_DIGEST_FILE),
             str(CONFORMAL_STATE_FILE),   # conformal 區間校準 q 需跨日持久化才會收斂
             str(SOURCE_HEALTH_HISTORY_FILE),   # N4:來源健康 30 天歷史,需跨日累積才算得出連續失敗
             str(RUN_MANIFEST_FILE),   # P1-4:本次執行耗時/來源 manifest(觀測用,市場中性)
             str(EMAIL_ARCHIVE_DIR)],   # §B:寄出信件 HTML 存檔(去識別),供日後檢索/RAG
            f"chore: update state {date_str} [skip ci]")
    except Exception as e:
        print(f"[state] 寫入失敗: {e}", file=sys.stderr)


def persist_delivered_report_state(entry: Optional[dict],
                                   podcast_episodes: list[dict],
                                   mark_podcasts: bool) -> None:
    """Persist delivery state; production callers invoke this only after SMTP succeeds."""
    if mark_podcasts:
        mark_podcast_episodes_shown(podcast_episodes)
    if entry:
        save_history_state(entry, days_to_keep=450)


def _format_event_scenarios(calendar: Optional[list],
                            now_tpe: Optional[dt.datetime] = None) -> str:
    """G2:把風險事件日曆篩成「未來約 48 小時(今日～後日)」的重要事件清單文字,
    供 prompt 的「事件情境決策表」取材。每列:日期 時間｜標題(含既有的預期/前值 note)。
    只輸出既有日曆事件(供 LLM 判讀),不新增/不編造;無事件回固定提示字串。"""
    now_tpe = now_tpe or dt.datetime.now(TPE)
    today = now_tpe.date()
    horizon = today + dt.timedelta(days=2)   # ~48h(含當日),日期粒度、留邊
    rows: list[str] = []
    for e in (calendar or []):
        d = e.get("date")
        # datetime 是 date 的子類:先正規化成 date,否則「date <= datetime」比較會拋 TypeError
        # (財報 adapter 可能保留 datetime/Timestamp 子類)→ 一顆壞事件就讓整份 prompt 降級(Codex review)
        if isinstance(d, dt.datetime):
            d = d.date()
        if not isinstance(d, dt.date) or not (today <= d <= horizon):
            continue
        note = str(e.get("note") or "").strip()
        t = str(e.get("time") or "").strip()
        rows.append(f"- {d.isoformat()} {t}｜{str(e.get('title', '')).strip()}"
                    + (f"（{note}）" if note else ""))
        if len(rows) >= 6:
            break
    return "\n".join(rows) if rows else "（未來 48 小時無重大排程事件）"


def _format_narrative_delta(history: Optional[list], today: Optional[str] = None) -> str:
    """G4:取最近一份歷史(=昨日報)的立場 + 重點事件,逐字整理成「昨日敘事回顧」供 prompt
    做「昨日 vs 今日」差分。history 為時間升冪(最新在末)。無可用紀錄回固定佔位字串。
    防幻覺:只整理 history 原文,不新增判讀(判讀交給 LLM)。

    today('YYYY-MM-DD'):今日報告日期。**嚴格排除 date >= today 的 entry**——同日重跑
    (手動 dispatch / retry / DRY_RUN 後正式跑)會把「今天早上的報告」存進 history,不排除
    就會被當成「昨日」拿今天比今天、產生假的強化/推翻(Codex review)。"""
    hist = [h for h in (history or []) if isinstance(h, dict)]
    if today:
        hist = [h for h in hist if str(h.get("date") or "")[:10] < today]
    if not hist:
        return "（無昨日紀錄可對照）"
    last = hist[-1]
    date = (str(last.get("date") or "").split() or ["昨日"])[0] or "昨日"
    stance = str(last.get("stance_label") or "").strip() or "未記錄"
    crit = [str(c).strip() for c in (last.get("critical_news") or []) if str(c).strip()][:5]
    if not crit and stance == "未記錄":
        return "（無昨日紀錄可對照）"
    lines = [f"【昨日({date})本報敘事回顧——逐字對照,不可竄改】",
             f"昨日立場:{stance}"]
    if crit:
        lines.append("昨日重點事件:")
        lines.extend(f"- {c}" for c in crit)
    else:
        lines.append("昨日無自動記錄的重大事件。")
    return "\n".join(lines)


def _compute_weekly_review_stats(history: Optional[list],
                                 today: Optional[str] = None) -> dict:
    """G5:上週預測 vs 實際的確定性統計(Python 算,非 LLM),供週一綜合報的「週報檢討」引用。

    取近一週(≤7 筆)已成熟(有 actual_open)的紀錄,對加權與 2330 各算:
      n(樣本)、mae_pct(平均絕對誤差%)、bias_pct(平均帶號誤差%;正=實際高於預測、模型低估)、
      hit_rate_pct(方向命中率:以「前一筆實際開盤」為基準,預測方向 vs 實際方向同號比例;best-effort)。
    另彙整上週 critical_news 供 LLM 檢討哪些成真/落空。無資料回 {}。純函式、不動計分。"""
    def _num(v):
        return v if isinstance(v, (int, float)) and v > 0 else None

    def _sgn(x):
        return (x > 0) - (x < 0)

    hist = [h for h in (history or []) if isinstance(h, dict)]
    if today:
        hist = [h for h in hist if str(h.get("date") or "")[:10] < today]
    hist = sorted(hist, key=lambda h: str(h.get("target_session_date") or h.get("date") or ""))
    # 先建「已成熟」cohort(有任一實際開盤=該交易日已發生),再取最近 7 筆——否則週六尚未
    # 成熟的紀錄會佔滿 last-7 slot、把更早的成熟紀錄擠出上週,且其事件被誤當上週(Codex review)。
    mature = [h for h in hist
              if _num(h.get("actual_open_taiex")) or _num(h.get("actual_open_2330"))]
    mature = mature[-7:]

    def _stats(pred_k, act_k):
        errs: list = []
        dir_pairs: list = []
        prev_act = None
        for h in mature:
            pv = _num(h.get(pred_k))
            av = _num(h.get(act_k))
            if pv and av:
                errs.append(av / pv - 1.0)
                if prev_act is not None:
                    # 三方向(-1/0/+1)比較:預測「不變」但實際變動(或反之)算「未命中」,
                    # 不可略過(否則膨脹命中率);只有缺基準/缺數值時才不計(Codex review)。
                    dir_pairs.append(_sgn(pv - prev_act) == _sgn(av - prev_act))
            if av:
                prev_act = av
        if not errs:
            return None
        return {
            "n": len(errs),
            "mae_pct": round(sum(abs(e) for e in errs) / len(errs) * 100, 2),
            "bias_pct": round(sum(errs) / len(errs) * 100, 2),
            "hit_rate_pct": (round(sum(dir_pairs) / len(dir_pairs) * 100)
                             if dir_pairs else None),
            "n_dir": len(dir_pairs),
        }

    taiex = _stats("pred_taiex", "actual_open_taiex")
    tw2330 = _stats("weighted_final_2330", "actual_open_2330")
    # 事件只從已成熟 cohort 收;且「無任何成熟預測配對」時整體回 {}(不讓純事件撐起七之五)。
    if not taiex and not tw2330:
        return {}
    crit: list = []
    for h in mature:
        for c in (h.get("critical_news") or []):
            c = str(c).strip()
            if c and c not in crit:
                crit.append(c)
    crit = crit[:8]
    return {"taiex": taiex, "tw2330": tw2330,
            "critical_events": crit, "n_days": len(mature)}


def _format_weekly_review(stats: Optional[dict]) -> str:
    """把 _compute_weekly_review_stats 的結果整理成 prompt 文字。無資料回 ""。"""
    if not stats:
        return ""

    def _line(name, s):
        if not s:
            return f"{name}:上週無可對照的成熟預測。"
        hit = (f"、方向命中 {s['hit_rate_pct']:.0f}%(n={s['n_dir']})"
               if s.get("hit_rate_pct") is not None else "")
        return (f"{name}:樣本 {s['n']} 日、平均絕對誤差 {s['mae_pct']:.2f}%、"
                f"持續偏誤 {s['bias_pct']:+.2f}%(正=實際高於預測=模型偏低估){hit}")

    lines = ["【上週預測回顧(Python 統計,數字僅能引用此處)】",
             _line("加權指數開盤", stats.get("taiex")),
             _line("2330 開盤", stats.get("tw2330"))]
    crit = stats.get("critical_events") or []
    if crit:
        lines.append("上週重點事件(供檢討哪些成真/落空/只是噪音):")
        lines.extend(f"- {c}" for c in crit)
    return "\n".join(lines)


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
        prefix += _supply_chain_2330_tag(n.get("company_label"))
        # summary 顯示 600 字(由 fetch_news 端 800 切過,這裡再做一次安全切);
        # 之前 200 切太短常切在「公司剛被提及」就沒下文,LLM 看不到具體事實
        grade = n.get("source_grade") or _news_source_grade(n)
        # G6:critical/high(with_full)附可信度確定性標記——獨立來源數 + 是否含官方來源。
        cred = _credibility_tag(n) if with_full else ""
        text = (f"- {prefix}[來源{grade}:{n['source']}]{cred} "
                f"{n['title']}（{n.get('summary','')[:600]}）")
        if with_full and n.get("fulltext"):
            text += f"\n  [全文摘錄]：{n['fulltext'][:1500]}"
        return text

    # 世界大事項目不進市場新聞配額桶(crit[:10]/high[:20]/norm[:30]):它們有專屬的
    # 【昨日世界大事新聞】取材段;「戰爭」等詞會被判 critical,不排除的話忙碌新聞日
    # 會把 Fed/台股/公司消息從配額裡擠掉——市場仍是本報核心(Codex review)。
    # (真正牽動市場的地緣事件仍由既有 Google-地緣/CNBC 等一般來源進桶,不受影響。)
    def _world_cat_of(n: dict) -> str:
        wc = str(n.get("world_cat") or "")
        if wc:
            return wc
        src = str(n.get("source", ""))
        if src.startswith("世界-"):
            return src[3:]
        return src if src == "中央社國際" else ""

    # world_and_market=同一事件同時來自市場與世界來源(dedup 時標記)→ 兩個版面都收
    market_news = [n for n in news
                   if not _world_cat_of(n) or n.get("world_and_market")]
    crit_news = [n for n in market_news if n.get("importance") == "critical"]
    high_news = [n for n in market_news if n.get("importance") == "high"]
    norm_news = [n for n in market_news if n.get("importance") == "normal"]

    news_block = "★★★ 重大事件（必讀，含全文摘錄）★★★\n"
    if crit_news:
        news_block += "\n".join(fmt_news(n, with_full=True) for n in crit_news[:10]) + "\n\n"
    else:
        news_block += "（昨日無自動辨識的 Fed/數據/政策重大事件）\n\n"
    # high 也帶全文(fetch_news_fulltext 對 high 也抓了,個股新聞多半在這層,
    # 不帶全文 LLM 只看到 600 字 snippet → R12 觸發 → 公司被刪)
    news_block += "★★ 高權重事件（地緣/台灣政策/個股催化/法說 / 8-K 等）★★\n"
    if high_news:
        # 地緣/政策新聞在 news 串接順序較前(各 RSS 來源),個股催化(company_label)由公司查詢
        # 最後 append → 排在後面;取 20 則確保兩者都進得來,不會被個股催化擠掉地緣/政策。
        news_block += "\n".join(fmt_news(n, with_full=True) for n in high_news[:20]) + "\n\n"
    else:
        news_block += "（無）\n\n"
    news_block += "★ 一般新聞（參考）★\n"
    news_block += "\n".join(fmt_news(n) for n in norm_news[:30])

    # 重點公司新聞(Google News 查詢)獨立成段,確保「科技板塊脈動 / 關注三檔」一定取得到個股素材。
    # (這些多半被分類為 normal,易被 norm[:30] 截掉;故額外保證露出。)
    company_news = [n for n in news if n.get("company_label")]
    if company_news:
        # 依公司分組,每家最多 3 則,避免單一公司洗版
        by_label: dict[str, list] = {}
        for n in company_news:
            by_label.setdefault(n.get("company_label", "?"), []).append(n)
        # 每家先過品質閘門(濾純喊價/純籌碼雜訊),每家至少保留 1 則(取最可信完整者),
        # 各家最多 3 則;優先深耕公司(2330 台積電 + 2882/2891 兩大金控,使用者要求
        # 2026-07-14 加深其財報/人事/投資覆蓋)放寬到 5 則。
        _DEEP_COMPANY_LABELS = {"2330": 5, "2882": 5, "2891": 5}
        _max_rank = max(3, *(_DEEP_COMPANY_LABELS.values()))
        per_label: list[tuple] = []   # (label, tag, [news…])
        for label, lst in by_label.items():
            tag = _supply_chain_2330_tag(label)
            filtered = [n for n in lst if not _is_low_value_tech_headline(n)]
            if not filtered:
                filtered = sorted(lst, key=_news_keep_score, reverse=True)[:1]
            per_label.append((label, tag, filtered[:_DEEP_COMPANY_LABELS.get(label, 3)]))
        # 三段式展平(Codex review:單純輪替在 30 家全有新聞的忙日,rank-0 就吃掉 30 行,
        # 剩餘配額按清單序給前幾家的第 2 則 → 排在後段的深耕金控反而拿不到深度):
        #   (1) 每家首則全數露出(30 家保底);
        #   (2) 深耕公司(2330/2882/2891)的第 2-5 則優先保留(3 家 × 4 = 12 行);
        #   (3) 還有餘裕才輪替遞補一般公司的第 2、3 則。42 行上限恰容納 (1)+(2)。
        def _fmt_company_line(label, tag, n):
            return f"- [{label}] {tag}{n['title']}（{n.get('summary','')[:300]}）"

        lines = []
        for label, tag, items in per_label:                    # (1) 全員首則
            if items:
                lines.append(_fmt_company_line(label, tag, items[0]))
        for label, tag, items in per_label:                    # (2) 深耕公司 2-5 則
            if label in _DEEP_COMPANY_LABELS:
                lines.extend(_fmt_company_line(label, tag, n) for n in items[1:])
        for rank in range(1, _max_rank):                       # (3) 一般公司遞補
            for label, tag, items in per_label:
                if label not in _DEEP_COMPANY_LABELS and rank < len(items):
                    lines.append(_fmt_company_line(label, tag, items[rank]))
        news_block += ("\n\n【重點公司最新新聞（Google News，供「科技板塊脈動」「九、其他類股」"
                       "與「關注三檔」取材;標 [對2330供應鏈] 者請在分析點出對 2330 的傳導）】\n"
                       + "\n".join(lines[:42]))

    # 其他(非科技)類股新聞(來源名前綴「類股-」)獨立成段、依類股分組,
    # 確保「九、其他類股資訊」每個類股都有素材可寫(否則易被 norm[:30] 截掉)。
    sector_news: dict[str, list] = {}
    for n in news:
        src = str(n.get("source", ""))
        if src.startswith("類股-"):
            sector_news.setdefault(src[len("類股-"):], []).append(n)
    if sector_news:
        sec_lines = []
        for label, lst in sector_news.items():
            sec_lines.append(f"\n■ {label}")
            for n in lst[:6]:  # 每類股最多 6 則;Google News 標題末已含來源媒體,摘要多為 HTML 雜訊故不放
                sec_lines.append(f"- {n['title']}")
        news_block += ("\n\n【其他類股最新新聞（Google News，供「九、其他類股資訊」取材;"
                       "依類股分組,標題末為來源媒體;此處為「新聞」非股價數據）】\n"
                       + "\n".join(sec_lines))

    # 整理台股 universe 法人/表現摘要表（讓 LLM 一眼掃完）。
    # 五檔由 Python 排名渲染,LLM 不再自選個股 → 只需給法人買超前 50 檔當背景即可,
    # 不必塞滿 100 列(縮短 prompt、降低 context-overflow 與成本)。
    sector_news = {}
    for n in news:
        label = _other_sector_label_from_source(str(n.get("source", "")))
        if label and not n.get("date_missing"):
            sector_news.setdefault(label, []).append(n)
    sec_lines = []
    for label in OTHER_SECTOR_LABELS:
        sec_lines.append(f"\n## {label}")
        lst = sector_news.get(label) or []
        if not lst:
            sec_lines.append("- no dated material headline available")
            continue
        for n in lst[:6]:
            published = str(n.get("published_dt") or n.get("published") or "")[:19]
            sec_lines.append(f"- [{published}] {n['title']}")
    news_block += ("\n\n[Other sector coverage: dated headlines only; if a label says no dated material, "
                   "write that no major news was found and do not invent details.]\n"
                   + "\n".join(sec_lines))

    # 世界大事(非市場)新聞獨立成段:供「世界大事速覽」取材。以 world_cat 判定
    # (dedup 後仍保留;來源前綴僅為 fallback)。每類最多 4 則:保留跨類別多樣性
    # (國際/災難/科學/AI),同時控 prompt 長度(Codex 第二意見)。
    world_lines: list[str] = []
    _world_per_cat: dict[str, int] = {}
    for n in news:
        cat = _world_cat_of(n)
        if not cat or n.get("date_missing"):
            continue
        _world_per_cat[cat] = _world_per_cat.get(cat, 0) + 1
        if _world_per_cat[cat] > 4:
            continue
        published = str(n.get("published_dt") or n.get("published") or "")[:16]
        world_lines.append(f"- [{published}][{cat}] {n['title']}")
    if world_lines:
        news_block += ("\n\n【昨日世界大事新聞(非市場導向,供「世界大事速覽」取材;"
                       "[類別] 標示,標題末為來源媒體)】\n" + "\n".join(world_lines[:18]))

    # 類股熱度表(純行情數據,供「九、其他類股」判斷哪些類股在動、誰領漲;不進計分)
    heat_block = _format_sector_heat_block(quotes.get("SECTOR_HEAT") or {})
    if heat_block:
        news_block += heat_block

    if tw0050:
        tw0050_sorted = sorted(tw0050, key=lambda x: x.get("total_lot", 0), reverse=True)[:50]
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
    foreign_top10_total = quotes.get("FOREIGN_TOP10_TOTAL")
    foreign_top10_block = (
        f"{foreign_top10_total:+,.0f} 張"
        if isinstance(foreign_top10_total, (int, float)) else "資料缺失"
    )

    # 客觀關注排名 Top 15：固定公式由高至低排序，供 LLM 解釋而非自由換股。
    if tw0050:
        ranked = sorted(tw0050,
                        key=lambda x: x.get("ranking_score", x.get(
                            "attention_score", (x.get("breakout") or {}).get("score", 0))),
                        reverse=True)
        bk_top = [s for s in ranked if (s.get("breakout") or {}).get("score", 0) > 0][:15]
        if bk_top:
            bk_rows = []
            for s in bk_top:
                bk = s.get("breakout") or {}
                sm = s.get("smart_money") or {}
                comp = bk.get("components", {})
                fs = s.get("foreign_streak", 0) or 0
                is_ = s.get("invest_streak", 0) or 0
                wow = s.get("tdcc_wow_pct")
                vr20 = s.get("vol_ratio_20d")
                p5 = s.get("pct_5d")
                d20 = s.get("ma20_dist_pct")
                yoy = s.get("rev_yoy_pct")
                mom = s.get("rev_mom_pct")
                eps = s.get("eps")
                tot_lot = s.get("total_lot", 0)
                f30 = s.get("foreign_30d_lot", 0)
                rel = s.get("rel_strength_5d")
                scr = s.get("short_cover_ratio")
                def _f(v, suf="", d="-"):
                    return f"{v:+.1f}{suf}" if isinstance(v, (int, float)) else d
                bk_rows.append(
                    f"{s['code']} {s['name']:<6} 客觀排名分={s.get('ranking_score', s.get('attention_score', bk.get('score',0))):>5} "
                    f"(結構{(s.get('ranking_components') or {}).get('structure',0):+4.1f}/"
                    f"新聞{(s.get('ranking_components') or {}).get('news_event',0):+4.1f}/"
                    f"產業{(s.get('ranking_components') or {}).get('industry_neutral',0):+4.1f}/"
                    f"勝率{(s.get('ranking_components') or {}).get('beat_market',0):+4.1f}/"
                    f"報酬{(s.get('ranking_components') or {}).get('expected_return',0):+4.1f}/"
                    f"品質{(s.get('ranking_components') or {}).get('quality_penalty',0):+4.1f}/"
                    f"流動性{(s.get('ranking_components') or {}).get('liquidity_penalty',0):+4.1f}/"
                    f"漂移{(s.get('ranking_components') or {}).get('feature_drift_penalty',0):+4.1f}/"
                    f"來源{(s.get('ranking_components') or {}).get('source_health_penalty',0):+4.1f}/"
                    f"校準{(s.get('ranking_components') or {}).get('model_monitor_penalty',0):+4.1f}/"
                    f"過熱{(s.get('ranking_components') or {}).get('overheat_penalty',0):+4.1f}) "
                    f"[籌{comp.get('chips',0):.0f}/動{comp.get('momentum',0):.0f}/"
                    f"營{comp.get('revenue',0):.0f}/EPS{comp.get('eps',0):.0f}] | "
                    f"昨日法人{tot_lot:+.0f}張 30日外資{f30:+.0f}張 外連{fs:+d}投連{is_:+d} "
                    f"大戶ΔWoW{_f(wow,'%')} 站隊{sm.get('score',0)} | "
                    f"5日{_f(p5,'%')} MA20{_f(d20,'%')} 相對同業{_f(rel,'%')} "
                    f"量比{(f'{vr20:.2f}x' if vr20 else '-')} 借券回補{_f(scr,'%')} | "
                    f"營收YoY{_f(yoy,'%')} MoM{_f(mom,'%')} EPS{(f'{eps:.2f}' if eps is not None else '-')}"
                )
            smart_money_block = "\n".join(bk_rows)
        else:
            smart_money_block = "（今日無有效爆發力候選;部分因子需累積歷史[大戶WoW/EPS年增]才會完整）"
    else:
        smart_money_block = "（資料抓取失敗,跳過爆發力排序）"

    attention_top = _rank_attention_candidates(tw0050)[:5]
    if attention_top:
        attention_rows = []
        for rank, stock in enumerate(attention_top, 1):
            forecast = stock.get("price_forecast") or {}
            f3 = forecast.get("3d") or {}
            f5 = forecast.get("5d") or {}
            catalysts = stock.get("news_catalysts") or []
            catalyst_text = "；".join(
                f"[{c.get('relation')}/{c.get('source_grade')}] {c.get('title')}"
                for c in catalysts[:2]) or "無直接催化"
            attention_rows.append(
                f"{rank}. {stock['code']} {stock['name']}｜客觀排名分 {stock.get('ranking_score', stock.get('attention_score',0)):.1f} "
                f"(結構 {(stock.get('ranking_components') or {}).get('structure',0):+.1f} / "
                f"新聞 {(stock.get('ranking_components') or {}).get('news_event',0):+.1f} / "
                f"產業中性 {(stock.get('ranking_components') or {}).get('industry_neutral',0):+.1f} / "
                f"勝過大盤 {(stock.get('ranking_components') or {}).get('beat_market',0):+.1f} / "
                f"預期報酬 {(stock.get('ranking_components') or {}).get('expected_return',0):+.1f} / "
                f"品質 {(stock.get('ranking_components') or {}).get('quality_penalty',0):+.1f} / "
                f"流動性 {(stock.get('ranking_components') or {}).get('liquidity_penalty',0):+.1f} / "
                f"漂移 {(stock.get('ranking_components') or {}).get('feature_drift_penalty',0):+.1f} / "
                f"來源 {(stock.get('ranking_components') or {}).get('source_health_penalty',0):+.1f} / "
                f"校準 {(stock.get('ranking_components') or {}).get('model_monitor_penalty',0):+.1f} / "
                f"過熱 {(stock.get('ranking_components') or {}).get('overheat_penalty',0):+.1f})｜"
                f"昨收 {stock.get('close')}｜"
                f"3日預測 {f3.get('expected_price','資料不足')} "
                f"[{f3.get('lower','-')}~{f3.get('upper','-')}]｜"
                f"5日預測 {f5.get('expected_price','資料不足')} "
                f"[{f5.get('lower','-')}~{f5.get('upper','-')}]｜"
                f"模型信心 {forecast.get('confidence','低')}｜催化：{catalyst_text}"
            )
        attention_top_block = "\n".join(attention_rows)
    else:
        attention_top_block = "（無可用候選）"

    # 總經指標摘要（含 252 日百分位）
    macro = quotes.get("MACRO", {}) or {}
    def fmt_m(name: str) -> str:
        return _format_macro_line(name, macro.get(name) or {})
    macro_block = "\n".join(
        [f"  {fmt_m(n)}" for n in
         ["VIX", "VIX9D", "SOX", "10Y", "DXY", "13W", "N225", "SSE",
          "NQ", "ES", "WTI", "GOLD"]])
    # 殖利率曲線 10Y − 13W 利差（由已抓資料推導，倒掛為衰退領先訊號）。
    # 給 LLM 完整技術資訊以利判斷,但另附白話結論——信件呈現請用白話、避免術語(使用者要求)。
    ten_y = macro.get("10Y", {}) or {}
    thirteen_w = macro.get("13W", {}) or {}
    if ten_y.get("close") is not None and thirteen_w.get("close") is not None:
        spread = ten_y["close"] - thirteen_w["close"]
        macro_block += (f"\n  殖利率曲線 10Y−13W 利差 = {spread:+.2f} 個百分點"
                        f"（負值=倒掛，衰退領先訊號；轉正回升=景氣回溫訊號）")
    _yc_read = _yield_curve_read(macro)
    if _yc_read.get("detail"):
        macro_block += f"\n  美債利率環境(請用此白話、勿在信中寫「倒掛/殖利率曲線」術語):{_yc_read['detail']}"
    # 台股估值溫度 + 選擇權磁吸參考(白話顯示資料,同步給 LLM 當背景;不進計分,
    # 引用時用白話、勿寫 max pain/OI 術語)
    _val_p = quotes.get("VALUATION") or {}
    if _val_p.get("median_pe"):
        macro_block += (f"\n  台股估值溫度:全市場本益比中位數 {_val_p['median_pe']} 倍"
                        + (f"、殖利率中位數 {_val_p['median_yield']}%"
                           if _val_p.get("median_yield") else "")
                        + f" → {_val_p.get('label', '')}(長期經驗區間)")
    _mag_p = quotes.get("TXO_MAGNET") or {}
    if _mag_p.get("magnet"):
        macro_block += (f"\n  選擇權籌碼參考({_mag_p.get('settle', '')} 結算):"
                        f"結算磁吸參考價約 {_mag_p['magnet']:,.0f} 點"
                        + (f",上方壓力參考 {_mag_p['call_wall']:,.0f}"
                           if _mag_p.get("call_wall") else "")
                        + (f",下方支撐參考 {_mag_p['put_wall']:,.0f}"
                           if _mag_p.get("put_wall") else ""))
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

    # 分析師評等/目標價動能(近 30 日;ADR/美股,TSM≈2330 供應鏈)
    analyst_mom = quotes.get("ANALYST_MOMENTUM", {}) or {}
    if analyst_mom:
        _rows = []
        for tk, v in analyst_mom.items():
            _rows.append(
                f"  {tk}: 淨動能 {v.get('net', 0):+d}（升{v.get('up', 0)}/降{v.get('down', 0)}/"
                f"調高目標{v.get('tgt_raise', 0)}/調低{v.get('tgt_cut', 0)};最近 {v.get('latest', '')}）")
        analyst_block = ("近 30 日賣方分析師動向（淨動能=升評+調高目標 − 降評+調低目標;"
                         "TSM 對應 2330、其餘為 AI/半導體龍頭,屬前瞻共識轉向、非當日新聞）:\n"
                         + "\n".join(_rows))
    else:
        analyst_block = "（分析師評等資料暫無或抓取失敗）"

    # 台股重點公司 MOPS 重大訊息。深耕公司(2330/2882/2891,使用者指定)的公告
    # (人事異動/重大投資/財報自結)優先入列,不與其他 40 家搶最新 20 筆的名額——
    # 這些公告是「九、其他類股」金融條目與科技段的 A 級素材(信中 MOPS 表已隱藏,
    # 內容改由分析段落呈現)。
    tw_mops = quotes.get("TW_MOPS", []) or []
    if tw_mops:
        _deep_mops = [m for m in tw_mops
                      if str(m.get("code", "")) in ("2330", "2882", "2891")][:8]
        _other_mops = [m for m in tw_mops if m not in _deep_mops]
        _mops_pick = _deep_mops + _other_mops[:max(0, 20 - len(_deep_mops))]

        def _mops_line(m: dict) -> str:
            line = f"- {m.get('code','')} {m.get('title','')[:80]}"
            # 深耕公司附「說明」摘要:人事異動的人名/生效日、投資案的金額/交易對象
            # 常只在 summary、標題僅泛稱「公告總經理異動」——不附摘要 LLM 寫不出
            # 具體內容甚至瞎編(Codex review P1)。其他公司維持標題,控 prompt 長度。
            if m in _deep_mops:
                summary = " ".join(str(m.get("summary") or "").split())[:400]
                if summary:
                    line += f"\n  說明:{summary}"
            return line

        mops_block = "\n".join(_mops_line(m) for m in _mops_pick)
    else:
        mops_block = "（過去 48 小時無重點公司 MOPS 重大訊息，或來源暫不可用）"

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
    # 大額交易人 + 台指選擇權 P/C(借鏡 node-twstock,TAIFEX OpenAPI;附加在期貨 block,只餵 LLM 不單獨渲染)
    lt = quotes.get("TAIFEX_LARGE_TRADERS", {}) or {}
    if lt.get("top10_net") is not None:
        taifex_block += (
            f"\n  大額交易人(台指期所有契約)前10大淨部位: {lt['top10_net']:+d} 口"
            f"（正=偏多、負=偏空;前10大集中度 {lt.get('concentration_pct')}%）")
        if lt.get("spec_top10_net") is not None:
            taifex_block += f"；其中特定法人前10大淨 {lt['spec_top10_net']:+d} 口（更貼近機構方向）"
    pcr = quotes.get("TAIFEX_PCR", {}) or {}
    if pcr.get("pc_oi_ratio") is not None:
        taifex_block += (
            f"\n  台指選擇權 Put/Call 比(未平倉): {pcr['pc_oi_ratio']}%"
            f"（>100=未平倉偏 Put/避險偏空;極端偏高常是散戶過度避險 → 反向偏多訊號）")

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
            f"  夜盤收盤: {night.get('night_close')}"
            + (f"（日盤收 {night.get('day_close')} 僅診斷,除息/正價差日勿直接相減）\n"
               if night.get('day_close') is not None else "\n")
            + f"  夜盤官方漲跌: {night['night_pct']:+.2f}% ← TAIFEX 盤後官方漲跌%（直接反映外資對今日台股開盤的方向預期；正=偏多開高）"
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
        # 校準後漲跌 = 由(已含 bias 修正的)pred_open 回推,與信件卡片顯示一致;
        # weighted_pct 是「未校準原始訊號」,LLM 引用它會與卡片數字打架(2026-07 實例:敘述 1.32% vs 卡片 1.17%)。
        _cal_pct = ((pred['pred_open'] / pred['last_close'] - 1) * 100
                    if pred.get('last_close') else pred.get('weighted_pct', 0.0))
        taiex_pred_block = (
            f"  加權指數昨收: {pred['last_close']}\n"
            f"  訊號: {signals_str}\n"
            f"  加權預測漲跌（校準後,敘述請一律引用此值）: {_cal_pct:+.2f}%"
            f"（未校準原始訊號 {pred['weighted_pct']:+.2f}%,僅供參考、勿寫進結論）\n"
            f"  ★ 預測開盤點位: {pred['pred_open']} （參考區間 {pred['ci_lower']} ~ {pred['ci_upper']}）\n"
            f"  區間方法: {pred.get('interval_method', '資料缺失')}\n"
            f"  訊號共識: {pred['consensus']}（標準差 {pred.get('signal_std','—')}）\n"
            f"  自我校正: {_calibration_note(pred)}"
        )
    else:
        taiex_pred_block = "（資料不足，無法預測大盤）"

    # Task F: 預測回溯 block
    backtest_block = quotes.get("BACKTEST", "（無回溯資料）") or "（無回溯資料）"
    breakout_tracking_block = (
        quotes.get("BREAKOUT_TRACKING", "（候選追蹤樣本累積中）")
        or "（候選追蹤樣本累積中）"
    )

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

    structured_news_block = json.dumps(
        (quotes.get("STRUCTURED_NEWS_EVENTS") or [])[:25],
        ensure_ascii=False,
        separators=(",", ":"),
    )

    # Podcast 觀點(主持人個人看法):供 LLM 在分析中「引用對照」,嚴禁當成事實或本報立場
    podcast_lines = []
    for ep in (quotes.get("PODCAST_DIGEST") or [])[:3]:
        d = ep.get("digest") or {}
        pts = "; ".join(str(p) for p in (d.get("summary_points") or [])[:3])
        tk = ", ".join(
            f"{t.get('name')}({t.get('direction')})"
            for t in (d.get("tickers") or [])[:5])
        podcast_lines.append(f"- {ep.get('show')}「{str(ep.get('title', ''))[:40]}」:{pts}"
                             + (f" | 個股觀點: {tk}" if tk else ""))
    podcast_block = "\n".join(podcast_lines) if podcast_lines else "(近 48 小時無新集)"
    walk_forward_block = json.dumps(
        quotes.get("MODEL_WALK_FORWARD") or {},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    # ===== 注入「Python 已算」的乾淨美股報價 + 2330/00662 關鍵價位(NT$) =====
    # 目的:(1) 不再把含 history DataFrame 的整個 dict 倒給 LLM(那會讓 LLM 看到一堆
    # 美元數字、把台積電 ADR(US$) 誤當成 2330 本地價);(2) 關鍵價位改由 Python 算好注入,
    # LLM 只能原樣引用,根除「2330 守穩 430 元」這類把 ADR 美元價當台股價的幻覺。
    def _fmt_us_quote(d) -> str:
        if not isinstance(d, dict) or d.get("error") or d.get("close") is None:
            return "資料未提供"
        pct = d.get("change_pct")
        pct_s = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "—"
        hi, lo = d.get("high"), d.get("low")
        hl = f"，高/低 {hi}/{lo}" if hi and lo else ""
        vol = d.get("volume")
        vol_s = f"，量 {vol:,}" if isinstance(vol, (int, float)) and vol else ""
        return f"{d['close']} 美元（{pct_s}）{hl}{vol_s}"

    _qqq_s = _fmt_us_quote(quotes.get("QQQ"))
    _tsm_s = _fmt_us_quote(quotes.get("TSM"))
    _spy_s = _fmt_us_quote(quotes.get("SPY"))

    _p_ok = isinstance(predictions, dict) and not predictions.get("error")
    _p_mid = predictions.get("mid") if _p_ok else None
    _p_last = predictions.get("last_2330") if isinstance(predictions, dict) else None
    _band_2330 = 0.015 if us_hol.get("detected") else 0.01
    if _p_mid:
        _lo2330 = round(_p_mid * (1 - _band_2330))
        _hi2330 = round(_p_mid * (1 + _band_2330))
        # 注意:指令文字(請原樣引用等)不可放進這行 — LLM 會連指令一起抄進信件;
        # 約束已由 R14 鐵律與「我的明確立場」段的格式說明承擔。
        key_2330_line = (
            f"2330 台積電（新台幣計價）：預測開盤中樞 {round(_p_mid)} 元、昨收 "
            f"{round(_p_last) if _p_last else '—'} 元。關鍵價位——站上 {_hi2330} 元偏強、"
            f"跌破 {_lo2330} 元轉弱。")
        _mid2330_txt = str(round(_p_mid))
    else:
        key_2330_line = "2330 預測資料未提供 → 本行寫「資料未提供」，**嚴禁自行編造價位**。"
        _mid2330_txt = "（資料未提供）"

    _f_ok = isinstance(fair, dict) and not fair.get("error")
    _f_price = fair.get("fair_price") if _f_ok else None
    _f_last = fair.get("last_00662_price") if isinstance(fair, dict) else None
    if _f_price:
        key_00662_line = (
            f"00662（新台幣計價）：合理估值 {_f_price} 元、昨收 "
            f"{_f_last if _f_last else '—'} 元。開盤明顯低於 {round(_f_price * 0.995, 2)} 元偏便宜、"
            f"高於合理值則偏貴。")
    else:
        key_00662_line = "00662 估值資料未提供 → 寫「資料未提供」，嚴禁編造。"

    # G2:未來 ~48h 重要行事曆事件(含既有預期/前值),供「七之三、事件情境決策表」取材。
    event_scenario_lines = _format_event_scenarios(quotes.get("EVENT_CALENDAR"))
    # G4:昨日本報立場+重點事件(逐字),供「七之四、敘事變化」做昨日 vs 今日差分。
    #     傳今日日期以排除同日重跑存下的「今天」紀錄(避免今天比今天)。
    narrative_delta_block = _format_narrative_delta(
        quotes.get("HISTORY"), today=dt.datetime.now(TPE).strftime("%Y-%m-%d"))
    # G5:週一綜合報才有 WEEKLY_REVIEW(main 依 mode 存入);有才組「七之五、週報檢討」段。
    weekly_review_block = _format_weekly_review(quotes.get("WEEKLY_REVIEW"))
    weekly_review_section = (f"""## 七之五、上週檢討與本週假設（**僅週一綜合報**;有上週統計才寫）

{weekly_review_block}

依上方【上週預測回顧】,用 **≤6 行**寫:
1. 上週預測整體準不準——**引用平均絕對誤差與持續偏誤數字**,一句總評(偏樂觀高估/偏保守低估/大致準)。
2. 上週哪些重點判斷/事件**成真**、哪些**落空**、哪些只是**一日噪音**——只引用上方事件清單與已知走勢,不杜撰。
3. 本週要重點驗證的 **≤3 個假設**(可證偽、具體,如「若 CPI 低於預期則 00662 補漲」)。
**鐵則**:數字只能引用上方統計;事件只能引用上方清單或歷史;不得杜撰未發生的走勢或不存在的事件。
""" if weekly_review_block else "")

    return f"""你是嚴謹但敢於下判斷的科技股財經分析師。為一位重押 00662（NASDAQ-100）與 2330（台積電）的台灣投資人寫晨報。

【資料品質（最優先閱讀）】
{dq_block}
※ status=OK：資料正常，可正常引用。
※ status=FALLBACK：降級資料（樣本不足或部分來源失敗），引用時須說明「資料有限」。
※ status=ERROR：該來源今日抓取失敗 ＝「資料未提供」。對應段落必須明寫「資料未提供」，
   嚴禁腦補、嚴禁編造數字 / 新聞 / 法人買賣超 / 公司財報。寧可少寫，不可瞎掰。

【昨日美股收盤】（**以下 QQQ/TSM/SPY 均為美股、美元計價**）
- QQQ：{_qqq_s}
- TSM (台積電 ADR，**美元計價，1 ADR≈5 股台積電；這不是 2330 的新台幣股價**)：{_tsm_s}
- SPY：{_spy_s}
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

【分析師評等動能（近 30 日，賣方共識轉向）】
{analyst_block}
※ 持續調高目標價/升評 = 共識轉強的前瞻訊號;TSM 動能可作為 2330 的領先參考。屬方向性訊號(B 級),勿當當日催化。

【台股重點公司 MOPS 重大訊息（市值前 10 大 + 初步候選前 15，近 48 小時）】
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

【加權指數預測（Task A，美股訊號重縮放 + 夜盤台指期）】
{taiex_pred_block}
※ 美股訊號(SOX/TSM ADR)依歷史有效 beta(約 0.31,482 日全合成回測)縮放後,與夜盤台指期 0.7/0.3 合成;表中為有效權重。訊號分歧時信心降低。

【大盤量能與市場廣度（TWSE STOCK_DAY_ALL 統計）】
{breadth_block}

【外資市值前 10 大昨日合計買賣超】
{foreign_top10_block}

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

【財經 Podcast 主持人觀點(股癌/財經皓角/財報狗,AI 轉錄摘要)】
{podcast_block}
※ 這是「主持人個人觀點」非事實新聞:可在分析中引用對照(須標注「股癌觀點」等來源),
   嚴禁當成市場事實、嚴禁未標注來源就採納為本報立場。與你的數據結論分歧時,以數據為準並可點出分歧。

【近 24-30 小時新聞清單（含國際財經、Fed、台灣財經、政府政策）】
{news_block}

【結構化新聞事件（抽取器已聚類、官方來源優先、含新鮮度衰減）】
{structured_news_block}
※ 各事件附 surprise_score(0-1,越高越意外、越值得優先寫)與 lifecycle(confirmed 已確認 / rumor 傳聞 / withdrawn 已撤回):
  surprise_score ≥ 0.6 優先且醒目處理;< 0.3 可略過(不意外、低資訊量);lifecycle=rumor 必標「未證實」、
  withdrawn 須註明「已撤回/暫緩」。這些分數由 Python 計算,**請直接引用、不要自己重算或質疑數值**。

【台股市值前 100 大昨日表現 + 三大法人買賣超 + 30日累積法人（張，正為買超）+ 月營收年增率 + 大戶持股 + 5日動能 + 距 MA20】
{tw0050_block}
※「營收YoY」為該公司最新月營收的去年同月年增率（真實數據，TWSE 月營收彙總）；「-」代表無資料
※「大戶」為持股 ≥ 400 張的大戶占集保總數比例（TDCC 集保股權分散表，週更）；比例高 = 籌碼集中在大戶/主力手上

【★★ 台股客觀關注排名 Top 15（固定公式由高至低排序；信件底部 Top5 卡片使用前五名）】
{smart_money_block}
※ 客觀排名分 = 結構分（籌碼、動能、營收、EPS，正規化後最高 70 分）+ 新聞事件分 + 產業中性修正 + 勝過大盤機率修正 + 3 日預期報酬修正 + 模型品質、流動性、機率校準、特徵漂移與來源健康度折扣。
※ 中括號 [籌X/動X/營X/EPSX] 為原始結構因子貢獻分；括號內各欄位為最終排名各分項，總分可重現、可回測。
※ 目標：篩選**未來 3-5 個工作天值得關注**的候選。信件底部 Top5 由 Python 固定公式直接渲染；LLM 不另寫五檔段落。
※ 大戶ΔWoW / EPS年增 需累積歷史才完整(剛上線可能多為「-」);此時以籌碼+動能+月營收為主即可。
※ 相對同業 = 該股 5 日漲幅 − 同產業中位數(>0 = 比同業強,輪動領先);借券回補 = -(融券+借券賣出餘額日變化)/20日均量 %(正 = 空方還券回補,常見軋空/反彈;負 = 空方加碼放空)。

【Python 已整合新聞後的五檔候選與股價預測】
{attention_top_block}
※ 這五檔已將「結構分 + 新聞事件 + 產業中性 + 勝過大盤機率 + 3 日預期報酬 + 模型品質」整合完成。3 日 / 5 日預測價為可回測的保守點估計，方括號為 80% 參考區間。
※ 這些資料只供「我的明確立場」引用風險與市場主題；不要撰寫「今日台股關注五檔」段落，因為信件底部 Top5 卡片會由 Python 統一顯示。

【短線候選初步追蹤（晨報快照間報酬，尚未完成正式 walk-forward 校準）】
{breakout_tracking_block}

【完整 point-in-time walk-forward 指標】
{walk_forward_block}

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
R12. **個股動態以「具體事實 + 透明標記」為原則**:「科技板塊脈動」每一條敘述,**優先用具體事實**(明確產品/合約/數字/法說發言/SEC 表單編號 / MOPS 公告)。
- **A 級(有具體事實)**:照寫,信心可給「中-高」。範例:「Broadcom 宣布 Anthropic 80 億美元 ASIC 合約,盤後 +4.5%」
- **B 級(只有方向性訊號,如分析師喊買 / 動能標題 / 法人買超)**:**可寫,但須明確標註「資訊有限」並降為「低-中」信心**。範例:「NVIDIA 昨日外資買超 12,000 張(籌碼面正向,但今日無具體公司消息,信心:中-低)」
- **C 級(只有「揭露意外真相」「迎來轉折」「市場關注」這類沒內容的標題)**:不要寫。
- **重點:不要把 B 級當 C 級砍掉** — 籌碼 / 分析師動向也是有用的訊號,只是要透明標記。
- 輸出前自我檢查:每句話的「資訊強度」(A/B 級)是否與信心等級相符;若寫了 B 級卻給高信心 = 失敗報告。
R13. **美股休市日 → 美股訊號必須標 stale 給 0 分**:若【市場警告】中出現「美股昨日休市」警告,代表 QQQ/TSM/SOX/VIX/VIX9D/NQ/ES/WTI/黃金/10Y/DXY/13W 全部都是**上一個美股交易日的延續值,不是昨日新資訊**。在「我的明確立場」段的 11 維加減分中:
- 所有美股相關維度(QQQ/SOX/VIX/TSM ADR/NQ/VIX9D/WTI/10Y)的分數**強制給 0**,並在該維度後加 `[stale]` 標籤
- 僅信任 TW 本地維度(外資市值前 10 大、外資台指期、市場廣度)
- 信心等級**強制改為「低」**,「我的明確立場」段的理由必須首句明寫「**今日美股休市,美股訊號 stale**」
- 預測模型仍會跑但「2330/00662/加權」的開盤關鍵價位建議寬度應加大 (±1.5% 而非 ±1%)
違反此規則 = 失敗報告。
R14. **2330 / 0050 / 加權一律新台幣計價，且數字必須合理**:2330 台積電本地股價在「數千元(約 2000–2500)」量級;
凡寫到 2330 的價位,**只能引用上方 Python 已注入的新台幣中樞/關鍵價位**(見「我的明確立場」段),
**嚴禁自行計算或改用台積電 ADR 的美元價(約 400–450 美元)**。若你寫出的 2330 價位落在 400–500,
代表你把美股 ADR 美元價誤當成台股新台幣價 = **失敗報告**。00662/0050/加權同理,一律新台幣、需與上方數字一致。
所有金額/目標價/點位一律用**正常數字與千分位**(如 3,242 元、45,577 點),**嚴禁出現位數錯位或多餘逗號**(如『3,2424』『1,2,345』);不確定的具體數字寧可不寫,不可亂湊。

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
5. 外資市值前 10 大昨日合計買超 > 0: +1；< 0: -1
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

## 七、昨夜三大重點

**用 3 條 bullet，每條 ≤ 50 字**。
必須涵蓋（按優先序）：
1. **最影響 00662 的事件**（美股科技股 / Fed / 半導體政策）
2. **最影響 2330 的事件**（TSM 動向 / 台積電供應鏈消息 / 半導體出口管制）
3. **第三個總經或地緣風險事件**

每條必須附上**具體數據或來源**（例：「Nvidia 盤後 +2.3% 因 Mag7 ASIC 訂單超預期 [CNBC]」）

## 七之二、世界大事速覽（3-5 條;**股市之外的世界**）

讓讀者一眼掌握「昨天世界發生了什麼」。取材以【昨日世界大事新聞】為主,輔以其他新聞中的重大非市場事件。涵蓋(有才寫):重大地緣衝突/停火、大選政變、重大災難、科學/太空/醫藥里程碑、AI 重大發布、影響深遠的社會/制度變化。

**鐵則**:
1. 每條=「發生什麼(具體事實+數字+來源媒體)」+「為什麼重要」,合計 ≤60 字。
2. 七、已寫過的市場事件**不重複**——這段寫的是「市場之外的世界」;確有市場影響者以半句帶過並指向對應段落即可。
3. **只寫「已發生」的事**:科學/醫藥只寫已完成的里程碑(發射成功/核准上市/試驗解盲/得獎),**禁止**把「研究中/有望/可能」寫成突破;災難寫具體災情數字(死傷/停班/規模),不誇大不渲染。
4. 來源 A/B/C 分級照 R12;寧可 3 條紮實,不要 5 條灌水;昨日確無大事就寫「昨日世界相對平靜」一行帶過。

## 七之三、未來 48 小時關鍵事件情境（**最多 3 個**;無事件則整段省略,不留空標題）

未來約 1–2 個交易日的重要行事曆事件（**僅以下清單為準**,不可自行新增事件、不可編造未列出的數字）：
{event_scenario_lines}

若上方清單為「無重大排程事件」,則本段只寫一行「未來 48 小時無重大排程事件」即可。
否則,挑出「最可能牽動台股/00662/2330」的 ≤3 個事件,每個事件寫成一小段(每段 ≤4 行):
- **事件與時間**:照抄清單的名稱與日期時間。
- **基準預期**:**只能引用**清單內的「預期 X / 前值 Y」;清單**沒有**預期值的事件,寫「無市場共識預期,僅關注方向」,**嚴禁自己編一個數字**。
- **偏多情境 / 偏空情境**:只寫「數據高於/低於 預期(或前值)時,對成長股(00662/2330/加權)偏多或偏空」的**方向與一句話傳導機制**——例:「CPI 低於預期 → 降息預期升溫 → 成長股估值折扣收斂 → 偏多 00662」。**禁止**寫出「XX 以上就漲 Y%」這種自創的數字門檻。
- **最受影響**:限 00662 / 0050 / 2330 / 加權 其中一或多個。
- **失效條件**:一句話——什麼情況會讓上面的判斷作廢(如「若同日 Fed 官員鷹派發言蓋過數據」)。

**鐵則**:本段是「若…則…」的條件式沙盤,不是預測;所有門檻一律以「相對預期/前值的高低方向」表述,不得出現任何自創的絕對數字目標。

## 七之四、敘事變化（昨日觀點 vs 今日新證據;**無昨日紀錄則整段省略**）

{narrative_delta_block}

對照上方昨日紀錄與今日的新聞/數據,用 **≤5 行**說明:昨日的哪些判斷/事件今日被**強化**(有新證據支持)、
哪些被**推翻/降溫**(出現反向證據)、哪些**無進展**(今日沒有新消息);若今日立場與昨日不同,補一句「為何轉變」。
**鐵則**:昨日部分只能引用上方【昨日本報敘事回顧】的原文,**不可**替昨日補記它沒說過的話;今日部分必須引用今日新聞/數據。
若上方為「(無昨日紀錄可對照)」,本段只寫一行「無昨日紀錄可對照」即可。

{weekly_review_section}
## 八、科技板塊脈動（**7–10 條,最多 12 條**;有料就寫滿,沒料 7 條也可)

**重要**:寫 7-10 條;只有 A 級具體事實很多時才可到 12 條。R12 已放寬:B 級資訊也可寫但須明確標註信心降級。
本段**只寫科技/半導體類股**(00662 與 2330 相關);非科技類股一律寫在下方「九、其他類股資訊」,不要混在這裡。
**台積電自家動態優先且可加深**(使用者核心持股):新聞素材中凡屬 2330 自家的**財報/月營收數字、
法說會(展望/資本支出/毛利率指引)、先進製程(N2/A16)、CoWoS 先進封裝、海內外擴產、大客戶訂單**,
一律優先入選,可寫 **2-3 條**深入分析(其他公司仍每家至多 1 條);法說/財報季時把「數字 vs 市場預期」
的差距講清楚,不可只寫「符合預期」帶過。

**深度鐵則（每條必須三段式因果鏈，否則就是填充垃圾）**：
1. **事件**：發生什麼——具體產品 / 合約 / 財報數字 / 法說發言 / SEC 表單 / MOPS 公告 ＋ 來源。
2. **傳導機制**：為何牽動 2330 / 00662——必須點名**具體機制**（CoWoS / HBM / 先進製程 N2 / 稼動率 / AI 伺服器拉貨 / 匯率 / 出口管制），不是只說「有正面影響」。
3. **方向＋幅度＋信心**：利多 / 利空 / 中性、幅度大小、A/B 級＋信心。
**禁止**只寫「影響中性」「中性偏正」「有帶動作用」這類沒有機制的空話——那等於沒分析。

**沒有真正公司新聞時的正確處理**：不要硬湊 8-12 檔、逐一只報「盤後漲跌 X%」充數（那是失敗報告）。
改寫 **2-3 個產業主題**（如「AI 伺服器拉貨」「記憶體報價」「先進製程稼動率」），引用上方新聞 / 8-K 的具體數字；真的沒料就誠實少寫幾條。

每條格式（嚴格遵守）：
**公司中英文名（一句話業務簡介）**：[事件＋數字＋來源] ＋ [傳導機制] ＋ **資訊強度(A/B)＋信心(高/中/低)**

範例 A 級(具體事實＋機制):
**Broadcom（AVGO，全球前三大半導體 IP 設計商，主導 AI ASIC 客製晶片）**：宣布獲 Anthropic 80 億美元算力訂單，AVGO 盤後 +4.5%。ASIC 量產倚賴台積電 N3 / CoWoS，直接墊高 2330 2026 先進封裝訂單能見度（CoWoS 產能仍吃緊）。**[A 級・信心:高]**

範例 B 級(只有方向性訊號,但仍點出機制):
**NVIDIA(NVDA,GPU/AI 加速器龍頭)**：無原始數字，惟鉅亨報導分析師上修目標價（分析師動向）。GPU 出貨增量會經 CoWoS / HBM 傳導到 2330 稼動率，故對 2330 偏正、但僅屬方向性。**[B 級・信心:中-低,資訊有限]**

## 九、其他類股資訊（金融 / 航運 / 生技 / 汽車 / 傳產原物料 / 營建資產 / 重電綠能 / 觀光內需，含台灣與全球；**目標 6–10 條**）

聚焦非科技類股的昨日重大動態。**依【類股熱度表】的今日成交熱度排序**：優先寫「今日成交熱、且【其他類股最新新聞】確有實質新聞事件」的類股——不限傳統四大類，若傳產/營建/重電/觀光今日有真新聞就寫進來。取材以上方【其他類股最新新聞】各類股分組標題為主;**金融條目另可取材【重點公司最新新聞】中 [2881]/[2882]/[2891] 的條目**(金控深度覆蓋,使用者指定)。熱度表只當背景(判斷哪類在動、誰領漲),**不可**把熱度表的漲跌數字單獨當一條新聞。

**鐵則（務必遵守，違反即為失敗報告）**：
1. **每條必須是一則真正的「新聞事件」**——寫出「發生了什麼事」（政策 / 財報 / 合約 / 併購 / 運價 / 新藥進度 / 車市數據 / 鋼價塑化報價 / 房市政策 / 電網儲能標案 / 觀光客流 / 國際大事…），並引用標題裡的具體內容、數字與來源媒體。
2. **嚴禁**把「股價漲跌 X% / 法人買賣超 X 張 / 營收年增率 Y%」單獨當成一條——那些是量化數據、別的段落已涵蓋，**不算類股新聞**。若某類股當日你手上只有股價 / 法人數據而沒有新聞，**寧可略過該類股**，也不要拿數據湊數。
   - **唯一例外「行情觀察」條目(全節最多 1 條)**:當【類股熱度表】顯示某類股出現**極端異動**(中位數 |漲跌| 大、或成交佔比異常放大)而新聞標題無對應事件時,可寫一條標記為**【類股名｜行情觀察】**的條目——必須(1)引用熱度表的具體數字、(2)給出機制推論並標明是推論(如「反映利差承壓,屬推論」)、(3)結尾標 **[行情觀察・信心:低-中]**。來源就寫「類股熱度表」——這張表信件內有刊出,讀者查得到。
3. **只寫確有實質新聞的類股，沒有就略過該類**（避免信件冗長）;有全球重大新聞的類股(金融/航運/生技/汽車)再補全球，傳產/營建/重電/觀光以台灣在地事件為主。**不可跨類張冠李戴**（例：航運就寫運價 / SCFI / BDI / 長榮 / 陽明 / 塞港，**不要拿油價或別類消息充當航運**）。
4. **影響說明必須具體**：要寫「利多/利空了誰、透過什麼機制、幅度多大」。**禁止**「對 X 類股有帶動作用」「情緒帶動」「中性偏正」這類無機制空話——沒講出機制就等於沒分析。
   - **航運**判斷「利多/利空幅度」時可援引油價(WTI,燃油成本)與匯率(USD/TWD)作背景(上方總經區有數據),但「新聞事件」本身仍須是運價/航商/塞港動態,油價匯率只當佐證、不可單獨充當航運新聞。
   - **金融(壽險/金控)**請扣連使用者熟悉的傳導鏈:美股/美債走勢→壽險投資收益、央行利率→銀行淨息差;能寫出這條鏈才算合格。
     **國泰金(2882)/中信金(2891) 為本段核心觀察**(使用者指定):【重點公司最新新聞】與
     【台股重點公司 MOPS 重大訊息】中凡屬這兩家的**月獲利/財報數字、人事異動(董總/子公司高層)、
     重大投資或併購、增資/配息、金管會裁罰**,優先入選且各可獨立成條——MOPS 公告(代號 2882/2891
     開頭者)為公司自行申報的 A 級來源,人事異動與重大投資公告多只出現在這裡,務必檢查、有就寫;
     獲利數字要給 YoY/EPS 等具體值,人事/投資要點出對後續營運的意涵,不可一句帶過。
   - **生技/醫療(本報讀者為醫師,請特別著墨且寫得具體)**:事件優先序 FDA/EMA 核准或里程碑 > 臨床試驗解盲/進度 > 健保給付 > 併購/授權;機制要明確——新藥上市→專利獨佔期營收、解盲成敗→股價常 ±15–30%、納入健保→營收確定性提升。**禁止**「生技基金看好」「長線可期」這類無事件、無機制的空話。
   - **傳產原物料(鋼鐵/塑化/水泥)**:機制走「報價/景氣循環」——鋼價或塑化利差變動→中鋼/台塑四寶毛利,中國需求/反傾銷/油價成本是背景;寫得出報價方向與利差傳導才算合格。
   - **營建資產/房市**:機制走「房市政策/預售買氣/資產題材」——升降息與選擇性信用管制→建商推案與去化,土地開發/都更/資產活化是個股催化。
     **房市寫「全台+中彰投在地」雙軌**(使用者居住台中/彰化,2026-07-15 指定):
     【房市-中彰投】【建設-中彰投】分組的素材——台中/彰化/南投草屯/斗六的房市買氣、交易熱區、
     重大公共建設(如中捷藍線、中科擴建)——**有素材必寫 1 條**,寫清楚「哪一區、買氣/價格
     方向、什麼建設題材」;此條屬生活+資產配置情報,可不綁個股、不用湊機制傳導。
   - **重電綠能**:機制走「電網強韌計畫/台電標案/儲能離岸風電」——電力基建資本支出→重電三雄(華城/士電/中興電)在手訂單能見度。
   - **觀光內需**:機制走「客流/客運量/內需消費」——來台/出國旅客與航空客運載客率→觀光航空營收,零售看內需景氣。
5. **可信度分級**:來源可用 A(主管機關/公司公告/法說)、B(主流財經媒體)、C(聚合/未具名來源)三級;C 級或僅方向性者必須明確標「信心:低」。

每條格式（嚴格遵守）：
**【類股｜台灣/全球】公司或主題（一句話簡介）**：發生什麼（具體事件＋數字／來源媒體）＋ 影響（**點名利多/利空對象＋傳導機制＋幅度**）＋ **資訊強度(A/B/C)＋信心(高/中/低)**（C 級或僅方向性者一律標信心:低）

範例（皆為新聞事件，不是股價/法人數據）：
**【金融｜台灣】壽險業（國泰、富邦等大型壽險）**：金管會公布壽險業前 4 月稅後大賺約 1,945 億元、投資收益為最大推手（經濟日報）。利多壽險金控、對加權指數金融權值有撐。**[A 級・信心:中]**
**【金融｜全球】美國銀行股**：市場焦點由非農數據轉向通膨與 Fed 決策，地區銀行評價受關注（鉅亨）。傳導須分流:殖利率下行壓縮銀行淨息差(偏空銀行),但有利債券評價與壽險投資收益(偏多壽險)——勿一概而論「利多金融股」。**[B 級・信心:低，資訊有限]**
**【航運｜全球】貨櫃運價**：上海出口集裝箱運價指數(SCFI)週漲 X%、紅海擾動推升歐美線運價（工商時報）。對長榮、陽明屬中性偏正。**[A 級・信心:中]**
**【生技｜台灣】某新藥廠**：旗下新藥獲納入健保 / 取得 FDA 里程碑（UDN）。屬個股重大利多、帶動生技類股情緒。**[A 級・信心:中]**
**【汽車｜全球】特斯拉（TSLA，全球電動車龍頭）**：Robotaxi 取得進展但股價受晶片股拖累（MoneyDJ）。電動車供應鏈台廠（和大、貿聯-KY）可留意。**[B 級・信心:中-低]**

**不可**與 00662 / 2330 硬扯傳導；改從「該類股 / 相關台股 / 整體市場」的角度說明。R12 的 A/B/C 級透明標記規則同樣適用(只有「迎來轉折」「市場關注」這類沒內容的 C 級標題不要寫)。

## 十、總體經濟與政策環境（**精簡段:全節 8 句內**;收盤值/變動%上方「總經指標」卡已完整列出,本節**只寫解讀,不重抄數字表**）

分三小段（每段 2-3 句，禁止超過;(C) 有 geo_critical 事件時可放寬至 4 句）：

**(A) 美國利率/美元/VIX/通膨**：
解讀 VIX、10Y、DXY、SOX 對今日風險偏好的意義(引用關鍵數字即可,不逐項重列)。如有 CPI/PPI/就業數據釋出，必列數字。

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
QQQ X.X% [±1/0]、SOX X.X% [±1/0]、VIX X [±1/0]、TSM ADR X.X% [±1/0]、外資市值前10大合計 [±1/0]、外資台指期 [±1/0]、10Y X bps [±1/0]、NQ X.X% [±1/0]、VIX9D/VIX X.XX [±1/0]、WTI X.X% [±1/0]、市場廣度 X% [±1/0] = 淨分 X
```

**第 2 行 — 立場標籤**：
> **立場：偏多 / 偏空 / 中性**（按淨分自動判定）

**第 3 行 — 理由（3-5 句）**：說明為什麼是這個立場，每句必附數據。**至少一句要寫出「傳導機制」而非只給結論**——把指標一路推到使用者持股,例:「VIX 16.2(低檔)→成長股估值折扣收斂→00662/NASDAQ 風險資產定價偏多」「SOX +5.45% → 台積電 ADR 連動 → 2330 開盤有撐」。禁止只寫「VIX 低 → 偏多」這種沒有中間鏈的跳論。

**第 4-6 行**（**每行獨立成段，中間空行**）：

> **2330 開盤關鍵價位**：{key_2330_line}

> **00662 操作建議**：{key_00662_line} 在此基礎上明確寫「加碼 / 觀望 / 減碼」。

（上兩行的價位數字由 Python 計算:**原樣引用、不可自行更動、不可改用 ADR 美元價**;
 這段括號說明是給你的指令,**不要抄進輸出**。）

> **主要風險**：1 句話點出最可能讓今日預測失效的單一事件

## 十三、一句話總結

20 字內。給一句**具體可執行**的結論（含立場 + 動作）。
**立場用詞必須與第十二段「立場標籤」完全一致（偏多／偏空／中性）——不可另創說法**
（不要用「樂觀/保守/審慎」等同義詞改寫,讓讀者一眼看到同一個立場詞；
 風險或操作紀律可在動作裡補述,但開頭立場詞要一致）。

範例：「偏多操作 00662，2330 守穩 {_mid2330_txt} 元逢回加碼」（**2330 價位請用上方 Python 提供的新台幣中樞值，不可寫成美元 ADR 價**）
"""


def _call_gemini_once(model: str, prompt: str) -> str:
    """單次呼叫 Gemini REST。失敗時直接 raise，由外層處理重試/降級。"""
    if not GEMINI_API_KEY:
        raise RuntimeError("缺 GEMINI_API_KEY 環境變數")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": max(8192, LLM_REPORT_MAX_TOKENS),
        },
    }
    r = requests.post(url, json=payload, timeout=_llm_request_timeout(),
                      headers={"x-goog-api-key": GEMINI_API_KEY})
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
                last_err = RuntimeError(_http_error_summary(e))
                if code in RETRY_STATUS_CODES and attempt < 3:
                    wait = 5 * (3 ** (attempt - 1))   # 5, 15, 45
                    print(f"[llm] HTTP {code} 暫時故障，{wait}s 後重試", file=sys.stderr)
                    _llm_sleep(wait)
                    continue
                print(f"[llm] {model} 最終失敗: {last_err}", file=sys.stderr)
                break  # 進入下一個 fallback 模型
            except Exception as e:
                last_err = e
                print(f"[llm] {model} 異常: {_redact_secret_text(str(e))}", file=sys.stderr)
                if attempt < 3:
                    _llm_sleep(5)
                    continue
                break
    raise RuntimeError(f"Gemini 所有降級模型皆失敗: {last_err}")


def _call_anthropic(prompt: str) -> str:
    """Claude Sonnet 付費 API。"""
    import anthropic  # 延後 import，未用就不需安裝
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("缺 ANTHROPIC_API_KEY 環境變數")
    client = anthropic.Anthropic(
        api_key=ANTHROPIC_API_KEY,
        timeout=_llm_request_timeout(),
    )
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=LLM_REPORT_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def _call_deepseek(prompt: str) -> str:
    """
    DeepSeek API (OpenAI 相容 chat completions 介面)。
    支援重試與降級：deepseek-v4-pro → deepseek-v4-flash。
    每月成本估算（22 次/月、5000 tokens 輸入、3500 輸出）：
      - deepseek-v4-flash: 約 NT$1-3
      - deepseek-v4-pro:   約 NT$4-6
    """
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("缺 DEEPSEEK_API_KEY 環境變數")

    # 模型降級鏈：主模型不穩時依序往下試
    # v4-pro (旗艦) → v4-flash (輕量)
    fallback_models = [DEEPSEEK_MODEL]
    for alt in ("deepseek-v4-flash",):
        if alt not in fallback_models:
            fallback_models.append(alt)

    # prompt 長度 log:400 多半是「內容過長(context overflow)」或「參數不被接受」,
    # 印出長度有助診斷(中文約 1.5-2 字/token,40000 字 ≈ 20-27K tokens)。
    print(f"[llm] DeepSeek prompt 長度 {len(prompt):,} 字")
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{DEEPSEEK_BASE_URL}/v1/chat/completions"
    last_err: Optional[Exception] = None
    for model in fallback_models:
        slim = False    # 收到 400 後切「精簡模式」:去掉 thinking/reasoning_effort + 降 max_tokens
        attempt = 0
        while attempt < 3:
            attempt += 1
            try:
                print(f"[llm] 嘗試 DeepSeek model={model} attempt={attempt}"
                      f"{' (slim)' if slim else ''}")
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 4096 if slim else LLM_REPORT_MAX_TOKENS,
                    "stream": False,
                }
                # v4-pro / reasoner 思考模式（精簡模式下停用,以排除參數造成的 400）
                if (not slim
                        and DEEPSEEK_REASONING_EFFORT not in ("", "off", "none", "disabled")
                        and ("pro" in model or "reasoner" in model)):
                    payload["thinking"] = {"type": "enabled"}
                    payload["reasoning_effort"] = DEEPSEEK_REASONING_EFFORT
                r = requests.post(
                    url, json=payload, headers=headers,
                    timeout=_llm_request_timeout(),
                )
                r.raise_for_status()
                data = r.json()
                choices = data.get("choices") or []
                if not choices:
                    raise RuntimeError(f"DeepSeek 回應無 choices: {data}")
                content = choices[0].get("message", {}).get("content")
                if not content:
                    raise RuntimeError(f"DeepSeek 回應無 content: {data}")
                usage = data.get("usage", {})
                print(f"[llm] DeepSeek 成功 — tokens: prompt={usage.get('prompt_tokens')} "
                      f"completion={usage.get('completion_tokens')} "
                      f"cache_hit={usage.get('prompt_cache_hit_tokens', 0)}")
                return content
            except requests.exceptions.HTTPError as e:
                code = e.response.status_code if e.response is not None else None
                # 關鍵:印出 DeepSeek 回傳的錯誤內文(含具體原因),並帶進 last_err 讓信件看得到
                body = ""
                try:
                    body = (e.response.text or "")[:400] if e.response is not None else ""
                except Exception:
                    body = ""
                last_err = RuntimeError(_redact_secret_text(
                    f"HTTP {code}: {body}" if body else str(e)))
                print(f"[llm] DeepSeek {model} HTTP {code}: {_redact_secret_text(body)}",
                      file=sys.stderr)
                if code == 400 and not slim:
                    # 400 → 改精簡 payload(去 reasoning + 降 tokens)立即重試,排除參數/長度問題
                    print("[llm] DeepSeek 400 → 改用精簡 payload 重試", file=sys.stderr)
                    slim = True
                    attempt -= 1     # 這次不算入重試次數
                    continue
                if code in RETRY_STATUS_CODES and attempt < 3:
                    wait = 5 * (3 ** (attempt - 1))
                    print(f"[llm] DeepSeek HTTP {code}，{wait}s 後重試", file=sys.stderr)
                    _llm_sleep(wait)
                    continue
                break
            except Exception as e:
                last_err = e
                print(f"[llm] DeepSeek {model} 異常: {_redact_secret_text(str(e))}",
                      file=sys.stderr)
                if attempt < 3:
                    _llm_sleep(5)
                    continue
                break
    raise RuntimeError(f"DeepSeek 所有模型皆失敗: {last_err}")


def _fallback_analysis_text(news: list[dict], err: Exception) -> str:
    """LLM 完全失敗時的備援文字。仍提供原始新聞清單與錯誤說明。"""
    top_news = "\n".join(
        f"- [{n['source']}] {n['title']}"
        for n in news[:20]
    )
    return f"""## LLM 服務暫時不可用

今日早晨 LLM API 多次重試均失敗，已自動降級寄出基本版報告。錯誤訊息：
`{type(err).__name__}: {_redact_secret_text(str(err))[:200]}`

## 一、原始新聞清單（供你自行判讀）

{top_news}

## 二、提示

請直接看上方「美股收盤行情」「00662 公允價」「2330 雙模型預測」三個區塊做判斷。
若情況持續，可考慮：
- 切換 LLM_PROVIDER 為 anthropic（Claude 付費版較穩）
- 等待數小時後 Gemini 服務恢復
"""


def _analysis_complete_enough(text: str) -> bool:
    """Detect obvious report truncation before rendering/sending.
    除了需含「我的明確立場」「一句話總結」兩段,還要求立場可被解析(有淨分或立場詞),
    否則頂部 KPI/結論卡會變「—」——這種輸出視為不完整,觸發重試。"""
    body = _strip_llm_watchlist_section(text or "")
    if "我的明確立場" not in body or "一句話總結" not in body:
        return False
    st = _extract_stance(body)
    return st.get("score") is not None or bool(st.get("label"))


def _call_llm_text(prompt: str) -> str:
    """Dispatch an LLM task without mixing extraction and report-writing prompts."""
    if LLM_PROVIDER == "anthropic":
        return _call_anthropic(prompt)
    if LLM_PROVIDER == "deepseek":
        return _call_deepseek(prompt)
    return _call_gemini(prompt)


def _call_deepseek_extractor(prompt: str) -> str:
    """Use one short, non-reasoning call so extraction stays bounded in Actions."""
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY 環境變數")
    response = requests.post(
        f"{DEEPSEEK_BASE_URL}/v1/chat/completions",
        json={
            "model": DEEPSEEK_EXTRACTOR_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 1200,
            "stream": False,
        },
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        timeout=45,
    )
    response.raise_for_status()
    choices = (response.json().get("choices") or [])
    content = (choices[0].get("message") or {}).get("content") if choices else None
    if not content:
        raise RuntimeError("DeepSeek extractor 回應缺少 content")
    return content


_LLM_EVENT_TYPES = {"guidance_raise", "guidance_cut", "orders", "earnings",
                    "revenue_growth", "export_controls", "litigation", "geopolitical", "general"}


def _validate_llm_events(events: list) -> tuple[list, int]:
    """驗證 LLM 抽取事件 schema:event_type 屬允許集合、direction ∈ {-1,0,1}、entity 為字串或缺。
    回 (合格清單, 丟棄數)。不合格項丟棄(寧缺勿濫,避免髒事件污染下游計分/去重)。"""
    valid, dropped = [], 0
    for ev in events or []:
        if not isinstance(ev, dict):
            dropped += 1
            continue
        try:
            direction = int(ev.get("direction"))
        except (TypeError, ValueError):
            direction = None
        entity = ev.get("entity")
        if (str(ev.get("event_type") or "") in _LLM_EVENT_TYPES
                and direction in (-1, 0, 1)
                and isinstance(entity, (str, type(None)))):
            valid.append(ev)
        else:
            dropped += 1
    return valid, dropped


def call_llm_event_extractor(news: list[dict], mops: list[dict]) -> list[dict]:
    """Run one bounded extractor call, then merge its output with deterministic events."""
    deterministic = extract_structured_events(news, mops)
    if os.environ.get("LLM_EVENT_EXTRACTION", "1") != "1":
        return deterministic
    if not any((DEEPSEEK_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY)):
        return deterministic
    now_utc = dt.datetime.now(dt.timezone.utc)

    def _extractor_priority(item: dict) -> tuple:
        source_grade = item.get("source_grade") or _news_source_grade(item)
        importance = {"critical": 4, "high": 3, "normal": 1}.get(
            str(item.get("importance") or "normal"), 1)
        parsed_published = _parse_news_time_required(item.get("published"))
        published = parsed_published or (now_utc - dt.timedelta(days=7))
        age_hours = max(0.0, (now_utc - published).total_seconds() / 3600)
        return (
            source_grade == "A",
            importance,
            bool(item.get("fulltext")),
            bool(item.get("company_label")),
            parsed_published is not None,
            -age_hours,
            len(str(item.get("summary") or "")) + len(str(item.get("fulltext") or "")),
        )

    ranked_items = sorted(
        list(mops or []) + list(news or []),
        key=_extractor_priority,
        reverse=True,
    )
    compact_items = [{
        "source": item.get("source"),
        "source_grade": item.get("source_grade") or _news_source_grade(item),
        "company_label": item.get("company_label"),
        "published": item.get("published"),
        "title": str(item.get("title") or "")[:180],
        "summary": (str(item.get("fulltext") or item.get("summary") or "")[:360]),
    } for item in ranked_items[:35]]
    prompt = (
        "You are a financial-news event extractor. Return JSON only: an array of at most "
        "30 objects. Each object must have entity, event_type, direction, confidence, "
        "surprise_score, lifecycle, "
        "title, source, published. direction is -1, 0, or 1. Use only supplied evidence. "
        "Prefer official disclosures over media rewrites. Merge duplicates. "
        "lifecycle must be rumor, confirmed, implemented, or withdrawn. "
        "surprise_score is 0.1 to 1.0: use a low score for already-expected news. "
        "Allowed event_type: guidance_raise, guidance_cut, orders, earnings, "
        "revenue_growth, export_controls, litigation, geopolitical, general.\nINPUT:\n"
        + json.dumps(compact_items, ensure_ascii=False, separators=(",", ":"))
    )
    def _call(p: str) -> str:
        return (_call_deepseek_extractor(p) if LLM_PROVIDER == "deepseek"
                else _call_llm_text(p))

    try:
        parsed = _parse_llm_event_json(_call(prompt))
        valid, dropped = _validate_llm_events(parsed)
        if dropped:
            print(f"[llm-extractor] 丟棄 {dropped} 個不合格事件(schema)", file=sys.stderr)
        # 有解析出事件卻全數不合格 → 帶嚴格提醒重試一次(空陣列=合法「無事件」,不重試;成本上限 +1)
        if parsed and not valid:
            print("[llm-extractor] 全數不合格 → 重試一次", file=sys.stderr)
            valid = _validate_llm_events(_parse_llm_event_json(_call(
                prompt + "\nSTRICT REMINDER: output ONLY a JSON array; every event_type MUST be one of "
                "the allowed list above; direction MUST be exactly -1, 0, or 1.")))[0] or valid
        return extract_structured_events(news, mops, llm_events=valid)
    except Exception as e:
        print(f"[llm-extractor] fallback to deterministic events: {e}", file=sys.stderr)
        return deterministic


def call_llm_analysis(quotes: dict, fair: dict, predictions: dict,
                      news: list[dict], tw0050: list[dict] | None = None,
                      calibration: str = "") -> str:
    """Run report generation inside one shared wall-clock budget."""
    global _LLM_DEADLINE
    previous_deadline = _LLM_DEADLINE
    _LLM_DEADLINE = time.monotonic() + max(1.0, LLM_TOTAL_TIMEOUT_SECONDS)
    try:
        return _call_llm_analysis_impl(
            quotes, fair, predictions, news, tw0050, calibration)
    finally:
        _LLM_DEADLINE = previous_deadline


def _call_llm_analysis_impl(quotes: dict, fair: dict, predictions: dict,
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
        text = _call_llm_text(prompt)
        if _analysis_complete_enough(text):
            return text
        print("[llm] 分析輸出疑似截斷，改用短版提示重試一次", file=sys.stderr)
        concise_prompt = (
            prompt
            + "\n\n【長度控制追加規則】\n"
              "上一版容易過長。請完整輸出所有章節，但更短：世界大事速覽最多 4 條、每條一行;"
              "科技板塊脈動 6-8 條(只寫科技);"
              "其他類股資訊依類股熱度表挑今日在動且確有新聞的類股、每類 1-2 條、"
              "以真正的新聞事件為主(非股價/法人/營收數據),無新聞的類股略過;"
              "不要撰寫今日台股關注五檔，該區塊由 Python Top5 卡片處理；"
              "必須寫完我的明確立場與一句話總結。"
        )
        text = _call_llm_text(concise_prompt)
        if _analysis_complete_enough(text):
            return text
        raise RuntimeError("LLM concise retry output incomplete")
    except Exception as e:
        # 跨供應商備援:主供應商(通常 DeepSeek)整個掛掉時,若有 Gemini 金鑰就改用 Gemini,
        # 避免單一 API 故障(如 400/限流)導致整份分析空白。
        if LLM_PROVIDER != "gemini" and GEMINI_API_KEY:
            try:
                print(f"[llm] 主供應商失敗({type(e).__name__}),改用 Gemini 備援", file=sys.stderr)
                return _call_gemini(prompt)
            except Exception as e2:
                print(f"[llm] Gemini 備援也失敗: {_redact_secret_text(str(e2))}",
                      file=sys.stderr)
                return _fallback_analysis_text(news, e)
        print(f"[llm] 全部失敗，使用備援文字: {_redact_secret_text(str(e))}",
              file=sys.stderr)
        return _fallback_analysis_text(news, e)


# 向後相容別名（test_with_mock.py 等舊程式仍可運作）
call_claude_analysis = call_llm_analysis


# ---------- HTML 組版（Email 友善版） ----------


def _wrap_tw_picks(html: str) -> str:
    """把『今日台股關注五檔』段落包成琥珀色卡片，每檔個股做成獨立子卡片。"""
    marker = "今日台股關注五檔" if "今日台股關注五檔" in html else "今日台股關注三檔"
    if marker not in html:
        return html

    # 找該段開始（h2 含「今日台股關注五/三檔」）
    idx_six = html.find(marker)
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
                f"原值 {cal.get('raw')};屬追趕型修正,市場結構驟變時失效率較高）")
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


def _render_ma200_html(status: dict) -> str:
    """長線趨勢參考:核心持股站上/跌破 200 日均線(波段觀點,非買賣訊號)。無資料回空。"""
    if not status:
        return ""
    rows = []
    for v in status.values():
        above = v.get("above")
        tag = "站上(波段偏多)" if above else "跌破(波段轉弱)"
        color = "#dc2626" if above else "#16a34a"   # TW 紅漲綠跌
        lev_badge = (" <span style='color:#b45309;font-size:11px;font-weight:700;'>槓桿</span>"
                     if v.get("leveraged") else "")
        rows.append(
            f"<tr><td style='padding:7px 12px;border-bottom:1px solid #e2e8f0;font-weight:700;"
            f"color:#0f172a;font-size:13px;'>{v.get('name', '')}{lev_badge}</td>"
            f"<td style='padding:7px 12px;border-bottom:1px solid #e2e8f0;text-align:right;"
            f"font-size:12px;color:#64748b;font-variant-numeric:tabular-nums;'>"
            f"收 {v.get('close')} / MA200 {v.get('ma200')}</td>"
            f"<td style='padding:7px 12px;border-bottom:1px solid #e2e8f0;text-align:right;"
            f"font-weight:700;font-size:13px;color:{color};white-space:nowrap;'>"
            f"{tag} {v.get('dist_pct', 0):+.1f}%</td></tr>")
    return (
        '<div style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;margin:14px 0;">'
        '<div style="background:#f1f5f9;color:#475569;padding:8px 14px;font-weight:700;font-size:14px;">'
        '長線趨勢參考(200 日均線)</div>'
        '<table style="width:100%;border-collapse:collapse;background:#ffffff;">'
        + "".join(rows) + '</table></div>')
    # 註:回測背景(抗回撤定位/槓桿長抱風險)保留在 fetch_ma200_status docstring,
    # 信件註腳依使用者要求(2026-07-14)移除。


def _render_etf_action_card(fair_00662, pred_0050) -> str:
    """ETF 今日進出參考價(使用者核心需求:買入/賣出的相對合理價位,行動導向)。

    00662 帶寬 ±0.5%:公允淨值 = QQQ×匯率 的 NAV 套利錨,模型誤差小;
    0050  帶寬 ±1.0%:衍生自 2330+加權 預測,誤差較大故放寬。
    低於下緣 = 相對便宜(分批買入參考);高於上緣 = 相對偏貴(分批調節參考)。
    """
    # 手機版堆疊式:每檔一卡兩行(原 4 欄表在 390px 寬會擠爆)
    cards = []
    for label, center, band in (("00662 富邦NASDAQ", fair_00662, 0.005),
                                ("0050 元大台灣50", pred_0050, 0.010)):
        if not isinstance(center, (int, float)) or center <= 0:
            continue
        lo = round(center * (1 - band), 2)
        hi = round(center * (1 + band), 2)
        cards.append(
            f"<div style='padding:10px 14px;border-bottom:1px solid #e2e8f0;background:#fff;'>"
            f"<div style='font-weight:700;color:#0f172a;font-size:15px;'>{label}</div>"
            f"<div style='font-size:14px;margin-top:6px;line-height:1.9;'>"
            f"<span style='color:#15803d;font-weight:700;'>&lt; {lo} 可分批買</span>"
            f"<span style='color:#cbd5e1;'>　|　</span>"
            f"<span style='color:#475569;'>{lo}~{hi} 觀望</span>"
            f"<span style='color:#cbd5e1;'>　|　</span>"
            f"<span style='color:#b91c1c;font-weight:700;'>&gt; {hi} 偏貴</span></div>"
            f"</div>")
    if not cards:
        return ""
    return (
        '<div style="border:2px solid #0284c7;border-radius:10px;overflow:hidden;margin:14px 0;">'
        '<div style="background:#0284c7;color:#fff;padding:8px 14px;font-weight:700;font-size:15px;">'
        'ETF 今日進出參考價</div>'
        + "".join(cards) +
        '</div>')


def _render_portfolio_risk_html(risk: dict) -> str:
    """G1|持倉曝險卡(白話)。只顯示比例/情境/壓力,無任何持股明細。

    以 <!--PF_ROW_START/END--> 包裹 → archive_report_html 存檔時整卡去識別移除
    (曝險輪廓仍屬個人財務,repo 目前為 public,存檔不落地)。無資料回空字串。"""
    if not risk:
        return ""
    tw = risk.get("tw_beta")
    qqq = risk.get("qqq_beta")
    fx = risk.get("fx_beta")

    def _c(pct):   # TW 慣例:漲(正)紅、跌(負)綠
        return "#dc2626" if pct >= 0 else "#16a34a"

    lines = []   # 白話「連動」三行
    if tw is not None:
        direction = "同向" if tw >= 0 else "反向"   # 空頭/反向 ETF 可能為負,方向依係數符號
        lev = "(含槓桿,漲跌都放大)" if abs(tw) >= 1.3 else ""
        lines.append(f"整體大約等於 <b>{phrase_multiple(abs(tw))}</b> 台股大盤{lev}——"
                     f"台股大盤變動 1%,你的資產約{direction}變動 {abs(tw):.1f}%。")
    if qqq is not None:
        direction = "同向" if qqq >= 0 else "反向"
        lines.append(f"與<b>美股科技(那斯達克)</b>的連動:那斯達克變動 1%,"
                     f"你的資產約{direction}變動 {abs(qqq):.1f}%。")
    if fx is not None and abs(fx) >= 0.05:
        # 驅動固定為「台幣貶值(美元走強)」,方向由結果符號承載(正=資產受益、負=受損),
        # 不隨係數符號翻轉驅動字眼,避免「升值→負值」的雙重反轉錯誤(Codex review)。
        # 機制說明依符號分開寫:負值不是「海外部位換匯效果」(那必為正),而是貶值日
        # 常伴隨外資賣超/台股走弱,台股部位跌幅蓋過海外部位匯率受益(07-15 信實見 -1.8%)。
        fx_why = ("美元計價海外部位的換匯受益" if fx >= 0
                  else "貶值的日子常伴隨外資賣超、台股走弱,整體連動蓋過海外部位的換匯受益")
        lines.append(f"<b>匯率</b>:台幣每貶值 1%,你的資產約 "
                     f"<span style='color:{_c(fx)}'>{fx:+.1f}%</span>({fx_why})。")
    lines_html = "".join(
        f"<div style='padding:3px 0;color:#334155;'>{ln}</div>" for ln in lines)

    scen = risk.get("scenarios") or []
    scen_rows = "".join(
        f"<tr><td style='padding:6px 12px;border-bottom:1px solid #eef2f7;color:#334155;'>{s['label']}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #eef2f7;text-align:right;font-weight:700;"
        f"color:{_c(s['delta_pct'])};font-variant-numeric:tabular-nums;white-space:nowrap;'>"
        f"你的資產約 {s['delta_pct']:+.1f}%</td></tr>"
        for s in scen)
    scen_html = (
        "<div style='padding:8px 14px 2px;font-weight:700;color:#0f172a;font-size:13px;'>"
        "如果發生這些狀況(粗估、假設每次只動一個因素):</div>"
        f"<table style='width:100%;border-collapse:collapse;font-size:13px;'>{scen_rows}</table>"
        if scen_rows else "")

    stress = risk.get("stress") or []
    if stress:
        parts = "、".join(
            f"那斯達克 −{s['drawdown_pct']}% 時約 "
            f"<span style='color:{_c(s['delta_pct'])};font-weight:700;'>{s['delta_pct']:+.0f}%</span>"
            for s in stress)
        stress_html = (
            "<div style='padding:8px 14px;font-size:13px;color:#334155;line-height:1.8;'>"
            f"<b>歷史級大跌壓力測試</b>:{parts}。</div>")
    else:
        stress_html = ""

    cov = risk.get("cov_shown")
    n = risk.get("n_samples") or 0
    cov_txt = f"涵蓋你約 {cov*100:.0f}% 部位、" if isinstance(cov, (int, float)) else ""
    foot = (
        "<div style='padding:8px 14px;background:#f8fafc;font-size:12px;color:#94a3b8;line-height:1.6;'>"
        f"※ 以近 {n} 個交易日的實際連動估算;{cov_txt}為粗估非精準預測,漲跌方向相反、幅度相同。"
        "此區僅寄給你本人,存檔時自動移除,不含任何持股明細。</div>")

    return (
        "<!--PF_ROW_START-->"
        '<div style="border:1px solid #cbd5e1;border-radius:10px;overflow:hidden;margin:14px 0;">'
        '<div style="background:#334155;color:#fff;padding:8px 14px;font-weight:700;font-size:15px;">'
        '你的持倉曝險(白話估算)</div>'
        '<div style="padding:10px 14px;background:#fff;font-size:13px;line-height:1.7;">'
        f"{lines_html}</div>"
        f"{scen_html}{stress_html}{foot}"
        "</div>"
        "<!--PF_ROW_END-->")


def _render_world_evidence_html(signals: list) -> str:
    """G3|世界證據門檻警示卡:平日 signals 為空 → 回空字串(不顯示);異常時列白話一行。
    掛在總經卡下方,琥珀色提醒風格,明示「僅供參考」。"""
    if not signals:
        return ""
    import html as _h
    rows = "".join(
        f"<div style='padding:6px 0;color:#78350f;font-size:13px;line-height:1.6;'>"
        f"⚠ {_h.escape(str(s))}</div>" for s in signals)
    return (
        '<div style="border:1px solid #f59e0b;border-radius:10px;overflow:hidden;'
        'margin:12px 0;background:#fffbeb;">'
        '<div style="background:#f59e0b;color:#fff;padding:7px 14px;font-weight:700;font-size:14px;">'
        '市場結構訊號(異常時才出現)</div>'
        f'<div style="padding:8px 14px;">{rows}</div>'
        '<div style="padding:4px 14px 8px;color:#b45309;font-size:11px;">'
        '※ 顯示層啟發式門檻,僅供留意背景風險,非買賣訊號、不影響本報立場計分。</div>'
        '</div>')


def _fallback_stance_from_signals(quotes: dict) -> dict:
    """LLM 未輸出可解析的立場時,用 Python 訊號(加權預測共識/方向)給保底立場,
    避免頂部 KPI/加權區出現「—」。score 留 None(非 11 維淨分),label 標 source=signals。"""
    taiex = quotes.get("TAIEX_PRED") or {}
    consensus = str(taiex.get("consensus") or "")
    label = None
    if "偏多" in consensus:
        label = "偏多"
    elif "偏空" in consensus:
        label = "偏空"
    elif consensus:
        label = "中性"
    if label is None:
        wp = taiex.get("weighted_pct")
        if isinstance(wp, (int, float)):
            label = "偏多" if wp > 0 else ("偏空" if wp < 0 else "中性")
    return {"label": label, "score": None, "source": "signals"} if label else {}


def _render_summary_bar(summary: str, stance_detail: str, htmllib) -> str:
    """頂端「今日結論」卡:一句話總結(粗體)+ 立場敘述/關鍵價位/操作建議/風險。
    (使用者要求:十二、十三章內容直接上移到頂端,不在信件中段重複。)"""
    if not summary and not stance_detail:
        return ""
    import re as _re

    def _fmt(text: str) -> str:
        safe = htmllib.escape(text)
        safe = _re.sub(r"\*\*([^*\n]+?)\*\*", r"<b>\1</b>", safe)
        lines = [ln.strip().lstrip("&gt;").strip() for ln in safe.split("\n")]
        return "<br>".join(ln for ln in lines if ln)

    headline = (f"<div style='font-size:16px;color:#0f172a;font-weight:700;"
                f"line-height:1.55;'>{htmllib.escape(summary)}</div>" if summary else "")
    detail = (f"<div style='font-size:13px;color:#334155;line-height:1.8;"
              f"margin-top:8px;'>{_fmt(stance_detail)}</div>" if stance_detail else "")
    return f"""
          <tr>
            <td style="background:#fef3c7;border-top:3px solid #f59e0b;padding:16px 24px;">
              <div style="font-size:12px;letter-spacing:2px;color:#92400e;font-weight:700;text-transform:uppercase;margin-bottom:6px;">今日結論</div>
              {headline}
              {detail}
            </td>
          </tr>"""


def _html_escape_safe(s: str) -> str:
    import html as _h
    return _h.escape(str(s))


def _render_tw_intelligence_html(intelligence: dict, htmllib,
                                 include_policy: bool = True,
                                 include_medical: bool = True) -> str:
    """Render awareness-only Taiwan policy and medical sections.
    include_policy / include_medical 供 102KB 超標時依使用者優先序(先砍政策、再砍醫界)
    各自移除其一,不影響另一塊。"""
    if not intelligence or not (include_policy or include_medical):
        return ""

    def section(kind: str, title: str, color: str, background: str) -> str:
        items = intelligence.get(kind) or []
        diag = ((intelligence.get("diagnostics") or {}).get(kind) or {})
        sources = diag.get("sources") or {}
        html_undated = sum(_safe_number(stats.get("html_undated")) for stats in sources.values())
        date_missing = sum(_safe_number(stats.get("date_missing")) for stats in sources.values())
        date_parse_failed = sum(
            _safe_number(stats.get("date_parse_failed")) for stats in sources.values())
        source_errors = []
        rejected = []
        for source_name, stats in sources.items():
            for error in stats.get("errors") or []:
                if len(source_errors) < 4:
                    source_errors.append(f"{source_name}:{error}")
            for sample in stats.get("rejected_samples") or []:
                if len(rejected) < 3:
                    rejected.append(f"{sample.get('reason', '')}:{sample.get('title', '')}")
        diagnostic_html = (
            "<div style='padding:8px 14px;background:#f8fafc;color:#64748b;"
            "font-size:12px;line-height:1.5;border-top:1px solid #e2e8f0;'>"
            f"診斷：entries={htmllib.escape(str(diag.get('entries', 0)))}；"
            f"returned={htmllib.escape(str(diag.get('returned', 0)))}；"
            f"official_entries={htmllib.escape(str(diag.get('official_entries', 0)))}；"
            f"official_empty={htmllib.escape(str(diag.get('official_empty', 0)))}；"
            f"html_undated={htmllib.escape(str(int(html_undated)))}；"
            f"date_missing={htmllib.escape(str(int(date_missing)))}；"
            f"date_parse_failed={htmllib.escape(str(int(date_parse_failed)))}"
            + (f"<br>errors: {htmllib.escape('; '.join(source_errors))}" if source_errors else "")
            + (f"<br>rejected: {htmllib.escape('; '.join(rejected))}" if rejected else "")
            + "</div>"
        )
        # 診斷字串(entries/errors/rejected)僅供開發除錯,預設不放進正式信件;
        # 需要時設環境變數 TW_INTELLIGENCE_DEBUG=1(或 MORNING_REPORT_DEBUG=1)才輸出。
        if not (os.getenv("TW_INTELLIGENCE_DEBUG") or os.getenv("MORNING_REPORT_DEBUG")):
            diagnostic_html = ""
        if not items:
            empty_text = (
                "近一個月未抓到足夠的重要政策發酵資訊，建議仍以主管機關公告為準。"
                if kind == "policy"
                else "昨日未抓到足夠的重要公開資訊，建議仍以主管機關公告為準。"
            )
            rows = (
                "<div style='padding:12px 14px;color:#64748b;font-size:13px;'>"
                f"{empty_text}</div>"
            )
        else:
            rows = "".join(
                f"<div style='padding:12px 14px;border-bottom:1px solid #e2e8f0;'>"
                f"<div style='font-size:12px;color:#64748b;margin-bottom:4px;'>"
                f"{htmllib.escape(str(item.get('published', '')))} ・ "
                f"{htmllib.escape(str(item.get('scope', '昨日新訊')))} ・ "
                f"{htmllib.escape(str(item.get('topic', '')))} ・ "
                f"<b style='color:{'#15803d' if item.get('official') else '#a16207'};'>"
                f"{htmllib.escape(str(item.get('source_grade', '')))}</b> ・ "
                f"{htmllib.escape(str(item.get('status', '')))} ・ "
                f"重要性 {htmllib.escape(str(item.get('importance', '—')))}</div>"
                f"<a href='{htmllib.escape(str(item.get('link', '')))}' "
                f"style='font-size:14px;line-height:1.65;color:#0f172a;text-decoration:none;'>"
                f"{htmllib.escape(str(item.get('title', '')))}</a>"
                f"<div style='font-size:12px;color:#94a3b8;line-height:1.5;margin-top:4px;'>"
                f"入選原因：{htmllib.escape('、'.join(item.get('why') or ['寬召回分類']))}</div>"
                f"</div>"
                for item in items[:3]   # 使用者要求:政策/醫界各只挑最重要 3 篇(已依重要性排序)
            )
        return f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:{background};border-left:5px solid {color};border-radius:4px;">{title}</h2>
        <div style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;background:#ffffff;">
          {rows}
          {diagnostic_html}
        </div>"""

    policy_window = htmllib.escape(str(
        intelligence.get("policy_window") or intelligence.get("window") or "近一月"))
    medical_window = htmllib.escape(str(
        intelligence.get("medical_window") or intelligence.get("window") or "昨日"))
    intro_bits = []
    body = ""
    if include_policy:
        intro_bits.append(f"政策整理區間：{policy_window}")
        body += section("policy", "台灣政策近月走向", "#7c3aed", "#f5f3ff")
    if include_medical:
        intro_bits.append(f"醫界整理區間：{medical_window}")
        body += section("medical", "台灣醫界昨日走向", "#0891b2", "#ecfeff")
    intro = (f"<p style='font-size:12px;color:#64748b;margin:28px 0 4px;'>"
             f"{'；'.join(intro_bits)}。以下為快速情報，不納入股價模型。</p>")
    return intro + body


# ===== 天氣(信件開頭問候卡;Open-Meteo 免金鑰) =====
WEATHER_LOCATIONS = [
    ("彰化市", 24.0809, 120.5383),
    ("台中北區", 24.1668, 120.6840),
]
_WMO_CODE_LABEL = (
    ((0,), "晴朗"), ((1, 2), "多雲時晴"), ((3,), "陰天"), ((45, 48), "有霧"),
    ((51, 53, 55, 56, 57), "毛毛雨"), ((61, 63, 65, 66, 67), "有雨"),
    ((71, 73, 75, 77), "降雪"), ((80, 81, 82), "陣雨"),
    ((85, 86), "陣雪"), ((95, 96, 99), "雷雨"),
)


def fetch_weather() -> list[dict]:
    """Open-Meteo 抓兩地當日氣溫/降雨機率/天氣;失敗回空(不影響晨報)。"""
    out = []
    for name, lat, lon in WEATHER_LOCATIONS:
        try:
            r = _http_get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon,
                "daily": ("temperature_2m_max,temperature_2m_min,"
                          "precipitation_probability_max,weather_code,"
                          "precipitation_sum,wind_gusts_10m_max,wind_speed_10m_max"),
                "timezone": "Asia/Taipei", "forecast_days": 1}, timeout=15)
            r.raise_for_status()
            d = r.json().get("daily", {})
            code = int((d.get("weather_code") or [0])[0])
            label = next((lbl for codes, lbl in _WMO_CODE_LABEL if code in codes), "—")
            out.append({
                "name": name,
                "t_min": round(float((d.get("temperature_2m_min") or [0])[0])),
                "t_max": round(float((d.get("temperature_2m_max") or [0])[0])),
                "rain_prob": int((d.get("precipitation_probability_max") or [0])[0]),
                "label": label,
                # 颱風風雨評估用(km/h、mm;使用者要求 2026-07-15)
                "rain_sum": round(float((d.get("precipitation_sum") or [0])[0] or 0), 1),
                "gust": round(float((d.get("wind_gusts_10m_max") or [0])[0] or 0)),
                "wind": round(float((d.get("wind_speed_10m_max") or [0])[0] or 0)),
            })
        except Exception as e:
            print(f"[weather] {name} 抓取失敗: {e}", file=sys.stderr)
    return out


def _weather_advice(locs: list[dict]) -> str:
    """規則式穿著/帶傘建議(取兩地較極端值)。"""
    if not locs:
        return ""
    t_max = max(loc["t_max"] for loc in locs)
    t_min = min(loc["t_min"] for loc in locs)
    rain = max(loc["rain_prob"] for loc in locs)
    if t_max >= 32:
        wear = "短袖即可,注意防曬與補水"
    elif t_max >= 28:
        wear = "短袖為主,室內冷氣房可備薄外套"
    elif t_max >= 22:
        wear = "短袖加薄外套,早晚溫差留意"
    else:
        wear = "長袖加外套,注意保暖"
    if t_min <= 16:
        wear += "(清晨偏冷)"
    if rain >= 60:
        umbrella = "降雨機率高,出門記得帶傘"
    elif rain >= 30:
        umbrella = "可能有局部陣雨,建議備折疊傘"
    else:
        umbrella = "下雨機率低,不太需要帶傘"
    return f"{wear};{umbrella}。"


def _typhoon_signal(locs: list[dict]) -> str:
    """門檻式颱風風雨警示:預測值對照「停班停課參考標準」(平均風力 7 級≈50km/h、
    陣風 10 級≈89km/h、24h 雨量 350mm),達標/接近(80%)才顯示白話一行;平日回空。
    僅供參考——實際停班停課以各縣市政府晚間公告為準(見停班停課新聞列)。"""
    hits = []
    for loc in locs or []:
        wind, gust, rain = loc.get("wind") or 0, loc.get("gust") or 0, loc.get("rain_sum") or 0
        if wind >= 50 or gust >= 89 or rain >= 350:
            hits.append(f"{loc['name']} 預測風雨已達停班停課參考標準"
                        f"(陣風 {gust}km/h、雨量 {rain}mm)")
        elif wind >= 40 or gust >= 71 or rain >= 280:
            hits.append(f"{loc['name']} 風雨接近停班停課參考標準"
                        f"(陣風 {gust}km/h、雨量 {rain}mm)")
    if not hits:
        return ""
    # 免責固定附註(Codex review:達標紅字不可讓讀者誤為停班定論)
    return ";".join(hits) + "——預測僅供參考,是否停班停課以縣市政府公告為準"


def fetch_suspension_news(hours: int = 30) -> list[dict]:
    del hours   # 視窗改為固定「台北昨日 16:00 起」,參數保留介面相容
    """停班停課公告新聞(中彰投雲):人事總處頁面憑證缺 SKI 無法程式抓,改新聞源——
    縣市公告一出新聞秒發,晨報 06:00 一定抓得到前晚公告。過濾:標題須含在地縣市名
    且含停班/停課字樣(排除社論/評論雜訊)。失敗回空。"""
    regions = ("彰化", "台中", "臺中", "南投", "雲林")
    try:
        feed = _feedparser_parse_url_with_timeout(
            _gnews_rss("停班停課 OR 颱風假 OR 停止上班", when="2d"))
        # 只收「台北昨日 16:00 之後」發布的公告新聞:今日停班的公告都在前晚 18-23 時
        # 或今晨發布;更早的「今日照常/停班」其『今日』指昨天,跨日顯示會誤導(Codex review)
        _now_tpe = dt.datetime.now(TPE)
        cutoff = (_now_tpe - dt.timedelta(days=1)).replace(
            hour=16, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc)
        items = []
        for entry in feed.entries:
            if len(items) >= 4:
                break
            title = str(entry.get("title", ""))
            if not any(r in title for r in regions):
                continue
            if not any(k in title for k in ("停班", "停課", "停止上班", "照常上班")):
                continue
            pub = entry.get("published_parsed") or entry.get("updated_parsed")
            if pub and dt.datetime(*pub[:6], tzinfo=dt.timezone.utc) < cutoff:
                continue
            items.append({"title": title[:90], "link": str(entry.get("link", ""))})
        return items
    except Exception as e:
        print(f"[weather] 停班停課新聞抓取失敗: {e}", file=sys.stderr)
        return []


def _render_weather_html(locs: list[dict],
                         suspension: Optional[list] = None) -> str:
    # 天氣抓取失敗但有停班停課公告 → 公告仍須顯示(重要資訊不可因天氣源掛掉而消失,
    # Codex review);兩者皆空才回空。
    if not locs and not suspension:
        return ""
    import html as _h
    parts = "　|　".join(
        f"<b>{loc['name']}</b> {loc['t_min']}~{loc['t_max']}°C {loc['label']}・降雨 {loc['rain_prob']}%"
        for loc in locs) if locs else "(天氣資料暫缺)"
    # 颱風風雨門檻警示(達標/接近才出現;紅字)
    signal = _typhoon_signal(locs)
    signal_html = (f"<br><b style='color:#b91c1c;'>⚠ {_h.escape(signal)}</b>"
                   if signal else "")
    # 停班停課公告新聞(縣市公告即時,黑字可點;無公告日自動消失)
    susp_html = "".join(
        f"<br><a href='{_h.escape(str(i.get('link', '')))}' "
        f"style='color:#0f172a;text-decoration:none;font-weight:700;'>"
        f"🏫 {_h.escape(str(i.get('title', '')))}</a>"
        for i in (suspension or []))
    return (
        f"<div style='background:#f0f9ff;border:1px solid #bae6fd;border-radius:10px;"
        f"padding:12px 16px;margin:0 0 14px;font-size:13px;color:#0c4a6e;line-height:1.8;'>"
        f"<b>早安!</b>　{parts}<br>"
        f"<span style='color:#0369a1;'>{_weather_advice(locs)}</span>"
        f"{signal_html}{susp_html}</div>")


def _render_weekly_recap_html(history: list[dict]) -> str:
    """週末綜合報專屬:本週預測準確度回顧(加權/2330 預測 vs 實際開盤)。"""
    rows = []
    for rec in (history or [])[-5:]:
        tgt = rec.get("target_session_date", "")
        pt, at = rec.get("pred_taiex"), rec.get("actual_open_taiex")
        p2, a2 = rec.get("weighted_final_2330"), rec.get("actual_open_2330")
        if not (pt and at) and not (p2 and a2):
            continue
        e_t = f"{(at / pt - 1) * 100:+.2f}%" if (pt and at) else "—"
        e_2 = f"{(a2 / p2 - 1) * 100:+.2f}%" if (p2 and a2) else "—"
        rows.append(
            f"<tr><td style='padding:6px 12px;border-bottom:1px solid #e2e8f0;"
            f"font-size:13px;color:#0f172a;'>{tgt[5:]}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e2e8f0;text-align:right;"
            f"font-size:13px;'>{e_t}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #e2e8f0;text-align:right;"
            f"font-size:13px;'>{e_2}</td></tr>")
    if not rows:
        return ""
    return (
        '<div style="border:1px solid #c4b5fd;border-radius:10px;overflow:hidden;margin:14px 0;">'
        '<div style="background:#f5f3ff;color:#5b21b6;padding:8px 14px;font-weight:700;'
        'font-size:14px;">本週預測回顧(實際開盤 vs 預測的偏差)</div>'
        '<table style="width:100%;border-collapse:collapse;background:#fff;">'
        '<tr style="background:#faf5ff;"><th style="padding:6px 12px;text-align:left;'
        'font-size:12px;color:#6d28d9;">日期</th><th style="padding:6px 12px;text-align:right;'
        'font-size:12px;color:#6d28d9;">加權誤差</th><th style="padding:6px 12px;'
        'text-align:right;font-size:12px;color:#6d28d9;">2330 誤差</th></tr>'
        + "".join(rows) + "</table></div>")


# ===== 風險事件日曆(未來 7 天:經濟數據/FOMC/結算日/三巫/財報) =====
def _third_weekday_of_month(year: int, month: int, weekday: int) -> dt.date:
    """該月第三個星期 weekday(0=一,2=三,4=五)。"""
    d = dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + dt.timedelta(days=offset + 14)


# 2026 年 FOMC 會議日程(Fed 官網公布;FF 免費日曆只有本週,
# 跨週的 FOMC 會漏 → 硬編,每年初更新一次)
FOMC_2026 = (
    dt.date(2026, 1, 28), dt.date(2026, 3, 18), dt.date(2026, 4, 29),
    dt.date(2026, 6, 17), dt.date(2026, 7, 29), dt.date(2026, 9, 16),
    dt.date(2026, 10, 28), dt.date(2026, 12, 9),
)


def _rule_based_events(today: dt.date, horizon_days: int = 7) -> list[dict]:
    """規則式市場結構日:FOMC、台指期結算、美股三巫、MSCI 季調生效(近似)。"""
    events = []
    end = today + dt.timedelta(days=horizon_days)
    for fomc in FOMC_2026:
        if today <= fomc <= end:
            events.append({"date": fomc, "time": "02:00(隔日凌晨)",
                           "title": "FOMC 利率決策(台北時間隔日凌晨 2:00 公布)",
                           "note": "決策日前後美股波動放大", "impact": "high"})
    for probe in (today, (today.replace(day=1) + dt.timedelta(days=32)).replace(day=1)):
        y, m = probe.year, probe.month
        settle = _third_weekday_of_month(y, m, 2)
        if today <= settle <= end:
            events.append({"date": settle, "time": "13:30", "title": "台指期/選擇權結算日",
                           "note": "結算日波動天生較大,慎防尾盤急拉急殺", "impact": "high"})
        if m in (3, 6, 9, 12):
            witch = _third_weekday_of_month(y, m, 4)
            if today <= witch <= end:
                events.append({"date": witch, "time": "美股收盤", "title": "美股三巫日(期貨/期權同步到期)",
                               "note": "美股尾盤量能與波動放大", "impact": "high"})
        if m in (2, 5, 8, 11):
            last = (dt.date(y, m, 28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
            while last.weekday() >= 5:
                last -= dt.timedelta(days=1)
            if today <= last <= end:
                events.append({"date": last, "time": "台股收盤", "title": "MSCI 季調生效日(近似)",
                               "note": "尾盤外資被動買賣壓放大", "impact": "medium"})
    return events


def _event_category(title: str) -> str:
    """把事件標題收斂成粗分類 key,讓同一事件的不同來源/寫法只留一筆。
    例:規則式『FOMC 利率決策…』與 ForexFactory『[USD] FOMC Statement / Federal Funds Rate』
    都歸 FOMC;其餘事件以去國別前綴+去標點的標題當 key(CPI 與 Core CPI 仍視為不同)。"""
    t = str(title).lower()
    # 僅認 Fed 專屬字樣;不可用泛用的 "interest rate decision"(會把 ECB/BOE 等也歸 FOMC)。
    if ("fomc" in t or "federal funds" in t or "federal open market" in t
            or "聯準會" in t or "fed 利率決策" in t):
        return "FOMC"
    if "三巫" in t or "quadruple witching" in t or "triple witching" in t:
        return "WITCHING"
    if "結算" in t and ("台指" in t or "選擇權" in t):
        return "TW_SETTLE"
    # 其餘事件以「去標點的標題」當 key:保留 [usd]/[eur] 國別前綴(否則 [USD] CPI 與
    # [EUR] CPI 會塌成同一筆),CPI 與 Core CPI 也因字串不同而視為不同事件。
    import re as _re
    base = _re.sub(r"[\s（）()，,。.、:：;；/-]+", "", t)
    return base or t


_STRUCTURAL_EVENT_CATS = {"FOMC", "WITCHING", "TW_SETTLE"}


def _dedupe_calendar_events(events: list[dict]) -> list[dict]:
    """事件日曆去重(保序,就地填補 note)。
    一般事件:同日同類別只留一筆(FF 本週重疊、不同寫法收斂)。
    結構性事件(FOMC/三巫/結算)跨來源日期可能差一天(規則式用美國會議日 6/17、
    FF 用台北公布日 6/18)→ 與同類別且日期相差 ≤1 天的已留事件視為同一筆;但相隔
    較遠者(較長 horizon 下不同月份的結算日)仍各自保留,不可一律塌成一筆。
    保留者無 note 而被丟的同類事件有(如 FF 的預期/前值)→ 補上,不漏資訊。"""
    def _merge_note(kept: dict, dup: dict) -> None:
        # 併入被丟者的 note(如 FF 的預期/前值);保留者已有 note 時附加「不同」內容,
        # 不重複附加(FF 本週重疊的同名事件 note 相同 → 不會疊字)。
        dn = (dup.get("note") or "").strip()
        if not dn:
            return
        kn = (kept.get("note") or "").strip()
        if not kn:
            kept["note"] = dn
        elif dn not in kn:
            kept["note"] = f"{kn}；{dn}"

    deduped: list[dict] = []
    seen_generic: set = set()
    for e in events:
        cat = _event_category(e["title"])
        if cat in _STRUCTURAL_EVENT_CATS:
            near = next((k for k in deduped
                         if _event_category(k["title"]) == cat
                         and abs((e["date"] - k["date"]).days) <= 1), None)
            if near is not None:
                _merge_note(near, e)
                continue
            deduped.append(e)
            continue
        key = (e["date"], cat)
        if key in seen_generic:
            _merge_note(next(k for k in deduped
                             if (k["date"], _event_category(k["title"])) == key), e)
            continue
        seen_generic.add(key)
        deduped.append(e)
    return deduped


def fetch_event_calendar(now_tpe: Optional[dt.datetime] = None,
                         horizon_days: int = 7) -> list[dict]:
    """未來 7 天風險事件:ForexFactory 高衝擊經濟數據(含預期值)+ 規則式市場結構日
    + 重點美股財報(yfinance)。全部轉 TPE 時間;失敗回部分結果。"""
    now_tpe = now_tpe or dt.datetime.now(TPE)
    today = now_tpe.date()
    end = today + dt.timedelta(days=horizon_days)
    events: list[dict] = list(_rule_based_events(today, horizon_days))

    # ForexFactory 本週日曆(免金鑰;nextweek 端點實測 404 不存在,
    # 跨週缺口由 FOMC_2026 硬編日程補)
    for url in ("https://nfs.faireconomy.media/ff_calendar_thisweek.json",):
        try:
            r = _http_get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            for e in r.json():
                if e.get("impact") != "High" or e.get("country") not in ("USD", "CNY", "EUR"):
                    continue
                try:
                    when = dt.datetime.fromisoformat(str(e.get("date", ""))).astimezone(TPE)
                except Exception:
                    continue
                if not (today <= when.date() <= end):
                    continue
                extra = []
                if e.get("forecast"):
                    extra.append(f"預期 {e['forecast']}")
                if e.get("previous"):
                    extra.append(f"前值 {e['previous']}")
                events.append({"date": when.date(), "time": when.strftime("%H:%M"),
                               "title": f"[{e.get('country')}] {str(e.get('title', ''))[:40]}",
                               "note": "、".join(extra), "impact": "high"})
        except Exception as ex:
            print(f"[calendar] FF 日曆抓取失敗: {ex}", file=sys.stderr)
    events = _dedupe_calendar_events(events)

    # 重點美股財報(yfinance earnings;逐檔輕量,失敗逐檔略過)
    for tk in ("NVDA", "AAPL", "MSFT", "AVGO", "TSLA", "AMD", "GOOGL", "META", "MU", "QCOM"):
        try:
            cal = yf.Ticker(tk).calendar
            dates = (cal or {}).get("Earnings Date") or []
            for d in dates[:1]:
                ed = d if isinstance(d, dt.date) else getattr(d, "date", lambda: None)()
                if ed and today <= ed <= end:
                    events.append({"date": ed, "time": "盤後(美東)", "title": f"{tk} 財報",
                                   "note": "", "impact": "high"})
        except Exception:
            continue
    events.sort(key=lambda e: (e["date"], e.get("time", "")))
    return events


# ===== 台股行事曆:公開申購 + 除權息預告 =====
def fetch_tw_calendar(now_tpe: Optional[dt.datetime] = None,
                      dividend_watchlist: tuple = ("0050", "00662", "2330", "0056", "00878")) -> dict:
    """TWSE 公開申購(進行中/即將開始)+ 關注標的除權息預告(未來 14 天)。"""
    now_tpe = now_tpe or dt.datetime.now(TPE)
    today = now_tpe.date()
    out: dict = {"ipo": [], "dividends": []}
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    def _roc_to_date(s: str) -> Optional[dt.date]:
        import re as _re
        m = _re.search(r"(\d{2,3})[/年](\d{1,2})[/月](\d{1,2})", str(s or ""))
        if not m:
            return None
        try:
            return dt.date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    def _market_price(code: str) -> Optional[float]:
        """申購標的現價 best-effort:上市(STOCK_DAY_ALL)→ 上櫃(TPEX openapi)。"""
        try:
            for row in _fetch_twse_stock_day_all():
                keys = list(row.keys())
                code_k = next((k for k in keys if k == "Code" or "代號" in k), None)
                close_k = next((k for k in keys if "clos" in k.lower() or "收盤" in k), None)
                if code_k and str(row.get(code_k, "")).strip() == code:
                    return _to_float(row.get(close_k))
        except Exception:
            pass
        try:
            r = _http_get(
                "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
                timeout=12, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            for row in r.json() or []:
                if str(row.get("SecuritiesCompanyCode", "")).strip() == code:
                    return _to_float(row.get("Close") or row.get("ClosingPrice"))
        except Exception:
            pass
        return None

    try:
        r = _http_get("https://www.twse.com.tw/announcement/publicForm",
                         params={"response": "json"}, timeout=15, headers=headers)
        d = r.json()
        fields = d.get("fields") or []
        idx = {name: i for i, name in enumerate(fields)}
        for row in d.get("data") or []:
            try:
                end_d = _roc_to_date(row[idx.get("申購結束日", 6)])
                start_d = _roc_to_date(row[idx.get("申購開始日", 5)])
                if not end_d or end_d < today or (start_d and start_d > today + dt.timedelta(days=7)):
                    continue
                code = str(row[idx.get("證券代號", 3)]).strip()
                name = str(row[idx.get("證券名稱", 2)]).strip()
                # 只留「股票」抽籤(上櫃轉上市等):公債/央債/公司債(代號含字母,
                # 如 A151GA;或名稱含「債」)對讀者無抽籤價值,一律排除(使用者要求 2026-07-14)
                if not code.isdigit() or "債" in name:
                    continue
                price = _to_float(str(row[idx.get("承銷價(元)", 9)]).replace(",", ""))
                units = _to_float(str(row[idx.get("申購股數", 13)]).replace(",", "")) or 1000
                market = _market_price(code)
                spread = round(market - price, 2) if (market and price) else None
                profit = (round((market - price) * units)
                          if (market and price and units) else None)
                out["ipo"].append({
                    "name": name,
                    "code": code,
                    "start": start_d, "end": end_d,
                    "draw": _roc_to_date(row[idx.get("抽籤日期", 1)]),
                    "price": price, "market": market,
                    "spread": spread, "profit": profit,
                    "lottery_pct": str(row[idx.get("中籤率(%)", 16)]).strip(),
                })
            except Exception:
                continue
    except Exception as e:
        print(f"[tw_calendar] 申購抓取失敗: {e}", file=sys.stderr)

    try:
        r = _http_get("https://www.twse.com.tw/exchangeReport/TWT48U",
                         params={"response": "json"}, timeout=15, headers=headers)
        d = r.json()
        fields = d.get("fields") or []
        idx = {name: i for i, name in enumerate(fields)}
        watch = set(dividend_watchlist)
        for row in d.get("data") or []:
            try:
                code = str(row[idx.get("股票代號", 1)]).strip()
                if code not in watch:
                    continue
                ex_d = _roc_to_date(row[idx.get("除權除息日期", 0)])
                if not ex_d or not (today <= ex_d <= today + dt.timedelta(days=14)):
                    continue
                amount = ""
                for cand in ("現金股利", "權值+息值", "息值"):
                    if cand in idx:
                        amount = str(row[idx[cand]])
                        break
                out["dividends"].append({
                    "code": code, "name": str(row[idx.get("名稱", 2)]),
                    "ex_date": ex_d, "kind": str(row[idx.get("除權息", 3)]),
                    "amount": amount,
                })
            except Exception:
                continue
    except Exception as e:
        print(f"[tw_calendar] 除權息預告抓取失敗: {e}", file=sys.stderr)

    # ETF 配息補值:TWSE TWT48U 對 ETF 常整段是「待公告實際收益分配金額」文字
    # (投信例於除息前數日才公告)。改以 FinMind TaiwanStockDividend 補實際金額——
    # 公告一出下一封信就顯示數字,不會停在「待公告」;並帶發放日。逐檔失敗略過。
    _fm_token = os.getenv("FINMIND_TOKEN", "").strip()
    for v in out["dividends"]:
        s = str(v.get("amount") or "").replace(",", "").strip()
        try:
            if s and math.isfinite(float(s)):
                continue                      # TWSE 已有數字 → 不用補
        except ValueError:
            pass
        try:
            params = {"dataset": "TaiwanStockDividend", "data_id": v["code"],
                      "start_date": (today - dt.timedelta(days=120)).isoformat()}
            if _fm_token:
                params["token"] = _fm_token
            r = _http_get("https://api.finmindtrade.com/api/v4/data", params=params,
                          timeout=12, headers={"User-Agent": "Mozilla/5.0"})
            ex_iso = v["ex_date"].isoformat()
            for rec in (r.json() or {}).get("data") or []:
                if str(rec.get("CashExDividendTradingDate")) != ex_iso:
                    continue
                cash = rec.get("CashEarningsDistribution") or 0
                if isinstance(cash, (int, float)) and cash > 0:
                    v["amount"] = f"{cash:g}"
                pay = str(rec.get("CashDividendPaymentDate") or "")
                if len(pay) == 10:
                    v["pay_date"] = pay
                break
        except Exception as e:
            print(f"[tw_calendar] {v['code']} FinMind 配息補值略過: {e}", file=sys.stderr)
    return out


def _render_tw_calendar_html(cal: dict) -> str:
    ipo = (cal or {}).get("ipo") or []
    divs = (cal or {}).get("dividends") or []
    if not ipo and not divs:
        return ""
    blocks = []
    if ipo:
        def _ipo_line(i: dict) -> str:
            parts = [f"<b>{i['name']}（{i['code']}）</b>　申購至 {i['end'].strftime('%m/%d')}"]
            if i.get("draw"):
                parts.append(f"抽籤 {i['draw'].strftime('%m/%d')}")
            if i.get("price"):
                parts.append(f"承銷價 {i['price']:g} 元")
            if i.get("market"):
                parts.append(f"市價 {i['market']:g} 元")
            if i.get("spread") is not None:
                color = "#b91c1c" if i["spread"] > 0 else "#15803d"
                parts.append(f"<b style='color:{color};'>價差 {i['spread']:+g} 元</b>")
            if i.get("profit") is not None:
                parts.append(f"中籤潛在獲利約 <b>{i['profit']:+,} 元</b>")
            return "・".join(parts)
        rows = "".join(f"<li style='margin:4px 0;'>{_ipo_line(i)}</li>" for i in ipo[:6])
        blocks.append(f"<div style='margin:6px 0;'><b style='color:#0f172a;'>公開申購(抽籤)</b>"
                      f"<ul style='margin:4px 0;padding-left:20px;font-size:13px;color:#334155;"
                      f"line-height:1.7;'>{rows}</ul></div>")
    if divs:
        def _div_amt(a) -> str:
            # 只有「有限數字」才顯示金額。TWSE 對未公告 ETF 回文字「待公告實際收益分配金額」→ 顯示
            # 「配息待公告」;空/NaN 儲存格 str() 後會變 "nan"/"inf"(float() 不拋例外)→ 一律留空,
            # 絕不印「每股 nan 元」(Codex review)。
            s = str(a if a is not None else "").strip()
            try:
                v = float(s.replace(",", ""))
                if math.isfinite(v):
                    return f"・每股 {v:g} 元"
            except ValueError:
                pass
            return "・配息待公告" if s and s.lower() not in ("nan", "inf", "-inf", "none") else ""
        def _pay(v) -> str:
            p = str(v.get("pay_date") or "")
            return f"・發放 {p[5:].replace('-', '/')}" if len(p) == 10 else ""
        rows = "".join(
            f"<li style='margin:4px 0;'><b>{v['name']}（{v['code']}）</b>"
            f"　{v['ex_date'].strftime('%m/%d')} 除{v['kind']}"
            + _div_amt(v.get("amount")) + _pay(v)
            + "</li>"
            for v in divs[:6])
        blocks.append(f"<div style='margin:6px 0;'><b style='color:#0f172a;'>關注標的除權息</b>"
                      f"<ul style='margin:4px 0;padding-left:20px;font-size:13px;color:#334155;"
                      f"line-height:1.7;'>{rows}</ul></div>")
    return (
        '<div style="border:1px solid #fcd34d;border-radius:10px;padding:8px 14px;'
        'margin:14px 0;background:#fffbeb;">'
        '<div style="font-weight:700;font-size:14px;color:#92400e;margin-bottom:4px;">台股行事曆</div>'
        + "".join(blocks) + "</div>")


# ===== 醫學文獻速報(PubMed E-utilities + DeepSeek 中文摘要) =====
MEDICAL_JOURNALS = [
    ("JAAD", "J Am Acad Dermatol"),
    ("JEADV", "J Eur Acad Dermatol Venereol"),
    ("BJD", "Br J Dermatol"),
    ("JAMA Derm", "JAMA Dermatol"),
    ("NEJM", "N Engl J Med"),
    ("AJO", "Am J Ophthalmol"),
]


def fetch_medical_journal_articles(per_journal: int = 3) -> list[dict]:
    """PubMed 近 7 天各期刊新文(過濾 Comment/Reply/Erratum);失敗回部分結果。"""
    out = []
    for short, journal in MEDICAL_JOURNALS:
        try:
            r = _http_get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                             params={"db": "pubmed", "term": f'"{journal}"[ta]',
                                     "reldate": "7", "datetype": "edat",
                                     "retmode": "json", "retmax": "12"}, timeout=20)
            ids = r.json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                continue
            r2 = _http_get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                              params={"db": "pubmed", "id": ",".join(ids),
                                      "retmode": "json"}, timeout=20)
            res = r2.json().get("result", {})
            kept = 0
            for pid in ids:
                if kept >= per_journal:
                    break
                item = res.get(pid) or {}
                title = str(item.get("title") or "").strip().rstrip(".")
                low = title.lower()
                if not title or low.startswith(("comment", "reply", "erratum",
                                                "correction", "response to")):
                    continue
                out.append({"journal": short, "pmid": pid, "title": title})
                kept += 1
            time.sleep(0.5)   # NCBI 禮貌限速
        except Exception as e:
            print(f"[journals] {short} 抓取失敗: {e}", file=sys.stderr)
    return out


def translate_journal_titles(articles: list[dict]) -> list[dict]:
    """DeepSeek 把英文標題譯成繁中一句重點;失敗保留英文(degrade)。

    自帶 chat/completions 呼叫(response_format=json_object):
    extractor 路徑曾在 GitHub Actions 上回空 content,直連 + JSON 模式最穩。
    """
    if not articles or not DEEPSEEK_API_KEY:
        return articles
    try:
        payload = [{"i": i, "title": a["title"]} for i, a in enumerate(articles)]
        r = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={
                "model": os.getenv("DEEPSEEK_EXTRACTOR_MODEL", "deepseek-v4-flash"),
                "messages": [
                    {"role": "system", "content":
                        "你是醫學文獻編譯。把每篇論文標題翻成一句台灣繁體中文重點"
                        "(口語、保留關鍵術語原文縮寫,嚴禁簡體字)。"
                        '輸出 JSON:{"items": [{"i": 索引, "zh": "中文一句"}]}'},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            },
            timeout=120)
        r.raise_for_status()
        items = json.loads(r.json()["choices"][0]["message"]["content"]).get("items", [])
        zh_map = {int(it.get("i", -1)): str(it.get("zh", "")) for it in items}
        for i, a in enumerate(articles):
            if zh_map.get(i):
                a["zh"] = zh_map[i]
    except Exception as e:
        print(f"[journals] 中文摘要失敗(顯示英文): {e}", file=sys.stderr)
    return articles


def _render_journals_html(articles: list[dict], htmllib) -> str:
    if not articles:
        return ""
    by_journal: dict[str, list] = {}
    for a in articles:
        by_journal.setdefault(a["journal"], []).append(a)
    blocks = []
    for short, _ in MEDICAL_JOURNALS:
        arts = by_journal.get(short)
        if not arts:
            continue
        items = "".join(
            f"<li style='margin:5px 0;'>"
            f"<a href='https://pubmed.ncbi.nlm.nih.gov/{a['pmid']}/' "
            f"style='color:#0f172a;text-decoration:none;'>"
            f"{htmllib.escape(a.get('zh') or a['title'])}</a>"
            + (f"<div style='font-size:12px;color:#94a3b8;'>{htmllib.escape(a['title'][:90])}</div>"
               if a.get("zh") else "")
            + "</li>"
            for a in arts)
        blocks.append(f"<div style='margin:8px 0;'><b style='color:#7c2d12;'>{short}</b>"
                      f"<ul style='margin:4px 0;padding-left:20px;font-size:13px;"
                      f"color:#334155;line-height:1.6;'>{items}</ul></div>")
    return (
        '<h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;'
        'background:#fff7ed;border-left:5px solid #ea580c;border-radius:4px;">'
        '醫學文獻速報（近 7 天・JAAD / JEADV / NEJM / AJO）</h2>'
        '<div style="border:1px solid #e2e8f0;border-radius:10px;padding:6px 16px;background:#ffffff;">'
        + "".join(blocks) + '</div>')


# ===== 重大事件連續劇追蹤(延燒事件 timeline) =====
EVENT_TIMELINE_FILE = Path("state/event_timeline.json")
_TIMELINE_EVENT_TYPES = {"geopolitical", "export_controls", "litigation"}


def update_event_timeline(structured_events: list[dict],
                          now_tpe: Optional[dt.datetime] = None) -> list[dict]:
    """以 structured events 維護延燒事件 timeline:同 entity+type 連續出現則累計天數;
    3 天沒新進展自動退場。回傳進行中的事件(供渲染「第 N 天」)。"""
    now_tpe = now_tpe or dt.datetime.now(TPE)
    today = now_tpe.strftime("%Y-%m-%d")
    state: dict = {}
    if EVENT_TIMELINE_FILE.exists():
        try:
            state = json.loads(EVENT_TIMELINE_FILE.read_text(encoding="utf-8")) or {}
        except Exception:
            state = {}
    for ev in structured_events or []:
        if str(ev.get("event_type")) not in _TIMELINE_EVENT_TYPES:
            continue
        key = f"{ev.get('event_type')}:{str(ev.get('entity') or '')[:20]}"
        rec = state.get(key) or {"first_seen": today, "days": 0, "last_seen": ""}
        if rec.get("last_seen") != today:
            rec["days"] = int(rec.get("days", 0)) + 1
            rec["last_seen"] = today
        rec["latest_title"] = str(ev.get("title") or "")[:90]
        state[key] = rec
    # 退場:超過 3 天無更新
    cutoff = (now_tpe - dt.timedelta(days=3)).strftime("%Y-%m-%d")
    state = {k: v for k, v in state.items() if v.get("last_seen", "") >= cutoff}
    try:
        EVENT_TIMELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        EVENT_TIMELINE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[timeline] 寫入失敗: {e}", file=sys.stderr)
    active = [{"key": k, **v} for k, v in state.items()
              if v.get("last_seen") == today and v.get("days", 0) >= 2
              # 需綁定具體標的(公司/類股/商品);無 entity 的泛新聞(中國天氣、貿易談判)關聯性低 → 不列
              and str(k).split(":", 1)[-1].strip()]
    active.sort(key=lambda r: -r.get("days", 0))
    return active


_S2TW_CC = None
_S2TW_CC_TRIED = False


def _to_traditional(text: str) -> str:
    """簡體→台灣繁體(opencc s2twp);外部中文標題(如延燒事件抓自陸媒)正名用。
    opencc 不可用或轉換失敗時原樣返回,絕不讓報告中斷;已是繁體者近乎不變(idempotent)。"""
    global _S2TW_CC, _S2TW_CC_TRIED
    s = str(text or "")
    if not s:
        return s
    if not _S2TW_CC_TRIED:
        _S2TW_CC_TRIED = True
        try:
            from opencc import OpenCC
            _S2TW_CC = OpenCC("s2twp")
        except Exception as e:
            print(f"[opencc] 不可用,中文標題不轉繁: {e}", file=sys.stderr)
    if _S2TW_CC is None:
        return s
    try:
        return _S2TW_CC.convert(s)
    except Exception:
        return s


def translate_event_titles(active: list[dict]) -> list[dict]:
    """把「延燒中事件」的英文標題譯成一句繁中重點(zh_title);失敗保留原文(degrade)。
    沿用 journals 的直連 chat/completions + JSON 模式(在 Actions 上最穩)。
    註:不論翻譯成功與否,顯示時都會再經 _to_traditional 簡轉繁(陸媒原標題多為簡體)。"""
    targets = [r for r in (active or []) if str(r.get("latest_title") or "").strip()]
    if not targets or not DEEPSEEK_API_KEY:
        return active
    try:
        payload = [{"i": i, "title": r["latest_title"]} for i, r in enumerate(targets)]
        resp = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={
                "model": os.getenv("DEEPSEEK_EXTRACTOR_MODEL", "deepseek-v4-flash"),
                "messages": [
                    {"role": "system", "content":
                        "你是財經新聞編譯。把每則延燒事件的標題翻成一句台灣繁體中文重點"
                        "(精簡、保留關鍵公司名/專有名詞,嚴禁簡體字)。"
                        '輸出 JSON:{"items": [{"i": 索引, "zh": "中文一句"}]}'},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            },
            timeout=60)
        resp.raise_for_status()
        items = json.loads(resp.json()["choices"][0]["message"]["content"]).get("items", [])
        zh_map = {int(it.get("i", -1)): str(it.get("zh", "")) for it in items}
        for i, rec in enumerate(targets):
            if zh_map.get(i):
                rec["zh_title"] = zh_map[i]
    except Exception as e:
        print(f"[timeline] 事件中文翻譯失敗(顯示原文): {e}", file=sys.stderr)
    return active


def _render_event_timeline_html(active: list[dict], htmllib) -> str:
    if not active:
        return ""
    rows = "".join(
        f"<div style='margin:4px 0;font-size:13px;color:#334155;'>"
        f"・<b>{htmllib.escape(_to_traditional(str(r['key']).split(':', 1)[-1] or '事件'))}</b>"
        f"<span style='color:#b91c1c;font-weight:700;'>(第 {r['days']} 天)</span>　"
        f"{htmllib.escape(_to_traditional(r.get('zh_title') or r.get('latest_title', '')))}</div>"
        for r in active[:4])
    return (
        '<div style="border:1px solid #c7d2fe;border-radius:10px;padding:8px 14px;'
        'margin:14px 0;background:#eef2ff;">'
        '<div style="font-weight:700;font-size:14px;color:#3730a3;margin-bottom:2px;">'
        '延燒中事件</div>' + rows + "</div>")


# ===== 體育快訊(醫界區下方;ESPN 公開 API 比分 + Google News 消息) =====
# 在地快訊(中彰投雲;使用者 2026-07-15:快速掌握在地建設/房市/產業/學區,含斗六)。
# 主題式查詢(縣市政府泛查詢實測 77 則但防空演習/二手書站雜訊多 → 捨棄);
# 各查詢皆經 live 實測有召回且切題。純生活情報卡,不進計分、不餵 LLM。
LOCAL_NEWS_QUERIES: list[tuple] = [
    # 彰基/中國醫(使用者夫妻任職)整合於此(2026-07-15 拍板,自醫界卡遷入;
    # 兩院的裁罰/感染等硬新聞仍會依一般規則上醫界卡,此處涵蓋建設/決策/一般消息)
    ("彰基/中國醫", "彰化基督教醫院 OR 彰基 OR 中國醫藥大學附設醫院 OR 中醫大附醫"),
    ("斗六/雲林", "斗六 建設 OR 斗六 房市 OR 斗六市 OR 雲林 重大建設"),
    ("建設", "中友百貨 OR 台中捷運 OR 彰化市 建設 OR 草屯 建設"),
    ("房市", "台中 房市 OR 彰化 房市 OR 南投 房市 OR 草屯 OR 台中 建案"),
    ("產業/科技", "中科 OR 彰濱工業區 OR 雲林科技工業區 OR 二林 園區"),
    ("學區/文教", "台中 學區 OR 彰化 學區 OR 斗六 學區 OR 雲林 學區"),
    # 交通異動(泛「國道 彰化/台中」52 則含全台事故雜訊 → 用精準版)
    ("交通異動", "台74 OR 國道1號 中部 OR 台中 道路 施工"),
]


def fetch_local_news(now_tpe: Optional[dt.datetime] = None,
                     per_label: int = 2, hours: int = 30) -> dict:
    """在地快訊:各主題抓近 hours 小時內最新 per_label 則(標題+連結)。
    逐主題失敗略過(晨報不可斷);回 {label: [{"title","link"}...]}。"""
    del now_tpe   # 介面對齊其他 fetch;cutoff 用 UTC now
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    out: dict = {}
    seen_titles: set = set()   # 跨主題去重:同一新聞常同時命中房市+建設
    for label, query in LOCAL_NEWS_QUERIES:
        try:
            # when=2d:Google 伺服器端 when:1d 只回 24h 內,會吃掉 24-30h 的新聞;
            # 抓寬一天、由下方 cutoff 精確限制 30h(Codex review)
            feed = _feedparser_parse_url_with_timeout(_gnews_rss(query, when="2d"))
            items = []
            for entry in feed.entries:
                if len(items) >= per_label:
                    break
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub and dt.datetime(*pub[:6], tzinfo=dt.timezone.utc) < cutoff:
                    continue
                title = str(entry.get("title", ""))[:90]
                norm = "".join(ch for ch in title.lower() if ch.isalnum())[:60]
                if norm in seen_titles:
                    continue
                seen_titles.add(norm)
                items.append({"title": title,
                              "link": str(entry.get("link", ""))})
            if items:
                out[label] = items
        except Exception as e:
            print(f"[local] 在地快訊 {label} 抓取失敗: {e}", file=sys.stderr)
    return out


def _render_local_news_html(local: dict) -> str:
    """在地快訊卡(中彰投雲):主題分行、標題為黑字可點連結。無資料回空。"""
    if not local:
        return ""
    import html as _h
    rows = []
    for label, items in local.items():
        lines = "".join(
            "<div style='font-size:13px;color:#334155;line-height:1.8;'>"
            + (f"<a href='{_h.escape(str(i.get('link', '')))}' "
               f"style='color:#0f172a;text-decoration:none;'>{_h.escape(str(i.get('title', '')))}</a>"
               if i.get("link") else _h.escape(str(i.get("title", ""))))
            + "</div>"
            for i in items)
        rows.append(f"<div style='margin:6px 0;'><b style='color:#0c4a6e;font-size:12px;'>"
                    f"{_h.escape(label)}</b>{lines}</div>")
    return (
        '<div style="border:1px solid #bae6fd;border-radius:10px;padding:8px 14px;'
        'margin:14px 0;background:#f0f9ff;">'
        '<div style="font-weight:700;font-size:14px;color:#0c4a6e;margin-bottom:2px;">'
        '在地快訊</div>'
        + "".join(rows) + "</div>")


SPORTS_NEWS_QUERIES = [
    ("世足", "世界盃足球 OR FIFA World Cup"),
    ("MLB", "MLB 大聯盟"),
    ("NBA", "NBA"),
    ("中華職棒", "中華職棒"),
    ("網球", "網球 ATP OR WTA OR 大滿貫"),
]


def _cpbl_from_wikipedia(year: Optional[int] = None) -> list[dict]:
    """Wikipedia「中華職棒N年」戰績表(全球可用,社群賽後更新)。

    頁內有多個 wikitable(熱身賽/上半季/下半季),取「已賽總場次最大」者 = 當前賽季進度。
    """
    import re as _re
    year = year or dt.datetime.now(TPE).year
    page = f"中華職棒{year - 1989}年"   # 2026 = 中職 37 年
    r = _http_get("https://zh.wikipedia.org/w/api.php", params={
        "action": "parse", "page": page, "prop": "wikitext",
        "format": "json", "formatversion": "2"},
        timeout=20, headers={"User-Agent": "MorningReportBot/1.0"})
    r.raise_for_status()
    wikitext = (r.json().get("parse") or {}).get("wikitext", "")
    # 列格式:| 1 || [[富邦悍將]] ||應賽||已賽||勝||敗||和||{{Winning percentage|勝|敗}}||勝差
    # 勝率欄是 {{Winning percentage|W|L}} 模板(內含單 pipe),用 non-greedy 跨過
    row_re = _re.compile(
        r"\|\s*(\d+)\s*\|\|\s*\[\[([^\]|]+)(?:\|[^\]]*)?\]\]\s*"
        r"\|\|\s*(\d+)\s*\|\|\s*(\d+)\s*\|\|\s*(\d+)\s*\|\|\s*(\d+)\s*\|\|\s*(\d+)"
        r"\s*\|\|.*?\|\|\s*([^\n|]*)")
    best: list[dict] = []
    best_games = -1
    for block in wikitext.split('{|'):
        rows = []
        played_sum = 0
        for m in row_re.finditer(block):
            rank, team = int(m.group(1)), m.group(2).strip()
            played, wins, losses, ties = (
                int(m.group(4)), int(m.group(5)), int(m.group(6)), int(m.group(7)))
            pct = wins / (wins + losses) if (wins + losses) else 0.0
            rows.append({"rank": rank, "team": team, "games": str(played),
                         "wdl": f"{wins}-{ties}-{losses}", "pct": f"{pct:.3f}",
                         "gb": m.group(8).strip().replace("–", "-")})
            played_sum += played
        if len(rows) >= 4 and played_sum > best_games:
            best, best_games = rows, played_sum
    return best[:6]


def fetch_cpbl_standings(meta: Optional[dict] = None) -> list[dict]:
    """CPBL 戰績:官網直連(台灣 IP 可)→ Wikipedia 備援(GitHub Actions 海外 IP
    被官網 geo-block 回 404;r.jina.ai 代理實測也被擋,改用 wiki)。失敗回空。

    meta(可選 dict)會被填入 {"source": "官網"/"Wikipedia 備援"/"無"},供渲染端標註
    資料來源透明度(Wikipedia 取決於社群編輯速度,可能遲滯)。
    """
    import re as _re
    if meta is not None:
        meta["source"] = "無"
    try:
        r = _http_get("https://www.cpbl.com.tw/standings/season", timeout=15,
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Accept-Language": "zh-TW,zh;q=0.9"})
        r.raise_for_status()
        out = []
        pattern = _re.compile(
            r'<div class="rank">(\d+)</div>.*?/team\?TeamNo=[^"]*">([^<]+)</a>.*?'
            r'<td class="num">(\d+)</td>\s*<td class="num">([\d\-]+)</td>\s*'
            r'<td class="num">([\d.]+)</td>\s*<td class="num">([^<]*)</td>',
            _re.S)
        for m in pattern.finditer(r.text):
            out.append({"rank": int(m.group(1)), "team": m.group(2).strip(),
                        "games": m.group(3), "wdl": m.group(4),
                        "pct": m.group(5), "gb": m.group(6).strip()})
            if len(out) >= 6:
                break
        if out:
            if meta is not None:
                meta["source"] = "官網"
            return out
    except Exception as e:
        print(f"[sports] CPBL 官網失敗({str(e)[:60]}),改用 Wikipedia", file=sys.stderr)
    try:
        rows = _cpbl_from_wikipedia()
        if rows and meta is not None:
            meta["source"] = "Wikipedia 備援"
        return rows
    except Exception as e:
        print(f"[sports] CPBL Wikipedia 備援也失敗: {e}", file=sys.stderr)
        return []


# 世足國家隊英文→繁中對照(ESPN 回傳英文隊名)。查無對照時回原英文,不漏資料。
_WC_TEAM_ZH = {
    "Argentina": "阿根廷", "Brazil": "巴西", "France": "法國", "Spain": "西班牙",
    "England": "英格蘭", "Portugal": "葡萄牙", "Germany": "德國", "Netherlands": "荷蘭",
    "Belgium": "比利時", "Italy": "義大利", "Croatia": "克羅埃西亞", "Uruguay": "烏拉圭",
    "Colombia": "哥倫比亞", "Mexico": "墨西哥", "United States": "美國", "USA": "美國",
    "Canada": "加拿大", "Japan": "日本", "South Korea": "南韓", "Korea Republic": "南韓",
    "Australia": "澳洲", "Morocco": "摩洛哥", "Senegal": "塞內加爾", "Switzerland": "瑞士",
    "Denmark": "丹麥", "Poland": "波蘭", "Serbia": "塞爾維亞", "Ecuador": "厄瓜多",
    "Ghana": "迦納", "Cameroon": "喀麥隆", "Nigeria": "奈及利亞", "Egypt": "埃及",
    "Tunisia": "突尼西亞", "Algeria": "阿爾及利亞", "Ivory Coast": "象牙海岸",
    "Cote d'Ivoire": "象牙海岸", "Saudi Arabia": "沙烏地阿拉伯", "Iran": "伊朗",
    "Qatar": "卡達", "Iraq": "伊拉克", "Jordan": "約旦", "Uzbekistan": "烏茲別克",
    "Austria": "奧地利", "Norway": "挪威", "Sweden": "瑞典", "Scotland": "蘇格蘭",
    "Wales": "威爾斯", "Turkey": "土耳其", "Ukraine": "烏克蘭", "Czech Republic": "捷克",
    "Czechia": "捷克", "Hungary": "匈牙利", "Greece": "希臘", "Romania": "羅馬尼亞",
    "Slovakia": "斯洛伐克", "Slovenia": "斯洛維尼亞", "Paraguay": "巴拉圭", "Peru": "秘魯",
    "Chile": "智利", "Venezuela": "委內瑞拉", "Bolivia": "玻利維亞", "Costa Rica": "哥斯大黎加",
    "Panama": "巴拿馬", "Honduras": "宏都拉斯", "Jamaica": "牙買加", "New Zealand": "紐西蘭",
    "South Africa": "南非", "Mali": "馬利", "Burkina Faso": "布吉納法索", "DR Congo": "剛果民主共和國",
    "Congo DR": "剛果民主共和國", "Cape Verde": "維德角", "Angola": "安哥拉", "Gabon": "加彭",
    "China": "中國", "China PR": "中國", "Cuba": "古巴", "Curacao": "古拉索", "Curaçao": "古拉索",
    "Haiti": "海地", "El Salvador": "薩爾瓦多", "Bosnia-Herzegovina": "波士尼亞",
    "Türkiye": "土耳其",
}


def _wc_zh(name: str) -> str:
    name = (name or "").strip()
    return _WC_TEAM_ZH.get(name, name)


# 2026 世界盃賽期(美/加/墨,2026-06-11 ~ 2026-07-19)。賽期外不抓,避免 ESPN
# 殘留上屆分組戰績被誤當「目前累計」顯示(stale standings)。下屆需更新此區間,
# 與既有 FOMC_2026 硬編慣例一致。
_WC_WINDOW = (dt.date(2026, 6, 11), dt.date(2026, 7, 19))
# 淘汰賽(32 強)首日:對戰表範圍查詢的固定下界。不可用「今天−N 天」滾動窗——
# 小組賽 72 場+淘汰賽 32 場(未賽 fixtures 也算 events)=104 場會超過 ESPN 單次
# 回覆 100 場上限而截尾(Codex review P1:06/28 查 06/03→07/20 正好全包)。
# 取 −1 天緩衝:ESPN 以美國日期分桶,台北 06/28 早上的場次在 06/27 桶。
_WC_KO_START = dt.date(2026, 6, 28)


# ESPN season.slug → 中文回合名與顯示順序。回合資訊在 event.season.slug
# (實測;notes.headline 是 PK 大戰註記如「Paraguay advance 4-3 on penalties」,不是回合)。
_WC_ROUND_ZH: dict[str, tuple[int, str]] = {
    "group-stage": (0, "小組賽"),
    "round-of-32": (1, "32 強"),
    "round-of-16": (2, "16 強"),
    "quarterfinals": (3, "8 強"),
    "semifinals": (4, "4 強"),
    "3rd-place-match": (5, "季軍戰"),   # 實測 slug(2026-07-14)
    "third-place": (5, "季軍戰"),
    "3rd-place": (5, "季軍戰"),
    "third-place-game": (5, "季軍戰"),
    "final": (6, "決賽"),               # 實測:決賽正確翻譯,slug 為 final(s) 之一
    "finals": (6, "決賽"),
}

# 未定隊伍的英文佔位(如「Semifinal 2 Winner」)→ 繁中。僅處理實測見過的型態,
# 其餘原樣顯示(誠實 fallback,不猜)。
_WC_PLACEHOLDER_RE = None   # lazy compile


def _wc_placeholder_zh(name: str) -> str:
    global _WC_PLACEHOLDER_RE
    if _WC_PLACEHOLDER_RE is None:
        import re as _re
        _WC_PLACEHOLDER_RE = _re.compile(
            r"^(Round of 32|Round of 16|Quarterfinal|Semifinal)s?\s+(\d+)\s+(Winner|Loser)$",
            _re.IGNORECASE)
    m = _WC_PLACEHOLDER_RE.match(str(name or "").strip())
    if not m:
        return name
    stage = {"round of 32": "32 強", "round of 16": "16 強",
             "quarterfinal": "8 強", "semifinal": "4 強"}[m.group(1).lower()]
    side = "勝方" if m.group(3).lower() == "winner" else "負方"
    return f"{stage}戰{m.group(2)}{side}"


def _wc_round_of(ev: dict) -> tuple[int, str]:
    """回 (順序, 中文回合名)。未知 slug 原樣顯示(誠實不猜),排在最後。"""
    slug = str(((ev.get("season") or {}).get("slug")) or "").strip().lower()
    if slug in _WC_ROUND_ZH:
        return _WC_ROUND_ZH[slug]
    return (9, slug.replace("-", " ") or "其他")


def _espn_match_odds_line(comp: dict, zh_by_side: dict) -> str:
    """把 ESPN 賽事的 DraftKings 賭盤轉成白話一行:「賭盤:甲 58%・和 24%・乙 18%」。

    美式賠率→隱含機率(+175→100/275;-140→140/240),三向加總正規化成 100%
    (去除莊家抽水的粗略近似)。無賠率/解析失敗回 ""(顯示層,失敗不影響賽程)。
    使用者要求 2026-07-15:體育加入賭盤預測(如世足冠軍機率)。"""
    try:
        odds = (comp.get("odds") or [{}])[0]
        ml = odds.get("moneyline") or {}

        def _imp(o_str) -> Optional[float]:
            try:
                o = float(str(o_str).replace("+", ""))
            except (TypeError, ValueError):
                return None
            if o > 0:
                return 100.0 / (o + 100.0)
            return -o / (-o + 100.0)

        probs: list[tuple[str, float]] = []
        for side in ("home", "away"):
            o = ((ml.get(side) or {}).get("close") or {}).get("odds") \
                or ((ml.get(side) or {}).get("open") or {}).get("odds")
            p = _imp(o)
            if p is not None and zh_by_side.get(side):
                probs.append((zh_by_side[side], p))
        draw = _imp((odds.get("drawOdds") or {}).get("moneyLine"))
        if draw is not None:
            probs.append(("和", draw))
        if len(probs) < 2:
            return ""
        total = sum(p for _, p in probs)
        parts = "・".join(f"{name} {p / total * 100:.0f}%" for name, p in probs)
        provider = str((odds.get("provider") or {}).get("name") or "").strip()
        # 含「和」=足球 90 分鐘三向市場(非晉級/奪冠盤)——明確標示,
        # 不可宣稱為冠軍機率(淘汰賽可能延長/PK;Codex review)
        label = "賭盤(90分鐘)" if draw is not None else "賭盤"
        return f"{label}:{parts}" + (f"({provider})" if provider else "")
    except Exception:
        return ""


def fetch_worldcup(now_tpe: Optional[dt.datetime] = None) -> dict:
    """世足(FIFA World Cup):昨日/最新完賽戰績 + 各分組累計戰績表 + 淘汰賽對戰表。

    使用者需求:世足要各階段(小組賽/8 強/4 強/決賽…)完整賽果與未賽場次的開球時間。
    資料源 ESPN(免費,無金鑰)。賽期外回空 dict,渲染端自動略過。
    """
    now_tpe = now_tpe or dt.datetime.now(TPE)
    out: dict = {"results": [], "groups": []}
    # 賽期外直接略過:既省呼叫,也避免顯示上屆殘留戰績表。
    if not (_WC_WINDOW[0] <= now_tpe.date() <= _WC_WINDOW[1]):
        return out
    # 完賽比分:ESPN 以「美國日期」分桶,2026 世足在北美 → 台北早上的場次落在
    # 「台北日期−1」的桶;昨天(台北)早上的場次就落在「−2」的桶。只查 back=(1,0)
    # 會讓台北 07:00 後開打的比賽永遠消失(實測:台北 07/12 09:00 的 8 強戰在
    # bucket 20260711)→ 改掃 back=(2,1,0),顯示日期以各場開球時間換算台北為準。
    seen = set()
    for back in (2, 1, 0):
        day = (now_tpe - dt.timedelta(days=back)).strftime("%Y%m%d")
        try:
            r = _http_get(
                "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
                params={"dates": day}, timeout=15)
            r.raise_for_status()
            for ev in r.json().get("events", []):
                comp = (ev.get("competitions") or [{}])[0]
                teams = comp.get("competitors", [])
                if len(teams) != 2:
                    continue
                home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
                away = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
                st = (((ev.get("status") or {}).get("type")) or {})
                if not st.get("completed"):
                    continue  # 只列已完賽,未開賽/進行中不列「昨日戰績」
                gid = ev.get("id") or ev.get("uid")
                if gid in seen:
                    continue
                seen.add(gid)
                ht = _wc_zh((home.get("team") or {}).get("displayName")
                            or (home.get("team") or {}).get("name", "?"))
                at = _wc_zh((away.get("team") or {}).get("displayName")
                            or (away.get("team") or {}).get("name", "?"))
                detail = st.get("shortDetail") or st.get("detail") or "完賽"
                # 顯示日期用「該場開球時間換算台北」,不可用查詢桶日(桶=美國日,會標錯天)
                gdate = f"{day[4:6]}/{day[6:]}"
                gts = ""
                try:
                    iso = str(ev.get("date") or "").replace("Z", "+00:00")
                    ko_tpe = dt.datetime.fromisoformat(iso).astimezone(TPE)
                    gdate = ko_tpe.strftime("%m/%d")
                    gts = ko_tpe.isoformat()
                except Exception:
                    pass
                out["results"].append({
                    "text": f"{at} {away.get('score', '-')} : {home.get('score', '-')} {ht}",
                    "status": str(detail)[:18],
                    "date": gdate,
                    # 回合取自 season.slug(notes.headline 是 PK 註記非回合,勿用)
                    "round": _wc_round_of(ev)[1],
                    "_ts": gts,
                })
        except Exception as e:
            print(f"[sports] 世足比分抓取失敗({day}): {e}", file=sys.stderr)
    out["results"].sort(key=lambda g: g.get("_ts") or "", reverse=True)   # 新→舊
    for g in out["results"]:
        g.pop("_ts", None)
    out["results"] = out["results"][:14]
    # 各分組累計戰績表
    try:
        r = _http_get(
            "https://site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings",
            timeout=15)
        r.raise_for_status()
        for grp in r.json().get("children", []):
            gname = str(grp.get("name") or grp.get("abbreviation") or "").strip()
            gname = gname.replace("Group ", "").strip() + " 組" if gname else "分組"
            rows = []
            for en in (grp.get("standings") or {}).get("entries", []):
                stats = {s.get("name"): s for s in en.get("stats", [])}

                def _v(key):
                    return int(float((stats.get(key) or {}).get("value") or 0))
                rows.append({
                    "team": _wc_zh((en.get("team") or {}).get("displayName")
                                   or (en.get("team") or {}).get("name", "?")),
                    "gp": _v("gamesPlayed"), "w": _v("wins"),
                    "d": _v("ties") or _v("draws"), "l": _v("losses"),
                    "pts": _v("points"),
                    "gd": _v("pointDifferential"), "gf": _v("pointsFor"),
                    "rank": _v("rank"),
                })
            # ESPN entries 並非依名次排序(實測會以種子/隊名序回傳),自行依 FIFA 小組賽
            # tie-break 排序:積分→淨勝分→進球數;三者全同時,再用 ESPN 的 rank 收尾
            # (rank 已含對戰成績/公平競賽分等我方無法計算的次序),前 2 名才是真正晉級線。
            rows.sort(key=lambda r: (-r["pts"], -r["gd"], -r["gf"],
                                     r["rank"] if r["rank"] else 99))
            if rows:
                out["groups"].append({"name": gname, "rows": rows})
    except Exception as e:
        print(f"[sports] 世足分組戰績抓取失敗: {e}", file=sys.stderr)
    # 今日/明日(台北)賽程預告——小組賽結束後自動變成淘汰賽對戰。
    # ESPN 的 dates 以其自身行事曆(UTC 為主)分桶,與台北日界不一致,故多查幾天
    # (昨~後天),把開球時間換成台北時區後,只留台北「今天/明天」的場次,避免漏抓或錯抓。
    fixtures = []
    fseen = set()
    today_tpe = now_tpe.date()
    tomorrow_tpe = today_tpe + dt.timedelta(days=1)
    for off in (-1, 0, 1, 2):
        day = (now_tpe + dt.timedelta(days=off)).strftime("%Y%m%d")
        try:
            r = _http_get(
                "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
                params={"dates": day}, timeout=15)
            r.raise_for_status()
            for ev in r.json().get("events", []):
                gid = ev.get("id") or ev.get("uid")
                if gid in fseen:
                    continue
                st = (((ev.get("status") or {}).get("type")) or {})
                if st.get("completed") or st.get("state") == "in":
                    continue  # 只列尚未開打的
                try:
                    iso = str(ev.get("date") or "").replace("Z", "+00:00")
                    ko = dt.datetime.fromisoformat(iso).astimezone(TPE)
                except Exception:
                    continue  # 無法判定開球時間就不列(避免誤放錯日場次)
                if ko.date() not in (today_tpe, tomorrow_tpe):
                    continue
                comp = (ev.get("competitions") or [{}])[0]
                teams = comp.get("competitors", [])
                if len(teams) != 2:
                    continue
                fseen.add(gid)
                names = [_wc_zh((t.get("team") or {}).get("displayName")
                                or (t.get("team") or {}).get("name", "?")) for t in teams]
                rnd = str((comp.get("notes") or [{}])[0].get("headline") or "")[:24]
                # 賭盤隱含勝率(DraftKings;使用者要求 2026-07-15)——決賽場次的賭盤
                # 即「誰會冠軍幾 %」;失敗回空字串不影響賽程
                zh_by_side = {str(t.get("homeAway") or ""): _wc_zh(
                    (t.get("team") or {}).get("displayName")
                    or (t.get("team") or {}).get("name", "?")) for t in teams}
                fixtures.append({"text": " vs ".join(names),
                                 "kickoff": ko.strftime("%m/%d %H:%M"),
                                 "round": rnd, "_ko": ko,
                                 "odds": _espn_match_odds_line(comp, zh_by_side)})
        except Exception as e:
            print(f"[sports] 世足賽程抓取失敗({day}): {e}", file=sys.stderr)
    fixtures.sort(key=lambda f: f["_ko"])
    for f in fixtures:
        f.pop("_ko", None)
    out["fixtures"] = fixtures[:10]
    # ---------- 淘汰賽對戰表:各回合完整賽果 + 未賽場次(台北開球時間) ----------
    # 使用者需求:不只昨日,32 強→決賽的「每一階段」賽果與開賽時間都要看得到。
    # 範圍查詢一次取回,起點固定為淘汰賽首日−1(緩衝美國日期桶):
    # 淘汰賽全部 32 場+末日小組賽遠低於 ESPN 單次回覆 100 場上限。
    # 不可用滾動窗(會把小組賽+全部未賽 fixtures 包進來超過上限而截尾);
    # 小組賽期間不查(對戰表僅 TBD 佔位無資訊,且會誤觸發渲染端的小組表收斂)。
    if now_tpe.date() < _WC_KO_START:
        return out
    try:
        span = (f"{(_WC_KO_START - dt.timedelta(days=1)).strftime('%Y%m%d')}"
                f"-{(_WC_WINDOW[1] + dt.timedelta(days=1)).strftime('%Y%m%d')}")
        r = _http_get(
            "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
            params={"dates": span}, timeout=25)
        r.raise_for_status()
        rounds: dict[str, dict] = {}
        for ev in r.json().get("events", []):
            rank, rname = _wc_round_of(ev)
            if rank == 0:
                continue   # 小組賽已有積分表/近期戰績,不進對戰表
            comp = (ev.get("competitions") or [{}])[0]
            teams = comp.get("competitors", [])
            if len(teams) != 2:
                continue
            home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
            away = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
            ht = _wc_placeholder_zh(_wc_zh((home.get("team") or {}).get("displayName")
                                           or (home.get("team") or {}).get("name", "?")))
            at = _wc_placeholder_zh(_wc_zh((away.get("team") or {}).get("displayName")
                                           or (away.get("team") or {}).get("name", "?")))
            try:
                iso = str(ev.get("date") or "").replace("Z", "+00:00")
                ko = dt.datetime.fromisoformat(iso).astimezone(TPE)
            except Exception:
                continue   # 無法定時間就不列,避免錯標
            st = (((ev.get("status") or {}).get("type")) or {})
            if st.get("completed"):
                # PK 大戰註記在 notes.headline(如「X advance 4-3 on penalties」)
                pk = str((comp.get("notes") or [{}])[0].get("headline") or "")
                game = {"text": f"{at} {away.get('score', '-')} : {home.get('score', '-')} {ht}"
                                + (f"(PK,{_wc_zh(pk.split(' advance')[0])} 晉級)" if "penalt" in pk.lower() else ""),
                        "when": ko.strftime("%m/%d"), "done": True}
            elif st.get("state") == "in":
                game = {"text": f"{at} vs {ht}", "when": "進行中", "done": False}
            else:
                game = {"text": f"{at} vs {ht}",
                        "when": ko.strftime("%m/%d %H:%M"), "done": False,
                        # 未賽場次附賭盤(90 分鐘三向市場,非晉級/奪冠盤——
                        # 顯示端已標示;TBD 佔位對戰無賠率自然回空)。
                        "odds": _espn_match_odds_line(comp, {"home": ht, "away": at})}
            game["_ko"] = ko
            rounds.setdefault(rname, {"rank": rank, "games": []})["games"].append(game)
        ko_rounds = []
        for rname, rd in sorted(rounds.items(), key=lambda kv: kv[1]["rank"]):
            rd["games"].sort(key=lambda g: g["_ko"])
            for g in rd["games"]:
                g.pop("_ko", None)
            ko_rounds.append({"name": rname, "games": rd["games"]})
        if ko_rounds:
            out["knockout"] = ko_rounds
    except Exception as e:
        print(f"[sports] 世足淘汰賽對戰表抓取失敗(不影響其他區塊): {e}", file=sys.stderr)
    return out


# 台灣旅外 MLB 球員(英文搜尋名 → 繁中顯示名)。可用環境變數 MLB_TW_PLAYERS 覆寫,
# 格式:「英文名:中文名,英文名:中文名」。資料源 MLB statsapi(免費,不 geo-block)。
_MLB_TW_PLAYERS_DEFAULT = {
    "Kai-Wei Teng": "鄧愷威",
    "Yu Chang": "張育成",
    "Yu-Min Lin": "林昱珉",
    "Chih-Jung Liu": "劉致榮",
}


def _mlb_tw_players() -> dict:
    raw = os.getenv("MLB_TW_PLAYERS", "").strip()
    if not raw:
        return dict(_MLB_TW_PLAYERS_DEFAULT)
    out = {}
    for pair in raw.split(","):
        if ":" in pair:
            en, zh = pair.split(":", 1)
            if en.strip():
                out[en.strip()] = zh.strip() or en.strip()
    return out or dict(_MLB_TW_PLAYERS_DEFAULT)


def fetch_mlb_taiwan_players(now_tpe: Optional[dt.datetime] = None) -> list[dict]:
    """台灣旅外 MLB 球員近期最新一場出賽數據。MLB statsapi(免費)。

    打者顯示打數/安打/全壘打/打點;投手顯示局數/責失/三振/防禦率;附出賽日期。
    台灣旅外 MLB 球員不多、且常上下大小聯盟,故取「近 7 天內最新一場」而非僅限昨日,
    超過 7 天未出賽(可能下放小聯盟/傷兵)則略過,避免顯示過舊資料。
    """
    now_tpe = now_tpe or dt.datetime.now(TPE)
    season = now_tpe.year
    recent_cut = now_tpe.date() - dt.timedelta(days=7)
    out = []
    for en_name, zh_name in _mlb_tw_players().items():
        try:
            r = _http_get("https://statsapi.mlb.com/api/v1/people/search",
                             params={"names": en_name}, timeout=12)
            people = r.json().get("people", [])
            if not people:
                continue
            pid = people[0].get("id")
            latest = None
            for grp in ("hitting", "pitching"):
                rr = _http_get(
                    f"https://statsapi.mlb.com/api/v1/people/{pid}/stats",
                    params={"stats": "gameLog", "season": season, "group": grp},
                    timeout=12)
                splits = (rr.json().get("stats") or [{}])[0].get("splits") or []
                if not splits:
                    continue
                sp = splits[-1]
                d = sp.get("date") or ""
                try:
                    gdate = dt.datetime.strptime(d, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if gdate < recent_cut:
                    continue
                if latest is None or gdate >= latest[0]:
                    summary = (sp.get("stat") or {}).get("summary") or ""
                    latest = (gdate, grp, summary)
            if latest:
                out.append({
                    "name": zh_name, "en": en_name,
                    "role": "投手" if latest[1] == "pitching" else "打者",
                    "date": latest[0].strftime("%m/%d"),
                    "summary": str(latest[2])[:60],
                })
        except Exception as e:
            print(f"[sports] MLB 台灣球員 {en_name} 抓取失敗: {e}", file=sys.stderr)
    return out


# ESPN 網球 grouping slug → 我方 tour 標籤(只取單打;雙打與其他略過)
_TENNIS_SINGLES = {"mens-singles": "ATP", "womens-singles": "WTA"}

# 賽事分層:大滿貫 > Masters1000/WTA1000 > 其他(ATP500/250)。投資人/球迷關注大滿貫遠多於週賽。
_TENNIS_SLAM_KEYS = ("australian open", "roland garros", "french open", "wimbledon",
                     "the championships", "us open")
_TENNIS_M1000_KEYS = ("indian wells", "miami open", "monte", "madrid open", "italian open",
                      "rome", "national bank", "canadian open", "cincinnati",
                      "western & southern", "shanghai", "paris masters", "rolex paris",
                      "masters 1000", "1000")


def _tennis_tier(name: str) -> tuple:
    """回 (rank, label):0=大滿貫, 1=Masters/1000, 2=其他。rank 越小越優先。"""
    low = (name or "").lower()
    if any(k in low for k in _TENNIS_SLAM_KEYS):
        return (0, "大滿貫")
    if any(k in low for k in _TENNIS_M1000_KEYS):
        return (1, "1000 級")
    return (2, "")


def _cut_word(s: str, n: int) -> str:
    """截字加省略號,盡量斷在空白處:避免「Hall of Fame Open for th」「10:00 AM ED」
    這種難看的中斷字(2026-07-12 週日信實見)。"""
    s = str(s or "")
    if len(s) <= n:
        return s
    cut = s.rfind(" ", 0, n)
    if cut < int(n * 0.6):   # 找不到合理空白(如中文/連續長字)就硬切
        cut = n - 1
    return s[:cut].rstrip() + "…"


def fetch_tennis_digest(now_tpe: Optional[dt.datetime] = None) -> dict:
    """網球 ATP/WTA 近日賽事與最新完賽勝方。ESPN 免費 scoreboard(不含逐盤比分)。

    注意 ESPN 行為:
      - 不帶 dates 會回某個預設日(可能是舊資料),故必須帶近 3 日的 dates 區間。
      - atp/wta 兩端點都會夾帶對方性別的 grouping,且同一場 competition 兩端點都出現;
        故以 grouping.slug 判定性別(只取單打)、並用 competition id 全域去重。
      - 場次為舊→新,故各 tour 依時間新→舊取最近 3 場再合併。
    """
    now_tpe = now_tpe or dt.datetime.now(TPE)
    # 範圍涵蓋「過去 3 天(賽果)+ 未來 7 天(即將開打的賽事)」——使用者要求有未來賽程
    dates = (f"{(now_tpe - dt.timedelta(days=3)).strftime('%Y%m%d')}"
             f"-{(now_tpe + dt.timedelta(days=7)).strftime('%Y%m%d')}")
    out: dict = {"tournaments": [], "results": []}

    def _an(c):
        a = c.get("athlete") or {}
        return a.get("shortName") or a.get("displayName") or "?"

    seen_comp = set()
    seen_tourn = set()
    by_label: dict = {"ATP": [], "WTA": []}
    for tour in ("atp", "wta"):
        try:
            r = _http_get(
                f"https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard",
                params={"dates": dates}, timeout=15)
            r.raise_for_status()
            # 先依賽事層級排序再截量:ESPN 回傳順序與重要性無關,溫網週小賽事一多,
            # 大滿貫會被擠出前 8 而整個消失(2026-07-12 週日信實見:溫網決賽週卻只列
            # Challenger 小賽)。大滿貫優先、同層保留原序,再取前 10。
            evs = sorted(r.json().get("events", []),
                         key=lambda e: _tennis_tier(
                             str(e.get("shortName") or e.get("name") or ""))[0])[:12]
            for ev in evs:
                st_type = (((ev.get("status") or {}).get("type")) or {})
                name = str(ev.get("shortName") or ev.get("name") or "")
                tier_rank, tier_label = _tennis_tier(name)
                # 賽事列表只列「進行中/即將開打」(已完賽的混在列表裡只是雜訊——賽果區已涵蓋;
                # 舊版顯示 "7/5 - 11:05 AM EDT" 這種美東原始字串,使用者反映混亂,改台北日期)
                state = str(st_type.get("state") or "")
                if name and name not in seen_tourn and state in ("pre", "in"):
                    seen_tourn.add(name)
                    if state == "in":
                        when = "進行中"
                    else:
                        try:
                            iso = str(ev.get("date") or "").replace("Z", "+00:00")
                            when = dt.datetime.fromisoformat(iso).astimezone(TPE).strftime("%m/%d 起")
                        except Exception:
                            when = "即將"
                    # event_key=未截斷原名:渲染端「已結束→冠軍行」收斂靠它與賽果比對;
                    # 顯示名 40 字截斷 vs 賽果 30 字截斷不相等,長名賽事會被誤判已結束(Codex review)
                    out["tournaments"].append({"name": _cut_word(name, 40),
                                               "event_key": name,
                                               "status": when,
                                               "tier": tier_label, "_tier": tier_rank})
                for g in (ev.get("groupings") or []):
                    slug = str((g.get("grouping") or {}).get("slug") or "")
                    label = _TENNIS_SINGLES.get(slug)
                    if not label:
                        continue  # 只取單打,跳過雙打/其他
                    for comp in (g.get("competitions") or []):
                        cid = comp.get("id")
                        if cid in seen_comp:
                            continue  # 兩端點重複的同一場去重
                        st = (((comp.get("status") or {}).get("type")) or {})
                        cs = comp.get("competitors", [])
                        if len(cs) != 2 or not st.get("completed"):
                            continue
                        win = next((c for c in cs if c.get("winner")), None)
                        lose = next((c for c in cs if not c.get("winner")), None)
                        if not (win and lose):
                            continue
                        seen_comp.add(cid)
                        by_label[label].append({
                            "tour": label, "winner": _an(win), "loser": _an(lose),
                            "event": _cut_word(name, 30), "event_key": name,
                            "tier": tier_label, "_tier": tier_rank,
                            "_ts": str(comp.get("date") or ev.get("date") or "")})
        except Exception as e:
            print(f"[sports] 網球 {tour} 抓取失敗: {e}", file=sys.stderr)
    # 可選:只顯示大滿貫(TENNIS_FAVOR_SLAMS=1 且當期確實有大滿貫時)
    if os.getenv("TENNIS_FAVOR_SLAMS") == "1":
        if any(m["_tier"] == 0 for ms in by_label.values() for m in ms):
            for k in by_label:
                by_label[k] = [m for m in by_label[k] if m["_tier"] == 0]
        out["tournaments"] = [t for t in out["tournaments"] if t["_tier"] == 0] or out["tournaments"]
    combined = []
    for label in ("ATP", "WTA"):
        ms = sorted(by_label[label], key=lambda m: m["_ts"], reverse=True)  # 新→舊
        ms.sort(key=lambda m: m["_tier"])                                   # 穩定:大滿貫優先
        combined += ms[:3]
    combined.sort(key=lambda m: m["_ts"], reverse=True)
    combined.sort(key=lambda m: m["_tier"])     # 穩定排序:大滿貫 > 1000 > 其他,同層新→舊
    for m in combined:
        # 賽果附台北日期(使用者反映賽果不知何時打的、區塊混亂)
        try:
            iso = str(m["_ts"]).replace("Z", "+00:00")
            m["date"] = dt.datetime.fromisoformat(iso).astimezone(TPE).strftime("%m/%d")
        except Exception:
            m["date"] = ""
        m.pop("_ts", None)
        m.pop("_tier", None)
    out["results"] = combined[:6]
    out["tournaments"].sort(key=lambda t: t["_tier"])
    for t in out["tournaments"]:
        t.pop("_tier", None)
    out["tournaments"] = out["tournaments"][:6]
    return out


def fetch_cpbl_scores(now_tpe: Optional[dt.datetime] = None) -> list[dict]:
    """中華職棒昨日(及今日已完賽)比分。

    資料源 Yahoo 運動 sports editorial scoreboard API(免金鑰、全球 CDN 可連),
    避開中職官網對 GitHub Actions 海外機房 IP 的 geo-block。抓不到回空,
    渲染端自動只保留戰績表。
    """
    from email.utils import parsedate_to_datetime
    now_tpe = now_tpe or dt.datetime.now(TPE)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
               "Accept": "application/json"}
    out = []
    seen = set()
    for back in (1, 0):   # 昨日為主,今日已完賽者一併收
        day = (now_tpe - dt.timedelta(days=back)).strftime("%Y-%m-%d")
        try:
            r = _http_get(
                "https://api-secure.sports.yahoo.com/v1/editorial/s/scoreboard",
                params={"leagues": "cpbl", "date": day}, headers=headers, timeout=15)
            r.raise_for_status()
            sb = ((r.json().get("service") or {}).get("scoreboard")) or {}
            games = sb.get("games") or {}
            teams = sb.get("teams") or {}
            for gid, g in games.items():
                if gid in seen or g.get("status_type") != "status.type.final":
                    continue  # 只列已完賽
                # 比分缺值不可當 0(會誤報 0:0 或錯判勝方),解析失敗就跳過該場。
                # 注意:驗證通過才標記 seen —— 否則某桶缺比分的同場會擋掉另一桶有效的版本。
                a_raw = g.get("total_away_points")
                h_raw = g.get("total_home_points")
                if a_raw in (None, "") or h_raw in (None, ""):
                    continue
                try:
                    a_s, h_s = int(float(a_raw)), int(float(h_raw))
                except (TypeError, ValueError, OverflowError):
                    continue
                seen.add(gid)
                away = str((teams.get(g.get("away_team_id")) or {}).get("display_name") or "?")
                home = str((teams.get(g.get("home_team_id")) or {}).get("display_name") or "?")
                # 日期以該場開賽時間(轉台北)為準,而非查詢日期桶
                gdate = f"{day[5:7]}/{day[8:]}"
                try:
                    gdt = parsedate_to_datetime(str(g.get("start_time") or ""))
                    if gdt.tzinfo is None:
                        gdt = gdt.replace(tzinfo=dt.timezone.utc)
                    gdate = gdt.astimezone(TPE).strftime("%m/%d")
                except (ValueError, TypeError):
                    pass
                out.append({
                    "away": away, "home": home, "away_score": a_s, "home_score": h_s,
                    "winner": "away" if a_s > h_s else ("home" if h_s > a_s else ""),
                    "date": gdate,
                })
        except Exception as e:
            print(f"[sports] CPBL 比分抓取失敗({day}): {e}", file=sys.stderr)
    return out[:10]


def fetch_cpbl_today_fixtures(now_tpe: Optional[dt.datetime] = None,
                              days: int = 7) -> list[dict]:
    """中華職棒「未來一週賽程」(對戰組合+台北開賽日期時間)。

    使用者需求:體育要有未來一週賽程,不只賽果。與 fetch_cpbl_scores 同一 Yahoo
    scoreboard 端點(免金鑰),逐日查未來 days 天、只取「尚未開打」場次。
    單日失敗略過該日(graceful degrade),全失敗回空(渲染端自動略過)。
    """
    from email.utils import parsedate_to_datetime
    now_tpe = now_tpe or dt.datetime.now(TPE)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
               "Accept": "application/json"}
    out = []
    seen: set = set()
    for off in range(days):
        day = (now_tpe + dt.timedelta(days=off)).strftime("%Y-%m-%d")
        try:
            r = _http_get(
                "https://api-secure.sports.yahoo.com/v1/editorial/s/scoreboard",
                params={"leagues": "cpbl", "date": day}, headers=headers, timeout=15)
            r.raise_for_status()
            sb = ((r.json().get("service") or {}).get("scoreboard")) or {}
            games = sb.get("games") or {}
            teams = sb.get("teams") or {}
            for gid, g in games.items():
                if gid in seen:
                    continue   # Yahoo 日期桶偶重疊,依 game id 去重
                status = str(g.get("status_type") or "")
                if "final" in status or "postponed" in status or "cancel" in status:
                    continue   # 只列未開打(進行中也略過:晨報寄出時中職不會在打)
                try:
                    gdt = parsedate_to_datetime(str(g.get("start_time") or ""))
                    if gdt.tzinfo is None:
                        gdt = gdt.replace(tzinfo=dt.timezone.utc)
                    gdt = gdt.astimezone(TPE)
                except (ValueError, TypeError):
                    continue   # 沒有可靠開賽時間就不列(開賽時間是本區塊的存在意義)
                if gdt < now_tpe or gdt.date() > (now_tpe + dt.timedelta(days=days)).date():
                    continue   # 只留「現在之後、一週之內」
                seen.add(gid)
                away = str((teams.get(g.get("away_team_id")) or {}).get("display_name") or "?")
                home = str((teams.get(g.get("home_team_id")) or {}).get("display_name") or "?")
                out.append({"away": away, "home": home,
                            "date": gdt.strftime("%m/%d"),
                            "start": gdt.strftime("%H:%M"), "_ko": gdt})
        except Exception as e:
            print(f"[sports] CPBL 賽程 {day} 抓取失敗: {e}", file=sys.stderr)
    out.sort(key=lambda x: x["_ko"])
    for x in out:
        x.pop("_ko", None)
    return out[:16]


def _espn_week_fixtures(league_path: str, now_tpe: dt.datetime, days: int = 7,
                        cap: int = 8) -> list[dict]:
    """ESPN scoreboard 範圍查詢 → 未來 days 天「未開打」場次(台北時間)。

    使用者需求:體育要有未來一週賽程。共用 helper 供 MLB/NBA;失敗拋給呼叫端
    (呼叫端各自 try 包,graceful degrade)。回 [{"text","when","special"}...] 依時間排序。
    """
    span = (f"{now_tpe.strftime('%Y%m%d')}"
            f"-{(now_tpe + dt.timedelta(days=days)).strftime('%Y%m%d')}")
    r = _http_get(f"https://site.api.espn.com/apis/site/v2/sports/{league_path}/scoreboard",
                  params={"dates": span}, timeout=20)
    r.raise_for_status()
    out = []
    for ev in r.json().get("events", []):
        st = (((ev.get("status") or {}).get("type")) or {})
        if st.get("state") != "pre":
            continue   # 只列未開打
        try:
            iso = str(ev.get("date") or "").replace("Z", "+00:00")
            ko = dt.datetime.fromisoformat(iso).astimezone(TPE)
        except Exception:
            continue   # 無可靠開賽時間就不列
        name = str(ev.get("shortName") or ev.get("name") or "")
        slug = str((ev.get("season") or {}).get("slug") or "").lower()
        special = "all-star" in slug or "All-Star" in str(ev.get("name") or "")
        # 保留兩隊 competitor 原始結構供關注隊過濾:shortName 只有縮寫("LAL @ BOS"),
        # 而 NBA_FAVORITE_TEAMS 文件明載用全名——只用 shortName 過濾會全數濾光;
        # 攤平成字串再 substring 又會讓 'den' 誤中 'Golden State'(Codex review 兩輪 P2)。
        # 一律交給既有 _nba_team_matches_favorite(單字整詞、多字子字串)判定。
        _competitors = list(((ev.get("competitions") or [{}])[0].get("competitors")) or [])
        # 賭盤(DraftKings 隱含機率;使用者要求 2026-07-15 MLB/NBA 也要)——
        # 以隊名縮寫組行(MLB 渲染端會再轉中文);無賠率回空字串不影響賽程
        _abbr_by_side = {
            str(c.get("homeAway") or ""): str(
                (c.get("team") or {}).get("abbreviation")
                or (c.get("team") or {}).get("shortDisplayName") or "")
            for c in _competitors}
        _odds = _espn_match_odds_line(
            (ev.get("competitions") or [{}])[0], _abbr_by_side)
        out.append({"text": name, "when": ko.strftime("%m/%d %H:%M"),
                    "special": special, "odds": _odds,
                    "_competitors": _competitors, "_ko": ko})
    out.sort(key=lambda g: g["_ko"])
    for g in out:
        g.pop("_ko", None)
    return out[:cap]


def fetch_mlb_week_fixtures(now_tpe: Optional[dt.datetime] = None,
                            top_teams: Optional[set] = None) -> list[dict]:
    """MLB 未來一週「焦點」賽程:強隊(戰績前列)對戰 + 特別賽事(明星賽)。

    一週例行賽 ~100 場全列是雜訊;只留兩隊任一在戰績前列者(top_teams 由
    fetch_sports_digest 的戰績榜前三供給),或明星賽等特別場次。台北時間。
    """
    now_tpe = now_tpe or dt.datetime.now(TPE)
    games = _espn_week_fixtures("baseball/mlb", now_tpe, cap=200)
    tops = {str(t).upper() for t in (top_teams or set()) if t}
    if tops:
        def _keep(g):
            if g.get("special"):
                return True
            # shortName 形如 "TB @ BOS" → 任一隊在強隊清單即保留
            teams = {p.strip().upper() for p in str(g["text"]).replace("@", " ").split()}
            return bool(teams & tops)
        games = [g for g in games if _keep(g)]
    for g in games:
        g.pop("_competitors", None)
    return games[:8]


def fetch_nba_week_fixtures(now_tpe: Optional[dt.datetime] = None) -> list[dict]:
    """NBA 未來一週賽程(台北時間)。休賽季 ESPN 自然回空(渲染端顯示休賽季說明)。
    有設 NBA_FAVORITE_TEAMS 時只列關注球隊場次;未設則列前 10 場。"""
    now_tpe = now_tpe or dt.datetime.now(TPE)
    games = _espn_week_fixtures("basketball/nba", now_tpe, cap=100)
    favs = _nba_favorite_teams()
    if favs:
        # 沿用 _nba_team_matches_favorite 的既有規則:單字關鍵字整詞比對
        # ('den' 不誤中 'Golden State')、多字關鍵字子字串比對。
        games = [g for g in games
                 if g.get("special")
                 or any(_nba_team_matches_favorite(c, f)
                        for c in (g.get("_competitors") or []) for f in favs)]
    for g in games:
        g.pop("_competitors", None)
    return games[:10]


def _nba_offseason_note(now_tpe: dt.datetime) -> str:
    """NBA 休賽季(約 6 月下旬–10 月中)的階段說明,讓冠軍賽結束後該區不致空白空轉。
    球季進行中(含冠軍賽)→ 回空字串(由實際賽果渲染)。"""
    m, d = now_tpe.month, now_tpe.day
    if m == 6 and d >= 20:
        return "NBA 球季尾聲;選秀(6 月底)、自由市場(7 月初)即將登場。"
    if m == 7:
        return "NBA 休賽季:自由市場與夏季聯賽進行中。"
    if m == 8:
        return "NBA 休賽季(交易與陣容調整期)。"
    if m == 9:
        return "NBA 休賽季;新球季 10 月中下旬開打。"
    if m == 10 and d <= 14:
        return "NBA 季前賽期間,例行賽 10 月下旬開打。"
    return ""


def _nba_favorite_teams() -> list[str]:
    """環境變數 NBA_FAVORITE_TEAMS(逗號分隔,如 'Celtics,Lakers')→ 小寫關鍵字清單。
    未設定回空 → NBA 維持只顯示冠軍賽(預設行為不變)。"""
    raw = os.getenv("NBA_FAVORITE_TEAMS", "").strip()
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def _nba_team_matches_favorite(competitor: dict, fav: str) -> bool:
    """球隊是否命中關注關鍵字。單字關鍵字用「整詞」比對(避免 'den' 誤中 'golden state');
    多字關鍵字(含空白,如 'golden state')才用整串子字串比對。"""
    tm = competitor.get("team") or {}
    text = " ".join(str(tm.get(k, "") or "") for k in (
        "displayName", "name", "location", "shortDisplayName")).lower()
    if " " in fav:
        return fav in text
    tokens = set(text.split())   # 整詞比對:'den' 不會誤中 'golden state warriors'
    tokens.add(str(tm.get("abbreviation", "") or "").lower())
    return fav in tokens


def fetch_nba_favorite_games(now_tpe: dt.datetime, favorites: list[str]) -> list[dict]:
    """關注球隊近 8 日最近一場(不限冠軍賽)。僅在設定 NBA_FAVORITE_TEAMS 時呼叫。
    隊名/比分皆 HTML 跳脫後才加 <b> 標記;同一場(兩支關注隊對戰)只列一次。"""
    import html as _h
    found_favs: set = set()
    games: list[dict] = []
    seen_games: set = set()

    def _name(t):
        nm = _h.escape(str((t.get("team") or {}).get("abbreviation", "?")))
        return f"<b>{nm}</b>" if t.get("winner") else nm

    def _sc(t):
        return _h.escape(str(t.get("score", "-")))

    for back in range(0, 8):
        if len(found_favs) >= len(favorites):
            break
        day = (now_tpe - dt.timedelta(days=back)).strftime("%Y%m%d")
        try:
            r = _http_get(
                "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
                params={"dates": day}, timeout=15)
            r.raise_for_status()
            events = r.json().get("events", [])
        except Exception as e:
            print(f"[sports] NBA 關注球隊抓取失敗({day}): {e}", file=sys.stderr)
            continue
        for ev in events:
            if not (((ev.get("status") or {}).get("type")) or {}).get("completed"):
                continue
            comp = (ev.get("competitions") or [{}])[0]
            tlist = comp.get("competitors", [])
            if len(tlist) != 2:
                continue
            matched = [f for f in favorites if f not in found_favs
                       and any(_nba_team_matches_favorite(t, f) for t in tlist)]
            if not matched:
                continue
            found_favs.update(matched)   # 已找到這些關注隊的最近一場
            gid = ev.get("id") or ev.get("uid")
            if gid in seen_games:
                continue                 # 兩支關注隊對戰 → 同一場只列一次
            seen_games.add(gid)
            away = next((t for t in tlist if t.get("homeAway") == "away"), tlist[0])
            home = next((t for t in tlist if t.get("homeAway") == "home"), tlist[1])
            games.append({
                "text": f"{_name(away)} {_sc(away)}:{_sc(home)} {_name(home)}",
                "date": f"{day[4:6]}/{day[6:]}",
                "note": ((comp.get("notes") or [{}])[0].get("headline") or "")[:40],
            })
    return games


def fetch_sports_digest(now_tpe: Optional[dt.datetime] = None) -> dict:
    """CPBL 戰績表/昨日比分 + NBA 冠軍賽 + MLB 戰績榜/台灣球員 + 世足 + 網球 + 體育新聞。

    使用者需求:中職要完整戰績表;NBA 要冠軍賽比分與系列賽狀態;不要 MLB 逐場比分;
    世足要最新/昨日戰績、分組表與今日賽程;加台灣旅外 MLB 球員與網球賽況。
    """
    now_tpe = now_tpe or dt.datetime.now(TPE)
    out: dict = {"news": {}}
    try:
        _cpbl_meta: dict = {}
        out["cpbl"] = fetch_cpbl_standings(_cpbl_meta)
        out["cpbl_source"] = _cpbl_meta.get("source")
    except Exception as e:
        print(f"[sports] CPBL 戰績抓取失敗: {e}", file=sys.stderr)
    # CPBL 昨日比分(Yahoo 運動,避開中職官網 geo-block)
    try:
        cs = fetch_cpbl_scores(now_tpe)
        if cs:
            out["cpbl_scores"] = cs
    except Exception as e:
        print(f"[sports] CPBL 比分抓取失敗: {e}", file=sys.stderr)
    try:
        cf = fetch_cpbl_today_fixtures(now_tpe)
        if cf:
            out["cpbl_fixtures"] = cf
    except Exception as e:
        print(f"[sports] CPBL 今日賽程抓取失敗: {e}", file=sys.stderr)
    # NBA:往回找最近一場(冠軍賽系列非每天打),取比分+系列賽戰況(如 NY leads 3-1)
    try:
        for back in range(1, 6):
            day = (now_tpe - dt.timedelta(days=back)).strftime("%Y%m%d")
            r = _http_get(
                "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
                params={"dates": day}, timeout=15)
            events = r.json().get("events", [])
            finals = []
            for e in events:
                comp = (e.get("competitions") or [{}])[0]
                teams = comp.get("competitors", [])
                if len(teams) != 2:
                    continue
                away = next((t for t in teams if t.get("homeAway") == "away"), teams[0])
                home = next((t for t in teams if t.get("homeAway") == "home"), teams[1])

                def _fmt(t):
                    name = (t.get("team") or {}).get("abbreviation", "?")
                    return f"<b>{name}</b>" if t.get("winner") else name
                series = (comp.get("series") or {}).get("summary", "")
                note = ((comp.get("notes") or [{}])[0].get("headline") or "")
                finals.append({
                    "text": f"{_fmt(away)} {away.get('score', '-')}:"
                            f"{home.get('score', '-')} {_fmt(home)}",
                    "series": series, "note": note[:50],
                    "date": f"{day[4:6]}/{day[6:]}",
                })
            if finals:
                out["nba"] = finals
                break
    except Exception as e:
        print(f"[sports] NBA 抓取失敗: {e}", file=sys.stderr)
    # NBA 關注球隊(opt-in;未設 NBA_FAVORITE_TEAMS 時不動作,維持只顯示冠軍賽)
    _nba_favs = _nba_favorite_teams()
    if _nba_favs:
        try:
            fav_games = fetch_nba_favorite_games(now_tpe, _nba_favs)
            if fav_games:
                out["nba_fav"] = fav_games
        except Exception as e:
            print(f"[sports] NBA 關注球隊整體失敗: {e}", file=sys.stderr)
    # 休賽季:無任何 NBA 賽果可顯示時,給階段說明(選秀/自由市場/休賽季),避免該區空轉
    if not out.get("nba") and not out.get("nba_fav"):
        _off = _nba_offseason_note(now_tpe)
        if _off:
            out["nba_offseason"] = _off
    # MLB 戰績榜(AL/NL 各前 5,含勝率;使用者要求完整戰績而非只有一行前三)
    _mlb_top_abbrs: set = set()
    try:
        r = _http_get("https://site.api.espn.com/apis/v2/sports/baseball/mlb/standings",
                         timeout=15)
        r.raise_for_status()
        standings = {}
        for league in r.json().get("children", []):
            name = "美聯" if "American" in str(league.get("name", "")) else "國聯"
            entries = (league.get("standings") or {}).get("entries", [])

            def _wins(en):
                stats = {s.get("name"): s for s in en.get("stats", [])}
                return float((stats.get("winPercent") or {}).get("value") or 0)
            top = sorted(entries, key=_wins, reverse=True)[:5]
            standings[name] = [
                {"team": (en.get("team") or {}).get("abbreviation", "?"),
                 "record": next((s.get("displayValue") for s in en.get("stats", [])
                                 if s.get("name") == "overall"), ""),
                 "pct": round(_wins(en), 3)}
                for en in top]
            _mlb_top_abbrs |= {t["team"] for t in standings[name][:3]}
        out["standings"] = standings
    except Exception as e:
        print(f"[sports] MLB 戰績抓取失敗: {e}", file=sys.stderr)
    # MLB 未來一週焦點賽程(強隊對戰;台北時間)——使用者要求 MLB 也要有賽程
    try:
        mf = fetch_mlb_week_fixtures(now_tpe, _mlb_top_abbrs)
        if mf:
            out["mlb_fixtures"] = mf
    except Exception as e:
        print(f"[sports] MLB 賽程抓取失敗: {e}", file=sys.stderr)
    # NBA 未來一週賽程(台北時間;休賽季自然為空,渲染端顯示休賽季說明)
    try:
        nf = fetch_nba_week_fixtures(now_tpe)
        if nf:
            out["nba_fixtures"] = nf
    except Exception as e:
        print(f"[sports] NBA 賽程抓取失敗: {e}", file=sys.stderr)
    # 世足(賽期內才有資料,非賽期回空,渲染端自動略過)。
    # knockout 必須列入條件:休賽日 results/fixtures 可能全空、groups 可能抓失敗,
    # 漏了會把整張淘汰賽對戰表丟掉(2026-07-14 自查)。
    try:
        wc = fetch_worldcup(now_tpe)
        if (wc.get("results") or wc.get("groups") or wc.get("fixtures")
                or wc.get("knockout")):
            out["worldcup"] = wc
    except Exception as e:
        print(f"[sports] 世足抓取失敗: {e}", file=sys.stderr)
    # 台灣旅外 MLB 球員昨日表現
    try:
        tw_mlb = fetch_mlb_taiwan_players(now_tpe)
        if tw_mlb:
            out["mlb_tw"] = tw_mlb
    except Exception as e:
        print(f"[sports] MLB 台灣球員抓取失敗: {e}", file=sys.stderr)
    # 網球 ATP/WTA 賽況
    try:
        tennis = fetch_tennis_digest(now_tpe)
        if tennis.get("tournaments") or tennis.get("results"):
            out["tennis"] = tennis
    except Exception as e:
        print(f"[sports] 網球抓取失敗: {e}", file=sys.stderr)

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=30)
    for label, query in SPORTS_NEWS_QUERIES:
        try:
            # when=2d:同在地快訊——伺服器端 1d 過濾會吃掉 24-30h 新聞,cutoff 才是精確閘
            feed = _feedparser_parse_url_with_timeout(_gnews_rss(query, when="2d"))
            titles = []
            for entry in feed.entries:
                if len(titles) >= 3:
                    break
                pub = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub and dt.datetime(*pub[:6], tzinfo=dt.timezone.utc) < cutoff:
                    continue
                # 保留原文連結(使用者要求 2026-07-14:標題做超連結,有興趣可點進去)
                titles.append({"title": str(entry.get("title", ""))[:90],
                               "link": str(entry.get("link", ""))})
            out["news"][label] = titles
        except Exception as e:
            print(f"[sports] {label} 新聞抓取失敗: {e}", file=sys.stderr)
    return out


PODCAST_DIGEST_FILE = Path("state/podcast_digest.json")
# Top5 波段觀察卡渲染開關:使用者 2026-07-15 要求刪除(長線大盤型為主);
# 排名/回測/state/prompt 素材照常運作,只關顯示。要復用改 True 即可。
_RENDER_TOP5_CARD = False
# 現行訂閱節目(2026-07-14 使用者拍板瘦身後)兼「顯示順序」:台灣節目優先、外國殿後。
# 同時是 load_podcast_digest 的白名單——已刪節目(科技報橘/美股投資學/財經一路發/
# WSJ What's News 等)的 state 殘留不再進信件。與 podcast_digest.PODCASTS 同步維護。
_PODCAST_DISPLAY_RANK: dict[str, int] = {name: i for i, name in enumerate([
    "股癌", "游庭皓的財經皓角", "財報狗", "M觀點", "財經M平方",
    "Wall Street Breakfast", "Odd Lots", "Sharp Tech (Ben Thompson)",
    "Money Talks (Economist)", "All-In Podcast", "BG2 Pod",
])}
# keep 模式超標時逐步壓「每集重點條數」的階梯(不丟任何一集)。
# 為何不改砍集數:load_podcast_digest 每節目最多取 2 集未顯示、且丟棄 >96h 的未顯示集,
# 而顯示順序固定(台灣節目優先)。若砍集數,排序靠後的節目(WSJ/Wall Street Breakfast…)
# 會永遠輪不到、96h 後直接過期 = 永久消失(Codex review 指出的「餓死」)。
# 壓條數則每集都在、都被正確標記已顯示,信也變小(2026-07-10 剪信事故)。
_PODCAST_KEEP_COMPACT_STEPS = (10, 8, 6, 4)


def _norm_podcast_point(s) -> str:
    """重點句正規化(去空白/標點/全形符號),供跨集去重比對。"""
    import re as _re
    return _re.sub(r"[\s，。、！？,.!?:：;；…()（）「」【】\"'`%　|｜]+", "", str(s)).lower()


def _podcast_bigrams(s) -> set:
    """中文無詞界,用字元 bigram 當 token 做近似比對。"""
    t = _norm_podcast_point(s)
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _overlap_coef(a: set, b: set) -> float:
    """重疊係數 |A∩B| / min(|A|,|B|);比 Jaccard 更能偵測『一方包含於另一方』(聯名特輯)。"""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _dedup_podcast_episodes(episodes: list[dict]) -> list[dict]:
    """跨節目/跨集去重(信件最大長度元兇)。同場聯名特輯被兩個獨立 feed 各收一次
    (內容是 LLM 各自改寫,逐字幾乎無重疊,但『標題』含同一特輯名)→ 用標題 bigram 重疊係數
    偵測為主、長集摘要 bigram 重疊係數為輔,整集略過。其餘集再以 difflib 模糊比對
    移除『與先前集近乎相同』的個別重點(移除後 <2 點則保留原樣,避免空集)。

    整集略過刻意只發生在『不同節目』之間:同一節目連續集(EP670/EP671、每日財經快訊)
    標題格式雷同但內容不同,不可用標題重疊互砍(同節目重貼已由 guid 去重)。標題路徑另要求
    共享 bigram 絕對量足夠(避免短標題以重疊係數誤判);摘要路徑要求兩集都夠長(各 ≥8 點),
    避免短集恰為長集子集時、以 min 分母把更豐富的長集吃掉。門檻偏保守,寧可漏去重也不誤砍。
    被略過的集未標 shown,其雙胞胎顯示後它隔日獨立出現已不重複。"""
    import difflib
    kept: list[dict] = []
    kept_meta: list[dict] = []   # {show, title_bg, sum_bg, npts}
    seen_points: list[str] = []
    for ep in episodes:
        d = ep.get("digest") or {}
        pts = [p for p in (d.get("summary_points") or []) if str(p).strip()]
        show = ep.get("show", "")
        title_bg = _podcast_bigrams(ep.get("title", ""))
        sum_bg = _podcast_bigrams("".join(str(p) for p in pts))
        npts = len(pts)
        is_dup = False
        for m in kept_meta:
            if m["show"] == show:
                continue   # 同節目連續集 → 不以標題/摘要重疊互砍
            shared_title = title_bg & m["title_bg"]
            if len(shared_title) >= 10 and _overlap_coef(title_bg, m["title_bg"]) >= 0.6:
                is_dup = True
                break
            if npts >= 8 and m["npts"] >= 8 and _overlap_coef(sum_bg, m["sum_bg"]) >= 0.65:
                is_dup = True
                break
        if is_dup:
            continue   # 整集近重複(跨節目聯名特輯重貼)→ 略過
        uniq = []
        for p in pts:
            npn = _norm_podcast_point(p)
            if npn and any(difflib.SequenceMatcher(None, npn, sp).ratio() >= 0.85
                           for sp in seen_points):
                continue   # 與先前集近乎相同的重點 → 略過
            uniq.append(p)
        if 2 <= len(uniq) < len(pts):
            ep = {**ep, "digest": {**d, "summary_points": uniq}}
        seen_points.extend(_norm_podcast_point(p) for p in pts if str(p).strip())
        kept.append(ep)
        kept_meta.append({"show": show, "title_bg": title_bg,
                          "sum_bg": sum_bg, "npts": npts})
    return kept


def _radar_processed_guids() -> set:
    """股癌雷達(gooaye_radar.py)已『獨立寄出』的股癌集 guid。雷達寫自己的 state/gooaye_radar.json
    (不碰 podcast_digest.json,避免兩 workflow 競寫),晨報讀它來去重:雷達已處理的股癌集,
    晨報 Podcast 段不再重複。讀檔失敗一律回空集(降級為不去重,最壞重複一次)。"""
    try:
        p = Path("state/gooaye_radar.json")
        if not p.exists():
            return set()
        data = json.loads(p.read_text(encoding="utf-8"))
        out = set()
        for show in (data or {}).values():
            if isinstance(show, dict):
                for ep in show.get("episodes") or []:
                    if ep.get("guid") and ep.get("radar_sent_at"):
                        out.add(str(ep["guid"]))
        return out
    except Exception:
        return set()


def load_podcast_digest(max_age_hours: int = 96) -> list[dict]:
    """讀 podcast_digest.py 產出的摘要,回「尚未在信件中顯示過」的近期集。

    每集只出現一次(使用者需求):寄信成功後 mark_podcast_episodes_shown 標記
    shown_at,之後的信不再重複;也因此每集可完整展開全部重點而不擔心信件過長。
    另:股癌若已由「股癌雷達」獨立信處理(radar_processed),晨報此處也跳過,避免兩封重複。
    """
    if not PODCAST_DIGEST_FILE.exists():
        return []
    try:
        data = json.loads(PODCAST_DIGEST_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[podcast] digest 讀取失敗: {e}", file=sys.stderr)
        return []
    out = []
    radar_guids = _radar_processed_guids()
    now = dt.datetime.now(dt.timezone.utc)
    for show in (data or {}).values():
        if not isinstance(show, dict):
            continue
        # 只載入「現行訂閱清單」的節目:已刪節目的 state 殘留 episodes(尚無 shown_at)
        # 不得再進信件,否則清單瘦身在下一封信不生效(Codex review)。
        # 白名單=display_order(見下),與 podcast_digest.PODCASTS 同步維護。
        if show.get("name") not in _PODCAST_DISPLAY_RANK:
            continue
        unshown_count = 0
        for ep in show.get("episodes") or []:
            if ep.get("shown_at") or (ep.get("guid") and str(ep["guid"]) in radar_guids):
                continue   # 已在先前信件顯示過、或已由股癌雷達獨立信寄出 → 不再重複
            try:
                ts = dt.datetime.strptime(
                    ep.get("processed_at", ""), "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=dt.timezone.utc)
            except Exception:
                continue
            if (now - ts).total_seconds() / 3600 <= max_age_hours:
                out.append({"show": show.get("name", ""), **ep})
                unshown_count += 1
                if unshown_count >= 2:
                    break
    out.sort(key=lambda e: _PODCAST_DISPLAY_RANK.get(e.get("show", ""), 99))
    return _dedup_podcast_episodes(out)   # 跨節目/跨集去重(聯名特輯/同事件重貼)


def mark_podcast_episodes_shown(episodes: list[dict]) -> None:
    """寄信成功後把本次顯示的集標記 shown_at,之後的信不再重複出現。
    寫回 PODCAST_DIGEST_FILE;晨報由 save_history_state、週日綜合由
    _git_commit_and_push_state 各自 git push 帶回 repo。"""
    if not episodes or not PODCAST_DIGEST_FILE.exists():
        return
    try:
        data = json.loads(PODCAST_DIGEST_FILE.read_text(encoding="utf-8"))
        shown_guids = {str(ep.get("guid")) for ep in episodes if ep.get("guid")}
        now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        marked = 0
        for show in (data or {}).values():
            if not isinstance(show, dict):
                continue
            for ep in show.get("episodes") or []:
                if str(ep.get("guid")) in shown_guids and not ep.get("shown_at"):
                    ep["shown_at"] = now_iso
                    marked += 1
        if marked:
            PODCAST_DIGEST_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[podcast] 已標記 {marked} 集為已顯示(下次信件不再重複)")
    except Exception as e:
        print(f"[podcast] 標記已顯示失敗(下封可能重複): {e}", file=sys.stderr)


def _cap_analysis_text(text: str, max_chars: int = 6000) -> str:
    """LLM 分析異常過長時在段落邊界截斷(保險絲,防模型跑飛)。

    2400→3200→3800:歷次擴充(其他類股 8 類、世界大事速覽)後調升,仍常截到十/十一。
    3800→6000(2026-07-14):使用者拍板「內容完整優先、接受 Gmail 摺疊」(尺寸守衛改
    full 模式)後,本上限不再為信件大小服務,只防 LLM 異常輸出;正常分析(~4-5k 字)
    應完整呈現,不再截斷十、總經/十一、台灣動態。"""
    if not text or len(text) <= max_chars:
        return text
    cut = text.rfind("\n\n", 0, max_chars)
    if cut < int(max_chars * 0.6):
        cut = text.rfind("\n", 0, max_chars)
    if cut < int(max_chars * 0.6):
        cut = max_chars
    out = text[:cut].rstrip()
    # 孤兒標題清理:切點若剛好落在某節標題之後,會留下「## 十、…」+截斷訊息的空殼
    # (07-13 信實見)。截斷後結尾若是標題行,連標題一起移除。
    lines = out.split("\n")
    while lines and lines[-1].lstrip().startswith("#"):
        lines.pop()
    # 截斷不再附註解文字(使用者要求 2026-07-14);截斷本身仍保留(防極端超長)
    return "\n".join(lines).rstrip()


def _estimated_email_kb(html: str) -> float:
    """估算 Gmail 是否會剪信用的大小(KB)。
    Gmail ~102KB 截斷量的是「解碼後的 HTML 內容」本身(Email on Acid 6000+ 封實測、
    Litmus、Mailchimp 一致),而非 base64 編碼後大小——base64 信反而更晚才剪(~110KB)。
    故直接量解碼後 UTF-8 大小即可,不可再 ×1.37(那會在離真正危險還有 ~30KB 餘裕時就誤判超標,
    把使用者要看的內容過早砍掉)。"""
    return len(html.encode("utf-8")) / 1024.0


# 超標時的預設犧牲優先序(先移除最前者;依使用者指定:政策→醫界→醫學文獻→五檔觀察,
# 其後才是低價值卡片,Podcast 與體育殿後、萬不得已才動)。
_TRUNCATE_SECTIONS = ("policy", "medical", "journals", "top5",
                      "event_timeline", "model_evidence", "sports", "podcast")
_TRUNCATE_LABELS = {"policy": "政府政策", "medical": "醫界", "journals": "醫學文獻",
                    "top5": "五檔觀察", "event_timeline": "事件連續劇",
                    "model_evidence": "模型實證", "sports": "體育", "podcast": "Podcast"}


def _truncate_order() -> list[str]:
    """整塊移除的優先序。可用環境變數 EMAIL_TRUNCATE_ORDER(逗號分隔 key)覆寫,
    例:'policy,medical,journals,top5,sports,podcast'。
    env 指定者優先,未列入者沿用預設相對順序接在後面(確保涵蓋全部區塊、不漏)。"""
    raw = os.environ.get("EMAIL_TRUNCATE_ORDER", "").strip()
    if not raw:
        return list(_TRUNCATE_SECTIONS)
    wanted: list[str] = []
    for k in raw.split(","):
        k = k.strip()
        if k in _TRUNCATE_LABELS and k not in wanted:
            wanted.append(k)
    return wanted + [k for k in _TRUNCATE_SECTIONS if k not in wanted]


# 敘述-數字交叉驗證:LLM 用「跳水/暴跌」「暴漲/飆漲」等戲劇性字眼形容某指標時,
# 核對該指標實際漲跌幅是否相符,攔截「VIX 22.2 跳水」這類數字幻覺(僅記錄不改稿)。
_DRAMA_DOWN = ("暴跌", "跳水", "重挫", "崩跌", "急殺", "崩盤", "大跌", "狂瀉", "雪崩")
_DRAMA_UP = ("暴漲", "飆漲", "狂飆", "大漲", "噴出", "狂噴", "急拉")
_MACRO_ALIASES = {
    "VIX": ("vix", "恐慌指數"),
    "SOX": ("費半", "費城半導體", "sox"),
    "QQQ": ("那斯達克", "nasdaq", "qqq"),
    "NQ": ("那指期", "nq"),
    "WTI": ("油價", "原油", "wti", "西德州"),
    "10Y": ("十年期", "10年期", "美債殖利率", "10y"),
}


def _audit_dramatic_macro_claims(analysis: str, macro: dict, threshold: float = 1.0) -> list[str]:
    """掃描分析文,對「指標 + 戲劇性漲跌詞」核對實際 change_pct;不符則回報(供記錄,不改稿)。
    只檢查本報有資料的總經指標;找不到對應指標就跳過,降低誤報。"""
    if not isinstance(analysis, str) or not isinstance(macro, dict):
        return []
    low = analysis.lower()
    alias_to_key = [(a.lower(), key) for key, aliases in _MACRO_ALIASES.items() for a in aliases]

    import re as _re

    def _nearest_indicator(pos: int) -> Optional[str]:
        window = low[max(0, pos - 16):pos]   # 指標名通常緊鄰在戲劇詞之前
        # 只看同一子句:截到最後一個標點之後,避免跨句誤掛(「VIX變動不大。台股暴跌」)
        window = _re.split(r"[。!?！？.,，;;；、\n]", window)[-1]
        best, best_i = None, -1
        for a, key in alias_to_key:
            i = window.rfind(a)
            if i > best_i:
                best_i, best = i, key
        return best

    flags: list[str] = []
    for words, expect_down in ((_DRAMA_DOWN, True), (_DRAMA_UP, False)):
        for w in words:
            start = 0
            while True:
                pos = analysis.find(w, start)
                if pos < 0:
                    break
                start = pos + len(w)
                key = _nearest_indicator(pos)
                if not key:
                    continue
                cp = _safe_number((macro.get(key) or {}).get("change_pct"))
                if cp is None:
                    flags.append(f"{key}「{w}」但無漲跌資料")
                elif expect_down and cp >= -abs(threshold):
                    flags.append(f"{key}「{w}」但 change_pct={cp:+.2f}%(未明顯下跌)")
                elif (not expect_down) and cp <= abs(threshold):
                    flags.append(f"{key}「{w}」但 change_pct={cp:+.2f}%(未明顯上漲)")
    return flags


def _sector_rotation(snapshot: list, min_members: int = 3, top_n: int = 4) -> dict:
    """從 universe snapshot 聚合各類股近 5 日中位漲幅,算相對大盤的資金輪動方向。
    借鏡 daily_stock_analysis 的 sector rotation;純聚合既有 industry+pct_5d,無新增抓取。
    回 {"market_median", "strong":[(類股,中位%,相對大盤%,檔數)...], "weak":[...]}(類股不足則回 {})。"""
    def _med(xs: list) -> float:
        sv = sorted(xs)
        n = len(sv)
        return sv[n // 2] if n % 2 else (sv[n // 2 - 1] + sv[n // 2]) / 2
    by_ind: dict[str, list] = {}
    all_p5: list = []
    for e in snapshot:
        p5 = e.get("pct_5d")
        if isinstance(p5, (int, float)):
            ind = str(e.get("industry") or "").strip()
            if ind and ind != "未分類":
                by_ind.setdefault(ind, []).append(p5)
                all_p5.append(p5)
    if not all_p5:
        return {}
    mkt = _med(all_p5)
    ranked = [(ind, round(_med(xs), 2), round(_med(xs) - mkt, 2), len(xs))
              for ind, xs in by_ind.items() if len(xs) >= min_members]
    if len(ranked) < 3:                      # 類股太少不具輪動意義
        return {}
    ranked.sort(key=lambda r: r[1], reverse=True)
    weak = [r for r in ranked[::-1][:2] if r not in ranked[:top_n]]   # 最弱 2 類,排除與強勢重疊
    return {"market_median": round(mkt, 2), "strong": ranked[:top_n], "weak": weak}


def render_html(quotes: dict, fair: dict, predictions: dict, analysis: str,
                report_date: str, mode: str) -> str:
    import html as _htmllib   # 整個 render_html 共用：用於各段 user-supplied 字串 escape
    analysis_for_render = _strip_llm_watchlist_section(analysis)
    # 數字健全性最後防線:把 LLM 誤植的 2330「美元 ADR 價」改回新台幣中樞值
    analysis_for_render = _sanitize_llm_2330_prices(analysis_for_render, predictions)
    # 一般畸形數字(如「3,2424」逗號後 4+ 位)全文遮蔽——2330 專用修正管不到的其它段落(如科技脈動目標價)
    analysis_for_render = _mask_malformed_numbers(analysis_for_render)
    # 敘述-數字交叉驗證(僅記錄):戲劇性漲跌詞與實際幅度不符 → 印警告供監看
    try:
        _drama = _audit_dramatic_macro_claims(analysis_for_render, quotes.get("MACRO") or {})
        if _drama:
            print(f"[render] ⚠ 敘述-數字交叉驗證:{'; '.join(_drama[:6])}", file=sys.stderr)
    except Exception as _e:
        print(f"[render] 敘述-數字交叉驗證略過: {_e}", file=sys.stderr)
    stance = _extract_stance(analysis_for_render)
    # LLM 未產出可解析的立場(輸出不完整/格式變異)時,用 Python 訊號共識保底,頂部不顯示「—」
    if stance.get("score") is None and not stance.get("label"):
        stance = _fallback_stance_from_signals(quotes) or stance
    summary_text = _extract_summary(analysis_for_render)
    # 抽完立場/淨分後,再把 11 維計算行自顯示移除(計算仍要求 LLM 輸出以保品質)
    analysis_for_render = _strip_stance_calculation(analysis_for_render)
    # 十二(立場敘述/價位/操作/風險)上移到頂端結論卡,body 中移除十二、十三避免重複
    stance_detail = _extract_stance_section(analysis_for_render)
    analysis_for_render = _strip_llm_sections(
        analysis_for_render, ("我的明確立場", "一句話總結"))
    tw_intelligence_html = _render_tw_intelligence_html(
        quotes.get("TW_DAILY_INTELLIGENCE") or {}, _htmllib)
    # 渲染「全部」載入的集數(不設武斷上限):load_podcast_digest 已限制每節目最多 2 集未顯示,
    # 若這裡再砍集數,排序靠後的節目會永遠輪不到、96h 後過期消失(Codex review)。
    # 超標時改由下方 keep/trim 分支「先壓條數、必要時才減集數並同步下修 shown 數」處理。
    _pod_eps_init = quotes.get("PODCAST_DIGEST") or []
    podcast_html = _render_podcast_html(
        _pod_eps_init, quotes.get("TW_UNIVERSE_SNAPSHOT") or [], _htmllib,
        max_episodes=max(1, len(_pod_eps_init)))
    weather_html = _render_weather_html(quotes.get("WEATHER") or [],
                                        quotes.get("SUSPENSION_NEWS") or [])
    local_news_html = _render_local_news_html(quotes.get("LOCAL_NEWS") or {})
    ma200_html = _render_ma200_html(quotes.get("MA200_STATUS") or {})
    # G1 持倉曝險卡:使用者要求刪除(2026-07-15,上線一天後);引擎與測試保留,
    # main() 已不再計算 PORTFOLIO_RISK(節省 ~秒級 yfinance 抓取)。
    portfolio_risk_html = ""
    sports_html = _render_sports_html(quotes.get("SPORTS") or {}, _htmllib)
    event_calendar_html = _render_event_calendar_html(quotes.get("EVENT_CALENDAR") or [])
    event_timeline_html = _render_event_timeline_html(
        quotes.get("EVENT_TIMELINE") or [], _htmllib)
    tw_calendar_html = _render_tw_calendar_html(quotes.get("TW_CALENDAR") or {})
    journals_html = _render_journals_html(quotes.get("MEDICAL_JOURNALS") or [], _htmllib)
    weekly_recap_html = (_render_weekly_recap_html(quotes.get("HISTORY") or [])
                         if "週末" in str(mode) else "")
    model_evidence_html = _render_model_evidence_html(quotes)

    # ===== 1. 行情表格 =====
    def fmt_quote(q: dict) -> str:
        # 手機版 3 欄(標的/收盤/漲跌):iPhone Gmail 寬度 ~390px,高低與量在小螢幕沒人看
        if "error" in q:
            return (f"<tr><td style='padding:10px 14px;border-bottom:1px solid #e2e8f0;'>{q['ticker']}</td>"
                    f"<td colspan='2' style='padding:10px 14px;border-bottom:1px solid #e2e8f0;color:#dc2626'>{q['error']}</td></tr>")
        pct = q.get("change_pct") or 0
        # 台股慣例：紅漲綠跌
        color = "#dc2626" if pct >= 0 else "#16a34a"
        sign = "+" if pct >= 0 else ""
        return (
            f"<tr>"
            f"<td style='padding:12px 14px;border-bottom:1px solid #e2e8f0;font-weight:700;color:#0f172a;font-size:15px;'>{q['ticker']}</td>"
            f"<td style='padding:12px 14px;border-bottom:1px solid #e2e8f0;text-align:right;font-variant-numeric:tabular-nums;font-size:15px;'>{q['close']:.2f}</td>"
            f"<td style='padding:12px 14px;border-bottom:1px solid #e2e8f0;text-align:right;color:{color};font-weight:700;font-size:15px;'>{sign}{pct}%</td>"
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
                bg = "#dcfce7"
                tcolor = "#15803d"  # 低位（綠）
            elif rank > 70:
                bg = "#fee2e2"
                tcolor = "#b91c1c"  # 高位（紅）
            else:
                bg = "#f1f5f9"
                tcolor = "#475569"  # 中位
            rank_cell = (f"<span style='background:{bg};color:{tcolor};"
                          f"padding:2px 8px;border-radius:10px;font-size:12px;font-weight:700;'>"
                          f"{rank:.0f}%</span>")
        # 手機版:判讀提示改放第二列小字(原本獨立欄在 390px 寬擠掉數字欄)
        return (f"<tr>"
                f"<td style='padding:10px 14px 2px;font-weight:700;color:#0f172a;font-size:14px;'>{label}</td>"
                f"<td style='padding:10px 14px 2px;text-align:right;font-variant-numeric:tabular-nums;font-size:14px;'>{m['close']:,.2f}</td>"
                f"<td style='padding:10px 14px 2px;text-align:right;color:{color};font-weight:700;font-size:14px;'>{sign}{pct:.2f}%</td>"
                f"<td style='padding:10px 14px 2px;text-align:center;'>{rank_cell}</td>"
                f"</tr>"
                f"<tr><td colspan='4' style='padding:0 14px 8px;border-bottom:1px solid #e2e8f0;"
                f"color:#94a3b8;font-size:12px;'>{hint}</td></tr>")
    # 信件只顯示「一般投資人看得懂」的指標;艱澀的 VIX9D / NQ・ES 期貨 / 10Y・13W 殖利率
    # 已從 email 移除,但仍在 MACRO dict + LLM prompt 內(後台保留餵立場評分與模型,品質不降)。
    macro_rows = (
        fmt_macro_row("VIX 恐慌指數", "VIX", "<15樂觀 / >25恐慌") +
        fmt_macro_row("SOX 費半指數", "SOX", "美國半導體,與台積電連動最高") +
        fmt_macro_row("DXY 美元指數", "DXY", "升→外資易匯出、台股偏壓") +
        fmt_macro_row("日經 225", "N225", "亞股開盤情緒參考") +
        fmt_macro_row("上證綜指", "SSE", "中國盤面→台股資金面") +
        fmt_macro_row("WTI 原油", "WTI", "通膨/地緣風險定價") +
        fmt_macro_row("黃金", "GOLD", "避險情緒,漲多代表避險升溫") +
        fmt_macro_row("BTC 比特幣", "BTC", "風險偏好溫度計,24h 交易") +
        fmt_macro_row("銅期貨", "COPPER", "景氣領先指標,與台股出口連動")
    )
    # 美債利率環境:白話結論(隱藏殖利率曲線/倒掛術語,只給結果)。跨兩欄放表末。
    _yc = _yield_curve_read(macro)
    if _yc.get("detail"):
        _yc_color = {"warn": "#b91c1c", "caution": "#a16207", "normal": "#475569"}.get(
            _yc.get("flag"), "#475569")
        macro_rows += (
            f"<tr><td colspan='4' style='padding:10px 14px;border-bottom:1px solid #e2e8f0;'>"
            f"<span style='font-weight:700;color:#0f172a;font-size:13px;'>美債利率環境　</span>"
            f"<span style='color:{_yc_color};font-size:13px;'>{_yc['detail']}</span></td></tr>")
    # === 外資台指期未平倉區塊:使用者要求隱藏。===
    #     TAIFEX_OI 資料仍計算並用於 conflict-shrink / 開盤預測的後台判定,只是不再單獨渲染本區塊。
    taifex_html = ""

    # === SEC 8-K 公告區塊（只顯示「重點科技股」白名單:美股前 10 大市值 + 關鍵半導體 + 台積電）===
    sec_filings = quotes.get("SEC_FILINGS", []) or []
    # 過濾:只留 priority(消費/零售/工業雜訊不顯示);舊資料無 priority 欄位時退化為全顯示
    sec_priority = [f for f in sec_filings if f.get("priority")]
    if not sec_priority and sec_filings and not any("priority" in f for f in sec_filings):
        sec_priority = sec_filings    # 向後相容:state 來的舊 filing 沒有 priority 欄
    _sec_html = ""
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
        _sec_html = f"""
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

    # === 台股重點公司 MOPS 重大訊息:使用者要求刪除本區塊(2026-07-14)。===
    #     TW_MOPS 資料仍抓取並餵 LLM prompt/事件抽取(公告內容經常成為科技脈動/事件連續劇
    #     的素材),只是不再於信中渲染原始表格;約省 2KB 還給 Podcast(102KB 天花板下互擠)。

    # === 市場警告 Banner:使用者要求隱藏(費半急跌/外資台指期淨空等)。===
    #     ALERTS 資料仍計算並用於下方「開盤預測」的操作紀律判定,只是不再單獨渲染警告區塊。
    alerts_html = ""

    # === 加權指數預測卡 (Task A) ===
    taiex_pred = quotes.get("TAIEX_PRED", {}) or {}
    taiex_html = ""
    # 夜盤台指期列:依使用者要求(2026-07-14)併入「五、加權指數開盤預測」表格,
    # 不再獨立成卡(第五段缺席的降級運行才退回獨立卡,見下方夜盤區)。
    _night_for_taiex = quotes.get("NIGHT_TXF", {}) or {}
    night_row_html = ""
    if _night_for_taiex.get("night_pct") is not None:
        _np = _night_for_taiex["night_pct"]
        _nc = "#dc2626" if _np >= 0 else "#16a34a"
        _ns = "+" if _np >= 0 else ""
        night_row_html = f"""
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">夜盤台指期（{_night_for_taiex.get('date','—')}）</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-variant-numeric:tabular-nums;">{_night_for_taiex.get('night_close')} <b style="color:{_nc};">({_ns}{_np}%)</b></td>
          </tr>"""
    if taiex_pred.get("pred_open"):
        # 使用者回饋:SOX/TSM/夜盤個別訊號屬內部計算,信件只顯示最終預測結果
        signal_rows = ""
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
        # 使用者要求:開盤預測卡移除「今日立場/短線開盤/操作紀律」整段,以及「區間方法」「已自我校正」
        # 註腳(相關判定改放後台);卡片只保留 昨收/預測漲跌/預測開盤/合理區間/訊號共識。
        taiex_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">五、加權指數開盤預測</h2>
        <table style="width:100%;border-collapse:collapse;margin:12px 0;background:#f8fafc;border-radius:8px;overflow:hidden;">
          {signal_rows}
        </table>
        <table style="width:100%;border-collapse:collapse;margin:12px 0;">
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;width:55%;">加權昨收</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;font-variant-numeric:tabular-nums;">{taiex_pred['last_close']}</td>
          </tr>{night_row_html}
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
        """

    # === 0050 ETF 開盤預測卡 ===
    tw0050p_data = quotes.get("TW0050_PRED", {}) or {}
    _tw0050_card_html = ""
    if tw0050p_data.get("pred_open") and tw0050p_data.get("last"):
        p50 = tw0050p_data["pred_open"]
        l50 = tw0050p_data["last"]
        pct50 = ((p50 / l50) - 1) * 100
        c50 = "#dc2626" if pct50 >= 0 else "#16a34a"
        s50 = "+" if pct50 >= 0 else ""
        _tw0050_card_html = f"""
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
        <p style="font-size:12px;color:#94a3b8;margin:6px 0;">預測方法：{tw0050p_data.get('method','—')}（0050 約 50% 為 2330）</p>
        """

    # === 台股客觀關注排名 Top 5（固定公式分項 + 可回測價格預測）===
    # 使用者要求刪除本卡(2026-07-15:「都買大盤市值型放長線」);排名計分/每日回測/
    # state/prompt 的 Top5 素材**全部保留**(僅信件不渲染),旗標關閉、日後一行復用。
    smart_money_html = ""
    universe_snapshot = quotes.get("TW_UNIVERSE_SNAPSHOT", []) or []
    if universe_snapshot and _RENDER_TOP5_CARD:
        scored = _rank_attention_candidates(universe_snapshot)
        top5 = scored[:5]
        if top5:
            # FinMind 補值(EPS年增/外資持股)已於 main 抓取階段併入 snapshot;render 只讀不抓(避免寄信前 live HTTP)
            rows_html = []
            for rank, s in enumerate(top5, 1):
                sm = s.get("smart_money") or {}
                score = s.get("ranking_score", s.get(
                    "attention_score", (s.get("breakout") or {}).get("score", 0)))
                ranking_components = s.get("ranking_components") or {}
                tags = sm.get("tags", []) or []
                if score >= 80:
                    score_bg, score_fg = "#fee2e2", "#b91c1c"   # 紅:強訊號
                elif score >= 60:
                    score_bg, score_fg = "#fef3c7", "#92400e"   # 橘:站隊
                else:
                    score_bg, score_fg = "#dbeafe", "#1e40af"   # 藍:輕微
                tag_chips = "".join(
                    f'<span style="display:inline-block;background:#f1f5f9;color:#475569;'
                    f'padding:1px 7px;border-radius:8px;font-size:12px;margin:0 3px 3px 0;">'
                    f'{_htmllib.escape(str(t))}</span>'
                    for t in tags[:6]
                )
                tag_chips_line = tag_chips or '<span style="color:#94a3b8;font-size:12px;">無特別標籤</span>'
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
                _ranking_line = (
                    f"客觀排名 #{rank} ・ 結構 {ranking_components.get('structure', 0):+.1f}"
                    f" ・ 新聞 {ranking_components.get('news_event', 0):+.1f}"
                    f" ・ 產業中性 {ranking_components.get('industry_neutral', 0):+.1f}"
                    f" ・ 勝過大盤 {ranking_components.get('beat_market', 0):+.1f}"
                    f" ・ 預期報酬 {ranking_components.get('expected_return', 0):+.1f}"
                    f" ・ 品質 {ranking_components.get('quality_penalty', 0):+.1f}"
                    f" ・ 流動性 {ranking_components.get('liquidity_penalty', 0):+.1f}"
                    f" ・ 漂移 {ranking_components.get('feature_drift_penalty', 0):+.1f}"
                    f" ・ 來源 {ranking_components.get('source_health_penalty', 0):+.1f}"
                    f" ・ 校準 {ranking_components.get('model_monitor_penalty', 0):+.1f}"
                    f" ・ 過熱 {ranking_components.get('overheat_penalty', 0):+.1f}")
                forecast = s.get("price_forecast") or {}
                f3 = forecast.get("3d") or {}
                f5 = forecast.get("5d") or {}
                quality = f3.get("quality") or {}
                hit_pct = quality.get("recent_direction_hit_pct")
                hit_text = f"{hit_pct}%" if hit_pct is not None else "—"
                _quality_line = (
                    f"模型 {quality.get('model_version', MODEL_VERSION)}"
                    f" ・ 樣本 {quality.get('training_rows', 0)}"
                    f" ・ 近期方向命中 {hit_text}"
                    f" ・ 單邊滑價估計 {s.get('slippage_bps', '—')} bps"
                    f" ・ {'fallback' if quality.get('fallback_enabled', True) else quality.get('interval_method', 'model')}"
                )
                # 回測:隔日~數日幾乎無預測力(IC≈0)→ 不再顯示「隔日」噪音價,只留 3/5 日參考區間
                # 並標短期信心低;真正略有訊號在 ~月線(見區塊說明的波段框架)。
                forecast_line = (
                    f"短期參考(信心低): 3日 {f3.get('expected_price','—')} "
                    f"({f3.get('lower','—')}~{f3.get('upper','—')})"
                    f" ・ 5日 {f5.get('expected_price','—')} ({f5.get('lower','—')}~{f5.get('upper','—')})"
                    f" ・ 信心 {forecast.get('confidence','低')}")

                # 基本面/估值/籌碼擴充欄(取得到才顯示;與股癌雷達同口徑,純參考、不計入排名分數)
                def _num(v):
                    return v if isinstance(v, (int, float)) else None
                # 金融業(金控/銀行/保險/證券):營收YoY 易受合併/利差扭曲、Piotroski/Altman/Beneish 皆不適用。
                # 多源判斷:產業別中文名 / TWSE 產業代碼 17(金融保險)/ 公司簡介(fallback universe 的 industry 可能為空)
                _ind = str(s.get("industry", "")).strip()
                _desc = str(s.get("desc", ""))
                is_fin = (_ind == "17"
                          or any(k in _ind for k in ("金融", "保險", "金控", "銀行", "證券", "票券"))
                          or any(k in _desc for k in ("金控", "銀行", "保險", "證券", "票券", "壽險", "產險")))
                fund_bits = []
                ry = _num(s.get("rev_yoy_pct"))
                if ry is not None and not is_fin:
                    fund_bits.append(f"營收YoY {ry:+.0f}%")
                _mm = "／".join(x for x in [
                    f"毛利 {s['gross_margin']:.0f}%" if _num(s.get("gross_margin")) is not None else None,
                    f"營益 {s['op_margin']:.0f}%" if _num(s.get("op_margin")) is not None else None,
                    f"淨利 {s['net_margin']:.0f}%" if _num(s.get("net_margin")) is not None else None] if x)
                if _mm:
                    fund_bits.append(_mm)
                eps_v, eg = _num(s.get("eps")), _num(s.get("eps_yoy_pct"))
                if eps_v is not None:
                    fund_bits.append(f"EPS {eps_v}" + (f"(年增 {eg:+.0f}%)" if eg is not None else ""))
                roe_q = _num(s.get("roe_q"))
                if roe_q is not None:
                    fund_bits.append(f"單季ROE {roe_q:.1f}%")
                fund_line = ("基本面: " + " ・ ".join(fund_bits)) if fund_bits else ""
                val_bits = []
                for label, key, suf in (("PER", "per", ""), ("殖利率", "yield_pct", "%"), ("PBR", "pbr", "")):
                    v = _num(s.get(key))
                    if v is not None:
                        # PER 極高(>100)多為轉機/獲利剛回升,標註避免誤讀為「貴」
                        tag = "(極高·轉機股留意)" if key == "per" and v >= 100 else ""
                        val_bits.append(f"{label} {v:.1f}{suf}{tag}")
                mc = _num(s.get("market_cap"))
                if mc:
                    val_bits.append(f"市值 {mc / 1e8:,.0f}億")
                # DCF 內在價值 gap(移植 ai-hedge-fund 估值法;持續經營口徑,保守參考非精算)
                dcfg, dcfz = _num(s.get("val_dcf_gap_pct")), s.get("val_dcf_zone")
                if dcfg is not None and dcfz:
                    # gap 極端值(|.|>200%)夾住顯示,避免 +353% 這類誇張數字傷可信度
                    _dcf = (">+200%" if dcfg > 200 else ("<-200%" if dcfg < -200 else f"{dcfg:+.0f}%"))
                    val_bits.append(f"DCF {dcfz}({_dcf})")
                val_line = ("估值: " + " ・ ".join(val_bits)) if val_bits else ""
                chip2_bits = []
                mh, fhp = _num(s.get("major_holder_pct")), _num(s.get("foreign_hold_pct"))
                f30, mbl, scr = _num(s.get("foreign_30d_lot")), _num(s.get("margin_balance_lot")), _num(s.get("short_cover_ratio"))
                if mh is not None:
                    chip2_bits.append(f"大戶持股 {mh:.0f}%")
                if fhp is not None:
                    chip2_bits.append(f"外資持股 {fhp:.0f}%")
                if f30 is not None:
                    chip2_bits.append(f"外資30日 {int(f30):+,}張")
                if mbl:
                    chip2_bits.append(f"融資餘額 {int(mbl):,}張")
                if scr is not None:
                    chip2_bits.append(f"空方回補 {scr}")
                chip2_line = ("籌碼: " + " ・ ".join(chip2_bits)) if chip2_bits else ""
                # 財報品質(Piotroski F-score + Altman Z-score 破產 + Beneish M-score 盈餘操弄;
                # FinMind 三表、純參考不計分)。金融業不適用此三法 → 不算不顯示,改標「不適用」。
                fz_bits = []
                if not is_fin:
                    fsc, fden = _num(s.get("fscore")), _num(s.get("fscore_denom"))
                    # 可得準則太少(分母 <5,多為資料缺漏)→ F-score 不可信,不顯示(避免誤標體質弱)
                    if fsc is not None and (not fden or fden >= 5):
                        grade = "強健" if fsc >= 7 else ("中等" if fsc >= 4 else "體質弱")
                        fz_bits.append(f"F-score {int(fsc)}/{int(fden) if fden else 9}({grade})")
                    zsc = _num(s.get("zscore"))
                    if zsc is not None:
                        fz_bits.append(f"Z-score {zsc}({s.get('zscore_zone', '')})")
                    msc = _num(s.get("mscore"))
                    if msc is not None:
                        # Beneish M-score 對「高成長/剛轉機」股天然誤報(高營收/EPS 成長長得像美化帳)→ 軟化措辭
                        if not s.get("mscore_flag"):
                            mtag = "正常"
                        elif (isinstance(eg, (int, float)) and eg > 100) or (isinstance(ry, (int, float)) and ry > 50):
                            mtag = "偏高(高成長股常見誤報)"
                        else:
                            mtag = "⚠留意操弄"
                        fz_bits.append(f"M-score {msc}({mtag})")
                fz_line = ("財報品質: " + " ・ ".join(fz_bits)) if fz_bits else (
                    "財報品質: 金融業·F/Z/M-score 不適用" if is_fin else "")
                ext_html = "".join(
                    f"<div style='margin-top:4px;font-size:12px;color:#475569;'>{_htmllib.escape(x)}</div>"
                    for x in (fund_line, val_line, chip2_line, fz_line) if x)
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
                    f"<div style='margin-top:5px;font-size:12px;color:#94a3b8;'>{metrics_line}</div>"
                    # 基本面/估值/籌碼擴充(與股癌雷達同口徑,純參考、不計入排名分數)
                    f"{ext_html}"
                    # 排名分解(ranking_line)與模型技術行(quality_line)屬內部計算細節,
                    # 使用者回饋不需顯示 — 隱藏(資料仍在 state/log 供除錯)
                    f"<div style='margin-top:5px;font-size:12px;color:#0369a1;'>{forecast_line}</div>"
                    f"</td>"
                    f"</tr>"
                )
            top_score = max(_safe_number(item.get("ranking_score", item.get("attention_score")))
                            for item in top5)
            # 熔斷橫幅文字已依使用者要求(2026-07-14)移除;熔斷「機制」不變:
            # suppress_ranking 時 ML 組件仍自排名移除(計分層,見 MODEL_MONITORING),只是不再顯示紅字說明。
            low_confidence_note = ""
            # 使用者要求刪除「為何大漲日也都是觀察」說明段(門檻說明已足夠精簡於圖例)。
            title_text = (
                f"台股波段觀察名單 Top {len(top5)}（中長線・相對排名）"
                if top_score < 60
                else f"台股客觀關注排名 Top {len(top5)}（由高至低）"
            )
            # 資金輪動(借鏡 daily_stock_analysis sector rotation):各類股近 5 日中位漲幅 vs 大盤
            sector_rotation_html = ""
            _rot = _sector_rotation(universe_snapshot)
            if _rot:
                def _rot_chip(item):
                    ind, med, rel, _n = item
                    col = "#dc2626" if med >= 0 else "#16a34a"   # 台股慣例:紅漲綠跌
                    return (f'<span style="display:inline-block;background:#fff;border:1px solid #fcd9b6;'
                            f'color:{col};padding:2px 8px;border-radius:8px;font-size:12px;margin:0 4px 4px 0;">'
                            f'{_htmllib.escape(ind)} {med:+.1f}%'
                            f'<span style="color:#94a3b8;"> (相對{rel:+.1f})</span></span>')
                strong_chips = "".join(_rot_chip(it) for it in _rot["strong"])
                weak_chips = "".join(_rot_chip(it) for it in _rot["weak"])
                weak_part = (f'<div style="margin-top:4px;"><span style="font-size:12px;color:#64748b;">轉弱：</span>'
                             f'{weak_chips}</div>' if weak_chips else "")
                sector_rotation_html = (
                    f'<div style="margin:4px 0 14px;padding:10px 12px;background:#fffbeb;border-radius:8px;">'
                    f'<div style="font-size:13px;font-weight:600;color:#92400e;margin-bottom:6px;">'
                    f'近 5 日資金輪動（類股中位漲幅，大盤中位 {_rot["market_median"]:+.1f}%）</div>'
                    f'<div>{strong_chips}</div>{weak_part}'
                    f'<div style="font-size:11px;color:#94a3b8;margin-top:6px;">'
                    f'※ 各類股成分股近 5 日漲幅中位數；「相對」為減去全市場中位數（&gt;0＝資金相對流入）。純參考、非買賣訊號。</div>'
                    f'</div>')
            smart_money_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#fff7ed;border-left:5px solid #ea580c;border-radius:4px;">{title_text}</h2>
        {sector_rotation_html}
        {low_confidence_note}
        <table role="presentation" style="width:100%;border-collapse:collapse;margin:12px 0;">
          {''.join(rows_html)}
        </table>
        <p style="font-size:12px;color:#94a3b8;margin:6px 0;line-height:1.6;">
          ※ 分數 <b>≥80 強關注(紅)</b>、≥60 中度關注(橘)、其餘為觀察(藍)。
          大戶 ΔWoW = 大戶持股比例週變化;量比20d = 今日量 / 近 20 日均量(&lt; 0.8 量縮、&gt; 1.5 放量)。<br>
          ※ 排名由固定公式產生並每日回測驗證;此分數僅供觀察參考，不是買進訊號。
        </p>
        """

    # === 大盤成交額 + 市場廣度卡 ===
    breadth = quotes.get("BREADTH", {}) or {}
    breadth_html = ""
    if breadth.get("total"):
        adv = breadth.get("advance", 0)
        dec = breadth.get("decline", 0)
        unch = breadth.get("unchanged", 0)
        adv_ratio = breadth.get("advance_ratio", 0)
        # 顏色：上漲多 = 紅 (台股慣例); 下跌多 = 綠
        if adv_ratio >= 60:
            b_color, b_label = "#dc2626", "普漲（強勢）"
        elif adv_ratio <= 40:
            b_color, b_label = "#16a34a", "普跌（弱勢）"
        elif 45 <= adv_ratio <= 55:
            b_color, b_label = "#64748b", "多空均衡"
        else:
            b_color, b_label = "#a16207", "窄幅（少數股撐盤）"
        # 標示資料所屬 session:颱風臨時休市/連假後「昨日」其實是數天前的收盤,不標會誤導
        _lts = str(quotes.get("LAST_TRADING_SESSION") or "")
        _lts_label = f"(上一交易日 {_lts[5:].replace('-', '/')} 收盤)" if len(_lts) == 10 else ""
        breadth_html = f"""
        <div style="background:#f1f5f9;border-radius:10px;padding:14px 18px;margin:12px 0;">
          <div style="font-size:13px;color:#475569;font-weight:700;margin-bottom:6px;">大盤成交額與市場廣度{_lts_label}</div>
          <div style="font-size:14px;color:#0f172a;line-height:1.7;">
            成交金額 <b>{breadth.get('total_value_yi',0):,.0f} 億</b>　｜
            上漲 <b style="color:#dc2626;">{adv}</b> 檔・下跌 <b style="color:#16a34a;">{dec}</b> 檔・平盤 {unch} 檔　|
            上漲佔比 <b style="color:{b_color};">{adv_ratio:.1f}%</b>
            <span style="font-size:12px;color:{b_color};margin-left:8px;">（{b_label}）</span>
          </div>
        </div>
        """
        # 類股熱度(前 5 熱門產業):附掛在廣度卡內。「九、其他類股」的行情觀察條目引用
        # 此表 → 讀者看得到出處(先前 LLM 引 [類股熱度表] 但信裡沒有,形同隱形來源)。
        # 純渲染 quotes["SECTOR_HEAT"](main 已算好,零網路);無資料自動略過。
        _heat = quotes.get("SECTOR_HEAT") or {}
        _hsec, _hrank = _heat.get("sectors") or {}, _heat.get("ranked") or []
        if _hsec and _hrank:
            _hrows = []
            for _hn in _hrank[:5]:
                _hs = _hsec.get(_hn) or {}
                _hc = "#dc2626" if _hs.get("median_pct", 0) > 0 else (
                    "#16a34a" if _hs.get("median_pct", 0) < 0 else "#64748b")
                _hlead = "、".join(
                    f"{m['code']} {m['name']} {m['pct']:+.1f}%"
                    for m in (_hs.get("leaders") or [])[:2])
                _hrows.append(
                    f"<div style='font-size:12px;color:#334155;line-height:1.8;'>"
                    f"<b>{_hn}</b>　成交 {_hs.get('value_yi', 0):,.0f} 億"
                    f"({_hs.get('value_share_pct', 0):.1f}%)・中位 "
                    f"<b style='color:{_hc};'>{_hs.get('median_pct', 0):+.1f}%</b>"
                    f"　領先:{_hlead or '-'}</div>")
            breadth_html += f"""
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px 18px;margin:12px 0;">
          <div style="font-size:13px;color:#475569;font-weight:700;margin-bottom:4px;">類股熱度表（依成交值前 5;全市場口徑）</div>
          {''.join(_hrows)}
        </div>
        """
    # 台股估值溫度(A4)+ 選擇權磁吸參考(A5)兩張卡:使用者要求刪除(2026-07-15)。
    # VALUATION/TXO_MAGNET 資料仍照抓並餵 LLM prompt 當背景(見 _build_prompt macro_block),
    # 僅信件不再顯示。

    # === 中期展望:使用者要求刪除整段(改以「長線趨勢參考」MA200 卡為準)。===
    #     MIDTERM 仍於 main 計算並存於 quotes 供後台,只是不再於信中渲染。
    midterm_html = ""

    # === 夜盤台指期卡 (Task B) ===
    # 夜盤已併入「五、加權指數開盤預測」表格(使用者要求 2026-07-14);
    # 僅在第五段缺席(加權預測失敗的降級運行)才退回獨立卡,避免資料遺失。
    night = quotes.get("NIGHT_TXF", {}) or {}
    night_html = ""
    if night.get("night_pct") is not None and not taiex_pred.get("pred_open"):
        n_pct = night["night_pct"]
        n_color = "#dc2626" if n_pct >= 0 else "#16a34a"
        n_sign = "+" if n_pct >= 0 else ""
        night_html = f"""
        <div style="background:#f1f5f9;border-radius:10px;padding:14px 18px;margin:12px 0;">
          <div style="font-size:13px;color:#475569;font-weight:700;margin-bottom:6px;">夜盤台指期（{night.get('date','—')}）</div>
          <div style="font-size:16px;color:#0f172a;">
            夜盤收 {night.get('night_close')}
            <span style="color:{n_color};font-weight:700;margin-left:8px;">({n_sign}{n_pct}%)</span>
            {f'<span style="font-size:12px;color:#94a3b8;margin-left:8px;">日盤收 {night.get("day_close")}（僅診斷,正價差/除息日勿直接相比）</span>' if night.get("day_close") is not None else ''}
          </div>
        </div>
        """
    # 台指期與現貨價差(純事實,不下情緒結論;獨立於夜盤資料是否存在)
    night_html += _basis_line_html(quotes.get("TAIFEX_BASIS") or {})

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
          </tr>
          {macro_rows}
        </table>
        """
    # G3 世界證據門檻警示(平日空字串;異常時掛總經卡下方一則白話)
    world_evidence_html = _render_world_evidence_html(
        _world_evidence_signals(quotes.get("MACRO") or {}, quotes.get("SPY") or {}))

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
                pp_color = "#dc2626"
                pp_label = "溢價"          # 偏貴
            elif pp < -0.5:
                pp_color = "#16a34a"
                pp_label = "折價"          # 偏便宜
            else:
                pp_color = "#64748b"
                pp_label = "接近合理"
            pp_sign = "+" if pp >= 0 else ""
            premium_row = f"""
          <tr><td colspan="2" style="height:4px;"></td></tr>
          <tr>
            <td style="padding:10px 14px;background:#f8fafc;color:#475569;">折溢價（vs NDX 隱含 NAV，60 日基準）</td>
            <td style="padding:10px 14px;background:#f8fafc;text-align:right;color:{pp_color};font-weight:700;font-variant-numeric:tabular-nums;">{pp_sign}{pp:.2f}% <span style="font-weight:500;font-size:12px;color:{pp_color};">({pp_label})</span></td>
          </tr>"""

        method_label = fair.get("method", "")
        calib_extra = _calibration_note_compact(fair)
        fair_foot = (f'<p style="font-size:12px;color:#94a3b8;margin:6px 0;">'
                     f'計算方式：{method_label}'
                     + (f'　｜　{calib_extra}' if calib_extra else '')
                     + '</p>')

        _fair_html = f"""
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
        _fair_html = f"<p style='color:#dc2626'>{fair.get('error','資料缺失')}</p>"

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
            models_label = (f"四模型估值<br><span style=\"color:#94a3b8;font-size:12px;\">"
                            f"1:1 / 60日比值 / ADR衰減{decay} / 5日動能 {mom_str} ×0.15</span>")
        else:
            models_compact = f"{_fmt(m1)} / {_fmt(m2)} / {_fmt(m3)}"
            models_label = (f"三模型估值<br><span style=\"color:#94a3b8;font-size:12px;\">"
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
            wf_line = (f'<p style="font-size:12px;color:#94a3b8;margin:6px 0;">'
                       f'{"　｜　".join(notes)}</p>')
        _pred_html = (f'<table style="width:100%;border-collapse:collapse;margin:12px 0;">'
                      f'{rows_html}</table>{wf_line}')
    else:
        _pred_html = f"<p style='color:#dc2626'>{predictions.get('error','資料缺失')}</p>"

    # ===== 3.4 預測準確度回顧區塊 =====
    backtest_text = (quotes.get("BACKTEST") or "").strip()
    _backtest_html = ""
    if backtest_text:
        _backtest_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">預測準確度回顧</h2>
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:14px 16px;margin:12px 0;font-size:12px;line-height:1.75;color:#475569;white-space:pre-wrap;font-family:'Consolas','Menlo','Courier New',monospace;">{_htmllib.escape(backtest_text)}</div>
        <p style="font-size:12px;color:#94a3b8;margin:4px 0;">※ 比對「當日預測 vs 隔日實際開盤」。平均誤差為正＝預測偏低、為負＝預測偏高；此誤差會回饋進隔日的自我校正。</p>
        """

    # ===== 3.5 資料品質區塊 =====
    dq_list = quotes.get("DATA_QUALITY", []) or []
    _dq_html = ""
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
        summary = "全部正常" if (n_err == 0 and n_fb == 0) else f"{n_err} 項失敗、{n_fb} 項降級"
        _dq_html = f"""
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
    kpi_strip = _render_kpi_strip(quotes, fair, predictions, stance)
    summary_bar = _render_summary_bar(summary_text, stance_detail, _htmllib)

    # ===== 4. LLM 分析（Markdown → HTML 後加樣式;過長先在段落邊界截斷） =====
    analysis_for_render = _cap_analysis_text(analysis_for_render)
    analysis_html = _md_to_html(analysis_for_render)
    analysis_html = _style_analysis_html(analysis_html)
    analysis_html = _wrap_stance(analysis_html)
    # (llm_label 已隨信尾三行移除而不再需要,2026-07-14)

    # === 個股開盤預測(2330 / 00662 / 0050 三合一精簡表,置於加權預測下方)===
    # 取代原本分散的三、四、六大卡;頭部 KPI 已有頭條數字,這裡給昨收/預測/幅度即可。
    def _pred_row(label: str, last_v, pred_v, pct_v, note: str = "") -> str:
        if last_v is None or pred_v is None:
            return (f"<tr><td style='padding:9px 12px;border-bottom:1px solid #e2e8f0;font-weight:700;color:#0f172a;'>{label}</td>"
                    f"<td colspan='3' style='padding:9px 12px;border-bottom:1px solid #e2e8f0;color:#dc2626;font-size:13px;'>資料缺失</td></tr>")
        pc = "#dc2626" if (pct_v or 0) >= 0 else "#16a34a"
        sg = "+" if (pct_v or 0) >= 0 else ""
        return (f"<tr>"
                f"<td style='padding:9px 12px;border-bottom:1px solid #e2e8f0;font-weight:700;color:#0f172a;'>{label}</td>"
                f"<td style='padding:9px 12px;border-bottom:1px solid #e2e8f0;text-align:right;color:#64748b;font-variant-numeric:tabular-nums;'>{last_v}</td>"
                f"<td style='padding:9px 12px;border-bottom:1px solid #e2e8f0;text-align:right;font-weight:700;color:#0f172a;font-variant-numeric:tabular-nums;'>{pred_v}</td>"
                f"<td style='padding:9px 12px;border-bottom:1px solid #e2e8f0;text-align:right;font-weight:700;color:{pc};font-variant-numeric:tabular-nums;'>{sg}{pct_v:.2f}%</td>"
                f"</tr>")

    _p_mid = predictions.get("mid") if isinstance(predictions, dict) else None
    _p_last = predictions.get("last_2330") if isinstance(predictions, dict) else None
    _p_pct = ((_p_mid / _p_last - 1) * 100) if (_p_mid and _p_last) else None
    _f_price = fair.get("fair_price") if isinstance(fair, dict) else None
    _f_last = fair.get("last_00662_price") if isinstance(fair, dict) else None
    _f_pct = fair.get("implied_change_pct") if isinstance(fair, dict) else None
    _tw = quotes.get("TW0050_PRED", {}) or {}
    _t_pred = _tw.get("pred_open")
    _t_last = _tw.get("last")
    _t_pct = ((_t_pred / _t_last - 1) * 100) if (_t_pred and _t_last) else None
    combined_pred_html = f"""
        <h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">六、個股開盤預測與公允價（2330 / 0050 開盤;00662 公允價）</h2>
        <table style="width:100%;border-collapse:collapse;margin:12px 0;font-size:14px;">
          <tr style="background:#f1f5f9;">
            <th style="padding:8px 12px;text-align:left;color:#475569;font-size:12px;">標的</th>
            <th style="padding:8px 12px;text-align:right;color:#475569;font-size:12px;">昨收</th>
            <th style="padding:8px 12px;text-align:right;color:#475569;font-size:12px;">預測開盤／公允價</th>
            <th style="padding:8px 12px;text-align:right;color:#475569;font-size:12px;">預估漲跌</th>
          </tr>
          {_pred_row("2330 台積電", _p_last, _p_mid, _p_pct)}
          {_pred_row("00662 富邦NASDAQ 公允價", _f_last, _f_price, _f_pct)}
          {_pred_row("0050 元大台灣50", _t_last, _t_pred, _t_pct)}
        </table>
        {_render_etf_action_card(_f_price, _t_pred)}
        """

    truncation_notice = ""

    # 收件匣預覽文字(preheader):Gmail/iOS 主旨後那行灰字。不設則抓到信首(天氣/MARKET BRIEF 等
    # 雜訊)。放當日最重要數字,一眼可判今日盤勢。隱私:僅公開預測(加權/2330/0050/00662),絕無持股。
    def _ph_num(v, nd=0):
        return f"{v:,.{nd}f}" if isinstance(v, (int, float)) else None
    _taiex_ph = (quotes.get("TAIEX_PRED") or {}).get("pred_open")
    _ph_bits = [b for b in (
        (f"加權預估 {_ph_num(_taiex_ph)}" if _ph_num(_taiex_ph) else None),
        (f"2330 {_ph_num(_p_mid)}" if _ph_num(_p_mid) else None),
        (f"0050 {_ph_num(_t_pred, 2)}" if _ph_num(_t_pred, 2) else None),
        (f"00662公允 {_ph_num(_f_price, 2)}" if _ph_num(_f_price, 2) else None),
        (f"立場{stance.get('label')}" if stance.get("label") else None),
    ) if b]
    preheader = _htmllib.escape("　".join(_ph_bits) or f"美股晨報 {report_date}")

    def _assemble() -> str:
        return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>美股晨報 {report_date}</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang TC','Microsoft JhengHei',sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:#f1f5f9;opacity:0;">{preheader}</div>
  <table role="presentation" style="width:100%;border-collapse:collapse;background:#f1f5f9;">
    <tr>
      <td align="center" style="padding:12px 4px;">
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

          <!-- BODY(手機版兩側 16px:28px 在 390px 寬會吃掉 15% 可用寬度)-->
          <tr><td style="padding:20px 16px 8px;">

            {truncation_notice}

            {weather_html}

            {alerts_html}

            {event_calendar_html}

            {event_timeline_html}

            {weekly_recap_html}

            <h2 style="color:#0f172a;font-size:20px;margin:0 0 12px;padding:8px 14px;background:#e0f2fe;border-left:5px solid #0284c7;border-radius:4px;">一、美股收盤行情</h2>
            <table style="width:100%;border-collapse:collapse;margin:12px 0;font-size:14px;">
              <tr style="background:#f1f5f9;">
                <th style="padding:10px 14px;text-align:left;color:#475569;font-size:12px;letter-spacing:1px;">標的</th>
                <th style="padding:10px 14px;text-align:right;color:#475569;font-size:12px;letter-spacing:1px;">收盤</th>
                <th style="padding:10px 14px;text-align:right;color:#475569;font-size:12px;letter-spacing:1px;">漲跌</th>
              </tr>
              {quote_rows}
            </table>

            {macro_table_html}

            {world_evidence_html}

            {taiex_html}

            {combined_pred_html}

            {portfolio_risk_html}

            {ma200_html}

            {tw_calendar_html}

            {breadth_html}

            {midterm_html}

            <div style="margin-top:32px;">{analysis_html}</div>

            {podcast_html}

            {model_evidence_html}

            {night_html}

            {taifex_html}

            {sports_html}

            {smart_money_html}

            {local_news_html}

            {tw_intelligence_html}

            {journals_html}

          </td></tr>

          <!-- FOOTER:免責/來源/產生方式三行依使用者要求(2026-07-14)移除,僅留收尾邊框 -->
          <tr>
            <td style="padding:10px 28px;background:#f8fafc;border-top:1px solid #e2e8f0;"></td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    # === Gmail ~102KB 剪裁防護 ===
    # Gmail 量的是「解碼後 HTML」大小(~102KB;base64 信更晚才剪),_estimated_email_kb 已直接量解碼後大小。
    # 預設 full 模式(使用者 2026-07-14 拍板:「信件超過大小沒關係,接受被折疊,手動打開就好」):
    #   完全不壓縮、不減集、不移除任何區塊——內容完整優先,超過 102KB 由 Gmail 摺疊、
    #   使用者點「查看整封郵件」展開。07-13/14 的教訓:keep 模式的減集把 10 集 Podcast
    #   擠到剩 1 集,比摺疊更傷。
    # keep 模式(EMAIL_OVERFLOW_MODE=keep):不移除區塊,但逐步壓 Podcast 條數/集數到 95KB 內。
    # trim 模式(EMAIL_OVERFLOW_MODE=trim):依優先序局部縮減 + 整塊移除,完全避免摺疊;
    #   犧牲序可由 EMAIL_TRUNCATE_ORDER 覆寫。
    # 三模式下行情表/2330·00662·0050 預測卡/結論永不被移除。門檻 95KB:對 ~102KB 真實線留安全邊際。
    LIMIT_KB = 95.0
    overflow_mode = os.environ.get("EMAIL_OVERFLOW_MODE", "full").strip().lower()
    intel_data = quotes.get("TW_DAILY_INTELLIGENCE") or {}
    inc_policy = inc_medical = True
    podcast_eps = quotes.get("PODCAST_DIGEST") or []
    pod_snapshot = quotes.get("TW_UNIVERSE_SNAPSHOT") or []
    # 追蹤「實際出現在信中的 Podcast 集數」:局部縮減/整塊移除後,只有真正顯示的集才該被
    # 標成已顯示(否則被砍掉的集會被誤標 shown、永遠不再出現 —— 曾導致整日 Podcast 消失)。
    podcast_shown_n = len(podcast_eps) if podcast_html else 0
    html = _assemble()
    dropped: list[str] = []
    reduced = False

    if overflow_mode == "trim":
        for key in _truncate_order():
            if _estimated_email_kb(html) <= LIMIT_KB:
                break
            label = _TRUNCATE_LABELS[key]
            if key == "podcast":
                # 不論集數多寡都先試局部縮減(少數但很長的集也能靠 compact_points 壓條數)。
                if podcast_html and podcast_eps:
                    for cap, pts in ((8, 8), (5, 6), (3, 5)):
                        if _estimated_email_kb(html) <= LIMIT_KB:
                            break
                        podcast_html = _render_podcast_html(podcast_eps, pod_snapshot, _htmllib,
                                                            max_episodes=cap, compact_points=pts)
                        podcast_shown_n = min(cap, len(podcast_eps))
                        reduced = True
                        html = _assemble()
                    if _estimated_email_kb(html) <= LIMIT_KB:
                        break
                was_present, podcast_html = bool(podcast_html), ""   # 縮到最小仍超標 → 整塊移除
                podcast_shown_n = 0
                if was_present:
                    dropped.append(label)
                html = _assemble()
                continue
            if key == "policy":
                was_present = bool(intel_data.get("policy"))
                inc_policy = False
                tw_intelligence_html = _render_tw_intelligence_html(
                    intel_data, _htmllib, inc_policy, inc_medical)
            elif key == "medical":
                was_present = bool(intel_data.get("medical"))
                inc_medical = False
                tw_intelligence_html = _render_tw_intelligence_html(
                    intel_data, _htmllib, inc_policy, inc_medical)
            elif key == "top5":
                was_present, smart_money_html = bool(smart_money_html), ""
            elif key == "sports":
                was_present, sports_html = bool(sports_html), ""
            elif key == "journals":
                was_present, journals_html = bool(journals_html), ""
            elif key == "model_evidence":
                was_present, model_evidence_html = bool(model_evidence_html), ""
            else:   # event_timeline
                was_present, event_timeline_html = bool(event_timeline_html), ""
            if was_present:
                dropped.append(label)
            html = _assemble()
    elif overflow_mode == "keep" and podcast_html and podcast_eps:
        # keep 模式:不省略任何區塊。超標時 (1) 先逐步壓「每集重點條數」(不丟集);
        # (2) 壓到最小仍超標,才作為最後手段逐步減少集數,並**同步下修 podcast_shown_n**
        #     → 未渲染的集不會被標記已顯示,隔天會再出現(不會「沒看到卻永久消失」)。
        # 減集數是最後手段而非首選:砍集數會讓固定顯示順序中排後面的節目長期輪不到
        # 而在 96h 後過期(Codex review 的「餓死」)。
        for _pts in _PODCAST_KEEP_COMPACT_STEPS:
            if _estimated_email_kb(html) <= LIMIT_KB:
                break
            podcast_html = _render_podcast_html(podcast_eps, pod_snapshot, _htmllib,
                                                max_episodes=podcast_shown_n,
                                                compact_points=_pts)
            html = _assemble()
        _pts_floor = _PODCAST_KEEP_COMPACT_STEPS[-1]
        # 一次只減 1 集:減 2 會在「3 集超標、2 集剛好塞得下」時直接跳到 1 集,
        # 多丟一集 → 反而加重它要防的餓死風險(Codex review)。
        while _estimated_email_kb(html) > LIMIT_KB and podcast_shown_n > 1:
            podcast_shown_n -= 1
            podcast_html = _render_podcast_html(podcast_eps, pod_snapshot, _htmllib,
                                                max_episodes=podcast_shown_n,
                                                compact_points=_pts_floor)
            html = _assemble()
        if _estimated_email_kb(html) > LIMIT_KB:
            print(f"[render] keep 模式已壓到 {podcast_shown_n} 集 × {_pts_floor} 條仍偏長",
                  file=sys.stderr)
    # 橫幅:trim 模式真的動了區塊 → 紅色「已暫略…」;否則(含 keep 模式)內容偏長 → 琥珀色提示可點開看全文。
    if dropped or (reduced and podcast_html):
        still_over = _estimated_email_kb(_assemble()) > LIMIT_KB  # 估含內容、未含橫幅
        tail = ";惟內容仍偏長,信末仍可能被 Gmail 截斷" if still_over else ""
        bits = []
        if reduced and podcast_html and "Podcast" not in dropped:
            bits.append("Podcast 已縮減集數")
        if dropped:
            bits.append("已暫略:" + "、".join(dropped))
        truncation_notice = (
            '<div style="margin:0 0 14px;padding:10px 14px;background:#fef2f2;'
            'border-left:5px solid #ef4444;border-radius:4px;font-size:12px;color:#7f1d1d;">'
            f'⚠ 為避免 Gmail 截斷,本期{";".join(bits)}'
            f'(行情、2330/00662/0050 預測與結論完整保留){tail}。</div>')
        html = _assemble()
    # 使用者要求移除「本期內容較長」琥珀提示(keep 模式下不再顯示任何提示橫幅)。
    final_kb = _estimated_email_kb(html)   # 含橫幅後的真實大小
    if final_kb > 102:
        print(f"[render] ⚠ 郵件約 {final_kb:.0f}KB,已逾 Gmail 102KB,信末可能被剪",
              file=sys.stderr)
    print(f"[render] 郵件約 {final_kb:.0f}KB(解碼後 HTML);"
          f"移除區塊={'、'.join(dropped) if dropped else '無'}", file=sys.stderr)
    # 回報實際顯示的 Podcast 集,供寄信後只標記這些為 shown(被砍/縮掉的集留待下次再出現)。
    quotes["PODCAST_SHOWN_EPISODES"] = podcast_eps[:podcast_shown_n]
    return html


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


EMAIL_ARCHIVE_DIR = Path("state/emails")


def _redact_private_for_archive(html: str) -> str:
    """存檔前移除 KPI 個人持股列(昨日帳上損益 % + NT$ 金額 + 持倉名稱)——敏感財務不落地 repo/RAG。
    第一層:以 render 端插入的 <!--PF_ROW_START/END--> 標記精準移除整列(持股資訊唯一出現處)。
    第二層(防禦縱深,Codex review):即使未來持股資訊漏到標記之外,也把使用者自訂的持倉名稱一併遮蔽。
    持股代號/股數本就從不進 HTML(僅彙總損益),故雙層後存檔不含任何可識別持股資訊。無持股設定時為 no-op。"""
    import re as _re
    out = _re.sub(r"<!--PF_ROW_START-->.*?<!--PF_ROW_END-->",
                  "<!--[持股列存檔時已去識別移除]-->", html, flags=_re.S)
    for name in (PORTFOLIO_1_NAME, PORTFOLIO_2_NAME):
        if name and name not in ("持倉1", "持倉2") and len(name) >= 2:
            out = out.replace(name, "持倉")
    return out


def archive_report_html(html: str, date_str: str, keep_days: int = 365) -> Optional[Path]:
    """把寄出的信件 HTML(去識別後)存成 state/emails/<date>.html.gz,供日後檢索/RAG。
    §B:先前 state 只存結構化數字,無法回溯「當天信實際說了什麼」。gzip 後每日 ~15-25KB、
    年約 6-9MB;保留近 keep_days 天,超過者刪除。任何失敗都不影響寄信(晨報不可斷)。"""
    import gzip
    import re as _re
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_str or "")):   # 檔名安全:僅收 YYYY-MM-DD
        print(f"[archive] 日期格式異常({date_str!r}),略過存檔", file=sys.stderr)
        return None
    try:
        EMAIL_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        out = EMAIL_ARCHIVE_DIR / f"{date_str}.html.gz"
        with gzip.open(out, "wt", encoding="utf-8") as f:
            f.write(_redact_private_for_archive(html))
        cutoff = (dt.datetime.now(TPE) - dt.timedelta(days=keep_days)).strftime("%Y-%m-%d")
        for p in EMAIL_ARCHIVE_DIR.glob("*.html.gz"):
            stem = p.name.split(".")[0]   # 只修剪合法日期檔名,異常檔名不動(Codex nit)
            if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", stem) and stem < cutoff:
                p.unlink()
        return out
    except Exception as e:
        print(f"[archive] 信件存檔略過(不影響寄信): {e}", file=sys.stderr)
        return None


def deliver_report(html: str, subject: str, state_entry: Optional[dict],
                   podcast_episodes: list[dict]) -> None:
    """Send first, then commit delivery state for at-least-once semantics."""
    send_email(html, subject)
    archive_report_html(
        html,
        (state_entry or {}).get("date") or dt.datetime.now(TPE).strftime("%Y-%m-%d"))
    persist_delivered_report_state(
        state_entry,
        podcast_episodes,
        mark_podcasts=True,
    )


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

    # P0-2 時間預算降級:本次跑因時間不足跳過的非核心步驟(供 LLM 知悉、資料品質透明)
    if _DEGRADED_STEPS:
        add("時間預算", "fallback",
            f"本期為確保準時寄出,已跳過:{'、'.join(dict.fromkeys(_DEGRADED_STEPS))}")

    # 2330 預測透明度:預測與昨收幾乎持平、但 TSM ADR 有明顯波動 → 提示留意
    # (可能是 bias 校正恰好抵銷 ADR 訊號,也可能是輸入新鮮度問題;2026-07-13 信
    #  出現連兩日 +0.00% 預測,事後查為颱風假 bar 污染校正樣本——此警示讓下次一眼看到)。
    try:
        _wf = (predictions or {}).get("weighted_final")
        _lc = (predictions or {}).get("last_2330")
        _tsm_chg = abs(float((quotes.get("TSM") or {}).get("change_pct") or 0))
        if _wf and _lc and abs(_wf / _lc - 1) < 0.0005 and _tsm_chg > 0.3:
            add("2330 預測", "fallback",
                f"預測與昨收持平但 TSM ADR 波動 {_tsm_chg:.2f}%——校正抵銷或輸入新鮮度,請留意")
    except (TypeError, ValueError):
        pass

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
    _fx_stale = quotes.get("USDTWD_STALE")
    if _fx_stale:
        add("USD/TWD 匯率", "fallback",
            f"即時抓取失敗,採 {_fx_stale.get('age_days')} 天前昨值 {_fx_stale.get('value')}")
    elif quotes.get("USDTWD") is not None:
        add("USD/TWD 匯率", "ok", str(quotes.get("USDTWD")))
    else:
        add("USD/TWD 匯率", "error", "TWD=X 抓取失敗")

    # 總經 + 國際指標 + 期貨/商品 (12 項)
    macro = quotes.get("MACRO", {}) or {}
    # VIX_TERM 是 derived,不算實際抓取項目;5Y/30Y 為選配(僅供美債利率白話卡),
    # MOVE/RSP 為 G3 世界證據選配(門檻式白話,平日不顯示)——
    # 抓不到不應把整個總經來源判成 fallback、誤入 LLM 資料品質區塊(Codex review / A2 教訓)。
    _MACRO_OPTIONAL = {"VIX_TERM", "5Y", "30Y", "MOVE", "RSP"}
    countable = {k: v for k, v in macro.items() if k not in _MACRO_OPTIONAL}
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

    # TSM ADR 新鮮度 sanity:SOX 大幅變動但 TSM ADR 幾乎不動 → 疑 ADR 報價未更新
    # (2026-07-10:SOX +3.06% 但 TSM ADR +0.00% → 2330 開盤預測被拉成 +0.00% 誤導)。
    # 門檻嚴(|SOX|≥2.5% 且 |TSM|<0.3%)避免誤報;僅記錄餵 LLM,不改任何預測/計分。
    _sox_pct = _safe_number((macro.get("SOX") or {}).get("change_pct"), None)
    _tsm_q = quotes.get("TSM") or {}
    _tsm_pct = None if _tsm_q.get("error") else _safe_number(_tsm_q.get("change_pct"), None)
    if (_sox_pct is not None and _tsm_pct is not None
            and abs(_sox_pct) >= 2.5 and abs(_tsm_pct) < 0.3):
        add("TSM ADR 新鮮度", "fallback",
            f"SOX {_sox_pct:+.2f}% 但 TSM ADR {_tsm_pct:+.2f}% 背離 → 疑報價未更新,2330 預測請留意")

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

    # 台股重點公司 MOPS 重大訊息（空清單 = 真無公告 OR 來源不可用，視為 fallback 不算 error）
    mops = quotes.get("TW_MOPS", []) or []
    if mops:
        add("MOPS 重大訊息 (重點公司)", "ok", f"{len(mops)} 筆")
    else:
        add("MOPS 重大訊息 (重點公司)", "fallback", "近 48h 無公告或來源暫不可用")

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

    backfill = quotes.get("MODEL_BACKFILL", {}) or {}
    if backfill.get("method") == "licensed_point_in_time_archive":
        add("模型歷史回填", "ok", f"{backfill.get('total_records', 0)} 個交易日・正式 point-in-time archive")
    elif backfill.get("total_records"):
        add("模型歷史回填", "fallback",
            f"{backfill.get('total_records', 0)} 個交易日・免費版市值使用目前發行股數估算")
    else:
        add("模型歷史回填", "fallback", "尚未累積歷史快照")

    drift = quotes.get("FEATURE_DRIFT", {}) or {}
    add("模型 feature drift", drift.get("status", "fallback"),
        f"penalty={drift.get('penalty', 0)}・alerts={len(drift.get('alerts') or [])}")

    source_health = quotes.get("SOURCE_HEALTH", {}) or {}
    _persist_fail = source_health.get("persistent_failures") or []
    add("模型來源健康度", source_health.get("status", "fallback"),
        f"score={source_health.get('score', 0)}・缺失={','.join(source_health.get('failures') or []) or '無'}"
        + (f"・⚠連續失敗:{','.join(_persist_fail)}" if _persist_fail else ""))
    awareness_failures = source_health.get("awareness_failures") or []
    add("台灣政策/醫界情報", source_health.get("awareness_status", "fallback"),
        f"awareness-only・缺失={','.join(awareness_failures) or '無'}")

    monitoring = quotes.get("MODEL_MONITORING", {}) or {}
    rolling = monitoring.get("rolling_origin_metrics") or {}
    if rolling:
        add("rolling-origin 回測", monitoring.get("status", "fallback"),
            f"origins={rolling.get('origins', 0)}・samples={rolling.get('samples', 0)}"
            f"・top5 net={rolling.get('top5_avg_net_return_pct')}"
            f"・ranking net={rolling.get('ranking_top5_avg_net_return_pct')}"
            f"・Brier={rolling.get('brier_score')}")

    model_monitoring = quotes.get("MODEL_MONITORING", {}) or {}
    monitor_metrics = model_monitoring.get("metrics") or {}
    add("模型機率校準監控", model_monitoring.get("status", "fallback"),
        f"Brier={monitor_metrics.get('brier_score')}・ECE={monitor_metrics.get('ece_pct')}%"
        f"・區間覆蓋={monitor_metrics.get('interval_coverage_pct')}%"
        f"・樣本={monitor_metrics.get('probability_samples', 0)}")

    n_consensus = sum(1 for item in (tw0050 or [])
                      if item.get("rev_expectation_method") == "external_consensus")
    n_proxy = sum(1 for item in (tw0050 or [])
                  if item.get("rev_expectation_method") == "cumulative_yoy_baseline")
    add("營收預期差", "ok" if n_consensus else "fallback",
        f"外部共識 {n_consensus} 檔・TWSE 趨勢 proxy {n_proxy} 檔")

    n_err = sum(1 for d in dq if d["status"] == "error")
    n_fb = sum(1 for d in dq if d["status"] == "fallback")
    print(f"[data_quality] {len(dq)} 項來源：ok={len(dq)-n_err-n_fb}, fallback={n_fb}, error={n_err}")
    return dq


def _published_within_hours(pub_str, hours: float = 30,
                            now_tpe: Optional[dt.datetime] = None) -> bool:
    """published 字串(台北時間,如 '2026-06-13 14:30')是否落在近 N 小時內。

    無法解析或過舊回 False(保守:不因無法判讀就誤判為新內容而重複寄信)。
    """
    if not pub_str:
        return False
    now_tpe = now_tpe or dt.datetime.now(TPE)
    s = str(pub_str).strip()
    parsed = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = dt.datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = dt.datetime.fromisoformat(s)
        except ValueError:
            return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TPE)
    age = (now_tpe - parsed).total_seconds()
    # 允許小幅未來偏差(時區/時鐘誤差),但拒絕過舊
    return -6 * 3600 <= age <= hours * 3600


def _weekend_digest_has_content(sports: dict, podcast_eps: list,
                                intel: dict, journals: list,
                                now_tpe: Optional[dt.datetime] = None) -> bool:
    """週日輕量信只在「週六信之後才新增」的內容時才寄(使用者需求:有新的才寄)。

    用時效判定「新」,而非只看「存在」,避免與週六信重複:
      - Podcast:load_podcast_digest 已以 shown_at 去重,回傳的即未顯示過的新集。
      - 世足/NBA/中職:使用者明確要求週日要看「昨日戰績」,故昨日/今日的賽果視為當日新內容
        (這是刻意的每日戰報,賽季中與週六信小幅重疊屬預期;NBA 回看 5 天的舊場次不算)。
      - 政策/醫界:只認 published 落在近 24 小時者 ≈「上一封信之後才出刊」,避開週六已看過的。
        (純覺察用途,不另建已送清單做精準去重;24h 邊界的排程抖動影響可忽略。)
      - 文獻(7 天窗)、純戰績表、一般體育新聞:僅作版面內容,不單獨觸發寄信。
    """
    now_tpe = now_tpe or dt.datetime.now(TPE)
    if podcast_eps:
        return True
    sports = sports or {}
    if (sports.get("worldcup") or {}).get("results"):
        return True
    fresh_dates = {(now_tpe - dt.timedelta(days=1)).strftime("%m/%d"),
                   now_tpe.strftime("%m/%d")}
    if any((g.get("date") in fresh_dates) for g in (sports.get("nba") or [])):
        return True
    if any((s.get("date") in fresh_dates) for s in (sports.get("cpbl_scores") or [])):
        return True
    intel = intel or {}
    for kind in ("policy", "medical"):
        for item in (intel.get(kind) or []):
            if _published_within_hours(item.get("published"), 24, now_tpe):
                return True
    return False


def render_weekend_digest_html(report_date: str, weather_html: str,
                               sports_html: str, podcast_html: str,
                               intel_html: str, journals_html: str,
                               calendar_html: str,
                               local_news_html: str = "") -> str:
    """週日綜合輕量信:天氣/在地快訊/體育/Podcast/政策/醫界/文獻,不跑行情與預測。"""
    body = "".join(s for s in (
        weather_html,
        '<div style="margin:8px 0 16px;padding:10px 14px;background:#f0fdf4;'
        'border-left:5px solid #16a34a;border-radius:4px;font-size:13px;color:#475569;">'
        '週日綜合:本日不開盤,僅彙整週末新增的體育戰績、Podcast、政策與醫界訊息。'
        '</div>',
        sports_html,
        podcast_html,
        local_news_html,
        intel_html,
        journals_html,
        calendar_html,
    ) if s)
    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>週末綜合 {report_date}</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang TC','Microsoft JhengHei',sans-serif;">
  <table role="presentation" style="width:100%;border-collapse:collapse;background:#f1f5f9;">
    <tr>
      <td align="center" style="padding:12px 4px;">
        <table role="presentation" style="max-width:680px;width:100%;border-collapse:collapse;background:#ffffff;border-radius:12px;box-shadow:0 4px 20px rgba(15,23,42,0.06);overflow:hidden;">
          <tr>
            <td style="background:linear-gradient(135deg,#065f46,#16a34a);padding:26px 28px 20px;color:#ffffff;">
              <div style="font-size:13px;letter-spacing:2px;opacity:0.85;margin-bottom:6px;">WEEKEND DIGEST</div>
              <h1 style="margin:0;font-size:26px;font-weight:700;color:#ffffff;line-height:1.3;">週日綜合</h1>
              <div style="margin-top:6px;font-size:15px;opacity:0.92;">{report_date} ・ <span style="background:rgba(255,255,255,0.18);padding:2px 10px;border-radius:12px;font-size:13px;">週日綜合</span></div>
            </td>
          </tr>
          <tr><td style="padding:20px 16px 8px;">
            {body}
          </td></tr>
          <tr>
            <td style="padding:10px 28px;background:#f8fafc;border-top:1px solid #e2e8f0;"></td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def run_weekend_digest(now_tpe: dt.datetime) -> int:
    """週日輕量綜合信:不跑行情/ML/預測,只抓週末新增的體育/Podcast/政策/醫界/文獻,
    且僅在有新內容時才寄信(使用者需求)。寄出後標記 podcast 已顯示並 push state。"""
    import html as _htmllib
    report_date = now_tpe.strftime("%Y-%m-%d (%a)")
    print(f"[weekend] 開始產生週日綜合 — {report_date}")

    try:
        weather = fetch_weather()
    except Exception as e:
        print(f"[weekend] 天氣抓取失敗: {e}", file=sys.stderr)
        weather = []
    try:
        sports = fetch_sports_digest(now_tpe)
    except Exception as e:
        print(f"[weekend] 體育抓取失敗: {e}", file=sys.stderr)
        sports = {}
    podcast_eps = load_podcast_digest()
    try:
        intel = fetch_tw_daily_intelligence(now_tpe)
    except Exception as e:
        print(f"[weekend] 政策/醫界抓取失敗: {e}", file=sys.stderr)
        intel = {}
    try:
        journals = translate_journal_titles(fetch_medical_journal_articles())
    except Exception as e:
        print(f"[weekend] 醫學文獻抓取失敗: {e}", file=sys.stderr)
        journals = []
    try:
        calendar = fetch_event_calendar(now_tpe)
    except Exception as e:
        print(f"[weekend] 風險事件日曆失敗: {e}", file=sys.stderr)
        calendar = []
    try:
        local_news = fetch_local_news(now_tpe)   # 在地快訊週日也要有(Codex review)
    except Exception as e:
        print(f"[weekend] 在地快訊抓取失敗: {e}", file=sys.stderr)
        local_news = {}
    try:
        suspension = fetch_suspension_news()     # 颱風停班停課(週日晚間公告週一)
    except Exception as e:
        print(f"[weekend] 停班停課新聞抓取失敗: {e}", file=sys.stderr)
        suspension = []

    if not _weekend_digest_has_content(sports, podcast_eps, intel, journals, now_tpe):
        print("[weekend] 無新增體育/Podcast/政策/醫界內容 → 本週日不寄信")
        return 0

    weather_html = _render_weather_html(weather or [], suspension or [])
    sports_html = _render_sports_html(sports or {}, _htmllib)
    # 與平日報對稱:渲染「全部」載入的集,再把「這些」集標成已顯示(見下方 deliver_report)。
    # 若沿用 renderer 預設 14 集上限卻對 deliver_report 傳入完整 podcast_eps,第 15 集起會被
    # 誤標 shown 卻從未出現在信中;週末信每週僅一次、集在 96h 內過期,等於永久遺失(Codex review)。
    podcast_html = _render_podcast_html(podcast_eps, [], _htmllib,
                                        max_episodes=max(1, len(podcast_eps)))
    intel_html = _render_tw_intelligence_html(intel or {}, _htmllib)
    journals_html = _render_journals_html(journals or [], _htmllib)
    calendar_html = _render_event_calendar_html(calendar or [])
    local_news_html = _render_local_news_html(local_news or {})
    html = render_weekend_digest_html(
        report_date, weather_html, sports_html, podcast_html,
        intel_html, journals_html, calendar_html,
        local_news_html=local_news_html)

    if os.environ.get("DRY_RUN") == "1":
        # 同時寫入晨報慣用的預覽路徑,讓 CI 的 dry-run-preview artifact 在週日也抓得到。
        for out in ("/tmp/morning_report_preview.html",
                    "/tmp/weekend_digest_preview.html"):
            with open(out, "w", encoding="utf-8") as f:
                f.write(html)
        print("[weekend] DRY_RUN — 預覽寫入 /tmp/morning_report_preview.html"
              "(同時 /tmp/weekend_digest_preview.html)")
        return 0

    subject = f"📰 週日綜合 {report_date} | 體育 / Podcast / 政策 / 醫界"
    # 寄信成功後才標記 podcast 已顯示(避免漏寄)。週日不寫入預測歷史:weekend 筆記的
    # target_session_date 會指向週一,與週六晨報的「週一預測」撞號,save_history_state
    # 去重時會誤刪週六的真實預測紀錄。因此這裡 entry=None,只單獨 push podcast 狀態檔。
    deliver_report(html, subject, None, podcast_eps)
    _git_commit_and_push_state(
        [str(PODCAST_DIGEST_FILE), str(EMAIL_ARCHIVE_DIR)],   # §B:週末信件存檔一併 push
        f"chore: weekend podcast state {now_tpe.strftime('%Y-%m-%d')} [skip ci]")
    print("[weekend] 週日綜合已寄出")
    return 0


# ---------- 主流程 ----------
def main() -> int:
    global _RUN_DEADLINE
    _RUN_DEADLINE = time.monotonic() + RUN_BUDGET_SECONDS   # P0-2 保命 deadline
    _DEGRADED_STEPS.clear()
    now_tpe = dt.datetime.now(TPE)
    # 週日(台北)走輕量綜合信:不開盤,只在有新增體育/Podcast/政策/醫界時才寄。
    if now_tpe.weekday() == 6:
        return run_weekend_digest(now_tpe)
    mode = determine_mode(now_tpe)
    report_date = now_tpe.strftime("%Y-%m-%d (%a)")
    target_session_date = _infer_target_session_date(now_tpe.strftime("%Y-%m-%d"))
    target_session_day = dt.datetime.strptime(target_session_date, "%Y-%m-%d").date()

    print(f"[main] 開始產生 {mode} 報告 — {report_date}")
    _RUN_MANIFEST["marks"].clear()
    _mark_phase("行情/總經/FX")

    # 1. 抓行情
    quotes = {
        "QQQ": fetch_quote("QQQ"),
        "TSM": fetch_quote("TSM"),
        "SPY": fetch_quote("SPY"),
    }
    usdtwd_today, usdtwd_prev = fetch_usdtwd_pair()
    quotes["USDTWD"] = usdtwd_today
    quotes["USDTWD_prev"] = usdtwd_prev
    if usdtwd_today is None:
        # 即時抓取失敗 → 昨值降級「只寫進 quotes['USDTWD'] 供顯示/prompt/資料品質」。
        # 絕不改 local usdtwd_today —— 它之後餵 calc_00662_fair_value 與 calc_2330_predictions;
        # 保持它=None,預測看到的 usdtwd 就與「本功能加入前」完全相同(00662 把 FX 變動視為 0、
        # 2330 排除 model2 改用 model1/3/4),即維持系統原有的降級輸出,對計分零改變。
        # (刻意不讓預測「完全停掉」——那是改預測行為 + 違反晨報不可斷;昨值也不進預測避免用舊 FX 汙染。)
        _stale_fx = _last_known_usdtwd(now_tpe=now_tpe)
        if _stale_fx:
            quotes["USDTWD"] = _stale_fx["value"]
            quotes["USDTWD_STALE"] = _stale_fx
            print(f"[main] USD/TWD 即時抓取失敗 → 昨值 {_stale_fx['value']}"
                  f"({_stale_fx['date']},{_stale_fx['age_days']}天前)僅供顯示,預測維持 fail-closed",
                  file=sys.stderr)

    # 1.5 抓 4+1 個總經指標
    print("[main] 抓總經指標…")
    macro = fetch_macro_indicators()
    quotes["MACRO"] = macro
    # 分析師評等動能(借鏡 yfinance upgrades_downgrades;ADR/美股,前瞻共識轉向)。fail-safe 回 {}
    quotes["ANALYST_MOMENTUM"] = fetch_analyst_rating_momentum()

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

    # 3.5 預測目標交易日的 corporate actions 必須在模型前載入。
    # 若最後才硬扣配息，pred_pct / bias / state 會互相不一致。
    public_codes = ["2330", "0050", "00662"]
    try:
        ex_div = detect_ex_dividend_today(public_codes, target_session_day)
    except Exception as e:
        print(f"[main] 除息偵測失敗(不影響晨報): {e}", file=sys.stderr)
        ex_div = {}
    quotes["EX_DIV_TODAY"] = ex_div

    # 4. 計算（升級版：NAV + 折溢價 + 匯率變動 + ADR 衰減）
    #    QQQ / TSM 任一抓取失敗時走降級：回傳 error dict，render_html 會顯示「資料缺失」而非整包爆掉。
    qqq_q = require_quote(quotes, "QQQ")
    tsm_q = require_quote(quotes, "TSM")
    if qqq_q is not None:
        fair = calc_00662_fair_value(
            qqq_q["close"], qqq_q["prev_close"],
            usdtwd_today, last_00662, usdtwd_prev=usdtwd_prev,
            ex_div_amt=ex_div.get("00662", 0.0),
        )
    else:
        fair = {"error": "QQQ 行情抓取失敗，無法估算 00662 合理價"}
        print("[main] QQQ 行情缺失 → 00662 估值降級", file=sys.stderr)
    if tsm_q is not None:
        predictions = calc_2330_predictions(
            tsm_q["close"], tsm_q["prev_close"],
            usdtwd_today, hist_2330, ex_div_amt=ex_div.get("2330", 0.0),
        )
    else:
        predictions = {"error": "TSM ADR 行情抓取失敗，無法預測 2330 開盤價"}
        print("[main] TSM 行情缺失 → 2330 預測降級", file=sys.stderr)

    # 5. 抓新聞
    _mark_phase("新聞+政策+體育")
    print("[main] 抓新聞中…")
    news = fetch_news()
    print(f"[main] 抓到 {len(news)} 則新聞")
    print("[main] 整理台灣政策與醫界昨日走向…")
    quotes["TW_DAILY_INTELLIGENCE"] = fetch_tw_daily_intelligence(now_tpe)
    # Podcast 摘要由獨立排程(podcast-digest.yml)預先產生,這裡只讀檔,失敗不影響晨報
    quotes["PODCAST_DIGEST"] = load_podcast_digest()
    if quotes["PODCAST_DIGEST"]:
        print(f"[main] 載入 {len(quotes['PODCAST_DIGEST'])} 集 podcast 摘要")
    print("[main] 抓天氣與體育快訊…")
    try:
        quotes["WEATHER"] = fetch_weather()
        quotes["LOCAL_NEWS"] = fetch_local_news(now_tpe)   # 在地快訊(中彰投雲,2026-07-15)
        quotes["SUSPENSION_NEWS"] = fetch_suspension_news()   # 停班停課公告(颱風季)
    except Exception as e:
        print(f"[main] 天氣抓取失敗(不影響晨報): {e}", file=sys.stderr)
        quotes["WEATHER"] = []
    try:
        quotes["SPORTS"] = fetch_sports_digest(now_tpe)
    except Exception as e:
        print(f"[main] 體育抓取失敗(不影響晨報): {e}", file=sys.stderr)
        quotes["SPORTS"] = {}
    try:
        quotes["MA200_STATUS"] = fetch_ma200_status()   # 核心持股 200 日線(長線波段參考)
    except Exception as e:
        print(f"[main] MA200 抓取失敗(不影響晨報): {e}", file=sys.stderr)
        quotes["MA200_STATUS"] = {}
    try:
        quotes["EVENT_CALENDAR"] = fetch_event_calendar(now_tpe)
    except Exception as e:
        print(f"[main] 風險事件日曆失敗(不影響晨報): {e}", file=sys.stderr)
        quotes["EVENT_CALENDAR"] = []
    try:
        quotes["TW_CALENDAR"] = fetch_tw_calendar(now_tpe)
    except Exception as e:
        print(f"[main] 台股行事曆失敗(不影響晨報): {e}", file=sys.stderr)
        quotes["TW_CALENDAR"] = {}
    try:
        quotes["MEDICAL_JOURNALS"] = translate_journal_titles(
            fetch_medical_journal_articles())
    except Exception as e:
        print(f"[main] 醫學文獻抓取失敗(不影響晨報): {e}", file=sys.stderr)
        quotes["MEDICAL_JOURNALS"] = []

    # 5.05 新聞去重（同事件常被多個 RSS 重貼，去重後 LLM 訊號更乾淨）
    news = dedup_news(news)

    # 5.1 (Task B) 新聞重要性分類
    news = classify_news_importance(news)

    # 5.2 (Task A) 對 critical 事件抓全文(P0-2:時間預算不足時跳過——全文是「加深」而非核心)
    if _run_budget_ok(360, "重大事件全文擷取"):
        print("[main] 對重大事件擷取全文…")
        try:
            # 同時對 critical 與 high 級新聞抓全文(個股新聞多半屬 high,只有 RSS snippet
            # 會讓 LLM 因「沒有具體事實」而把該公司刪掉,報告變稀薄)
            news = fetch_news_fulltext(news, max_critical=10, max_high=16)
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
    _mark_phase("TAIFEX/籌碼/預測")
    print("[main] 抓 TAIFEX 三大法人台指期未平倉…")
    try:
        taifex_oi = fetch_taifex_foreign_futures()
    except Exception as e:
        print(f"[main] TAIFEX 抓取失敗: {e}", file=sys.stderr)
        taifex_oi = {}
    # 5.4b 大額交易人 + 選擇權 P/C(借鏡 node-twstock;OpenAPI;各自 fail-safe 回 {})
    taifex_large = fetch_taifex_large_traders()
    taifex_pcr = fetch_taifex_options_pc_ratio()

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

    # 5.8 (Opt 1) 載入歷史記憶（450 天，供預測校準與回溯；prompt 仍只顯示近 7 天敘事流）
    history = load_history_state(days=450)

    # 5.9 (Task B) 抓 TAIFEX 夜盤台指期
    print("[main] 抓 TAIFEX 夜盤台指期…")
    try:
        night_txf = fetch_taifex_night_session()
    except Exception as e:
        print(f"[main] 夜盤抓取失敗: {e}", file=sys.stderr)
        night_txf = {}

    # 5.9b 台指期與現貨價差(純事實,不進計分)
    try:
        quotes["TAIFEX_BASIS"] = fetch_taifex_basis()
    except Exception as e:
        print(f"[main] 台指期價差抓取失敗(不影響晨報): {e}", file=sys.stderr)
        quotes["TAIFEX_BASIS"] = {}

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
        taiex_pred = calc_taiex_prediction(
            taiex_hist, sox_pct, tsm_pct, night_pct,
            context={"MACRO": macro, "TAIFEX_OI": taifex_oi,
                     "us_beta_samples": _taiex_us_beta_samples(history)})
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

    # 5.106 0050 ETF 開盤預測（2330 + 加權扣除 2330 後的市場），再做 0050 獨立 bias 校正
    print("[main] 計算 0050 開盤預測…")
    last_0050 = fetch_twse_close("0050")
    if not last_0050:
        # TWSE 失敗時退回 Yahoo(2026-06-12 TWSE 單點故障使 0050 預測整列缺失)。
        # Yahoo 對 0050 偶有延遲,但「略舊的昨收」仍遠勝「整列資料缺失」。
        last_0050 = (fetch_quote("0050.TW") or {}).get("close")
        if last_0050:
            print("[main] 0050 昨收 TWSE 失敗,改用 Yahoo fallback", file=sys.stderr)
    try:
        tw0050_pred = calc_0050_prediction(
            last_0050, predictions, taiex_pred, ex_div_amt=ex_div.get("0050", 0.0))
        # 0050 自身殘差校正(原本完全沒校正,殘差最大 +1.77%)
        tw0050_pred = calibrate_0050_bias(tw0050_pred, history)
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
    quotes["BREADTH"] = breadth

    # 5.11 (Task F) 預測準確度回溯
    print("[main] 計算預測準確度回溯…")
    backtest_block = build_prediction_backtest(history)

    # 6. 抓台股市值前 100 大 universe + 法人/表現（含 30 日累積）
    _mark_phase("TWSE universe/候選新聞")
    print("[main] 抓台股市值前 100 大 universe…")
    try:
        tw_universe = fetch_tw_top100_universe(top_n=100)
    except Exception as e:
        print(f"[main] universe 抓取失敗，用 fallback: {e}", file=sys.stderr)
        tw_universe = _fallback_universe()
    quotes["TW_UNIVERSE_FALLBACK"] = any(
        v.get("fallback") for v in tw_universe.values())

    # 6.05 類股熱度掃描(純計算,重用 STOCK_DAY_ALL + 上市基本資料快取,零新請求;不進計分)。
    #      供「九、其他類股」硬數據背景與非科技動態公司池的類股挑選依據。
    try:
        quotes["SECTOR_HEAT"] = fetch_sector_heat()
    except Exception as e:
        print(f"[main] 類股熱度計算失敗(不影響晨報): {e}", file=sys.stderr)
        quotes["SECTOR_HEAT"] = {}
    # 6.06 台股估值溫度(A4)+ 選擇權結算磁吸價(A5)——白話顯示層,皆不進計分
    try:
        quotes["VALUATION"] = fetch_market_valuation()
    except Exception as e:
        print(f"[main] 估值溫度失敗(不影響晨報): {e}", file=sys.stderr)
        quotes["VALUATION"] = {}
    try:
        quotes["TXO_MAGNET"] = fetch_txo_magnet()
    except Exception as e:
        print(f"[main] 選擇權磁吸價失敗(不影響晨報): {e}", file=sys.stderr)
        quotes["TXO_MAGNET"] = {}

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
    quotes["FOREIGN_TOP10_TOTAL"] = _foreign_top10_total(tw0050)

    # 6.2 市值前 15 大 + 爆發力前 30 檔 MOPS 重大訊息(擴大覆蓋,讓五檔候選抓得到自家重訊;
    #     每檔一支 RSS,故合計上限 40 檔以控制請求量與 Actions 時間)
    print("[main] 抓台股重點公司 MOPS 重大訊息…")
    try:
        top_mcap_codes = [c for c, _ in sorted(
            tw_universe.items(),
            key=lambda kv: kv[1].get("market_cap") or 0, reverse=True)[:15]]
        breakout_codes = [
            item.get("code") for item in sorted(
                tw0050,
                key=lambda item: (item.get("breakout") or {}).get("score", 0),
                reverse=True,
            )[:30]
            if item.get("code")
        ]
        # 深耕公司(使用者指定)釘進監看清單:人事異動/重大投資公告多只出現在 MOPS,
        # 不可因市值排名波動或清單縮減而漏掉。
        _deep_watch = ["2330", "2882", "2891"]
        mops_codes = list(dict.fromkeys(_deep_watch + top_mcap_codes + breakout_codes))[:40]
        tw_mops = fetch_tw_major_announcements(mops_codes)
    except Exception as e:
        print(f"[main] MOPS 抓取失敗: {e}", file=sys.stderr)
        tw_mops = []

    # 6.3 對「爆發力前 20 檔候選」動態查 Google News(補五檔候選的自家催化訊號;
    #     已被固定 12 檔權值查過的不重複)。tag company_label → 直接歸因到該股。
    print("[main] 對爆發力候選查個股新聞…")
    try:
        cand_news = fetch_candidate_company_news(
            tw0050, top_n=20,
            exclude_codes={lbl for _, lbl in GOOGLE_NEWS_COMPANIES})
        if cand_news:
            news = dedup_news(news + classify_news_importance(cand_news))
            print(f"[main] 併入候選個股新聞後共 {len(news)} 則")
    except Exception as e:
        print(f"[main] 候選個股新聞抓取失敗(不影響晨報): {e}", file=sys.stderr)

    # 6.35 動態宇宙(美股側):今日有 8-K 的重點科技股,自動加進新聞查詢
    try:
        eightk_news = fetch_8k_company_news(
            sec_filings, exclude_labels={lbl for _, lbl in GOOGLE_NEWS_COMPANIES})
        if eightk_news:
            news = dedup_news(news + classify_news_importance(eightk_news))
            print(f"[main] 併入 8-K 公司新聞後共 {len(news)} 則")
    except Exception as e:
        print(f"[main] 8-K 公司新聞抓取失敗(不影響晨報): {e}", file=sys.stderr)

    # 6.37 非科技類股領先股:依 SECTOR_HEAT 挑當日最熱的非科技類股領先個股,補其自家新聞。
    #      補齊「金融/傳產/航運/生技以外個股催化長期抓不到」的缺口;與候選機制同路徑(擴輸入非改係數)。
    try:
        sector_news = fetch_sector_leader_news(
            quotes.get("SECTOR_HEAT") or {},
            exclude_codes={lbl for _, lbl in GOOGLE_NEWS_COMPANIES})
        if sector_news:
            news = dedup_news(news + classify_news_importance(sector_news))
            print(f"[main] 併入非科技類股領先股新聞後共 {len(news)} 則")
    except Exception as e:
        print(f"[main] 非科技類股新聞抓取失敗(不影響晨報): {e}", file=sys.stderr)

    # 6.36 補抓全文:候選股/8-K 新聞在 5.2 全文擷取之後才併入,其中升級為
    # critical/high 者在此補抓(fetch_news_fulltext 冪等,已抓過的會跳過)。
    if _run_budget_ok(300, "候選/8-K 補抓全文"):
        try:
            news = fetch_news_fulltext(news, max_critical=3, max_high=8)
        except Exception as e:
            print(f"[main] 補抓全文失敗(不影響晨報): {e}", file=sys.stderr)

    _mark_phase("事件抽取/模型/walk-forward")
    print("[main] 建立台股交易日曆、新聞事件聚類與 point-in-time 模型…")
    _ml_t0 = time.monotonic()
    trading_sessions = fetch_tw_trading_sessions(months=18)
    # 上一交易日:供渲染端標示「昨收/成交額」實際屬於哪個 session。颱風臨時休市/連假後
    # 「昨收」其實是好幾天前的收盤(07-13 信的昨收實為 07-09),不標日期會誤導。
    try:
        _today_str = now_tpe.strftime("%Y-%m-%d")
        quotes["LAST_TRADING_SESSION"] = max(
            (s for s in trading_sessions if s < _today_str), default="")
    except Exception:
        quotes["LAST_TRADING_SESSION"] = ""
    model_history = load_model_history()
    model_history, model_backfill = backfill_model_history(
        model_history, trading_sessions)
    quotes["MODEL_BACKFILL"] = model_backfill
    # 事件抽取:LLM 「豐富化」是額外呼叫、非核心;時間不足時**只跳過 LLM 那層**,
    # 仍用確定性抽取 extract_structured_events 產生 events——它是計分/歸因/來源健康的
    # 輸入,若改傳 [] 會在時間預算觸發時「悄悄改變計分」並讓來源健康被誤扣分
    # (Codex review;違反計分凍結)。(P0-2)
    if _run_budget_ok(260, "LLM 新聞事件抽取(豐富化)"):
        print(f"[main] 模型歷史/回填完成 ({time.monotonic()-_ml_t0:.1f}s);跑事件抽取…")
        _events = call_llm_event_extractor(news, tw_mops)
    else:
        _events = extract_structured_events(news, tw_mops)   # 確定性 baseline,無 LLM/網路
    structured_events = apply_event_timeline(model_history, _events)
    quotes["STRUCTURED_NEWS_EVENTS"] = structured_events
    try:
        quotes["EVENT_TIMELINE"] = translate_event_titles(
            update_event_timeline(structured_events, now_tpe))
    except Exception as e:
        print(f"[main] 事件 timeline 失敗(不影響晨報): {e}", file=sys.stderr)
        quotes["EVENT_TIMELINE"] = []
    quotes["FEATURE_DRIFT"] = build_feature_drift_report(model_history, tw0050)
    quotes["SOURCE_HEALTH"] = build_source_health_report(
        tw0050, news, structured_events, quotes.get("TW_DAILY_INTELLIGENCE"))
    try:   # N4:滾動 30 天來源健康歷史 → 標記連續失敗的來源(不影響計分)
        _persist = update_source_health_history(
            quotes["SOURCE_HEALTH"], now_tpe.strftime("%Y-%m-%d"), feed_stats=_FEED_STATS)
        if _persist:
            quotes["SOURCE_HEALTH"]["persistent_failures"] = _persist
            print(f"[health] 連續失敗來源: {', '.join(_persist)}", file=sys.stderr)
    except Exception as e:
        print(f"[health] 歷史更新略過: {e}", file=sys.stderr)
    print(f"[main] 事件/來源健康完成 ({time.monotonic()-_ml_t0:.1f}s);跑 walk-forward…")
    quotes["MODEL_WALK_FORWARD"] = evaluate_model_walk_forward(
        model_history, trading_sessions)
    quotes["MODEL_MONITORING"] = build_model_monitoring_report(
        quotes["MODEL_WALK_FORWARD"])
    quotes["US_HOLIDAY"] = detect_us_holiday(quotes, now_tpe.date())
    try:
        # Absorption Ratio 系統性風險早警(借鏡 Kritzman-Li);失敗不影響晨報
        quotes["ABSORPTION"] = calc_absorption_ratio(model_history)
    except Exception as e:
        print(f"[main] Absorption Ratio 失敗(不影響晨報): {e}", file=sys.stderr)
        quotes["ABSORPTION"] = {}
    quotes["MARKET_REGIME"] = _market_regime(quotes)
    tw0050 = enrich_stock_attention_candidates(
        tw0050, news, tw_mops, history, target_session_date,
        model_history=model_history,
        sessions=trading_sessions,
        quotes=quotes,
        structured_events=structured_events,
        feature_drift=quotes["FEATURE_DRIFT"],
        source_health=quotes["SOURCE_HEALTH"],
        model_monitoring=quotes["MODEL_MONITORING"])
    # 為每日股池補估值/獲利率/ROE(TWSE 全市場一次),供「Top5 推薦/觀察」顯示 + model_history 累積
    try:
        _attach_listing_fundamentals(tw0050)
    except Exception as e:
        print(f"[main] 附加基本面失敗(略過): {e}", file=sys.stderr)
    quotes["BREAKOUT_TRACKING"] = build_breakout_tracking(
        history, tw0050, target_session_date, sessions=trading_sessions)
    _ml_elapsed = time.monotonic() - _ml_t0
    print(f"[main] ML/情報區塊總耗時 {_ml_elapsed:.1f}s")
    if _ml_elapsed > 600:
        print(f"[main] ⚠ ML 區塊耗時 {_ml_elapsed:.0f}s 偏高(workflow timeout 900s);"
              f"如逼近上限可調降 MODEL_BACKFILL_BATCH_DAYS", file=sys.stderr)

    # 6.5 建立歷史校準資料（TSM vs 2330 開盤實證對照）
    calibration = build_historical_calibration(hist_2330, days=7)
    print(f"[main] 歷史校準資料已生成（{len(calibration)} 字）")

    # 6.55 美股休市偵測:已在上方模型區塊算過 quotes["US_HOLIDAY"],這裡僅記錄,不重複計算
    if quotes.get("US_HOLIDAY", {}).get("detected"):
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

    # 6.55 外資台指期「日變化」+ 外資現貨買超 → 讓淨空警告判讀「方向」而非只看「水位」。
    #   (外資現貨大買 + 期貨淨空 = 多為避險,非看空;只有空單『新增』且現貨同步調節才是實空壓)
    try:
        if isinstance(taifex_oi, dict) and taifex_oi.get("foreign_oi_net") is not None:
            prev_oi = next((h.get("taifex_foreign_oi") for h in reversed(history)
                            if h.get("taifex_foreign_oi") is not None), None)
            if prev_oi is not None:
                taifex_oi["foreign_oi_prev"] = prev_oi
                taifex_oi["foreign_oi_chg"] = taifex_oi["foreign_oi_net"] - prev_oi
            if tw0050:
                taifex_oi["foreign_spot_net_lot"] = round(
                    sum(_safe_number(s.get("foreign_lot")) for s in tw0050), 0)
    except Exception as e:
        print(f"[main] 外資期貨變化/現貨彙整失敗: {e}", file=sys.stderr)

    # 6.6 (Task H) 偵測過熱警告（含 US_HOLIDAY + 過熱/超賣警示）
    alerts = detect_market_alerts(quotes, fair, predictions, taifex_oi)
    print(f"[main] 偵測到 {len(alerts)} 個警告訊號")

    # 把 SEC + TAIFEX + 新增資料包進 quotes
    quotes["SEC_FILINGS"] = sec_filings
    quotes["TW_MOPS"] = tw_mops   # 去重已在 fetch_tw_major_announcements 內以未截斷原文完成
    quotes["TAIFEX_OI"] = taifex_oi
    quotes["TAIFEX_LARGE_TRADERS"] = taifex_large
    quotes["TAIFEX_PCR"] = taifex_pcr
    quotes["MARGIN"] = margin
    quotes["WEEKLY"] = weekly
    quotes["EARNINGS_PROXIMITY"] = earnings_proximity
    quotes["HISTORY"] = history
    # G5:僅週一綜合報加「上週檢討」確定性統計(供 LLM 週報檢討段引用;純統計、不動計分)。
    if mode == "週末綜合":
        quotes["WEEKLY_REVIEW"] = _compute_weekly_review_stats(
            history, today=now_tpe.strftime("%Y-%m-%d"))
    quotes["NIGHT_TXF"] = night_txf
    quotes["TAIEX_PRED"] = taiex_pred
    quotes["TW0050_PRED"] = tw0050_pred
    # 把 universe snapshot 也塞進 quotes,讓 render_html 可以畫「籌碼悄悄站隊 Top 10」
    quotes["TW_UNIVERSE_SNAPSHOT"] = tw0050
    quotes["BREADTH"] = breadth
    # Top5 的 FinMind 補值(EPS年增/外資持股)在此(渲染前、抓取階段)先做,
    # 避免 render_html 於寄信前才同步打 FinMind live HTTP(慢會拖到寄信)。失敗略過不影響晨報。
    try:
        _top5 = _rank_attention_candidates(tw0050)[:5]
        if _top5:
            _fm5 = _finmind_top5_extras(
                [str(s.get("code", "")) for s in _top5],
                prices={str(s.get("code", "")): s.get("close") for s in _top5})
            for _s in _top5:
                _s.update(_fm5.get(str(_s.get("code", "")), {}))
    except Exception as e:
        print(f"[main] Top5 FinMind 補值略過: {e}", file=sys.stderr)

    # 6.65 個人持股「昨日帳上漲跌」(用 前天收盤 vs 昨天收盤,非預測)
    #      隱私:只算彙總 % + 金額,不揭露任何個股明細。
    if PORTFOLIO_1 or PORTFOLIO_2:
        print("[main] 計算個人持股昨日帳上漲跌…")
        try:
            all_codes = {**PORTFOLIO_1, **PORTFOLIO_2}
            closes_map: dict = {}
            for code in all_codes:
                cl = fetch_twse_recent_closes(code, want=2)   # TWSE 官方,避開 Yahoo ETF 落後
                if len(cl) >= 2:
                    closes_map[code] = (cl[-2], cl[-1])        # (前天收盤, 昨天收盤)
            quotes["PORTFOLIO_ACTUAL"] = {
                "p1": calc_portfolio_actual(PORTFOLIO_1, closes_map),
                "p2": calc_portfolio_actual(PORTFOLIO_2, closes_map),
                "p1_name": PORTFOLIO_1_NAME,
                "p2_name": PORTFOLIO_2_NAME,
            }
        except Exception as e:
            print(f"[main] 持股昨日漲跌計算失敗(不影響晨報): {e}", file=sys.stderr)
            quotes["PORTFOLIO_ACTUAL"] = {}

    # 6.655 G1 持倉曝險卡已依使用者要求移除(2026-07-15);引擎(portfolio_risk.py/
    #       fetch_portfolio_risk)保留供日後復用,main 不再抓取計算。

    # 6.66 除息已在預測模型執行前套用，這裡只加入報告提醒。
    if ex_div:
        # 逐檔附 Python 算好的「除息參考價 = 昨收 − 配息」,讓 LLM 直接引用,
        # 避免它自己心算把參考價與預測中樞混排(曾寫出「2,2182 元」這種畸形數字)。
        _last_by_code = {
            "2330": predictions.get("last_2330") if isinstance(predictions, dict) else None,
            "00662": fair.get("last_00662_price") if isinstance(fair, dict) else None,
            "0050": tw0050_pred.get("last") if isinstance(tw0050_pred, dict) else None,
        }
        parts = []
        for c in public_codes:
            if c not in ex_div:
                continue
            last_c = _last_by_code.get(c)
            ref = (f"，除息參考價 {round(last_c - ex_div[c], 2)} 元（昨收 {last_c} − 配息）"
                   if isinstance(last_c, (int, float)) else "")
            parts.append(f"{c} 配息 {ex_div[c]} 元{ref}")
        named = "、".join(parts)
        alerts.append({"level": "yellow", "title": "除息日提示",
                       "detail": f"預測交易日除息：{named}。上方預測開盤點位已減息，除息缺口非跌幅。"
                                 f"（參考價由 Python 計算，引用時請原樣使用）"})

    quotes["BACKTEST"] = backtest_block
    quotes["ALERTS"] = alerts

    # 6.7 彙整資料品質（讓 LLM 與 HTML 都知道哪些來源失敗 / 降級）
    quotes["DATA_QUALITY"] = build_data_quality(quotes, fair, predictions, news, tw0050)

    # 7. LLM 分析
    _mark_phase("LLM 主分析")
    print(f"[main] 呼叫 LLM 分析… (provider={LLM_PROVIDER})")
    analysis = call_llm_analysis(quotes, fair, predictions, news, tw0050, calibration)

    # 8. 組信
    _mark_phase("渲染")
    html = render_html(quotes, fair, predictions, analysis, report_date, mode)

    # 8.5 (Opt 1) 準備今日記憶。Production 必須等 SMTP 成功後才提交，
    # 否則寄信失敗卻先標記 Podcast shown_at，會造成永久漏寄。
    pending_state_entry: Optional[dict] = None
    try:
        crit_titles = [n["title"] for n in news if n.get("importance") == "critical"][:5]
        # G4:存今日 LLM 立場,供明日「敘事變化」段逐字對照(顯示層產物,非凍結計分模型)。
        _stance_state = _extract_stance(analysis) if isinstance(analysis, str) else {}
        pending_state_entry = {
            "date": now_tpe.strftime("%Y-%m-%d"),
            "stance_label": _stance_state.get("label"),
            "stance_score": _stance_state.get("score"),
            "generated_at": now_tpe.isoformat(),
            "target_session_date": target_session_date,
            "weekday": now_tpe.strftime("%a"),
            "qqq_pct": quotes["QQQ"].get("change_pct"),
            "tsm_pct": quotes["TSM"].get("change_pct"),
            "spy_pct": quotes["SPY"].get("change_pct"),
            "vix": (quotes.get("MACRO", {}) or {}).get("VIX", {}).get("close"),
            "sox_pct": (quotes.get("MACRO", {}) or {}).get("SOX", {}).get("change_pct"),
            "usdtwd": quotes.get("USDTWD"),
            # 標記今日 usdtwd 是否為 stale 昨值降級——供 _last_known_usdtwd 讀取時跳過,
            # 避免「昨值被當成新的真觀測」讓 max_age_days 護欄失效而無限延用(Codex review)。
            "usdtwd_stale": bool(quotes.get("USDTWD_STALE")),
            "fair_00662": fair.get("fair_price"),
            # 三個 model 的「原始」預測值（供 calibrate_predictions 算各模型 MAE 與權重）
            "model1_2330": predictions.get("model1_1to1"),
            "model2_2330": predictions.get("model2_regression"),
            "model3_2330": predictions.get("model3_adr_decay"),
            "model4_2330": predictions.get("model4_momentum"),
            "momentum_5d_pct_2330": predictions.get("momentum_5d_pct"),
            # 經誤差加權 + bias 校正後的最終 2330 預測（供下次算 bias）
            "weighted_final_2330": predictions.get("weighted_final"),
            "foreign_top10_total": quotes.get("FOREIGN_TOP10_TOTAL"),
            "pred_taiex": taiex_pred.get("pred_open"),
            # 0050 開盤預測（供下次 backtest 對比）
            "pred_0050": tw0050_pred.get("pred_open") if isinstance(tw0050_pred, dict) else None,
            "last_0050": tw0050_pred.get("last") if isinstance(tw0050_pred, dict) else None,
            "night_txf_pct": night_txf.get("night_pct"),
            "taifex_foreign_oi": taifex_oi.get("foreign_oi_net"),
            "critical_news": crit_titles,
            "earnings_proximity": earnings_proximity.get("impact"),
            "ex_div_today": ex_div,
            "breakout_candidates": _breakout_candidates_for_state(tw0050),
            # 籌碼悄悄站隊:本次 TDCC 大戶持股快照,供下次 WoW Δ% 比較
            "tdcc_snapshot": tdcc_snapshot_for_state if 'tdcc_snapshot_for_state' in locals() else {},
        }
        completed_session = _latest_completed_session(
            trading_sessions if 'trading_sessions' in locals() else [],
            target_session_date,
        )
        if completed_session:
            # 基本面已於上方 _attach_listing_fundamentals(tw0050) 附加,此處直接存史累積
            label_prices, label_prices_complete = _current_label_prices(model_history)
            save_model_history({
                "session_date": completed_session,
                "generated_at": now_tpe.isoformat(),
                "model_version": MODEL_VERSION,
                "universe_method": "daily_point_in_time_top100",
                "taiex_close": (
                    taiex_pred.get("last_close")
                    or (twse_taiex_close if 'twse_taiex_close' in locals() else None)
                ),
                "market_regime": quotes.get("MARKET_REGIME"),
                "stocks": _snapshot_for_model(tw0050),
                "label_prices": label_prices,
                "label_prices_complete": label_prices_complete,
                "structured_events": (
                    quotes.get("STRUCTURED_NEWS_EVENTS") or [])[:40],
            })
    except Exception as e:
        print(f"[main] 準備歷史記憶失敗（不影響寄信）: {e}", file=sys.stderr)

    # 9. dry-run 模式：只輸出檔案
    if os.environ.get("DRY_RUN") == "1":
        persist_delivered_report_state(
            pending_state_entry,
            quotes.get("PODCAST_SHOWN_EPISODES", quotes.get("PODCAST_DIGEST")) or [],
            mark_podcasts=False,
        )
        out = "/tmp/morning_report_preview.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[main] DRY_RUN — 預覽寫入 {out}")
        _mark_phase("完成")
        _write_run_manifest(now_tpe)
        return 0

    # P1-4:在寄信/state push 前寫 manifest,使其隨 state 一併 commit(供跨日趨勢);
    # SMTP 送出約 5-10s 未計入屬可接受(manifest 主要看 fetch/compute 花在哪)。失敗不影響寄信。
    _mark_phase("完成")
    _write_run_manifest(now_tpe)

    # 10. 寄信
    subject = f"📈 美股晨報 {report_date} | QQQ {quotes['QQQ'].get('change_pct','?')}% / TSM {quotes['TSM'].get('change_pct','?')}%"
    # SMTP 成功後才把本次 Podcast 標成已顯示並 push 所有 state。
    # 若 state push 失敗，下次最多重複寄送，不會發生未寄出卻永久消失。
    deliver_report(
        html,
        subject,
        pending_state_entry,
        # 只把「實際出現在信中」的 Podcast 集標成已顯示;被尺寸守衛砍/縮掉的留待下次再出現。
        quotes.get("PODCAST_SHOWN_EPISODES", quotes.get("PODCAST_DIGEST")) or [],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
