"""結構化新聞事件的純規則層(A5-B5,自 morning_report.py 抽出)。

原則:函式本體自 morning_report.py 逐字搬遷、一字不改;morning_report 以同名
re-export 維持既有測試與呼叫端;驗證以 tools/refactor_audit.py verify-move 為準。
"""
from typing import Optional

from num_utils import _safe_number

from news_rules import PERIOD_TOKEN as _PERIOD_TOKEN_SHARED
from news_rules import (
    NEWS_NEGATIVE_TERMS,
    NEWS_POSITIVE_TERMS,
    _matches_any,
)
import datetime as dt
import re as _re_module

import subject_identity as _si


def _news_event_direction(text: str) -> int:
    """用明確事件詞判斷消息方向；同時有多空詞或沒有方向時不加分。"""
    positive = bool(_matches_any(text, NEWS_POSITIVE_TERMS))
    negative = bool(_matches_any(text, NEWS_NEGATIVE_TERMS))
    if positive == negative:
        return 0
    return 1 if positive else -1

def _cyber_tokens() -> tuple:
    """資安事件的詞彙**取自身分層的宣告**(`event_actions` 的 `cyberattack`)。

    兩層 taxonomy 曾經自己互相矛盾:身分層判 `cyberattack`、型別層判
    `geopolitical`。詞彙表只有一份,加一個詞只要改宣告那一處。
    載不到就回空 tuple —— 那時退回舊行為(仍會被 geopolitical 收走),
    是誠實的降級:不會因為 import 失敗就把整條規則靜默關掉又假裝有。
    """
    try:
        import event_actions as _ea
        for row in _ea.ACTION_TABLE:
            if row and row[0] == "cyberattack":
                return tuple(row[2:])
    except Exception:                   # noqa: BLE001 - 宣告載不到就不判
        pass
    return ()


def _event_type(text: str) -> str:
    """Map noisy headlines to a small, learnable event taxonomy."""
    # 英文 token 需列出複數/變體:word boundary 下 "order" 不再命中 "orders"
    # (舊 substring 靠副作用吃到複數,改法時一併補齊)
    rules = (
        ("guidance_raise", ("raises guidance", "raise guidance", "上修財測", "調高財測")),
        ("guidance_cut", ("cuts guidance", "cut guidance", "下修財測", "調降財測")),
        ("orders", ("order", "orders", "訂單", "接單", "合約", "contract", "contracts")),
        ("earnings", ("earnings", "eps", "財報", "獲利", "盈餘")),
        ("revenue_growth", ("revenue", "revenues", "營收", "sales growth")),
        ("export_controls", ("export control", "export controls", "出口管制",
                             "制裁", "sanction", "sanctions", "sanctioned",
                             "sanctioning")),
        ("litigation", ("lawsuit", "lawsuits", "litigation", "訴訟", "裁罰")),
        # **資安攻擊不是地緣攻擊**(repo-wide 外審 2026-08-18,P2-1)。
        # 這一條**必須排在 geopolitical 前面**:那一條收「attack / 攻擊」,
        # 於是任何公司的資安新聞都變成地緣政治事件。生產實例:
        #   `entity=AAPL、event_type=geopolitical、surprise=0.90`
        #   ← 「Apple 發出間諜軟體威脅通知 用戶恐成攻擊目標」
        #   ← 「博通單日跌6%,駭客攻擊VMware讓市場重新定價軟體風險」
        # 而 geopolitical 拿 0.90 的意外度(prompt 明說 ≥0.6 要優先且醒目)
        # 與催化評分的地緣權重 —— 那不是標籤好不好看的問題,是真的改了
        # 優先順序與影響量級。
        #
        # **詞彙表不在這裡重寫**:身分層早就宣告過同一組詞
        # (`event_actions` 的 `cyberattack` 動作),兩份會分歧,而分歧的
        # 症狀正是這次的缺陷 —— 一層說 cyberattack、另一層說 geopolitical。
        ("cybersecurity", _cyber_tokens()),
        ("geopolitical", ("war", "missile", "missiles", "attack", "attacks",
                          "attacked", "attacking", "戰爭", "飛彈", "攻擊")),
    )
    # 動詞變形也要列(sanctioned/attacked 等,Codex review):刻意不加語意含混的
    # ordered(court ordered)/contracted(economy contracted)——word boundary 的
    # 目的就是擋這類誤中,寧可少收
    # 統一走 _matches_any(英文 word boundary、中文 substring):
    # 舊 substring 比對會讓 award 誤中 war、steps 誤中 eps、disorder 誤中 order
    # (GPT-5.6 二審 P1)。lower 化由 _matches_any 內部處理。
    for event_type, tokens in rules:
        if _matches_any(text or "", list(tokens)):
            return event_type
    return "general"

def normalize_event_type(event_type: str, text: str) -> str:
    """把**上游給的**型別也帶回同一條規則(2026-08-18 外審 P2-1)。

    `event_type` 可能來自 LLM 抽取器,而它照樣會把資安事件寫成
    `geopolitical`。只修確定性推導的話,錯誤分類每天會從另一條路進來,
    而 state 清理會與它每天打架:清掉、隔天又寫回來,延燒天數永遠是 1。

    **只改這一種**(geopolitical → cybersecurity,而且標題要命中宣告過的
    資安詞彙)—— 不是拿確定性推導覆寫模型的所有判斷:那會把模型讀懂
    上下文的能力整個丟掉。
    """
    et = str(event_type or "")
    if et == "geopolitical" and _matches_any(text or "", list(_cyber_tokens())):
        return "cybersecurity"
    return et


def _freshness_weight(age_hours: float) -> float:
    """Fresh events matter most; old duplicates fade quickly."""
    if age_hours <= 12:
        return 1.0
    if age_hours <= 24:
        return 0.75
    if age_hours <= 48:
        return 0.45
    return 0.20

def _event_cluster_key(event: dict) -> tuple:
    # 跑內跨來源聚合鍵:有 entity 的型別事件抹掉標題(不同媒體同一事件標題不同,
    # 靠 entity+type+direction 聚合);entityless 型別事件必須保留標題指紋——
    # 否則同型別+同方向的所有無主體事件(常見:地緣)全撞成一鍵,互相吞併
    # (GPT-5.6 三審 P0:實際 state 中同鍵重複 29 次)。
    import re as _re
    title = _re.sub(r"\W+", "", str(event.get("title") or "").lower())[:48]
    entity = str(event.get("entity") or "")
    if event.get("event_type") != "general" and entity:
        title = ""
    return (
        entity,
        str(event.get("event_type") or "general"),
        int(_safe_number(event.get("direction"))),
        title,
    )

def _event_bucket_key(event: dict) -> tuple:
    """跑內聚合的**粗桶**:主體 + 型別 + 方向,不含標題。

    批#64:舊的 `_event_cluster_key` 直接把桶當成聚合鍵——有主體的型別事件
    標題被整個抹掉,於是同一天同一家公司同型別同方向的**所有**事件塌成一則,
    輸的那則靜默消失,活下來的那則還會宣稱自己有多來源交叉驗證。

    **實測**:兩則不同的台積電訂單新聞合併成 1 則、`corroboration_count=2`;
    真實 state 裡 2884 同桶並存的兩個標題是「115年6月自結盈餘」與
    「董事會決議增資發行新股」——**兩件毫不相干的公告**,只是碰巧同桶。

    改法:桶只負責粗分,是否真的是同一件事交給 `same_event_title` 判斷。
    """
    return (
        str(event.get("entity") or ""),
        str(event.get("event_type") or "general"),
        int(_safe_number(event.get("direction"))),
    )


#: 標題重疊率門檻。**由真實語料校準**,不是憑感覺挑的:
#: 143 次執行的歷史事件裡,跨來源重複幾乎全是**標題完全相同**的轉載
#: (93 則無主體多來源事件都是靠標題完全相等才合併的),不是改寫。
#: 而語料中重疊率落在 0.86~0.92 的配對——富邦金「總經理選任」vs「董事長選任」、
#: 威力彩第 115050 期 vs 第 115051 期——**都是不同事件**。
#: 所以門檻必須高:0.9 搭配數字守衛與「較長標題當分母」,對語料中
#: 七組邊界案例(含包含關係)全部判對。
_MERGE_OVERLAP = 0.9
#: 少於這個 bigram 數的標題不套重疊率(太短時任何兩則都容易高分),改要求完全相同
_MERGE_MIN_GRAMS = 4


def _content_bigrams(title: str) -> set:
    s = _re_module.sub(r"[\W_]+", "", str(title or "").lower())
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _digit_signature(title: str) -> tuple:
    """標題裡的數字串集合。期別/月份/季別/金額不同 → 一定是不同事件。"""
    return tuple(sorted(_re_module.findall(r"\d+", str(title or ""))))


def same_event_title(a: str, b: str) -> bool:
    """兩個標題是否在講**同一件事**(供跑內跨來源去重使用)。

    數字守衛先行:「威力彩第115050期」與「第115051期」字元重疊率高達 0.92,
    但期別不同就是兩次開獎。任何數字集合不同的配對一律不合併。
    """
    if _digit_signature(a) != _digit_signature(b):
        return False
    ga, gb = _content_bigrams(a), _content_bigrams(b)
    if not ga or not gb:
        return False
    if min(len(ga), len(gb)) < _MERGE_MIN_GRAMS:
        return ga == gb
    # 分母取**較長**的那一邊。r1(Codex,P2):原本除以較短的一邊(overlap
    # coefficient),於是「A 完全包含於 B」會得到 1.0 —— 實測
    # 「公告本公司董事會決議增資發行新股」與「⋯之資金用途變更」重疊率 1.000,
    # 兩件不同的公告會被併掉一件。MOPS 標題共用大量制式前綴,這種包含關係
    # 並不罕見。改用較長的一邊當分母後同一組降到 0.682。
    #
    # 代價講明白:短標題被長標題吸收的情況現在會**留成兩則**
    # (「台積電法說會」vs「台積電法說會登場」→ 0.714,不合併)。
    # 這個方向是刻意的 —— 多留一則重複的代價,遠小於靜默消滅一則真事件
    # 再幫倖存者偽造交叉驗證。
    return len(ga & gb) / max(len(ga), len(gb)) >= _MERGE_OVERLAP


