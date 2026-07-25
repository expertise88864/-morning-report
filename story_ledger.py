# -*- coding: utf-8 -*-
"""Story Ledger:把晨報從「今日快照」變成「連續劇」。

**要解的兩個問題**(使用者原話:「像是在講一個故事的發生,而不是只單純分析今日新聞」)

1. **敘事連續性**:今天的新聞若接不上昨天的脈絡,讀者每天都在讀無關的片段。
2. **每天寫一樣的東西**:同一條線索天天以類似措辭重寫,佔版面卻沒有新資訊。

兩者其實是同一件事的兩面:系統沒有「這條線索走到哪了」的記憶。

**做法**

每條線索是一個 story,有狀態機:
    醞釀(brewing) → 發展(developing) → 高潮(peak) → 收斂(resolving) → 沉寂(dormant)

每天把新聞分成三類:
    續報(follow) — 接上某條既有 story;必須寫「昨天說 X → 今天 Y」
    新開(new)    — 開一條新 story
    雜訊(noise)  — 不寫

**這個設計的副作用正是想要的**:沒有 delta 的 story 會自動退到沉寂、不再佔版面,
於是信件長度變成**結果**而不是目標——不需要另外訂「省略哪些區塊」的規則。

**刻意的設計取捨**

- 分類由 LLM 做(語意判斷),但**狀態機轉移由 Python 決定**——比照 PR-2 的
  「Python 計分權威、LLM 只能抄錄」原則。LLM 可以說「這則接上 story-7」,
  不能自己宣告 story-7 進入高潮。
- story 的比對用 **entity + 事件類型 + 標題指紋**,不用 embedding:
  ARCHITECTURE_REVIEW 已否決「全文逐日存向量」的成本;而且 story 數量是數十條
  等級,啟發式足夠,不值得為它引入向量儲存。
- 沉寂的 story **不刪除**,只是不再進 prompt——同一條線索三個月後復燃時
  (例如併購案重啟),仍要接得回去。
"""
from __future__ import annotations

import hashlib
import re

# 狀態機:值為「連續幾天沒有 delta 就往下掉一級」的容忍天數
STATES = ("brewing", "developing", "peak", "resolving", "dormant")
STATE_ZH = {
    "brewing": "醞釀",
    "developing": "發展",
    "peak": "高潮",
    "resolving": "收斂",
    "dormant": "沉寂",
}

# 每個狀態能佔的版面權重(供呼叫端排序;不是硬性配額)
STATE_WEIGHT = {"peak": 3.0, "developing": 2.0, "resolving": 1.2,
                "brewing": 1.0, "dormant": 0.0}

# 閒置降級的**顯式**對照表。刻意不用 STATES 的相鄰元素:那個 tuple 是「熱度
# 由低到高再收斂」的順序,developing 的下一個是 peak,拿來當降級會把冷掉的線索
# 升級成高潮(r1 Codex F1 實際發生過)。
#
# r2(Codex):這張表是**每日**套用在持久化的帳本上,不是一次算到底。所以
# resolving 不能再往 dormant 掉——否則第 2 天 developing→resolving、第 3 天
# resolving→dormant,線索閒置三天就消失,而設計是七天(DORMANT_AFTER_DAYS)。
# 何時沉寂交給 _advance 裡的 days_idle >= DORMANT_AFTER_DAYS 那條唯一決定。
_IDLE_DEMOTION = {
    "brewing": "dormant",        # 從未成形的線索,沒動就沉寂(刻意較嚴)
    "developing": "resolving",
    "peak": "resolving",
    "resolving": "resolving",    # 停在收斂,等 DORMANT_AFTER_DAYS 才沉寂
    "dormant": "dormant",
}

