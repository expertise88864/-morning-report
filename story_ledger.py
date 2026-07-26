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

# Codex r2(P1):否定判準**只有一份**,定義在 news_events(無第一方相依)。
# 兩邊各維護一份會分歧——上一輪就是這樣讓同一句話在兩個訊號上結論相反。
from news_events import is_negated_decision, is_pending_decision

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
SEEN_SIG_KEEP = 12            # 每條線索保留的重播簽章數
KEEP_DAYS = 120               # 帳本保留天數(沉寂的也留著,供日後復燃接回)

_PUNCT_RE = re.compile(r"[\s，。、；：！？「」『』()（）\[\]【】<>《》,.;:!?\"'`~\-—–－]+")


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


SUBJECT_OVERLAP_MIN = 0.10     # 低於此視為「不是同一件事」,分出新線索


def _bigrams(text: str) -> set:
    t = _norm(text)
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else ({t} if t else set())


def _subject_overlap(a: str, b: str, entity: str = "", alias="") -> float:
    """兩則標題的主體重疊度(字元 bigram Jaccard;先剝掉公司識別)。

    剝除是關鍵:同一公司的兩件事標題都含公司名,不剝的話任何兩則都有基礎重疊。

    r5(Codex,P1):**生產環境的 entity 是股票代號**(extract_structured_events
    優先取 code / company_label),而中文標題寫的是公司名——只剝「2317」等於沒剝,
    「鴻海」仍留在兩則標題裡撐高重疊度,短標題就可能越過門檻、拿到錯誤前情。
    故必須連公司名(alias)一起剝;alias 由呼叫端以代號→名稱對照表提供。
    """
    ta, tb = _norm(a), _norm(b)
    aliases = alias if isinstance(alias, (list, tuple, set)) else [alias]
    for token in [_norm(entity)] + [_norm(x) for x in aliases]:
        if token:
            ta, tb = ta.replace(token, ""), tb.replace(token, "")
    ga, gb = _bigrams(ta), _bigrams(tb)
    if not ga or not gb:
        return 1.0          # 無從判斷時不分岔(保連續性)
    return len(ga & gb) / len(ga | gb)


def _is_same_subject(story: dict, ev: dict) -> bool:
    """新事件是否真的是這條線索的續報。

    r3(Codex):`_event_timeline_key` 對 orders/litigation/geopolitical 等只掛「月」
    bucket,同公司同月的兩宗不同訴訟/兩張不同訂單會共用 lineage。news_events 接受
    這個代價,是因為那裡的後果是 event study **少計**(保守、無害);但在 story
    ledger 裡後果是**把錯誤的前情當事實餵給 LLM**,那是輸出錯誤而非保守。

    **不用標題雜湊當 key**(先做過、自測否決):續報本來就會換標題措辭,
    雜湊會把「鴻海洽談收購案 → 鴻海收購案重啟」也切開,等於廢掉本模組的核心價值。
    改用相似度:只有當新事件與該線索**幾乎毫無內容交集**時才判定為另一件事。
    門檻刻意訂得寬鬆(0.10),偏向保住連續性;要分岔必須是明顯不相干的主體。

    比對對象取 headline 與 last_delta 兩者的較高者——線索演進時,新報導可能接近
    最近一次的措辭而非最初的標題。
    """
    title = str(ev.get("title") or "")
    entity = str(story.get("entity") or ev.get("entity") or "")
    alias = str(story.get("entity_name") or ev.get("entity_name") or "").split()
    best = max(_subject_overlap(story.get("headline") or "", title, entity, alias),
               _subject_overlap(story.get("last_delta") or "", title, entity, alias))
    return best >= SUBJECT_OVERLAP_MIN


