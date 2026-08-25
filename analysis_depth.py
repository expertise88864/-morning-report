# -*- coding: utf-8 -*-
"""**合法但淺**的判準,以及加深那一次的取捨(第十五/十六輪)。

與 `analysis_validate` 刻意分開,因為兩者的**後果完全不同**:

  * 不合法 → 修補;修不好 → 落回 legacy。
  * **淺 → 什麼都不擋。** 淺而正確的分析落回 legacy 只會換來一封更淺的信,
    所以淺只用來決定「要不要把還沒用掉的那次呼叫拿去加深」。

判準全部是**結構性**的(數步數、查空欄、比集合),不是關鍵詞 ——
第十六輪 P1-5 說對了:關鍵詞當門檻,一句「兩者同向,預計明年影響 5%」
就能騙過。
"""
from __future__ import annotations

# `validate` 在函式內延遲取用 —— `analysis_validate` 的尾端會反向
# import 本模組(相容出口),頂層互相 import 會在載入順序上翻車。


# ------------------------------------------------------------ 深度(不擋信)

def _registry_of(packet):
    """**沒有 packet 時退回寬鬆判準**(舊呼叫端仍要能用),而且說得出來:
    那時 `is_numeric_anchor` 查不到 metadata,只能看命名空間。"""
    if not isinstance(packet, dict):
        return None
    import evidence_registry as _reg
    return _reg.registry(packet)


#: 兩段條目不足時 prompt 要模型用的缺口代號(`data_gaps[].gap_id`)。
#: **這不是閘門** —— 素材夠不夠由 Python 數(見 `depth_advisories`);
#: 這兩個代號的用途是讓缺口自己說出是哪一段,`gap:other:*` 是
#: `tension_refs.canonicalize_gap_ids` 認得的契約命名空間。
#: 常數是**單一定義**:prompt 裡的字面值由測試比對回這裡。
#: 用代號而不是關鍵字:任何提到「科技」的缺口都能關掉建議的話,
#: 這條守衛就等於不存在(r4 外審)。代號要同步寫進 prompt。
TECH_COVERAGE_GAP = "gap:other:tech_coverage"
SECTOR_COVERAGE_GAP = "gap:other:sector_coverage"


#: 這兩個代號合起來是一個集合:它們**宣稱的是輸出自己的形狀**,
#: 而加深輪的工作正是改變那個形狀 —— 見 `_identity` 與
#: `contradicted_coverage_gaps`。
COVERAGE_GAPS = frozenset({TECH_COVERAGE_GAP, SECTOR_COVERAGE_GAP})

#: 兩段的條目下限(prompt 也是這個數;`COVERAGE_FLOORS` 是單一定義)。
#: 2026-08-24 使用者第二次反映「科技/其他類股新聞有點少」—— 那天的信是
#: 7 + 6 = 13 則,**剛好卡在舊目標**(10–16 / ≥6 / ≥5),所以沒有任何守衛
#: 會催它。目標整組上調;字面值只寫在這裡,比較與文案都從這裡取。
COVERAGE_FLOORS = {TECH_COVERAGE_GAP: 8, SECTOR_COVERAGE_GAP: 7}

#: `top_news_analysis` 的總則數目標(下限就是兩段下限之和 —— 分開訂會
#: 出現「兩段都達標而總數不達標」這種自相矛盾的催促)。
NEWS_TARGET_MIN = sum(COVERAGE_FLOORS.values())
NEWS_TARGET_MAX = NEWS_TARGET_MIN + 5

#: 總則數那一條的素材前提(逐段那兩條用**自己那一段**的素材數,見
#: `depth_advisories`)。2026-08-25 外審:先前是 `目標 × 2` 且整組建議
#: 都包在它底下 —— 憑空的倍數,而且造出 15~29 則素材的盲區。改成「有
#: 那麼多素材才要求那麼多則」,不多要一倍。
NEWS_SOURCE_MIN = NEWS_TARGET_MIN


def section_counts(obj, packet=None):
    """`(科技條目數, 科技以外條目數)`;分類壞掉或無法分類時回 `(None, None)`。

    分類走**渲染端同一支**(`analysis_render_depth.is_tech`)—— 兩邊各判
    一次的話,建議說的「科技不足」與信上實際分到第八段的條目可以是兩件事。
    """
    news = [n for n in ((obj or {}).get("top_news_analysis") or [])
            if isinstance(n, dict)]
    try:
        from analysis_render_depth import is_tech as _is_tech
        from analysis_render_depth import news_subject as _subj
        tech = sum(1 for n in news if _is_tech(_subj(n, packet)))
    except Exception:                       # noqa: BLE001 - 分類壞了不猜
        return None, None
    return tech, len(news) - tech


def coverage_gap_faults(before, after, packet=None) -> list:
    """涵蓋率缺口的**雙向**規則。回一串人看得懂的理由(空 = 沒問題)。

    這兩個代號宣稱的是「輸出自己那一段條目不足」,所以它**該不該在**
    完全由那一版自己的條目數決定:

      - 達標了還留著 → 那句話被自己的內容否證,而它會照樣印在信裡。
      - 沒達標卻撤掉 → **撤掉了一句還成立的揭露**(2026-08-24 r3 外審:
        上一版把這兩個代號無條件從身分保存與面向計數裡豁免,於是
        「提早撤掉」沒有任何守衛看得到 —— 修一個洞開一個洞)。

    兩條各自有自己的訊息:處置不同(一邊是撤掉那句話,一邊是把它放回去)。
    """
    out: list = []
    tech, other = section_counts(after, packet)
    # **兩個方向的舉證責任不一樣**(2026-08-24 r4 外審)。上一版數不出來就
    # 一律放行 —— 那同時放過了「撤掉一句還成立的揭露」,而它不需要任何
    # 證明就過關。分開來看:
    #   - 留著 → 要**證明**那一段已達標才算矛盾;證不出來就別擋(不亂擋加深)。
    #   - 撤掉 → 要**證明**那一段已達標才可以撤;證不出來就是不可以撤。
    # 「證不出來」偏向哪一邊,取決於哪一邊的後果不可逆:讀者收到一份看起來
    # 完整的報告,比多留一句過時的揭露嚴重。
    got = {TECH_COVERAGE_GAP: tech, SECTOR_COVERAGE_GAP: other}
    for gid in sorted(COVERAGE_GAPS):
        floor, n = COVERAGE_FLOORS[gid], got[gid]
        in_b, in_a = _declares(before, gid), _declares(after, gid)
        if in_b and not in_a and n is None:
            out.append(f"第二版撤掉了 `{gid}`,但分類不出那一段有幾則 ——"
                       f"**撤掉揭露要有證明**,證不出已達下限 {floor} 就不可以撤")
        elif in_a and n is not None and n >= floor:
            out.append(f"第二版留著 `{gid}`(那一段不足),但它自己已經有 "
                       f"{n} 則(下限 {floor}) —— 那句話已被自己的內容否證,"
                       "要刪掉或改寫成真正還缺的東西")
        elif in_b and not in_a and n is not None and n < floor:
            out.append(f"第二版撤掉了 `{gid}`,但那一段仍然只有 {n} 則"
                       f"(下限 {floor}) —— 那句揭露還成立,不可以就這樣消失"
                       "(讀者會收到一份看起來完整的報告)")
    return out