def _event_surprise_score(event: dict) -> float:
    """Estimate how much genuinely new information an event carries."""
    explicit = event.get("surprise_score")
    if explicit is not None:
        return round(max(0.1, min(1.0, _safe_number(explicit, 0.5))), 3)
    text = f"{event.get('title', '')} {event.get('summary', '')}".lower()
    if any(token in text for token in (
            "unexpected", "surprise", "beats estimates", "misses estimates",
            "優於預期", "低於預期", "意外", "突發", "緊急")):
        return 0.95
    if any(token in text for token in (
            "as expected", "in line with", "符合預期", "市場預期", "早已預期")):
        return 0.25
    return {
        "guidance_raise": 0.90, "guidance_cut": 0.90, "orders": 0.70,
        "earnings": 0.60, "revenue_growth": 0.50, "export_controls": 0.85,
        "litigation": 0.75, "geopolitical": 0.90, "general": 0.35,
        # 公司資安事件:重大(仍在 prompt 的 ≥0.6 優先門檻內),
        # 但不是地緣等級 —— 先前它借用 geopolitical 的 0.90。
        "cybersecurity": 0.70,
    }.get(str(event.get("event_type")), 0.35)

def _event_lifecycle(event: dict) -> str:
    """Classify event progression so repeated coverage does not repeatedly add score."""
    explicit = str(event.get("lifecycle") or event.get("status") or "").lower()
    text = f"{explicit} {event.get('title', '')} {event.get('summary', '')}".lower()
    # r10(Codex,P1):**explicit lifecycle 與明確否決結論必須排在最前面**。
    # 先前 implemented 的關鍵字(生效/實施/effective)排在否決判定之前,於是:
    #   「主管機關駁回已生效許可之展延申請」→ implemented(實測)
    #   explicit lifecycle="rejected" 但標題含「生效」→ 也是 implemented
    # 明確的否決被改判成已實施,拿到錯誤的 transition 與權重,並把錯誤 lifecycle
    # 寫進 ledger/state。我上一輪的註解宣稱「先判否決」,實際上只排在 confirmed
    # 之前,**沒排在 withdrawn/implemented 之前**——順序只對了一半。
    if explicit in ("rejected", "denied", "declined", "否決", "駁回"):
        return "rejected"
    if explicit in ("withdrawn", "撤回", "cancelled", "canceled"):
        return "withdrawn"
    if any(token in text for token in (
            "withdrawn", "withdraw", "cancelled", "canceled", "撤回", "取消", "暫緩")):
        return "withdrawn"
    # 明確否決結論優先於泛用的 implemented 關鍵字
    if any(tok in text for tok in ("不予核准", "不予備查", "不予通過")):
        return "rejected"
    for _tok in ("否決", "駁回", "退回", "rejected", "reject",
                 "denied", "deny", "declined"):
        _i = text.find(_tok)
        while _i >= 0:
            if not is_negated_decision(text, _i):
                return "rejected"
            _i = text.find(_tok, _i + 1)
    if any(token in text for token in (
            "implemented", "effective", "takes effect", "上路", "生效", "實施")):
        return "implemented"
    # r2(七維度審查,P1)+ r4(Codex,P1):否定守衛。
    #
    # r4 指出我 r2 的作法有**兩個漏洞**,兩個都實測重現:
    # (a)「公告本公司董事會未通過收購案」——「公告」本身沒被否定,迴圈一命中
    #     就立刻回 confirmed,內文的「未通過」根本沒機會被看到。
    #     官方公告的標題**幾乎都以「公告」開頭**,所以這不是邊角情況。
    # (b) 就算標題沒有「公告」,迴圈跳過「未通過」後仍會落到最後一行的
    #     A 級來源 fallback → 一樣回 confirmed。而 MOPS 正是 A 級。
    # 兩者的後果相同:既有 confirmed lineage 拿到 is_incremental=False、權重歸零,
    # 這條修正要救的「官方相反結論」還是被壓掉。
    #
    # 正確順序:**先判定有沒有被否定的決策結果**,有就直接回非 confirmed,
    # 再套用一般公告/A 級 fallback。「公告」是文件類型,不是結論。
    # r6(Codex,P1):我 r5 只從「未通過/未核准」這種**否定形式**推導 rejected,
    # 完全沒匹配**直接否決措辭**——實測「董事會否決收購案」「主管機關駁回申請」
    # 都被判成 confirmed(落到 A 級 fallback),explicit lifecycle="rejected"
    # 也一樣。而「否決」「駁回」正是官方公告最常見的寫法。
    # 我的測試只用了「未通過」,所以整個直接否決的類別沒被覆蓋到。
    # (r7 的否決守衛與 explicit 判定已由 r10 前移到 withdrawn/implemented 之前,
    #  見本函式開頭。保留在那裡的理由:明確的否決結論不得被泛用的「生效/實施」
    #  關鍵字蓋掉。r7 的原始理由——否決詞本身也必須過否定守衛,免得
    #  「收購案未遭否決」被判成 rejected 並拿最高權重——一併搬過去。)

    _DECISION_TOKENS = ("通過", "核准", "核定", "同意", "批准",
                        "approved", "approve", "confirmed")
    for token in _DECISION_TOKENS:
        i = text.find(token)
        while i >= 0:
            # r8(Codex,P1):**pending 必須先判,不能巢狀在 negated 底下**。
            # 我上一輪把它放進 `if is_negated_decision(...)` 裡,於是「尚待」
            # 這種**不含任何否定詞**的待決標記永遠到不了——實測
            # 「本案尚待董事會核准」落到 A 級 fallback 判成 confirmed,
            # 可能造成假的 rumor → confirmed 轉移並拿到權重;
            # 而 story_ledger 直接呼叫 pending 判定,給的是 pending_*
            # ——**又是同一句話兩種結論**。
            # 「尚未核准」是流程還在跑,不是被否決;判成 rejected 會讓
            # 「後來核准了」看起來像翻案而非正常進度。
            if is_pending_decision(text, i):
                return "rumor"
            if is_negated_decision(text, i):
                return "rejected"      # 明確的否決結論,不得再被 fallback 蓋掉
            i = text.find(token, i + 1)

    _CONFIRM_TOKENS = ("confirmed", "announced", "approved",
                       "公告", "核定", "通過", "證實")
    for token in _CONFIRM_TOKENS:
        i = text.find(token)
        while i >= 0:
            if not is_negated_decision(text, i):
                return "confirmed"
            i = text.find(token, i + 1)
    if any(token in text for token in (
            "rumor", "reportedly", "may", "considering", "傳聞", "擬", "可能", "研議")):
        return "rumor"
    return "confirmed" if event.get("source_grade") == "A" else "rumor"

# 天生按「集數」發生的事件型別:同 entity+type 不同期是不同 episode。
# 舊鍵 (entity, type) 會把台積電 Q1/Q2/明年 Q1 財報全撞成同一事件,第二季起
# lifecycle 增量被誤判為「無進展」而權重歸零(GPT-5.6 二審 P0)。
# 財報/財測=季頻;營收=**月頻**(台股月營收每月公布,若用季 bucket,同季第
# 二、三個月的營收事件會被吃成 0 權重——Codex review)。
_QUARTERLY_EVENT_TYPES = frozenset({"earnings", "guidance_raise", "guidance_cut"})
_MONTHLY_EVENT_TYPES = frozenset({"revenue_growth"})


#: 標題裡的會計期間。民國年(115年6月)與西元年(2026年6月)都收。
_FISCAL_MONTH_RE = _re_module.compile(
    r"(?:民國)?(\d{3,4})\s*年\s*(\d{1,2})\s*月")
_FISCAL_QUARTER_RE = _re_module.compile(
    r"(?:(?:民國)?(\d{3,4})\s*年[^0-9]{0,6})?第\s*([一二三四1-4])\s*季"
    # r1(Codex,P1):Q 格式原本只吃「緊鄰 Q 的四位西元年」,而「2025年Q4財報」
    # 與「民國114年Q4」中間隔著「年」,於是年份抓不到 → 退用發布年,
    # 2026 年初公布的 2025Q4 財報會被錯掛成 2026Q4 —— 而那是**未來期別**,
    # 仍落在 ±18 個月的合理性守衛內,不會被擋下。
    r"|(?:(?:民國)?(\d{3,4})\s*年?\s*)?[Qq]([1-4])(?![0-9])")
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4}


def _norm_year(raw) -> int:
    """3 位數視為民國年(115 → 2026),4 位數視為西元年。"""
    try:
        y = int(raw)
    except (TypeError, ValueError):
        return 0
    return y + 1911 if 100 <= y <= 199 else (y if 1990 <= y <= 2100 else 0)


