# -*- coding: utf-8 -*-
"""股癌事件抽取層（P-2 dry-run）。

把 transcripts/EP<n>.md 逐字稿 → 結構化事件，嚴格照 ANNOTATION_GUIDE.md：
  - 題材事件 (sector) + 個股事件 (ticker)，各帶 mention_type 六分類、stance、
    conviction、already_ran、evidence 原句。
  - 只有 bullish_call / bearish_call 算「可進主統計」；retrospective(回顧已漲)、
    neutral、non_investment、macro_concept 記錄但不進報酬統計。
  - 只記錄「真的說過」的，不腦補；evidence 必附原句供人工稽核。

輸入：data/pilot_episodes.json（fetch_transcripts.py 產出）+ transcripts/EP<n>.md
輸出：data/events_raw.json（每事件一列，append-only 概念；gitignore）
      + 印出 dry-run 品質指標（事件密度 / ticker 命中率 / 立場可判率）。

金鑰：環境變數 DEEPSEEK_API_KEY，或本機 gooaye_study/_secrets.txt（KEY=VALUE 行；gitignore）。

用法：
  python extract_events.py            # 跑全部 pilot 集
  python extract_events.py --limit 3  # 先測前 3 集
  python extract_events.py --model deepseek-v4-pro
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
PILOT_PATH = HERE / "data" / "pilot_episodes.json"
MANIFEST_PATH = HERE / "data" / "episode_manifest.json"
TRANSCRIPT_DIR = HERE / "transcripts"
EVENTS_PATH = HERE / "data" / "events_raw.json"
SECRETS_PATH = HERE / "_secrets.txt"

DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# 預設 flash:結構化抽取夠用且快;v4-pro 的推理模式會在某些集把連線掛住數小時(實測),
# 不適合批次。需要更高品質可用 --model deepseek-v4-pro 但建議搭 --workers 1 並盯著。
DEFAULT_MODEL = os.getenv("GOOAYE_EXTRACT_MODEL", "deepseek-v4-flash")
DEFAULT_WORKERS = int(os.getenv("GOOAYE_EXTRACT_WORKERS", "3"))   # API-bound;3 緒較溫和,避免被限流掛連線
MAX_TRANSCRIPT_CHARS = 180000


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
        except Exception:
            pass


def load_secrets() -> None:
    """把 _secrets.txt 的 KEY=VALUE 灌進 os.environ（不覆蓋已存在的環境變數）。"""
    if not SECRETS_PATH.exists():
        return
    for line in SECRETS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def log(msg: str) -> None:
    print(f"[extract] {msg}", flush=True)


EXTRACT_PROMPT = """你是台股研究的事件標註員。輸入是一集《股癌》(主持人謝孟恭)的逐字稿(機器轉錄,可能有錯字,
依上下文校正公司名/數字)。你的任務是**嚴格、忠實**地抽出可供量化事件研究的「題材」與「個股」事件。

【語言鐵則】所有輸出一律台灣繁體中文(zh-TW),嚴禁簡體字。

【最重要:mention_type 六分類】每個題材/個股都要標一個 mention_type:
- "bullish_call":明確看多,且語境是「往前看、現在還可參與」(例:我覺得這邊還沒反映完/還可以買)。
- "bearish_call":明確看空、提醒避開/減碼/小心。
- "neutral":有討論但無明確方向,或多空並陳、「再觀察」。
- "retrospective":**回顧已發生的行情**(例:之前漲很多/這波吃到了/早就該買)。即使語氣正面,只要是回顧過去就標這個,不是前瞻訊號。
- "non_investment":反諷/玩笑/政治/生活/業配,非投資語境。
- "macro_concept":宏觀或概念性泛談,無法對應具體可交易標的(例:泛談美國要降息)。
判斷要訣:「現在還看好/還沒反映完」→ bullish_call;「已經漲一段了/這波賺到」→ retrospective(關鍵區別)。

【欄位】輸出 JSON:
{
  "episode_summary": "本集投資相關重點 2-3 句",
  "sectors": [   // 題材/族群層級(不放個股代號)
    {"name":"族群名(台股慣用詞,如 功率半導體/被動元件/重電/CoWoS)",
     "stance":"bullish|bearish|neutral",
     "conviction":"high|medium|low",       // 語氣強度+篇幅
     "mention_type":"上述六類之一",
     "already_ran": true/false,             // 主持人話中是否顯示該題材近期『已大漲/大跌』才被討論
     "reasoning":"主持人的邏輯一句話",
     "evidence":"逐字稿原句(可稽核,20-60字)"}
  ],
  "tickers": [   // 明確點名的個股/ETF
    {"name":"公司或ETF名","code":"台股代號或美股ticker,不確定留空字串","market":"TW|US",
     "theme":"該股在本集被股癌歸到的族群(必須對應上方 sectors 之一的 name;純個股討論、無題材歸屬則留空字串)",
     "stance":"bullish|bearish|neutral","conviction":"high|medium|low",
     "mention_type":"上述六類之一","already_ran": true/false,
     "mention_count": 整數(該集大致提及次數),
     "reason":"主持人對它的看法一句話","evidence":"逐字稿原句(可稽核)"}
  ]
}