def _declares(obj, gid: str) -> bool:
    """`obj` 的 `data_gaps` 有沒有宣告這個代號。"""
    return any(str((g or {}).get("gap_id") or "").strip() == gid
               for g in ((obj or {}).get("data_gaps") or [])
               if isinstance(g, dict))


def contradicted_coverage_gaps(obj, packet=None) -> list:
    """宣告了「這一段不足」而**輸出自己**那一段已經達標的缺口代號。

    2026-08-24 r2 外審:矛盾要用**輸出內部**判,不是從素材則數推論。
    「EVIDENCE 有 12 則科技新聞」推不出「宣告是假的」—— 12 則可能是同一
    件事的 12 家轉載,那時候寫 `gap:other:tech_coverage` 是誠實的。
    但**輸出自己有 6 則科技條目卻同時說第八段不足**,那句話就是被它自己
    的內容否證的,而且它會照樣印在信裡(`analysis_render` 的「資料缺口」段)。

    這正是加深輪的常態產物:第一版 2 則 + 缺口(一致),補到 6 則之後
    缺口沒跟著撤(不一致)。所以這兩個代號**不進身分保存**(見 `_identity`),
    讓加深輪撤得掉它。
    """
    ids = {str((g or {}).get("gap_id") or "").strip()
           for g in ((obj or {}).get("data_gaps") or []) if isinstance(g, dict)}
    tech, other = section_counts(obj, packet)
    if tech is None:
        return []
    got = {TECH_COVERAGE_GAP: tech, SECTOR_COVERAGE_GAP: other}
    return [gid for gid in sorted(COVERAGE_GAPS)
            if gid in ids and got[gid] >= COVERAGE_FLOORS[gid]]


