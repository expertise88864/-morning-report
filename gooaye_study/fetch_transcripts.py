# -*- coding: utf-8 -*-
"""股癌逐字稿抓取層（P1）。

資料源（已實測 2026-06-21）：
  - 索引：https://whatmkreallysaid.com/episodes.json
      671 集 metadata：number / title / filename / description / summary / date(站台,不可信)
  - 全文：https://whatmkreallysaid.com/episodes/<URL-encoded filename>
      text/markdown，免認證；EP1(2020-02)→EP671(2026-06) 全可抓。
  - 權威日期：SoundOn RSS（站台 date 欄有 ~14.5% 與 RSS 不符 → t0 一律以 RSS 為準）。

設計鐵則（fail loud，不可 silently 污染 t0 / 漏資料，經 GPT-5.5 複審強化）：
  - number 一律轉 int；index 缺 number / 重複 EP / 重複 filename → 直接報錯。
  - t0 用 RSS published 轉 Asia/Taipei 取日期；缺 RSS 日期 → 預設 hard fail
    （站台日期不可信，要 --allow-site-date-fallback 才放行）。
  - RSS 解析到的集數過少 → 報錯（疑似被擋/格式變），禁止整批退回站台日期。
  - 逐字稿快取需通過驗證（長度/編碼/非 HTML 錯誤頁），atomic write；失敗不吞、exit 非 0。
  - 全程 UTF-8（避 cp950）；JSON ensure_ascii=True；啟動固定 stdout/stderr 編碼。
  - 禮貌抓取：真實 UA + 可調間隔(預設 1s) + Retry/backoff/Retry-After。
  - 逐字稿為第三方非官方站內容，僅供個人研究、不再散布、不進 git（見 .gitignore）。

用法：
  python fetch_transcripts.py            # 抓「跨年代均勻取樣」正好 PILOT_SAMPLE_N 集 + 建 manifest
  python fetch_transcripts.py --all      # 抓全部 671 集（將來全量階段）
  python fetch_transcripts.py --eps 660 661 662   # 抓指定集（缺號會報錯）
  python fetch_transcripts.py --gap 1.5  # 調整每集間隔
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
from requests.adapters import HTTPAdapter

try:                                              # urllib3 路徑相容
    from urllib3.util.retry import Retry
except Exception:                                 # pragma: no cover
    from requests.packages.urllib3.util.retry import Retry  # type: ignore

HERE = Path(__file__).resolve().parent
TRANSCRIPT_DIR = HERE / "transcripts"
MANIFEST_PATH = HERE / "data" / "episode_manifest.json"
PILOT_PATH = HERE / "data" / "pilot_episodes.json"

EPISODES_URL = "https://whatmkreallysaid.com/episodes.json"
TRANSCRIPT_BASE = "https://whatmkreallysaid.com/episodes/"
RSS_URL = "https://feeds.soundon.fm/podcasts/954689a5-3096-43a4-a80b-7810b219cef3.xml"

HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124 Safari/537.36")}
DEFAULT_REQUEST_GAP_SEC = 1.0     # 禮貌間隔（對非官方站，預設拉到 1s）
PILOT_SAMPLE_N = 40               # pilot dry-run 取樣集數（跨年代均勻、含首尾、正好 N）
MIN_TRANSCRIPT_BYTES = 300        # 低於此視為失敗/半檔
RSS_MIN_EPISODES = 100            # RSS 解析少於此 → 視為被擋/格式變，禁止 fallback
try:                                              # 缺 IANA tzdata 環境 fallback 到固定 UTC+8
    TAIPEI = ZoneInfo("Asia/Taipei")
except Exception:                                 # pragma: no cover
    from datetime import timedelta, timezone
    TAIPEI = timezone(timedelta(hours=8))
EP_RE = re.compile(r"\bEP\s*[.#-]?\s*(\d+)\b", re.IGNORECASE)


def configure_stdio() -> None:
    """Windows cp950 console 遇 emoji/中文 log 會炸 → 固定 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
        except Exception:
            pass


def make_session() -> requests.Session:
    s = requests.Session()
    _kw = dict(total=5, connect=3, read=3, status=5, backoff_factor=1.0,
               status_forcelist=(429, 500, 502, 503, 504),
               respect_retry_after_header=True)
    try:                                          # 新版 urllib3
        retry = Retry(allowed_methods=frozenset(["GET"]), **_kw)
    except TypeError:                             # 舊版 urllib3
        retry = Retry(method_whitelist=frozenset(["GET"]), **_kw)
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(HEADERS)
    return s


SESSION = make_session()


def log(msg: str) -> None:
    print(f"[transcripts] {msg}", flush=True)


