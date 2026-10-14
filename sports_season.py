"""賽季感知的體育整理:季後賽系列戰況、昨日比分、今日焦點(不自行連網、不碰 state;
請求一律走呼叫端注入的 `get`,方便離線測試)。

使用者 2026-10-14:季後賽期間要看「系列賽戰況+昨日比分」(例如美聯冠軍戰 守護者 1-0 光芒),
例行賽戰績收起來;中職標「台灣大賽 G1/G2…」與系列戰績;頂部一行「今日焦點」;
各運動依賽季重要性排序(季後賽中的排前面)。

資料來源的事實(2026-10-14 實測):
- ESPN MLB scoreboard 的季後賽場次 `season.type == 3`,`competitions[0].notes[0].headline`
  形如 "ALCS - Game 2",`competitions[0].series` 給 summary 與各隊 wins(預定場次給的是
  開打前的戰績)。
- Yahoo 中職 scoreboard 的季後賽場次 `season_phase_id == "season.phase.postseason"`,
  `game_type` 為 "Taiwan Series" / "Playoff Series";系列戰績欄位是空的,要自己數完賽場次。
"""
from __future__ import annotations

import datetime as dt
import re
from email.utils import parsedate_to_datetime
from typing import Any

TPE = dt.timezone(dt.timedelta(hours=8))
#: 昨日比分 / 即將開打 的時間窗(報告 06:00 寄出,前一晚與當天的場次都在窗內)
WINDOW = dt.timedelta(hours=30)

