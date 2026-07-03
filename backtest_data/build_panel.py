# -*- coding: utf-8 -*-
"""
Build backtest_data/panel.csv + quality.json per the unified backtest protocol.

- TWSE (monthly, cached, >=3s between requests):
    TAIEX OHLC : /indicesReport/MI_5MINS_HIST?response=json&date=YYYYMM01
    2330 OHLC  : /exchangeReport/STOCK_DAY?response=json&date=YYYYMM01&stockNo=2330
    Ex-div     : /exchangeReport/TWT49U (yearly)
- US (yfinance with backoff retry; stooq CSV fallback): ^SOX, TSM, EWT, QQQ closes.

Alignment: TW trading day D -> most recent US trading-day close strictly before D.
Drop sample if calendar gap > 4 days (stale).
y_taiex = TAIEX open(D)/close(D-1) - 1 (%)
y_2330  = (2330 open(D) + div(D)) / close(D-1) - 1 (%)   [div added back on ex-div days]
"""
import io
import json
import os
import re
import time
from datetime import date, datetime

import pandas as pd
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache")
os.makedirs(CACHE, exist_ok=True)
LOG_PATH = os.path.join(BASE, "build.log")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

START_MONTH = (2021, 7)
END_MONTH = (2026, 6)

# Gold anchors
ANCHOR_TAIEX_OPEN = {
    "2026-06-09": 43687.62,
    "2026-06-10": 44581.45,
    "2026-06-11": 43172.21,
}
ANCHOR_2330_OPEN = {
    "2026-06-09": (2295.0, 2315.0),  # "around 2305"
    "2026-06-10": (2285.0, 2285.0),  # exact
}


