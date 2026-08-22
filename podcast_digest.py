# -*- coding: utf-8 -*-
"""Podcast 重點摘要產生器(獨立於晨報主流程)。

流程:iTunes Search 解析 RSS → 抓最新集 → 未處理且 48h 內的新集 →
下載 mp3 → faster-whisper 本地轉錄(免費,GitHub Actions CPU)→
DeepSeek 摘要(repo 既有 key,不依賴 Gemini)→
寫入 state/podcast_digest.json(git push 交給 workflow)。

晨報(morning_report.py)只讀 state JSON 渲染,本腳本失敗不影響寄信。
執行:python podcast_digest.py(需 DEEPSEEK_API_KEY;faster-whisper 由
workflow 單獨 pip install,不進 requirements.txt 以免拖慢晨報/CI)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser

import state_store as _ss
import requests

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# 一般節目:flash + small 即可(便宜快)。核心節目(accuracy=high)走較準的組合,
# 把額度花在使用者真正在讀、且個股/數字密集的台系深度節目上。
DEEPSEEK_MODEL = os.getenv("PODCAST_DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_MODEL_HIGH = os.getenv("PODCAST_DEEPSEEK_MODEL_HIGH", "deepseek-v4-pro")
WHISPER_MODEL = os.getenv("PODCAST_WHISPER_MODEL", "small")   # small 中文夠用
WHISPER_MODEL_HIGH = os.getenv("PODCAST_WHISPER_MODEL_HIGH", "medium")  # 中文/公司名更準,慢約一倍
#: 批#74 r1(Codex,P2):**生產者與消費者必須共用同一個 state 根。**
#: 晨報的 state 路徑已改由 `STATE_ROOT` 衍生,而這裡仍硬寫 `state/…` ——
#: 設定 `STATE_ROOT` 之後生產者寫舊路徑、晨報讀新路徑,兩邊靜默分家
#: (podcast 內容變空/過期、radar 的 GUID 去重失效讓已寄過的集數再出現)。
#: 刻意讀同一個環境變數而不 import morning_report:那會把獨立排程的
#: 生產者綁上整支主程式的匯入成本與相依。
STATE_ROOT = Path(os.environ.get("STATE_ROOT") or "state")
STATE_FILE = STATE_ROOT / "podcast_digest.json"
# 72h:涵蓋「被每日預算擋掉的集隔天補轉」與「清空重轉」情境(48h 曾讓兩者永遠錯過)
MAX_EPISODE_AGE_HOURS = float(os.getenv("PODCAST_MAX_AGE_H", "72"))
MAX_AUDIO_MB = 200
# 轉錄文字進 LLM 前的長度上限。180000 字 ≈ 2.5 小時,確保 90 分鐘長集尾段不被截掉
# (曾因 60000 上限把股癌尾段的被動元件/記憶體討論切掉)。DeepSeek v4 context 夠大可一次吃下。
MAX_TRANSCRIPT_CHARS = int(os.getenv("PODCAST_MAX_TRANSCRIPT_CHARS", "180000"))
KEEP_EPISODES_PER_SHOW = 12

# faster-whisper initial_prompt:餵領域詞庫,讓機器轉錄不把公司名/術語聽成錯字
# (台股代號、半導體/AI 供應鏈常見詞)。這是準確度 CP 值最高的一招。
WHISPER_ZH_PROMPT = (
    "以下是台灣財經投資 podcast,內容常見台股與半導體、AI 供應鏈。"
    "台積電、聯發科、輝達(NVIDIA)、台達電、鴻海、廣達、緯創、緯穎、技嘉、"
    "日月光、聯詠、瑞昱、世芯、創意、力旺、群聯、南亞科、華邦電、聯電、"
    "被動元件、散熱、水冷、光通訊、矽光子、CoWoS、HBM、記憶體、晶圓代工、"
    "先進封裝、ASIC、伺服器、機器人、重電、加權指數、台股、那斯達克、聯準會。"
)
WHISPER_EN_PROMPT = (
    "A finance and markets podcast discussing stocks, the Federal Reserve, "
    "Nvidia, TSMC, semiconductors, AI, earnings, inflation and the S&P 500."
)
# 核心(accuracy=high)節目轉錄較慢,job 預算估時用較大的 realtime factor 才不會超時
TRANSCRIBE_REALTIME_FACTOR_HIGH = float(
    os.getenv("PODCAST_TRANSCRIBE_REALTIME_FACTOR_HIGH", "0.55"))

# priority 1 = 每天必轉(短/每日/核心);2 = 預算內輪轉(長集深度)。
# lang: zh/en → whisper 轉錄語言;country → iTunes Search 商店。
# 註:Acquired(單集 3.5h)與 Bloomberg Surveillance(每日 1-2h)因時長
# 超出每日預算太多,刻意不納入。
# accuracy="high" → 用 medium 轉錄 + beam 5 + v4-pro 摘要(個股/數字密集的台系深度節目)。
PODCASTS = [
    # --- 中文核心(每日/高契合) ---
    {"key": "gooaye", "name": "股癌", "search": "股癌 Gooaye",
     "lang": "zh", "country": "TW", "priority": 1, "accuracy": "high"},
    {"key": "haojiao", "name": "游庭皓的財經皓角", "search": "游庭皓的財經皓角",
     "lang": "zh", "country": "TW", "priority": 1, "accuracy": "high"},
    {"key": "statementdog", "name": "財報狗", "search": "財報狗",
     "lang": "zh", "country": "TW", "priority": 1, "accuracy": "high"},
    {"key": "mviewpoint", "name": "M觀點", "search": "M觀點 Miula",
     "lang": "zh", "country": "TW", "priority": 1, "accuracy": "high"},
    # 2026-07-14 使用者拍板瘦身(每天 ~10 集讀不完 → ~5 集),刪 4 檔重複度最高的每日型:
    #   科技報橘(科技早餐=科技新聞朗讀,與信中「八、科技板塊脈動」全重複)
    #   美股投資學(每日美股 recap,與 Wall Street Breakfast+信中美股區重複)
    #   財經一路發(訪談名師,觀點與游庭皓/股癌重疊、來賓品質波動)
    #   WSJ What's News(與 Wall Street Breakfast 同為美股每日快訊;世界新聞信中已有速覽)
    {"key": "macromicro", "name": "財經M平方", "search": "財經M平方",
     "lang": "zh", "country": "TW", "priority": 2},
    # --- 英文每日新聞(短,便宜;美股盤前僅留最精煉的一檔) ---
    {"key": "ws-breakfast", "name": "Wall Street Breakfast", "search": "Wall Street Breakfast",
     "lang": "en", "country": "US", "priority": 1},
    # --- 英文深度 / 科技(預算內輪轉;貼近 2330/00662 半導體與 NASDAQ 曝險) ---
    {"key": "oddlots", "name": "Odd Lots", "search": "Odd Lots Bloomberg",
     "lang": "en", "country": "US", "priority": 2},
    {"key": "sharptech", "name": "Sharp Tech (Ben Thompson)",
     "search": "Sharp Tech Ben Thompson",
     "lang": "en", "country": "US", "priority": 2},   # 科技/半導體策略,貼 2330/00662
    {"key": "allin", "name": "All-In Podcast", "search": "All-In Podcast",
     "lang": "en", "country": "US", "priority": 2},    # 總經+科技+市場,週更格局大
    # 2026-07-31 新增(以真實 feed 實測後挑選):
    {"key": "circuit", "name": "The Circuit", "search": "The Circuit Ben Bajarin",
     "lang": "en", "country": "US", "priority": 2},
    # 半導體產業深度,實測 6.6 天/集 —— 原清單最缺這一塊(最貼 2330)
    {"key": "fwdguidance", "name": "Forward Guidance",
     "search": "Forward Guidance Blockworks",
     "lang": "en", "country": "US", "priority": 2},
    # 總經/利率/美債,實測 5.0 天/集 —— 對應長天期殖利率這條主線
    # 2026-07-31 依實測移除兩檔(digest 累積至今**各 0 集**,不是抓取壞掉,是節目本身):
    #   BG2 Pod —— iTunes 最新集停在 2026-03-15,停更四個半月
    #   Money Talks (Economist) —— feed 實測 167 天/集,幾乎不更新
    # 兩者都通過 iTunes 查詢(feed 有效),所以既有的抓取失敗告警不會響 ——
    # 「節目不出新集」與「我們抓不到」是兩件事,而只有後者有告警。
]

# 首跑實測:轉錄速度 ~0.18x 音長(147 分音檔僅 25 分轉錄),預算可放寬;
# 且被擋的集隔天常已超過 48h 齡限而永遠錯過 → 300 分讓單日積壓也消化得完。
DAILY_BUDGET_MINUTES = float(os.getenv("PODCAST_DAILY_BUDGET_MIN", "300"))
JOB_BUDGET_SECONDS = float(os.getenv("PODCAST_JOB_BUDGET_MIN", "95")) * 60
TRANSCRIBE_REALTIME_FACTOR = float(os.getenv("PODCAST_TRANSCRIBE_REALTIME_FACTOR", "0.30"))

DIGEST_PROMPT = """你是財經 podcast 重點整理員。以下是一集節目的逐字稿(機器轉錄,可能有錯字,
請依上下文自行校正,尤其公司名與數字)。