def _fiscal_period_from_text(text: str, ref: dt.datetime, monthly: bool) -> str:
    """從標題/摘要解析**會計期間**。解析不到或不合理回空字串。

    批#67(P1-2):原本期別 bucket 直接取 `published` —— 那是**新聞發布時間**,
    不是報表所屬期間。台股月營收固定在次月 10 日前公告,所以「115年6月營收」
    永遠被掛到 `2026-07`;季報同理(Q1 財報四月公布 → 掛 2026Q2)。
    整條序列的期別標籤系統性偏一期,而同一期別的**更正公告**跨月出現時,
    還會被切成兩個 episode。

    `ref` = published,用途有二:①標題只寫「第二季」沒寫年份時補年份;
    ②合理性守衛 —— 解析結果距離 published 超過 18 個月就不採信
    (標題裡提到別的年份、或根本是誤判)。
    """
    t = str(text or "")
    if not t:
        return ""
    if monthly:
        m = _FISCAL_MONTH_RE.search(t)
        if not m:
            return ""
        year, mon = _norm_year(m.group(1)), int(m.group(2))
        if not year or not 1 <= mon <= 12:
            return ""
        got = dt.date(year, mon, 1)
        bucket = f"{year}-{mon:02d}"
    else:
        m = _FISCAL_QUARTER_RE.search(t)
        if not m:
            return ""
        raw_y = m.group(1) or m.group(3)
        raw_q = m.group(2) or m.group(4)
        q = _CN_DIGITS.get(str(raw_q), 0) or (
            int(raw_q) if str(raw_q).isdigit() else 0)
        year = _norm_year(raw_y) if raw_y else ref.year
        if not year or not 1 <= q <= 4:
            return ""
        got = dt.date(year, (q - 1) * 3 + 1, 1)
        bucket = f"{year}Q{q}"
    months_off = (ref.year - got.year) * 12 + (ref.month - got.month)
    return bucket if -18 <= months_off <= 18 else ""


def _event_period_bucket(event: dict, monthly: bool) -> str:
    """事件的期別 bucket(月頻 YYYY-MM / 季頻 YYYYQn)。

    優先採標題/摘要裡寫明的**會計期間**;解析不到才退回 published
    (那是發布時間,對月營收與季報系統性偏一期,只能當後備)。
    無法解析回空(退回無 bucket 舊鍵)。
    """
    raw = str(event.get("published") or "").strip()
    try:
        d = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    stated = _fiscal_period_from_text(
        f"{event.get('title') or ''} {event.get('summary') or ''}", d, monthly)
    if stated:
        return stated
    if monthly:
        return f"{d.year}-{d.month:02d}"
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


_SUBJECT_SEP = _re_module.compile(r"\s+[-|｜–—]\s+|\s*\|\s*")
_SUBJECT_BOILERPLATE = ("股市爆料同學會", "提供者", "作者", "討論牆",
                        "盤中速報", "產業即時新聞", "Investing.com", "CMoney",
                        # 批#80 r3:「鉅亨速報 - Factset 最新調查:X(代號)EPS 預估
                        # 上修至…」是**逐檔量產的樣板**,帳本裡就有 9 條。
                        # 樣板本身佔掉主旨的大半,不同公司之間重疊度衝到
                        # 0.65~0.77(門檻 0.45/0.65),於是「利西亞車行 LAD-US」
                        # 與「穎崴 6515-TW」會被判成同一條敘事。
                        "鉅亨速報")
#: 太常見、不具辨識力的英文詞。留著會讓「不同子公司」看起來像同一個,
#: 也會讓兩件不同的事看起來像同一件。
#:
#: 第十輪 P2-4:這個名字原本在本檔**定義兩次**(公司字尾那組、縮寫那組),
#: 後者靜默勝出 —— 於是第一個消費端(`_SUBJECT_LATIN`,線索主體比對)
#: 失去了 `LIMITED` 與 `CORPORATION`,公司字尾會被當成有辨識力的 token。
#: 合併成**聯集**,兩個消費端的原意都保留。
#: (我批#92 加的「重複頂層定義」守衛只查 def/class,不查模組層賦值 ——
#:  守衛自己有洞,所以這個檔一直通過。已一併補上。)
_SUBJECT_LATIN_STOP = {
    # 公司字尾
    "LIMITED", "LTD", "INC", "CORP", "CORPORATION", "CO",
    # 常見虛詞
    "THE", "AND", "FOR", "NEW",
    # 常見縮寫
    "AI", "ETF", "US", "CEO", "CFO", "IPO", "EPS", "USD", "TWD",
    "GDP", "CPI", "PCE", "ADR", "OEM", "ODM", "IDC", "API", "NEWS",
}
_SUBJECT_LATIN = _re_module.compile(r"[A-Za-z]{2,}")
#: 句尾的來源標註(分隔符不是 " - ",剝段落剝不到)
_SUBJECT_CREDIT = _re_module.compile(r"\s*(?:提供者|作者|編譯|記者)\s*\S{0,12}\s*$")
#: 有主體時可以放寬(主體本身已經是很強的錨);無主體時沒有錨,必須保守。
#: 兩個門檻都由 1502 條真實線索校準,見 `_same_story_subject`。
STORY_MATCH_THRESHOLD = 0.45
STORY_MATCH_THRESHOLD_NO_ENTITY = 0.65
#: 共同 bigram 少於這個數就不算,免得極短的通用主旨(「營收公布」)四處攀親
STORY_MATCH_MIN_SHARED = 5




def strip_outlet_suffix(title: str) -> str:
    """剝掉 Google News 標題尾端的媒體名/欄目/來源標註。

    r1(Codex,P1):批#72 第一版的對象指紋直接吃原始標題,於是
    「台積電獲蘋果2奈米大單 - CNBC」與「⋯ - DIGITIMES」拿到不同指紋
    (`2奈米,cnbc,蘋果` vs `2奈米,digitimes,蘋果`)→ 同一訂單在不同媒體
    或隔日轉載會變成不同 `event_id`,跨月 rumor→confirmed 又接不起來。

    **這套剝除規則 repo 早就有**(原本寫在 story_ledger 的 `_story_subject`
    裡,含 24 字上限與「提供者 X」句尾標註的處理,都是實測校準過的)。
    我沒有重用它就是重複造輪子 —— 下移到這一層,story_ledger 改為匯入,
    兩邊共用同一份判準。
    """
    parts = [x.strip() for x in _SUBJECT_SEP.split(str(title or "")) if x.strip()]
    for _ in range(2):
        if len(parts) > 1 and len(parts[-1]) <= 24 and not any(
                c.isdigit() for c in parts[-1]):
            parts.pop()
    out = " ".join(parts)
    out = _SUBJECT_CREDIT.sub("", out)
    for b in _SUBJECT_BOILERPLATE:
        out = out.replace(b, "")
    return out.strip()


_SUBJECT_LATIN_RE = _re_module.compile(r"[A-Za-z][A-Za-z0-9]{2,}")
#: 製程節點/規格(2奈米、3nm、HBM3E):它們是事件的**對象**,鑑別力很高
_SUBJECT_SPEC_RE = _re_module.compile(r"\d+\s*(?:奈米|nm|吋|GW|MW)", _re_module.I)

#: 規格單位的中英對照(Event Identity v4)。`2奈米` 與 `2nm` 是同一件事,
#: 但它們原本是兩個不同的 token —— 於是「台積電獲蘋果2奈米大單」與
#: 「TSMC wins Apple 2nm order」的主體指紋不同,同一筆訂單被當成兩個事件。
_SPEC_UNIT_CANON = {"奈米": "nm", "吋": "in"}


def _canon_spec(text: str) -> str:
    """規格 token 正規化:去空白、單位轉成英文、統一小寫。"""
    out = _re_module.sub(r"\s+", "", str(text or "")).lower()
    for zh, en in _SPEC_UNIT_CANON.items():
        out = out.replace(zh, en)
    return out


def _alias_to_code(known_names) -> dict:
    """`{別名 token: 代號}`。**一個別名對到多個代號時視為歧義,不收。**

    Event Identity v4:主體指紋原本吐出「比對到的別名字串」,所以
    `蘋果` 與 `Apple` 是兩個不同的 token。改成吐出**代號**之後,
    同一家公司不論用哪個名字寫都會收斂到同一個指紋。

    歧義的例子是真實存在的(「中信」既是中信金也是中信兄弟),
    而猜錯的代價是把兩家公司的事件合併 —— 那比不合併嚴重得多,
    所以歧義時回退成原本的別名行為(誠實的降級)。
    """
    hits: dict = {}
    if not hasattr(known_names, "items"):
        return {}
    for code, raw in known_names.items():
        code = str(code or "").strip()
        if not code:
            continue
        for alias in ((raw,) if isinstance(raw, str) else (raw or ())):
            for piece in str(alias or "").split():
                if len(piece) >= 2:
                    hits.setdefault(piece.lower(), set()).add(code.lower())
    return {k: next(iter(v)) for k, v in hits.items() if len(v) == 1}


#: **「這則新聞在講誰」與「這則新聞跟誰有關」是兩件事**
#: (repo-wide 外審 2026-08-18,P1-1)。
#:
#: 生產狀態被污染的實例(都是 Python 確定性層寫進去的,不是模型幻覺):
#:   * `e:2454|l:geopolitical` ← 「黃金終於鬆開手煞車!8月大漲9%重拾避險光環」
#:   * `e:2890|l:earnings`     ← 「【公告】勝悅-KY 第2季合併財報董事會日期」
#:   * `e:3231|l:earnings`     ← 「緯穎飆出6740元歷史新天價」
#: 成因是同一個:編輯標註的**相關**個股(鉅亨 `stock` 欄位)、以及
#: **發起查詢的那個代號**,都被直接寫進 `entity`,而 `entity` 是
#: story key / timeline / 催化評分 / model history 的身分。
#:
#: 這裡是唯一的判準:**主體要在新聞自己的文字裡出現**。
#: 出現的方式只有兩種,兩種都是可核對的事實:
#:   1. **括號裡的代號**(`緯創(3231)`、`【2330】`)—— 裸數字不算,
#:      「大盤大漲 2454 點」的 2454 是點數不是聯發科;
#:   2. 宣告過的公司別名(`known_names` = `{代號: (別名, …)}`,
#:      那張表是人維護的,不是從新聞猜的)。
#: **兩者都對不上就沒有主體**(回空字串)—— 那是誠實的降級:
#: 市場級/總經事件本來就沒有公司主體,帳本對這種事件另有 cluster 鍵。
#:
#: 為什麼不是「再加一個關鍵字守衛」:這個判準**取代**了「誰可以當 entity」
#: 這件事本身,而不是在某一個入口多擋一次。相關個股改放 `related_tickers`,
#: 發起查詢的代號改放 `query_origin` —— 三個概念不再共用一個欄位。
_SUBJECT_CODE_BOUNDARY = _re_module.compile(r"[0-9A-Za-z]")