【鐵則】
1. 只記錄主持人「真的說過」的;聽不清的代號留空字串;廣告/閒聊跳過。
2. evidence 必須是逐字稿裡的實際句子(可截短),不可杜撰。
3. 產業級觀點放 sectors,具名公司放 tickers;同一標的若先回顧再給前瞻看法,以前瞻那句標 bullish_call。
4. 寧缺勿濫:沒有明確方向就標 neutral,不要硬湊 bullish_call。
5. 一集 tickers 最多 12 檔、sectors 最多 10 個。
6. tickers[].theme 是題材籃子的關鍵:只在「股癌明確把該股當成某族群的受惠/成分股」時,
   才填對應的 sectors[].name;若只是獨立聊某檔股、或無題材脈絡,theme 留空字串。務必準確,寧空勿錯填。"""


def deepseek_extract(transcript: str, model: str, api_key: str) -> dict:
    messages = [
        {"role": "system", "content": EXTRACT_PROMPT},
        {"role": "user", "content": transcript[:MAX_TRANSCRIPT_CHARS]},
    ]
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "messages": messages,
                      "response_format": {"type": "json_object"},
                      "temperature": 0.1},
                timeout=(10, 90))   # (連線, 讀取) 上限;卡住 90s 即放手重試,避免無限掛連線
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            content = r.json()["choices"][0]["message"]["content"]
            obj = json.loads(content)
            if not isinstance(obj, dict):
                raise RuntimeError("回傳非 dict")
            obj.setdefault("sectors", [])
            obj.setdefault("tickers", [])
            return obj
        except Exception as e:
            last_err = e
            log(f"  抽取第 {attempt+1} 次失敗: {str(e)[:120]}")
            time.sleep(5)
    raise RuntimeError(f"DeepSeek 抽取失敗: {last_err}")


VALID_TYPES = {"bullish_call", "bearish_call", "neutral",
               "retrospective", "non_investment", "macro_concept"}
TRADEABLE_TYPES = {"bullish_call", "bearish_call"}


def flatten_events(ep: dict, digest: dict, model: str) -> list[dict]:
    """把單集抽取結果攤平成事件列（題材 + 個股）。"""
    base = {"ep": ep["ep"], "t0_date": ep["t0_date"],
            "date_source": ep.get("date_source", ""),
            "extractor_model": model}
    rows = []
    for s in digest.get("sectors", []) or []:
        mt = str(s.get("mention_type", "")).strip()
        rows.append({**base, "level": "theme",
                     "name": str(s.get("name", "")).strip(), "code": "", "market": "TW",
                     "stance": str(s.get("stance", "")).strip(),
                     "conviction": str(s.get("conviction", "")).strip(),
                     "mention_type": mt if mt in VALID_TYPES else "neutral",
                     "already_ran": bool(s.get("already_ran", False)),
                     "tradeable": mt in TRADEABLE_TYPES,
                     "mention_count": 1,
                     "reason": str(s.get("reasoning", ""))[:200],
                     "evidence": str(s.get("evidence", ""))[:300]})
    for t in digest.get("tickers", []) or []:
        mt = str(t.get("mention_type", "")).strip()
        rows.append({**base, "level": "stock",
                     "name": str(t.get("name", "")).strip(),
                     "code": str(t.get("code", "")).strip(),
                     "market": str(t.get("market", "TW")).strip() or "TW",
                     "theme": str(t.get("theme", "")).strip(),   # 所屬題材(連到 sector.name)
                     "stance": str(t.get("stance", "")).strip(),
                     "conviction": str(t.get("conviction", "")).strip(),
                     "mention_type": mt if mt in VALID_TYPES else "neutral",
                     "already_ran": bool(t.get("already_ran", False)),
                     "tradeable": mt in TRADEABLE_TYPES,
                     "mention_count": int(t.get("mention_count", 1) or 1),
                     "reason": str(t.get("reason", ""))[:200],
                     "evidence": str(t.get("evidence", ""))[:300]})
    return rows


def print_metrics(events: list[dict], n_eps: int) -> None:
    """dry-run 品質指標:事件密度 / ticker 命中率 / 立場可判率。"""
    from collections import Counter
    stock = [e for e in events if e["level"] == "stock"]
    theme = [e for e in events if e["level"] == "theme"]
    mt = Counter(e["mention_type"] for e in events)
    tradeable = [e for e in events if e["tradeable"]]
    stock_with_code = [e for e in stock if e["code"]]
    calls = [e for e in events if e["mention_type"] in TRADEABLE_TYPES]
    already = [e for e in calls if e["already_ran"]]
    log("=" * 56)
    log(f"DRY-RUN 品質指標（{n_eps} 集）")
    log(f"  事件總數 {len(events)}（題材 {len(theme)} / 個股 {len(stock)}）")
    log(f"  事件密度 = {len(events)/max(n_eps,1):.1f} 事件/集"
        f"（個股 {len(stock)/max(n_eps,1):.1f}、題材 {len(theme)/max(n_eps,1):.1f}）")
    log(f"  mention_type 分佈: {dict(mt)}")
    log(f"  可進主統計(bullish/bearish_call) = {len(tradeable)} "
        f"({100*len(tradeable)/max(len(events),1):.0f}%)")
    log(f"  個股 ticker 命中率(有代號) = {len(stock_with_code)}/{len(stock)} "
        f"({100*len(stock_with_code)/max(len(stock),1):.0f}%)")
    log(f"  立場可判率(非 neutral) = "
        f"{100*sum(1 for e in events if e['stance'] in ('bullish','bearish'))/max(len(events),1):.0f}%")
    log(f"  反向因果旗標 already_ran(占 call) = {len(already)}/{len(calls)} "
        f"({100*len(already)/max(len(calls),1):.0f}%)")
    log("=" * 56)


_SAVE_LOCK = threading.Lock()


def _process_one(ep: dict, model: str, api_key: str) -> tuple[dict, list[dict] | None, str]:
    """單集 worker:讀逐字稿 → 抽取 → 攤平。回 (ep, rows 或 None, err)。供 ThreadPool 並行。"""
    tp = TRANSCRIPT_DIR / f"EP{ep['ep']}.md"
    if not tp.exists():
        return ep, None, "缺逐字稿"
    try:
        transcript = tp.read_text(encoding="utf-8")
        digest = deepseek_extract(transcript, model, api_key)
        return ep, flatten_events(ep, digest, model), ""
    except Exception as e:
        return ep, None, str(e)[:120]


def main() -> int:
    configure_stdio()
    load_secrets()
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 集(0=全部)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="並行執行緒數")
    ap.add_argument("--fresh", action="store_true", help="忽略既有 events_raw.json 重新抽")
    ap.add_argument("--full", action="store_true",
                    help="讀全 671 集 manifest（預設只讀 40 集 pilot）")
    args = ap.parse_args()

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        log("缺 DEEPSEEK_API_KEY（環境變數或 _secrets.txt），結束")
        return 1
    source = MANIFEST_PATH if args.full else PILOT_PATH
    if not source.exists():
        log(f"缺 {source}，請先跑 fetch_transcripts.py")
        return 1

    pilot = json.loads(source.read_text(encoding="utf-8"))
    pilot = [e for e in pilot if e.get("filename")]   # 全 manifest 含無 filename 者,濾掉
    if args.limit:
        pilot = pilot[:args.limit]

    # 續跑:載入既有 events_raw.json,跳過已抽集（全量 672 抗中斷）。--fresh 重來。
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_events: list[dict] = []
    done_eps: set = set()
    if EVENTS_PATH.exists() and not args.fresh:
        try:
            all_events = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
            done_eps = {e["ep"] for e in all_events}
        except Exception:
            all_events, done_eps = [], set()
    todo = [ep for ep in pilot if ep["ep"] not in done_eps]
    workers = max(1, min(args.workers, len(todo) or 1))
    log(f"模型={args.model}，pilot {len(pilot)} 集，已有 {len(done_eps)}，"
        f"待抽 {len(todo)}（並行 {workers} 緒、逐集落盤、可續跑）…")

    def _save() -> None:
        tmp = EVENTS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(all_events, ensure_ascii=True, indent=2), encoding="utf-8")
        tmp.replace(EVENTS_PATH)

    failed, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as exe:
        futs = {exe.submit(_process_one, ep, args.model, api_key): ep for ep in todo}
        for fut in as_completed(futs):
            done += 1
            ep = futs[fut]
            try:
                _ep, rows, err = fut.result()
            except Exception as e:
                rows, err = None, str(e)[:120]
            if not rows:
                log(f"  [{done}/{len(todo)}] EP{ep['ep']} 失敗/略過: {err}")
                failed.append(ep["ep"])
                continue
            with _SAVE_LOCK:                      # 執行緒安全:逐集落盤
                all_events.extend(rows)
                _save()
            log(f"  [{done}/{len(todo)}] EP{ep['ep']} ({ep['t0_date']}): {len(rows)} 事件"
                f"（題材{sum(1 for r in rows if r['level']=='theme')}"
                f"/個股{sum(1 for r in rows if r['level']=='stock')}）")

    log(f"events_raw 共 {len(all_events)} 列於 {EVENTS_PATH}")
    n_ok = len({e["ep"] for e in all_events})
    print_metrics(all_events, n_ok)
    if failed:
        log(f"失敗/缺檔 EP：{failed}")
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
