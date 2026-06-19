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
import re
import sys
import time
from pathlib import Path
from typing import Optional

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
# 市值/流動性門檻:排序前剔除「市值過小、實際難買進」的個股(這是過濾不是加分;留太少則自動放寬)
RADAR_MIN_MARKET_CAP = float(os.getenv("RADAR_MIN_MARKET_CAP", "3000000000"))   # 預設 30 億元
# FinMind(教育/非商業用途)token,留空則用免費無 token 額度;僅對最終 Top 名單抓外資持股比率
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "").strip()
_STANCES = ("看多", "看空", "中性")


def log(msg: str) -> None:
    print(f"[radar] {msg}", file=sys.stderr)


# 表情符號/雜訊符號移除(使用者要求信中不要 emoji;CMoney 新聞標題常夾 😇💕🌟、股癌集名帶 🌼)。
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF\U00002190-\U000021FF️‍⃣™ℹ⭐✅❌]",
    flags=re.UNICODE)


def _strip_emoji(s: str) -> str:
    return _EMOJI_RE.sub("", str(s or "")).strip()


def _clean(s: str) -> str:
    """顯示用文字一律:簡轉繁(opencc)+ 去 emoji。"""
    return _strip_emoji(mr._to_traditional(s))


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
    t = _clean
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
        "你是台股產業研究員。給你一個族群、以及『股癌本集對該族群的具體多頭邏輯/子題』,"
        "請列出在台灣『上市』、且**真正契合此具體子題**的代表性個股(不是把整個族群亂槍打鳥)。"
        "【鐵則】只列上市股(排除 ETF、興櫃);只收與該子題直接相關者,沾邊/題材無關者一律不列;"
        "不確定代號就把 code 留空字串、只給 name,嚴禁編造代號;一律台灣繁體中文。"
        "每檔附 theme_fit:一句(≤25字)說明它如何吻合此子題。"
        "只輸出 JSON:{\"candidates\":[{\"code\":\"4位數字或空\",\"name\":\"公司簡稱\","
        f"\"theme_fit\":\"契合此子題的理由\"}}]}},最多 {CAND_PER_SECTOR} 檔。"
    )
    try:
        raw = _deepseek_json(prompt, f"族群:{sector_name}\n股癌的具體多頭邏輯/子題:{reasoning}", model)
    except Exception as e:
        log(f"族群「{sector_name}」候選生成失敗: {str(e)[:100]}")
        return []
    out = []
    for c in (raw.get("candidates") or [])[:CAND_PER_SECTOR]:
        code = "".join(ch for ch in str(c.get("code", "")) if ch.isdigit())
        name = str(c.get("name", "")).strip()
        if code or name:
            out.append({"code": code, "name": name,
                        "theme_fit": _clean(str(c.get("theme_fit", "")).strip())})
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


def _name_to_code_map(whitelist: dict) -> dict:
    m = {}
    for code, meta in whitelist.items():
        nm = mr._norm_podcast_point(meta.get("name", ""))
        if nm:
            m.setdefault(nm, code)
    return m


def _resolve_code(cand: dict, whitelist: dict, name_to_code: dict):
    """單一候選 → 有效代號或 None。代號存在且名稱相符→用代號;否則以名稱反查救回(信任名稱)。"""
    code, name = cand.get("code", ""), cand.get("name", "")
    if len(code) == 4 and code in whitelist and _names_match(name, whitelist[code].get("name", "")):
        return code
    return name_to_code.get(mr._norm_podcast_point(name))