def depth_advisories(obj, packet=None) -> list:
    """**合法但淺**的地方(空 = 夠深)。與 `validate()` 刻意分開:

    這裡的每一條都**不會**讓輸出被拒絕 —— 淺而正確的分析落回 legacy
    只會換來一封更淺的信。它們的用途是:第一次輸出合法但淺的時候,
    把**還沒用掉的那次修補額度**拿來加深(見 `deepen_input`),
    最壞情況仍是兩次呼叫,與修補相同 —— 多的是深度,不是新的失敗模式。

    判準全部是結構性的(數步數、查空欄),不是關鍵詞 —— 第十五輪 P1-5
    說對了:關鍵詞當門檻,一句「兩者同向,預計明年影響 5%」就能騙過。
    """
    out: list = []
    if not isinstance(obj, dict):
        return out
    news = [n for n in (obj.get("top_news_analysis") or []) if isinstance(n, dict)]
    # **條數也是深度**(2026-08-19 使用者:「科技類股怎麼只有四篇新聞…
    # 我要的是更多新聞 且要能涵蓋到重要新聞」)。當日素材充足(packet 收了
    # 幾十則)而只分析四五則,漏掉的不是版面是內容。門檻是結構性的:
    # 素材夠(≥20 則)才要求條數(≥6),素材貧乏的日子不硬湊 —— 湊出來的
    # 那幾則會是把同一件事寫兩遍。
    _avail = len((packet or {}).get("news") or []) if isinstance(packet, dict) else 0
    # **兩段各自要夠**(2026-08-22 使用者:科技與其他類股都偏少)。
    # 判準要與 prompt 的目標一致 —— prompt 說十到十六、科技≥6、非科技≥5,
    # 而這裡先前還在執行舊契約(總數 6、非科技 1–2):守衛與 prompt 打架時,
    # 模型交出六則就沒有人會要求它補,信裡的兩段照樣稀薄。
    # 分類走**渲染端同一支**(`analysis_render_depth.is_tech`)—— 兩邊各判一次
    # 的話,建議說的「科技不足」與信上實際分到第八段的條目可以是兩件事。
    # **可行性由「那一段的素材夠不夠」判,不由全域則數判**
    # (2026-08-25 外審 P2)。先前整組建議包在 `_avail >= 30` 底下,而
    # 逐段的判準本來就已經是 `_src_tech >= 8` / `_src_other >= 7` ——
    # 兩套並存的結果是 15~29 則素材的那一大段(科技 15、其他 14、輸出
    # 只有 7+6)完全不進判斷,一句催促都不會發出。使用者已經**兩次**
    # 反映這兩段太少,而守衛在合理的生產區間裡靜靜地什麼都不做。
    #
    # 全域門檻只留給**總則數**那一條(它沒有逐段的分母可用),而且改成
    # 「兩段的下限加起來」而不是「目標 × 2」—— 後者是憑空的倍數。
    try:
        from analysis_render_depth import is_tech as _is_tech
        from analysis_render_depth import news_subject as _subj
        _tech, _ = section_counts(obj, packet)
        # **素材面也要照同一支分類數一次**(r2 外審):只看產出的比例
        # 會在「當天真的沒有那個類股的料」時要求一個做不到的下限 ——
        # 模型即使誠實寫進 data_gaps 也照樣被催,那是在逼它湊。
        # **素材面與產出面問的是兩個不同的問題**(2026-08-25 外審之後補):
        #   產出面 `_other = 總數 - 科技` —— 問「信上會分到哪一段」,
        #     而渲染端第九段本來就收所有非科技條目,認不出主體的也在那裡;
        #   素材面問的是「今天**真的有**那一段的料嗎」—— 那是能力判斷,
        #     `not is_tech` 只代表「沒被認出是科技」,不代表「是非科技」。
        # 先前 `_src_other = _avail - _src_tech` 把**認不出主體的新聞全部
        # 算成非科技素材**,於是 10 則空白新聞就足以要求第九段寫 7 則。
        _src_tech = _src_other = 0
        for it in ((packet or {}).get("news") or []):
            if not isinstance(it, dict):
                continue
            sub = _subj({"source_item_id": it.get("source_item_id")}, packet)
            if _is_tech(sub):
                _src_tech += 1
            elif (sub or {}).get("industry") or (sub or {}).get("name"):
                _src_other += 1
            # 認不出主體的:兩段都不算(它不是任何一段的「料」)
    except Exception:                   # noqa: BLE001 - 分類壞了只檢查總數
        _tech = _src_tech = _src_other = None
    _other = None if _tech is None else len(news) - _tech
    if len(news) < NEWS_TARGET_MIN and _avail >= NEWS_TARGET_MIN:
        out.append(
            f"top_news_analysis 只有 {len(news)} 則,而 EVIDENCE 收了 "
            f"{_avail} 則新聞 —— 目標 {NEWS_TARGET_MIN}–{NEWS_TARGET_MAX} 則;"
            "優先補未被涵蓋的重大事件"
            "(依 materiality 五項判準)。不足時要在 data_gaps 說明為什麼")
    # **出口由 Python 判,不由模型宣告**(2026-08-24 外審 P2)。先前
    # 「模型填了缺口代號就不再催」與 `_src_tech >= 6`(素材面的同一個
    # 問題,Python 自己數的)並存 —— 而後者才是事實。兩者衝突時前者贏,
    # 等於模型只要寫一行 `gap:other:tech_coverage` 就能關掉這條建議,
    # 即使 EVIDENCE 裡明明躺著十幾則科技新聞。素材夠不夠是可數的,
    # 不是可宣告的。
    _tf = COVERAGE_FLOORS[TECH_COVERAGE_GAP]
    if _tech is not None and _tech < _tf and (_src_tech or 0) >= _tf:
        out.append(
            f"科技條目只有 {_tech} 則,而素材有 {_src_tech} 則"
            f"(第八段靠它) —— 目標至少 {_tf} 則;"
            "同族群要寫不同事件,不是同一件事換句話說。"
            "素材雖多但都是同一件事的轉載時,把那個理由寫進 data_gaps"
            "(揭露,不是省略);補足之後 `gap:other:tech_coverage` 要"
            "一併撤掉,那句話就不再成立")
    _of = COVERAGE_FLOORS[SECTOR_COVERAGE_GAP]
    if _other is not None and _other < _of and (_src_other or 0) >= _of:
        out.append(
            f"科技以外只有 {_other} 則,而素材有 {_src_other} 則"
            f"(第九段靠它) —— 目標至少 {_of} 則"
            "(金融/航運/傳產/生技/能源/營建/重電/汽車/觀光),"
            "優先挑該類股龍頭的重大公告或財報")
    # r1 外審(2026-08-24):這裡曾經多一條「宣告了缺料但素材充足 →
    # 點名它說假話」。移除,兩個理由:
    #   1. **它宣稱的事沒有被證明。** `_src_tech` 數的是**素材則數**,
    #      12 則可能是同一件事的 12 家轉載 —— 「素材充足」推不出
    #      「宣告是假的」。這個 repo 記過同一形狀:便利的述詞不等於
    #      它要代表的狀態。
    #   2. **加深輪結構上滿足不了它。** `deepen_input` 明講「保留同樣的
    #      資料缺口」,而 `_identity()` 把 `what_is_missing` 納入不可
    #      遺失身分 —— 模型照著建議刪掉那個缺口,
    #      `deepen_is_an_improvement` 就判定「第二版弄丟了資料缺口」而
    #      沿用第一版。一條做了會被否決的建議,比沒有這條更糟。
    # **被輸出自己否證的涵蓋率缺口**(2026-08-24 r2 外審)。不受 `_avail`
    # 門檻限制:素材少的日子照樣不該印一句被自己內容否證的揭露。
    for _gid in contradicted_coverage_gaps(obj, packet):
        _n = dict(zip((TECH_COVERAGE_GAP, SECTOR_COVERAGE_GAP),
                      section_counts(obj, packet)))[_gid]
        out.append(
            f"data_gaps 還留著 `{_gid}`(那一段不足),但這一版自己已經有 "
            f"{_n} 則(下限 {COVERAGE_FLOORS[_gid]}) —— 那句話已經不成立,"
            "請把它刪掉或改寫成真正還缺的東西(它會印在信的「資料缺口」段)")

    for i, n in enumerate(news):
        where = f"top_news_analysis[{i}]"
        steps = [s for s in (n.get("mechanism_steps") or []) if isinstance(s, dict)]
        if n.get("materiality") == "high" and len(steps) < 2:
            out.append(f"{where} 是高重要性,因果鏈卻只有 {len(steps)} 步 —— "
                       "至少走到「事件 → 營運 → 財務或股價」兩步;"
                       "不確定的步驟標 inference/scenario,不要省略")
        # 第十七輪 P1-7:**兩步連續不等於走到終點。**
        # 「事件 → 市場關注提高 → 投資情緒改善」通得過先前的所有判準,
        # 卻沒有碰到訂單、稼動率、營收、估值或股價的任何一層。
        if n.get("materiality") == "high" and steps:
            import analysis_schema as _sch
            seen = {str(st.get("stage") or "") for st in steps}
            if not (seen & set(_sch.OPERATIONAL_STAGES)):
                out.append(f"{where} 的因果鏈沒有走到營運或產業供需層 —— "
                           "真的走不到就把最後一步標成 sentiment,"
                           "並在 why_this_magnitude 說明它停在敘事驗證")
            if not (seen & set(_sch.TERMINAL_STAGES)):
                out.append(f"{where} 的因果鏈沒有走到營收/毛利/獲利/估值/"
                           "籌碼/股價任何一層 —— **停在情緒不算分析**;"
                           "走不到就明說缺什麼才走得到")
        if (n.get("magnitude_band") in ("negligible", "small", "moderate", "large")
                and not str(n.get("why_this_magnitude") or "").strip()):
            out.append(f"{where} 給了量級卻沒有說為什麼是這個量級")
        # 深度加強(縱向,2026-08-05):**沒有量化錨點的鏈是散文。**
        # 「費半收漲 → 台股電子開盤定價」每一步都合法,而整條鏈沒有
        # 引用任何一個行情數字 —— 讀者無從判斷這個傳導是 0.3% 還是 3%。
        # 高重要性事件的鏈至少要有一步錨在 `market:` / `derived:` /
        # `valuation:` / `prediction:` 的數字上。判準是結構性的
        # (查引用的命名空間),不是關鍵詞。
        if n.get("materiality") == "high" and steps:
            import analysis_stages as _ast
            _reg = _registry_of(packet)
            # 第二十二輪 P2-1:**帶主體的錨點要在這一段的範圍裡** ——
            # 講台積電的鏈不能靠鴻海的漲跌當錨點。
            _subj = {str(a.get("asset_id")) for a in
                     (n.get("affected_assets") or []) if isinstance(a, dict)}
            anchored = any(
                _ast.is_numeric_anchor(e, n.get("source_item_id"), _reg,
                                       subjects=_subj)
                for st in steps for e in (st.get("evidence_ids") or []))
            if not anchored:
                out.append(
                    f"{where} 的因果鏈沒有任何一步引用行情或衍生數字 —— "
                    "至少把一步錨在具體數字上 —— 行情用 market:,"
                    "新聞裡的數字用 fact:(逐則列在 numeric_facts)")
    cms = obj.get("cross_market_synthesis")
    if isinstance(cms, dict):
        # 深度加強(橫向,2026-08-05):**只靠新聞的橫向綜合是轉述,
        # 不是綜合。** 橫向的原料是行情之間的張力與同向(Python 已經
        # 算好放在 packet 裡),綜合段的證據若一個 `market:` / `tension:` /
        # `derived:` 都沒有,它大概率只是把幾則新聞再說一次。
        cited = ([str(e) for e in (cms.get("evidence_ids") or [])]
                 + [str(e) for r in (cms.get("tension_resolutions") or [])
                    if isinstance(r, dict)
                    for e in (r.get("evidence_ids") or [])]
                 + [str(e) for r in (cms.get("alignment_readings") or [])
                    if isinstance(r, dict)
                    for e in (r.get("evidence_ids") or [])])
        if cited and not any(e.startswith(("market:", "tension:", "derived:"))
                             for e in cited):
            out.append(
                "cross_market_synthesis 的證據全是新聞 —— 橫向綜合的原料"
                "是行情之間的張力與同向(EVIDENCE 的 signal_tensions),"
                "沒有接上任何一個行情數字的綜合只是新聞轉述")
        has_content = any(str(v or "").strip() if isinstance(v, str) else v
                          for k, v in cms.items() if k != "evidence_ids")
        if has_content:
            if not [x for x in (cms.get("conflicting_signals") or [])
                    if str(x).strip()]:
                out.append("cross_market_synthesis 沒有列任何互相抵銷的訊號 —— "
                           "確實沒有衝突時要寫一條「今日無明顯互相抵銷的訊號」明講,"
                           "不得留空")
            if not str(cms.get("dominant_driver") or "").strip():
                out.append("cross_market_synthesis 沒有指出今天的主導因子")
            if not str(cms.get("what_would_flip_it") or "").strip():
                out.append("cross_market_synthesis 沒有說什麼情況會讓主導因子失效")
    if len(news) >= 3 and not any((n.get("relates_to") or []) for n in news):
        out.append(f"{len(news)} 則新聞裡沒有任何一則指出與其他條目的關係 —— "
                   "確認它們是否真的全部獨立;**沒有根據的關係不要硬湊**,"
                   "但搶同一段產能或同一個底層驅動的要指出來")
    # 分析面縱深:**與昨日觀點高度重複的敘述(主要+次要),退回加深。**
    # 本體在 `analysis_recap.restatements`(存什麼/比什麼/門檻是同一個
    # 閉環,拆兩處會各自漂移)。`packet` 在舊相容路徑上可以是 ID 集合
    # (見 `_registry_of`)—— 那時沒有事件群,重述檢查整段跳過。
    import analysis_recap as _rc
    out.extend(_rc.restatements(obj, packet if isinstance(packet, dict) else {}))
    # 2026-08-21 實信(特化路徑成功的第一天):十、總經與十之二、政策深析
    # **整段消失** —— FOMC 紀要與 3.9 兆總預算被塞進第九段,
    # macro_environment/taiwan_policy 全空。合法(schema 允許空)但淺:
    # 素材在而段落空白,加深要點名。判準是結構性的(查空欄+素材量)。
    _mac = (obj.get("macro_environment")
            if isinstance(obj.get("macro_environment"), dict) else {})
    _mac_empty = not any(
        str((_mac.get(k) or {}).get("analysis") or "").strip()
        for k in ("us_rates_fx_vix", "fed_policy", "geopolitics")
        if isinstance(_mac.get(k), dict))
    # **觸發要看真的有沒有總經素材**(2026-08-22 外審 P3)。上一版用
    # `_avail >= 10`(當日新聞則數)當代理 —— 12 則全是公司財報的日子
    # 照樣要求補 (A)(B)(C)。更糟的是 MACRO 抓取失敗那天素材根本不在,
    # 而建議仍叫模型「寫今天的增量」:那是在誘導它編造沒有的數字。
    if _mac_empty and _macro_evidence(packet):
        out.append("macro_environment 三個切面全空,而 EVIDENCE 有利率/匯率"
                   "或地緣事件素材 —— (A)(B)(C) 各寫今天的增量並引用"
                   " EVIDENCE 的 ID,真的沒有增量的切面才留空")
    # 公報**逐筆分類**已經有宣告式的判準(`tw_policy_sources` 的關注分類碼,
    # 生產每天都在算「關注 N 筆」)—— 用它,不要用「清單非空」:
    # 公報每個工作日都有東西,大多與資本市場無關。
    import tw_policy_sources as _tps      # 分類碼的宣告在那裡(單一權威)
    _gaz = [r for r in _gazette_records(packet) if _tps.is_focus_record(r)]
    if _gaz and not (obj.get("taiwan_policy") or []):
        out.append(f"taiwan_policy 空白,而 EVIDENCE 有 {len(_gaz)} 筆**關注"
                   "分類**的行政院公報 —— 逐項寫政策深析(source_item_id "
                   "回指來源),確無資本市場影響的才略過")
    return out


