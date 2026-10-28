"""新聞卡的編輯判準:主角要有卡片內容支撐、主題去重、產業輪替、影響標的節制(純函式,無網路)。

使用者 2026-10-27 看了當天晨報:
- 「〈台股盤前要聞〉美光罷工危機解除、四大公股投信整併案拍板」這種彙整稿,主角從標題取第一個
  點名的公司(美光),卡片寫的卻是公股投信整併 → 標題錯置,還因「美光是科技股」被放進科技段。
- 一週 98 張卡片有 29 張寫「對台積電(2330)而言」,AI 資本支出一條主線一天佔 4、5 張 →
  「都是這幾間科技公司在輪動」。

這裡的判準都只看**本報已有的資料**(來源標題、卡片自己的文字、packet 的 tw_universe / event_graph),
不呼叫模型、不改權威欄位;拿掉的只是排版(驗證照舊看完整的分析)。
"""
from __future__ import annotations

import re
from collections import Counter

#: 同一條主線(底層驅動)在八、九兩段合計最多幾張卡;其餘併成一句「同主題還有…」。
THEME_CAP = 2
#: 「其他類股」同一產業在第一輪最多幾張(先讓不同產業都上得了版,再補第二輪)。
SECTOR_FIRST_PASS = 2
#: 沒被這則新聞點名的核心標的,全天最多掛在幾張卡上(之後的卡不再寫「對 2330 而言」)。
CORE_REPEAT = 3
CORE_ASSETS = frozenset({"2330", "TSM", "0050", "00662", "TAIEX"})
#: 解讀至少這麼長才拿來判斷「卡片寫的是不是標題那家公司」(太短的無從判斷,沿用標題主角)。
_SUBSTANTIVE = 20
#: 模型自己承認沒有關係的影響句(「可能整併案與中信金無關」)—— 那不是分析,是湊數。
#: 只收「對**這個標的**沒有影響」的說法;句中別處的「無關」(「與財報無關的一次性損益」)不算。
#: 泛稱的否認只在**整句只有否認**時才不印;同一行還寫了別的影響(「一次性費用將降低本季獲利,
#: 對產能沒有直接影響」「對獲利不利但對產能沒有直接影響」)照印 —— 前綴只認「對**這個標的**」,
#: 修飾詞只認固定幾個,不讓寬鬆的萬用字元吞掉實質影響(外審 PR #29 三輪)。
_NO_EFFECT = (r"(?:可能|本報)?{prefix}(?:看不出(?:次級|直接|明顯|任何)?|沒有直接|無直接)"
              r"(?:的)?(?:影響|關係|關聯|傳導)[。.!！]?")
#: AI 資本支出主線的延伸說法(event_graph 的 ai_capex 只收「資本支出 / 資料中心」等字)。
_AI_CAPEX_EXTRA = re.compile(
    r"AI.{0,6}(?:基建|基礎建設|算力|融資|增資|支出承諾)|超大規模|hyperscaler"
    r"|(?:OpenAI|Anthropic|xAI|Meta|微軟|Alphabet|谷歌|亞馬遜).{0,12}(?:掛牌|上市|IPO|融資|增資|支出承諾|資本支出)", re.I)


def card_text(card: dict) -> str:
    """卡片**自己寫的**文字(解讀、因果鏈、影響標的);不含來源標題。"""
    parts = [str(card.get("why_it_matters") or "")]
    for step in card.get("mechanism_steps") or []:
        if isinstance(step, dict):
            parts += [str(step.get(k) or "") for k in ("from_what", "to_what", "channel")]
    for a in card.get("affected_assets") or []:
        if isinstance(a, dict):
            parts += [str(a.get(k) or "") for k in ("asset_id", "first_order_effect")]
    return " ".join(parts)


def referenced(card: dict, code: str, names) -> bool:
    """卡片自己的文字有沒有提到這家公司(代號或任一名稱)。

    主角要**同時**被來源標題點名、又被卡片內容寫到:彙整稿的標題會點名好幾家公司,
    只看標題就會把第一個點名的當主角,即使卡片寫的是同一篇裡的另一件事。
    """
    text = card_text(card)
    code = str(code or "")
    if len(str(card.get("why_it_matters") or "").strip()) < _SUBSTANTIVE:
        return True   # 解讀太短,看不出卡片在寫哪件事 → 不推翻標題的主角
    if code and re.search(r"(?<![A-Za-z0-9])" + re.escape(code) + r"(?![A-Za-z0-9])", text):
        return True
    import entity_alias
    # 卡片常用別的寫法(標題寫 Apple、卡片寫蘋果):已宣告的別名組一併承認(外審 PR #29)
    groups = entity_alias.expand([code, *names])
    aliases = {str(n) for n in names if n} | {a for g in groups for a in entity_alias.ALIAS_GROUPS[g]}
    return any(_mentions(text, a) for a in aliases if len(a) >= 2)