def fetch_index() -> list[dict]:
    """whatmkreallysaid 全集 metadata（list）。number 正規化為 int；缺號/重複 → 報錯。"""
    r = SESSION.get(EPISODES_URL, timeout=(10, 30))
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError("episodes.json 格式錯誤：預期 list")
    out: list[dict] = []
    bad: list[dict] = []
    for raw in data:
        if not isinstance(raw, dict) or raw.get("number") in (None, ""):
            bad.append(raw)
            continue
        try:
            row = dict(raw)
            row["number"] = int(row["number"])
            out.append(row)
        except Exception:
            bad.append(raw)
    if bad:
        raise RuntimeError(f"episodes.json 有 {len(bad)} 筆缺少或無法解析 number，不能靜默丟棄")
    nums = [r["number"] for r in out]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        raise RuntimeError(f"episodes.json 有重複 EP 編號：{dup[:20]}")
    fns = [r.get("filename") for r in out if r.get("filename")]
    dup_fn = sorted({f for f in fns if fns.count(f) > 1})
    if dup_fn:
        raise RuntimeError(f"episodes.json 有重複 filename（會覆寫同一份來源）：{dup_fn[:10]}")
    return out


def _rss_published_date(value: str) -> str:
    """RFC822 → Asia/Taipei 的日曆日（聽眾所在時區的發布日）。"""
    dt = parsedate_to_datetime(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TAIPEI)
    return dt.astimezone(TAIPEI).date().isoformat()


def fetch_rss_dates() -> dict[int, dict]:
    """SoundOn RSS → {ep: {date, published_raw, guid, audio_url, title}}（權威 t0 來源）。"""
    resp = SESSION.get(RSS_URL, timeout=(10, 30))
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    out: dict[int, dict] = {}
    for e in feed.entries:
        title = str(e.get("title", ""))
        m = EP_RE.search(title)
        if not m:
            continue
        num = int(m.group(1))
        if num in out:
            raise RuntimeError(f"RSS 有重複 EP{num}，無法決定權威日期")
        published_raw = str(e.get("published", "") or e.get("updated", ""))
        if not published_raw:
            raise RuntimeError(f"RSS EP{num} 缺 published，無法產生 t0_date")
        audio = next((enc.get("href") for enc in (e.get("enclosures") or [])
                      if enc.get("href")), "")
        out[num] = {"date": _rss_published_date(published_raw),
                    "published_raw": published_raw,
                    "guid": str(e.get("id") or ""),
                    "audio_url": audio, "title": title}
    if len(out) < RSS_MIN_EPISODES:
        raise RuntimeError(f"RSS 只解析到 {len(out)} 集，疑似被擋或格式改變，"
                           f"禁止整批 fallback 到站台日期")
    return out


def transcript_path(ep: int) -> Path:
    return TRANSCRIPT_DIR / f"EP{ep}.md"


def build_manifest(*, allow_site_date_fallback: bool = False) -> list[dict]:
    """合併 index + RSS，產出每集事件骨架（t0 以 RSS 為準）。"""
    index = fetch_index()
    rss = fetch_rss_dates()
    log(f"index={len(index)} 集；RSS={len(rss)} 集")
    manifest, fallback_eps, missing_t0, mismatch = [], [], [], 0
    for e in index:
        num = int(e["number"])
        site_date = str(e.get("date") or "")
        rss_row = rss.get(num, {})
        rss_date = str(rss_row.get("date") or "")
        t0 = rss_date or site_date
        source = "rss" if rss_date else ("site" if site_date else "none")
        if source == "site":
            fallback_eps.append(num)
        if not t0:
            missing_t0.append(num)
        if rss_date and site_date and rss_date != site_date:
            mismatch += 1
        manifest.append({
            "ep": num,
            "title": str(e.get("title", "")),
            "display_title": str(e.get("display_title", "")),
            "filename": str(e.get("filename", "")),
            "summary": str(e.get("summary", "")),
            "t0_date": t0,                          # ← 事件研究進場基準日
            "date_source": source,
            "date_needs_review": source != "rss",
            "site_date": site_date,
            "rss_date": rss_date,
            "rss_published_raw": rss_row.get("published_raw", ""),
            "date_mismatch": bool(rss_date and site_date and rss_date != site_date),
            "guid": rss_row.get("guid", ""),
            "audio_url": rss_row.get("audio_url", ""),
            "transcript_path": str(transcript_path(num).relative_to(HERE)),
        })
    if missing_t0:
        raise RuntimeError(f"以下 EP 沒有任何 t0_date：{missing_t0[:50]}")
    if fallback_eps and not allow_site_date_fallback:
        raise RuntimeError(
            f"以下 EP 缺 RSS 日期、會 fallback 到站台日期（不可信，會污染事件研究）："
            f"{fallback_eps[:50]}；人工確認可接受才加 --allow-site-date-fallback")
    manifest.sort(key=lambda r: r["ep"])
    log(f"date_mismatch（站台≠RSS）={mismatch}/{len(manifest)} "
        f"({100*mismatch/max(len(manifest),1):.1f}%) → 一律採用 RSS"
        + (f"；site fallback {len(fallback_eps)} 集" if fallback_eps else ""))
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=True, indent=2),
                             encoding="utf-8")
    log(f"manifest 寫入 {MANIFEST_PATH}")
    return manifest