def mentions_entity(text: str, code: str, known_names=None) -> str:
    """這段文字有沒有指名這個實體。回 `"code"` / `"alias"` / `""`。

    **回傳的是依據,不是布林** —— 下游要記得住「憑什麼說它是主體」,
    而「用代號認出來的」與「用別名認出來的」在出錯時要分得開。
    """
    hay = str(text or "")
    code = str(code or "").strip()
    if not hay or not code:
        return ""
    # 代號:台股是純數字,**裸數字一律不算**。「大盤大漲 2454 點」裡的 2454
    # 是點數不是聯發科,而「左右不是英數字」這種邊界檢查放它過(自測抓到)。
    # 台股新聞寫代號的慣例是括號:「緯創(3231)」「(6669)」「【2330】」——
    # 要求括號相鄰,數字巧合就進不來。沒寫括號的那些,公司名會由別名那一關
    # 認出來(「2330 台積電法說」有「台積電」),所以不會因此漏掉真的主體。
    for br_open, br_close in (("(", ")"), ("（", "）"), ("[", "]"),
                              ("【", "】"), ("〔", "〕")):
        if f"{br_open}{code}{br_close}" in hay:
            return "code"
    # **別名是一個整體,不是幾個可以各自比對的字**(外審 2026-08-18 P1-3)。
    # 第一版對每個別名再 `.split()` 然後做無邊界子字串比對,於是:
    #   `Hon Hai` → `Hon`  → 「iPhone demand…」命中鴻海
    #   `Arm`             → 「pharmaceutical」命中安謀
    #   `Applied Materials` → `materials` → 任何講材料的新聞命中應用材料
    # 那正好把這次要關掉的路徑重新打開(查詢代號又被升格成已驗證主體)。
    #
    # 純拉丁字母的別名要**詞邊界**(左右不是英數字);中文沒有詞邊界,
    # 用子字串,但長度至少兩個字(一個字的別名會命中任何句子)。
    low = hay.lower()
    for alias in ((known_names or {}).get(code) or ()):
        a = str(alias or "").strip()
        if len(a) < 2:
            continue
        if _LATIN_ALIAS.fullmatch(a):
            if _latin_alias_hit(low, a.lower()):
                return "alias"
        elif a.lower() in low:
            return "alias"
    return ""


#: 純拉丁別名(含空白與少數連接符號):`Hon Hai`、`Applied Materials`、`Arm`。
_LATIN_ALIAS = _re_module.compile(r"[A-Za-z0-9][A-Za-z0-9 .&'-]*")


def _latin_alias_hit(low_text: str, alias: str) -> bool:
    """拉丁別名要落在詞邊界上 —— `Arm` 不得命中 `pharmaceutical`。"""
    i = low_text.find(alias)
    while i >= 0:
        before = low_text[i - 1] if i > 0 else " "
        j = i + len(alias)
        after = low_text[j] if j < len(low_text) else " "
        # **邊界只看 ASCII 英數字**(外審 2026-08-18 第三輪):中文字的
        # `isalnum()` 也是 True,於是「Arm架構需求升溫」「Apple發表新晶片」
        # 會被當成別名落在單字內而拒絕 —— 合法主體反而命不中。
        if not (_SUBJECT_CODE_BOUNDARY.match(before)
                or _SUBJECT_CODE_BOUNDARY.match(after)):
            return True
        i = low_text.find(alias, i + 1)
    return False


#: 語意實體的表與判準已**上收到 `subject_identity`**(repo-wide 外審
#: 2026-08-20 P1-2:三套 canonical 權威在生產互打 —— 同一班 migration 把
#: `UAE|阿聯控伊朗…` 當不認得刪掉,producer 對同一則新聞卻判 `阿聯`
#: literal 保留)。canonical 從英文改採**中文顯示名**(與 event_actions
#: 法域表及既有 state 一致 —— Russia 的續報才接得回
#: `geopolitical:俄羅斯:…` 的舊鍵)。這裡只留薄轉接,**別再往這裡加表**。
SEMANTIC_ENTITY_ALIASES = None  # 已上收;留名擋「有人照舊 import 表本體」


def semantic_canonical(name: str) -> str:
    """這個名字有沒有宣告過的跨語言身分 → canonical 顯示名(認不得回空)。"""
    return _si.canonical_display(name) if _si.aliases_of(name) else ""


def _alias_hit(low_text: str, alias: str) -> bool:
    """單一別名的比對(與 mentions_entity 的別名規則一致)。"""
    a = str(alias or "").strip()
    if len(a) < 2:
        return False
    if _LATIN_ALIAS.fullmatch(a):
        return _latin_alias_hit(low_text, a.lower())
    return a.lower() in low_text


def resolve_subject(text: str, candidates, known_names=None) -> tuple:
    """依序試每個候選,回 `(主體, 依據)`;**沒有一個被文字證實就回 `("", "")`**。

    候選的順序由呼叫端決定(它知道哪個是模型宣告、哪個是編輯標註、
    哪個只是發起查詢的代號)—— 這裡只負責「文字有沒有指名它」。

    **傳進來的文字要與事後查得到的那份一致**(外審 2026-08-18 P1-1):
    帳本只存標題,所以生產者也只拿標題來驗 —— 用「標題+摘要」驗、卻用
    標題清理,會讓清理把生產者昨天建立的**合法** state 刪掉。
    語意上這也是對的:只在內文被提到的公司是**相關**,不是這則的主體。
    """
    # **候選順序是呼叫端的信任編碼**(r5:模型宣告的主體排最前,編輯
    # 標註/查詢代號在後)—— 不得整體重排。「中國」吃掉中國信託的問題
    # 改用**內嵌讓位**解:語意別名的命中若內嵌在更長的公司別名裡、而那個
    # 公司別名就在文字裡,命中的其實是那家公司 —— 這個候選讓位,由後面
    # 的公司候選(或誰都沒有)接手。
    for c in candidates or ():
        c = str(c or "").strip()
        if not c:
            continue
        basis = mentions_entity(text, c, known_names)
        if basis:
            return c, basis
        if not ((known_names or {}).get(c)):
            # 宣告過的語意實體:任一語言的別名出現即指名(P2-1 跨語言
            # 續報不斷線);回 canonical 鍵,中英寫法收斂成同一條線。
            canon = semantic_canonical(c)
            if canon:
                low = str(text or "").lower()
                hit = next((a for a in _si.aliases_of(canon)
                            if _alias_hit(low, a)), "")
                if hit and not _embedded_in_company_alias(low, hit,
                                                          known_names):
                    return canon, "alias"
                continue
            if _literal_mention(text, c):
                return c, "literal"
    return "", ""


def _embedded_in_company_alias(low_text: str, hit: str, known_names) -> bool:
    """這個語意別名的**每一次出現**是不是都內嵌在更長的公司別名裡。

    「中國」in「中國信託」而「中國信託」就在文字裡 → 命中的是那家銀行,
    不是國家(r4 F3 / r5 收斂解)。r6:**要比出現位置,不是存在性** ——
    「中國宣布新政策,中國信託獲利創高」裡有一個**獨立的**「中國」,
    只看「文字裡有沒有中國信託」會把合法的國家主體一併壓掉。
    只有當語意別名的每次出現都落在某個公司別名的區間內,才算內嵌。
    """
    h = str(hit or "").lower()
    if not h:
        return False
    spans = []
    for aliases in (known_names or {}).values():
        for a in (aliases or ()):
            al = str(a or "").lower()
            if len(al) <= len(h) or h not in al:
                continue
            start = 0
            while True:
                i = low_text.find(al, start)
                if i < 0:
                    break
                spans.append((i, i + len(al)))
                start = i + 1
    if not spans:
        return False
    start, found = 0, False
    while True:
        i = low_text.find(h, start)
        if i < 0:
            break
        found = True
        if not any(s0 <= i and i + len(h) <= e0 for s0, e0 in spans):
            return False        # 有獨立出現 → 語意主體成立,不讓位
        start = i + 1
    return found


#: 期間詞判準與 analysis_validate **共用同一份**(news_rules.PERIOD_TOKEN,
#: r3:兩份已漂移過 —— 1Q/1H/CY25/2Q26 一邊擋、一邊放)。裸數字的排除是
#: 這一側自己的:「成交量 3231 張」的 3231 是張數不是矽創;嚴格的
#: `mentions_entity` 本來就拒裸數字(要括號),literal fallback 不得把
#: 那條路重新打開。合法股票代號仍走括號代號規則。


def _literal_mention(text: str, cand: str) -> bool:
    """候選字串**自己**有沒有逐字出現在文字裡(規則與別名比對一致)。"""
    a = str(cand or "").strip()
    if len(a) < 2:
        return False
    if a.isdigit() or _PERIOD_TOKEN_SHARED.fullmatch(a):
        return False
    low = str(text or "").lower()
    if _LATIN_ALIAS.fullmatch(a):
        return _latin_alias_hit(low, a.lower())
    return a.lower() in low


