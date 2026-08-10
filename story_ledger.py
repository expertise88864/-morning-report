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
import news_events as _ne_module
from news_events import (is_negated_decision, is_pending_decision,
                         _content_bigrams, strip_outlet_suffix)

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
#: 每條線索保留幾個「時間點」。批#57:先前只存 last_delta / prev_delta 兩步,
#: 讀者看不到「上週說什麼」,也沒有連結可以回去讀。六步約可涵蓋一到兩週的
#: 報導節奏(同一條線索不會天天有實質更新)。
TIMELINE_KEEP = 6
#: 沉寂線索只留頭尾兩點——479 條線索若每條都留六點,state 檔會膨脹一倍。
TIMELINE_KEEP_DORMANT = 2
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


#: 「大盤/類股總結」類標題 —— 這種文章**沒有單一主體**,它講的是一整天的市場。
#: 實測(2026-07-29 帳本):〈美股盤後〉的市場總結被掛到「聯電」名下開成線索,
#: 因為文中提到了那檔股票;它的「數字事實」抽出來是道瓊漲點(260 → 500),
#: 於是「線索追蹤」卡把兩則不同日的大盤總結並列,看起來像同一件事在演進
#: —— 那是**完全沒有意義的軌跡**,而且它排在卡片第一條。
#: **強標記** —— 出現即為市場總結,不論標題其他內容。
#: 這些詞本身就宣告「這是一篇綜覽」,不是某公司的事。
_WRAP_STRONG = ("操盤筆記", "看盤", "盤勢", "盤中速報", "盤後速報",
                "美股四大指數", "台股收盤", "台股收跌", "台股收漲",
                "指數收", "股市收")
#: 欄目型標題的前綴(〈台股盤後〉…)
_WRAP_PREFIX = ("〈", "【", "《")
#: 欄目名裡出現這些詞 = **場次總結**欄目(不限哪個市場:〈能源盤後〉也是)。
#: r7(Codex,P1):**只認場次詞**。原本還收「速報/快訊」與市場詞,於是
#: 【美股焦點】輝達財報後大漲、【財報快訊】台積電獲利創高 在檢查公司名**之前**
#: 就被判成綜覽而靜默丟棄 —— 「焦點/快訊」是版面標籤,不是市場總結。
#: (「盤中速報」這類仍由 _WRAP_STRONG 抓,它是完整的欄目名而非單一泛詞。)
_WRAP_COLUMN_WORDS = ("盤後", "盤前", "盤中", "收盤", "開盤")
#: 市場級主體 —— 出現在標題裡代表這篇可能是在講整個市場。
_WRAP_MARKET_SUBJECTS = ("美股", "台股", "大盤", "指數", "費半", "道瓊",
                         "那指", "標普", "韓股", "日股", "陸股", "亞股")
#: 漲跌方向詞 —— 與市場主體同時出現才構成「市場在動」的敘述。
_WRAP_DIRECTION = ("收黑", "收紅", "收盤", "收漲", "收跌", "重挫", "大漲",
                   "大跌", "走高", "走低", "止穩", "反彈", "回檔", "盤後",
                   "盤前", "盤中")
#: (以下為 v1-v6 留下的常數,已不再使用,保留註記說明為何移除)

def is_market_wrap(title: str, known_names=()) -> bool:
    """標題是否為「大盤/類股總結」——這種文章不該開線索(它沒有單一主體)。

    2026-07-29 實測:〈美股盤後〉的市場總結被掛到「聯電」名下開成線索,
    「數字事實」抽出來是道瓊漲點(260 → 500),兩則不同日的總結被並列成
    「一條演進中的線索」——毫無意義,而且排在「線索追蹤」卡第一條。

    **這是第七版。** 前六版都在加關鍵字與詞性後綴,每一版都被下一個反例打破:
        v1 單一清單            → 漏「本週操盤筆記」
        v2 事件詞豁免          → 放走含「財報」的綜覽
        v3 強/弱兩級           → 仍以「出現關鍵字」當證據
        v4 市場主體+方向        → 繞過公司事件豁免
        v5 時節後綴(含動詞)    → 「法說會登場」被判成時節
        v6 拿掉動詞            → 「財報登場」反而被當成公司事件
    每一次都是同一個病:**用「有沒有出現某個詞」代替「這篇有沒有單一主體」**。

    第七版改用更直接的訊號:**標題裡有沒有具名公司**(呼叫端提供詞彙表)。
        - 強標記(操盤筆記/速報/看盤/盤勢)→ 一律綜覽
        - 欄目型標題且欄目名是市場詞(〈台股盤後〉…)→ 綜覽
        - 市場主體 + 漲跌方向,**且標題裡沒有任何已知公司名** → 綜覽
    其餘一律放行。**失敗方向刻意偏向放行**:誤判成綜覽會讓線索永遠開不起來
    且完全無聲;誤放行只是多一條雜訊線索,而卡片本來就要求 ≥2 個時間點。
    """
    t = str(title or "").strip()
    if not t:
        return False
    if any(m in t for m in _WRAP_STRONG):
        return True
    # 欄目型標題:〈美股盤後〉〈能源盤後〉【盤後】… —— 這個格式本身就是「綜覽」,
    # 不必是哪個市場(自測抓到:〈能源盤後〉的「能源」不在市場詞清單裡)。
    # 欄目型標題分兩種:
    #  (a) **場次欄目**(〈台股盤後〉〈能源盤後〉)—— 那就是一篇場次總結,絕對成立。
    #  (b) **市場欄目**(【美股】)—— r8(Codex,P2):把市場詞從欄目分支整個拿掉
    #      會讓「【美股】道瓊漲500點」漏判(「漲」不在方向詞裡)。但它必須
    #      **讓給具名公司**,否則【美股焦點】輝達財報後大漲 又會被誤殺。
    #      與主規則同一個原則:有具名公司就不是綜覽。
    _bracketed = t[:1] in _WRAP_PREFIX
    if _bracketed and any(m in t[:14] for m in _WRAP_COLUMN_WORDS):
        return True
    # **沒有詞彙表就不套市場規則**:那條規則靠「標題裡沒有已知公司名」當證據,
    # 詞彙表空的時候「沒有公司名」是必然成立的,會把所有市場相關標題都判成綜覽
    # ——那是最危險的方向(線索永遠開不起來且完全無聲)。刻意偏向放行。
    if not known_names:
        return False
    _named = any(n and len(str(n)) >= 2 and str(n) in t
                 for n in known_names)
    # (b) 市場詞欄目 —— r9(Codex,P1):這一支原本被我寫在 fail-open 守衛
    #     **前面**,於是無詞彙表時反而變成「偏向丟棄」,跟上面那段註解寫的契約
    #     正好相反。它跟主規則一樣是靠「沒有已知公司名」當證據的,就必須跟主規則
    #     待在同一側。
    if _bracketed and not _named and any(
            m in t[:14] for m in _WRAP_MARKET_SUBJECTS):
        return True
    if not (any(m in t for m in _WRAP_MARKET_SUBJECTS)
            and any(d in t for d in _WRAP_DIRECTION)):
        return False
    return not _named


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