_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
# r9(Codex,P1):**日期不能一律當雜訊剝掉**。「交易預計 8 月完成」→「延後至
# 9 月完成」是 R16b 明列的實質進展(時程改變),把日期剝光會讓它被判成沒進展、
# 線索在持續發展中被降級沉寂。
# 精準的區分:**只剝掉與該篇發布日相同的日期**——那才是「稿子自己帶的出版日期」
# 這種版面雜訊;標題裡其他的日期是內容(時程、期限、生效日)。
_PUB_DATE_PATTERNS = (
    "{m}月{d}日", "{m}/{d}", "{m}月 {d}日", "{y}年{m}月{d}日",
)
# 中文數量詞:「百億 → 兩百億」這種只用中文寫的更新,純 ASCII 數字抽取看不到。
# r12(Codex,P1):**必須要求數量語境**。上一輪把單位 lookahead 拿掉後,任何
# 「一二兩…」字元都會被當成數字事實——「董事會通過」→「董事會一致通過」的
# 「一致」會產生 {1},同一個決策被判成實質更新。
# 規則:數字串必須含十/百/千/萬/億/兆其一,且以單位字結尾或後接量詞。
_CJK_UNIT_WORDS = "億|萬|千|百|元|口|股|成|倍|%|月|日|季|次|家|名|人|美元|台幣"
_CJK_NUM_RE = re.compile(
    r"(?:[一二兩三四五六七八九十百千萬億兆]*[十百千萬億兆]"
    r"[一二兩三四五六七八九十百千萬億兆]*)"
    r"(?=\s*(?:" + _CJK_UNIT_WORDS + r")|$)")
_MIXED_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([萬億兆])")
_CJK_DIGITS = {"零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4,
               "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CJK_UNITS = {"十": 10, "百": 100, "千": 1000, "萬": 10000,
              "億": 10 ** 8, "兆": 10 ** 12}


def _cjk_to_int(text: str):
    """中文數字 → 整數(涵蓋十/百/千/萬/億的常見寫法);無法解析回 None。

    r11(Codex,P1):「金額 10 億」與「金額十億」是同一個事實的兩種寫法,
    不正規化就會被判成實質更新——跨媒體改寫稿又能推進狀態,繞回老問題。
    """
    total, section, current = 0, 0, 0
    seen = False
    for ch in str(text or ""):
        if ch in _CJK_DIGITS:
            current = _CJK_DIGITS[ch]
            seen = True
        elif ch in _CJK_UNITS:
            unit = _CJK_UNITS[ch]
            seen = True
            if unit >= 10 ** 4:
                base = section + current
                section = (base if base else 1) * unit
                total += section
                section, current = 0, 0
            else:
                section += (current or 1) * unit
                current = 0
        else:
            return None
    return (total + section + current) if seen else None


def _material_facts(text: str, entity: str = "", alias="", published: str = "") -> set:
    """標題裡的**數字事實**(金額/百分比/口數/日期…)。

    r6(Codex,P1):不能拿「標題文字有沒有變」當內容更新的判準——跨媒體改寫本來
    就會換措辭,任何相似度門檻都會把改寫稿誤判成新進展,重複推進的問題就回來了。
    真正可靠的訊號是**數字**:改寫稿會保留同樣的數字,而「金額由 10 億上修至
    20 億」這種實質更新必然帶來不同的數字集合。
    """
    raw = str(text or "").replace(",", "")
    aliases = alias if isinstance(alias, (list, tuple, set)) else [alias]
    for token in [str(entity or "")] + [str(x or "") for x in aliases]:
        if token:
            raw = raw.replace(token, " ")
    for pat in _pub_date_tokens(published):
        raw = raw.replace(pat, " ")
    # 先把「阿拉伯數字 + 中文單位」(如「10 億」)換算成單一數值,
    # 才能與純中文寫法(「十億」)得到相同的事實。
    def _mixed(m):
        try:
            return f" {int(float(m.group(1)) * _CJK_UNITS[m.group(2)])} "
        except (TypeError, ValueError, KeyError):
            return m.group(0)

    raw = _MIXED_NUM_RE.sub(_mixed, raw)
    # 再把純中文數字換算掉
    def _cjk(m):
        val = _cjk_to_int(m.group(0))
        return f" {val} " if val is not None else m.group(0)

    raw = _CJK_NUM_RE.sub(_cjk, raw)
    return {str(int(float(x))) if _is_intlike(x) else x
            for x in _NUM_RE.findall(raw)}


def _is_intlike(x: str) -> bool:
    try:
        return float(x) == int(float(x))
    except (TypeError, ValueError):
        return False