def validate_tickers(candidates: list[dict], whitelist: dict) -> list[str]:
    """whitelist = {code:{name,...}}(全上市)。回經四關驗證的有效代號(去重保序),fail-closed。
    四關:格式 → 存在於白名單(擋幻覺/下市)→ 官方名一致(擋張冠李戴)→ 名稱反查救回。
    代號與名稱衝突時『信任名稱』(以名稱反查正確代號),反查不到則丟棄,絕不誤收衝突代號。"""
    if not whitelist:                       # 白名單抓取失敗 → 不展開個股(由呼叫端決定降級)
        return []
    name_to_code = _name_to_code_map(whitelist)
    valid, seen = [], set()
    for c in candidates:
        chosen = _resolve_code(c, whitelist, name_to_code)
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
    """radar_score(偏基本面/估值,對齊股癌的波段視角;不計短線 5 日動能):
      基本面 30%(營收YoY 0.6 + 營益率 0.4)
    + 估值   15%(P/E 池內越低越好 0.6 + 殖利率 0.4;虧損/無 P/E 給保守 0.3)
    + 籌碼   25%(smart_money 站隊分)
    + 中期法人 15%(30 日外資累積)
    + 未過熱 15%(距 MA20 越遠越扣,逾 30% 歸零)。
    依晨報因子 IC 回測:短線技術/籌碼預測力弱、約 20 日(波段)才顯著、基本面較持久,
    故權重往基本面/估值傾斜(原版籌碼+動能各 40/20,易讓暴衝過熱股霸榜)。"""
    if not entries:
        return []
    sm = {e["code"]: _safe(e.get("smart_money", {}).get("score")) for e in entries}
    inst = {e["code"]: _safe(e.get("foreign_30d_lot")) for e in entries}
    revy = {e["code"]: _safe(e.get("rev_yoy_pct")) for e in entries}
    opm = {e["code"]: _safe(e.get("op_margin")) for e in entries}
    yld = {e["code"]: _safe(e.get("yield_pct")) for e in entries}
    per_pos = {e["code"]: _safe(e.get("per")) for e in entries
               if isinstance(_safe(e.get("per")), (int, float)) and _safe(e.get("per")) > 0}
    for e in entries:
        c = e["code"]
        fund = 0.6 * _norm01(revy, c) + 0.4 * _norm01(opm, c)
        per_v = _safe(e.get("per"))
        per_sc = (1 - _norm01(per_pos, c)) if (isinstance(per_v, (int, float)) and per_v > 0) else 0.3
        val = 0.6 * per_sc + 0.4 * _norm01(yld, c)
        dist = abs(_safe(e.get("ma20_dist_pct")) or 0)
        not_overheated = max(0.0, 1 - min(dist, 30) / 30)
        e["radar_score"] = round(
            100 * (0.30 * fund + 0.15 * val + 0.25 * (sm.get(c) or 0) / 100
                   + 0.15 * _norm01(inst, c) + 0.15 * not_overheated), 1)
    entries.sort(key=lambda e: (-(e.get("radar_score") or 0),
                                -(_safe(e.get("foreign_30d_lot")) or 0), e["code"]))
    return entries[:top_n]


def _safe(v):
    return v if isinstance(v, (int, float)) else None


def _pct(v):
    return ("%+.1f%%" % v) if isinstance(v, (int, float)) else "—"


def _lot(v):
    return f"{int(v):+,d} 張" if isinstance(v, (int, float)) else "—"


def _eok(v):
    """元 → 億元(顯示用)。"""
    v = _safe(v)
    return f"{v / 1e8:,.0f} 億" if isinstance(v, (int, float)) and v else "—"


def _radar_tradeable(e: dict) -> bool:
    """市值/流動性門檻:剔除『市值過小、實際難買進』者(過濾,非加分)。
    資料缺漏一律放行(不因缺資料誤殺)。"""
    if e.get("liquidity_eligible") is False:
        return False
    mc = _safe(e.get("market_cap"))
    if mc is not None and mc < RADAR_MIN_MARKET_CAP:
        return False
    return True


def _stock_verdict(e: dict) -> str:
    """一句話綜合資料面強弱(非買賣建議):點出最強/最弱面 + 過熱警示,協助判斷孰優孰劣。"""
    pos, neg = [], []
    fs, ins = _safe(e.get("foreign_streak")) or 0, _safe(e.get("invest_streak")) or 0
    f30 = _safe(e.get("foreign_30d_lot"))
    if fs >= 2 and ins >= 2:
        pos.append("外資投信同步連買")
    elif fs >= 2:
        pos.append(f"外資連買{int(fs)}日")
    if isinstance(f30, (int, float)) and f30 <= -1000:
        neg.append("30日外資大賣超")
    sm = (e.get("smart_money") or {}).get("score")
    if isinstance(sm, (int, float)) and sm <= 10:
        neg.append("籌碼鬆動")
    rev = _safe(e.get("rev_yoy_pct"))
    if isinstance(rev, (int, float)) and rev >= 20:
        pos.append(f"營收年增{rev:.0f}%")
    elif isinstance(rev, (int, float)) and rev < 0:
        neg.append(f"營收衰退{abs(rev):.0f}%")
    epsg = _safe(e.get("eps_yoy_pct"))
    if isinstance(epsg, (int, float)) and epsg >= 30:
        pos.append(f"EPS年增{epsg:.0f}%")
    elif isinstance(epsg, (int, float)) and epsg < 0:
        neg.append(f"EPS年減{abs(epsg):.0f}%")
    opm = _safe(e.get("op_margin"))
    if isinstance(opm, (int, float)) and opm >= 20:
        pos.append(f"營益率{opm:.0f}%佳")
    roe = _safe(e.get("roe_q"))
    if isinstance(roe, (int, float)) and roe >= 5:
        pos.append(f"單季ROE{roe:.0f}%佳")
    mh = _safe(e.get("major_holder_pct"))
    if isinstance(mh, (int, float)) and mh >= 65:
        pos.append(f"大戶持股{mh:.0f}%集中")
    fhp = _safe(e.get("foreign_hold_pct"))
    if isinstance(fhp, (int, float)) and fhp >= 50:
        pos.append(f"外資持股{fhp:.0f}%高")
    per = _safe(e.get("per"))
    if isinstance(per, (int, float)) and 0 < per <= 15:
        pos.append(f"本益比{per:.0f}偏低")
    elif isinstance(per, (int, float)) and per >= 40:
        neg.append(f"本益比{per:.0f}偏高")
    yld = _safe(e.get("yield_pct"))
    if isinstance(yld, (int, float)) and yld >= 4:
        pos.append(f"殖利率{yld:.1f}%")
    dpct = _safe(e.get("director_pct"))
    if isinstance(dpct, (int, float)) and dpct >= 30:
        pos.append(f"董監持股{dpct:.0f}%高")
    ppct = _safe(e.get("pledge_pct"))
    if isinstance(ppct, (int, float)) and ppct >= 30:
        neg.append(f"董監設質{ppct:.0f}%偏高")
    achv = _safe(e.get("forecast_achv_pct"))
    if isinstance(achv, (int, float)) and achv >= 100:
        pos.append(f"財測達成{achv:.0f}%")
    elif isinstance(achv, (int, float)) and achv < 90:
        neg.append(f"財測達成僅{achv:.0f}%")
    dist = _safe(e.get("ma20_dist_pct"))
    if isinstance(dist, (int, float)) and dist >= 20:
        neg.append(f"距MA20+{dist:.0f}%明顯過熱、追高風險")
    elif isinstance(dist, (int, float)) and dist >= 10:
        neg.append(f"距MA20+{dist:.0f}%略過熱")
    score = e.get("radar_score") or 0
    tone = "資料面偏強" if score >= 55 else ("資料面偏弱" if score < 35 else "資料面中性")
    bits = []
    if pos:
        bits.append("優:" + "、".join(pos))
    if neg:
        bits.append("弱:" + "、".join(neg))
    return f"{tone}({'；'.join(bits)})" if bits else tone