def _valid_cached_chars(path: Path) -> int | None:
    """已下載且通過驗證 → 回字元數；否則 None（需重抓）。"""
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        if len(raw) < MIN_TRANSCRIPT_BYTES:
            return None
        text = raw.decode("utf-8")
        if "<html" in text[:500].lower():
            return None
        return len(text)
    except UnicodeDecodeError:
        return None


def fetch_transcript(row: dict, force: bool = False) -> int:
    """抓單集全文 → transcripts/EP<n>.md（atomic）。回傳字元數；失敗丟例外（不吞）。"""
    ep = int(row["ep"])
    dest = transcript_path(ep)
    if not force:
        cached = _valid_cached_chars(dest)
        if cached is not None:
            return cached
    fn = row.get("filename") or ""
    if not fn:
        raise RuntimeError(f"EP{ep}: 無 filename")
    url = TRANSCRIPT_BASE + urllib.parse.quote(fn, safe="")   # safe="" → 含 / 也編碼
    r = SESSION.get(url, timeout=(10, 60))
    r.raise_for_status()
    if len(r.content) < MIN_TRANSCRIPT_BYTES:
        raise RuntimeError(f"EP{ep}: 內容過短 len={len(r.content)}")
    text = r.content.decode("utf-8")
    if "<html" in text[:500].lower():
        raise RuntimeError(f"EP{ep}: 疑似抓到 HTML 錯誤頁")
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)
    return len(text)


def pilot_sample(manifest: list[dict], n: int) -> list[dict]:
    """跨年代均勻取樣：含首尾、去重、正好 n 集（n>len 時回全部）。"""
    eps = [r for r in manifest if r.get("filename")]
    if n <= 0:
        return []
    if len(eps) <= n:
        return eps
    if n == 1:
        return [eps[-1]]
    positions: list[int] = []
    seen: set[int] = set()
    for i in range(n):
        pos = round(i * (len(eps) - 1) / (n - 1))
        if pos not in seen:
            positions.append(pos)
            seen.add(pos)
    p = len(eps) - 1                       # 罕見去重後不足 → 由尾端補滿，仍維持 exactly n
    while len(positions) < n and p >= 0:
        if p not in seen:
            positions.append(p)
            seen.add(p)
        p -= 1
    return [eps[i] for i in sorted(positions[:n])]


def main() -> int:
    configure_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="抓全部集數")
    ap.add_argument("--eps", nargs="+", type=int, help="抓指定 EP 編號（缺號會報錯）")
    ap.add_argument("--force", action="store_true", help="已存在也重抓")
    ap.add_argument("--gap", type=float, default=DEFAULT_REQUEST_GAP_SEC,
                    help="每集抓取間隔秒數")
    ap.add_argument("--allow-site-date-fallback", action="store_true",
                    help="允許缺 RSS 日期者退回站台日期（預設禁止）")
    args = ap.parse_args()

    manifest = build_manifest(allow_site_date_fallback=args.allow_site_date_fallback)
    by_ep = {r["ep"]: r for r in manifest}

    if args.eps:
        missing = [e for e in args.eps if e not in by_ep]
        if missing:
            raise SystemExit(f"指定 EP 不在 manifest：{missing}")
        targets = [by_ep[e] for e in args.eps]
        mode = "eps"
    elif args.all:
        targets = [r for r in manifest if r.get("filename")]
        mode = "all"
    else:
        targets = pilot_sample(manifest, PILOT_SAMPLE_N)
        mode = "pilot"

    log(f"模式={mode}，準備抓取 {len(targets)} 集逐字稿（gap={args.gap}s）…")
    total_chars, ok, failed, ok_eps = 0, 0, [], []
    for i, row in enumerate(targets, 1):
        try:
            chars = fetch_transcript(row, force=args.force)
            ok += 1
            total_chars += chars
            ok_eps.append(row["ep"])
        except Exception as exc:
            failed.append(row["ep"])
            log(f"EP{row['ep']}: 失敗 {str(exc)[:120]}")
        if i % 10 == 0 or i == len(targets):
            log(f"  進度 {i}/{len(targets)}，成功 {ok}，失敗 {len(failed)}，累計 {total_chars:,} 字")
        time.sleep(max(args.gap, 0.0))

    log(f"完成：{ok}/{len(targets)} 集，共 {total_chars:,} 字，存於 {TRANSCRIPT_DIR}")

    # 只有 pilot 模式才寫 pilot_episodes.json，且只含成功抓到的集（不污染 dry-run 輸入）
    if mode == "pilot":
        PILOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PILOT_PATH.write_text(
            json.dumps([by_ep[e] for e in ok_eps], ensure_ascii=True, indent=2),
            encoding="utf-8")
        log(f"pilot 取樣清單寫入 {PILOT_PATH}（{len(ok_eps)} 集）")

    if failed:
        log(f"失敗 EP：{failed}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