def _mentions(text: str, name: str) -> bool:
    """英數名稱要整字(`MU` 不命中 `MUST`),中文名稱子字串即可。"""
    if re.fullmatch(r"[A-Za-z0-9 .&-]+", name):
        return bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", text))
    return name in text


def theme_of(card: dict, packet: dict) -> str:
    """這張卡屬於哪一條底層主線(event_graph 的驅動代號);認不出來回空字串。"""
    import event_graph
    item = _item(card, packet)
    text = " ".join((str(item.get("title") or ""), str(card.get("why_it_matters") or "")))
    if _AI_CAPEX_EXTRA.search(text):
        return "ai_capex"
    return event_graph.driver_of(text)


def theme_label(theme: str) -> str:
    import event_graph
    return next((row[1] for row in event_graph.DRIVER_TABLE if row[0] == theme), theme)


def cap_themes(cards: list, packet: dict, keep: list | None = None) -> tuple[list, dict]:
    """同一主線最多 THEME_CAP 張(依傳入順序 = 重要性);`keep` 裡的卡一定留、但也佔名額。

    回 (留下的卡, {主線: [被收掉的卡…]})。總經主線(Fed、殖利率…)不在這裡收 ——
    它們走世界大事段,不佔類股名額。
    """
    keep = keep or []
    used: Counter = Counter(theme_of(c, packet) for c in keep)
    kept, overflow = [], {}
    for c in cards:
        theme = theme_of(c, packet)
        if c in keep or not theme or used[theme] < THEME_CAP:
            if c not in keep:
                used[theme] += 1
            kept.append(c)
        else:
            overflow.setdefault(theme, []).append(c)
    return kept, overflow


def backfill(chosen: list, spill: list, packet: dict, limit: int, taken: set) -> list:
    """段落不滿 `limit` 張時,把主線超額的卡依原順序補回(使用者 2026-10-28「有空位就補回」)。

    主線上限只決定先後:不同主線先上版;科技新聞大多屬於同一主線的日子,段落不再只剩四張。
    同一家公司已經有卡的不補(那一則改走「同公司還有」)。
    """
    out = list(chosen)
    for c in spill:
        key = company_key(c, packet)
        if len(out) >= limit:
            break
        if key not in taken:
            out.append(c)
            taken.add(key) if key else None
    return out


def company_key(card: dict, packet: dict) -> str:
    """卡片的單一公司主角(`中信金（2891）`);多家或沒有公司回空字串。"""
    from news_heading import companies
    label = companies(_item(card, packet), packet or {})
    return label if label and "、" not in label else ""


def first_per_company(cards: list, packet: dict, keep: list | None = None) -> list:
    """同一家公司只留第一張(依重要性);`keep` 裡的卡一定留(使用者 2026-10-28:中信金兩張)。"""
    seen, out = set(), []
    for c in cards:
        key = company_key(c, packet)
        if key and key in seen and c not in (keep or []):
            continue
        seen.add(key)
        out.append(c)
    return out


def company_extras(selected: list, limited: list, packet: dict) -> dict:
    """{留下那張的 source_item_id: [同公司被併掉的卡…]}。"""
    home = {}
    for c in selected:
        home.setdefault(company_key(c, packet), c.get("source_item_id"))
    out: dict = {}
    for c in limited:
        key = company_key(c, packet)
        if key and key in home:
            out.setdefault(home[key], []).append(c)
    return out


def company_line(cards: list, packet: dict) -> str:
    """`同公司還有：標題（來源）；…。` —— 接在那家公司那張卡後面。"""
    from analysis_render_depth import _headline_of
    import legacy_layout
    heads = [legacy_layout.with_source(h, legacy_layout.source_of(packet, c.get("source_item_id"))).rstrip("。")
             for c in cards for h in [_headline_of(c, packet)] if h][:3]
    return "同公司還有：" + "；".join(heads) + "。" if heads else ""