【語言鐵則(最重要)】所有輸出欄位一律使用**台灣繁體中文(zh-TW)**:
- 嚴禁簡體字(寫「臺/台、與、產業、訊號」,不寫「与、产业、信号」)
- 節目是英文時,summary_points / market_view / action_view / reason 全部翻譯成繁體中文,
  只有 notable_quote 可保留英文原文
- 用台灣用語(寫「漲跌幅、營收、晶片」,不寫「涨跌幅、营收、芯片」)

請整理重點,輸出 JSON:
{
  "summary_points": ["**4-8 條**本集重點,每條一句話,具體(含數字/事件/邏輯),不要空泛。
                      【2026-07-31 依實測降量】原本 5-15 條,實測單日 182 條要點、
                      平均每集 10.7 條,讀者讀不完。**寧可少而準**。
                      【最重要:只寫「這個節目獨有的東西」】同一件大事(如某公司財報)
                      當天會有多個節目講到,而**事實本身晨報正文已經寫過**。
                      所以這裡要寫的是**主持人的判斷、推論與操作思路**,
                      以及**只有這集有的數字或角度**——
                      「微軟 Azure 破千億」是事實(不要重複寫),
                      「主持人認為這證明 AI 資本支出可回收、因此加碼」才是這裡要的。
                      純粹複述新聞事實而沒有主持人觀點的條目,**直接不要寫**。
                      【只收與投資/市場相關】:總經、產業/族群、個股、財報、籌碼、資產配置、
                      或政策/地緣對市場的影響才收;主持人的政治立場與選舉八卦、地方民生政策、
                      生活/教養/心靈雞湯/個人感想等與投資無關內容一律略過(除非直接牽動特定產業或公司)。
                      產業/族群觀點(記憶體、被動元件、散熱、光通訊、金融股…)有主持人看法就收;
                      長集(>40 分鐘)且觀點密集時最多 8 條,投資含量低時 2-3 條即可,
                      **不要為湊數塞進無關內容或新聞複述**」],
  "tickers": [{"name": "公司或 ETF 名", "code": "台股代號或美股 ticker,不確定就留空字串",
               "market": "TW 或 US", "direction": "bullish/bearish/neutral",
               "reason": "主持人對它的看法一句話"}],
  "market_view": "主持人對大盤/總經的整體看法,1-2 句;沒明確說就寫空字串",
  "action_view": "主持人提到的操作思路(加碼/減碼/觀望/策略),1-2 句;沒有就空字串",
  "notable_quote": "一句最有代表性的原話(可空字串)"
}
鐵則:只記錄主持人「真的說過」的內容,嚴禁腦補或外推;聽不清楚/不確定的個股代號留空;
廣告與閒聊跳過;tickers 最多 8 檔。
tickers 收錄標準:節目中對特定公司(或 ETF)有「方向性討論」就收 —— 分析、看法、
提及其利多利空都算,不限明確推薦;純粹一筆帶過的新聞播報才略過。
產業級觀點(如「看好散熱族群」「記憶體循環向上」)請放進 summary_points,不放 tickers。"""


def log(msg: str) -> None:
    print(f"[podcast] {msg}", flush=True)


def _http_get(url, *, retries=2, backoff=1.2,
              retry_status=(429, 500, 502, 503, 504), **kwargs):
    """帶重試/退避的 GET(沿用 requests.get 介面、回傳 Response)。
    刻意不共用 morning_report._http_get:podcast_digest 為獨立輕量模組,import morning_report
    會拖入 15k 行 + pandas/yfinance,podcast job 不值得;故自帶語義相同的 mini 版
    (連線例外/5xx 才重試、404 直接回、耗盡拋出)。以 getattr 取 status_code,測試假物件無此屬性
    時視為 200(直接回、不重試),保 monkeypatch(pd.requests.get)相容。"""
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


def _name_matches(search_term: str, collection: str) -> bool:
    """iTunes 回的節目名與搜尋詞是否真的是同一個節目。

    2026-07-31 實測 iTunes 的模糊比對會回**完全不同的節目**:
      「Acquired podcast」        → 「Target Acquired Podcast / A Marvel…」
      「Stratechery Ben Thompson」→ 「Acquired」
    而 `resolve_feed_url` 原本直接取 `results[0]`,等於把別的節目的內容
    灌進晨報,而且**看起來完全正常**(有 feed、有新集、摘要也寫得出來)。

    判準:搜尋詞的具辨識力片段(去掉泛用詞)至少要有一個出現在節目名裡。
    刻意只要求一個 —— 節目名常帶副標(「BG2Pod with Brad Gerstner and Bill
    Gurley」),而搜尋詞常帶名稱裡沒有的消歧詞(「Odd Lots **Bloomberg**」);
    要求全中會把正確的擋掉。

    **能力邊界(誠實標註)**:擋得住完全不相干的誤配(Stratechery → Acquired),
    但擋**不住**「具辨識力詞整個出現在別的節目名裡」的情形 ——
    「Acquired」對「Target Acquired Podcast」仍會通過。要根本解決得把 feed URL
    釘進設定(人工核對過一次),搜尋只當備援。這裡的職責是**擋漂移**,
    不是做節目探索;PODCASTS 是人工策展的固定清單。
    """
    name = str(collection or "").lower()
    generic = {"podcast", "pod", "the", "show", "from", "with", "and", "&"}
    parts = [w for w in str(search_term or "").lower().replace("(", " ")
             .replace(")", " ").split() if w and w not in generic]
    if not parts:
        return bool(name)
    return any(w in name for w in parts)


def resolve_feed_url(search_term: str, country: str = "TW") -> str:
    """搜尋節目並回 feed URL;**名稱對不上就回空**(見 `_name_matches`)。

    取 limit=5 再挑第一個名稱對得上的 —— 只取 1 筆的話,一旦最相似的那筆是
    別的節目就直接誤用,沒有第二次機會。
    """
    r = _http_get("https://itunes.apple.com/search",
                  params={"term": search_term, "country": country,
                          "media": "podcast", "limit": 5},
                  timeout=20)
    r.raise_for_status()
    for item in r.json().get("results", []) or []:
        if not isinstance(item, dict):
            continue
        feed = str(item.get("feedUrl") or "")
        if feed and _name_matches(search_term, item.get("collectionName")):
            return feed
    return ""


def parse_feed_url(url: str, timeout: int = 20):
    """Fetch podcast RSS with a bounded request, then parse it locally."""
    response = _http_get(
        url,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/rss+xml,application/xml"},
    )
    response.raise_for_status()
    return feedparser.parse(response.content)


def _entry_published_at(entry) -> dt.datetime | None:
    raw = entry.get("published") or entry.get("updated") or ""
    try:
        pub = parsedate_to_datetime(raw)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=dt.timezone.utc)
        return pub
    except Exception:
        return None


def _entry_age_hours(entry) -> float:
    pub = _entry_published_at(entry)
    if pub is None:
        return float("inf")
    return (dt.datetime.now(dt.timezone.utc) - pub).total_seconds() / 3600


def load_state() -> dict:
    """壞檔**往上拋**(2026-08-22 外審 P2-4)。

    先前是「讀不出來就視為空」,而 `save_state` 之後會無條件覆寫同一個檔:
    一次暫時性損壞 → 所有 processed GUID 消失 → 近 72 小時的集數全部被當成
    新集重新轉錄 → 只要成功一集就把 `{}` + 這一集寫回去,**歷史 summaries
    與 processed GUID 永久消失**。與晨報那批修掉的 forecast_ledger 同型。

    這是獨立 workflow,沒有「晨報不可斷」的理由 —— 讀不動就整班不做事,
    原始位元組留著(不覆寫就是保存現場)。
    """
    data, _st = _ss.load_json_state(STATE_FILE, expected=dict)
    return data


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(f"{STATE_FILE.suffix}.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def download_audio(url: str, dest: Path) -> bool:
    with requests.get(url, stream=True, timeout=60,
                      headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        size = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                size += len(chunk)
                if size > MAX_AUDIO_MB * (1 << 20):
                    log(f"音檔超過 {MAX_AUDIO_MB}MB,放棄")
                    return False
                f.write(chunk)
    log(f"下載完成 {size / (1 << 20):.1f}MB")
    return True


_WHISPER_MODEL_CACHE: dict = {}


def transcribe_audio(path: Path, lang: str = "zh",
                     model_name: str = WHISPER_MODEL, beam_size: int = 1) -> str:
    """faster-whisper 本地轉錄(CPU int8,免費)。50 分鐘集約 10-25 分鐘。

    model_name/beam_size 可由呼叫端依節目重要性提高(medium + beam5 更準但較慢);
    initial_prompt 餵領域詞庫,大幅降低公司名/術語被聽錯的機率。
    """
    from faster_whisper import WhisperModel   # lazy import:晨報/CI 不裝此套件
    t0 = time.time()
    if model_name not in _WHISPER_MODEL_CACHE:   # 多集共用,各尺寸模型各載一次
        _WHISPER_MODEL_CACHE[model_name] = WhisperModel(
            model_name, device="cpu", compute_type="int8")
    model = _WHISPER_MODEL_CACHE[model_name]
    initial_prompt = WHISPER_ZH_PROMPT if (lang or "zh").startswith("zh") else WHISPER_EN_PROMPT
    segments, info = model.transcribe(
        str(path), language=lang or None, vad_filter=True,
        beam_size=beam_size, initial_prompt=initial_prompt)
    parts = []
    total = 0
    for seg in segments:
        parts.append(seg.text)
        total += len(seg.text)
        if total > MAX_TRANSCRIPT_CHARS:
            log(f"轉錄達 {MAX_TRANSCRIPT_CHARS} 字上限,截斷")
            break
    text = "".join(parts).strip()
    log(f"轉錄完成 {len(text)} 字(音長 {getattr(info, 'duration', 0) / 60:.0f} 分,"
        f"耗時 {(time.time() - t0) / 60:.1f} 分,model={model_name},beam={beam_size})")
    return text


# 常見「簡繁不同形」的簡體字樣本:命中即判定輸出含簡體,觸發重試
_SIMPLIFIED_CHARS = set(
    "贸属币当风点产离张环严胀价节让说证销级则妈题观项启动东陈"
    "刘汉权汇负责广团长门间问报应变这进对开关经济与业为电务亿万亏处")


def _lang_violation(digest: dict) -> str:
    """檢查摘要語言:回傳違規描述(空字串 = 合格)。"""
    fields = []
    for p in digest.get("summary_points") or []:
        fields.append(str(p))
    for t in digest.get("tickers") or []:
        fields.append(str(t.get("reason", "")))
    fields.append(str(digest.get("market_view", "")))
    fields.append(str(digest.get("action_view", "")))
    text = "".join(fields)
    if not text:
        return ""
    simp = [ch for ch in text if ch in _SIMPLIFIED_CHARS and "一" <= ch <= "鿿"]
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    if cjk and len(simp) >= 3:
        return f"輸出含簡體字(如 {''.join(sorted(set(simp))[:5])}),必須全部改用台灣繁體中文"
    if len(text) > 80 and cjk / max(len(text), 1) < 0.25:
        return "輸出主要是英文,必須翻譯成台灣繁體中文(僅 notable_quote 可留英文)"
    return ""


def _accuracy_settings(cfg: dict) -> dict:
    """依節目 accuracy 等級回傳轉錄/摘要設定。high = medium+beam5+v4-pro。"""
    if cfg.get("accuracy") == "high":
        return {"whisper": WHISPER_MODEL_HIGH, "beam": 5,
                "summary_model": DEEPSEEK_MODEL_HIGH,
                "rt_factor": TRANSCRIBE_REALTIME_FACTOR_HIGH}
    return {"whisper": WHISPER_MODEL, "beam": 1,
            "summary_model": DEEPSEEK_MODEL,
            "rt_factor": TRANSCRIBE_REALTIME_FACTOR}


def deepseek_digest(transcript: str, model: str = DEEPSEEK_MODEL) -> dict:
    """DeepSeek(OpenAI 相容 API)把逐字稿整理成結構化摘要 JSON。
    輸出做語言驗證(簡體/未翻譯英文 → 帶錯誤回饋重試)。"""
    messages = [
        {"role": "system", "content": DIGEST_PROMPT},
        {"role": "user", "content": transcript[:MAX_TRANSCRIPT_CHARS]},
    ]
    last_err = None
    for attempt in range(4):
        try:
            r = requests.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json={"model": model, "messages": messages,
                      "response_format": {"type": "json_object"},
                      "temperature": 0.2},
                timeout=300)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            digest = json.loads(text)
            if not (isinstance(digest, dict) and digest.get("summary_points")):
                raise RuntimeError("摘要 JSON 缺 summary_points")
            violation = _lang_violation(digest)
            if violation:
                log(f"語言驗證未過(第 {attempt + 1} 次): {violation}")
                # 把違規回饋進對話,要求重寫(最多重試到迴圈上限)
                messages = messages[:2] + [
                    {"role": "assistant", "content": text[:2000]},
                    {"role": "user", "content": f"上一版不合格:{violation}。"
                     f"請重新輸出完整 JSON,嚴格遵守語言鐵則。"},
                ]
                last_err = RuntimeError(violation)
                continue
            return digest
        except Exception as e:
            last_err = e
            log(f"摘要第 {attempt + 1} 次失敗: {str(e)[:100]}")
            time.sleep(15)
    raise RuntimeError(f"DeepSeek 摘要失敗: {last_err}")


def _duration_minutes(entry) -> float:
    """從 feed 的 itunes_duration 解析時長(分);格式可為秒數或 HH:MM:SS。沒有就估 40 分。"""
    raw = str(entry.get("itunes_duration") or "").strip()
    if not raw:
        return 40.0
    try:
        if ":" in raw:
            parts = [float(p) for p in raw.split(":")]
            secs = parts[-1] + parts[-2] * 60 + (parts[-3] * 3600 if len(parts) > 2 else 0)
        else:
            secs = float(raw)
        return max(1.0, secs / 60)
    except Exception:
        return 40.0


def find_new_episodes(cfg: dict, state: dict, limit: int = 5) -> list[tuple]:
    """Return multiple recent unprocessed episodes so backlog can drain."""
    key, name = cfg["key"], cfg["name"]
    feed_url = resolve_feed_url(cfg["search"], cfg.get("country", "TW"))
    if not feed_url:
        log(f"{name}: iTunes 查無 feed")
        return []
    feed = parse_feed_url(feed_url)
    if not feed.entries:
        log(f"{name}: feed 無集數")
        return []
    show = state.setdefault(key, {"name": name, "episodes": []})
    found = []
    # 多掃幾集，讓每日多更或前一日預算不足的積壓能在 72 小時內補完。
    for entry in feed.entries[:12]:
        guid = str(entry.get("id") or entry.get("link") or entry.get("title") or "")
        if any(ep.get("guid") == guid for ep in show["episodes"]):
            continue
        if _entry_age_hours(entry) > MAX_EPISODE_AGE_HOURS:
            continue
        dur = _duration_minutes(entry)
        if dur < 3:
            continue
        audio_url = next((enc.get("href") for enc in (entry.get("enclosures") or [])
                          if enc.get("href")), "")
        if not audio_url:
            continue
        found.append((entry, audio_url, dur))
        if len(found) >= limit:
            break
    return found


def find_new_episode(cfg: dict, state: dict):
    """Backward-compatible single-episode wrapper."""
    found = find_new_episodes(cfg, state, limit=1)
    return found[0] if found else None


def _stored_pub_dt(ep: dict) -> dt.datetime:
    """已存集的發布時間(供新→舊排序):先用 published(RFC822),退回 processed_at,再退回最小值。"""
    pub = str(ep.get("published") or "").strip()
    if pub:
        try:
            d = parsedate_to_datetime(pub)
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        except Exception:
            pass
    proc = str(ep.get("processed_at") or "").strip()
    if proc:
        try:
            return dt.datetime.strptime(proc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        except Exception:
            pass
    return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def _process_order_key(item):
    """轉錄處理順序:優先級小者先;同優先級內『最新集先轉』(新→舊,確保旗艦最新集在預算內);
    再以時長短者為次要鍵(同日多更時先轉短的、較省預算)。無發布日者視為最舊、最後處理。"""
    cfg, entry, _audio, dur = item
    pub = _entry_published_at(entry)
    ts = pub.timestamp() if pub else 0.0
    return (cfg.get("priority", 9), -ts, dur)


def process_episode(cfg: dict, state: dict, entry, audio_url: str) -> bool:
    """下載 → 轉錄 → DeepSeek 摘要 → 寫入 state。"""
    key, name = cfg["key"], cfg["name"]
    guid = str(entry.get("id") or entry.get("link") or entry.get("title") or "")
    log(f"{name}: 處理新集「{str(entry.get('title', ''))[:50]}」")
    acc = _accuracy_settings(cfg)
    tmp = Path(f"podcast_{key}.mp3")
    try:
        if not download_audio(audio_url, tmp):
            return False
        transcript = transcribe_audio(
            tmp, lang=cfg.get("lang", "zh"),
            model_name=acc["whisper"], beam_size=acc["beam"])
        if len(transcript) < 500:
            log(f"{name}: 轉錄過短({len(transcript)} 字),跳過")
            return False
        digest = deepseek_digest(transcript, model=acc["summary_model"])
    finally:
        tmp.unlink(missing_ok=True)

    show = state.setdefault(key, {"name": name, "episodes": []})
    show["episodes"].append({
        "guid": guid,
        "title": str(entry.get("title", ""))[:120],
        "published": str(entry.get("published", "")),
        "processed_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "digest": digest,
    })
    # 與轉錄順序解耦:一律依發布時間新→舊排序儲存,確保晨報挑到「最新未顯示」、
    # 截斷時也保留最新 KEEP_EPISODES_PER_SHOW 集(原本靠 insert(0)+舊集先轉達成,
    # 改最新先轉後需顯式排序,否則顯示會變舊→新)。
    show["episodes"].sort(key=_stored_pub_dt, reverse=True)
    show["episodes"] = show["episodes"][:KEEP_EPISODES_PER_SHOW]
    log(f"{name}: 摘要完成({len(digest.get('summary_points', []))} 條重點、"
        f"{len(digest.get('tickers', []))} 檔個股)")
    return True


def main() -> int:
    if not DEEPSEEK_API_KEY:
        log("缺 DEEPSEEK_API_KEY,結束")
        return 1
    try:
        state = load_state()
    except _ss.StateCorrupt as e:
        # **不轉錄、不寄送、不寫新 state**:重跑一次比毀掉歷史便宜。
        log(f"state 讀不動,本班不做事(原檔保留): {e}")
        return 2
    started = time.monotonic()

    # 第一輪:盤點所有節目的新集(只打 RSS,便宜)
    pending = []
    for cfg in PODCASTS:
        try:
            for found in find_new_episodes(cfg, state):
                pending.append((cfg, *found))
        except Exception as e:
            log(f"{cfg['name']} 盤點失敗: {str(e)[:120]}")
    log(f"盤點完成:{len(pending)} 個節目有新集")

    # 第二輪:優先級排序 + 每日轉錄預算(音檔總分鐘),超出者留待下次。
    # 同優先級內「最新集先轉」(見 _process_order_key):晨報要的是當日最新,預算吃緊時
    # 也保證旗艦節目(股癌等)的最新集先進庫,不再被舊積壓擠到預算外;舊集仍在 72h 內由
    # 後續(一天 4 次)的剩餘預算補完。顯示順序與轉錄順序解耦,由 process_episode 依發布時間排序。
    pending.sort(key=_process_order_key)
    used_min = 0.0
    updated = False
    for cfg, entry, audio_url, dur in pending:
        if used_min + dur > DAILY_BUDGET_MINUTES:
            log(f"{cfg['name']}: 超出每日預算({used_min:.0f}+{dur:.0f}"
                f">{DAILY_BUDGET_MINUTES:.0f} 分),本次跳過")
            continue
        # Reserve enough wall time for transcription, summarization, state write and
        # the workflow's final git commit step. Skipped items remain eligible tomorrow.
        # 核心節目用 medium 轉錄較慢,估時用較大的 realtime factor 才不會超時。
        estimated_seconds = dur * 60 * _accuracy_settings(cfg)["rt_factor"] + 1500
        if time.monotonic() - started + estimated_seconds > JOB_BUDGET_SECONDS:
            log(f"{cfg['name']}: 剩餘 job 時間不足以安全完成，留待下次")
            continue
        try:
            if process_episode(cfg, state, entry, audio_url):
                updated = True
                used_min += dur
                save_state(state)   # 逐集落盤:後面失敗/超時不丟已完成的摘要
        except Exception as e:
            log(f"{cfg['name']} 處理失敗(不影響其他節目): {str(e)[:150]}")

    if updated:
        log(f"已寫入 {STATE_FILE}(共轉錄 {used_min:.0f} 分鐘音檔)")
    else:
        log("本次無新集")
    return 0


if __name__ == "__main__":
    sys.exit(main())