HIGH_SURPRISE_PEAK = 0.75     # 超過此 surprise 直接視為高潮(新開或續報皆適用)
STALE_DAYS_TO_DEMOTE = 2      # 連續幾天沒有新進展就降一級
DORMANT_AFTER_DAYS = 7        # 超過幾天完全沒動就直接沉寂
MAX_ACTIVE_STORIES = 12       # 進 prompt 的活躍 story 上限
KEEP_DAYS = 120               # 帳本保留天數(沉寂的也留著,供日後復燃接回)

_PUNCT_RE = re.compile(r"[\s，。、；：！？「」『』()（）\[\]【】<>《》,.;:!?\"'`~\-—–]+")


def _norm(text: str) -> str:
    return _PUNCT_RE.sub("", str(text or "")).lower()


def story_key(entity: str, event_type: str, title: str = "",
              published: str = "") -> str:
    """story 身分:實體 + 事件類型(+ episodic 型別的期別 bucket),皆缺才退標題指紋。

    刻意**不**把標題裡的金額或日期放進 key——同一條線索的後續報導數字會變,
    放進去等於每天都開新 story,連續性就沒了。

    r1(Codex F3):但**財報/財測/營收是「按集數發生」的事件**——台積電 Q1 與 Q2
    財報是兩件事,共用一把 key 會讓 Q2 被當成 Q1 的續報、還餵給 LLM 錯誤的前情。
    news_events 早就為此建了期別 bucket(財報/財測=季、營收=月),這裡直接重用
    同一套規則,不另造一份會走樣的。

    **只對 episodic 型別分桶**:orders/litigation/geopolitical 等在 news_events
    是掛「月」bucket,但那是為了 event study 的樣本獨立性;story ledger 要的是
    跨週敘事連續性,把併購案這種長線在月界切斷會直接破壞本模組的目的。
    """
    ent, et = _norm(entity), _norm(event_type)
    if ent and et:
        bucket = _episodic_bucket(event_type, published)
        return f"e:{ent}|t:{et}" + (f"|p:{bucket}" if bucket else "")
    if ent:
        return f"e:{ent}"
    digest = hashlib.sha256(_norm(title).encode("utf-8")).hexdigest()[:16]
    return f"h:{digest}"


def story_key_for_event(ev: dict) -> str:
    """事件 → story 身分。**直接沿用 news_events 的事件身分規則**。

    r2(Codex F2):我原本只對財報/財測/營收分桶,其餘同 entity+type 共用一把 key。
    但 news_events 早就記載過這個碰撞的實害——`(entity, "general")` 曾讓單一金控
    的 16 則不同公告互吞。我等於把同一個已知缺陷重新引入,而且同日還會因 touched
    直接忽略第二件、跨日則餵給 LLM 錯誤的前情。

    改為整套沿用 `_event_timeline_key`(那是經過三輪審查收斂的既有身分規則:
    general 帶標題 digest、財報/財測掛季、營收掛月、其餘掛月)。

    **已知代價**(明確記錄,不假裝沒有):非 episodic 型別掛月 bucket,表示跨月的
    同一條長線(如併購案 7 月洽談 → 8 月進展)會被切成兩條 story,連續性在月界斷開。
    先前我為了避開這點而不分桶,但兩害相權:**把兩件不同的事寫成同一條的續報是
    事實錯誤,把一條長線切兩段只是連續性變差**。錯誤輸出比退化嚴重,故取一致性。
    """
    try:
        import news_events as _ne
        entity, lineage = _ne._event_timeline_key(ev)
        return f"e:{_norm(entity)}|l:{_norm(lineage)}"
    except Exception:
        # news_events 不可用時退回本地規則(仍優於完全沒有身分)
        return story_key(ev.get("entity"), ev.get("event_type"),
                         str(ev.get("title") or ""), str(ev.get("published") or ""))


def _is_real_progress(ev: dict) -> bool:
    """這則事件是否代表**真的有新進展**。

    r2(Codex F1):`apply_event_timeline()` 早就算好了 `is_incremental`——跨日的
    重複報導(同一 lifecycle 再被報一次)會被標成 False、lifecycle_weight=0。
    帳本原本無條件把每則都當 delta,於是**沒有新進展的線索照樣
    brewing→developing→peak 拿到主線版面**,prev_delta 與 last_delta 甚至可能是
    同一則標題——那正是這個模組要消滅的「每天寫一樣的東西」。

    欄位缺席時視為有進展(保守:寧可多推進,不要因為上游沒標就整條線索凍住)。
    """
    return ev.get("is_incremental") is not False