def event_subject_key(title: str, entity: str = "",
                      entity_aliases=(), known_names=None) -> str:
    """事件的**對象指紋**:標題裡除了自己之外的可辨識主體。

    批#72(第七輪 P0-1 錯誤A)。實測問題:台積電 7/05「獲蘋果2奈米大單」與
    7/25「獲輝達CoWoS追加訂單」共用 `timeline_key ('2330','orders|2026-07')`
    與同一個 `event_id`,於是第二張真訂單的 `lifecycle_weight` 被歸零
    (`is_incremental=False`)—— 批#64 只修了跑內聚合,生命週期層仍然塌掉。

    這裡刻意用**確定性的 token 集合**而不是相似度比對:身分必須可重現,
    而且同一事件的 rumor→confirmed→implemented 三種標題寫法不同,
    相似度會飄、token 集合(交易對手/規格)不會。
      「台積電獲蘋果2奈米大單」    → apple 系 + 2奈米
      「台積電確認蘋果2奈米訂單」  → 同上(接得起來)
      「台積電獲輝達CoWoS追加訂單」→ 輝達 + cowos(分得開)

    取不到對象時回空字串 —— 那時退回原本的月 bucket 行為(誠實的降級:
    沒有可辨識對象的事件本來就無法區分)。
    """
    # r1(Codex,P1):先剝媒體尾綴。原本直接吃原始標題,「⋯ - CNBC」與「⋯ - DIGITIMES」
    # 會拿到不同指紋 → 同一訂單跨媒體/隔日轉載變成不同 event_id。
    text = strip_outlet_suffix(title)
    if not text:
        return ""
    # r1(Codex,P1):**自身的每一個別名都要排除**,不是只排除整串。
    # `entity_aliases` 現在是 tuple(例如 ("輝達","NVIDIA")),原本傳整個查詢字串
    # 進來、只比對整串 → NVDA 自己的標題會以 `nvidia` 當「對象」。
    mine = {str(entity).strip().lower()} if str(entity).strip() else set()
    if isinstance(entity_aliases, str):
        entity_aliases = (entity_aliases,)
    for alias in entity_aliases or ():
        for piece in str(alias or "").split():
            if piece:
                mine.add(piece.lower())
    tokens = set()
    # 詞彙表的值可能是**多 token 的查詢字串**(GOOGLE_NEWS_COMPANIES 的
    # 「輝達 NVIDIA」「蘋果 Apple」),必須逐 token 比對。
    # 自測抓到:第一版把整串當單一 token,於是所有美股別名都比不中,
    # 英文/中英混寫的標題完全取不到對象 —— 與批#71 的 `_8K_QUERY_BY_TICKER`
    # 是同一個坑(那邊已經處理過,這裡又犯了一次)。
    # `known_names` 是 {code: (別名, ...)};別名表**獨立於搜尋查詢**,
    # 不含主題詞與查詢運算子(見 morning_report._US_ENTITY_ALIASES 的說明)。
    candidates = []
    values = (known_names or {}).values() if hasattr(known_names, "values")         else (known_names or ())
    for raw in values:
        for alias in ((raw,) if isinstance(raw, str) else (raw or ())):
            for piece in str(alias or "").split():
                candidates.append(piece)
    # Event Identity v4:比對到別名時吐出**代號**而不是別名字串。
    # 實測(2026-08-01)原本的行為:
    #   「台積電確認蘋果2奈米訂單」→ '2奈米,蘋果'
    #   「台積電獲Apple 2奈米訂單」→ '2奈米,apple'
    # 同一筆訂單、同一句中文,只因為對手方寫成英文就變成兩個事件。
    # 歧義的別名(對到多個代號)查不到,自然退回原本的別名行為。
    alias_code = _alias_to_code(known_names)
    for raw in candidates:
        nm = str(raw or "").strip()
        if len(nm) < 2 or nm.lower() in mine or nm.isdigit():
            continue
        canon = alias_code.get(nm.lower(), nm.lower())
        if canon in mine:            # 自己的代號也算自己
            continue
        if any("一" <= ch <= "鿿" for ch in nm):
            if nm in text:
                tokens.add(canon)
            continue
        if _re_module.search(
                rf"(?<![A-Za-z0-9]){_re_module.escape(nm)}(?![A-Za-z0-9])",
                text, _re_module.I):
            tokens.add(canon)
    # 拉丁字母只收**專有名詞/型號**,不收普通字詞。自測抓到:原本什麼都收,
    # 「TSMC wins Apple 2nm order」的指紋是 `2nm,apple,order,tsmc,wins` ——
    # 換個動詞(secures/lands)指紋就變了,身分完全不穩,而身分必須可重現。
    # 判準:全大寫(HBM、ABF)或**內部**有大寫(CoWoS、HBM3E)才算型號/縮寫;
    # 普通首字大寫的英文詞(Apple、Order)只有出現在詞彙表裡才採信(上面那圈)。
    for m in _SUBJECT_LATIN_RE.findall(text):
        up = m.upper()
        if up in _SUBJECT_LATIN_STOP or up.lower() in mine:
            continue
        if not (m.isupper() or any(ch.isupper() for ch in m[1:])):
            continue
        # v4:這一圈也要正規化。自測抓到:「TSMC gets NVIDIA CoWoS order」
        # 的 NVIDIA 全大寫,會被當成型號再收一次 —— 指紋變成
        # `cowos,nvda,nvidia`,而中文版是 `cowos,nvda`,兩邊照樣分開。
        # 別名比對與型號掃描是兩條獨立的路徑,只修一條等於沒修。
        canon = alias_code.get(up.lower(), up.lower())
        if canon in mine:
            continue
        tokens.add(canon)
    for m in _SUBJECT_SPEC_RE.findall(text):
        tokens.add(_canon_spec(m))     # v4:2奈米 與 2nm 是同一個規格
    if not tokens:
        return ""
    return ",".join(sorted(tokens))[:60]


def _event_timeline_key(event: dict) -> tuple[str, str]:
    """Use a stable lineage key across rumor, confirmation and implementation coverage.

    財報/財測/營收類事件附季度 bucket 成獨立 episode(向後相容:previous map
    每次由歷史事件 dict 重算本函式,舊紀錄會以同一規則重新分桶,無 state 遷移)。"""
    entity = str(event.get("entity") or "").strip()
    # **鍵的主體走機器身分**(2026-08-22 外審 P1):法域/機構
    # (Russia/俄羅斯)之外,**公司也要收斂**。上一版把公司排除在外,
    # 理由是「公司鍵慣例是代號」—— 而生產 state 裡就有
    # `export_controls:輝達:2026-08`,前提不成立;輝達/NVIDIA/NVDA
    # 因此是三條 lifecycle。收斂配 `state_migrations` 的鍵遷移
    # (與 08/21 生產跑過的那條同一支)。
    entity = _si.identity_name(entity) or entity
    event_type = str(event.get("event_type") or "general").strip() or "general"
    if not entity or event_type == "general":
        import hashlib
        cluster = "|".join(str(part) for part in _event_cluster_key(event))
        if not cluster.strip("|"):
            cluster = str(event.get("title") or event.get("summary") or "")
        digest = hashlib.sha1(cluster.encode("utf-8")).hexdigest()[:10]
        if entity:
            # 有 entity 的 general 事件:標題 digest 必須進 key——否則同公司所有
            # 雜項公告(董事異動/子公司投資…)全撞 (entity, "general") 一鍵,
            # 實際 state 中單一金控 16 則不同公告互吞(三審 P0-1 殘餘碰撞)
            return entity, f"general|{digest}"
        return f"cluster:{digest}", event_type
    if event_type in _QUARTERLY_EVENT_TYPES or event_type in _MONTHLY_EVENT_TYPES:
        bucket = _event_period_bucket(event, monthly=event_type in _MONTHLY_EVENT_TYPES)
    else:
        # 其餘型別(orders/litigation/export_controls/geopolitical…)一律掛「月」bucket:
        # 舊鍵 (entity, type) 讓同公司三月與六月的兩張訂單永久共用 lineage,第二張
        # 的 confirmed 被判「無進展」權重歸零(GPT-5.6 三審 P0-2)。不用「日」bucket——
        # rumor→confirmed 常跨數日,日 bucket 會把同一事件的生命週期切斷。
        # 代價:同公司同月兩件同型別事件仍共 lineage(寧可少計,不灌水)。
        # 批#72(第七輪 P0-1 錯誤A):**有可辨識對象時用對象,不用月份。**
        # 月 bucket 有兩個相反的毛病,實測都會發生:
        #   (a) 同公司**同月**兩件不同事件共用 lineage → 第二件真事件的
        #       lifecycle_weight 被歸零(台積電 7/05 蘋果單 vs 7/25 輝達單)
        #   (b) 同一樁事情**跨月**被切成兩集 → rumor 在七月、confirmed 在八月
        #       就接不起來(舊註解已記載這個代價)
        # 對象指紋同時解掉兩邊:它在同一事件的 rumor→confirmed→implemented
        # 之間穩定,在兩件不同事件之間相異,而且是**確定性**的(見
        # `event_subject_key` 說明:身分不能靠相似度)。
        # 取不到對象時退回月 bucket —— 誠實的降級,行為與改動前完全相同。
        subject = str(event.get("subject_key") or "")
        if subject:
            # **年份仍然要留。** 拿掉月份是為了讓同一樁事情跨月接得起來,
            # 但完全不帶時間會讓「每年同一批產能訂單」永久共用 lineage
            # ——第二年的真訂單會再次被判為無進展而歸零,等於把同一個 bug
            # 推到一年後。年界的代價只是「跨年的同一樁事情被切成兩集」
            # (多算一次),方向上比「少算一次真事件」安全。
            year = _event_period_bucket(event, monthly=True)[:4]
            return entity, f"{event_type}|s:{subject}|{year}"
        bucket = _event_period_bucket(event, monthly=True)
    if bucket:
        return entity, f"{event_type}|{bucket}"
    return entity, event_type