def _pub_date_tokens(published: str) -> list[str]:
    """該篇發布日的各種中文/數字寫法,用來從標題剝掉出版日期雜訊。"""
    import datetime as dt
    raw = str(published or "").strip()
    if not raw:
        return []
    try:
        d = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return []
    # r13(Codex,P1):`published` 是 **UTC** ISO 字串,但台媒標題寫的是**台北**日期。
    # 台灣凌晨 00:00–07:59 發的稿子在 UTC 會落到前一天,產生的 token 是「7月24日」
    # 而標題寫「7月25日」→ 出版日期沒被剝掉,被當成內容事實,跨媒體重複稿因此
    # 被誤判成新進展。轉回台北時區再取年月日。
    if d.tzinfo is not None:
        d = d.astimezone(dt.timezone(dt.timedelta(hours=8)))
    out = []
    for pat in _PUB_DATE_PATTERNS:
        out.append(pat.format(y=d.year, m=d.month, d=d.day))
        out.append(pat.format(y=d.year, m=f"{d.month:02d}", d=f"{d.day:02d}"))
    # r10(Codex,P1):**不剝裸年份**。「預計 2027 年完成」→「提前至 2026 年完成」
    # 若把 2026年 當出版雜訊剝掉,事實集合會空掉、時程提前被判成沒進展。
    # 只剝完整的出版日期形式。
    #
    # 民國年:台灣官方公告普遍寫「115年7月25日」,漏了這式會讓年份洩漏成事實。
    for pat in _PUB_DATE_PATTERNS:
        if "{y}" in pat:
            out.append(pat.format(y=d.year - 1911, m=d.month, d=d.day))
            out.append(pat.format(y=d.year - 1911,
                                  m=f"{d.month:02d}", d=f"{d.day:02d}"))
    # r2(七維度審查,P1)**實跑確認**:呼叫端是逐一 `raw.replace(tok, " ")`,
    # 所以**順序決定結果**。短式「7月25日」先命中,會把「2026年7月25日」切成
    # 「2026年 」,完整式那條 pattern 於是永遠匹配不到 → **裸年份留在事實集合裡**。
    # 後果:同一事實的兩則稿子(一則寫完整日期、一則寫短式)事實集合不同 →
    # 判成實質更新 → 實測重複稿三天衝到 peak、九天仍不沉寂,拿最高版面權重,
    # 正是本模組要消滅的「每天寫一樣的東西」。**長的先剝**即可。
    return sorted(set(out), key=len, reverse=True)


# 決策/極性詞:同一事實的改寫稿會沿用同一個動詞,而「支持→否決」「核准→駁回」
# 這種反轉是 R16b 明列的實質進展,卻不帶任何數字。
# r10(Codex,P1):純數字判準對這類無數字的立場翻轉完全無感。
# r11(Codex,P1):比較的是**語意類別**而非字面詞。「上修」與「調高」是同義改寫,
# 字面不同但事實相同;拿字面比會把改寫稿判成更新,又繞回重複推進的老問題。
# r12(Codex,P1):**只合併真正等價的詞,保留階段**。上一輪把支持/通過/核准/簽約
# 全壓成 approve,於是「董事會支持併購案」→「主管機關核准併購案」這種同方向但
# 階段推進的真實里程碑被判成沒變化,ledger 完全不更新。
_DECISION_CATEGORIES = {
    "support": ("支持", "同意", "看好", "贊成"),
    "board_approve": ("通過", "拍板", "決議", "核定"),
    "regulator_approve": ("核准", "獲准", "許可", "放行"),
    "contract": ("簽約", "成立", "簽署", "定案"),
    "reject": ("反對", "否決", "駁回", "遭拒", "破局", "解約", "撤回", "撤銷",
               "中止", "終止"),
    "delay": ("暫緩", "延後", "推遲", "遞延"),
    "advance": ("提前",),
    "up": ("上修", "調高", "擴大", "看好", "增資", "加碼", "上調"),
    "down": ("下修", "調降", "縮減", "看壞", "減資", "減碼", "下調"),
    "alert": ("警示", "示警"),
    "clear": ("解除", "排除"),
}


