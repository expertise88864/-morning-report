"""Financial-group editorial coverage only; never issuer tags or stock scores.

Names checked against the groups' official subsidiary pages on 2026-09-06.
The Taichung sports park is a Taiwan Life BOT project, not a subsidiary.
Queries were HTTP-tested with the production Google News two-day window.
"""
from __future__ import annotations
import re

GROUPS = ("finance:ctbc", "finance:cathay")
ALIASES = {
    GROUPS[0]: ("中信金", "中國信託", "中信銀", "台灣人壽", "臺灣人壽", "台壽",
                "中信資融", "中信保全", "台灣彩券", "臺灣彩券", "中信產險",
                "中信清水", "中信榮盛", "日月行館", "君龍人壽",
                "台中超巨蛋", "臺中超巨蛋", "台中市運動產業園區", "臺中市運動產業園區"),
    GROUPS[1]: ("國泰金", "國泰人壽", "國泰世華", "國泰產險", "國泰世紀產物",
                "國泰綜合證券", "國泰證券", "國泰投信", "國泰投顧", "國泰電業",
                "陸家嘴國泰人壽", "越南國泰人壽"),
}
QUERIES = {
    "金融-銀行壽險": "中信金 OR 中國信託 OR 台灣人壽 OR 臺灣人壽 OR 中信銀",
    "金融-保險資管": ("國泰金 OR 國泰人壽 OR 國泰世華 OR 國泰投信 OR 國泰產險 "
                  "OR 國泰證券 OR 國泰投顧 OR 國泰電業 OR 國泰綜合證券 OR 國泰世紀產物"),
    "金融-轉投資": ("中國信託產險 OR 中信產險 OR 中信資融 OR 中信保全 OR 台灣彩券 營運 "
                 "OR 中信清水 OR 中信榮盛 OR 日月行館 OR 君龍人壽"),
    "金融-建設投資": ("台中 超巨蛋 OR 臺中 超巨蛋 OR 台中市 運動產業園區 "
                  "OR 臺中市 運動產業園區"),
}

WRITING = """
## 金融產業取材
- 金融條目優先分析中信金控與國泰金控及其銀行、壽險、產險、證券、投信、
  轉投資事業的實質新聞；兩個集團都有重大新事實時，各自涵蓋，不讓一方洗版。
  台灣人壽及其台中超巨蛋／運動產業園區 BOT 案亦屬此範圍；投資案不是子公司。
  區分公告、取得資格、簽約、動工及營運，不把規劃金額當作已實現收益。
- 仍以當期來源證據為準；沒有新事實就略過，不補舊聞、不虛構進度，不以
  開獎、刷卡優惠或例行行情湊數；其他金融公司的重大事件仍要保留。
- 正文自然呈現公司、事件、來源與影響；編輯設定及個人化需求一律不進正文，
  不透露新聞入選理由、關注範圍或缺席情況。
"""


def routine_promotion(title: str) -> bool:
    """Editorial exclusion only; substantive financial/regulatory news stays."""
    routine = re.search(
        r"開獎|獎號|刮刮樂|威力彩|大樂透|今彩539|優惠碼|"
        r"(?:信用卡|卡友|持卡|刷卡|新戶|開戶).*(?:享|回饋|優惠|折|送|抽|點數)|"
        r"卡.*(?:回饋|優惠|折扣|贈|點數)|(?:回饋|優惠|折扣|贈品|點數).*卡|"
        r"cashback|coupon|card rewards", title, re.I)
    material = re.search(r"裁罰|違規|違法|詐騙|外洩|財報|獲利|淨利|營收|增資|"
                         r"法說|併購|停業|取消|停發|重大訊息|"
                         r"(?:調漲|上調|調高|提高|新增|加收|開徵).{0,8}手續費|"
                         r"手續費.{0,8}(?:調漲|上調|調高|提高|新增|加收|開徵)", title)
    return bool(routine and not material)


def _direct_groups(item: dict) -> tuple[str, ...]:
    """Headline mentions identify editorial topics, NOT ownership/causation.

    Never infer from a query label, stock number, affected_assets, or broad
    names such as 國泰 / 中信 (Cathay Pacific and mainland CITIC are unrelated).
    Undated material and routine lottery/discount headlines get no reserve.
    """
    if item.get("date_missing") or not (item.get("published") or item.get("published_dt")):
        return ()
    title = str(item.get("title") or "")
    if routine_promotion(title):
        return ()
    return tuple(g for g in GROUPS if any(name in title for name in ALIASES[g]))