def _norm_keep_clauses(text: str) -> str:
    """與 _norm 相同,但**保留子句分隔符**(供作用域判斷用)。

    r10(Codex,P1):_norm 的 _PUNCT_RE 正好剝除 is_pending_decision 依賴的
    那些字元,兩者直接串起來會讓子句邊界失效。
    """
    from news_events import _CLAUSE_SEPARATORS
    return "".join(
        ch for ch in str(text or "").lower()
        if ch in _CLAUSE_SEPARATORS or not _PUNCT_RE.match(ch))


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
    # r10(Codex,P1):**_norm() 會剝掉逗號等子句分隔符**,而 is_pending_decision
    # 正是靠那些分隔符界定作用域 → 「本案尚待主管機關審議,董事會已通過」
    # 被判成 pending_board_approve,而 news_events 對原文判 confirmed。
    # 兩訊號又分歧,而且媒體只要調換相同子句的順序,ledger 就會誤判為實質更新
    # 並推進狀態。作用域判斷要看得到分隔符,故此處保留它們;
    # 詞彙比對本身不受影響(決策詞裡不含標點)。
    t = _norm_keep_clauses(text)
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


# r10(Codex,P1):**rejected 原本不在這張表裡**。後果:A 級官方把線索標成
# rejected 後,隔日較新的 C 級媒體稿仍稱 confirmed 時 _lifecycle_regresses()
# 回 False → 那則低分級稿不會被標成 delta_unconfirmed,還會覆寫官方 lifecycle
# 與來源級別,**使持久化帳本與 prompt 從「官方否決」翻回「已確認」**。
# 同一套保護早就為 withdrawn 做了,news_events 的狀態機也已納入 rejected,
# 只有 ledger 的權威比較表停在四種舊狀態——我加 rejected 時沒推廣到這裡。
# rejected 與 withdrawn 同階:兩者都是「結論被推翻」的權威終局。
_LIFECYCLE_RANK = {"rumor": 0, "confirmed": 1, "implemented": 2,
                   "withdrawn": 3, "rejected": 3}


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


def format_fact(raw) -> str:
    """數字事實 → 可讀字串(10000000000 → 「100億」)。

    帳本裡存的是正規化後的**純數字字串**(中文與阿拉伯寫法都換算成同一個值,
    才能比對出「金額有沒有變」)。直接印給讀者看是一串零,故渲染時換回中文單位。
    """
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    for unit, div in (("兆", 10 ** 12), ("億", 10 ** 8), ("萬", 10 ** 4)):
        if abs(n) >= div:
            v = n / div
            return f"{v:.10g}{unit}"
    return f"{n:.10g}"


def _norm_point_title(raw) -> str:
    """軌跡點的標題比對鍵:去空白與標點,避免同一則因全半形差異被當成兩件事。"""
    import re as _re
    return _re.sub(r"[\s\W_]+", "", str(raw or "")).lower()[:60]


def _timeline_entry(ev: dict, today: str, facts) -> dict:
    """一個時間點。刻意用短鍵名(d/t/l/s/f):479 條線索 × 6 點,
    鍵名長度直接反映在 state 檔大小與每日 commit 的 diff 量上。"""
    return {
        "d": str(today)[:10],
        "t": str(ev.get("title") or ev.get("headline") or "")[:80],
        "l": str(ev.get("link") or "")[:200],
        "s": str(ev.get("source_name") or ev.get("source") or "")[:24],
        "f": sorted(facts)[:4] if facts else [],
    }


#: 同一天最多留幾個軌跡點。1 會讓同日的第二件事**靜默消失**;
#: 太多則單日就能塞滿整條軌跡(TIMELINE_KEEP=6)。
TIMELINE_MAX_PER_DAY = 2


def _push_timeline(story: dict, entry: dict,
                   supersede_today: bool = False) -> None:
    """把時間點併入線索軌跡。

    批#67(P1-4):原本是「同日只留最後一筆」——同一天的第二件事會把第一件
    整個刪掉。批#64 讓同一天同一家公司的兩件不同事件不再互相吞併之後,
    這個缺陷變得具體:蘋果訂單與輝達訂單同日進來,軌跡上只會剩後者,
    而「敘事連貫性」正是這條軌跡存在的理由。

    改為:同日**同標題**視為同一件事(較後者勝出,通常是更完整的版本);
    同日不同標題可並存,但每日上限 `TIMELINE_MAX_PER_DAY`——事件是依
    quality_score 由高到低處理的,所以超額時捨棄的是品質較低的那些。
    """
    tl = [x for x in (story.get("timeline") or []) if isinstance(x, dict)]
    day = str(entry.get("d"))
    key = _norm_point_title(entry.get("t"))
    if supersede_today:
        # 呼叫端明說「這一則取代了線索今天的看法」(權威來源推翻先前報導)。
        # 這種取代**必須靠呼叫端的知識**,不能靠標題相似度猜:
        # 「收購案成立」與「收購案取消」字面上差很遠,卻正是同一件事的翻轉。
        tl = [x for x in tl if str(x.get("d")) != day]
    else:
        tl = [x for x in tl
              if not (str(x.get("d")) == day
                      and _norm_point_title(x.get("t")) == key)]
        if len([x for x in tl if str(x.get("d")) == day]) >= TIMELINE_MAX_PER_DAY:
            return                  # 當日已滿:保留先到的(品質較高的)那些
    tl.append(entry)
    story["timeline"] = tl[-TIMELINE_KEEP:]