def _event_generation_bridge_key(event: dict) -> tuple:
    """跨世代橋接鍵:(主體, 型別, 正規化標題)。

    r1(Codex,P1):歷史 state 裡的事件沒有 `subject_key`,算出來的是月 bucket 鍵;
    部署後同一樁事情的重複報導算出的是對象鍵 → 在 previous 裡找不到前態,
    重複的 confirmed 會**重新拿到 1.0 權重**,event-study 又因 event_id 不同
    把它當第二個獨立事件,永久灌進可信樣本。

    **為什麼用標題而不是舊的月 bucket 當相容鍵**:自測抓到——用月 bucket 會把
    同月的**不同**事件(蘋果單 vs 輝達單)也一起壓成非增量,那是拿錯誤B換錯誤A。
    重複報導的標題相同、不同事件的標題不同,所以標題才是能分開兩者的橋。

    只在主鍵查不到時查它,且隨歷史滾動自然退場,不留永久分支。
    """
    title = _re_module.sub(
        r"\W+", "", str(event.get("title") or "").lower())[:48]
    if not title:
        return ()
    # 主體走機器身分(2026-08-22 外審 P1):橋接鍵原樣帶 entity,
    # 舊代記錄寫 NVIDIA、今天寫輝達時連這條退路都接不上。
    return (_si.identity_name(str(event.get("entity") or "")),
            str(event.get("event_type") or "general"), title)


def _event_instance_id(event: dict) -> str:
    """Episodic 事件實體 ID(GPT-5.6 三審 P0-1)。

    舊 event_id = sha1(cluster key),非 general 事件的 cluster key 抹掉標題且無期別
    → 台積電 2026Q1/Q2/2027Q1 財報全撞同一 ID,event study 去重(event_id 優先)
    把後續季度樣本永遠擋在門外,learned impact 累積不到樣本。
    新 ID 由 timeline 身分衍生(entity 或 entityless 標題指紋 + type|期別 bucket)
    同一事件跨來源同 ID(可去重)、不同季/月/主體不同 ID(不互吞)。

    第十輪 P2-3:這段原本寫「再加 direction」,而下一行註解與實作都明說
    **direction 不進事件身分**(批#72 第七輪 P0-1 錯誤B)。宣稱與實作不符
    會讓後續維護者拿錯誤的前提當設計依據。"""
    import hashlib
    ident, type_key = _event_timeline_key(event)
    # 批#72(第七輪 P0-1 錯誤B):**direction 不進事件身分。**
    # 實測:同一樁事情的傳聞(+1)與否認(−1)拿到不同的 event_id
    # (1978c729d568 vs a12f2f577fbc)卻是同一個 timeline_key —— 身分定義
    # 自相矛盾,event-study 會把同一事件的兩次觀測算成兩個 unique events。
    # direction 是**可修訂的觀測屬性**(信念會被下一則報導推翻),不是身分。
    # 不會因此失去區分能力:event-study 的去重鍵本來就另外帶 direction
    # (`("event_id", event_id, code, event_type, direction)`),所以拿掉之後
    # 正負事件仍分得開,只是同一事件的翻轉不再被當成兩件事。
    raw = "|".join((ident, type_key))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def apply_event_timeline(model_history: list[dict],
                         events: list[dict],
                         known_names=None,
                         migration_stats: Optional[dict] = None) -> list[dict]:
    """Annotate incremental lifecycle transitions and suppress repeated event scoring.

    `known_names`(批#72 r2,Codex P1):代號→別名 tuple。歷史 state 裡的事件沒有
    `subject_key`(那是本批新增的),若只靠標題橋接,**改寫過的重複報導**
    (「獲蘋果2奈米大單」→「確認獲蘋果2奈米訂單」)橋不起來,confirmed 會重新
    拿到 1.0 權重。有了別名表就能直接**為歷史事件補算 v3 身分**,兩代主鍵對齊,
    改寫與換媒體都不影響。拿不到別名表時仍退回標題橋接(至少擋住逐字重複)。
    """
    previous: dict[tuple[str, str], str] = {}
    #: 跨世代橋接表(見下方 `_event_generation_bridge_key` 說明)
    previous_bridge: dict[tuple, str] = {}
    for record in sorted(model_history or [], key=lambda item: item.get("session_date", "")):
        for event in record.get("structured_events") or []:
            lifecycle = str(event.get("lifecycle") or _event_lifecycle(event))
            # 為**舊世代**的歷史事件用當前公式重算身分(見 docstring)。
            # 不寫回 state —— 這裡只是為了讓兩代主鍵在本次比對中對齊。
            #
            # r1(Codex #4,P2):原本的條件是「欄位缺失才重算」。
            # v3 的事件**存著** subject_key(`2奈米,蘋果`),於是升到 v4 之後
            # 它不會被重算,而新事件是 `2nm,aapl` —— 主鍵永遠對不上,
            # 標題橋又因改寫而 miss,confirmed 的重複報導會重新拿到完整的
            # lifecycle weight。判準改成「缺欄位**或**世代較舊」。
            _gen = int(_safe_number(event.get("event_schema")))
            if known_names and (not str(event.get("subject_key") or "")
                                or _gen < EVENT_SCHEMA_VERSION):
                _ent = str(event.get("entity") or "")
                _was = str(event.get("subject_key") or "")
                event = dict(event, subject_key=event_subject_key(
                    str(event.get("title") or ""), _ent,
                    (known_names or {}).get(_ent) or (), known_names))
                # 第十輪 P1-11:**遷移要能被驗收。** 沒有這些計數,
                # 「v4 上線了」與「v4 一則都沒改到」在 manifest 裡長得一樣。
                if migration_stats is not None:
                    migration_stats["recomputed"] = (
                        migration_stats.get("recomputed", 0) + 1)
                    _now = str(event.get("subject_key") or "")
                    if _was and _was != _now:
                        # 第十一輪 P1-1:**原本是 `dict[舊指紋] = 新指紋`。**
                        # 同一個舊指紋第二次產生不同的新指紋時直接覆蓋 ——
                        # 於是「分裂」在資料結構上就不可能被觀測到,
                        # 而我寫的 `splits > 0 → 降級告警` 一次都不會響。
                        # 我自己造了一個永遠通過的守衛。
                        #
                        # 改成 append-only 的觀測清單,而且 key 納入
                        # entity 與 event_type:不同公司碰巧有相同舊指紋時
                        # 也不該互相覆蓋。
                        migration_stats.setdefault("changed_pairs", []).append({
                            "entity": _ent,
                            "event_type": str(event.get("event_type") or ""),
                            "old": _was, "new": _now})
                    if _now and _now != _was:
                        migration_stats["canonicalized"] = (
                            migration_stats.get("canonicalized", 0) + 1)
                    migration_stats.setdefault("by_schema", {})
                    migration_stats["by_schema"][str(_gen or "legacy")] = (
                        migration_stats["by_schema"].get(str(_gen or "legacy"), 0) + 1)
            previous[_event_timeline_key(event)] = lifecycle
            # r1(Codex,P1):**歷史紀錄沒有 `subject_key`。**
            # 批#72 之前存下來的事件算出的是月 bucket 鍵(`orders|2026-07`),
            # 而部署後同一樁事情的重複報導算出的是對象鍵
            # (`orders|s:2奈米,蘋果|2026`)→ 在 previous 裡找不到前態,
            # 重複的 confirmed 會**重新拿到 1.0 權重**,而 event-study 又因
            # event_id 不同把它當成第二個獨立事件,永久灌進可信樣本。
            #
            # 不做 state 遷移(要重算指紋就得有詞彙表,而這一層拿不到,
            # 硬塞會讓身分層依賴上層資料)。改為**同時登錄相容鍵**:
            # 歷史事件即使有 subject_key 也一併以月 bucket 形式登錄一次,
            # 新事件查不到主鍵時再查相容鍵。兩代身分因此接得起來,
            # 而且隨著歷史滾動自然退場,不留永久分支。
            bridge = _event_generation_bridge_key(event)
            if bridge:
                previous_bridge[bridge] = lifecycle
    # r5(Codex,P1):**`rejected` 原本沒進這三張表**,所以 confirmed → rejected
    # 拿到 is_incremental=False、權重 0 —— 與 F9 修正前的後果一模一樣,
    # 缺陷只是換了個名字。「董事會否決收購案」是**最該被寫出來**的那種消息,
    # 而它的權重是 0。
    # 定位:rejected 與 withdrawn 同屬「結論被推翻」,在 order 中放最高階
    # (可從任何前態抵達),base_weight 給 1.0(與 withdrawn 一致)。
    order = {"rumor": 1, "confirmed": 2, "implemented": 3,
             "withdrawn": 4, "rejected": 4}
    base_weight = {"rumor": 0.35, "confirmed": 1.0, "implemented": 0.55,
                   "withdrawn": 1.0, "rejected": 1.0}
    transitions = {("rumor", "confirmed"): 0.65, ("confirmed", "implemented"): 0.45,
                   # 已確認的事被官方否決,是**反轉**,資訊量最高
                   ("confirmed", "rejected"): 1.0,
                   ("implemented", "rejected"): 1.0}
    output = []
    for raw in events or []:
        event = dict(raw)
        key = _event_timeline_key(event)
        bridged_prior = None
        if key not in previous:
            # 主鍵查不到時,用**標題**橋接到舊世代(見
            # `_event_generation_bridge_key`)。刻意不用舊的月 bucket 當相容鍵:
            # 那會把同月的**不同**事件也一起壓掉,等於拿錯誤B換錯誤A。
            bridged_prior = previous_bridge.get(
                _event_generation_bridge_key(event))
        status = _event_lifecycle(event)
        prior = previous.get(key)
        if prior is None and bridged_prior is not None:
            prior = bridged_prior      # 跨世代橋接(見上方說明)
        is_incremental = prior != status and (
            prior is None or status in ("withdrawn", "rejected")
            # withdrawn/rejected 都不是不可逆終態:撤回或否決後的新動態
            # = 新 episode 重新起算(GPT-5.6 二審 P0;否則撤回過的主題永遠拿
            # 0 權重)。r5:rejected 一併納入,否則被否決過的案子重啟時
            # 永遠拿不到權重——而「否決後重新提案」正是值得寫的續報。
            or prior in ("withdrawn", "rejected")
            or order.get(status, 0) > order.get(prior, 0))
        event["lifecycle"] = status
        event["previous_lifecycle"] = prior
        event["timeline_key"] = "|".join(key)
        event["is_incremental"] = is_incremental
        event["lifecycle_weight"] = (
            transitions.get((prior, status), base_weight.get(status, 0.0))
            if is_incremental else 0.0
        )
        previous[key] = status if is_incremental or prior is None else prior
        output.append(event)
    return output