def _decision_terms(text: str) -> set:
    """標題命中的決策**語意類別**(不是字面詞)。

    r2(七維度審查,P1)**實跑確認**:先前是純子字串比對,「未通過」含「通過」
    → 與「通過」同判為 board_approve。配上 news_events._event_lifecycle 同樣
    只認「通過」(也把「未通過」判成 confirmed、is_incremental=False),兩個訊號
    同時說「沒進展」→ 帳本完全不更新,headline 停在昨天的**相反**結論,
    還標成「今日無新進展」。送進 LLM 的前情與今天的新聞恰好相反——
    這是**輸出錯誤**,不是保守降級。

    否定判準見 news_events.is_negated_decision(**只有一份**,兩邊共用)。
    """
    t = _norm(text)
    out = set()
    for cat, words in _DECISION_CATEGORIES.items():
        for w in words:
            i = t.find(w)
            while i >= 0:
                # r7(Codex,P1 延伸):待決 ≠ 否決。先前「尚未獲董事會核准」
                # 在 ledger 算 negated_(等同否決),在 lifecycle 卻是 rumor
                # ——兩個訊號又分歧。分出 pending_ 類別後兩邊一致,
                # 而且「待決 → 核准」仍會被正確認成進展。
                if is_pending_decision(t, i):
                    out.add(f"pending_{cat}")
                elif is_negated_decision(t, i):
                    out.add(f"negated_{cat}")
                else:
                    out.add(cat)
                i = t.find(w, i + 1)
    return out


def _participants(text: str, vocab) -> set:
    """標題中出現的**已知**組織/公司名。

    r13(Codex,P1):R16b 明列「參與者的改變」是進展,但先前判準只有數字與決策詞
    ——「鴻海與蘋果洽談合作」→「微軟加入鴻海合作案」兩邊都無數字、無決策詞,
    被判成沒進展,線索即使有新參與者仍逐日降級。
    用**已知名稱詞彙表**比對(不是任意專有名詞抽取),確定性且不會亂認詞。
    """
    if not vocab:
        return set()
    t = _norm(text)
    return {name for name in vocab if name and _norm(name) in t}


def _content_changed(story: dict, ev: dict, vocab=None) -> bool:
    """今日這則相對該線索上一次是否有**實質**更新。

    判準是數字事實的集合有變。標題沒有數字時回 False——此時只剩 lifecycle
    可以判斷,保守地不把改寫稿當進展(寧可少推進,不要每天重複升級)。
    """
    prev = str(story.get("last_delta") or "")
    if not prev:
        return True
    entity = str(story.get("entity") or "")
    alias = str(story.get("entity_name") or "").split()
    before = _material_facts(prev, entity, alias,
                             str(story.get("last_published") or ""))
    after = _material_facts(ev.get("title"), entity, alias,
                            str(ev.get("published") or ""))
    # 決策極性/階段有變 = 實質進展(無數字也算)
    if _decision_terms(prev) != _decision_terms(ev.get("title")):
        return True
    # 參與者有變(新公司加入/退出)= 實質進展。
    # r14(Codex,P1):**必須排除 story 自身的公司名**——改寫稿常常一則寫
    # 「公告本公司董事會通過」、另一則寫「台積電董事會通過」,只因主體有沒有被
    # 寫出來就判成參與者變化,重複稿又能推進 story。數字事實那邊早就剝了,
    # 這裡漏掉。
    own = {_norm(entity)} | {_norm(x) for x in alias}
    others = {v for v in vocab if _norm(v) not in own} if vocab else set()
    if others and _participants(prev, others) != _participants(ev.get("title"), others):
        return True
    if not after:
        return False
    return before != after


def _remember_sig(seen: dict, key: str, sig: str) -> list:
    """把簽章記到有序清單尾端(已存在則移到尾端),只保留最近 N 個。

    順序必須由**插入序**決定,不能靠 set——見 update_ledger 內的說明。
    """
    sigs = seen.setdefault(key, [])
    if sig in sigs:
        sigs.remove(sig)
    sigs.append(sig)
    del sigs[:-SEEN_SIG_KEEP]
    return list(sigs)


_LIFECYCLE_RANK = {"rumor": 0, "confirmed": 1, "implemented": 2, "withdrawn": 3}


def _lifecycle_regresses(ev: dict, story: dict) -> bool:
    """新事件的 lifecycle 是否比 story 目前記錄的**倒退**(如 withdrawn→confirmed)。"""
    a = _LIFECYCLE_RANK.get(_norm(ev.get("lifecycle")), -1)
    b = _LIFECYCLE_RANK.get(_norm(story.get("lifecycle")), -1)
    return a >= 0 and b >= 0 and a < b