def prune_timeline(story: dict) -> None:
    """沉寂線索只留頭尾兩點(復燃時仍接得回「最初是什麼、最後停在哪」)。"""
    if str(story.get("state")) != "dormant":
        return
    tl = [x for x in (story.get("timeline") or []) if isinstance(x, dict)]
    if len(tl) > TIMELINE_KEEP_DORMANT:
        story["timeline"] = [tl[0], tl[-1]]


#: 標題裡的媒體名/欄目/版型雜訊。剝掉之後才比得出「講的是不是同一件事」。
#: 太常見、不具辨識力的英文詞。留著會讓「不同子公司」看起來像同一個。
#: (與 news_events 那份同名集合**用途不同**:這一份給「線索身分的英文互斥
#:  守衛」,那一份給「事件對象指紋」;兩者的取捨方向相反,刻意不共用。)
_SUBJECT_LATIN_STOP = {"LIMITED", "LTD", "INC", "CORP", "CORPORATION", "CO",
                       "THE", "AND", "FOR", "NEW", "AI", "ETF", "US", "CEO",
                       # 批#80 r3:樣板詞混進「具辨識力的英文詞」會讓**互斥守衛
                       # 自己失效** —— 兩則不同公司的鉅亨速報都含 FACTSET/EPS,
                       # 交集非空,守衛因此不觸發。這一格存在的目的就是收容
                       # 這種「常見但不辨識」的詞,它們正是最典型的例子。
                       "FACTSET", "EPS"}
_SUBJECT_LATIN = re.compile(r"[A-Za-z]{2,}")
#: 有主體時可以放寬(主體本身已經是很強的錨);無主體時沒有錨,必須保守。
#: 兩個門檻都由 1502 條真實線索校準,見 `_same_story_subject`。
STORY_MATCH_THRESHOLD = 0.45
STORY_MATCH_THRESHOLD_NO_ENTITY = 0.65
#: 共同 bigram 少於這個數就不算,免得極短的通用主旨(「營收公布」)四處攀親
STORY_MATCH_MIN_SHARED = 5


def _story_subject(title, entity="", entity_name="") -> str:
    """把標題化簡成「主旨」:去掉媒體名、欄目版型與主體本身。

    實測依據:未剝版型時,相似度 0.5~0.6 那一段被版型雜訊主宰——
    「中信銀行 沈強副行長任職資格」與「中信證券:二次原油衝擊」只因為共用
    「提供者 智通財經 - Investing」後綴就拿到 0.54,兩者毫不相干。
    """
    # 批#72 r1:媒體尾綴/欄目/來源標註的剝除已下移到 news_events 供兩層共用
    # (事件對象指紋也需要同一套規則)。原本這裡有一份,那就是重複造輪子。
    out = strip_outlet_suffix(title)
    for x in (entity, entity_name):
        if x and len(str(x)) >= 2:
            out = out.replace(str(x), "")
    return out.strip()


def _subject_latin_tokens(text: str) -> set:
    return {w.upper() for w in _SUBJECT_LATIN.findall(str(text or ""))}         - _SUBJECT_LATIN_STOP


def _same_story_subject(a: str, b: str, threshold: float) -> bool:
    """兩個主旨是否屬於**同一條敘事**。

    批#67(P1-3)。診斷:真實 state 裡 1502 條線索有 1485 條只有 1 次更新,
    **沒有任何一條累積到 3 個軌跡點**——「敘事連貫」這個功能實際上沒有在運作。
    根因是 `general` 事件的線索 key 內含**標題 digest**:換一個標題就開新線索,
    續報永遠接不回去(881 條無主體線索更是每篇文章一條)。

    分母取**較短**的一邊、門檻由那 1502 條線索校準:
      - 有主體 0.45:0.45~0.55 這一段人工核對全部是真的同一條敘事
        (中信銀亞灣分行六家媒體、高通晶片漲價、廣達買友達廠房、
        Meta/BlackRock 140 億、蘋果服務中斷、特斯拉電池…),
        再往下到 0.36 也仍然乾淨——取 0.45 是留餘裕,不是那裡開始出錯。
      - 無主體 0.65:沒有主體當錨,而無主體線索有 881 條、配對數量級大得多,
        誤判機會相應變高,所以保守。

    兩道守衛(缺一不可,校準時兩者都出現過反例):
      1. **具辨識力的英文詞完全互斥 → 不同主體**。抓的是「同一份制式公告、
         不同子公司」:CHANNEL PILOT vs ASUS INTERNATIONAL 相似度 0.56,
         比該合併的廣達那組(0.46)還高,純門檻切不開。
      2. **共同 bigram 至少 5 個**,免得極短的通用主旨四處攀親。

    另外主旨必須先剝掉媒體名/欄目/來源標註(見 `_story_subject`)——
    未剝之前「蘋果超越輝達重返市值最高」與「蘋果調漲產品售價」只因共用
    「Business Insider Taiwan」就被判成同一條。
    """
    ga, gb = _content_bigrams(a), _content_bigrams(b)
    shared = ga & gb
    if len(shared) < STORY_MATCH_MIN_SHARED:
        return False
    # 分母取**較短**的一邊 —— 與批#64 的事件去重刻意相反。續報常常把內容
    # 變長(「收購案成立」→「收購案完成交割 細節公布 金額300億」),
    # 用較長的一邊當分母會被長度稀釋成 0.13,敘事就接不起來。
    # 兩邊的取捨方向本來就不同:事件去重錯了會**消滅一則真事件**,
    # 線索歸屬錯了只是把兩條敘事併在一起,後者可回復、前者不可。
    if len(shared) / min(len(ga), len(gb)) < threshold:
        return False
    la, lb = _subject_latin_tokens(a), _subject_latin_tokens(b)
    return not (la and lb and not (la & lb))