#: 事件身分公式的世代。批#72 起為 3(direction 移出 event_id、非期別型改用
#: 對象指紋)。event-study 只信任**當代**的 event_id;更舊的 evidence 走
#: session 級 fallback,避免兩代 ID 把同一事件算成兩個可信事件。
#:
#: 批#107 起為 4(Event Identity v4:對象與規格正規化)。指紋公式改了就必須
#: 跳版 —— 不跳的話,舊的 schema-3 事件會被當成同代而錯誤接續:
#: 同一筆訂單的舊 ID 是 `2奈米,蘋果`、新 ID 是 `2nm,aapl`,
#: 兩者並存卻都自稱當代,event-study 會把它算成兩個獨立的可信事件。
#: 批#115 起為 5(2026-08-22 外審 r1 P1):**公司主體改走機器身分**,
#: `_event_instance_id` 由 `_event_timeline_key` 雜湊而來 —— 部署前存下的
#: `2330|earnings|2026Q2` 與部署後的 `台積電|earnings|2026Q2` 是兩個 ID,
#: 而兩者都自稱 schema 4。不跳版的話 event-study 會把同一樁事算成兩個
#: 獨立的可信事件(那正是 3→4 跳版時記下的同一種傷害)。跳版後舊
#: evidence 走既定的 session 級 fallback。
EVENT_SCHEMA_VERSION = 5


def _event_study_dedupe_key(row: dict, evidence: dict) -> tuple:
    event_type = str(evidence.get("event_type") or "")
    direction = int(_safe_number(evidence.get("direction")))
    code = str(row.get("code") or "")
    # event_schema >= 2 = episodic ID 世代(2026-07-17 起)。舊 evidence 的
    # event_id 是無期別的 cluster 雜湊——歷史上不同季度/月份的事件共用同一 ID,
    # event study 全史重算時會永久壓掉後續 episode 的樣本(不會「自然癒合」,
    # GPT-5.6 四審 P1);舊 timeline_key 同樣缺 bucket。因此舊 evidence 一律走
    # session 級 fallback:同事件跨日報導會略為過切(可控),但不同 episode
    # 不再互吞(方向:寧過切勿互吞,配合 unique_events 誠實計數)。
    # 批#72 r1(Codex,P1):**身分公式換代了,舊 ID 不能再當可信 ID 用。**
    # v3(本批)把 direction 移出 event_id 並改用對象指紋,所以同一樁事情在部署
    # 前後會拿到兩個不同的 event_id;若兩代都標 `event_schema: 2` 而一律信任
    # event_id,event-study 會**永久**把它算成兩個獨立的可信事件。
    # 改為只信任**當代**:舊世代 evidence 一律走 session 級 fallback
    # (與 schema<2 的處理一致,已記載為「寧過切勿互吞」)。
    # 世代編號也放進鍵裡,兩代 ID 永不意外相撞。
    # 殘留代價講明白:跨越部署那一刻的同一事件會被多算一次 —— 一次性、有界,
    # 且隨 model_history 修剪自然退場;比「永久兩個可信事件」好。
    if int(_safe_number(evidence.get("event_schema"))) >= EVENT_SCHEMA_VERSION:
        event_id = str(evidence.get("event_id") or "").strip()
        # 世代編號放在**尾端**。自測抓到:第一版插在 index 1,而消費端
        # (`build_event_study` 的 `event_key[:2] + event_key[3:]`)用**位置切片**
        # 丟掉 code —— 插在中間會變成丟掉 event_id、保留 code,5 個不同 ID
        # 直接塌成 1 個。這種位置耦合很脆,但改消費端的切片風險更大,
        # 所以維持既有的 (標籤, 身分, code, type, direction) 前綴不動,只加尾巴。
        if event_id:
            return ("event_id", event_id, code, event_type, direction,
                    EVENT_SCHEMA_VERSION)
        timeline_key = str(evidence.get("timeline_key") or "").strip()
        if timeline_key:
            return ("timeline", timeline_key, code, event_type, direction,
                    EVENT_SCHEMA_VERSION)
    return (
        "fallback",
        str(row.get("session_date") or ""),
        code,
        event_type,
        direction,
        str(evidence.get("scope_company") or ""),
        str(evidence.get("scope_industry") or ""),
        str(evidence.get("scope_supply_chain") or ""),
        str(evidence.get("lifecycle") or ""),
        str(evidence.get("relation") or ""),
    )

def _shrunk_event_impact(event_study: dict[tuple, dict],
                         code: str,
                         industry: str,
                         supply_chain: str,
                         event_type: str,
                         direction: int) -> tuple[float, int, str]:
    """Shrink sparse company studies toward industry, supply-chain and global priors."""
    levels = [
        ("company", code, 10.0),
        ("industry", industry, 18.0),
        ("supply_chain", supply_chain, 18.0),
        ("global", "", 30.0),
    ]
    weighted, total_weight, samples, used = 0.0, 0.0, 0, []
    for scope, scope_id, prior_strength in levels:
        if scope != "global" and not scope_id:
            continue
        stats = event_study.get((scope, scope_id, event_type, direction)) or {}
        # 樣本數用「不重複事件數」而非 event-stock 觀測數:一個出口管制事件映射
        # 20 檔股票不是 20 個獨立樣本(GPT-5.6 四審 P0-1)。五審再收斂:只認
        # schema-2 世代(unique_events_v2)——legacy 走 session fallback 會過切
        # 灌數;缺欄退階 unique_events → samples(舊快取相容,study 每次重建
        # 實務不會發生)。
        n = int(stats.get("unique_events_v2",
                          stats.get("unique_events", stats.get("samples", 0))))
        if not n:
            continue
        weight = n / (n + prior_strength)
        weighted += _safe_number(stats.get("avg_excess_pct")) * weight
        total_weight += weight
        # 樣本數取「最寬層」而非跨層加總:company ⊆ industry/supply_chain ⊆ global
        # 是巢狀子集,同一事件會同時進多層;加總會把 2 個真實事件灌成 8 個樣本,
        # 讓下游「study_samples >= 5 才用 learned impact」門檻形同虛設
        # (GPT-5.6 三審 P0-3)。max = 支撐這次估計的不重複觀測數上界。
        samples = max(samples, n)
        used.append(scope)
    if not total_weight:
        return 0.0, 0, "conservative_fallback"
    impact = max(-3.0, min(3.0, weighted / total_weight))
    return impact, samples, "hierarchical_event_study:" + "+".join(used)

_LLM_EVENT_TYPES = {"guidance_raise", "guidance_cut", "orders", "earnings",
                    "revenue_growth", "export_controls", "litigation", "geopolitical",
                    "cybersecurity", "general"}

# LLM 抽取事件的欄位白名單=extractor prompt 明文要求的欄位。名單外的欄位
# (source/source_grade/official/quality_score…)一律剝除:LLM(或藏在新聞裡的
# 注入指令)不得自封官方 A 級來源或高品質分(GPT-5.6 三審 P1-1)。
# 批#68:`surprise_score` 移出白名單 —— 那是**評分**不是抄錄。程式碼裡批#42 r2
# 的註解已經記載過實測後果:「LLM 版 surprise_score 由它自報(實測 0.7)高於
# 權威版的啟發式(0.35),**戲劇化的那版反而更醒目**」。當時只從 event_type
# 那一側修,分數本身仍讓模型自訂。依本專案既有原則(Python 權威、LLM 只能
# 抄錄)收回,一律由 `_event_surprise_score` 的啟發式決定。
#
# `published` 保留在白名單裡,但**會被來源項的權威時間覆寫**(見
# morning_report.extract_structured_events 的標題唯一命中回填):
# 直接刪掉欄位會讓事件退回「七天前」的預設值,新鮮度反而更失真。
_LLM_EVENT_FIELDS = frozenset({
    "entity", "event_type", "direction", "confidence",
    "lifecycle", "title", "summary", "published",
    # 批#76(第七輪 P1-3):**來源項 ID**。這是把 provenance 收回 Python 的鑰匙
    # ——有了它,`published` / `source_grade` / 交叉驗證都可以由 Python 從
    # 來源項直接取,不必再依賴模型抄錄標題後由我們反查(標題稍微改寫或多筆
    # 同標題時,反查就失效,而失效是靜默的:模型自報的時間會被留下來)。
    "source_item_ids"})
_LLM_LIFECYCLES = frozenset({"rumor", "confirmed", "implemented", "withdrawn"})
# LLM 二手抽取的自報信心上限=一般媒體項的預設信心(0.65):不得高於一手媒體、
# 更不得逼近官方(0.90)。
_LLM_CONFIDENCE_CAP = 0.65


