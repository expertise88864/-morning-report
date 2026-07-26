"""結構化新聞事件的純規則層(A5-B5,自 morning_report.py 抽出)。

原則:函式本體自 morning_report.py 逐字搬遷、一字不改;morning_report 以同名
re-export 維持既有測試與呼叫端;驗證以 tools/refactor_audit.py verify-move 為準。
"""
from num_utils import _safe_number

from news_rules import (
    NEWS_NEGATIVE_TERMS,
    NEWS_POSITIVE_TERMS,
    _matches_any,
)
import datetime as dt


def _news_event_direction(text: str) -> int:
    """用明確事件詞判斷消息方向；同時有多空詞或沒有方向時不加分。"""
    positive = bool(_matches_any(text, NEWS_POSITIVE_TERMS))
    negative = bool(_matches_any(text, NEWS_NEGATIVE_TERMS))
    if positive == negative:
        return 0
    return 1 if positive else -1

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
    }.get(str(event.get("event_type")), 0.35)

def _event_lifecycle(event: dict) -> str:
    """Classify event progression so repeated coverage does not repeatedly add score."""
    explicit = str(event.get("lifecycle") or event.get("status") or "").lower()
    text = f"{explicit} {event.get('title', '')} {event.get('summary', '')}".lower()
    if any(token in text for token in (
            "withdrawn", "withdraw", "cancelled", "canceled", "撤回", "取消", "暫緩")):
        return "withdrawn"
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
    if explicit in ("rejected", "denied", "declined", "否決", "駁回"):
        return "rejected"
    _REJECT_TOKENS = ("否決", "駁回", "退回", "不予核准", "不予備查",
                      "rejected", "reject", "denied", "deny", "declined")
    if any(tok in text for tok in _REJECT_TOKENS):
        return "rejected"

    _DECISION_TOKENS = ("通過", "核准", "核定", "同意", "批准",
                        "approved", "approve", "confirmed")
    for token in _DECISION_TOKENS:
        i = text.find(token)
        while i >= 0:
            if is_negated_decision(text, i):
                # r6:區分「正式否決」與「**尚未**核准」的待決狀態——
                # 「尚未核准」是流程還在跑,不是被否決,判成 rejected 會讓
                # 「後來核准了」看起來像翻案而非正常進度。
                _win = text[max(0, i - 6):i]
                if any(w in _win for w in ("尚未", "還沒", "仍未", "暫未",
                                           "not yet", "pending")):
                    return "rumor"
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


def _event_period_bucket(event: dict, monthly: bool) -> str:
    """事件的期別 bucket(月頻 YYYY-MM / 季頻 YYYYQn),取 published;
    無法解析回空(退回無 bucket 舊鍵)。"""
    raw = str(event.get("published") or "").strip()
    try:
        d = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    if monthly:
        return f"{d.year}-{d.month:02d}"
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def _event_timeline_key(event: dict) -> tuple[str, str]:
    """Use a stable lineage key across rumor, confirmation and implementation coverage.

    財報/財測/營收類事件附季度 bucket 成獨立 episode(向後相容:previous map
    每次由歷史事件 dict 重算本函式,舊紀錄會以同一規則重新分桶,無 state 遷移)。"""
    entity = str(event.get("entity") or "").strip()
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
        bucket = _event_period_bucket(event, monthly=True)
    if bucket:
        return entity, f"{event_type}|{bucket}"
    return entity, event_type


def _event_instance_id(event: dict) -> str:
    """Episodic 事件實體 ID(GPT-5.6 三審 P0-1)。

    舊 event_id = sha1(cluster key),非 general 事件的 cluster key 抹掉標題且無期別
    → 台積電 2026Q1/Q2/2027Q1 財報全撞同一 ID,event study 去重(event_id 優先)
    把後續季度樣本永遠擋在門外,learned impact 累積不到樣本。
    新 ID 由 timeline 身分衍生(entity 或 entityless 標題指紋 + type|期別 bucket)
    再加 direction:同一事件跨來源同 ID(可去重)、不同季/月/主體不同 ID(不互吞)。"""
    import hashlib
    ident, type_key = _event_timeline_key(event)
    raw = "|".join((ident, type_key,
                    str(int(_safe_number(event.get("direction"))))))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def apply_event_timeline(model_history: list[dict],
                         events: list[dict]) -> list[dict]:
    """Annotate incremental lifecycle transitions and suppress repeated event scoring."""
    previous: dict[tuple[str, str], str] = {}
    for record in sorted(model_history or [], key=lambda item: item.get("session_date", "")):
        for event in record.get("structured_events") or []:
            previous[_event_timeline_key(event)] = str(
                event.get("lifecycle") or _event_lifecycle(event))
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
        status = _event_lifecycle(event)
        prior = previous.get(key)
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
    if int(_safe_number(evidence.get("event_schema"))) >= 2:
        event_id = str(evidence.get("event_id") or "").strip()
        if event_id:
            return ("event_id", event_id, code, event_type, direction)
        timeline_key = str(evidence.get("timeline_key") or "").strip()
        if timeline_key:
            return ("timeline", timeline_key, code, event_type, direction)
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
                    "revenue_growth", "export_controls", "litigation", "geopolitical", "general"}

# LLM 抽取事件的欄位白名單=extractor prompt 明文要求的欄位。名單外的欄位
# (source/source_grade/official/quality_score…)一律剝除:LLM(或藏在新聞裡的
# 注入指令)不得自封官方 A 級來源或高品質分(GPT-5.6 三審 P1-1)。
_LLM_EVENT_FIELDS = frozenset({
    "entity", "event_type", "direction", "confidence", "surprise_score",
    "lifecycle", "title", "summary", "published"})
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
