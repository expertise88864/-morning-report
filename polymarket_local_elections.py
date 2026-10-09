"""Display-only Taiwan local election prices; no model inputs or paid APIs."""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import re
import sys
from collections.abc import Callable

# Verified against Gamma /events and /public-search over HTTP on 2026-10-09.
# County magistrate and county-administered city mayor are different offices.
MARKETS = (
    ("台北市長", "Taipei Mayor", "taipei-mayor-election-winner-20260813125857100"),
    ("新北市長", "New Taipei Mayor", "new-taipei-mayor-election-winner-20260813125857200"),
    ("台中市長", "Taichung Mayor", "taichung-mayor-election-winner-20260813125857400"),
    ("彰化縣長", "Changhua County Magistrate", "changhua-county-magistrate-election-winner-20260813135716600"),
    ("彰化市長", "Changhua Mayor", ""),
    ("雲林縣長", "Yunlin County Magistrate", "yunlin-county-magistrate-election-winner-20260813135716800"),
    ("斗六市長", "Douliu Mayor", ""),
    ("高雄市長", "Kaohsiung Mayor", "kaohsiung-mayor-election-winner-20260813125857600"),
)
NAMES = {
    "Chiang Wan-an": "蔣萬安", "Puma Shen": "沈伯洋",
    "Lee Shu-chuan": "李四川", "Su Chiao-hui": "蘇巧慧",
    "Johnny Chiang": "江啟臣", "Ho Hsin-chun": "何欣純",
    "Chen Su-yueh": "陳素月", "Wei Ping-cheng": "魏平政",
    "Chang Chia-chun": "張嘉郡", "Liu Chien-kuo": "劉建國",
    "Ko Chih-en": "柯志恩", "Lai Jui-lung": "賴瑞隆",
}
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_PLACEHOLDER = re.compile(r"(?:candidate|party|player|team)\s+[a-z0-9]+\Z", re.I)
_TPE = dt.timezone(dt.timedelta(hours=8))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _identity(event: dict, office: str, now: dt.datetime) -> bool:
    title = str(event.get("title") or "").casefold()
    titles = {f"{office} Election Winner".casefold()}
    if office in {"Changhua Mayor", "Douliu Mayor"}:
        titles.add(f"{office.replace(' Mayor', ' City Mayor')} Election Winner".casefold())
    if (title not in titles or event.get("closed") is not False
            or event.get("active") is not True or event.get("archived") is True):
        return False
    if not _SLUG.fullmatch(str(event.get("slug") or "")):
        return False
    try:
        end = dt.datetime.fromisoformat(str(event.get("endDate") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    return (end.tzinfo is not None and end.year == 2026 and end > now
            and "2026" in str(event.get("description") or ""))


def _prices(event: dict, now: dt.datetime | None = None) -> list[tuple[float, str]]:
    markets = event.get("markets")
    if not isinstance(markets, list):
        raise ValueError("missing markets")
    rows: list[tuple[float, str]] = []
    for market in markets:
        if (not isinstance(market, dict) or market.get("closed") is not False
                or market.get("active") is not True or market.get("archived") is True):
            continue
        if now is not None and market.get("endDate"):
            try:
                end = dt.datetime.fromisoformat(str(market["endDate"]).replace("Z", "+00:00"))
                if end.tzinfo is None or end <= now:
                    continue
            except ValueError:
                continue
        name = str(market.get("groupItemTitle") or "").strip()
        if not name or name.casefold() == "other" or _PLACEHOLDER.fullmatch(name):
            continue
        try:
            outcomes, prices = market.get("outcomes"), market.get("outcomePrices")
            outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
            prices = json.loads(prices) if isinstance(prices, str) else prices
            if not isinstance(outcomes, list) or not isinstance(prices, list):
                continue
            labels = [str(x).casefold() for x in outcomes]
            if len(prices) != 2 or sorted(labels) != ["no", "yes"]:
                continue
            values = [_number(x) for x in prices]
            if any(x is None or not 0 <= x <= 1 for x in values):
                continue
            p = values[labels.index("yes")]
            assert p is not None
        except (TypeError, ValueError):
            continue
        volume, spread = _number(market.get("volume24hr")), _number(market.get("spread"))
        flags = []
        if volume is None or volume < 0:
            flags.append("量未知")
        elif volume < 10000:
            flags.append("量低")
        if spread is None or not 0 <= spread <= 1:
            flags.append("價差未知")
        elif spread > 0.05:
            flags.append("價差大")
        text = f"{NAMES.get(name, name)} {p * 100:.1f}%"
        rows.append((p, text + (f"（{'、'.join(flags)}）" if flags else "")))
    return sorted(rows, key=lambda row: row[0], reverse=True)


def fetch_rows(events: Callable, search: Callable, now: dt.datetime) -> list[dict]:
    """Use the caller's shared timeout/circuit breaker, at most eight requests.

    Unknown city markets are searched each day, never substituted with a county.
    Missing data is omitted at the user's request; failures leave Actions warnings.
    """
    rows = []
    for label, office, slug in MARKETS:
        if now.astimezone(_TPE).date() > dt.date(2026, 11, 28):
            return []
        try:
            data = events({"slug": slug}) if slug else search(f"{office} Election Winner")
            if not isinstance(data, list):
                raise ValueError("invalid event payload")
            matches = [e for e in data if isinstance(e, dict) and _identity(e, office, now)
                       and (not slug or e.get("slug") == slug)]
            if len(matches) > 1:
                raise ValueError("ambiguous election markets")
            if matches:
                event = matches[0]
                prices = _prices(event, now)
                if not prices:
                    raise ValueError("no valid named candidate prices")
                detail = "・".join(text for _, text in prices[:3])
                if len(prices) > 3:
                    detail += "（僅列報價前 3 名）"
                rows.append({"label": f"2026 {label}", "kind": "tw_local_election",
                             "as_of": now.astimezone(_TPE).strftime("%m/%d %H:%M 台北"),
                             "detail": detail,
                             "source_url": "https://polymarket.com/event/" + event["slug"]})
            elif slug:
                print(f"::warning::[poly-local] {label}: unavailable known market", file=sys.stderr)
        except Exception as exc:
            print(f"::warning::[poly-local] {label}: {type(exc).__name__}", file=sys.stderr)
    return rows


def _label(row: dict) -> str:
    label = html.escape(str(row.get("label", "")))
    url = str(row.get("source_url") or "")
    prefix = "https://polymarket.com/event/"
    if url.startswith(prefix) and _SLUG.fullmatch(url[len(prefix):]):
        return f'<a href="{html.escape(url, quote=True)}" style="color:#0f172a;">{label}</a>'
    return label


def render_pulse(rows: list[dict], divergence_note: str = "") -> str:
    """Existing pulse card, with source links and a local-election price legend."""
    if not rows:
        return ""
    lines = "".join(
        f"<tr><td style='padding:8px 14px;border-bottom:1px solid #e2e8f0;"
        f"font-size:13px;color:#0f172a;font-weight:700;'>{_label(r)}</td>"
        f"<td style='padding:8px 14px;border-bottom:1px solid #e2e8f0;text-align:right;"
        f"font-size:13px;color:#b45309;font-weight:700;'>{html.escape(str(r.get('detail', '')))}</td></tr>"
        for r in rows)
    div_html = (f"<div style='padding:8px 14px;font-size:12px;color:#b45309;"
                f"background:#fffbeb;border-top:1px solid #fde68a;font-weight:700;'>"
                f"{html.escape(divergence_note)}</div>") if divergence_note else ""
    local = next((r for r in rows if r.get("kind") == "tw_local_election"), None)
    if local:
        div_html += ("<div style='padding:8px 14px;font-size:12px;color:#64748b;'>"
                     f"選舉盤查詢：{html.escape(str(local.get('as_of', '')))}。"
                     "百分比為市場報價，非民調或保證勝率；獨立合約不強制加總 100%。</div>")
    return (
        '<h2 style="color:#0f172a;font-size:20px;margin:32px 0 12px;padding:8px 14px;'
        'background:#fefce8;border-left:5px solid #ca8a04;border-radius:4px;">'
        '預測市場觀點(Polymarket)</h2>'
        '<div style="border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;background:#ffffff;">'
        '<table style="width:100%;border-collapse:collapse;">' + lines + "</table>"
        + div_html + "</div>")