def sector_balanced(cards: list, packet: dict) -> list:
    """「其他類股」:每個產業第一輪最多 SECTOR_FIRST_PASS 張,讓不同產業先上版,其餘照原順序補在後面。"""
    from analysis_render_depth import sector_tag
    seen: Counter = Counter()
    first, later = [], []
    for c in cards:
        key = sector_tag(c, packet) or "其他"
        (first if seen[key] < SECTOR_FIRST_PASS else later).append(c)
        seen[key] += 1
    return first + later


def tech_by_assets(card: dict, packet: dict):
    """主角判不出來時,用卡片影響的**非核心**台股標的的產業決定科技與否;沒有就回 None。

    標題裡的「AI」不代表這是科技新聞(DHL:AI 產品運輸推高運價 → 影響的是長榮航)。
    """
    from industry_class import is_tech_industry
    index = {str(r.get("code")): r for r in (packet or {}).get("tw_universe") or [] if isinstance(r, dict)}
    votes = [is_tech_industry(index[aid].get("industry"))
             for aid in (str((a or {}).get("asset_id") or "") for a in card.get("affected_assets") or [])
             if aid in index and aid not in CORE_ASSETS]
    if not votes or votes.count(True) == votes.count(False):
        return None
    return votes.count(True) > votes.count(False)


def keep_asset(card: dict, asset: dict, packet: dict, budget: Counter | None) -> bool:
    """這一行「對X而言」要不要印。

    - 模型自己寫「與X無關 / 看不出」的,不印(那是湊數,不是分析)。
    - 核心標的(2330、TSM、0050、00662、加權指數)若來源標題沒點名、也不是卡片主角,
      全天最多印 CORE_REPEAT 次 —— 不然每則新聞最後都接一句「對台積電而言」。
    `budget` 由渲染端跨卡片共用;傳 None 表示不限(單卡渲染、測試)。
    """
    effect = str(asset.get("first_order_effect") or "")
    aid = str(asset.get("asset_id") or "")
    row = next((r for r in (packet or {}).get("tw_universe") or []
                if isinstance(r, dict) and str(r.get("code")) == aid), {})
    import company_profiles
    import entity_alias
    # 外國標的不在 tw_universe:顯示名(MU→美光)與已宣告別名也算「這個標的」(外審 PR #29 四輪)
    names = {n for n in (aid, str(row.get("name") or ""), company_profiles.display_name(aid)) if n}
    names |= {a for g in entity_alias.expand(names) for a in entity_alias.ALIAS_GROUPS[g]}
    prefix = "(?:對(?:" + "|".join(map(re.escape, sorted(names, key=len, reverse=True))) + "))?" if names else ""
    denied = re.fullmatch(_NO_EFFECT.format(prefix=prefix), effect.strip())
    if denied or any(f"與{n}無關" in effect for n in names):
        return False
    if budget is None or aid not in CORE_ASSETS:
        return True
    title = str(_item(card, packet).get("title") or "")
    named = {"2330": ("台積電", "台積", "TSMC"), "TSM": ("台積電", "TSMC", "TSM"),
             "0050": ("0050", "元大台灣50"), "00662": ("00662",), "TAIEX": ("台股", "加權")}
    if any(n in title for n in named.get(aid, ())):
        return True
    budget[aid] += 1
    return budget[aid] <= CORE_REPEAT


def overflow(selected: list, limited: list, packet: dict) -> dict:
    """被省略的卡裡,主線已滿額(THEME_CAP)的那些,依主線分組。"""
    full = Counter(t for t in (theme_of(c, packet) for c in selected) if t)
    out: dict = {}
    for c in limited:
        theme = theme_of(c, packet)
        if theme and full[theme] >= THEME_CAP:
            out.setdefault(theme, []).append(c)
    return out


def overflow_line(theme: str, cards: list, packet: dict) -> str:
    """被收掉的同主線卡片併成一句,讀者仍知道還有哪些事(不是消失)。"""
    from analysis_render_depth import _headline_of
    heads = [h for h in (_headline_of(c, packet) for c in cards) if h][:4]
    return f"同主題（{theme_label(theme)}）還有：" + "；".join(heads) + "。" if heads else ""


def _item(card: dict, packet: dict) -> dict:
    return next((n for n in (packet or {}).get("news") or []
                 if isinstance(n, dict) and n.get("source_item_id") == card.get("source_item_id")), {})