#: 「今天真的有總經素材嗎」的判準。**只認宣告過的來源**:利率/匯率那組
#: (`MACRO`/`USDTWD`,(A) 切面的直接素材)與地緣型別的結構化事件
#: ((C) 切面)。認不出來就不催 —— 素材不在還要求寫,等於要模型編。
_MACRO_EVIDENCE_EVENT_TYPES = frozenset({"geopolitical"})


def _has_observation(block) -> bool:
    """這一格是**真的觀測到值**,還是只是一筆失敗紀錄。

    r1(外審):`fetch_macro_indicators` 抓不到時寫的是
    `{"10Y": {"error": "..."}}` —— 整個 MACRO 仍然 truthy。只問「在不在」
    的話,全線斷料那天判準照樣說「有素材」,而那正是這批要消掉的行為
    (素材不在還要求引用 ID = 誘導編造)。
    """
    if isinstance(block, dict):
        if "error" in block:
            return False
        return any(_has_observation(v) for v in block.values())
    return block is not None and block != ""


def _macro_evidence(packet) -> bool:
    mk = (packet or {}).get("market") or {} if isinstance(packet, dict) else {}
    if _has_observation(mk.get("MACRO")) or _has_observation(mk.get("USDTWD")):
        return True
    return any(isinstance(e, dict)
               and str(e.get("event_type") or "") in _MACRO_EVIDENCE_EVENT_TYPES
               for e in (mk.get("STRUCTURED_NEWS_EVENTS") or []))


