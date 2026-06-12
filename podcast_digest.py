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
import requests

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# 摘要用 flash 即可(輸入是轉錄文字 ~2-3 萬 tokens,flash 便宜且夠用)
DEEPSEEK_MODEL = os.getenv("PODCAST_DEEPSEEK_MODEL", "deepseek-v4-flash")
WHISPER_MODEL = os.getenv("PODCAST_WHISPER_MODEL", "small")   # small 中文夠用;medium 更準但慢一倍
STATE_FILE = Path("state/podcast_digest.json")
MAX_EPISODE_AGE_HOURS = 48      # 只處理 48 小時內的新集
MAX_AUDIO_MB = 200
MAX_TRANSCRIPT_CHARS = 60000    # 轉錄文字進 LLM 前的長度上限(~90 分鐘集數也夠)
KEEP_EPISODES_PER_SHOW = 5

PODCASTS = [
    {"key": "gooaye", "name": "股癌", "search": "股癌 Gooaye"},
    {"key": "haojiao", "name": "游庭皓的財經皓角", "search": "游庭皓的財經皓角"},
    {"key": "statementdog", "name": "財報狗", "search": "財報狗"},
]

DIGEST_PROMPT = """你是財經 podcast 重點整理員。以下是一集節目的逐字稿(機器轉錄,可能有錯字,
請依上下文自行校正,尤其公司名與數字)。請整理重點,輸出 JSON(繁體中文):
{
  "summary_points": ["3-6 條本集重點,每條一句話,具體(含數字/事件/邏輯),不要空泛"],
  "tickers": [{"name": "公司或 ETF 名", "code": "台股代號或美股 ticker,不確定就留空字串",
               "market": "TW 或 US", "direction": "bullish/bearish/neutral",
               "reason": "主持人對它的看法一句話"}],
  "market_view": "主持人對大盤/總經的整體看法,1-2 句;沒明確說就寫空字串",
  "action_view": "主持人提到的操作思路(加碼/減碼/觀望/策略),1-2 句;沒有就空字串",
  "notable_quote": "一句最有代表性的原話(可空字串)"
}
鐵則:只記錄主持人「真的說過」的內容,嚴禁腦補或外推;聽不清楚/不確定的個股代號留空;
廣告與閒聊跳過;tickers 最多 8 檔,只收主持人有實質觀點的。"""


def log(msg: str) -> None:
    print(f"[podcast] {msg}", flush=True)


def resolve_feed_url(search_term: str) -> str:
    r = requests.get("https://itunes.apple.com/search",
                     params={"term": search_term, "country": "TW",
                             "media": "podcast", "limit": 1},
                     timeout=20)
    r.raise_for_status()
    results = r.json().get("results", [])
    return str(results[0].get("feedUrl", "")) if results else ""


def _entry_age_hours(entry) -> float:
    raw = entry.get("published") or entry.get("updated") or ""
    try:
        pub = parsedate_to_datetime(raw)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - pub).total_seconds() / 3600
    except Exception:
        return float("inf")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as e:
            log(f"state 讀取失敗(視為空): {e}")
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")


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


def transcribe_audio(path: Path) -> str:
    """faster-whisper 本地轉錄(CPU int8,免費)。50 分鐘集約 10-25 分鐘。"""
    from faster_whisper import WhisperModel   # lazy import:晨報/CI 不裝此套件
    t0 = time.time()
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(path), language="zh", vad_filter=True, beam_size=1)
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
        f"耗時 {(time.time() - t0) / 60:.1f} 分,model={WHISPER_MODEL})")
    return text


def deepseek_digest(transcript: str) -> dict:
    """DeepSeek(OpenAI 相容 API)把逐字稿整理成結構化摘要 JSON。"""
    body = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": DIGEST_PROMPT},
            {"role": "user", "content": transcript[:MAX_TRANSCRIPT_CHARS]},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json=body, timeout=300)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            digest = json.loads(text)
            if isinstance(digest, dict) and digest.get("summary_points"):
                return digest
            raise RuntimeError("摘要 JSON 缺 summary_points")
        except Exception as e:
            last_err = e
            log(f"摘要第 {attempt + 1} 次失敗: {str(e)[:100]}")
            time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"DeepSeek 摘要失敗: {last_err}")


def process_podcast(cfg: dict, state: dict) -> bool:
    """處理單一節目最新集;有新摘要寫入 state 回 True。"""
    key, name = cfg["key"], cfg["name"]
    feed_url = resolve_feed_url(cfg["search"])
    if not feed_url:
        log(f"{name}: iTunes 查無 feed")
        return False
    feed = feedparser.parse(feed_url)
    if not feed.entries:
        log(f"{name}: feed 無集數")
        return False
    entry = feed.entries[0]
    guid = str(entry.get("id") or entry.get("link") or entry.get("title") or "")
    show = state.setdefault(key, {"name": name, "episodes": []})
    if any(ep.get("guid") == guid for ep in show["episodes"]):
        log(f"{name}: 最新集已處理過,跳過")
        return False
    age = _entry_age_hours(entry)
    if age > MAX_EPISODE_AGE_HOURS:
        log(f"{name}: 最新集已 {age:.0f}h(>{MAX_EPISODE_AGE_HOURS}h),跳過")
        return False
    audio_url = next((enc.get("href") for enc in (entry.get("enclosures") or [])
                      if enc.get("href")), "")
    if not audio_url:
        log(f"{name}: 無音檔連結")
        return False

    log(f"{name}: 處理新集「{str(entry.get('title', ''))[:50]}」({age:.0f}h 前)")
    tmp = Path(f"podcast_{key}.mp3")
    try:
        if not download_audio(audio_url, tmp):
            return False
        transcript = transcribe_audio(tmp)
        if len(transcript) < 500:
            log(f"{name}: 轉錄過短({len(transcript)} 字),跳過")
            return False
        digest = deepseek_digest(transcript)
    finally:
        tmp.unlink(missing_ok=True)

    show["episodes"].insert(0, {
        "guid": guid,
        "title": str(entry.get("title", ""))[:120],
        "published": str(entry.get("published", "")),
        "processed_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "digest": digest,
    })
    show["episodes"] = show["episodes"][:KEEP_EPISODES_PER_SHOW]
    log(f"{name}: 摘要完成({len(digest.get('summary_points', []))} 條重點、"
        f"{len(digest.get('tickers', []))} 檔個股)")
    return True


def main() -> int:
    if not DEEPSEEK_API_KEY:
        log("缺 DEEPSEEK_API_KEY,結束")
        return 1
    state = load_state()
    updated = False
    for cfg in PODCASTS:
        try:
            if process_podcast(cfg, state):
                updated = True
        except Exception as e:
            log(f"{cfg['name']} 處理失敗(不影響其他節目): {str(e)[:150]}")
    if updated:
        save_state(state)
        log(f"已寫入 {STATE_FILE}")
    else:
        log("本次無新集")
    return 0


if __name__ == "__main__":
    sys.exit(main())