def _delta_is_unconfirmed(ev: dict, story: dict) -> bool:
    """這筆 delta 是否「未經權威來源證實」。

    情境:story 目前的權威狀態來自較高分級來源(如 MOPS 官方撤回),而今天這則
    較低分級的報導與之相反(lifecycle 倒退)。內容仍要寫進帳本——它是今日真的
    發生的報導——但不得改寫權威狀態,且必須在 prompt 標明未經證實,
    否則 LLM 會把單一低分級稿當成官方翻案。
    """
    _GRADE = {"A": 3, "B": 2, "C": 1}
    return (_GRADE.get(str(ev.get("source_grade") or "").upper(), 0)
            < _GRADE.get(str(story.get("source_grade") or "").upper(), 0)
            and _lifecycle_regresses(ev, story))


def _is_more_authoritative(ev: dict, story: dict) -> bool:
    """這則事件是否有資格覆寫 story 目前的內容。

    這條規則收斂了四輪,把每一輪的失敗情境一起記下來,避免日後又擺回去:

    r14:不可 last-write-wins——生產端 structured_events 按 quality_score
      **降序**排序,迭代到的最後一則通常品質最低,不是時間最新。
    r15:不可以時間為第一順位——官方 09:00 撤回會被低品質轉載 10:00 的
      「已確認」覆寫。
    r16:不可以 lifecycle 為第一順位——那會讓 withdrawn 永久最高,而
      news_events 明確把「撤回後重啟」當新集數(withdrawn 非終態)。
    r19:**不可以來源分級為第一順位**——A 級(MOPS 官方)建立的線索會永遠
      不能被較晚的 B/C 級真實進展更新,線索被標成「今日無新進展」並逐日沉寂。

    定案規則(不是單一排序鍵,而是兩種情境分開判斷):
      - 事件**較新**:一律允許更新內容(那是今天真的發生的報導)。
        r20(Codex,P1):先前把「較低分級且 lifecycle 倒退」整個擋下,結果
        **官方撤回後、較低分級來源報導的真實重啟也被永久封鎖**——而
        news_events 明訂 withdrawn 非終態、撤回後 lifecycle 應重新起算。
        單憑分級無法分辨「陳舊誤報」與「真實重啟」,故改為:內容照常更新
        (它是今日事實),但**權威狀態保留較高分級那一方**(見
        _delta_is_unconfirmed),並在 prompt 標明未經證實。
      - 事件**較舊**:必須分級**嚴格更高**才可覆寫。等級相同的舊訊息不得蓋掉
        較新的內容(r14 的情境:同批次裡較舊的媒體稿排在官方公告之後)。
    """
    _GRADE = {"A": 3, "B": 2, "C": 1}
    ev_grade = _GRADE.get(str(ev.get("source_grade") or "").upper(), 0)
    st_grade = _GRADE.get(str(story.get("source_grade") or "").upper(), 0)
    newer = str(ev.get("published") or "") >= str(story.get("last_published") or "")
    if not newer:
        return ev_grade > st_grade
    return True


def _remember_today(today_sigs: dict, key: str, sig: str, today: str) -> dict:
    """當日批次簽章(**不截斷**)——重跑冪等性靠它。"""
    bucket = today_sigs.setdefault(key, set())
    bucket.add(sig)
    return {"date": str(today)[:10], "sigs": sorted(bucket)}