def _gazette_records(packet) -> list:
    mk = (packet or {}).get("market") or {} if isinstance(packet, dict) else {}
    return [r for r in (mk.get("GAZETTE_RECORDS") or []) if isinstance(r, dict)]


def deepen_input(user_payload: str, advisories: list, previous=None) -> str:
    """加深那一次呼叫的 user 輸入。

    **要把上一版附上去**(第十六輪 P1-8)。先前只說「上一版深度不足」,
    模型看不到自己寫過什麼,只能整份重生 —— 於是可能修好了深度、卻少分析
    一則重要新聞,或把立場改掉。附上前一版並要求**保留已成立的內容**,
    把「重寫」變成「加深」。

    **加深是把已有的證據走完因果鏈,不是編內容** —— 這句話要留在指令裡,
    否則「至少兩步」這種要求本身就會誘發編造。
    """
    prev = ""
    if previous is not None:
        import json as _json
        prev = ("\n<PREVIOUS_OUTPUT>\n"
                + _json.dumps(previous, ensure_ascii=False)
                + "\n</PREVIOUS_OUTPUT>\n")
    return (user_payload + "\n\nDEEPEN\n上一版輸出合法,但深度不足。" + prev
            + "請**保留上一版所有已經成立的內容**(同一批新聞、同一個立場、"
            "同樣的資料缺口),只針對下列各點加深,再輸出**完整** JSON。"
            "唯一的例外是 `gap:other:tech_coverage` / "
            "`gap:other:sector_coverage`:那兩句宣稱的是**這一版自己**"
            "那一段條目不足,補足之後就不再成立 —— 補足了就把它刪掉"
            "(其餘缺口一律保留)。"
            "沒有根據的關係與證據**不得硬湊** —— 加深是把已有的證據"
            "走完因果鏈與量級判斷,不是編造新內容;真的判斷不出量級就選 "
            "unknown 並寫缺哪些資料:\n"
            + "\n".join(f"- {a}" for a in advisories[:6]))


def _claim_fingerprint(c) -> str:
    """一條主張的**全部判斷內容**。ID 不變而內容換掉,是換一份報告。"""
    return ":".join(str(c.get(k) or "") for k in (
        "claim_id", "statement", "claim_type", "direction", "materiality",
        # 第二十一輪 P1-8:`confidence` 先前不在身分裡 ——
        # 0.9 → 0.2 而其餘不變時,第二版照樣可以勝出。
        "confidence", "horizon", "falsification_trigger")) + ":" + ",".join(
        sorted(map(str, c.get("evidence_ids") or []))) + ":" + ",".join(
        sorted(map(str, c.get("asset_scope") or [])))


def _claim_sections(o):
    import claim_map as _cm
    return _cm.section_claim_mappings(o)