def _resolve_story_key(ev: dict, by_key: dict) -> str:
    """事件歸屬哪條線索。主動追蹤帶回來的 `followup_key` 只是**提示**。

    r2(Codex,P1):追蹤查詢直接帶著發起它的線索 key,否則 event_type 由標題推導
    (常是 general 且帶標題 digest),算出來的 key 與原線索不同,主動追蹤等於白做。

    r3(Codex,P1)**但無條件採用是危險的**:Google News 查詢本來就會撈回不相干或
    只是「沾到同一家公司」的文章,而那些文章會被強制掛進該線索、**取代它的
    headline 與軌跡點、把它標成今日有更新**,還影響公司催化評分。
    (這正是我自己列進 review focus 的風險,而它成真了。)

    故採用前必須通過既有的主體比對——那是本模組原本就用來判斷「這則是不是這條
    線索的續報」的判準,直接重用,不另造一套會走樣的。不通過就退回正常推導,
    讓它自己去開一條線索(那是誠實的:它確實是另一件事)。
    """
    followed = str(ev.get("followup_key") or "").strip()
    if not followed.startswith(("e:", "h:", "cluster:")):
        return _match_open_story(ev, by_key) or story_key_for_event(ev)
    target = by_key.get(followed)
    if not target:
        return _match_open_story(ev, by_key) or story_key_for_event(ev)
    # 條件一:主體要對得上。
    if not _is_same_subject(target, ev):
        return _match_open_story(ev, by_key) or story_key_for_event(ev)
    # 條件二(r3 Codex,P1):**_is_same_subject 一個人擋不住**。它的門檻
    # (SUBJECT_OVERLAP_MIN=0.10)是**刻意寬鬆**的——設計目的是「已經同 key 時
    # 要不要保留前情」,偏向保住連續性;拿它當歸屬閘門,等於用寬鬆的檢查做嚴格
    # 的事。Codex 的反例:AI 伺服器**專利訴訟**與 AI 伺服器**訂單**的標題重疊度
    # 足以越過 0.10,於是訴訟被強制掛進 orders 線索、取代它的 headline 與軌跡。
    #
    # 關鍵在於**我們為什麼需要這個提示**:追蹤抓回來的文章 event_type 常被推導成
    # `general`(標題看不出類型),算出來的 key 因而與原線索不同。
    # 但若它**自己就推導出一個明確且不同的類型**(litigation vs orders),
    # 那正是「這是另一件事」的證據——此時應該相信它自己的判斷,而不是提示。
    # r5(Codex,P2)**這正是我送審時自己標記的邊界,確認過嚴了**:
    # 線索常以 general 起頭(早期標題看不出類型),之後的後續報導才把它講清楚
    # (orders / litigation…)。原本的條件會把那則**正確的後續報導**判為矛盾、
    # 另開一條線索,反而把軌跡切斷、讓原線索停在舊值。
    # 只有**雙方都明確且不同**才算矛盾;目標仍是 general 時,新的明確分類
    # 是資訊增加而不是衝突 —— 接回去,並把線索的型別一起升級。
    ev_type = str(ev.get("event_type") or "").strip() or "general"
    tgt_type = str(target.get("event_type") or "").strip() or "general"
    if ev_type != "general" and tgt_type != "general" and ev_type != tgt_type:
        return _match_open_story(ev, by_key) or story_key_for_event(ev)
    # r6(Codex,P1):**升級不能在這裡做**。_resolve_story_key 只是「決定歸屬」,
    # 在它之後還有重播偵測、_is_real_progress、權威比較等關卡;在這裡改型別,
    # 等於讓一則**被拒收的低分級或重播事件**也悄悄把線索重新分類,
    # 進而影響之後的歸屬判斷與追蹤查詢。改到事件真的被接受之後才升級。
    return followed


def _match_open_story(ev: dict, by_key: dict) -> str:
    """在既有線索裡找出**同一條敘事**,找到回它的 key,否則回空字串。

    批#67(P1-3)。這是「敘事縱向連貫」真正的缺口:`story_key_for_event` 對
    `general` 事件把**標題 digest** 放進 key,換一個標題就開新線索。實測 1502 條
    線索有 1485 條只有 1 次更新、沒有任何一條累積到 3 個軌跡點——功能形同虛設。

    比對範圍限定**同一個主體**(無主體者彼此比),因為主體是最強的錨;
    型別不設限——線索常以 general 起頭,之後的報導才把類型講清楚,
    要求型別相同會把正確的後續報導擋在外面(這正是 r5 已經記載過的教訓)。

    多條命中時取相似度最高的那條;完全沒命中就讓它自己開一條(那是誠實的)。
    """
    ent = str(ev.get("entity") or "")
    subject = _story_subject(str(ev.get("title") or ev.get("headline") or ""),
                             ent, str(ev.get("entity_name") or ""))
    if not subject:
        return ""
    threshold = (STORY_MATCH_THRESHOLD if ent
                 else STORY_MATCH_THRESHOLD_NO_ENTITY)
    ev_period = _episodic_period(ev)
    subject_grams = _content_bigrams(subject)
    best_key, best_score = "", 0.0
    for key, story in by_key.items():
        cand_ent = str(story.get("entity") or "")
        # 批#71:**「有掛代號」與「沒掛代號」的同一則新聞必須互相比得到。**
        # 2026-07-30 實信的實害:同一篇〈聯電法說〉AI營收三年拚逾10億美元
        # 在 yahoo 與 cnyes 兩個鏡像站各開一條線索,一條 entity=2303、
        # 一條 entityless(`cluster…`)——因為原本要求 entity **完全相等**才比,
        # 兩者落在不同桶、永遠不會互相比較。聯電相關線索因此散成 26 條。
        #
        # 代號是**額外資訊**,不是衝突:一邊有、一邊沒有,仍可能是同一件事。
        # 兩邊都有卻不同才是真的衝突(不同公司)。
        # 有一邊 entityless 時採較嚴的門檻——少了代號這個錨,證據本來就較弱。
        if ent and cand_ent and cand_ent != ent:
            continue
        pair_threshold = (threshold if (ent and cand_ent)
                          else STORY_MATCH_THRESHOLD_NO_ENTITY)
        # r1(Codex,P1):**期別型事件跨期不得合併**。「公告本公司115年6月份
        # 自結合併營收」與「⋯115年7月份⋯」幾乎是同一個字串,分數遠超門檻,
        # 於是七月營收會在 `story_key_for_event` 建立新月份 key 之前就被掛回
        # 六月那條——而「不同期=不同集」正是月/季分桶存在的理由。
        #
        # 我當初刻意不加數字守衛,是為了讓「ETF 每日持股變動摘要」這類連續
        # 線索接得起來。那個判斷對**非期別型**仍然成立,所以這裡只擋期別型,
        # 也因此 general→明確型別的升級、跨月的同一樁併購案都不受影響。
        cand_period = _episodic_period_of_story(story)
        if (ev_period and cand_period
                and ev_period[0] == cand_period[0]
                and ev_period[1] != cand_period[1]):
            continue
        # r3(Codex,P2):原本一命中就 break,記下的是該線索**第一個**達標的
        # 候選文字而不是最好的——與「取相似度最高」的宣稱不符,線索之間可能
        # 因此排錯序。改為算完全部候選再取該線索的最高分。
        score = 0.0
        for cand in _story_match_candidates(story):
            # 主旨正規化時把**兩邊都知道的**代號/名稱都剝掉:一邊有掛代號、
            # 一邊沒有時,若只剝自己那一側,對照文字會殘留「聯電」而本側已被
            # 剝除,重疊率被硬生生壓低。
            cs = _story_subject(cand, cand_ent or ent,
                                str(story.get("entity_name")
                                    or ev.get("entity_name") or ""))
            if not _same_story_subject(subject, cs, pair_threshold):
                continue
            gb = _content_bigrams(cs)
            score = max(score, len(subject_grams & gb)
                        / max(1, min(len(subject_grams), len(gb))))
        if score > best_score:
            best_key, best_score = key, score
    return best_key