def _event_signature(ev: dict) -> str:
    """單一事件的重播簽章。

    r8(Codex,P1):**必須含 lifecycle**。只雜湊標題的話,「傳聞→證實」這種
    lifecycle 有推進但標題沒變的情況會被判成重播、直接短路掉,
    連 _is_real_progress 都走不到 → delta、last_update、狀態全部停在舊值。
    lifecycle 轉移正是 news_events 明訂的「真增量」。
    """
    raw = _norm(ev.get("title")) + "|" + _norm(ev.get("lifecycle"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _is_real_progress(ev: dict, story: dict | None = None, vocab=None) -> bool:
    """這則事件是否代表**真的有新進展**。

    r2(Codex):`apply_event_timeline()` 算好的 `is_incremental` 可用來擋掉跨日的
    純重複報導——帳本原本無條件把每則都當 delta,沒有新進展的線索照樣升到主線版面。

    r5(Codex,P1):**但 `is_incremental` 只比較 lifecycle,不看內容**。
    「訂單金額 10 億(confirmed)」→「金額上修 20 億(confirmed)」這種真實進展
    會被標成 False,若拿它當唯一門檻,線索會在持續發展中被判為停滯、照樣降級沉寂,
    LLM 也看不到今日真正的 delta。那比原本的問題更糟。

    故兩個訊號分開用:
      有進展 = 內容變了 **或** lifecycle 有推進
      沒進展 = 內容沒變 **且** lifecycle 也沒推進(才是真正的重複稿)
    新線索(story=None)只看 lifecycle:陳舊的重複報導不該憑空建出活躍線索。
    """
    lifecycle_incremental = ev.get("is_incremental") is not False
    if story is None:
        return lifecycle_incremental
    return _content_changed(story, ev, vocab) or lifecycle_incremental


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


def update_ledger(ledger: list[dict], events: list[dict], today: str,
                  name_map: dict | None = None) -> list[dict]:
    """把今日事件併入帳本,回傳更新後的帳本(不改動輸入)。

    `events` 需含 entity / event_type / title,可選 surprise_score 與 summary。
    同一 story 當日多則報導只算一次 delta(避免同事件跨媒體重複推進狀態)。
    """
    by_key = {str(s.get("key")): dict(s) for s in (ledger or []) if s.get("key")}
    # r1(Codex F2):**當日已處理過的 story 視同已推進**。帳本會被持久化,
    # workflow 手動重跑(或補跑)時會拿同一批事件再跑一次 update_ledger,
    # touched 若從空集合開始,每條既有 story 都會再被推進一次、updates 也多加一次
    # ——重跑一次就能把線索灌到高潮。以 last_update == today 當同日守衛。
    # r7(Codex F2):同日守衛不能整條 story 一起鎖。手動重跑時若某條線索當日
    # **確實有更新的報導**(首跑 10 億、重跑拿到官方 20 億),整條被 touched 擋住
    # 會讓 headline/delta/狀態全部停在舊值。改記**事件簽章**:完全相同的重播是
    # no-op,同日的實質更新仍可套用。
    # 參與者比對用的已知名稱詞彙表(公司名/別名)。空的話該訊號自動停用。
    vocab = {v for val in (name_map or {}).values()
             for v in str(val or "").split() if len(v) >= 2}
    touched: set[str] = set()
    # r11(Codex,P1):簽章必須用**有序 list**。原本轉成 set 再 `list(sigs)[-12:]`
    # 截斷——set 沒有順序,而 Python 的字串 hash 每個 process 都不同(hash
    # randomization),截斷會**隨機**丟掉近期簽章;跨天執行時被丟掉的那則若數字
    # 事實不同,重跑就會再次推進狀態,冪等性破功。
    seen_sigs: dict[str, list] = {
        k: [str(x) for x in (st.get("seen_sigs") or [])] for k, st in by_key.items()}
    # r12(Codex,P1):跨日記憶會截斷到 SEEN_SIG_KEEP,但**當日批次不能截斷**——
    # 若某條線索當天有超過 12 則不同簽章,最舊的會被淘汰,重跑時那幾則又被當成
    # 新進展套用一次,冪等性仍然破功。故當日簽章另存一份完整清單,跨日再合併進
    # 有上限的歷史記憶。
    today_sigs: dict[str, set] = {}
    for k, st in by_key.items():
        day = st.get("today_sigs") or {}
        if str(day.get("date") or "")[:10] == str(today)[:10]:
            today_sigs[k] = {str(x) for x in (day.get("sigs") or [])}

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
            # r3(Codex):**建立新線索前也要確認是真進展**。原本只在既有 story 的
            # 分支檢查 is_incremental,於是首次部署或 story 被清掉之後,一則陳舊的
            # 重複報導會憑空建出一條「活躍」線索。真正的首次出現會在下一次真增量時進來。
            if not _is_real_progress(ev):
                continue
            by_key[key] = {
                "key": key,
                "entity": str(ev.get("entity") or ""),
                # r5:公司名(代號→名稱)。主體比對必須連名稱一起剝,
                # 否則同公司的兩件事只靠共同的公司名就能撐過門檻。
                "entity_name": str((name_map or {}).get(
                    str(ev.get("entity") or ""), "") or ev.get("entity_name") or ""),
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
                "seen_sigs": [_event_signature(ev)],
                "today_sigs": {"date": today, "sigs": [_event_signature(ev)]},
                "last_published": str(ev.get("published") or ""),
                "lifecycle": str(ev.get("lifecycle") or ""),
                "source_grade": str(ev.get("source_grade") or ""),
            }
            seen_sigs[key] = [_event_signature(ev)]
            today_sigs[key] = {_event_signature(ev)}
            touched.add(key)
            continue
        sig = _event_signature(ev)
        replayed = (sig in seen_sigs.get(key, [])
                    or sig in today_sigs.get(key, set()))
        # r13(Codex,P1):同批次同 key 的**第二則真進展**不能整個丟掉。
        # story key 不含 direction,而上游的 cluster key 含 direction,所以
        # 「訂單成立」與「訂單取消」可以同批共存;原本第二則被 touched 壓掉、
        # 簽章還被記成已消費 → 重跑也補不回來,晨報會繼續寫「成立」而漏掉「取消」。
        # 修:當日只推進一次狀態(避免灌到高潮),但**內容仍要更新到最新那則**。
        if (key in touched and not replayed
                and _is_real_progress(ev, story, vocab)):
            if _is_more_authoritative(ev, story):
                _unconf = _delta_is_unconfirmed(ev, story)
                story["prev_delta"] = (story.get("last_delta") or "")                     if _is_same_subject(story, ev) else ""
                story["last_delta"] = title[:160]
                story["last_published"] = str(ev.get("published") or "")
                story["headline"] = title[:120]
                story["delta_unconfirmed"] = _unconf
                if not _unconf:
                    story["lifecycle"] = str(
                        ev.get("lifecycle") or story.get("lifecycle") or "")
                    story["source_grade"] = str(
                        ev.get("source_grade") or story.get("source_grade") or "")
            story["max_surprise"] = round(
                max(float(story.get("max_surprise") or 0.0), surprise), 3)
            story["seen_sigs"] = _remember_sig(seen_sigs, key, sig)
            story["today_sigs"] = _remember_today(today_sigs, key, sig, today)
            continue
        if replayed or key in touched or not _is_real_progress(ev, story, vocab):
            # 完全相同的重播、當次呼叫已推進過、或上游判定非增量且無實質更新:
            # 只更新 surprise 上界,不推進狀態、不增加 updates。
            # r9(Codex,P1):**被壓下的事件也必須記簽章**。同一批若有兩則同 key
            # 的不同更新,第二則被 touched 壓下卻沒記簽章 → 下次重跑時第一則被
            # 認出是重播、第二則卻被當成新的而套用,同樣的輸入跑兩次結果不同。
            story["seen_sigs"] = _remember_sig(seen_sigs, key, sig)
            story["today_sigs"] = _remember_today(today_sigs, key, sig, today)
            story["max_surprise"] = round(
                max(float(story.get("max_surprise") or 0.0), surprise), 3)
            continue
        # r3(Codex):同 lineage 但主體明顯不同(同公司同月的另一件訴訟/訂單)時,
        # **不得輸出前情**——Codex 指的實害正是「錯誤的前情被當事實餵給 LLM」。
        # 刻意不分岔成新線索:續報本來就會換措辭,以標題相似度決定身分會把真正的
        # 長線切碎(自測否決過),那是拿模組核心價值換一個較小的風險。
        # 只清掉前情,線索本身照常推進——寧可少給脈絡,不可給錯脈絡。
        # r17(Codex,P1):**跨日的內容覆寫同樣要過權威檢查**。先前只有同批次那條
        # 路徑檢查,於是昨天的 A 級官方撤回,今天可以被 C 級舊稿覆寫回「已確認」
        # ——apply_event_timeline 把 withdrawn 之後的任何報導都視為增量,
        # 所以這條路徑必然會走到。
        if not _is_more_authoritative(ev, story):
            # r18(Codex,P1):權威不足時**什麼進度都不能動**。先前只擋住內容覆寫,
            # 但 updates / state / last_update / touched 仍照樣更新 → 昨天的官方
            # 撤回會被今天的 C 級舊稿標成「今日有新進展」、還可能被推向高潮,
            # 並因為 touched 而躲過閒置降級。只記簽章與 surprise 上界。
            story["max_surprise"] = round(
                max(float(story.get("max_surprise") or 0.0), surprise), 3)
            story["seen_sigs"] = _remember_sig(seen_sigs, key, sig)
            story["today_sigs"] = _remember_today(today_sigs, key, sig, today)
            continue
        unconfirmed = _delta_is_unconfirmed(ev, story)
        story["prev_delta"] = (story.get("last_delta") or "") \
            if _is_same_subject(story, ev) else ""
        story["last_delta"] = title[:160]
        story["last_published"] = str(ev.get("published") or "")
        story["headline"] = title[:120]
        story["delta_unconfirmed"] = unconfirmed
        if not unconfirmed:
            # 權威狀態(lifecycle / source_grade)只由「不低於現況分級」的來源改寫;
            # 較低分級且與官方結論相反的報導,內容照收但不得改寫權威狀態。
            story["lifecycle"] = str(
                ev.get("lifecycle") or story.get("lifecycle") or "")
            story["source_grade"] = str(
                ev.get("source_grade") or story.get("source_grade") or "")
        story["updates"] = int(story.get("updates") or 0) + 1
        story["max_surprise"] = round(
            max(float(story.get("max_surprise") or 0.0), surprise), 3)
        story["state"] = _advance(str(story.get("state") or "brewing"),
                                  has_delta=True, days_idle=0, surprise=surprise)
        story["last_update"] = today
        story["seen_sigs"] = _remember_sig(seen_sigs, key, sig)
        story["today_sigs"] = _remember_today(today_sigs, key, sig, today)
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


def active_stories(ledger: list[dict], limit: int = MAX_ACTIVE_STORIES,
                   today: str = "") -> list[dict]:
    """會進 prompt 的 story:排除沉寂者,**今日有更新的優先**。

    沉寂的**不刪除**——同一條線索日後復燃時(例如併購案重啟)還要接得回去。

    r17(Codex):版面上限是 12 條,而閒置一天的線索仍停在 peak/developing,
    權重排序會讓「今天沒動的舊高潮」擠掉「今天真的有進展的線索」——那正好違反
    R16b「沒有新進展整條不要寫」。故今日有更新者一律排在前面。
    """
    alive = [s for s in (ledger or []) if s.get("state") != "dormant"]
    if today:
        alive.sort(key=lambda s: str(s.get("last_update") or "")[:10] == str(today)[:10],
                   reverse=True)
    return alive[:limit]


def format_story_block(ledger: list[dict], sanitize, limit: int = MAX_ACTIVE_STORIES,
                       today: str = "") -> str:
    """組給 LLM 的敘事脈絡塊。回傳空字串代表今日無活躍線索,呼叫端整段省略。

    `sanitize` 由呼叫端注入(_external_text)——story 的 headline/delta 來自
    外部新聞標題,且會**跨日回流**進 prompt,屬於存放式注入的高風險路徑
    (批#36 的教訓)。模組不得自行繞過消毒入口。
    """
    picked = active_stories(ledger, limit, today)
    if not picked:
        return ""
    lines = []
    for s in picked:
        state_zh = STATE_ZH.get(str(s.get("state")), "發展")
        ent = sanitize(s.get("entity"), 40) or "(未指名)"
        # r17(Codex):標明**今日有無新進展**。沒標的話,今天沒動的線索看起來與
        # 有進展的一樣新,LLM 會照樣重述 → 正是 R16b 要消滅的每日重複。
        fresh = ("今日有新進展"
                 if today and str(s.get("last_update") or "")[:10] == str(today)[:10]
                 else "今日無新進展(僅供脈絡,不要單獨成條)")
        # r2(七維度審查,P2):**這是黏著旗標**——設定後若隔天該線索沒有新聞就
        # 永不清除,prompt 會同時出現「今日無新進展」與「今日報導未經權威來源
        # 證實」兩句自相矛盾的標註。旗標描述的是「今日這則」,所以只有今天真的
        # 有更新時才該掛。
        if s.get("delta_unconfirmed") and fresh.startswith("今日有新進展"):
            fresh += "|今日報導未經權威來源證實,與官方既有結論不一致,須註明"
        lines.append(
            f"- [{state_zh}|{fresh}|已追蹤 {int(s.get('updates') or 1)} 次|"
            f"起於 {sanitize(s.get('first_seen'), 12)}] {ent}:"
            f"{sanitize(s.get('headline'), 120)}")
        prev = sanitize(s.get("prev_delta"), 160)
        if prev:
            lines.append(f"    前情:{prev}")
    return "\n".join(lines)