def _validate_llm_events(events: list) -> tuple[list, int]:
    """驗證 LLM 抽取事件 schema:event_type 屬允許集合、direction ∈ {-1,0,1}、entity 為字串或缺。
    回 (合格清單, 丟棄數)。不合格項丟棄(寧缺勿濫,避免髒事件污染下游計分/去重)。
    合格項只保留白名單欄位,lifecycle 限合法值、confidence 上限 0.65(見上)。"""
    valid, dropped = [], 0
    for ev in events or []:
        if not isinstance(ev, dict):
            dropped += 1
            continue
        # direction 嚴格限 -1/0/1:舊 int() 轉型會把 1.9 收成 1、0.5 收成 0、
        # True 收成 1(bool 是 int 子類,in (-1,0,1) 也擋不住)——LLM 輸出的
        # 模糊方向必須整筆丟棄,不得靜默捨入(GPT-5.6 四審 P3)
        direction = ev.get("direction")
        if isinstance(direction, bool) or not isinstance(direction, (int, float)) \
                or direction not in (-1, 0, 1):
            direction = None
        else:
            direction = int(direction)
        entity = ev.get("entity")
        if (str(ev.get("event_type") or "") in _LLM_EVENT_TYPES
                and direction in (-1, 0, 1)
                and isinstance(entity, (str, type(None)))):
            clean = {k: v for k, v in ev.items() if k in _LLM_EVENT_FIELDS}
            clean["direction"] = direction   # 正規化為 int(1.0 → 1)
            # 批#76:`source_item_ids` 只收「看起來像本次 payload 發出的 ID」
            # 的短字串清單。它是 provenance 的依據,不能讓模型塞任意內容
            # (下游會拿它去查表;查不到就當作沒給,不會憑空造出來源)。
            ids = clean.get("source_item_ids")
            if isinstance(ids, str):
                ids = [ids]
            if isinstance(ids, (list, tuple)):
                ids = [str(x)[:16] for x in ids
                       if isinstance(x, (str, int)) and str(x).strip()][:6]
            clean["source_item_ids"] = ids or []
            if str(clean.get("lifecycle") or "") not in _LLM_LIFECYCLES:
                clean.pop("lifecycle", None)
            try:
                clean["confidence"] = min(_LLM_CONFIDENCE_CAP,
                                          float(clean["confidence"]))
            except (KeyError, TypeError, ValueError):
                clean.pop("confidence", None)
            valid.append(clean)
        else:
            dropped += 1
    return valid, dropped


#: 真正的否定詞。Codex r1(P1):原本把「遭」也列進來,結果「遭否決」被判成
#: negated_reject —— 但「遭否決」就是否決,極性沒有反轉,於是從「否決」改寫成
#: 「遭否決」會被誤判為進展。「遭」是**被動標記**不是否定詞。
_NEGATORS = ("未", "不", "沒", "無", "非", "否")
#: 否定詞與決策動詞之間常插入副詞或受詞:「尚未獲董事會通過」「並未正式核准」。
#: Codex r1(P1):原本只看**緊鄰前一字**,這些常見寫法一律漏判。
#: 用有界視窗(往前 6 字)而非無界,避免「通過…但未…」這種跨子句誤配。
_NEG_WINDOW = 6


#: 「尚未X」是流程還在跑,不是被否決。判成否決會讓「後來核准了」看起來像翻案
#: 而不是正常進度(r6 Codex 提出,r7 一併套到 story_ledger 以消除兩邊分歧)。
_PENDING_MARKERS = ("尚未", "還沒", "仍未", "暫未", "尚待", "not yet", "pending")


#: 子句分隔符。待決標記管的是**整個子句**,不像否定詞那樣緊貼動詞。
#
# r9(Codex,P1):**用明確的 Unicode escape,不要直接打字元。**
# 我上一輪以為寫了「全形 + 半形」成對,但字元在編輯管線中被轉成 ASCII ——
# 實際存進檔案的是 `;;` `,,` `!!` `??` 四組**重複的半形**,全形
# ，；！？ 一個都不在。中文標題用的正是全形,所以整個子句邊界對真實輸入
# 幾乎失效:「尚待主管機關進一步審議，另案已核准」的「尚待」會跨過全形逗號,
# 把後面已核准的事判成 rumor。
# escape 形式看得出漏了什麼,也不會被任何編碼轉換弄壞。
_CLAUSE_SEPARATORS = (
    "。"      # 。 句號
    "，,"  # ，, 逗號(全形/半形)
    "；;"  # ；; 分號
    "！!"  # ！! 驚嘆號
    "？?"  # ？? 問號
    "：:"  # :: 冒號
    "、"      # 、 頓號
    "\n"
)


def is_pending_decision(text: str, idx: int) -> bool:
    """決策動詞前是否為「待決」語氣(而非否決)。

    r8(Codex):**用子句邊界而非固定字數**。「本案尚待主管機關進一步審查後核准」
    的「尚待」離「核准」有 11 個字,固定 6 字視窗抓不到 → 落到 A 級 fallback
    判成 confirmed。而待決標記在中文裡管的是整個子句,不像否定詞那樣緊貼動詞。

    仍以子句為界(不是無界回看):「已核准,尚待備查」的「尚待」在逗號之後,
    不該回頭把前一子句的「核准」判成待決。
    """
    start = 0
    for i in range(idx - 1, -1, -1):
        if text[i] in _CLAUSE_SEPARATORS:
            start = i + 1
            break
    return any(m in text[start:idx] for m in _PENDING_MARKERS)


def is_negated_decision(text: str, idx: int) -> bool:
    """text[idx:] 起的決策動詞是否被否定。用**有界視窗**而非只看緊鄰前一字。

    Codex r1(P1):「尚未獲董事會通過」「並未正式核准」這類寫法,否定詞與動詞
    之間隔著副詞或受詞,只看前一字一律漏判——而漏判的後果正是這條修正要防的
    (帳本不更新、headline 停在相反結論)。視窗有界(6 字)以免跨子句誤配。

    Codex r2(P1):**這個判準必須只有一份**。我上一輪只在 story_ledger 排除了
    「不但/不僅/不只」,news_events 沒排 → 同一句話在兩個 lifecycle 訊號上結論
    相反。定義放在 news_events(無第一方相依),story_ledger 匯入它——
    單一方向,不可能形成循環,也不可能再分歧。
    """
    window = text[max(0, idx - _NEG_WINDOW):idx]
    if any(n in window for n in _NEGATORS):
        # 「不」在「不但/不僅/不只」裡是遞進連接詞,不是否定該動詞
        for false_pos in ("不但", "不僅", "不只", "不外", "無論", "不管"):
            if false_pos in window and not any(
                    n in window.replace(false_pos, "") for n in _NEGATORS):
                return False
        return True
    return "not " in window.lower() or "fail" in window.lower()


#: 抽取器的結構化輸出 schema 上限。OpenAI strict 模式**不保證支援 `maxItems`**
#: (它只吃 JSON Schema 的一個子集),而我沒有辦法在這裡驗證那件事 ——
#: 所以數量上限一律由 Python 側把關(`_parse_llm_event_json` 的切片),
#: schema 只負責「欄位與型別」這種它確定管得到的部分。
#: 把上限寫進 schema 卻沒生效,會變成「以為擋住了」的那種假守衛。
LLM_EVENT_MAX_ITEMS = 30


def llm_event_json_schema() -> dict:
    """抽取器的 Structured Outputs schema(第九輪 P1-4)。

    **由 `_validate_llm_events` 用的同一組常數推導,不手抄。** 手抄一份 schema
    的必然結局是兩邊漂移:schema 允許的 event_type 多一個,驗證器就默默丟掉;
    少一個,模型就被迫亂填。這裡直接讀 `_LLM_EVENT_TYPES` /
    `_LLM_EVENT_FIELDS` / `_LLM_LIFECYCLES`,漂移在結構上不可能發生。

    OpenAI strict 模式的兩個硬性要求:根節點必須是 **object**(所以包一層
    `events`),而且 `additionalProperties: false` 之下**每個欄位都要列進
    `required`** —— 選填欄位因此用 `["string", "null"]` 這種聯集型別表達,
    而不是從 `required` 拿掉。

    為什麼值得做:抽取器的失敗模式是「回了東西但解析不出來」或「欄位對不上
    被整批丟棄」,而那在 manifest 裡看起來跟「沒有事件」一模一樣。
    schema 把這一類失敗從執行期挪到 API 層,由 provider 保證形狀。
    """
    def _nullable(kind):
        return [kind, "null"]

    props = {
        "entity": {"type": _nullable("string"),
                   "description": "股票代號或公司名;不確定就填 null,不要猜"},
        "event_type": {"type": "string", "enum": sorted(_LLM_EVENT_TYPES)},
        "direction": {"type": "integer", "enum": [-1, 0, 1]},
        "confidence": {"type": _nullable("number")},
        "lifecycle": {"type": _nullable("string"), "enum":
                      sorted(_LLM_LIFECYCLES) + [None]},
        "title": {"type": "string"},
        "summary": {"type": _nullable("string")},
        "published": {"type": _nullable("string")},
        "source_item_ids": {"type": "array", "items": {"type": "string"}},
    }
    # 白名單以外的欄位不得出現在 schema —— 否則等於在 API 層開了一個
    # `_validate_llm_events` 會剝掉的洞(模型填了、我們丟掉,兩邊都白做工)。
    assert set(props) <= set(_LLM_EVENT_FIELDS), \
        f"schema 有白名單以外的欄位:{sorted(set(props) - set(_LLM_EVENT_FIELDS))}"
    return {
        "name": "extracted_events",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["events"],
            "properties": {
                "events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": sorted(props),
                        "properties": props,
                    },
                },
            },
        },
    }


def timeline_subjects(ev: dict) -> list:
    """延燒事件的**主體清單**(正規化、去重、排序)。

    第二十四輪 P1-11:先前身分是 `event_type:entity` 一個字串,於是同一個
    故事會裂成多個身分 —— 生產實測荷姆茲談判同時存在
    `geopolitical:`(47 天)、`geopolitical:伊朗`(5 天)、`geopolitical:美國`(3 天)、
    `geopolitical:美國、伊朗、阿曼`(1 天):**同一件事有四個不同的「第 N 天」**。

    正規化:多主體字串拆開、去空白、去重、**排序**(順序不是語意),
    於是「美國、伊朗」與「伊朗、美國」是同一個身分。
    """
    import re as _re
    raw = str((ev or {}).get("entity") or "")
    parts = [p.strip() for p in _re.split(r"[、,,/／|｜]+", raw) if p.strip()]
    return sorted(dict.fromkeys(parts))[:4]