def _episodic_bucket(event_type: str, published: str) -> str:
    """財報/財測 → 季 bucket;營收 → 月 bucket;其餘 → 無 bucket。

    規則與 news_events._event_timeline_key 一致(直接取用該模組的型別集合與
    分桶函式,避免兩份規則日後走樣)。
    """
    try:
        import news_events as _ne
    except ImportError:
        return ""
    et = str(event_type or "").strip()
    monthly = et in _ne._MONTHLY_EVENT_TYPES
    if not monthly and et not in _ne._QUARTERLY_EVENT_TYPES:
        return ""
    return _ne._event_period_bucket({"published": published}, monthly=monthly)


def _days_between(a: str, b: str) -> int:
    """兩個 YYYY-MM-DD 相差幾天;格式不對回 0(視為同日,不誤觸降級)。"""
    import datetime as dt
    try:
        da = dt.date.fromisoformat(str(a)[:10])
        db = dt.date.fromisoformat(str(b)[:10])
    except (ValueError, TypeError):
        return 0
    return abs((db - da).days)


def _advance(state: str, has_delta: bool, days_idle: int,
             surprise: float = 0.0) -> str:
    """狀態轉移。**只由 Python 決定**(LLM 不得自行宣告 story 進入高潮)。

    有新進展:往上走一級;surprise 很高時可直接跳到高潮。
    沒有新進展:閒置滿門檻才往下掉,避免週末或單日空窗就把線索打入沉寂。
    """
    idx = STATES.index(state) if state in STATES else 0
    if has_delta:
        if surprise >= HIGH_SURPRISE_PEAK:
            return "peak"
        # 已在收斂的線索有新進展 → 回到發展(事情又有變化),不是再往下掉
        if state == "resolving":
            return "developing"
        if state == "dormant":
            return "developing"          # 復燃
        return STATES[min(idx + 1, STATES.index("peak"))]
    if days_idle >= DORMANT_AFTER_DAYS:
        return "dormant"
    if days_idle >= STALE_DAYS_TO_DEMOTE:
        # r1(Codex F1):**不可用 STATES 的下一個元素當降級**。STATES 是「熱度
        # 由低到高再收斂」的順序,developing 的下一個是 peak——閒置兩天的線索
        # 會被**升級成高潮**並搶到最高版面權重,與降級意圖完全相反。
        # 改用顯式的降級對照表。
        return _IDLE_DEMOTION.get(state, "dormant")
    return state