def _episodic_period(obj: dict) -> tuple:
    """期別型事件的 (event_type, 期別 bucket);非期別型回空 tuple。

    期別型=news_events 明確要求按季/按月隔離成獨立 episode 的那幾種
    (財報/財測按季、月營收按月)。對這幾種而言「不同期」在定義上就是
    「不同事件」,不是敘事的延續。
    """
    et = str(obj.get("event_type") or "").strip()
    if not et or (et not in _ne_module._QUARTERLY_EVENT_TYPES
                  and et not in _ne_module._MONTHLY_EVENT_TYPES):
        return ()
    bucket = _ne_module._event_period_bucket(
        obj, monthly=et in _ne_module._MONTHLY_EVENT_TYPES)
    # 與 key 內的寫法對齊(key 用 _norm 去標點:2026-06 → 202606),
    # 否則從 key 讀到的 bucket 永遠比不上從事件算出來的
    return (et, _norm(bucket)) if bucket else ()


def _episodic_period_of_story(story: dict) -> tuple:
    """線索的期別。**優先從 key 讀**,那才是權威值。

    r2(Codex,P1):我上一版從 `headline` 與 `last_published` 重算——但那兩個
    欄位會隨後續報導改變。六月營收的線索被追蹤報導接手後,headline 不再寫
    「115年6月」、`last_published` 變成七月,於是候選期別被誤算成七月;
    八月來的「115年7月營收」與它「同期」,跨期守衛因此不會擋,又併回去了。

    而 story key 本身早就永久保存了權威期別(`e:2884|l:revenue_growth|202606`)
    —— 用會漂移的欄位去重算一個已經寫死的事實,本來就是錯的方向。
    只有舊 key 沒有 bucket 時才退回推導。
    """
    key = str(story.get("key") or "")
    marker = "|l:"
    if marker in key:
        lineage = key.split(marker, 1)[1]
        if "|" in lineage:
            etype, bucket = lineage.split("|", 1)
            if bucket and (etype in _ne_module._QUARTERLY_EVENT_TYPES
                           or etype in _ne_module._MONTHLY_EVENT_TYPES):
                return (etype, bucket)
    # 舊 key(無 bucket)才後備推導。線索存的是 last_published/headline,
    # 不是 published/title —— 少了這層轉換整道守衛不會生效。
    return _episodic_period({"event_type": story.get("event_type"),
                             "published": story.get("last_published"),
                             "title": story.get("headline"), "summary": ""})


def _story_match_candidates(story: dict) -> list:
    """一條線索可用來比對的文字:目前的 headline + 軌跡點標題。

    只比 headline 不夠——線索會隨時間漂移(傳聞標題 → 公告標題),
    新的後續報導可能比較像早期的某一點而不是最新那一點。
    """
    out = [str(story.get("headline") or "")]
    out += [str(x.get("t") or "")
            for x in (story.get("timeline") or []) if isinstance(x, dict)]
    return [x for x in out if x]