def log(msg):
    line = "%s %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def month_iter():
    y, m = START_MONTH
    while (y, m) <= END_MONTH:
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def parse_num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").replace("+", "").strip()
    if s in ("", "--", "-", "X", "N/A", "0.00X"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_roc_date(s):
    """Parse '110/07/01' or '110年07月01日' -> date."""
    nums = re.findall(r"\d+", str(s))
    if len(nums) < 3:
        return None
    y, m, d = int(nums[0]), int(nums[1]), int(nums[2])
    if y < 1000:
        y += 1911
    return date(y, m, d)


def twse_get_json(url, params, cache_name, max_tries=6):
    """GET TWSE JSON with cache, rate limit (>=3s), 30s backoff on 403/429."""
    cache_path = os.path.join(CACHE, cache_name)
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    last_err = None
    for attempt in range(max_tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
            if r.status_code in (403, 429):
                log("TWSE %s -> HTTP %d, sleep 30s" % (cache_name, r.status_code))
                time.sleep(30)
                continue
            r.raise_for_status()
            j = r.json()
            stat = str(j.get("stat", ""))
            if stat != "OK":
                # "no data" is a valid terminal answer -> cache it
                j = {"stat": "NO_DATA", "orig_stat": stat}
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(j, f, ensure_ascii=False)
            time.sleep(3)
            return j
        except Exception as e:  # noqa: BLE001
            last_err = e
            log("TWSE %s attempt %d failed: %r -> sleep 30s" % (cache_name, attempt + 1, e))
            time.sleep(30)
    raise RuntimeError("TWSE fetch failed for %s: %r" % (cache_name, last_err))


def fetch_month_validated(url, params, cache_name, y, m, max_attempts=4):
    """Fetch a TWSE month and validate it really contains that month's rows.

    TWSE transiently returns NO_DATA or even a wrong month's data (observed
    2026-06-11: request for 2021-10 returned 2017-12 rows). Bad cache entries
    are deleted and refetched.
    """
    cache_path = os.path.join(CACHE, cache_name)
    for attempt in range(max_attempts):
        j = twse_get_json(url, params, cache_name)
        ok = j.get("stat") != "NO_DATA" and j.get("data")
        if ok:
            dates = [parse_roc_date(r[0]) for r in j["data"]]
            ok = all(d is not None and (d.year, d.month) == (y, m) for d in dates)
        if ok:
            return j
        log("%s invalid (stat=%s, attempt %d) -> delete cache, refetch"
            % (cache_name, j.get("stat"), attempt + 1))
        if os.path.exists(cache_path):
            os.remove(cache_path)
        time.sleep(10)
    raise RuntimeError("month %04d-%02d still invalid after %d attempts (%s)"
                       % (y, m, max_attempts, cache_name))


def fetch_taiex():
    """Return DataFrame indexed by date: open, close."""
    rows = []
    for y, m in month_iter():
        j = fetch_month_validated(
            "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST",
            {"response": "json", "date": "%04d%02d01" % (y, m)},
            "taiex_%04d%02d.json" % (y, m), y, m,
        )
        for row in j.get("data", []):
            d = parse_roc_date(row[0])
            o = parse_num(row[1])
            c = parse_num(row[4])
            if d and o is not None and c is not None:
                rows.append((d, o, c))
        log("TAIEX %04d-%02d: %d days" % (y, m, len(j.get("data", []))))
    df = pd.DataFrame(rows, columns=["date", "open", "close"]).drop_duplicates("date")
    return df.sort_values("date").set_index("date")


def fetch_2330():
    """Return DataFrame indexed by date: open, close (None for no-trade '--')."""
    rows = []
    for y, m in month_iter():
        j = fetch_month_validated(
            "https://www.twse.com.tw/exchangeReport/STOCK_DAY",
            {"response": "json", "date": "%04d%02d01" % (y, m), "stockNo": "2330"},
            "stock2330_%04d%02d.json" % (y, m), y, m,
        )
        for row in j.get("data", []):
            d = parse_roc_date(row[0])
            o = parse_num(row[3])
            c = parse_num(row[6])
            if d:
                rows.append((d, o, c))
        log("2330 %04d-%02d: %d days" % (y, m, len(j.get("data", []))))
    df = pd.DataFrame(rows, columns=["date", "open", "close"]).drop_duplicates("date")
    return df.sort_values("date").set_index("date")


def fetch_dividends():
    """Return ({date: div_per_share}, set_of_unparseable_exdiv_dates) for 2330."""
    div_map = {}
    bad_dates = set()
    for year in range(START_MONTH[0], END_MONTH[0] + 1):
        s = "%04d0101" % year
        e = "%04d1231" % year
        # NOTE: the working endpoint uses startDate/endDate (verified 2026-06-11);
        # strDate is silently ignored by TWSE and yields "end < start" errors.
        try:
            j = twse_get_json(
                "https://www.twse.com.tw/exchangeReport/TWT49U",
                {"response": "json", "startDate": s, "endDate": e},
                "twt49u_%04d.json" % year,
            )
        except RuntimeError:
            j = None
        if not j or j.get("stat") == "NO_DATA" or not j.get("data"):
            log("TWT49U %d: no data" % year)
            continue
        fields = j.get("fields", [])
        idx_date = idx_code = idx_val = None
        for i, fname in enumerate(fields):
            if idx_date is None and "日期" in fname:
                idx_date = i
            if "代號" in fname:
                idx_code = i
            if "權值" in fname:  # 「權值+息值」
                idx_val = i
        if idx_date is None or idx_code is None or idx_val is None:
            log("TWT49U %d: unexpected fields %r" % (year, fields))
            continue
        n = 0
        for row in j["data"]:
            try:
                if str(row[idx_code]).strip() != "2330":
                    continue
                d = parse_roc_date(row[idx_date])
                v = parse_num(row[idx_val])
                if d is None:
                    continue
                if v is None:
                    bad_dates.add(d)
                else:
                    div_map[d] = div_map.get(d, 0.0) + v
                    n += 1
            except Exception:  # noqa: BLE001
                continue
        log("TWT49U %d: %d ex-div events for 2330" % (year, n))
    return div_map, bad_dates


# ---------------- US data ----------------

US_SYMBOLS = ["^SOX", "TSM", "EWT", "QQQ"]
STOOQ_MAP = {"^SOX": "^sox", "TSM": "tsm.us", "EWT": "ewt.us", "QQQ": "qqq.us"}
COL_MAP = {"^SOX": "sox_pct", "TSM": "tsm_pct", "EWT": "ewt_pct", "QQQ": "qqq_pct"}


def us_cache_path(sym):
    return os.path.join(CACHE, "us_%s.csv" % sym.replace("^", "").lower())


def load_us_cache(sym):
    p = us_cache_path(sym)
    if os.path.exists(p):
        df = pd.read_csv(p, parse_dates=["Date"])
        if len(df) > 100:
            src = df["source"].iloc[0] if "source" in df.columns else "cache"
            return df, src
    return None, None


def try_yfinance(sym):
    import yfinance as yf
    h = yf.Ticker(sym).history(period="5y", auto_adjust=True)
    if h is None or len(h) < 100:
        raise RuntimeError("yfinance empty for %s (n=%s)" % (sym, 0 if h is None else len(h)))
    out = pd.DataFrame({
        "Date": [pd.Timestamp(t).date() for t in h.index],
        "Close": h["Close"].values,
    })
    out["Date"] = pd.to_datetime(out["Date"])
    return out


def try_stooq(sym):
    url = "https://stooq.com/q/d/l/"
    r = requests.get(url, params={"s": STOOQ_MAP[sym], "i": "d"}, headers=UA, timeout=45)
    r.raise_for_status()
    txt = r.text
    if not txt.lower().startswith("date"):
        raise RuntimeError("stooq bad payload for %s: %s" % (sym, txt[:60]))
    df = pd.read_csv(io.StringIO(txt), parse_dates=["Date"])
    df = df[df["Date"] >= pd.Timestamp(2021, 5, 1)][["Date", "Close"]].dropna()
    if len(df) < 100:
        raise RuntimeError("stooq too short for %s (n=%d)" % (sym, len(df)))
    return df


YAHOO_HOST = "query1.finance.yahoo.com"
# Discovered 2026-06-11: default DNS edge IPs (180.222.116.x) are unreachable from
# this network, but these gycpi edge IPs answer. Probed via DoH (dns.google /
# cloudflare-dns.com). yfinance/requests cannot pin an IP, so we shell out to curl.
YAHOO_EDGE_IPS = ["180.222.109.252", "180.222.109.251"]
YAHOO_SYM = {"^SOX": "%5ESOX", "TSM": "TSM", "EWT": "EWT", "QQQ": "QQQ"}
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def try_yahoo_direct(sym, tries_per_ip=3):
    """Yahoo v8 chart API via curl.exe pinned to a working edge IP.

    Same source/fields as yfinance auto_adjust=True (uses adjclose).
    """
    import subprocess
    url = ("https://%s/v8/finance/chart/%s?range=5y&interval=1d"
           "&includeAdjustedClose=true&events=div%%2Csplits"
           % (YAHOO_HOST, YAHOO_SYM[sym]))
    body = None
    for t in range(tries_per_ip):
        for ip in YAHOO_EDGE_IPS:
            r = subprocess.run(
                ["curl.exe", "-s", "-m", "30",
                 "--resolve", "%s:443:%s" % (YAHOO_HOST, ip),
                 "-A", BROWSER_UA, url],
                capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip().startswith("{"):
                body = r.stdout
                break
            time.sleep(8)
        if body:
            break
    if not body:
        raise RuntimeError("yahoo-direct: no edge IP responded for %s" % sym)
    j = json.loads(body)
    res = j["chart"]["result"][0]
    ts = res["timestamp"]
    closes = res["indicators"].get("adjclose", [{}])[0].get("adjclose") \
        or res["indicators"]["quote"][0]["close"]
    rows = []
    from datetime import timezone
    for t_, c in zip(ts, closes):
        if c is None:
            continue
        rows.append((datetime.fromtimestamp(t_, tz=timezone.utc).date(), float(c)))
    df = pd.DataFrame(rows, columns=["Date", "Close"]).drop_duplicates("Date")
    df["Date"] = pd.to_datetime(df["Date"])
    if len(df) < 100:
        raise RuntimeError("yahoo-direct too short for %s (n=%d)" % (sym, len(df)))
    return df.sort_values("Date")


def fetch_us(max_rounds=12, round_sleep=120, sym_sleep=5):
    """Returns (closes: {sym: df(Date, Close)}, sources: {sym: str}, ok: bool)."""
    closes, sources = {}, {}
    for sym in US_SYMBOLS:
        df, src = load_us_cache(sym)
        if df is not None:
            closes[sym] = df[["Date", "Close"]]
            sources[sym] = src
            log("US %s: loaded from cache (%s, n=%d)" % (sym, src, len(df)))

    pending = [s for s in US_SYMBOLS if s not in closes]
    rnd = 0
    while pending and rnd < max_rounds:
        rnd += 1
        log("US fetch round %d/%d, pending=%s" % (rnd, max_rounds, pending))
        still = []
        for sym in pending:
            got = None
            src = None
            try:
                got = try_yfinance(sym)
                src = "yfinance"
            except Exception as e:  # noqa: BLE001
                log("US %s yfinance failed: %r" % (sym, e))
                try:
                    got = try_stooq(sym)
                    src = "stooq"
                except Exception as e2:  # noqa: BLE001
                    log("US %s stooq failed: %r" % (sym, e2))
                    try:
                        got = try_yahoo_direct(sym)
                        src = "yahoo-chart-api-direct-ip"
                    except Exception as e3:  # noqa: BLE001
                        log("US %s yahoo-direct failed: %r" % (sym, e3))
            if got is not None:
                got = got.sort_values("Date").drop_duplicates("Date")
                got["source"] = src
                got.to_csv(us_cache_path(sym), index=False)
                closes[sym] = got[["Date", "Close"]]
                sources[sym] = src
                log("US %s: OK via %s (n=%d, %s..%s)" % (
                    sym, src, len(got),
                    got["Date"].min().date(), got["Date"].max().date()))
            else:
                still.append(sym)
            time.sleep(sym_sleep)
        pending = still
        if pending and rnd < max_rounds:
            log("US round %d done, %s still pending; sleep %ds" % (rnd, pending, round_sleep))
            time.sleep(round_sleep)
    ok = len(closes) == len(US_SYMBOLS)
    return closes, sources, ok


def build_us_returns(closes):
    """Inner-join closes on Date; pct-change in %, indexed by date."""
    ser = []
    for sym in US_SYMBOLS:
        df = closes[sym].set_index("Date")["Close"].rename(COL_MAP[sym])
        ser.append(df)
    joined = pd.concat(ser, axis=1, join="inner").sort_index()
    ret = joined.pct_change() * 100.0
    ret = ret.dropna()
    ret.index = [t.date() for t in ret.index]
    return ret


# ---------------- panel ----------------

def build_panel(taiex, s2330, div_map, bad_div_dates, us_ret):
    tw_days = list(taiex.index)
    us_dates = sorted(us_ret.index) if us_ret is not None else []
    rows = []
    stale_dropped = 0
    no_us_dropped = 0
    exdiv_dropped_2330 = 0
    tw_gap_dropped = 0

    for i in range(1, len(tw_days)):
        D, P = tw_days[i], tw_days[i - 1]
        if (D - P).days > 15:
            # safety net: longest real TW market closure (Chinese New Year) is
            # ~13 calendar days; anything longer means a data hole, and y
            # across the hole would be meaningless
            tw_gap_dropped += 1
            log("WARN tw gap %s -> %s (%d days), row dropped" % (P, D, (D - P).days))
            continue
        taiex_open = taiex.loc[D, "open"]
        taiex_prev = taiex.loc[P, "close"]
        y_taiex = (taiex_open / taiex_prev - 1.0) * 100.0

        # 2330
        o2330 = s2330.loc[D, "open"] if D in s2330.index else None
        c2330p = s2330.loc[P, "close"] if P in s2330.index else None
        div = float(div_map.get(D, 0.0))
        y_2330 = None
        if D in bad_div_dates:
            exdiv_dropped_2330 += 1  # ex-div day without usable dividend value
        elif o2330 is not None and c2330p is not None and c2330p > 0:
            y_2330 = ((o2330 + div) / c2330p - 1.0) * 100.0

        # US predictors: most recent US close strictly before D
        if us_ret is None or not us_dates:
            sox = tsm = ewt = qqq = None
            gap = None
        else:
            import bisect
            k = bisect.bisect_left(us_dates, D)
            if k == 0:
                no_us_dropped += 1
                continue
            ud = us_dates[k - 1]
            gap = (D - ud).days
            if gap > 4:
                stale_dropped += 1
                continue
            r = us_ret.loc[ud]
            sox, tsm, ewt, qqq = (r["sox_pct"], r["tsm_pct"], r["ewt_pct"], r["qqq_pct"])

        rows.append({
            "date": D.isoformat(),
            "taiex_open": round(taiex_open, 2),
            "taiex_prev_close": round(taiex_prev, 2),
            "y_taiex": round(y_taiex, 6),
            "tw2330_open": o2330,
            "tw2330_prev_close": c2330p,
            "tw2330_div": div,
            "y_2330": round(y_2330, 6) if y_2330 is not None else None,
            "sox_pct": round(sox, 6) if sox is not None else None,
            "tsm_pct": round(tsm, 6) if tsm is not None else None,
            "ewt_pct": round(ewt, 6) if ewt is not None else None,
            "qqq_pct": round(qqq, 6) if qqq is not None else None,
            "us_gap_days": gap,
        })

    panel = pd.DataFrame(rows)
    stats = {
        "stale_dropped": stale_dropped,
        "no_us_dropped": no_us_dropped,
        "exdiv_dropped_2330": exdiv_dropped_2330,
        "tw_gap_dropped": tw_gap_dropped,
    }
    return panel, stats


def check_anchors(panel):
    res = {"taiex": {}, "tw2330": {}, "passed": True}
    p = panel.set_index("date")
    for d, want in ANCHOR_TAIEX_OPEN.items():
        got = float(p.loc[d, "taiex_open"]) if d in p.index else None
        ok = got is not None and abs(got - want) < 0.005
        res["taiex"][d] = {"expected": want, "got": got, "ok": bool(ok)}
        if not ok:
            res["passed"] = False
    for d, (lo, hi) in ANCHOR_2330_OPEN.items():
        got = float(p.loc[d, "tw2330_open"]) if d in p.index and pd.notna(p.loc[d, "tw2330_open"]) else None
        ok = got is not None and lo - 1e-9 <= got <= hi + 1e-9
        res["tw2330"][d] = {"expected_range": [lo, hi], "got": got, "ok": bool(ok)}
        if not ok:
            res["passed"] = False
    # 2330 return consistency: y_2330 on 06-10 should equal open(06-10)/close(06-09)-1
    return res


def main():
    log("=== build_panel start ===")
    tw_data_ok = True
    notes = []

    # ---- Taiwan (stable) first ----
    try:
        taiex = fetch_taiex()
        log("TAIEX total days: %d (%s..%s)" % (len(taiex), taiex.index.min(), taiex.index.max()))
        s2330 = fetch_2330()
        log("2330 total days: %d" % len(s2330))
        div_map, bad_div = fetch_dividends()
        log("2330 dividends: %d events, %d unparseable" % (len(div_map), len(bad_div)))
    except Exception as e:  # noqa: BLE001
        log("FATAL TW fetch: %r" % e)
        tw_data_ok = False
        raise

    # ---- US (flaky, with backoff) ----
    closes, us_sources, us_ok = fetch_us()
    us_ret = None
    if us_ok:
        us_ret = build_us_returns(closes)
        log("US returns joined: %d days (%s..%s)" % (len(us_ret), min(us_ret.index), max(us_ret.index)))
    else:
        notes.append("US data incomplete after retries; got: %s" % sorted(us_sources.keys()))
        log("US data NOT ok; proceeding with TW-only panel (US cols empty)")

    panel, stats = build_panel(taiex, s2330, div_map, bad_div, us_ret)
    panel_path = os.path.join(BASE, "panel.csv")
    panel.to_csv(panel_path, index=False, encoding="utf-8")
    log("panel.csv written: %d rows" % len(panel))

    anchors = check_anchors(panel)
    log("anchors passed: %s" % anchors["passed"])

    missing = {c: int(panel[c].isna().sum()) for c in panel.columns}
    quality = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "n_rows": int(len(panel)),
        "date_range": [panel["date"].min(), panel["date"].max()],
        "columns": list(panel.columns),
        "column_desc": {
            "date": "TW trading day D (YYYY-MM-DD)",
            "taiex_open": "TAIEX open on D (TWSE MI_5MINS_HIST)",
            "taiex_prev_close": "TAIEX close on previous TW trading day",
            "y_taiex": "TAIEX open(D)/close(D-1)-1 in %",
            "tw2330_open": "2330 open on D (TWSE STOCK_DAY)",
            "tw2330_prev_close": "2330 close on previous TW trading day",
            "tw2330_div": "2330 cash dividend per share on ex-div day D (TWT49U), else 0",
            "y_2330": "(2330 open(D)+div)/close(D-1)-1 in %; empty if not computable",
            "sox_pct": "^SOX close-to-close % return of most recent US trading day before D",
            "tsm_pct": "TSM (ADR) same",
            "ewt_pct": "EWT same",
            "qqq_pct": "QQQ same",
            "us_gap_days": "calendar days between that US close date and D (rows with >4 dropped)",
        },
        "missing": missing,
        "drops": stats,
        "dividend_events": {d.isoformat(): v for d, v in sorted(div_map.items())},
        "anchors": anchors,
        "tw_data_ok": tw_data_ok,
        "us_data_ok": bool(us_ok),
        "us_sources": us_sources,
        "notes": notes,
        "protocol": {
            "alignment": "predictors = most recent US close strictly before TW day D; stale if gap>4 calendar days",
            "tw_range_requested": "2021-07 .. 2026-06",
        },
    }
    with open(os.path.join(BASE, "quality.json"), "w", encoding="utf-8") as f:
        json.dump(quality, f, ensure_ascii=False, indent=2)
    log("quality.json written")
    log("=== build_panel done ===")


if __name__ == "__main__":
    main()