def _identity(obj) -> dict:
    """第二版**必須保留**的東西(第十七輪 P1-8)。

    先前只比數量,於是第二版可以:刪掉台積電那則、換一則次要新聞;
    刪掉反證、補一筆重複的支持證據;把「缺訂單金額」換成另一個無關的
    缺口 —— **數量全部持平,實質全部退步**。所以改成比**身分集合**。
    """
    o = obj or {}
    news = [n for n in (o.get("top_news_analysis") or []) if isinstance(n, dict)]
    cms = o.get("cross_market_synthesis") or {}
    return {
        "分析過的新聞": {str(n.get("source_item_id") or "") for n in news},
        "處理過的張力": {str(r.get("tension_id") or "")
                   for r in (cms.get("tension_resolutions") or [])
                   if isinstance(r, dict)},
        # **反證要綁在自己那條 claim 上。** 先前是全域集合,於是
        # 反證可以在 claim A、B 之間互換而集合完全相同。
        "反面證據": {f"{c.get('claim_id')}:{x}"
                 for c in (o.get("claim_audit") or []) if isinstance(c, dict)
                 for x in (c.get("counterevidence_ids") or [])},
        # **涵蓋率缺口不進身分**(2026-08-24 r2 外審):它宣稱的是輸出
        # 自己那一段的形狀,而加深輪的工作正是改變那個形狀。保它的話,
        # 「補了條目就該撤掉那句話」在結構上做不到 —— 模型照建議撤掉,
        # 這裡就判定「第二版弄丟了資料缺口」而沿用含矛盾的第一版。
        # 其餘缺口(真的缺資料)照舊保。
        "資料缺口": {str((g or {}).get("what_is_missing") or "")
                 for g in (o.get("data_gaps") or []) if isinstance(g, dict)
                 and str((g or {}).get("gap_id") or "").strip()
                 not in COVERAGE_GAPS},
        # 2026-08-19(外審):`taiwan_policy` 是 v20 新欄位 —— 不進身分的話,
        # 條數 advisory 觸發的加深可以「補了新聞、刪了政策段」而勝出,
        # 使用者才剛要回來的段落又靜默消失。**內容也要保**(不只 ID):
        # 換一句 impact 就是換了一個結論。
        "政策項": {f"{(t or {}).get('source_item_id')}:"
                f"{(t or {}).get('what')}:{(t or {}).get('impact')}"
                for t in (o.get("taiwan_policy") or []) if isinstance(t, dict)},
        # v21(2026-08-19 第四批):legacy 骨架的每一段都要保 —— 不保的話,
        # 條數 advisory 觸發的加深可以「補了新聞、刪了世界大事/在地動態」
        # 而勝出。內容也保(換一句就是換一個結論)。
        # **身分要含所有會改變渲染內容或可見性的欄位**(外審 2026-08-19
        # 第二輪):只保 ID/標題的話,加深版本可以保留識別欄位、
        # **清空渲染必要的欄位**(impact / evidence_today / 情境內文)——
        # renderer 要求那些欄位非空才排,整段就靜默消失。
        "世界大事": {f"{(w or {}).get('source_item_id')}:{(w or {}).get('what')}:"
                 f"{(w or {}).get('why_it_matters')}"
                 for w in (o.get("world_events") or []) if isinstance(w, dict)},
        "在地動態": {f"{(t or {}).get('source_item_id')}:{(t or {}).get('what')}:"
                 f"{(t or {}).get('impact')}"
                 for t in (o.get("taiwan_local") or []) if isinstance(t, dict)},
        "情境事件": {":".join(str((e or {}).get(k) or "") for k in
                          ("when", "event", "base_expectation", "bull_case",
                           "bear_case", "most_affected", "invalidation"))
                 for e in (o.get("upcoming_event_scenarios") or [])
                 if isinstance(e, dict)},
        "敘事變化": {f"{(d or {}).get('prior_view_id')}:"
                 f"{(d or {}).get('prior_view')}:{(d or {}).get('change')}:"
                 f"{(d or {}).get('evidence_today')}:"
                 + ",".join(sorted(str(x) for x in
                                   ((d or {}).get("evidence_ids") or [])))
                 for d in (o.get("narrative_delta") or []) if isinstance(d, dict)},
        # v22(repo-wide 外審 2026-08-19 P2):**加深不得改寫或清空總經三格**
        # —— 先前 identity 沒有它,第二版把 (A)(B)(C) 全清空或改成相反結論,
        # 只要其他集合沒少照樣勝出,而 renderer 用的就是第二版。
        "總經": {(lambda _sec: f"{k}:{_sec.get('analysis') or ''}:"
                 + ",".join(sorted(str(x) for x in
                                   (_sec.get("evidence_ids") or []))))(
                     (o.get("macro_environment") or {}).get(k)
                     if isinstance((o.get("macro_environment") or {}).get(k),
                                   dict) else {})
               for k in ("us_rates_fx_vix", "fed_policy", "geopolitics")
               # r3(P2-3):**只保護 before 已成立的內容** —— 空切面不進
               # 身分,否則「空 → 加深補出內容」會因為 `fed_policy::` 這個
               # 空標記消失而被誤擋,而加深的目的正是把不足補完整。
               # r4 F1:判準與 has_content 一致 —— **只有證據沒有文字
               # 也是空**(evidence-only 進身分的話,加深補文字換證據會被
               # 誤擋)。收進身分之後證據仍在指紋裡(上面的 f-string)。
               if str((((o.get("macro_environment") or {}).get(k) or {})
                       if isinstance((o.get("macro_environment") or {})
                                     .get(k), dict) else {})
                      .get("analysis") or "").strip()},
        # 第十九輪 P1-11:**第二版可以「更深」而同時刪掉橫向與逐標的。**
        # 先前只保護新聞、張力、反證、缺口四個集合,於是「多一個財務層
        # 步驟、刪掉台積電與指數的差異分析、刪掉全部同向解讀、刪掉
        # claim 回指」會因為鏈變長而勝出 —— 加深反而讓信變淺。
        # 第二十輪 P1-4:**只保名字保不住結論。** `n1:2330` 不變而方向
        # bullish→bearish、量級 moderate→negligible —— ID 集合完全相同,
        # 第二版照樣勝出。**加深是把同一個判斷說得更清楚,不是換判斷**,
        # 所以身分要含方向/量級/時間 —— 改任何一格都是換了一個結論。
        # 第二十一輪 P1-8:`second_order_effect` 也會渲染進信 ——
        # 改成相反方向而其餘不變時,先前完全看不出來。
        "拆過的標的": {f"{n.get('source_item_id')}:{a.get('asset_id')}:"
                  f"{a.get('direction')}:{a.get('magnitude_band')}:"
                  f"{a.get('horizon')}:{a.get('first_order_effect')}:"
                  f"{a.get('second_order_effect')}:"
                  f"{','.join(sorted(map(str, a.get('evidence_ids') or [])))}"
                  for n in news for a in (n.get("affected_assets") or [])
                  if isinstance(a, dict) and a.get("asset_id")},
        "解讀過的同向訊號": {f"{r.get('alignment_id')}:{r.get('interpretation')}:"
                     f"{r.get('marginal_information')}:"
                     f"{r.get('double_count_risk')}:"
                     f"{','.join(sorted(map(str, r.get('evidence_ids') or [])))}"
                     for r in (cms.get("alignment_readings") or [])
                     if isinstance(r, dict)},
        # 矛盾那側同理:哪一側可信、憑什麼分出勝負、靠什麼證據 ——
        # 全部換掉而 tension_id 不變時,先前完全看不出來。
        "調和過的張力": {f"{r.get('tension_id')}:{r.get('dominant_side')}:"
                   f"{r.get('resolution')}:{r.get('decision_rule')}:"
                   f"{','.join(sorted(map(str, r.get('evidence_ids') or [])))}"
                   for r in (cms.get("tension_resolutions") or [])
                   if isinstance(r, dict)},
        # 同理:claim 的身分含**內容**。第二十輪 P1-4:先前只含
        # 本文/尺度/範圍 —— 於是 `direction` bullish→bearish、
        # `evidence_ids` 換成另一個合法但不相關的 ID、反證整組搬到
        # 另一條 claim 上,身分集合完全不變。**加深是把同一個判斷說得
        # 更清楚,不是換一個判斷。**
        "稽核過的主張": {_claim_fingerprint(c)
                   for c in (o.get("claim_audit") or [])
                   if isinstance(c, dict) and c.get("claim_id")},
        # 第二十輪 P2-5:**從 `claim_map` 長出來。** 先前寫死三段 + 手動補
        # 總結,於是 schema 新增的 scenario / watch / key_driver 回指
        # 可以被整批換掉而不被發現 —— 而寫死的清單漂移一次就再也對不回來。
        "各段的回指": {f"{sec}:{cid}" for sec, ids in
                  _claim_sections(o).items() for cid in ids},
        "因果步驟的證據": {f"{n.get('source_item_id')}:{e}"
                    for n in news for st in (n.get("mechanism_steps") or [])
                    if isinstance(st, dict)
                    for e in (st.get("evidence_ids") or [])},
        "條目之間的關係": {f"{n.get('source_item_id')}→"
                    f"{r.get('other_source_item_id')}"
                    for n in news for r in (n.get("relates_to") or [])
                    if isinstance(r, dict)},
        # 第二十三輪 P1-9:**首屏的三條與 Commit D 的新段落先前都不在
        # 身分裡** —— 加深可以改寫三大重點、翻轉淨效果方向、刪掉共同
        # 驅動警語、換掉 dismissed 的理由,而選優完全看不見。
        # 第二十四輪 P1-10:**這四格先前只保護了一部分可見欄位。**
        # 加深因此可以在「集合存在」不變的情況下改變信的語意:調高重點的
        # confidence、刪掉它的反證與失效條件、把淨效果的 claim 根據與
        # 抵銷事件換掉、改寫共同驅動指向的事件群、抽掉駁回的證據與回頭條件。
        # 這些全部會渲染進信 —— **可見的東西都要在身分裡**。
        "三大重點": {f"{d.get('cluster_id')}:{d.get('statement')}:"
                 f"{d.get('direction')}:{d.get('materiality')}:"
                 f"{d.get('horizon')}:{d.get('confidence')}:"
                 f"{d.get('falsification_trigger')}:"
                 f"{','.join(sorted(map(str, d.get('counterevidence_ids') or [])))}:"
                 f"{','.join(sorted(map(str, d.get('claim_ids') or [])))}:"
                 f"{','.join(sorted(map(str, d.get('evidence_ids') or [])))}"
                 for d in (o.get("key_drivers") or []) if isinstance(d, dict)},
        "逐標的淨效果": {f"{x.get('asset_id')}:{x.get('net_direction')}:"
                   f"{x.get('net_magnitude_band')}:{x.get('why')}:"
                   f"{','.join(sorted(map(str, x.get('offsetting_cluster_ids') or [])))}:"
                   f"{','.join(sorted(map(str, x.get('claim_ids') or [])))}"
                   for x in (o.get("asset_net_effects") or [])
                   if isinstance(x, dict)},
        "共同驅動說明": {f"{x.get('driver')}:{x.get('why_not_double_counted')}:"
                   f"{','.join(sorted(map(str, x.get('cluster_ids') or [])))}"
                   for x in (cms.get("shared_driver_notes") or [])
                   if isinstance(x, dict)},
        "駁回的事件": {f"{x.get('cluster_id')}:{x.get('why_not_material')}:"
                  f"{x.get('revisit_trigger')}:"
                  f"{','.join(sorted(map(str, x.get('supporting_evidence_ids') or [])))}"
                  for x in (o.get("dismissed_events") or [])
                  if isinstance(x, dict)},
    }