def _adopt_entity_from_event(story: dict, ev: dict, key: str, by_key: dict,
                             seen_sigs: dict, today_sigs: dict,
                             touched: set) -> str:
    """entityless 線索接到有代號的事件時,把代號(與 key)一起升級。回新 key。

    為什麼一定要升級:批#71 讓 entityless 線索可以跟有代號的事件合併
    (修「同一篇文章在兩個鏡像站散成兩條」),但如果 `entity` 留空,那條線索就是
    一張**萬用牌** —— 之後另一家公司的高相似度標題進來時,衝突檢查(要求兩邊
    都有代號)不會擋,會被錯併並覆寫標題。

    為什麼要連 key 遷移:r7 的教訓 —— 只改欄位不遷移 key,之後從一般 feed
    (沒有 followup_key)進來的同一件事會算出含代號的 key,**開出第二條線索**。

    為什麼**不**順便升級型別:r6 的教訓 —— 型別改了會影響後續的
    `_is_real_progress` / 權威比較 / 追蹤查詢,那個升級刻意留在「事件通過全部
    驗收之後」。所以這裡算新 key 時**沿用線索現有的型別**,只換代號。

    目標 key 已被別條線索佔用時不升級:錯併兩條既有線索是立即且不可回復的
    (維持 r7 的取捨),殘留的萬用牌風險刻意接受。
    """
    if str(story.get("entity") or "") or not str(ev.get("entity") or ""):
        return key
    probe = {k: v for k, v in ev.items() if k != "followup_key"}
    probe["event_type"] = str(story.get("event_type")
                              or ev.get("event_type") or "general")
    new_key = story_key_for_event(probe)
    if not new_key or new_key == key or new_key in by_key:
        return key
    story["entity"] = str(ev.get("entity"))
    story["entity_name"] = str(ev.get("entity_name")
                               or story.get("entity_name") or "")
    story["key"] = new_key
    by_key[new_key] = story
    by_key.pop(key, None)
    seen_sigs[new_key] = seen_sigs.pop(key, [])
    if key in today_sigs:
        today_sigs[new_key] = today_sigs.pop(key)
    if key in touched:
        touched.discard(key)
        touched.add(new_key)
    return new_key


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
        # 大盤總結不開線索(見 is_market_wrap):它沒有單一主體,
        # 掛到任一提及的個股上會產生毫無意義的「軌跡」。
        if is_market_wrap(title, vocab):
            continue
        key = _resolve_story_key(ev, by_key)
        surprise = float(ev.get("surprise_score") or 0.0)
        story = by_key.get(key)
        if story is not None:
            # r2(Codex,P2):**代號升級必須在所有提前返回之前做。**
            # 上一版把它放在型別升級旁邊(事件通過全部驗收之後),而生產是
            # **一次**把整份 structured_events 傳進 update_ledger ——
            # 同一批裡 entityless 鏡像事件先建立線索並把 key 標成 touched,
            # 有代號的那則隨即在 `key in touched` 分支提前 continue,
            # 永遠到不了升級。線索因此一直是 entityless 萬用牌。
            # 我的測試把兩則拆成兩次呼叫,touched 重新變空 —— 又一次
            # 「驗的是我蓋的東西,不是生產送進來的東西」。
            key = _adopt_entity_from_event(
                story, ev, key, by_key, seen_sigs, today_sigs, touched)
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
                # 批#57:軌跡第一點。先前只留 last_delta/prev_delta 兩步,
                # 讀者看不到「上週說什麼」,也沒有連結可以回去讀原文。
                "timeline": [_timeline_entry(
                    ev, today,
                    _material_facts(title, str(ev.get("entity") or ""),
                                    str(ev.get("entity_name") or "").split(),
                                    str(ev.get("published") or "")))],
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
                # r1(Codex,P2):**這條分支換了 headline 卻沒換軌跡點**。
                # 同一次 update_ledger 裡同 key 的兩則事件(上游的 cluster key 含
                # direction 而 story key 不含,所以這是允許的),後一則權威到足以
                # 取代前一則時,headline/last_delta 描述的是第二則,而當天的軌跡點
                # 仍指向第一則 —— 「先成立、後取消」會顯示成自相矛盾的軌跡。
                # 批#67:同日改為可留兩點,所以這裡必須**明說是取代**——
                # 「收購案成立」與「收購案取消」字面差很遠,靠相似度猜不出來。
                _push_timeline(story, _timeline_entry(
                    ev, today,
                    _material_facts(title, str(story.get("entity") or ""),
                                    str(story.get("entity_name") or "").split(),
                                    str(ev.get("published") or ""))),
                    supersede_today=True)
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
        # r6(Codex,P1):型別升級在**事件通過所有驗收之後**才做(見
        # _resolve_story_key 的說明)。線索常以 general 起頭,被講清楚是資訊增加。
        # r7(Codex,P2):升級**必須連 key 一起遷移**。story_key_for_event 由
        # event_type 推導身分,只改型別會讓 story["key"] 停在舊的 general lineage
        # → 之後從一般 feed(沒有 followup_key)進來的同一件事會算出型別化的 key,
        # **開出第二條線索**:軌跡分裂、卡片重複、原線索還可能因此沉寂。
        # (Codex 在 F15 的建議裡就寫了 "update/migrate ... consistently",
        #  我當時只做了 update。)
        # 批#71 r1(Codex,P2):**代號也要升級,而且要走同一條遷移路徑**。
        # 批#71 讓 entityless 線索可以跟有代號的事件合併(修鏡像站散成兩條),
        # 但合併後線索的 `entity` 仍是空的 → 它變成一張**萬用牌**:之後
        # 另一家公司的高相似度標題進來時,衝突檢查(要求兩邊都有代號)不會擋,
        # 可能被錯併、覆寫標題。
        # 用既有的 `story_key_for_event` + key 遷移(r7 的教訓:只改欄位不遷移 key
        # 會讓之後從一般 feed 進來的同一件事算出不同的 key、開出第二條線索)。
        # 代號升級已在迴圈開頭的 `_adopt_entity_from_event` 處理(必須早於所有
        # 提前返回,見該函式說明);這裡只留型別升級。
        _needs_type = (str(story.get("event_type") or "general") == "general"
                       and str(ev.get("event_type") or "general") != "general")
        if _needs_type:
            _new_key = story_key_for_event(
                {k: v for k, v in ev.items() if k != "followup_key"})
            # 目標 key 已被別條線索佔用時**不升級**——合併兩條線索的風險高於
            # 維持現狀,而維持現狀只是型別偏保守,不會產生錯誤輸出。
            if _new_key and _new_key != key and _new_key not in by_key:
                story["event_type"] = str(ev.get("event_type"))
                story["key"] = _new_key
                by_key[_new_key] = story
                by_key.pop(key, None)
                seen_sigs[_new_key] = seen_sigs.pop(key, [])
                if key in today_sigs:
                    today_sigs[_new_key] = today_sigs.pop(key)
                if key in touched:
                    touched.discard(key)
                    touched.add(_new_key)
                key = _new_key
            elif _new_key == key:
                story["event_type"] = str(ev.get("event_type"))
        _push_timeline(story, _timeline_entry(
            ev, today,
            _material_facts(title, str(story.get("entity") or ""),
                            str(story.get("entity_name") or "").split(),
                            str(ev.get("published") or ""))))
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
        # 批#57:沉寂後修剪軌跡。479 條線索若每條都留六點,state 檔會膨脹一倍
        # 而且每天的 commit diff 也跟著變大;沉寂線索留頭尾兩點即可
        # (復燃時仍接得回「最初是什麼、最後停在哪」)。
        prune_timeline(story)

    out = list(by_key.values())
    # 既有帳本的清理:批#63 之前存進來的大盤總結線索仍在(實測 47/1502),
    # 其中一條還排在「線索追蹤」卡第一位。新規則只擋新增,舊的要在這裡掃掉。
    out = [s for s in out
           if not is_market_wrap(str(s.get("headline") or ""), vocab)]
    # 批#71:**軌跡點也要掃**。2026-07-30 實信的實害——
    #   [高潮] 聯電:Factset…EPS預估上修  ・已追蹤 3 次
    #     07-28 〈美股盤後〉油價大幅回落 道瓊漲逾260點…   ← 大盤總結
    #     07-29 〈美股盤後〉油價下滑 道瓊漲逾500點…       ← 大盤總結
    #     07-30 Factset 最新調查:聯電 ADR…
    # 上面那道清掃只看 `headline`,而這條線索的 headline 已經換成聯電那則
    # (乾淨),於是整條被放行、軌跡卻是兩則跟聯電無關的大盤總結。
    # 實測全帳本 23/1476 個軌跡點是這種殘留。
    _swept_empty = []
    for s in out:
        tl = [p for p in (s.get("timeline") or []) if isinstance(p, dict)]
        kept = [p for p in tl
                if not is_market_wrap(str(p.get("t") or ""), vocab)]
        if len(kept) != len(tl):
            s["timeline"] = kept
            if not kept:
                _swept_empty.append(id(s))
    # 只丟「**本次被掃空**」的線索——它整條軌跡都是大盤總結,留著就是一行沒有
    # 來歷的標題。**不能**用「timeline 為空」當條件:批#57 之前建立的線索本來
    # 就沒有 timeline 欄位(真實帳本 466/1502),那樣會把它們全部刪掉。
    # (自測抓到:第一版寫成 `if s.get("timeline") or not s.get("updates")`,
    #  一條完全正常的 e:2330|l:orders 線索直接消失。)
    if _swept_empty:
        out = [s for s in out if id(s) not in set(_swept_empty)]
    out = [s for s in out
           if _days_between(s.get("last_update") or today, today) <= KEEP_DAYS]
    out.sort(key=lambda s: (STATE_WEIGHT.get(s.get("state"), 0.0),
                            float(s.get("max_surprise") or 0.0),
                            int(s.get("updates") or 0)), reverse=True)
    return out