def _stock_news_oneliner(code: str, name: str) -> str:
    try:
        feed = mr._feedparser_parse_url_with_timeout(mr._gnews_rss(f"{name} {code}", when="2d"))
        for e in (getattr(feed, "entries", None) or [])[:1]:
            title = str(e.get("title", "")).strip()
            if title:
                return _clean(title)
    except Exception:
        pass
    return "—"


def sector_trend_oneliner(sector_name: str, reasoning: str, model: str) -> str:
    """產業趨勢維度(軟訊號):用類股關鍵字抓近 7 天新聞標題 → DeepSeek 濃縮成一句『產業近況/趨勢』。
    抓不到新聞或無 LLM 則回空字串(不顯示);非投資建議,且要求不得杜撰標題沒有的事實。"""
    try:
        feed = mr._feedparser_parse_url_with_timeout(
            mr._gnews_rss(f"{sector_name} 產業 趨勢 需求", when="7d"))
        heads = [str(e.get("title", "")).strip()
                 for e in (getattr(feed, "entries", None) or [])[:6]]
        heads = [h for h in heads if h]
        if not heads:
            return ""
        raw = _deepseek_json(
            "你是台股產業分析助理。依據以下某類股近期新聞標題,用繁體中文寫『一句話』(40字內),"
            "點出該產業近況/趨勢方向(需求、報價、供需、政策動向)。只回 JSON {\"trend\":\"...\"};"
            "不得提及買賣或目標價,不得杜撰新聞標題沒有的事實,若標題訊息不足就寫『近期新聞有限』。",
            f"類股:{sector_name}\n股癌看多邏輯:{reasoning}\n新聞標題:\n- " + "\n- ".join(heads),
            model)
        return _clean(str(raw.get("trend", "")).strip())[:60]
    except Exception:
        return ""