#: **每則新聞自己的身分**(第十八輪 P1-11)。上一版只比新聞 ID 的集合,
#: 於是這個交換完全合法:
#:
#:     第一版:n1 = high 但鏈很淺、n2 = medium 但鏈很深
#:     第二版:n1 = medium 仍然很淺、n2 = high 已經很深
#:
#: 新聞 ID 集合不變、high 的**數量**不變、深度提示還會變少 ——
#: 而真正被要求加深的 n1 是**靠降級逃掉的**。深度要求不能用重新分類繞過。
_MATERIALITY_RANK = {"low": 0, "medium": 1, "high": 2}

#: 說得出來的東西**不得在加深後說不出來**。這些欄位由有變無,是實質退步,
#: 而它會讓報告看起來更乾淨(少了一堆但書)—— 最難察覺的那種。
#: 第二十一輪 P1-8:`source_caveat`(單一來源的保留事項)與
#: `why_it_matters` 都會渲染進信,先前不在保護範圍內。
_NEWS_KEPT = ("horizon", "confirmation_signal", "invalidation_signal",
              "why_this_magnitude", "source_caveat", "why_it_matters")

#: 立場信心的單次漂移上限。加深是把同一個判斷說得更清楚,不是換一個判斷 ——
#: 0.35 → 0.95 不可能是「補了幾條因果鏈」帶來的。**本模組自訂。**
_CONFIDENCE_DRIFT = 0.25

#: 佐證等級由弱到強。加深**不得往上調** —— 讓讀者高估可信度。
_CORROBORATION_RANK = {"unverified": 0, "single_source": 1,
                       "multi_source": 2, "official": 3}


def _news_identity(obj) -> dict:
    """`{source_item_id: {重要性, 量級已知, 說得出來的欄位}}`。"""
    out = {}
    for n in ((obj or {}).get("top_news_analysis") or []):
        if not isinstance(n, dict):
            continue
        out[str(n.get("source_item_id") or "")] = {
            "materiality": str(n.get("materiality") or ""),
            "direction": str(n.get("direction") or ""),
            "corroboration": str(n.get("corroboration_assessment") or ""),
            "magnitude_known": n.get("magnitude_band") not in (None, "", "unknown"),
            "said": {k for k in _NEWS_KEPT if str(n.get(k) or "").strip()},
        }
    return out


def news_regressions(before, after) -> list:
    """第二版在**個別新聞**上的退步(集合層看不見的那種)。"""
    ib, ia = _news_identity(before), _news_identity(after)
    bad = []
    for sid, b in ib.items():
        a = ia.get(sid)
        if a is None:            # 整則不見 —— 由集合層的「弄丟新聞」負責
            continue
        # 佐證等級不得被往上調(那會讓讀者高估可信度)。
        if b.get("corroboration") and a.get("corroboration") and                 _CORROBORATION_RANK.get(a["corroboration"], 0) >                 _CORROBORATION_RANK.get(b["corroboration"], 0):
            bad.append(f"{sid} 的佐證等級被調高("
                       f"{b['corroboration']} → {a['corroboration']})")
        if b.get("direction") and a.get("direction")                 and b["direction"] != a["direction"]:
            bad.append(f"{sid} 的方向被改掉({b['direction']} → {a['direction']})"
                       " —— 加深不該改判斷")
        rb = _MATERIALITY_RANK.get(b["materiality"], -1)
        ra = _MATERIALITY_RANK.get(a["materiality"], -1)
        if rb >= 0 and ra >= 0 and ra < rb:
            bad.append(f"{sid} 的重要性被降級({b['materiality']} → "
                       f"{a['materiality']})—— 深度要求不得用重新分類繞過")
        if b["magnitude_known"] and not a["magnitude_known"]:
            bad.append(f"{sid} 的量級從說得出來變成 unknown")
        lost = b["said"] - a["said"]
        if lost:
            bad.append(f"{sid} 不再說得出 {sorted(lost)}")
    return bad


