"""股癌雷達(Gooaye Radar):偵測《股癌》新集 → 本地轉錄 → LLM 萃取「討論到的族群 + 立場 + 深入重點」
→ 對【看多】族群用「混合驗證」展開台股個股(LLM 提名 → TWSE 真實上市清單裁決,擋幻覺/下市)
→ 每族群取前 5 檔、附籌碼/基本面/動能/近期新聞 → 寄一封獨立通知信。

獨立 workflow(週三/週六發片後跑),不動晨報;股癌處理後標 shown 讓晨報 Podcast 段不重複。
重用 podcast_digest.py(RSS/轉錄)與 morning_report.py(TWSE 籌碼/基本面/動能、寄信、簡轉繁)。

★ 個股清單為「本報依股癌所談族群、以程式自動整理」,非股癌推薦、非投資建議(股癌不點個股)。
★ v1 僅涵蓋「上市(TWSE)」個股;上櫃(TPEx)個股暫不展開(既有 snapshot 資料源為上市)。
★ 本機/CI 設 DRY_RUN=1 只輸出預覽檔不寄信;轉錄需 faster-whisper(workflow 另裝)。
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json
import os
import sys
import time
from pathlib import Path

import requests

import morning_report as mr
import podcast_digest as pdg

RADAR_STATE_FILE = Path("state/gooaye_radar.json")
RADAR_RECIPIENT = os.getenv("RADAR_RECIPIENT", "")            # 留空 → 用晨報同一批收件者
RADAR_MAX_BULLISH_SECTORS = int(os.getenv("RADAR_MAX_BULLISH_SECTORS", "6"))
TOP_N_PER_SECTOR = int(os.getenv("RADAR_TOP_N_PER_SECTOR", "5"))
CAND_PER_SECTOR = 12                                          # 每族群請 LLM 提名候選數
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
_STANCES = ("看多", "看空", "中性")


def log(msg: str) -> None:
    print(f"[radar] {msg}", file=sys.stderr)


def _gooaye_cfg() -> dict:
    return next((c for c in pdg.PODCASTS if c.get("key") == "gooaye"),
                {"key": "gooaye", "name": "股癌", "search": "股癌 Gooaye",
                 "lang": "zh", "country": "TW", "accuracy": "high"})


# ---------- 雷達自有 state(與晨報 podcast_digest.json 分開,避免互相污染)----------
def load_radar_state() -> dict:
    if RADAR_STATE_FILE.exists():
        try:
            data = json.loads(RADAR_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as e:
            log(f"state 讀取失敗(視為空): {e}")
    return {}


def save_radar_state(state: dict) -> None:
    try:
        RADAR_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = RADAR_STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(RADAR_STATE_FILE)
    except Exception as e:
        log(f"state 寫入失敗: {e}")


def radar_processed_guids() -> set:
    """供 morning_report 去重用:雷達已處理(已寄)的股癌 guid 集合。"""
    out = set()
    for show in (load_radar_state() or {}).values():
        if not isinstance(show, dict):
            continue
        for ep in show.get("episodes") or []:
            if ep.get("guid") and ep.get("radar_sent_at"):
                out.add(str(ep["guid"]))
    return out


# ---------- DeepSeek JSON 呼叫(沿用 podcast_digest 的直連 + JSON 模式 + 重試)----------
def _deepseek_json(system_prompt: str, user_content: str, model: str, retries: int = 4) -> dict:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("缺 DEEPSEEK_API_KEY")
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}]
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json={"model": model, "messages": messages,
                      "response_format": {"type": "json_object"}, "temperature": 0.2},
                timeout=300)
            r.raise_for_status()
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            last_err = e
            log(f"DeepSeek 第 {attempt + 1} 次失敗: {str(e)[:120]}")
            time.sleep(15)
    raise RuntimeError(f"DeepSeek 連續失敗: {last_err}")


# ---------- 族群萃取 ----------
SECTOR_EXTRACT_PROMPT = (
    "你是台股族群分析員。以下是一集《股癌》的逐字稿(faster-whisper 機器轉錄,可能有錯字,"
    "請依上下文校正公司名與數字)。股癌主持人習慣談『產業族群/主題趨勢』而少直接報明牌,"
    "請抽取本集討論到的族群與主持人立場。\n"
    "【語言鐵則】所有輸出一律台灣繁體中文,嚴禁簡體字與中國用語(寫晶片/記憶體/被動元件,"
    "不寫芯片/内存)。\n"
    "【鐵則】1) 只記主持人『真的說過』的族群與立場,嚴禁腦補外推。"
    "2) 絕對不要輸出任何個股名稱或股票代號 —— 個股由後段程式依族群自動展開,你只做族群層級。"
    "3) 族群名用台股慣用詞:功率半導體、被動元件、CoWoS先進封裝、HBM記憶體、散熱、光通訊/矽光子、"
    "ASIC、AI伺服器、機器人、重電、矽智財IP、PCB載板、面板、金融、航運、生技… 擇主持人實際提到者。"
    "4) 沒明確立場歸『中性』;明顯看好=『看多』、明顯看壞/示警=『看空』。"
    "5) 廣告、抽獎、生活閒聊、政治八卦略過。\n"
    "只輸出 JSON(無其他文字):{"
    '"episode_summary":"本集 2-4 句總綱",'
    '"key_takeaways":["5-12 條深入重點,每條一句、具體含數字/邏輯/事件"],'
    '"market_view":"對大盤/總經整體看法 1-2 句,沒講就空字串",'
    '"sectors":[{"name":"族群名(台股慣用詞)","stance":"看多|看空|中性",'
    '"reasoning":"主持人對此族群的核心邏輯 1-2 句","evidence":"逐字稿關鍵句重述(可空)"}]}'
    "\n鐵則重申:sectors 內不得出現個股或代號。"
)


def _norm_stance(s: str) -> str:
    s = str(s or "").strip()
    if s in _STANCES:
        return s
    if any(k in s for k in ("多", "bull", "正", "看好")):
        return "看多"
    if any(k in s for k in ("空", "bear", "壞", "示警", "看淡")):
        return "看空"
    return "中性"


def extract_sectors(transcript: str, model: str) -> dict:
    """回 {episode_summary,key_takeaways,market_view,sectors:[{name,stance,reasoning,evidence}]};
    text 欄位一律過 opencc 轉繁(防 LLM 偶出簡體)。"""
    raw = _deepseek_json(SECTOR_EXTRACT_PROMPT, transcript[:pdg.MAX_TRANSCRIPT_CHARS], model)
    t = mr._to_traditional
    sectors = []
    for s in (raw.get("sectors") or []):
        name = t(str(s.get("name", "")).strip())
        if not name:
            continue
        sectors.append({"name": name, "stance": _norm_stance(s.get("stance")),
                        "reasoning": t(str(s.get("reasoning", "")).strip()),
                        "evidence": t(str(s.get("evidence", "")).strip())})
    return {
        "episode_summary": t(str(raw.get("episode_summary", "")).strip()),
        "key_takeaways": [t(str(p).strip()) for p in (raw.get("key_takeaways") or []) if str(p).strip()],
        "market_view": t(str(raw.get("market_view", "")).strip()),
        "sectors": sectors,
    }


# ---------- 看多族群 → 候選個股(LLM 提名,只准提名,真假由白名單裁決)----------
def llm_candidate_tickers(sector_name: str, reasoning: str, model: str) -> list[dict]:
    prompt = (
        "你是台股產業研究員。給你一個族群與其多頭邏輯,請列出該族群在台灣『上市』的代表性個股。"
        "【鐵則】只列上市股(排除 ETF、興櫃);不確定代號就把 code 留空字串、只給 name,"
        "嚴禁編造代號;一律台灣繁體中文。"
        f"只輸出 JSON:{{\"candidates\":[{{\"code\":\"4位數字或空\",\"name\":\"公司簡稱\"}}]}},最多 {CAND_PER_SECTOR} 檔。"
    )
    try:
        raw = _deepseek_json(prompt, f"族群:{sector_name}\n多頭邏輯:{reasoning}", model)
    except Exception as e:
        log(f"族群「{sector_name}」候選生成失敗: {str(e)[:100]}")
        return []
    out = []
    for c in (raw.get("candidates") or [])[:CAND_PER_SECTOR]:
        code = "".join(ch for ch in str(c.get("code", "")) if ch.isdigit())
        name = str(c.get("name", "")).strip()
        if code or name:
            out.append({"code": code, "name": name})
    return out


# ---------- 混合驗證:候選 → 真實上市清單裁決(擋幻覺/下市/張冠李戴)----------
def _names_match(llm_name: str, official_name: str) -> bool:
    """LLM 給的名 vs 白名單官方名是否相符(擋張冠李戴)。LLM 沒給名→信任代號;
    完全相等/互為子字串/bigram 重疊≥0.5 視為相符。"""
    a, b = mr._norm_podcast_point(llm_name), mr._norm_podcast_point(official_name)
    if not a or not b:
        return True
    if a == b or a in b or b in a:
        return True
    return mr._overlap_coef(mr._podcast_bigrams(a), mr._podcast_bigrams(b)) >= 0.5


def validate_tickers(candidates: list[dict], whitelist: dict) -> list[str]:
    """whitelist = {code:{name,...}}(全上市)。回經四關驗證的有效代號(去重保序),fail-closed。
    四關:格式 → 存在於白名單(擋幻覺/下市)→ 官方名一致(擋張冠李戴)→ 名稱反查救回。
    代號與名稱衝突時『信任名稱』(以名稱反查正確代號),反查不到則丟棄,絕不誤收衝突代號。"""
    if not whitelist:                       # 白名單抓取失敗 → 不展開個股(由呼叫端決定降級)
        return []
    name_to_code = {}
    for code, meta in whitelist.items():
        nm = mr._norm_podcast_point(meta.get("name", ""))
        if nm:
            name_to_code.setdefault(nm, code)
    valid, seen = [], set()
    for c in candidates:
        code, name = c.get("code", ""), c.get("name", "")
        if len(code) == 4 and code in whitelist and _names_match(name, whitelist[code].get("name", "")):
            chosen = code                   # 代號存在且名稱相符
        else:
            chosen = name_to_code.get(mr._norm_podcast_point(name))   # 代號錯/張冠李戴/缺 → 名稱反查救回
        if chosen and chosen not in seen:
            seen.add(chosen)
            valid.append(chosen)
    return valid


# ---------- enrichment + 排序取前 N ----------
def _norm01(vals: dict, code: str, default_mid: bool = True) -> float:
    xs = [v for v in vals.values() if isinstance(v, (int, float))]
    v = vals.get(code)
    if not xs or not isinstance(v, (int, float)):
        return 0.5 if default_mid else 0.0
    lo, hi = min(xs), max(xs)
    if hi <= lo:
        return 0.5
    return (v - lo) / (hi - lo)


def rank_top5(entries: list[dict], top_n: int = TOP_N_PER_SECTOR) -> list[dict]:
    """以 radar_score 在『同族群候選池內』相對排序取前 N(各子項池內 min-max 正規化)。
    radar_score = 0.40 籌碼(smart_money) + 0.20 30日法人 + 0.20 月營收YoY + 0.20 動能。"""
    if not entries:
        return []
    sm = {e["code"]: _safe(e.get("smart_money", {}).get("score")) for e in entries}
    inst = {e["code"]: _safe(e.get("foreign_30d_lot")) for e in entries}
    rev = {e["code"]: _safe(e.get("rev_yoy_pct")) for e in entries}
    mom = {e["code"]: _safe(e.get("pct_5d")) for e in entries}
    for e in entries:
        c = e["code"]
        e["radar_score"] = round(
            100 * (0.40 * (sm.get(c) or 0) / 100
                   + 0.20 * _norm01(inst, c)
                   + 0.20 * _norm01(rev, c)
                   + 0.20 * _norm01(mom, c)), 1)
    entries.sort(key=lambda e: (-(e.get("radar_score") or 0),
                                -(_safe(e.get("foreign_30d_lot")) or 0), e["code"]))
    return entries[:top_n]


def _safe(v):
    return v if isinstance(v, (int, float)) else None


def _stock_news_oneliner(code: str, name: str) -> str:
    try:
        feed = mr._feedparser_parse_url_with_timeout(mr._gnews_rss(f"{name} {code}", when="2d"))
        for e in (getattr(feed, "entries", None) or [])[:1]:
            title = str(e.get("title", "")).strip()
            if title:
                return mr._to_traditional(title)
    except Exception:
        pass
    return "—"


def enrich_sector(codes: list[str], whitelist: dict) -> list[dict]:
    """把驗證後代號組成 mini-universe → fetch_tw0050_snapshot 取籌碼/營收/動能 → 排序前 N → 補新聞。"""
    if not codes:
        return []
    universe = {c: {"name": whitelist.get(c, {}).get("name", c),
                    "industry": whitelist.get(c, {}).get("industry", ""),
                    "market_cap": None} for c in codes}
    try:
        snap = mr.fetch_tw0050_snapshot(universe=universe)
    except Exception as e:
        log(f"snapshot 失敗: {str(e)[:120]}")
        return []
    entries = [e for e in (snap or []) if e.get("code") in set(codes)]
    top = rank_top5(entries)
    for e in top:
        e["_news"] = _stock_news_oneliner(e["code"], e.get("name", ""))
    return top


# ---------- 渲染 ----------
_DISCLAIM_TOP = (
    "⚠ 重要聲明:本信個股清單為『本報依股癌本集所談族群、以程式自動整理』,"
    "並非股癌節目或主持人推薦的個股(股癌不點名個股,只談族群趨勢)。僅供研究參考,"
    "非投資建議,請自行判斷並承擔風險。"
)
_DISCLAIM_FOOT = (
    "免責聲明:本信由程式自動轉錄《股癌》公開節目(faster-whisper,可能有轉錄誤差)、以 AI 萃取"
    "主持人談及之『產業族群與立場』後,再由本報程式依族群自動展開台股個股並佐以公開籌碼/基本面/"
    "動能/新聞資料,純為個人閱讀整理之輔助。(1) 個股為本報自動整理,非《股癌》或主持人之推薦或背書。"
    "(2) 族群立場係 AI 摘要,可能與主持人原意有出入,一切以節目原音為準。(3) 所有內容僅供參考,"
    "不構成投資建議;本報不對資料正確性與依本信操作之損益負責。(4) 投資有風險,請獨立判斷、自負盈虧。"
    "本信與《股癌》節目無任何合作或從屬關係。"
)


def _stance_color(stance: str) -> str:
    return {"看多": "#dc2626", "看空": "#16a34a"}.get(stance, "#64748b")


def render_radar_html(meta: dict, extract: dict, sector_stocks: list[dict],
                      whitelist_ok: bool = True) -> str:
    esc = _html.escape
    t = mr._to_traditional
    # 防禦性:顯示前一律轉繁(opencc 對繁體近乎 idempotent),即使上游漏轉也不外漏簡體。
    extract = {
        "episode_summary": t(extract.get("episode_summary", "")),
        "market_view": t(extract.get("market_view", "")),
        "key_takeaways": [t(p) for p in (extract.get("key_takeaways") or [])],
        "sectors": [{**s, "name": t(s.get("name", "")), "reasoning": t(s.get("reasoning", ""))}
                    for s in (extract.get("sectors") or [])],
    }
    sector_stocks = [{"sector": {**b["sector"], "name": t(b["sector"].get("name", "")),
                                 "reasoning": t(b["sector"].get("reasoning", ""))},
                      "stocks": b.get("stocks") or []} for b in (sector_stocks or [])]
    parts = [
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'PingFang TC\',\'Microsoft JhengHei\',sans-serif;'
        'max-width:680px;margin:0 auto;background:#fff;color:#0f172a;">',
        '<div style="background:linear-gradient(135deg,#7c2d12,#ea580c);color:#fff;padding:22px 24px;">'
        '<div style="font-size:13px;letter-spacing:2px;opacity:.85;">📻 GOOAYE RADAR・股癌雷達</div>'
        f'<h1 style="margin:6px 0 0;font-size:22px;">{esc(t(meta.get("title", "")))}</h1>'
        f'<div style="margin-top:4px;font-size:13px;opacity:.9;">{esc(meta.get("published", ""))}'
        '　|　AI 轉錄本集 → 萃取討論族群 → 依族群自動整理台股個股供延伸觀察</div></div>',
        '<div style="margin:14px 16px;padding:11px 14px;background:#fffbeb;border-left:5px solid #f59e0b;'
        f'border-radius:6px;font-size:13px;color:#78350f;line-height:1.6;">{esc(_DISCLAIM_TOP)}</div>',
    ]
    # 本集深入重點
    parts.append('<div style="padding:0 16px;">')
    if extract.get("episode_summary"):
        parts.append(f'<p style="font-size:15px;line-height:1.7;color:#0f172a;">'
                     f'<b>本集總綱:</b>{esc(extract["episode_summary"])}</p>')
    if extract.get("market_view"):
        parts.append(f'<p style="font-size:14px;color:#334155;"><b>大盤觀點:</b>{esc(extract["market_view"])}</p>')
    if extract.get("key_takeaways"):
        lis = "".join(f'<li style="margin:4px 0;">{esc(p)}</li>' for p in extract["key_takeaways"])
        parts.append('<div style="font-size:14px;color:#0f172a;"><b>深入重點:</b>'
                     f'<ul style="margin:6px 0;padding-left:20px;line-height:1.6;">{lis}</ul></div>')
    # 族群總覽
    if extract.get("sectors"):
        rows = "".join(
            f'<div style="margin:4px 0;font-size:13px;">'
            f'<b style="color:{_stance_color(s["stance"])};">[{esc(s["stance"])}]</b> '
            f'<b>{esc(s["name"])}</b>　<span style="color:#475569;">{esc(s.get("reasoning", ""))}</span></div>'
            for s in extract["sectors"])
        parts.append('<h2 style="font-size:17px;margin:18px 0 8px;border-left:4px solid #ea580c;'
                     f'padding-left:8px;">族群總覽</h2>{rows}')
    parts.append("</div>")
    # 各看多族群個股表
    if not whitelist_ok:
        parts.append('<div style="margin:14px 16px;padding:10px 14px;background:#f1f5f9;border-radius:6px;'
                     'font-size:13px;color:#475569;">※ 個股清單因 TWSE 上市清單暫時無法取得而略過,本期僅提供族群立場分析。</div>')
    for blk in sector_stocks:
        sec, stocks = blk["sector"], blk["stocks"]
        head = (f'<h2 style="font-size:17px;margin:18px 16px 6px;color:#b91c1c;">看多族群:{esc(sec["name"])}</h2>'
                f'<div style="margin:0 16px 6px;font-size:13px;color:#475569;">股癌邏輯:{esc(sec.get("reasoning", ""))}</div>')
        if not stocks:
            parts.append(head + '<div style="margin:0 16px;font-size:13px;color:#94a3b8;">'
                                '(此族群未取得通過驗證的上市個股,僅列立場)</div>')
            continue
        trs = []
        for i, e in enumerate(stocks, 1):
            sm = e.get("smart_money", {}) or {}
            inst30 = _safe(e.get("foreign_30d_lot"))
            rev = _safe(e.get("rev_yoy_pct"))
            p5 = _safe(e.get("pct_5d"))
            d20 = _safe(e.get("ma20_dist_pct"))
            trs.append(
                f'<tr><td style="padding:6px 8px;border-bottom:1px solid #e2e8f0;">{i}</td>'
                f'<td style="padding:6px 8px;border-bottom:1px solid #e2e8f0;font-weight:700;">'
                f'{esc(str(e.get("code", "")))} {esc(t(str(e.get("name", ""))))}</td>'
                f'<td style="padding:6px 8px;border-bottom:1px solid #e2e8f0;font-size:12px;">'
                f'{int(sm.get("score") or 0)} {esc(t(str(sm.get("tag", ""))))}</td>'
                f'<td style="padding:6px 8px;border-bottom:1px solid #e2e8f0;text-align:right;font-size:12px;">'
                f'{("%+d" % inst30) if inst30 is not None else "—"}</td>'
                f'<td style="padding:6px 8px;border-bottom:1px solid #e2e8f0;text-align:right;font-size:12px;">'
                f'{("%+.1f%%" % rev) if rev is not None else "—"}</td>'
                f'<td style="padding:6px 8px;border-bottom:1px solid #e2e8f0;text-align:right;font-size:12px;">'
                f'{("%+.1f%%" % p5) if p5 is not None else "—"} / '
                f'{("%+.1f%%" % d20) if d20 is not None else "—"}</td>'
                f'<td style="padding:6px 8px;border-bottom:1px solid #e2e8f0;font-size:12px;color:#475569;">'
                f'{esc(e.get("_news", "—"))}</td></tr>')
        parts.append(
            head +
            '<table style="width:calc(100% - 32px);margin:6px 16px;border-collapse:collapse;font-size:13px;">'
            '<tr style="background:#fff7ed;"><th style="padding:6px 8px;text-align:left;">#</th>'
            '<th style="padding:6px 8px;text-align:left;">代號 名稱</th>'
            '<th style="padding:6px 8px;text-align:left;">籌碼分</th>'
            '<th style="padding:6px 8px;text-align:right;">30日外資(張)</th>'
            '<th style="padding:6px 8px;text-align:right;">月營收YoY</th>'
            '<th style="padding:6px 8px;text-align:right;">5日 / 距MA20</th>'
            '<th style="padding:6px 8px;text-align:left;">近期新聞</th></tr>'
            + "".join(trs) + "</table>"
            f'<div style="margin:2px 16px 10px;font-size:11px;color:#94a3b8;">'
            f'※ 本表個股為本報依「{esc(sec["name"])}」主題自動篩選整理,非股癌推薦;籌碼/營收/動能為公開資料、'
            f'新聞為自動擷取,均不構成買賣建議。</div>')
    # 看空/中性族群(只列立場,不展開個股)
    others = [s for s in extract.get("sectors", []) if s["stance"] != "看多"]
    if others:
        rows = "".join(f'<div style="margin:3px 0;font-size:13px;">'
                       f'<b style="color:{_stance_color(s["stance"])};">[{esc(s["stance"])}]</b> '
                       f'<b>{esc(s["name"])}</b>　<span style="color:#475569;">{esc(s.get("reasoning", ""))}</span></div>'
                       for s in others)
        parts.append('<h2 style="font-size:16px;margin:18px 16px 6px;color:#475569;">看空/中性族群(僅列立場,不展開個股)</h2>'
                     f'<div style="margin:0 16px;">{rows}</div>')
    parts.append('<div style="margin:18px 16px;padding:12px 14px;background:#f8fafc;border-top:1px solid #e2e8f0;'
                 f'font-size:11px;color:#94a3b8;line-height:1.6;">{esc(_DISCLAIM_FOOT)}</div></div>')
    return "".join(parts)


# ---------- 主流程 ----------
def _build_whitelist() -> dict:
    try:
        wl = mr._fetch_twse_listing_basics() or {}
        log(f"上市白名單 {len(wl)} 檔")
        return wl
    except Exception as e:
        log(f"白名單抓取失敗(本期不展開個股): {str(e)[:120]}")
        return {}


def process_new_episode() -> int:
    cfg = _gooaye_cfg()
    state = load_radar_state()
    found = pdg.find_new_episodes(cfg, state, limit=1)
    if not found:
        log("無股癌新集(或皆已處理)→ 不寄信")
        return 0
    entry, audio_url, dur = found[0]
    guid = str(entry.get("id") or entry.get("link") or entry.get("title") or "")
    title = str(entry.get("title", ""))[:120]
    published = str(entry.get("published", ""))
    log(f"偵測到新集「{title}」({dur:.0f} 分)")

    acc = pdg._accuracy_settings(cfg)
    tmp = Path(f"podcast_radar_{cfg['key']}.mp3")
    try:
        if not pdg.download_audio(audio_url, tmp):
            log("音檔下載失敗 → 不寄信")
            return 0
        transcript = pdg.transcribe_audio(tmp, lang=cfg.get("lang", "zh"),
                                          model_name=acc["whisper"], beam_size=acc["beam"])
    finally:
        tmp.unlink(missing_ok=True)
    if len(transcript) < 500:
        log(f"轉錄過短({len(transcript)} 字)→ 不寄信")
        return 0

    extract = extract_sectors(transcript, acc["summary_model"])
    if not extract.get("sectors"):
        log("未萃取到族群 → 不寄信")
        return 0

    bullish = [s for s in extract["sectors"] if s["stance"] == "看多"][:RADAR_MAX_BULLISH_SECTORS]
    whitelist = _build_whitelist() if bullish else {}
    sector_stocks = []
    for sec in bullish:
        cands = llm_candidate_tickers(sec["name"], sec.get("reasoning", ""), acc["summary_model"])
        codes = validate_tickers(cands, whitelist)
        stocks = enrich_sector(codes, whitelist)
        sector_stocks.append({"sector": sec, "stocks": stocks})
        log(f"族群「{sec['name']}」候選 {len(cands)} → 驗證 {len(codes)} → 取 {len(stocks)}")

    meta = {"title": title, "published": published, "guid": guid}
    html = render_radar_html(meta, extract, sector_stocks, whitelist_ok=bool(whitelist) or not bullish)
    subject = f"📻 股癌雷達 ｜ {mr._to_traditional(title)[:30]}"
    log(f"信件約 {mr._estimated_email_kb(html):.0f}KB;看多族群 {len(bullish)}")

    # DRY_RUN:只輸出預覽、不寄信、不標記(避免抑制日後真正寄送);保留供本機/CI 驗收版面。
    if os.getenv("DRY_RUN"):
        out = Path(os.getenv("RADAR_PREVIEW_PATH", "/tmp/gooaye_radar_preview.html"))
        try:
            out.write_text(html, encoding="utf-8")
            log(f"DRY_RUN:預覽寫入 {out}(不寄信、不標記)")
        except Exception as e:
            log(f"DRY_RUN 預覽寫入失敗: {e}")
        return 0

    if not _deliver(html, subject):
        log("寄信失敗 → 不標記(下次重試,deliver-then-mark)")
        return 1

    # 寄信成功才標記(deliver-then-mark):寫雷達自有 state;晨報讀此檔的 radar_sent_at 去重,
    # 雷達本身不碰 podcast_digest.json(避免與 podcast-digest workflow 競寫)。
    state.setdefault(cfg["key"], {"name": cfg["name"], "episodes": []})
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state[cfg["key"]]["episodes"].insert(0, {
        "guid": guid, "title": title, "published": published,
        "processed_at": now_iso, "radar_sent_at": now_iso,
        "sectors": [{"name": s["name"], "stance": s["stance"]} for s in extract["sectors"]],
    })
    state[cfg["key"]]["episodes"] = state[cfg["key"]]["episodes"][:20]
    save_radar_state(state)
    return 0


def _deliver(html: str, subject: str) -> bool:
    try:
        if RADAR_RECIPIENT:                      # 自訂收件者 → 暫時覆寫 mr.RECIPIENTS
            mr.RECIPIENTS = mr._parse_recipients(RADAR_RECIPIENT)
        mr.send_email(html, subject)
        return True
    except Exception as e:
        log(f"寄信失敗: {str(e)[:150]}")
        return False


def main() -> int:
    try:
        return process_new_episode()
    except Exception as e:
        log(f"雷達執行失敗(不影響其他流程): {str(e)[:200]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