def update_ledger(ledger: list[dict], events: list[dict], today: str) -> list[dict]:
    """把今日事件併入帳本,回傳更新後的帳本(不改動輸入)。

    `events` 需含 entity / event_type / title,可選 surprise_score 與 summary。
    同一 story 當日多則報導只算一次 delta(避免同事件跨媒體重複推進狀態)。
    """
    by_key = {str(s.get("key")): dict(s) for s in (ledger or []) if s.get("key")}
    # r1(Codex F2):**當日已處理過的 story 視同已推進**。帳本會被持久化,
    # workflow 手動重跑(或補跑)時會拿同一批事件再跑一次 update_ledger,
    # touched 若從空集合開始,每條既有 story 都會再被推進一次、updates 也多加一次
    # ——重跑一次就能把線索灌到高潮。以 last_update == today 當同日守衛。
    touched: set[str] = {k for k, s in by_key.items()
                         if str(s.get("last_update") or "")[:10] == str(today)[:10]}

    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        title = str(ev.get("title") or "").strip()
        if not title:
            continue
        key = story_key_for_event(ev)
        surprise = float(ev.get("surprise_score") or 0.0)
        story = by_key.get(key)
        if story is None:
            by_key[key] = {
                "key": key,
                "entity": str(ev.get("entity") or ""),
                "event_type": str(ev.get("event_type") or ""),
                "headline": title[:120],
                # r1(Codex F5):高 surprise 的**新**線索直接是高潮。原本新開
                # 一律 brewing,等於重大突發事件第一天被 R16b 判為「不當主線」
                # ——那正是最該當主線的時候。
                # 注意**不能**直接套 _advance:它對任何有進展的都升一級,會讓
                # 所有新線索都從 developing 起跳(自測抓到)。只在高 surprise 時跳。
                "state": "peak" if surprise >= HIGH_SURPRISE_PEAK else "brewing",
                "first_seen": today,
                "last_update": today,
                "updates": 1,
                "last_delta": title[:160],
                "prev_delta": "",
                "max_surprise": round(surprise, 3),
            }
            touched.add(key)
            continue
        if key in touched or not _is_real_progress(ev):
            # 當日已推進過、或上游判定為「非增量」的重複報導:
            # 只更新 surprise 上界,不推進狀態、不增加 updates。
            story["max_surprise"] = round(
                max(float(story.get("max_surprise") or 0.0), surprise), 3)
            continue
        story["prev_delta"] = story.get("last_delta") or ""
        story["last_delta"] = title[:160]
        story["headline"] = title[:120]
        story["updates"] = int(story.get("updates") or 0) + 1
        story["max_surprise"] = round(
            max(float(story.get("max_surprise") or 0.0), surprise), 3)
        story["state"] = _advance(str(story.get("state") or "brewing"),
                                  has_delta=True, days_idle=0, surprise=surprise)
        story["last_update"] = today
        touched.add(key)

    # 今日沒被碰到的 story:依閒置天數降級
    for key, story in by_key.items():
        if key in touched:
            continue
        idle = _days_between(story.get("last_update") or today, today)
        story["state"] = _advance(str(story.get("state") or "brewing"),
                                  has_delta=False, days_idle=idle)

    out = list(by_key.values())
    out = [s for s in out
           if _days_between(s.get("last_update") or today, today) <= KEEP_DAYS]
    out.sort(key=lambda s: (STATE_WEIGHT.get(s.get("state"), 0.0),
                            float(s.get("max_surprise") or 0.0),
                            int(s.get("updates") or 0)), reverse=True)
    return out


def active_stories(ledger: list[dict], limit: int = MAX_ACTIVE_STORIES) -> list[dict]:
    """會進 prompt 的 story:排除沉寂者。

    沉寂的**不刪除**——同一條線索日後復燃時(例如併購案重啟)還要接得回去。
    """
    return [s for s in (ledger or []) if s.get("state") != "dormant"][:limit]


def format_story_block(ledger: list[dict], sanitize, limit: int = MAX_ACTIVE_STORIES) -> str:
    """組給 LLM 的敘事脈絡塊。回傳空字串代表今日無活躍線索,呼叫端整段省略。

    `sanitize` 由呼叫端注入(_external_text)——story 的 headline/delta 來自
    外部新聞標題,且會**跨日回流**進 prompt,屬於存放式注入的高風險路徑
    (批#36 的教訓)。模組不得自行繞過消毒入口。
    """
    picked = active_stories(ledger, limit)
    if not picked:
        return ""
    lines = []
    for s in picked:
        state_zh = STATE_ZH.get(str(s.get("state")), "發展")
        ent = sanitize(s.get("entity"), 40) or "(未指名)"
        lines.append(
            f"- [{state_zh}|已追蹤 {int(s.get('updates') or 1)} 次|"
            f"起於 {sanitize(s.get('first_seen'), 12)}] {ent}:"
            f"{sanitize(s.get('headline'), 120)}")
        prev = sanitize(s.get("prev_delta"), 160)
        if prev:
            lines.append(f"    前情:{prev}")
    return "\n".join(lines)