#: 加深**不得順手改掉**的判斷欄位。它們不是深度,改了就是換一份報告。
_PINNED = (("stance", "label"), ("stance", "time_horizon"),
           ("cross_market_synthesis", "dominant_driver"))


def _dominance(obj) -> dict:
    """比較兩版用的**可數面向**。只數結構,不評文字品質。

    刻意回一組數字而不是一個總分:合成之後,「深度 +3、證據 -2」
    會看起來像進步。
    """
    news = [n for n in ((obj or {}).get("top_news_analysis") or [])
            if isinstance(n, dict)]
    cms = (obj or {}).get("cross_market_synthesis") or {}
    ev = sum(len((st or {}).get("evidence_ids") or [])
             for n in news for st in (n.get("mechanism_steps") or []))
    return {
        "news_items": len(news),
        "high_materiality": sum(1 for n in news if n.get("materiality") == "high"),
        # **涵蓋率缺口不計入**(2026-08-24 r2 外審):這個面向是「揭露有沒有
        # 變少」,而涵蓋率缺口宣稱的是輸出自己那一段的形狀 —— 補足條目之後
        # 撤掉它是**修正**,不是少揭露一件事。連同 `_identity` 的豁免,
        # 三道守衛(數量/身分/面向)要一起放行,少放一道就仍然撤不掉。
        "data_gaps": sum(
            1 for g in ((obj or {}).get("data_gaps") or [])
            if isinstance(g, dict)
            and str(g.get("gap_id") or "").strip() not in COVERAGE_GAPS),
        "step_evidence": ev,
        "addressed_tensions": len(cms.get("tension_resolutions") or []),
        "counterevidence": sum(len((c or {}).get("counterevidence_ids") or [])
                               for c in ((obj or {}).get("claim_audit") or [])),
    }


def deepen_is_an_improvement(before, after, *, evidence_ids) -> tuple:
    """第二版**是不是真的比較好**(第十六輪 P1-8)。回 `(bool, 理由)`。

    先前只要第二版合法就採用 —— 而加深那次是**整份重生**,可能修好深度
    卻少分析一則新聞、改掉立場、刪掉資料缺口。
    **「一個修正可能比原本的缺陷更糟」正是這個 repo 反覆栽的形狀**,
    而這一次是我自己寫進去的。

    判準逐項檢查、任一條不成立就留第一版:合法、深度提示要**減少**、
    立場不得漂移、每個可數面向都不得退步。
    """
    if not isinstance(after, dict):
        return False, "第二版不是物件"
    from analysis_validate import validate   # 延遲:避免循環
    # **要傳完整 packet**(第十七輪 P1-8):只傳 ID 集合的話,
    # 「必須處理的張力」「有新聞卻沒分析」這些 packet-aware 規則
    # 在這裡整個不會跑 —— 而那正是第二版最可能退步的地方。
    problems = validate(after, evidence_ids)
    if problems:
        return False, f"第二版不合法({problems[0][:40]})"
    # **選優的判準要與觸發加深的判準是同一套**(第二十二輪 P2-1 順帶抓到)。
    # 上面三行剛講完「傳 packet 不是 ids」,而下面這一行自己沒傳 ——
    # 於是觸發加深的是 `depth_advisories(obj, packet)`(含錨點、橫向這些
    # packet-aware 提示),而選優數的是不含它們的那一套。第二版**剛好把
    # packet-aware 的那幾條修好**時,盲測的數量沒有變少 → 判定沒有改善 →
    # 把真正的改善丟掉,沿用第一版。
    # **矛盾要清掉才算改善**(2026-08-24 r2 外審)。排在數量比較**之前**:
    # 留著矛盾的第二版有時剛好讓總數持平,那時它會被「提示沒有減少」擋掉 ——
    # 結果對、訊息卻指向錯的原因,而處置不同(補深度 vs 撤掉那句話)。
    _bad = coverage_gap_faults(before, after, evidence_ids)
    if _bad:
        return False, _bad[0]
    adv_b = depth_advisories(before, evidence_ids)
    adv_a = depth_advisories(after, evidence_ids)
    if len(adv_a) >= len(adv_b):
        return False, f"深度提示沒有減少({len(adv_b)} → {len(adv_a)})"
    sb = str(((before or {}).get("stance") or {}).get("label") or "")
    sa = str(((after or {}).get("stance") or {}).get("label") or "")
    if sb and sa and sb != sa:
        return False, f"立場漂移({sb} → {sa}) —— 加深不該改變判斷"
    # **身分保存**:數量持平但內容被換掉,是最難察覺的退步。
    ib, ia = _identity(before), _identity(after)
    for name in ib:
        lost = ib[name] - ia[name]
        if lost:
            return False, f"第二版弄丟了{name}:{sorted(lost)[:3]}"
    for msg in news_regressions(before, after):
        return False, msg
    cb = ((before or {}).get("stance") or {}).get("confidence")
    ca = ((after or {}).get("stance") or {}).get("confidence")
    if isinstance(cb, (int, float)) and isinstance(ca, (int, float))             and abs(float(ca) - float(cb)) > _CONFIDENCE_DRIFT:
        return False, (f"立場信心漂移過大({cb} → {ca})—— 加深是把同一個"
                       "判斷說得更清楚,不是換一個判斷")
    for block, field in _PINNED:
        vb = str(((before or {}).get(block) or {}).get(field) or "")
        va = str(((after or {}).get(block) or {}).get(field) or "")
        if vb and va and vb != va:
            return False, f"{block}.{field} 被改掉({vb} → {va}) —— 加深不該改判斷"
    # 立場分與信心可以微調,但**大幅漂移**代表它重寫了判斷而不是加深。
    sb = ((before or {}).get("stance") or {}).get("score")
    sa = ((after or {}).get("stance") or {}).get("score")
    if isinstance(sb, int) and isinstance(sa, int) and abs(sa - sb) > 2:
        return False, f"立場分大幅改變({sb:+d} → {sa:+d})"
    db, da = _dominance(before), _dominance(after)
    worse = [k for k in db if da[k] < db[k]]
    if worse:
        return False, "這些面向退步了:" + "、".join(
            f"{k} {db[k]}→{da[k]}" for k in worse)
    return True, f"深度提示 {len(adv_b)} → {len(adv_a)}"


# ---------------------------------------------------------------- 相容出口
#
# 階段/指標搬到 `analysis_stages`(見該檔:**後果不同**)。呼叫端仍可從
# 這裡取用,一次只改一件事。
from analysis_stages import (                     # noqa: E402,F401
    both_sides_cited, depth_metrics, incomplete_chains)