# ---------- 估值/獲利率(TWSE OpenAPI,上市全市場,一次取)----------
_TWSE_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _f(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _twse_json(url: str) -> list:
    try:
        r = requests.get(url, timeout=20, headers=_TWSE_HEADERS)
        r.raise_for_status()
        return r.json() or []
    except Exception as e:
        log(f"TWSE {url.rsplit('/', 1)[-1]} 失敗: {str(e)[:80]}")
        return []


def fetch_valuation() -> dict:
    """{code: {per, yield_pct, pbr}} —— BWIBBU_ALL(上市個股本益比/殖利率/股價淨值比,一次全市場)。"""
    out = {}
    for row in _twse_json("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"):
        c = str(row.get("Code", "")).strip()
        if len(c) == 4 and c.isdigit():
            out[c] = {"per": _f(row.get("PEratio")), "yield_pct": _f(row.get("DividendYield")),
                      "pbr": _f(row.get("PBratio"))}
    return out


def fetch_margins() -> dict:
    """{code: {gross_margin, op_margin, net_margin}} —— 營益分析彙總(t187ap17_L,官方直接給率,
    比自行用綜合損益表推算更乾淨;最新季,全市場一次取)。"""
    out = {}
    for row in _twse_json("https://openapi.twse.com.tw/v1/opendata/t187ap17_L"):
        c = str(row.get("公司代號", "")).strip()
        if not (len(c) == 4 and c.isdigit()):
            continue
        out[c] = {
            "gross_margin": _f(row.get("毛利率(%)(營業毛利)/(營業收入)")),
            "op_margin": _f(row.get("營業利益率(%)(營業利益)/(營業收入)")),
            "net_margin": _f(row.get("稅後純益率(%)(稅後純益)/(營業收入)")),
        }
    return out


def fetch_roe() -> dict:
    """{code: {roe_q, roa_q}} —— 單季 ROE/ROA = 稅後淨利(t187ap14_L)÷ 權益/資產總額
    (t187ap07_L_ci 資產負債表);皆為『單季』(未年化),當品質參考、不進計分。"""
    ni = {}
    for row in _twse_json("https://openapi.twse.com.tw/v1/opendata/t187ap14_L"):
        c = str(row.get("公司代號", "")).strip()
        if len(c) == 4 and c.isdigit():
            ni[c] = _f(row.get("稅後淨利"))
    out = {}
    for row in _twse_json("https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci"):
        c = str(row.get("公司代號", "")).strip()
        if not (len(c) == 4 and c.isdigit()):
            continue
        n = ni.get(c)
        if n is None:
            continue
        eq, asset = _f(row.get("權益總額")), _f(row.get("資產總額"))
        out[c] = {
            "roe_q": round(n / eq * 100, 1) if eq and eq > 0 else None,
            "roa_q": round(n / asset * 100, 1) if asset and asset > 0 else None,
        }
    return out


def fetch_foreign_holding(codes: list[str]) -> dict:
    """{code: {foreign_hold_pct}} —— 外資持股比率(FinMind TaiwanStockShareholding,取最新一筆)。
    教育/非商業用途;只對最終 Top 名單(少量代號)查、免 token 即可,設 FINMIND_TOKEN 可拉高額度;
    任何一檔失敗就略過該檔,不拖垮整封。"""
    out = {}
    start = (dt.date.today() - dt.timedelta(days=45)).isoformat()
    for c in codes:
        try:
            params = {"dataset": "TaiwanStockShareholding", "data_id": c, "start_date": start}
            if FINMIND_TOKEN:
                params["token"] = FINMIND_TOKEN
            r = requests.get("https://api.finmindtrade.com/api/v4/data",
                             params=params, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            data = (r.json() or {}).get("data") or []
            if data:
                pct = _f(data[-1].get("ForeignInvestmentSharesRatio"))
                if pct is not None:
                    out[c] = {"foreign_hold_pct": pct}
        except Exception:
            continue
    return out


def fetch_eps_growth(codes: list[str]) -> dict:
    """{code: {eps_latest, eps_latest_q, eps_yoy_pct}} —— EPS 年增率(FinMind 財報季 EPS 序列,
    最新季 vs 去年同季)。教育/非商業用途;只對最終 Top 名單查、token 選填、失敗略過。"""
    out = {}
    start = (dt.date.today() - dt.timedelta(days=550)).isoformat()   # 至少涵蓋 5 季
    for c in codes:
        try:
            params = {"dataset": "TaiwanStockFinancialStatements", "data_id": c, "start_date": start}
            if FINMIND_TOKEN:
                params["token"] = FINMIND_TOKEN
            r = requests.get("https://api.finmindtrade.com/api/v4/data",
                             params=params, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            data = (r.json() or {}).get("data") or []
            eps = sorted(((str(row.get("date")), _f(row.get("value")))
                          for row in data
                          if row.get("type") == "EPS" and _f(row.get("value")) is not None),
                         key=lambda x: x[0])
            if not eps:
                continue
            latest_d, latest_v = eps[-1]
            rec = {"eps_latest": latest_v, "eps_latest_q": latest_d}
            yago = (str(int(latest_d[:4]) - 1) + latest_d[4:]) if latest_d[:4].isdigit() else ""
            prior = next((v for d, v in eps if d == yago), None)
            if prior:   # 去年同季存在且非 0
                rec["eps_yoy_pct"] = round((latest_v - prior) / abs(prior) * 100, 1)
            out[c] = rec
        except Exception:
            continue
    return out


def _roc_md(s) -> str:
    """民國 yyyymmdd(如 1150709)→ MM/DD。"""
    s = str(s or "").strip()
    return f"{s[3:5]}/{s[5:7]}" if len(s) >= 7 and s.isdigit() else ""


def _safe_fetch(fn, label: str) -> dict:
    """單一官方來源抓取失敗時降級為空 dict,不拖垮整封雷達。"""
    try:
        return fn() or {}
    except Exception as e:
        log(f"{label}抓取失敗(本期略過此欄): {str(e)[:120]}")
        return {}


def fetch_exdiv_calendar() -> dict:
    """{code: {exdiv_md, exdiv_type, cash_div}} —— 上市除權息預告(TWT48U_ALL)。"""
    out = {}
    for row in _twse_json("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"):
        c = str(row.get("Code", "")).strip()
        if len(c) == 4 and c.isdigit():
            out[c] = {"exdiv_md": _roc_md(row.get("Date")),
                      "exdiv_type": str(row.get("Exdividend", "")).strip(),
                      "cash_div": _f(row.get("CashDividend"))}
    return out


def fetch_dividends() -> dict:
    """{code: {year, cash_div, stock_div, progress}} —— 上市股利分派(t187ap45_L)。
    依「最新股利年度」彙總該年度各期(季配/半年配)現金股利與配股,避免只取單季而低估;
    year 為民國股利年度,卡片會標明所屬年度。"""
    by_code = {}
    for row in _twse_json("https://openapi.twse.com.tw/v1/opendata/t187ap45_L"):
        c = str(row.get("公司代號", "")).strip()
        if not (len(c) == 4 and c.isdigit()):
            continue
        yr = str(row.get("股利年度", "")).strip()
        if not yr:
            # 後備:由「股利所屬期間」起日取民國年(如 1140101~1141231 → 114)
            period = str(row.get("股利所屬期間", "")).strip()
            yr = period[:3] if len(period) >= 3 and period[:3].isdigit() else ""
        if not yr:
            continue
        cash = sum((_f(row.get(k)) or 0) for k in (
            "股東配發-盈餘分配之現金股利(元/股)", "股東配發-法定盈餘公積發放之現金(元/股)",
            "股東配發-資本公積發放之現金(元/股)"))
        stock = sum((_f(row.get(k)) or 0) for k in (
            "股東配發-盈餘轉增資配股(元/股)", "股東配發-法定盈餘公積轉增資配股(元/股)",
            "股東配發-資本公積轉增資配股(元/股)"))
        rec = by_code.setdefault(c, {}).setdefault(yr, {"cash": 0.0, "stock": 0.0, "progress": ""})
        rec["cash"] += cash
        rec["stock"] += stock
        prog = str(row.get("決議（擬議）進度", "")).strip()
        if prog:
            rec["progress"] = prog
    out = {}
    for c, years in by_code.items():
        yr = max(years.keys())   # 最新股利年度(同長度民國年字串字典序即年序)
        rec = years[yr]
        out[c] = {"div_year": yr, "cash_div": round(rec["cash"], 2),
                  "stock_div": round(rec["stock"], 2), "progress": rec["progress"]}
    return out


def fetch_insider(whitelist: dict) -> dict:
    """{code: {director_pct, pledge_pct}} —— 董監事持股(t187ap11_L 逐人 → 依代號彙總),
    佔比用 whitelist 的已發行股數;設質比例 = 設質/持股。"""
    agg = {}
    for row in _twse_json("https://openapi.twse.com.tw/v1/opendata/t187ap11_L"):
        c = str(row.get("公司代號", "")).strip()
        if not (len(c) == 4 and c.isdigit()):
            continue
        a = agg.setdefault(c, {"held": 0.0, "pledged": 0.0})
        a["held"] += _f(row.get("目前持股")) or 0
        a["pledged"] += _f(row.get("設質股數")) or 0
    out = {}
    for c, a in agg.items():
        shares = _f((whitelist.get(c) or {}).get("shares"))
        out[c] = {
            "director_pct": round(a["held"] / shares * 100, 1) if shares and shares > 0 else None,
            "pledge_pct": round(a["pledged"] / a["held"] * 100, 1) if a["held"] > 0 else None,
        }
    return out


def fetch_guidance() -> dict:
    """{code: {forecast_achv_pct}} —— 財測達成情形(t187ap15_L)。
    ⚠ 全市場僅約 6-8 家有發正式財測,覆蓋極低,雷達股多半取不到(有才顯示)。"""
    out = {}
    for row in _twse_json("https://openapi.twse.com.tw/v1/opendata/t187ap15_L"):
        c = str(row.get("公司代號", "")).strip()
        if not (len(c) == 4 and c.isdigit()):
            continue
        actual = _f(row.get("截至該季經會計師查核或核閱數"))
        fc = str(row.get("截至該季綜合損益預測數", "")).strip()
        if "~" in fc:
            parts = [p for p in (_f(x) for x in fc.split("~")) if p is not None]
            mid = sum(parts) / 2 if len(parts) == 2 else None
        else:
            mid = _f(fc)
        if actual is not None and mid and mid > 0:
            out[c] = {"forecast_achv_pct": round(actual / mid * 100, 0)}
    return out


def enrich_sector(codes: list[str], whitelist: dict,
                  candidates: Optional[list] = None, extra: Optional[dict] = None) -> list[dict]:
    """驗證後代號 → fetch_tw0050_snapshot 取籌碼/營收/動能,再併入估值(P/E/殖利率/P/B)、
    營益率、累計年增與 theme_fit → 排序前 N → 補新聞。extra=共用的全市場估值/獲利率/營收快取。"""
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
    code_set = set(codes)
    entries = [e for e in (snap or []) if e.get("code") in code_set]
    extra = extra or {}
    val, margins, rev = extra.get("val") or {}, extra.get("margins") or {}, extra.get("rev") or {}
    div, exdiv = extra.get("div") or {}, extra.get("exdiv") or {}
    insider, guidance, roe = extra.get("insider") or {}, extra.get("guidance") or {}, extra.get("roe") or {}
    themefit = {}
    if candidates:
        ntc = _name_to_code_map(whitelist)
        for cand in candidates:
            rc = _resolve_code(cand, whitelist, ntc)
            if rc and cand.get("theme_fit"):
                themefit.setdefault(rc, cand["theme_fit"])
    for e in entries:
        c = e["code"]
        e.update(val.get(c) or {})            # per / yield_pct / pbr
        e.update(margins.get(c) or {})        # gross_margin / op_margin / net_margin
        e.update(div.get(c) or {})            # cash_div / stock_div / progress
        e.update(insider.get(c) or {})        # director_pct / pledge_pct
        e.update(guidance.get(c) or {})       # forecast_achv_pct
        e.update(roe.get(c) or {})            # roe_q / roa_q
        if c in rev:
            e["rev_cum_yoy_pct"] = rev[c].get("cum_yoy_pct")
        if c in exdiv:
            e["exdiv_md"] = exdiv[c].get("exdiv_md")
            e["exdiv_type"] = exdiv[c].get("exdiv_type")
        # 市值:snapshot 未帶時,用 已發行股數 × 收盤 自算(元)
        if not _safe(e.get("market_cap")):
            shares, close = _f((whitelist.get(c) or {}).get("shares")), _safe(e.get("close"))
            if shares and close:
                e["market_cap"] = shares * close
        e["theme_fit"] = themefit.get(c, "")
    # 排序前先剔除「市值過小/流動性差、實際難買進」者;濾到不足 top_n 則放寬回全部(不寧缺勿濫)
    eligible = [e for e in entries if _radar_tradeable(e)]
    dropped = len(entries) - len(eligible)
    if len(eligible) >= TOP_N_PER_SECTOR:
        if dropped:
            log(f"  門檻濾除 {dropped} 檔(市值<{RADAR_MIN_MARKET_CAP/1e8:.0f}億/流動性不足)")
        pool = eligible
    else:
        pool = entries
    top = rank_top5(pool)
    for e in top:
        e["_news"] = _stock_news_oneliner(e["code"], e.get("name", ""))
    # 僅對最終 Top 名單補 FinMind 資料(少量代號、教育用途):外資持股比率 + EPS 年增率
    top_codes = [e["code"] for e in top]
    fh = _safe_fetch(lambda: fetch_foreign_holding(top_codes), "外資持股(FinMind)")
    eg = _safe_fetch(lambda: fetch_eps_growth(top_codes), "EPS年增(FinMind)")
    for e in top:
        e.update(fh.get(e["code"]) or {})     # foreign_hold_pct
        e.update(eg.get(e["code"]) or {})     # eps_latest / eps_latest_q / eps_yoy_pct
    return top


# ---------- 渲染 ----------
_DISCLAIM_TOP = (
    "重要聲明:本信個股清單為『本報依股癌本集所談族群、以程式自動整理』,"
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
    t = _clean
    # 防禦性:顯示前一律轉繁(opencc)+ 去 emoji,即使上游漏處理也不外漏簡體/表情符號。
    extract = {
        "episode_summary": t(extract.get("episode_summary", "")),
        "market_view": t(extract.get("market_view", "")),
        "key_takeaways": [t(p) for p in (extract.get("key_takeaways") or [])],
        "sectors": [{**s, "name": t(s.get("name", "")), "reasoning": t(s.get("reasoning", ""))}
                    for s in (extract.get("sectors") or [])],
    }
    sector_stocks = [{"sector": {**b["sector"], "name": t(b["sector"].get("name", "")),
                                 "reasoning": t(b["sector"].get("reasoning", ""))},
                      "stocks": b.get("stocks") or [],
                      "trend": t(b.get("trend") or "")} for b in (sector_stocks or [])]
    parts = [
        '<div style="font-family:-apple-system,BlinkMacSystemFont,\'PingFang TC\',\'Microsoft JhengHei\',sans-serif;'
        'max-width:680px;margin:0 auto;background:#fff;color:#0f172a;">',
        '<div style="background:linear-gradient(135deg,#7c2d12,#ea580c);color:#fff;padding:22px 24px;">'
        '<div style="font-size:13px;letter-spacing:2px;opacity:.85;">GOOAYE RADAR・股癌雷達</div>'
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
        if blk.get("trend"):
            head += (f'<div style="margin:0 16px 6px;font-size:13px;color:#0369a1;">'
                     f'產業近況(近一週新聞 AI 摘要):{esc(_clean(blk["trend"]))}</div>')
        if not stocks:
            parts.append(head + '<div style="margin:0 16px;font-size:13px;color:#94a3b8;">'
                                '(此族群未取得通過驗證的上市個股,僅列立場)</div>')
            continue
        cards = []
        for i, e in enumerate(stocks, 1):
            sm = e.get("smart_money", {}) or {}
            fs, ins = _safe(e.get("foreign_streak")), _safe(e.get("invest_streak"))
            d20 = _safe(e.get("ma20_dist_pct"))
            score = e.get("radar_score")
            badge = ('<span style="background:#dc2626;color:#fff;font-size:11px;padding:1px 7px;'
                     'border-radius:8px;margin-left:6px;white-space:nowrap;">本族群資料面首選</span>'
                     if i == 1 else "")
            chip_bits = []
            if isinstance(fs, (int, float)) and fs:
                chip_bits.append(f"外資連{'買' if fs > 0 else '賣'}{abs(int(fs))}日")
            if isinstance(ins, (int, float)) and ins:
                chip_bits.append(f"投信連{'買' if ins > 0 else '賣'}{abs(int(ins))}日")
            chip_line = "、".join(chip_bits) or "—"
            heat = (' <span style="color:#b45309;">(明顯過熱)</span>'
                    if isinstance(d20, (int, float)) and d20 >= 20
                    else ' <span style="color:#b45309;">(略過熱)</span>'
                    if isinstance(d20, (int, float)) and d20 >= 10 else "")
            # 官方來源:股利/除權息預告 + 董監持股/設質 + 財測達成(取得到才顯示)
            gov_bits = []
            cd, sd = _safe(e.get("cash_div")), _safe(e.get("stock_div"))
            cd = cd if isinstance(cd, (int, float)) else 0
            sd = sd if isinstance(sd, (int, float)) else 0
            if cd or sd:   # 純配股(現金=0)也要顯示
                amt = []
                if cd:
                    amt.append(f"現金股利 {cd} 元")
                if sd:
                    amt.append(f"配股 {sd} 元")
                yr = e.get("div_year")
                seg = (f"{yr}年度" if yr else "") + "+".join(amt)
                if e.get("progress"):
                    seg += f"({t(str(e.get('progress')))})"
                gov_bits.append(seg)
            if e.get("exdiv_md"):
                gov_bits.append(f"{t(str(e.get('exdiv_type') or '除息'))}日 {e.get('exdiv_md')}")
            dpct2, ppct2 = _safe(e.get("director_pct")), _safe(e.get("pledge_pct"))
            if isinstance(dpct2, (int, float)):
                seg = f"董監持股 {dpct2}%"
                if isinstance(ppct2, (int, float)) and ppct2:
                    seg += f"、設質 {ppct2}%"
                gov_bits.append(seg)
            achv2 = _safe(e.get("forecast_achv_pct"))
            if isinstance(achv2, (int, float)):
                gov_bits.append(f"財測達成 {achv2:.0f}%")
            gov_line = (f'<div style="font-size:12px;color:#475569;margin-top:4px;line-height:1.8;">'
                        f'官方:{esc("　".join(gov_bits))}</div>') if gov_bits else ""
            # 市值(自算或 snapshot)+ 第二籌碼列(大戶/外資持股、融資餘額、空方回補,有才列)
            mc_str = f'　市值 {_eok(e.get("market_cap"))}' if _safe(e.get("market_cap")) else ""
            chip2 = []
            mh = _safe(e.get("major_holder_pct"))
            if mh is not None:
                chip2.append(f"大戶持股 {mh:.0f}%")
            fhp = _safe(e.get("foreign_hold_pct"))
            if fhp is not None:
                chip2.append(f"外資持股 {fhp:.0f}%")
            mbl = _safe(e.get("margin_balance_lot"))
            if mbl:
                chip2.append(f"融資餘額 {int(mbl):,}張")
            scr = _safe(e.get("short_cover_ratio"))
            if scr is not None:
                chip2.append(f"空方回補比 {scr}")
            chip2_line = (f'　　{esc("　".join(chip2))}<br>') if chip2 else ""
            # 基本面延伸:淨利率 + 單季 ROE(有才列)
            nm = _safe(e.get("net_margin"))
            roe = _safe(e.get("roe_q"))
            epsg = _safe(e.get("eps_yoy_pct"))
            base_ext = ""
            if nm is not None:
                base_ext += f"／淨利率 {nm:.1f}%"
            if roe is not None:
                base_ext += f"　單季ROE {roe:.1f}%"
            if epsg is not None:
                base_ext += f"　EPS年增 {epsg:+.0f}%"
            cards.append(
                '<div style="border:1px solid #e2e8f0;border-radius:10px;margin:8px 16px;padding:10px 12px;">'
                f'<div style="font-size:15px;font-weight:700;color:#0f172a;">#{i} '
                f'{esc(str(e.get("code", "")))} {esc(t(str(e.get("name", ""))))}{badge}'
                f'<span style="float:right;font-size:12px;color:#64748b;font-weight:400;">綜合分 '
                f'{score if score is not None else "—"}</span></div>'
                f'<div style="font-size:13px;color:#334155;margin-top:3px;">收 '
                f'{_safe(e.get("close")) if _safe(e.get("close")) is not None else "—"} '
                f'({_pct(_safe(e.get("day_pct")))}){mc_str}　籌碼分 {int(sm.get("score") or 0)} '
                f'{esc(t(str(sm.get("tag", ""))))}</div>'
                f'<div style="font-size:12px;color:#475569;margin-top:4px;line-height:1.8;">'
                f'籌碼:{esc(chip_line)}　30日外資 {_lot(_safe(e.get("foreign_30d_lot")))}／投信 '
                f'{_lot(_safe(e.get("invest_30d_lot")))}　大戶持股週變 {_pct(_safe(e.get("tdcc_wow_pct")))}<br>'
                f'{chip2_line}'
                f'基本面:月營收 YoY {_pct(_safe(e.get("rev_yoy_pct")))}(MoM {_pct(_safe(e.get("rev_mom_pct")))}'
                f'、累計 {_pct(_safe(e.get("rev_cum_yoy_pct")))})'
                f'　毛利率 {_pct(_safe(e.get("gross_margin")))}／營益率 {_pct(_safe(e.get("op_margin")))}{base_ext}'
                f'　EPS {_safe(e.get("eps")) if _safe(e.get("eps")) is not None else "—"}<br>'
                f'估值:P/E {_safe(e.get("per")) if _safe(e.get("per")) is not None else "—"}'
                f'　殖利率 {_pct(_safe(e.get("yield_pct")))}'
                f'　P/B {_safe(e.get("pbr")) if _safe(e.get("pbr")) is not None else "—"}<br>'
                f'動能:近5日 {_pct(_safe(e.get("pct_5d")))}　距MA20 {_pct(d20)}{heat}</div>'
                + gov_line
                + (f'<div style="font-size:12px;color:#7c2d12;margin-top:4px;">'
                   f'符合子題:{esc(t(e.get("theme_fit", "")))}</div>' if e.get("theme_fit") else "")
                + f'<div style="font-size:13px;color:#0f172a;margin-top:6px;">'
                f'<b>雷達評語:</b>{esc(_stock_verdict(e))}</div>'
                f'<div style="font-size:12px;color:#64748b;margin-top:3px;">近期新聞:{esc(_clean(e.get("_news") or "—"))}</div>'
                '</div>')
        parts.append(
            head + "".join(cards) +
            '<div style="margin:2px 16px 12px;font-size:11px;color:#94a3b8;line-height:1.7;">'
            '※ <b>排名 = 綜合資料面強弱</b>(基本面 30%〔營收+營益率〕＋估值 15%〔P/E+殖利率〕＋籌碼 25%＋'
            '30日法人 15%＋未過熱 15%;偏基本面/估值以對齊波段視角,過熱會扣分);排序前已先<b>剔除市值過小/'
            '流動性不足</b>(實際難買進)者。淨利率/單季ROE/大戶持股/外資持股/融資餘額為新增<b>參考欄位,'
            '尚未計入分數</b>(計分權重之變更須先經回測驗證)。<b>#1 為資料面相對最強</b>,但這是研究排序、'
            f'<b>非買賣建議</b>,仍須自行評估題材與基本面。個股為本報依「{esc(sec["name"])}」主題自動整理、非股癌推薦。</div>')
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
    # 全市場估值/獲利率/月營收/官方來源一次抓、跨族群共用(免每族群重打)
    extra = {}
    if bullish and whitelist:
        extra = {"val": fetch_valuation(), "margins": fetch_margins(),
                 "rev": mr.fetch_tw_monthly_revenue(),
                 "exdiv": _safe_fetch(fetch_exdiv_calendar, "除權息預告"),
                 "div": _safe_fetch(fetch_dividends, "股利分派"),
                 "insider": _safe_fetch(lambda: fetch_insider(whitelist), "董監持股"),
                 "guidance": _safe_fetch(fetch_guidance, "財測達成"),
                 "roe": _safe_fetch(fetch_roe, "ROE/ROA")}
    sector_stocks = []
    for sec in bullish:
        cands = llm_candidate_tickers(sec["name"], sec.get("reasoning", ""), acc["summary_model"])
        codes = validate_tickers(cands, whitelist)
        stocks = enrich_sector(codes, whitelist, candidates=cands, extra=extra)
        trend = sector_trend_oneliner(sec["name"], sec.get("reasoning", ""), acc["summary_model"])
        sector_stocks.append({"sector": sec, "stocks": stocks, "trend": trend})
        log(f"族群「{sec['name']}」候選 {len(cands)} → 驗證 {len(codes)} → 取 {len(stocks)}")

    meta = {"title": title, "published": published, "guid": guid}
    html = render_radar_html(meta, extract, sector_stocks, whitelist_ok=bool(whitelist) or not bullish)
    subject = f"股癌雷達 ｜ {_clean(title)[:30]}"
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