MLB_ROUNDS = {
    "World Series": "世界大賽", "ALCS": "美聯冠軍戰", "NLCS": "國聯冠軍戰",
    "ALDS": "美聯分區系列賽", "NLDS": "國聯分區系列賽",
    "AL Wild Card": "美聯外卡戰", "NL Wild Card": "國聯外卡戰",
}
#: 例外狀態不是賽果也不是賽程(與 nba_scoreboard.result_row 同一份清單)
_EXCEPTIONAL = {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_DELAYED", "STATUS_SUSPENDED"}
#: 中職季後賽回看天數與完整性餘裕:系列第一場離回看起點不到 _MIN_LEAD 天,就不能確定前面沒有被截掉的場次
LOOKBACK_DAYS, _MIN_LEAD = 21, 7
#: (中文名, 幾戰幾勝的總場數)。季後挑戰賽:五戰三勝,第一名帶一勝優勢 —— 來源比分看不到那一勝,
#: 所以挑戰賽**不重建系列戰績、不據此判定結束**(只有台灣大賽排出它的隊伍才算結束)。
CPBL_SERIES = {"Taiwan Series": ("台灣大賽", 7), "Playoff Series": ("季後挑戰賽", 5)}


def _when(value: Any) -> dt.datetime | None:
    """ESPN ISO("2026-10-14T00:00Z")或 Yahoo RFC 2822 → 台北時間;不可靠就回 None。"""
    text = str(value or "")
    try:
        stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:   # 不能用「有沒有 T」分辨:Yahoo 的 Tue / Thu 也有大寫 T
        try:
            stamp = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
    return stamp.astimezone(TPE) if stamp.tzinfo else None


def _round_game(headline: str) -> tuple[str, int | None]:
    """"ALCS - Game 2" → ("ALCS", 2);認不得的回 ("", None)。"""
    match = re.match(r"\s*(.+?)\s*-\s*Game\s+(\d+)\s*$", str(headline or ""))
    if not match or match[1] not in MLB_ROUNDS:
        return "", None
    return match[1], int(match[2])


def mlb_postseason(events: list[dict], now: dt.datetime) -> dict:
    """ESPN MLB events → {"series", "results", "upcoming"};非季後賽或無資料回空 dict。

    系列戰況取每個回合**開球時間最晚**的那一場(預定場次的 wins 是開打前戰績,
    已完賽場次是賽後戰績),所以永遠是此刻的戰況。
    """
    latest: dict[tuple, tuple[dt.datetime, dict]] = {}
    nearest: dict[tuple, tuple[dt.datetime, dict]] = {}   # 每個系列最近的一場未打比賽
    results = []
    for event in events or []:
        if (event.get("season") or {}).get("type") != 3:
            continue
        comp = (event.get("competitions") or [{}])[0]
        rnd, game = _round_game(((comp.get("notes") or [{}])[0]).get("headline", ""))
        ko = _when(event.get("date"))
        teams = comp.get("competitors") or []
        if not rnd or ko is None or len(teams) != 2:
            continue
        abbr = {str(t.get("id") or (t.get("team") or {}).get("id")):
                str((t.get("team") or {}).get("abbreviation") or "?") for t in teams}
        side = {t.get("homeAway"): t for t in teams}
        away, home = side.get("away", teams[0]), side.get("home", teams[1])
        status = ((event.get("status") or {}).get("type") or {})
        key = (rnd, frozenset(abbr))   # 同一回合可能有兩組對戰(ALDS / NLDS)
        exceptional = status.get("name") in _EXCEPTIONAL   # 延賽/取消:不是比分、也不是下一場
        day = (ko.date() - now.astimezone(TPE).date()).days
        row = {"round": MLB_ROUNDS[rnd], "game": game, "when": ko.strftime("%m/%d %H:%M"),
               "day": {0: "今日", -1: "昨日"}.get(day, ko.strftime("%m/%d")),
               "start": ko.isoformat(),
               "away": abbr.get(str(away.get("id")), "?"), "home": abbr.get(str(home.get("id")), "?")}
        if exceptional:
            pass
        elif status.get("completed") and now - WINDOW <= ko <= now:
            row.update(away_score=str(away.get("score", "")), home_score=str(home.get("score", "")),
                       winner=next((abbr.get(str(t.get("id")), "") for t in teams if t.get("winner")), ""))
            results.append(row)
        elif status.get("state") == "pre" and ko >= now and (key not in nearest or ko < nearest[key][0]):
            nearest[key] = (ko, row)   # 休息日也要列下一場(不受 30 小時窗限制)
        series = comp.get("series") or {}
        wins = [(abbr.get(str(c.get("id")), "?"), int(c.get("wins") or 0))
                for c in series.get("competitors") or []]
        if len(wins) == 2 and (key not in latest or ko > latest[key][0]):
            latest[key] = (ko, {"round": MLB_ROUNDS[rnd], "teams": sorted(wins, key=lambda w: -w[1]),
                                "best_of": int(series.get("totalCompetitions") or 7),
                                "completed": bool(series.get("completed"))})
    order = list(MLB_ROUNDS)
    ranked = sorted(latest.items(), key=lambda kv: (order.index(kv[0][0]), kv[1][0]))
    # 已結束的系列只在它最後一場還在窗內時列(晉級消息);更早結束的不再佔版面
    shown = [s for _key, (ko, s) in ranked if not s["completed"] or now - ko <= WINDOW]
    upcoming = [row for key, (_ko, row) in nearest.items() if not (latest.get(key, (0, {}))[1]).get("completed")]
    if not shown and not results and not upcoming:
        return {}
    return {"series": shown, "results": sorted(results, key=lambda r: r["when"]),
            "upcoming": sorted(upcoming, key=lambda r: r["when"])}


def yahoo_cpbl_games(scoreboard: Any) -> list[dict]:
    """Yahoo scoreboard → 精簡場次清單(只留判斷季後賽需要的欄位)。

    **休兵日與資料缺漏要分開**(2026-10-14 實測):沒比賽的日子 Yahoo 回的 scoreboard
    只有 leagues / divisions、根本沒有 `games` 鍵 —— 那是合法的空日。但連中職的 league
    都沒有、或有 games 卻沒有 teams,就是缺資料:拋例外,讓呼叫端整段放棄(漏一場會讓
    戰績與 G 編號整個錯位)。
    """
    from sports_quality import cpbl_result_note
    if (not isinstance(scoreboard, dict)
            or ((scoreboard.get("leagues") or {}).get("cpbl") or {}).get("league_id") != "cpbl"):
        raise ValueError("Yahoo scoreboard 缺少中職聯盟資訊")
    games, teams = scoreboard.get("games"), scoreboard.get("teams")
    if games is None:
        return []   # 休兵日
    if not isinstance(games, dict) or not isinstance(teams, dict):
        raise ValueError("Yahoo scoreboard 有場次卻缺球隊資料")
    out = []
    for gid, g in games.items():
        final = g.get("status_type") == "status.type.final"

        def name(key):
            found = (teams.get(g.get(key)) or {}).get("display_name")
            if not found and final and g.get("season_phase_id") == "season.phase.postseason":
                raise ValueError(f"中職季後賽完賽場次缺球隊身分:{gid}")   # 勝場會合併到同一個未知隊
            return str(found or "待定")
        out.append({"gid": gid, "start": _when(g.get("start_time")),
                    "postseason": g.get("season_phase_id") == "season.phase.postseason",
                    "game_type": str(g.get("game_type") or ""), "status": str(g.get("status_type") or ""),
                    "away": name("away_team_id"), "home": name("home_team_id"),
                    "away_score": g.get("total_away_points"), "home_score": g.get("total_home_points"),
                    # 與「中華職棒 最新賽果」同一個判準:標示完賽但局數不足 → 比分暫列、不判勝方
                    "provisional": cpbl_result_note(g) if g.get("status_type") == "status.type.final" else ""})
    return out


def cpbl_postseason(games: list[dict], now: dt.datetime, since: dt.date | None = None) -> dict:
    """中職季後賽 → {"series": [進行中的系列…]};沒有就回空。

    每個系列:戰績 + 每場 G 編號 + 昨日比分 + 下一場。「進行中」= 還有未打的場次,
    或最後一場在 30 小時內打完。**季後挑戰賽還在打時台灣大賽賽程可能已公布**,兩者都要列
    (先列 30 小時內有比分的,再依下一場時間)—— 只挑一個會把還在打的系列整個藏起來。
    `since` = 回看的第一天:系列第一場離它不到 7 天,可能有更早的場次沒抓到 → 不列該系列
    (錯的 G 編號與戰績比沒有更糟)。季後賽場次缺可靠開賽時間 → 拋例外,呼叫端整段放棄。
    G 編號依開賽時間排,延賽/取消的不算場次。戰績只在**每一場完賽比分都在**、而且沒有看不到的
    帶入勝場時才列;否則標明原因、不列數字,也不據此判定系列結束(少算的數字仍是錯的數字)。
    """
    by_type: dict[str, dict[str, dict]] = {}
    for g in games or []:
        if (g.get("postseason") and g.get("game_type") in CPBL_SERIES
                and not re.search(r"postpone|cancel", g.get("status", ""))):
            if not g.get("start"):   # 默默丟掉會讓 G 編號錯位、少算勝場
                raise ValueError(f"中職季後賽場次缺開賽時間:{g.get('gid')}")
            bucket = by_type.setdefault(g["game_type"], {})
            bucket[g["gid"]] = _better(bucket.get(g["gid"]), g)
    # 台灣大賽已排入的隊伍 = 挑戰賽晉級者(挑戰賽唯一可靠的「結束」訊號)
    advanced = {g[side] for g in by_type.get("Taiwan Series", {}).values() for side in ("away", "home")}
    found = [s for s in (_cpbl_series(game_type, list(by_type[game_type].values()), now, advanced)
                         for game_type in by_type
                         if since is None or min(g["start"] for g in by_type[game_type].values()).date()
                         >= since + dt.timedelta(days=_MIN_LEAD)) if s]
    found.sort(key=lambda s: (not s["results"], (s["next"] or {}).get("start") or "9999"))
    return {"series": found} if found else {}


def _quality(g: dict) -> int:
    """完賽且兩隊比分都在 = 2;完賽但比分不全 = 1;其他 = 0。"""
    if g["status"] != "status.type.final":
        return 0
    try:
        int(float(g["away_score"])), int(float(g["home_score"]))
    except (TypeError, ValueError):
        return 1
    return 2


def _better(old: dict | None, new: dict) -> dict:
    """Yahoo 的日期桶會重疊,同一場出現兩次:保留比分完整的那一版(與 fetch_cpbl_scores 同一個
    原則);兩版都完整卻比分不同 = 資料矛盾,拋例外讓呼叫端整段放棄。"""
    if old is None:
        return new
    if _quality(old) == _quality(new) == 2 and (old["away_score"], old["home_score"]) != (
            new["away_score"], new["home_score"]):
        raise ValueError(f"中職季後賽同一場比分矛盾:{new['gid']}")
    return new if _quality(new) >= _quality(old) else old


def _cpbl_series(game_type: str, games: list[dict], now: dt.datetime, advanced: set) -> dict | None:
    everything = sorted(games, key=lambda g: g["start"])
    finals = [g for g in everything if g["status"] == "status.type.final"]
    # 開賽時間已過 6 小時還沒完賽的:系列已分勝負時是「如有必要」的剩餘場次(下面拿掉);
    # 系列**還沒**分勝負時,那是狀態不明的歷史場次 —— 刪掉再重編號會把 G3 標成 G2(第九輪審查)
    pending = [g for g in everything if g["status"] != "status.type.final"
               and g["start"] >= now - dt.timedelta(hours=6)]
    stale = [g for g in everything if g["status"] != "status.type.final"
             and g["start"] < now - dt.timedelta(hours=6)]
    if not finals and not pending:
        return None
    label, best_of = CPBL_SERIES[game_type]
    wins: dict[str, int] = {everything[0]["away"]: 0, everything[0]["home"]: 0}
    scored = []
    for g in finals:
        try:
            a, h = int(float(g["away_score"])), int(float(g["home_score"]))
        except (TypeError, ValueError):
            scored.append((g, None, None))
            continue
        if a != h and not g.get("provisional"):   # 暫列比分不是確定的勝場
            winner = g["away"] if a > h else g["home"]
            wins[winner] = wins.get(winner, 0) + 1
        scored.append((g, a, h))
    note = ("第一名帶一勝優勢,系列戰績以官方為準" if game_type == "Playoff Series" else
            "部分場次比分缺漏,系列戰績暫不列" if any(a is None for _, a, _ in scored) else
            "部分場次結束方式待確認,系列戰績暫不列" if any(g.get("provisional") for g in finals) else "")
    if game_type == "Playoff Series":
        decided = bool(advanced & set(wins))
    else:   # 一方已拿到 4 勝。有比分才計的勝場是**下限**(缺比分只會少算),所以達標就確定結束
        decided = max(wins.values(), default=0) >= best_of // 2 + 1
    if stale and not decided:
        raise ValueError(f"中職季後賽有過期但狀態不明的場次:{stale[0]['gid']}")
    if decided:
        pending = []   # 已分勝負:剩下的都是「如有必要」場次,不編號、不列、不當下一場
    if not pending and not (finals and now - finals[-1]["start"] <= WINDOW):
        return None
    numbers = {g["gid"]: i for i, g in enumerate(sorted(finals + pending, key=lambda g: g["start"]), 1)}
    results = [{"game": numbers[g["gid"]], "away": g["away"], "home": g["home"], "away_score": a,
                "home_score": h, "date": g["start"].strftime("%m/%d"), "note": g.get("provisional") or ""}
               for g, a, h in scored if a is not None and now - WINDOW <= g["start"] <= now]
    if decided and not results:
        return None   # 已分勝負、最後一場也不在 30 小時內:不再佔版面
    nxt = next((g for g in pending if g["start"] >= now), None)
    return {"label": label, "best_of": best_of, "numbers": numbers, "decided": decided, "note": note,
            "teams": [] if note else sorted(wins.items(), key=lambda kv: -kv[1]), "results": results,
            "next": ({"game": numbers[nxt["gid"]], "gid": nxt["gid"], "away": nxt["away"], "home": nxt["home"],
                      "when": nxt["start"].strftime("%m/%d %H:%M"), "start": nxt["start"].isoformat()}
                     if nxt else None)}


_ESPN_MLB = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
_YAHOO = "https://api-secure.sports.yahoo.com/v1/editorial/s/scoreboard"
_YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 Chrome/124.0 Safari/537.36", "Accept": "application/json"}


def fetch_posts(get, now: dt.datetime, log, degrade) -> dict:
    """MLB / 中職季後賽,各自失敗互不牽連;回 {"mlb_post": …, "cpbl_post": …} 中有資料的部分。

    失敗時版面退回例行賽樣式,但**降級要留下痕跡**(AGENTS.md 不變式 3):`log` 收到 `::warning::`,
    `degrade` 收到降級標籤(生產接 `_DEGRADED_STEPS.append`,進 run manifest)。`degrade` 刻意沒有
    預設值 —— 給了預設 no-op,忘了接線的呼叫端就會讓這道守衛靜默失效。

    中職要**每一天都抓到**才算系列戰績:漏一天就少算勝場,錯的戰績比沒有戰績更糟,
    所以任何一天失敗整段放棄(版面退回例行賽樣式)。只在 9-11 月查(季後賽只在秋季)。
    """
    from nba_scoreboard import week_events
    out: dict = {}
    try:
        post = mlb_postseason(week_events(get, now, 4, _ESPN_MLB, back=2), now)   # 往後 4 天:休息日也看得到下一場
        if post:
            out["mlb_post"] = post
    except Exception as exc:
        log(f"::warning::[sports] MLB 季後賽抓取失敗(版面退回例行賽樣式): {type(exc).__name__}: {exc}")
        degrade("sports:mlb_postseason_failed")
    if now.month in (9, 10, 11):
        try:
            games = []
            for offset in range(-LOOKBACK_DAYS, 8):
                day = (now + dt.timedelta(days=offset)).strftime("%Y-%m-%d")
                response = get(_YAHOO, params={"leagues": "cpbl", "date": day},
                               headers=_YAHOO_HEADERS, timeout=15)
                response.raise_for_status()
                games += yahoo_cpbl_games((response.json().get("service") or {}).get("scoreboard"))
            post = cpbl_postseason(games, now, (now - dt.timedelta(days=LOOKBACK_DAYS)).date())
            if post:
                out["cpbl_post"] = post
        except Exception as exc:
            log(f"::warning::[sports] 中職季後賽抓取失敗(整段放棄,不顯示不完整的系列戰績): {type(exc).__name__}")
            degrade("sports:cpbl_postseason_failed")
    return out


def tennis_state(event: dict, now: dt.datetime) -> str:
    """賽事進行中/即將/已結束,依 `date ≤ now < endDate` 判斷。

    ESPN 的 `status.state` 對「當天比完」的進行中賽事也給 post(2026-10-14 實測上海大師賽
    endDate 10/19 卻是 post),所以不能用它判斷;缺 endDate 才退回它。
    """
    start, end = _when(event.get("date")), _when(event.get("endDate"))
    if start and end:
        return "in" if start <= now < end else ("pre" if now < start else "post")
    return str((((event.get("status") or {}).get("type")) or {}).get("state") or "")


def fresh(timestamp: Any, now: dt.datetime, hours: int = 72) -> bool:
    """網球賽果/冠軍的新鮮度上限(使用者 2026-10-14:一週前的冠軍不該再列);時間不明視為不新鮮。

    只擋「太舊」:ESPN 的場次時間有時是日層級的時間戳,完賽場次的 `date` 可能晚於報告時間,
    再加未來上限會誤刪真的賽果。
    """
    stamp = _when(timestamp)
    return stamp is not None and stamp >= now - dt.timedelta(hours=hours)


def priority(sports: dict) -> dict[str, int]:
    """各項目的賽季重要性:0 季後賽/世足進行中、1 例行賽/網球、2 季前賽或休賽季。數字越小越前面。"""
    nba = sports.get("nba") or []
    nba_post = any("季後賽" in str(g.get("note", "")) for g in nba)
    nba_pre = bool(nba) and all("季前賽" in str(g.get("note", "")) for g in nba)
    return {"世足": 0, "MLB": 0 if sports.get("mlb_post") else 1,
            "中職": 0 if sports.get("cpbl_post") else 1,
            "NBA": 0 if nba_post else (2 if nba_pre or sports.get("nba_offseason") else 1),
            "網球": 1}


def focus_items(sports: dict, now: dt.datetime | None = None) -> list[str]:
    """今日焦點:未來 24 小時內的季後賽(台北時間;跨日標「明日」),台灣大賽首戰 3 天內先預告。

    `now` 預設取 `sports["as_of"]`(抓資料的時間);沒有可靠時間就不列焦點。
    純文字(英文隊名縮寫未翻譯),呼叫端負責翻譯與跳脫。
    """
    now = now or _when(sports.get("as_of"))
    if now is None:
        return []
    def clock(start: dt.datetime) -> str:
        return ("" if start.date() == now.astimezone(TPE).date() else "明日 ") + start.strftime("%H:%M")

    items = []
    for g in (sports.get("mlb_post") or {}).get("upcoming") or []:
        start = _when(g.get("start"))
        if start and start - now <= dt.timedelta(hours=24):
            items.append((start, f"{clock(start)} {g['round']} G{g['game']} {g['away']} @ {g['home']}"))
    for cpbl in (sports.get("cpbl_post") or {}).get("series") or []:
        nxt = cpbl.get("next")
        start = _when((nxt or {}).get("start"))
        if not (nxt and start):
            continue
        if start - now <= dt.timedelta(hours=24):
            items.append((start, f"{clock(start)} {cpbl['label']} G{nxt['game']} {nxt['away']} vs {nxt['home']}"))
        elif nxt["game"] == 1 and start - now <= dt.timedelta(days=3):
            items.append((start, f"{cpbl['label']} {nxt['when'][:5]} 開打({nxt['away']} vs {nxt['home']})"))
    return [text for _, text in sorted(items, key=lambda item: item[0])]


def award_line(rows: list[dict], line) -> str:
    """獎項盤:第一名 ≥95% 只寫「大熱門」(使用者 2026-10-14:99% 幾乎沒有資訊量)。"""
    top = (rows or [{}])[0]
    if isinstance(top.get("prob"), (int, float)) and top["prob"] >= 95:
        return f"{top.get('name', '')}(大熱門)"
    return line(rows)


# ---------------------------------------------------------------------------
# 渲染(與 render_utils 同款式;esc=html 跳脫、zh=隊名縮寫翻譯,由呼叫端提供)
# ---------------------------------------------------------------------------
_ROW = "<div style='font-size:13px;color:#334155;line-height:1.85;'>"
_DIM = "<span style='color:#94a3b8;'>"


def _score(away, a, home, h, winner, esc, zh) -> str:
    def side(team, score):
        cell = f"{esc(zh(team))} {esc(str(score))}"
        return f"<b style='color:#b91c1c;'>{cell}</b>" if team == winner else cell
    return f"{side(away, a)}　:　{side(home, h)}"


def render_mlb_post(post: dict, esc, zh, poly_html: str = "", odds: dict | None = None) -> str:
    """MLB 季後賽:系列戰況 → 昨日比分 → 下一場(使用者 2026-10-14 要的就是這三件事)。

    `odds`:{(客隊, 主隊, 台北開賽時間): 單場賭盤行},來自原本賽程那一路(_attach_mlb_poly_odds),
    季後賽版面取代賽程區塊時不可把它弄丟。
    """
    lines = [f"{_ROW}<b>{esc(s['round'])}</b>{_DIM}({s['best_of']} 戰 {s['best_of'] // 2 + 1} 勝)</span>　"
             + "　".join(f"{esc(zh(t))} <b>{w}</b>" for t, w in s["teams"])
             + ("　<span style='color:#b45309;'>系列結束</span>" if s.get("completed") else "") + "</div>"
             for s in post.get("series") or []]
    lines += [f"{_ROW}{_DIM}{esc(r.get('day') or '')} {esc(r['when'][:5])}</span>　{esc(r['round'])} G{r['game']}　"
              + _score(r["away"], r["away_score"], r["home"], r["home_score"], r.get("winner"), esc, zh) + "</div>"
              for r in post.get("results") or []]
    lines += [f"{_ROW}{_DIM}{esc(g['when'])}</span>　{esc(g['round'])} G{g['game']}　"
              f"{esc(zh(g['away']))} @ {esc(zh(g['home']))}"
              + (f"<div style='font-size:11px;color:#b45309;margin-left:2px;'>{esc(zh(line))}</div>"
                 if (line := (odds or {}).get((g["away"], g["home"], g["when"]))) else "") + "</div>"
              for g in post.get("upcoming") or []]
    return ("<div style='margin:8px 0;'><b style='color:#0f172a;'>MLB 季後賽（台北時間）</b>"
            + "".join(lines) + poly_html + "</div>")


def render_cpbl_post(post: dict, fixtures: list, standings_line: str, esc) -> str:
    """中職季後賽:系列戰績 → 昨日比分 → 賽程(每場標 G 編號)→ 例行賽戰績縮成一行。"""
    numbers = post.get("numbers") or {}
    nxt = post.get("next")
    if nxt and not any(f.get("gid") == nxt.get("gid") for f in fixtures or []):
        # 賽程清單那一路抓漏了這天,季後賽資料仍知道下一場:補列,不讓它消失
        fixtures = [*(fixtures or []), {"gid": nxt.get("gid"), "date": nxt["when"][:5], "start": nxt["when"][6:],
                                       "away": nxt["away"], "home": nxt["home"]}]
    record = ("　".join(f"{esc(t)} <b>{w}</b>" for t, w in post.get("teams") or [])
              or f"<span style='color:#64748b;'>{esc(post.get('note') or '')}</span>")
    lines = [f"{_ROW}系列戰績　{record}"
             + ("　<span style='color:#b45309;'>系列結束</span>" if post.get("decided") else "") + "</div>"]
    lines += [f"{_ROW}{_DIM}{esc(r['date'])}</span>　G{r['game']}　"
              + _score(r["away"], r["away_score"], r["home"], r["home_score"],
                       "" if r.get("note") else r["away"] if r["away_score"] > r["home_score"] else
                       (r["home"] if r["home_score"] > r["away_score"] else ""), esc, str)
              + (f"　<span style='color:#b45309;'>{esc(r['note'])}</span>" if r.get("note") else "") + "</div>"
              for r in post.get("results") or []]
    for f in fixtures or []:
        game = numbers.get(f.get("gid"))
        venue = f"<span style='color:#0f766e;'>@{esc(str(f['venue']))}</span>" if f.get("venue") else ""
        lines.append(f"{_ROW}{_DIM}{esc(str(f.get('date', '')))} {esc(str(f.get('start', '')))}</span>{venue}　"
                     + (f"<b>G{game}</b>　" if game else "")
                     + f"{esc(f['away'])} vs {esc(f['home'])}"
                     + (f"<div style='font-size:11px;color:#b45309;margin-left:2px;'>{esc(str(f['odds']))}</div>"
                        if f.get("odds") else "") + "</div>")
    if standings_line:
        lines.append(f"<div style='font-size:12px;color:#64748b;margin-top:2px;'>{standings_line}</div>")
    return (f"<div style='margin:8px 0;'><b style='color:#0f172a;'>中華職棒 {esc(post['label'])}"
            f"（{post['best_of']} 戰 {post['best_of'] // 2 + 1} 勝;台北時間）</b>" + "".join(lines) + "</div>")


def standings_line(tables: list[tuple[str, list]], esc) -> str:
    """季後賽期間例行賽戰績縮成一行:每張表只留第一名。"""
    parts = [f"{esc(label)} {esc(str(rows[0].get('team', '')))} {esc(str(rows[0].get('wdl', '')))}"
             for label, rows in tables if rows]
    return ("例行賽戰績:" + "・".join(parts)) if parts else ""