def active_stories(ledger: list[dict], limit: int = MAX_ACTIVE_STORIES,
                   today: str = "", states=()) -> list[dict]:
    """會進 prompt 的 story:排除沉寂者,**今日有更新的優先**。

    沉寂的**不刪除**——同一條線索日後復燃時(例如併購案重啟)還要接得回去。

    r17(Codex):版面上限是 12 條,而閒置一天的線索仍停在 peak/developing,
    權重排序會讓「今天沒動的舊高潮」擠掉「今天真的有進展的線索」——那正好違反
    R16b「沒有新進展整條不要寫」。故今日有更新者一律排在前面。
    """
    alive = [s for s in (ledger or []) if s.get("state") != "dormant"]
    # **先篩再截**(第三十輪外審 P2-2):呼叫端要的是「peak/developing
    # 的前 N 條」,而先截再篩會變成「前 N 條裡剛好是 peak/developing 的」
    # —— 實測 30 條 fresh brewing 排在前面時,追蹤查詢一條都發不出來,
    # 連帶橫向傳導(建立在縱向 followup 上)也一起歸零。
    if states:
        want = {str(x) for x in states}
        alive = [s for s in alive if str(s.get("state")) in want]

    def _rank(s):
        # 縱深第五批:名額先前只分「今天有沒有動」,同組之內按**插入順序**
        # —— 2026-08-10 實測:一次性雜訊(快艇翻覆/野火,updates=1、無實體)
        # 佔掉名額,多日有實體錨的線索(鴻海營收 upd=4)被擠出。
        # 「延燒」的本錢是追蹤紀錄與實體錨,不是誰先進帳本。
        ent = str(s.get("entity") or "")
        return ((str(s.get("last_update") or "")[:10] == str(today)[:10])
                if today else False,             # 今天有動優先(r17,不變)
                min(int(s.get("updates") or 0), 8),   # 多日 > 一次性;封頂
                bool(ent) and not ent.startswith("cluster"))  # 有實體錨
    alive.sort(key=_rank, reverse=True)
    return alive[:limit]


def _arc_steps(tl: list) -> list:
    """軌跡的取樣:第一步(起因)+ 尾端三步;被省略的段落標出步數。"""
    def _step(e):
        return {"date": str(e.get("d") or "")[:10],
                "title": str(e.get("t") or "")[:90],
                "facts": [format_fact(f) for f in (e.get("f") or [])[:2]]}
    if len(tl) <= 4:
        return [_step(e) for e in tl]
    omitted = len(tl) - 4
    steps = [_step(tl[0])] + [_step(e) for e in tl[-3:]]
    steps[0]["steps_omitted_after"] = omitted
    return steps


def story_arcs(ledger, today: str = "",
               limit: int = MAX_ACTIVE_STORIES) -> list:
    """給**特化路徑**(evidence packet)的結構化敘事弧。

    縱深第四批(2026-08-09):這個帳本先前**只餵 legacy prompt**
    (`format_story_block`)—— 同一條延燒中的線索,legacy 的信寫得出
    「上週 X → 前天 Y → 今天 Z」,特化的信只有「第 N 天」+ 昨天一句。
    故事縱深不是沒有,是沒接上。

    **選擇與 legacy 同一套**(`active_stories`):兩條路徑看到不同的
    線索集合的話,「哪條線索在燒」會依 provider 而變 —— 那不是模型的
    差異,是我們餵的差異。欄位是資料不是散文:渲染語氣交給模型,
    但狀態(醞釀/發展/高潮/收斂)由這裡算,模型不得改判。
    消毒交給 `evidence_packet.sanitize_tree` 整樹掃(這些全是字串葉節點)。
    """
    out = []
    for s in active_stories(ledger, limit, today):
        arc = {
            "entity": str(s.get("entity") or ""),
            "state": str(s.get("state") or ""),
            "state_zh": STATE_ZH.get(str(s.get("state")), "發展"),
            "first_seen": str(s.get("first_seen") or "")[:10],
            "updates": int(s.get("updates") or 1),
            # **「今日無新進展」要標出來**(與 legacy 的 r17 同一條規矩):
            # 沒標的話,今天沒動的線索看起來與有進展的一樣新,
            # 模型會照樣重述 —— 正是每日重複要消滅的東西。
            "fresh_today": bool(today) and (str(s.get("last_update") or "")[:10]
                                            == str(today)[:10]),
            "headline": str(s.get("headline") or "")[:120],
            # **軌跡是縱深的本體**:起因(第一步)→ 轉折 → 最新。
            # 上限四步(六步全給的話 payload 會被長線索吃掉),但**截斷
            # 不得丟掉起因**(外審 F2):prompt 把軌跡第一步當成起因,
            # 只取尾端的話,五、六步的線索的「第一步」其實是中途轉折 ——
            # 模型會把轉折誤寫成故事的開端。取**第一步 + 尾端三步**,
            # 中間被省略的步數標出來(讀的人才知道有跳接)。
            "trajectory": _arc_steps([x for x in (s.get("timeline") or [])
                                      if isinstance(x, dict)]),
            "prior_delta": str(s.get("prev_delta") or "")[:160],
        }
        if s.get("delta_unconfirmed") and arc["fresh_today"]:
            arc["unconfirmed_today"] = True
        out.append(arc)
    return out


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
        # 批#57:把**軌跡**餵進去,LLM 才寫得出「上週說 X → 前天 Y → 今天 Z」。
        # 先前只有 prev_delta 一步,跨週的比較根本無從寫起。
        # 數字換回中文單位(帳本存的是正規化後的純數字,直接印是一串零)。
        tl = [x for x in (s.get("timeline") or []) if isinstance(x, dict)][-4:]
        if len(tl) >= 2:
            steps = []
            for e in tl:
                facts = "・".join(format_fact(f) for f in (e.get("f") or [])[:2])
                steps.append(
                    f"{sanitize(e.get('d'), 10)} {sanitize(e.get('t'), 60)}"
                    + (f"({facts})" if facts else ""))
            lines.append("    軌跡:" + " → ".join(steps))
        prev = sanitize(s.get("prev_delta"), 160)
        if prev:
            lines.append(f"    前情:{prev}")
    return "\n".join(lines)


