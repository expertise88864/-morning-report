"""亞洲職棒冠軍爭霸賽(亞冠賽):中華隊賽程與比分(使用者 2026-11-08)。

中職資料源(Yahoo 中職 scoreboard)只收聯盟主辦的比賽,國家隊賽事不在裡面。
這裡改讀中文維基百科「{年}年亞洲職棒冠軍爭霸賽」的 `{{linescore}}` 逐場模板
(與中職戰績的 Wikipedia 備援同一個來源;GitHub Actions 可連)。

- 只在 11 月查(亞冠賽在 11 月;頁面不存在 = 今年沒辦,靜默回空)。
- 只在賽期前一天到賽後一天顯示(使用者:「在亞冠賽期間列出」)。
- 只列中華隊的場次;冠軍賽/季軍賽對手未定時列「待定」,中華隊排進其中一場後就只列那一場。
- 比分要兩隊都是整數、且開賽已過 3 小時才當完賽(維基可能在比賽中途被逐局編輯)。
- 頁面格式認不出(有模板卻缺日期/時間)→ 拋例外,呼叫端整段不顯示並記降級。
"""

from __future__ import annotations

import datetime as dt
import re

TPE = dt.timezone(dt.timedelta(hours=8))
JST = dt.timezone(dt.timedelta(hours=9))
TEAMS = {"TPE": "中華隊", "JPN": "日本", "KOR": "韓國", "AUS": "澳洲"}
_FINALS = ("季軍賽", "冠軍賽")
_WIKI = "https://zh.wikipedia.org/w/api.php"
_FIELD = r"\|\s*{}\s*=\s*([^|\n}}]*)"


def _field(block: str, key: str) -> str:
    m = re.search(_FIELD.format(key), block)
    return m.group(1).strip() if m else ""


def _runs(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def parse(wikitext: str) -> list[dict]:
    """維基原始碼 → 逐場 [{stage, start(台北), away, home, away_score, home_score}]。"""
    games, stage = [], "預賽"
    pieces = re.split(r"(\{\{linescore|^==+[^=\n]+==+\s*$)", wikitext or "", flags=re.M | re.I)
    for i, piece in enumerate(pieces):
        heading = re.fullmatch(r"==+\s*([^=\n]+?)\s*==+\s*", piece)
        if heading:
            stage = next((s for s in _FINALS if s in heading.group(1)), stage)
            continue
        if piece.lower() != "{{linescore" or i + 1 >= len(pieces):
            continue
        block = pieces[i + 1]
        day = re.search(r"\|\s*Date\s*=\s*(\d{4})年(\d{1,2})月(\d{1,2})日", block)
        clock = re.search(r"\|\s*Time\s*=\s*(\d{1,2}):(\d{2})", block)
        if not (day and clock):
            raise ValueError("亞冠賽 linescore 缺日期或時間")
        start = dt.datetime(*map(int, day.groups()), *map(int, clock.groups()), tzinfo=JST)
        games.append({"stage": stage, "start": start.astimezone(TPE),
                      "away": _field(block, "RoadAbr"), "home": _field(block, "HomeAbr"),
                      "away_score": _runs(_field(block, "RR")), "home_score": _runs(_field(block, "HR"))})
    return games


def select(games: list[dict], now: dt.datetime) -> dict:
    """賽期前一天到賽後一天:中華隊場次(含未定對手的冠軍/季軍賽)+ 預賽戰績。其餘回空。"""
    if not games:
        return {}
    first, last = min(g["start"] for g in games), max(g["start"] for g in games)
    if not (first.date() - dt.timedelta(days=1) <= now.astimezone(TPE).date()
            <= last.date() + dt.timedelta(days=1)):
        return {}
    ours = lambda g: "TPE" in (g["away"], g["home"])   # noqa: E731
    placed = any(ours(g) for g in games if g["stage"] in _FINALS)
    rows, record = [], [0, 0, 0]
    for g in sorted(games, key=lambda g: g["start"]):
        tbd = g["stage"] in _FINALS and not (g["away"] and g["home"]) and not placed   # 只填一隊也算未定
        if not (ours(g) or tbd):
            continue
        done = (g["away_score"] is not None and g["home_score"] is not None
                and g["start"] <= now - dt.timedelta(hours=3))
        if done and g["stage"] == "預賽":
            ours_runs, theirs = ((g["away_score"], g["home_score"]) if g["away"] == "TPE"
                                 else (g["home_score"], g["away_score"]))
            record[0 if ours_runs > theirs else 1 if ours_runs < theirs else 2] += 1
        rows.append({"stage": g["stage"], "when": g["start"].strftime("%m/%d %H:%M"),
                     "away": TEAMS.get(g["away"], "待定"), "home": TEAMS.get(g["home"], "待定"),
                     "score": (g["away_score"], g["home_score"]) if done else None})
    return {"rows": rows, "record": record} if rows else {}


def fetch(get, now: dt.datetime, log, degrade) -> dict:
    """回 {"apbc": …} 或空;失敗時 `log` 留 ::warning::、`degrade` 記降級標籤。"""
    if now.month != 11:
        return {}
    try:
        r = get(_WIKI, params={"action": "parse", "page": f"{now.year}年亞洲職棒冠軍爭霸賽",
                               "prop": "wikitext", "format": "json", "formatversion": "2"},
                timeout=20, headers={"User-Agent": "MorningReportBot/1.0"})
        r.raise_for_status()
        body = r.json()
        if (body.get("error") or {}).get("code") == "missingtitle":
            return {}   # 今年沒有亞冠賽
        if body.get("error") or "parse" not in body:   # 200 卻是 API 錯誤 ≠ 沒資料
            raise ValueError(f"Wikipedia API 錯誤: {str(body.get('error'))[:80]}")
        found = select(parse((body.get("parse") or {}).get("wikitext") or ""), now)
        return {"apbc": found} if found else {}
    except Exception as exc:
        log(f"::warning::[sports] 亞冠賽抓取失敗(整段不顯示): {type(exc).__name__}: {exc}")
        degrade("sports:apbc_failed")
        return {}


def render(apbc: dict, esc) -> str:
    """亞冠賽小區塊:標題附預賽戰績,逐場「日期 時間 階段 客 vs 主」或比分(勝方紅字)。"""
    def side(team, runs, win):
        cell = f"{esc(team)} {runs}"
        return f"<b style='color:#b91c1c;'>{cell}</b>" if win else cell
    lines = []
    for r in apbc.get("rows") or []:
        if r["score"]:
            a, h = r["score"]
            match = f"{side(r['away'], a, a > h)}　:　{side(r['home'], h, h > a)}"
        else:
            match = f"{esc(r['away'])} vs {esc(r['home'])}"
        lines.append(f"<div style='font-size:13px;color:#334155;line-height:1.9;'>"
                     f"<span style='color:#94a3b8;'>{esc(r['when'])}</span>　{esc(r['stage'])}　{match}</div>")
    w, lo, t = apbc.get("record") or (0, 0, 0)
    record = f"　預賽 {w} 勝 {lo} 敗" + (f" {t} 和" if t else "") if w + lo + t else ""
    return ("<div style='margin:8px 0;'><b style='color:#0f172a;'>亞冠賽 中華隊（東京巨蛋;台北時間）</b>"
            f"<span style='font-size:12px;color:#64748b;'>{record}</span>" + "".join(lines) + "</div>")