def groups(item: dict) -> tuple[str, ...]:
    """Derive topics from dated text, including originals retained by dedup."""
    old = item.get("finance_headlines")
    rows = [item] + (old if isinstance(old, list) else [])
    found = {g for row in rows if isinstance(row, dict) for g in _direct_groups(row)}
    return tuple(g for g in GROUPS if g in found)


def evidence(item: dict, clean=str) -> list[dict]:
    """At most one dated original per group; no free-form group flags.

    Retain its OWN date and publisher, not the winning copy's grade or date.
    This is alternate headline evidence within one merged article, never an
    issuer tag, new article ID or count of independent confirmation. Every
    string is sanitized by normalize_news before entering the primary packet.
    """
    old = item.get("finance_headlines")
    rows = [item] + (old if isinstance(old, list) else [])
    out, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        topics = set(_direct_groups(row))
        if not topics - seen:
            continue
        out.append({"title": clean(str(row.get("title") or ""))[:300],
                    "published": clean(str(row.get("published_dt") or row.get("published") or ""))[:80],
                    "source": clean(str(row.get("source_name") or row.get("source") or ""))[:120]})
        seen.update(topics)
    return out


def merge_evidence(a: dict, b: dict) -> list[dict]:
    return evidence({"finance_headlines": evidence(a) + evidence(b)})


def retain_evidence(target: dict, *sources: dict, clean=str) -> None:
    rows = evidence({"finance_headlines": [r for n in sources for r in evidence(n)]}, clean)
    if rows:
        target["finance_headlines"] = rows


def balanced(items: list[dict], per_group: int = 2) -> list[dict]:
    """Stable round-robin across groups; one article occupies one slot."""
    chosen = []
    seen: set[int] = set()
    for _ in range(per_group):
        for group in GROUPS:
            if sum(group in groups(items[i]) for i in chosen) > _:
                continue
            candidate = next((i for i, n in enumerate(items)
                              if i not in seen and group in groups(n)), None)
            if candidate is not None:
                chosen.append(candidate)
                seen.add(candidate)
    return [items[i] for i in chosen]


def legacy_block(news: list[dict], clean) -> str:
    """Small dated evidence block, inside the caller's existing source fence.

    Works after dedup replaces a sector feed's source with a better publisher.
    No selection instructions or reader preferences are placed in source data.
    """
    picked = balanced([n for n in news if isinstance(n, dict)])
    lines = [f"- [{row['published'][:19]}] {row['title']} [{row['source']}]"
             for n in picked for row in evidence(n, clean)]
    return "\n\n【金融產業新聞】\n" + "\n".join(lines) if lines else ""


def order_analyses(analyses: list, packet=None) -> list:
    """Reorder financial slots only; preserve every card and other sectors.

    Both available groups' first cards precede repeat cards. The model's
    analysis text is untouched and no badge, rationale, or filler is emitted.
    """
    from analysis_render_depth import news_subject

    by_id = {str(n.get("source_item_id") or "").strip(): n
             for n in ((packet or {}).get("news") or []) if isinstance(n, dict)}
    out = list(analyses or [])
    slots, raw = [], []
    for i, card in enumerate(out):
        if not isinstance(card, dict):
            continue
        item = by_id.get(str(card.get("source_item_id") or "").strip(), {})
        source = str(item.get("source") or "")
        industry = str(news_subject(card, packet).get("industry") or "")
        if (groups(item) or "sector:金融" in (item.get("coverage_buckets") or [])
                or industry == "17" or any(k in industry for k in ("金融", "銀行", "保險", "證券"))
                or source == "類股-金融" or source.startswith("類股-金融-")):
            slots.append(i)
            raw.append(item)
    first = balanced(raw, 1)
    indices = [i for n in first for i, item in enumerate(raw) if item is n]
    indices += [i for i, item in enumerate(raw) if i not in indices and groups(item)]
    indices += [i for i in range(len(raw)) if i not in indices]
    cards = [out[slots[i]] for i in indices]
    for slot, card in zip(slots, cards):
        out[slot] = card
    return out