#: 每天最多為幾條線索發追蹤查詢。每條是一次 Google News RSS 請求,
#: 而新聞抓取本來就是 wall-clock 主導者(Google News 一組已有 65 個請求)。
#: 五條是「涵蓋當日主線」與「不拖垮 25 分鐘預算」的折衷。
FOLLOWUP_MAX_QUERIES = 5
#: 只追高潮/發展中的線索——醞釀中的還不確定是不是真的,收斂與沉寂的不需要再追。
FOLLOWUP_STATES = ("peak", "developing")
#: 這些詞當關鍵字會抓回整片雜訊,不放進查詢。
_FOLLOWUP_STOPWORDS = frozenset({
    "公司", "集團", "股份", "有限", "宣布", "表示", "指出", "傳出", "傳",
    "報導", "消息", "新聞", "今日", "昨日", "本週", "上週", "台灣", "美國",
    "中國", "全球", "市場", "業者", "相關", "可能", "預計", "分析",
})


#: event_type → 中文檢索詞。**用語意標籤而不是切標題**:
#: 中文沒有空白分詞,按固定字數硬切必然產生「Fed決」「特斯拉股」這種垃圾片段
#: (自測第一版就是這樣,實際帳本跑出「台女攀富 反覆失去」)。
#: event_type 是 Python 端已經算好的分類,拿它當第二個關鍵字既準確又可預測。
_FOLLOWUP_TOPIC = {
    "orders": "訂單",
    "earnings": "財報",
    "revenue_growth": "營收",
    "guidance_raise": "財測",
    "guidance_cut": "財測",
    "litigation": "訴訟",
    "export_controls": "出口管制",
    "geopolitical": "",          # 地緣事件沒有穩定的公司錨,只用實體
    "general": "",
}


def followup_queries(ledger: list[dict], limit: int = FOLLOWUP_MAX_QUERIES,
                     today: str = "") -> list[tuple]:
    """為追蹤中的線索組**主動查詢**。回傳 [(story_key, 查詢字串), ...]。

    批#57(使用者要求「新聞抓取的深度優化」):
    先前完全是**被動**的——線索能不能拿到後續消息,取決於它有沒有剛好出現在
    那幾十個固定 feed 裡。一條正在發展的併購案,若當天只有產業媒體報導而不在
    我們訂的來源,線索就會被判成「今日無新進展」並開始降級,最後沉寂
    ——**不是因為事情停了,是因為我們沒去找**。

    **必須有實體才發查詢**:沒有公司名/代號可以錨定的線索(cluster 型 key),
    查詢只能由標題片段組成,而中文切不出乾淨的詞——自測時實際帳本跑出
    「台女攀富 反覆失去」這種查詢,撈回來的必然是雜訊。寧可不查。
    """
    picked = []
    seen_q = set()
    # 名額給「可追蹤的那些」的前幾條(外審 P2-2:篩選要在截斷之前)。
    # 仍多取一些:實體錨抽不出來的會在下面被跳過。
    for s in active_stories(ledger, limit=limit * 6, today=today,
                            states=FOLLOWUP_STATES):
        ent = _followup_entity(s)
        if not ent:
            continue          # 無實體錨 → 不查(見 docstring)
        topic = _FOLLOWUP_TOPIC.get(str(s.get("event_type") or ""), "")
        q = f"{ent} {topic}".strip()
        if q in seen_q:
            continue          # 同公司同類型只查一次
        seen_q.add(q)
        # r1(Codex,P1):**同時回傳實體**,呼叫端才能把抓回來的文章接回這條線索。
        # r2(Codex,P1):回傳的必須是**線索 key 所用的那個實體**(通常是股票代號),
        # 不是查詢用的公司名 —— 我上一輪回傳「鴻海」,而線索的 key 是
        # `e:2317|l:orders`,抓回來的文章因此開出一條 entity=鴻海 的**新線索**,
        # 原本的缺陷完全沒解掉。查詢仍用公司名(中文標題寫的是名字),
        # 但接回去要用代號。
        # r4(Codex,P1):**同時帶顯示名**,呼叫端才驗得出「這篇文章有沒有真的提到
        # 這家公司」。改用 dict:這個結構已經因為需求換過兩次形狀
        # (2-tuple → 3-tuple),再用 tuple 只會再壞一次解包。
        picked.append({"key": str(s.get("key") or ""), "query": q,
                       "entity": str(s.get("entity") or ent),
                       "name": ent})
        if len(picked) >= limit:
            break
    return picked


def _followup_entity(story: dict) -> str:
    """線索的檢索錨:優先用公司名(中文標題寫的是名字不是代號),退回代號。"""
    name = str(story.get("entity_name") or "").split()
    for n in name:
        if len(n) >= 2 and n not in _FOLLOWUP_STOPWORDS:
            return n
    code = str(story.get("entity") or "").strip()
    if not code or code.startswith("cluster"):
        return ""
    # r6(Codex,P1):純數字代號**不發查詢**。
    # 貼標閘門(_mentions_company)刻意不採信單獨的數字——數字在財經文章裡到處
    # 都是(價格、時間、張數)。於是「用代號查、再用代號驗」必然驗不過,
    # 抓回來的東西一律接不回線索:查了等於白查,還多打一次外部請求。
    # 有名字才查;沒名字的線索走被動路徑(那是誠實的:我們沒有可靠的錨)。
    return "" if code.isdigit() else code
