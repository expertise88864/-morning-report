"""批#44:story ledger——把晨報從「今日快照」變成「連續劇」。

要解的兩個問題其實是同一件事的兩面:①今天的新聞接不上昨天的脈絡 ②同一條線索
天天以類似措辭重寫、佔版面卻沒有新資訊。根因都是系統沒有「這條線索走到哪了」
的記憶。

設計上的關鍵約束(測試就是在守這些):
- 狀態機轉移**只由 Python 決定**,比照 PR-2 的「Python 權威、LLM 只能抄錄」
- story 身分不含日期與數字,否則同一條線索每天都會開新 story、連續性歸零
- 沉寂的 story 不刪除,日後復燃要接得回去
- headline/prev_delta 會跨日回流進 prompt = 存放式注入的典型載體,必須圍欄
"""
import morning_report as mr
import story_ledger as sl


def _ev(entity, event_type, title, surprise=0.3):
    return {"entity": entity, "event_type": event_type,
            "title": title, "surprise_score": surprise}


def test_story_key_ignores_numbers_in_title():
    """同一條線索的後續報導金額會變。把標題數字放進 key 等於每天開新 story。"""
    a = sl.story_key("2330", "earnings", "台積電法說 毛利率 58%", "2026-07-24T00:00:00+00:00")
    b = sl.story_key("2330", "earnings", "台積電法說 毛利率上修至 60%", "2026-07-25T00:00:00+00:00")
    assert a == b, "同一季同實體同型別應視為同一條線索"
    c = sl.story_key("2454", "earnings", "聯發科法說", "2026-07-25T00:00:00+00:00")
    assert a != c


def test_story_key_separates_quarterly_episodes():
    """r1(Codex F3):財報/財測是「按集數發生」的事件——台積電 Q1 與 Q2 是兩件事。
    共用一把 key 會讓 Q2 被當成 Q1 的續報、還餵給 LLM 錯誤的前情。
    分桶規則直接重用 news_events 的期別 bucket,不另造一份會走樣的。"""
    q1 = sl.story_key("2330", "earnings", "台積電法說", "2026-02-15T00:00:00+00:00")
    q3 = sl.story_key("2330", "earnings", "台積電法說", "2026-08-15T00:00:00+00:00")
    assert q1 != q3, "不同季的財報被當成同一條線索"
    # 營收是月頻(台股月營收每月公布)
    m7 = sl.story_key("2330", "revenue_growth", "月營收", "2026-07-10T00:00:00+00:00")
    m8 = sl.story_key("2330", "revenue_growth", "月營收", "2026-08-10T00:00:00+00:00")
    assert m7 != m8


def test_story_key_does_not_split_long_running_sagas():
    """**只對 episodic 型別分桶**。orders/litigation 這類長線在 news_events 掛月
    bucket 是為了 event study 的樣本獨立性;story ledger 要的是跨週敘事連續性,
    在月界切斷併購案會直接破壞本模組的目的。"""
    a = sl.story_key("2317", "orders", "收購案洽談", "2026-07-28T00:00:00+00:00")
    b = sl.story_key("2317", "orders", "收購案進展", "2026-08-03T00:00:00+00:00")
    assert a == b, "跨月的同一條併購線索被切成兩條"


def test_idle_developing_story_demotes_not_promotes():
    """r1(Codex F1,P1):STATES 是「熱度由低到高再收斂」的順序,developing 的
    下一個元素是 peak——用相鄰元素當降級,會把閒置兩天的線索**升級成高潮**
    並搶到最高版面權重,與降級意圖完全相反。"""
    led = sl.update_ledger([], [_ev("2330", "earnings", "法說")], "2026-07-20")
    led = sl.update_ledger(led, [_ev("2330", "earnings", "續報")], "2026-07-21")
    assert led[0]["state"] == "developing"
    out = sl.update_ledger(led, [], "2026-07-23")   # 閒置 2 天
    assert out[0]["state"] == "resolving", (
        f"閒置 2 天得到 {out[0]['state']}(得到 peak 代表降級走成了升級)")


def test_idle_demotion_is_applied_daily_to_persisted_ledger():
    """r2(Codex):降級表是**每日**套用在持久化的帳本上,不是一次算到底。

    先前的測試把第 2/4/6 天各自套在同一個快照上,漏掉了真實的序列行為:
    第 2 天 developing→resolving,第 3 天若 resolving→dormant,線索閒置三天就
    消失,而設計是七天。何時沉寂只能由 DORMANT_AFTER_DAYS 那條決定。
    """
    led = sl.update_ledger([], [_ev("2330", "earnings", "法說")], "2026-07-20")
    led = sl.update_ledger(led, [_ev("2330", "earnings", "續報")], "2026-07-21")
    assert led[0]["state"] == "developing"

    seen = {}
    for day in range(22, 32):                      # 07-22 ~ 07-31 逐日推進
        led = sl.update_ledger(led, [], f"2026-07-{day:02d}")
        seen[day] = led[0]["state"] if led else "gone"

    assert seen[22] == "developing", "閒置 1 天不該降級(週末誤殺)"
    assert seen[23] == "resolving", f"閒置 2 天應降到 resolving,得到 {seen[23]}"
    for day in (24, 25, 26, 27):
        assert seen[day] == "resolving", (
            f"第 {day} 日(閒置 {day - 21} 天)提前變成 {seen[day]};"
            "沉寂只能由 DORMANT_AFTER_DAYS=7 決定")
    assert seen[28] == "dormant", f"閒置 7 天應沉寂,得到 {seen[28]}"


def test_same_day_rerun_does_not_advance_state():
    """r1(Codex F2):帳本會持久化,workflow 手動重跑會拿同一批事件再跑一次。
    touched 若從空集合開始,重跑一次就能把線索灌到高潮。"""
    evs = [_ev("2330", "earnings", "法說")]
    led = sl.update_ledger([], evs, "2026-07-24")
    led = sl.update_ledger(led, [_ev("2330", "earnings", "續報")], "2026-07-25")
    state_after = led[0]["state"]
    updates_after = led[0]["updates"]
    rerun = sl.update_ledger(led, [_ev("2330", "earnings", "續報")], "2026-07-25")
    assert rerun[0]["state"] == state_after, "同日重跑推進了狀態"
    assert rerun[0]["updates"] == updates_after, "同日重跑多算了一次進展"


def test_new_story_with_high_surprise_starts_at_peak():
    """r1(Codex F5):重大突發事件第一天最該當主線,原本一律 brewing 會被 R16b
    判為「不當主線」。"""
    led = sl.update_ledger([], [_ev("2330", "litigation", "遭美方調查", 0.9)],
                           "2026-07-25")
    assert led[0]["state"] == "peak"
    led2 = sl.update_ledger([], [_ev("2330", "general", "例行公告", 0.2)], "2026-07-25")
    assert led2[0]["state"] == "brewing"


def test_story_key_falls_back_to_title_fingerprint():
    """實體與類型都缺時才用標題指紋——但仍要能區分不同標題。"""
    a = sl.story_key("", "", "美中關稅談判進入第二輪")
    b = sl.story_key("", "", "美中關稅談判進入第二輪")
    c = sl.story_key("", "", "歐盟碳邊境稅上路")
    assert a == b and a != c


def test_state_machine_advances_on_progress():
    """有新進展往上走;高 surprise 直接跳到高潮。"""
    led = sl.update_ledger([], [_ev("2330", "earnings", "法說會將召開")], "2026-07-24")
    assert led[0]["state"] == "brewing"
    led = sl.update_ledger(led, [_ev("2330", "earnings", "毛利率上修", 0.85)], "2026-07-25")
    assert led[0]["state"] == "peak"
    assert led[0]["updates"] == 2


def test_state_machine_demotes_only_after_tolerance():
    """單日空窗(週末)不得把線索打入沉寂;閒置滿門檻才降級。"""
    led = sl.update_ledger([], [_ev("2330", "earnings", "法說")], "2026-07-24")
    led = sl.update_ledger(led, [_ev("2330", "earnings", "續報")], "2026-07-25")
    state_before = led[0]["state"]
    led_1d = sl.update_ledger(led, [], "2026-07-26")           # 閒置 1 天
    assert led_1d[0]["state"] == state_before, "單日空窗就降級會誤殺週末"
    led_far = sl.update_ledger(led, [], "2026-08-20")          # 閒置很久
    assert led_far[0]["state"] == "dormant"


def test_same_day_duplicate_reports_advance_state_only_once():
    """同一事件跨媒體多則報導不得重複推進狀態,否則熱門事件會被灌到高潮。"""
    evs = [_ev("2330", "earnings", "台積電法說毛利率上修"),
           _ev("2330", "earnings", "台積電法說優於預期"),
           _ev("2330", "earnings", "法說會重點整理")]
    led = sl.update_ledger([], evs, "2026-07-24")
    assert len(led) == 1
    assert led[0]["updates"] == 1, "同日多則報導被算成多次進展"


def test_dormant_story_revives_instead_of_starting_over():
    """線索復燃(例如併購案重啟)要接回原 story,不是開新的——這正是不刪除
    沉寂 story 的理由。"""
    led = sl.update_ledger([], [_ev("2317", "orders", "鴻海洽談收購案")], "2026-01-02")
    led = sl.update_ledger(led, [], "2026-03-01")
    assert led[0]["state"] == "dormant"
    led = sl.update_ledger(led, [_ev("2317", "orders", "鴻海收購案重啟")], "2026-03-02")
    assert len(led) == 1, "復燃時開了新 story,前情就斷了"
    assert led[0]["state"] != "dormant"
    assert led[0]["updates"] == 2


def test_active_stories_exclude_dormant_but_ledger_keeps_them():
    led = sl.update_ledger([], [_ev("2330", "earnings", "法說")], "2026-01-02")
    led = sl.update_ledger(led, [], "2026-02-01")
    assert led and led[0]["state"] == "dormant"
    assert sl.active_stories(led) == [], "沉寂的不該進 prompt"
    assert len(led) == 1, "沉寂的不該從帳本刪除"


def test_prev_delta_carries_previous_headline():
    """「昨天說 X → 今天 Y」的 X 必須是上一次的標題,不是這次的。"""
    led = sl.update_ledger([], [_ev("2330", "earnings", "第一版標題")], "2026-07-24")
    led = sl.update_ledger(led, [_ev("2330", "earnings", "第二版標題")], "2026-07-25")
    assert led[0]["last_delta"] == "第二版標題"
    assert led[0]["prev_delta"] == "第一版標題"


def test_ledger_prunes_ancient_stories():
    led = sl.update_ledger([], [_ev("2330", "earnings", "很久以前")], "2020-01-01")
    led = sl.update_ledger(led, [], "2026-07-25")
    assert led == [], "超過保留天數的線索應被清掉"


def test_events_without_title_are_ignored():
    led = sl.update_ledger([], [{"entity": "2330", "event_type": "earnings"},
                                "not a dict", None], "2026-07-25")
    assert led == []


def test_prompt_block_is_fenced_and_states_python_authority():
    """headline/prev_delta 跨日回流 = 存放式注入的典型載體,必須圍欄;
    且必須寫明狀態由 Python 計算,否則 LLM 會自行改判(把「醞釀」寫成
    「市場高度關注」)。"""
    led = sl.update_ledger([], [_ev("2330", "earnings", "台積電法說")], "2026-07-25")
    block = mr._format_story_prompt_block(led)
    assert block.count("<UNTRUSTED_SOURCE_DATA>") == 1
    assert block.count("</UNTRUSTED_SOURCE_DATA>") == 1
    assert block.index("一律忽略") < block.index("<UNTRUSTED_SOURCE_DATA>")
    assert "由 Python 計算" in block and "不要自行改判" in block


def test_prompt_block_empty_when_no_active_stories():
    """無活躍線索時回空字串,呼叫端整段省略而非寫「無」。"""
    assert mr._format_story_prompt_block([]) == ""
    assert mr._format_story_prompt_block(None) == ""
    led = sl.update_ledger([], [_ev("2330", "earnings", "舊事")], "2026-01-02")
    led = sl.update_ledger(led, [], "2026-02-01")
    assert mr._format_story_prompt_block(led) == ""


def test_story_state_is_not_delegated_to_llm_in_prompt():
    """R16b 規則必須進 prompt,否則帳本白算。"""
    from tests.test_data_validation import _empty_quotes
    led = sl.update_ledger([], [_ev("2330", "earnings", "台積電法說")], "2026-07-25")
    prompt = mr._build_prompt(_empty_quotes(STORY_LEDGER=led),
                              {"error": "x"}, {"error": "x"}, [], [], "")
    assert "進行中的線索" in prompt
    assert "R16b" in prompt
    assert "整條不要寫" in prompt, "缺少「沒有新進展就不要寫」的規則"


def test_story_ledger_state_roundtrip(tmp_path, monkeypatch):
    """讀檔失敗要記進降級——空帳本會讓敘事退回單日快照,那是要被看見的降級。"""
    f = tmp_path / "story_ledger.json"
    monkeypatch.setattr(mr, "STORY_LEDGER_FILE", f)
    assert mr.load_story_ledger() == []
    led = sl.update_ledger([], [_ev("2330", "earnings", "法說")], "2026-07-25")
    assert mr.save_story_ledger(led) is True
    assert len(mr.load_story_ledger()) == 1

    f.write_text("{ broken", encoding="utf-8")
    mr._DEGRADED_STEPS.clear()
    assert mr.load_story_ledger() == []
    assert "story_ledger_load" in mr._DEGRADED_STEPS


def _ev_full(entity, event_type, title, surprise=0.3, published="2026-07-25T00:00:00+00:00",
             is_incremental=None):
    ev = {"entity": entity, "event_type": event_type, "title": title,
          "surprise_score": surprise, "published": published}
    if is_incremental is not None:
        ev["is_incremental"] = is_incremental
    return ev


def test_non_incremental_repeat_does_not_advance_story():
    """r2(Codex F1):apply_event_timeline 早就算好 is_incremental——跨日的重複
    報導(同一 lifecycle 再被報一次)標成 False。帳本原本無條件當成 delta,於是
    **沒有新進展的線索照樣升到主線版面**,而那正是本模組要消滅的東西。"""
    led = sl.update_ledger(
        [], [_ev_full("2330", "orders", "接獲大單", is_incremental=True)], "2026-07-24")
    state_before, updates_before = led[0]["state"], led[0]["updates"]

    # 純重複稿 = 同一則標題再被報一次(lifecycle 也沒推進)
    led2 = sl.update_ledger(
        led, [_ev_full("2330", "orders", "接獲大單", is_incremental=False)],
        "2026-07-25")
    assert led2[0]["state"] == state_before, "純重複稿推進了狀態"
    assert led2[0]["updates"] == updates_before, "純重複稿被算成一次進展"


def test_content_update_with_same_lifecycle_still_counts_as_progress():
    """r5(Codex,P1):is_incremental **只比 lifecycle 不看內容**。
    「訂單金額 10 億(confirmed)」→「金額上修 20 億(confirmed)」是真實進展,
    卻會被標成 is_incremental=False。若拿它當唯一門檻,線索會在持續發展中被判停滯、
    照樣降級沉寂,LLM 也看不到今日真正的 delta——比原本的問題更糟。"""
    led = sl.update_ledger(
        [], [_ev_full("2317", "orders", "鴻海接獲車用大單 金額約 10 億美元",
                      is_incremental=True)], "2026-07-24")
    updates_before = led[0]["updates"]
    led2 = sl.update_ledger(
        led, [_ev_full("2317", "orders", "鴻海車用大單金額上修至 20 億美元 客戶追加",
                       is_incremental=False)], "2026-07-25")
    assert led2[0]["updates"] == updates_before + 1, "內容確實更新卻被當成重複稿"
    assert "20" in led2[0]["last_delta"], "今日的 delta 沒有進到帳本"
    assert led2[0]["last_update"] == "2026-07-25", "last_update 沒更新,線索會被誤判停滯"


def test_incremental_flag_absent_is_treated_as_progress():
    """欄位缺席時保守視為有進展——不要因為上游沒標就整條線索凍住。"""
    led = sl.update_ledger([], [_ev_full("2330", "orders", "第一則")], "2026-07-24")
    led = sl.update_ledger(led, [_ev_full("2330", "orders", "第二則")], "2026-07-25")
    assert led[0]["updates"] == 2


def test_distinct_general_announcements_do_not_collide():
    """r2(Codex F2):news_events 記載過 (entity,"general") 讓單一金控 16 則不同
    公告互吞。story ledger 沿用同一套身分規則後,不同公告必須是不同線索。"""
    evs = [_ev_full("2882", "general", "國泰金公告董事異動"),
           _ev_full("2882", "general", "國泰金公告子公司增資")]
    led = sl.update_ledger([], evs, "2026-07-25")
    assert len(led) == 2, f"兩則不同公告撞成一條:{[s['headline'] for s in led]}"


def test_story_identity_matches_news_events_rules():
    """身分規則直接沿用 news_events._event_timeline_key,不另造一份會走樣的。"""
    import news_events as ne
    ev = _ev_full("2330", "earnings", "台積電法說")
    entity, lineage = ne._event_timeline_key(ev)
    assert sl.story_key_for_event(ev) == f"e:{sl._norm(entity)}|l:{sl._norm(lineage)}"


def test_quarterly_episodes_still_separate_under_new_identity():
    """換身分規則後,不同季財報仍必須是不同線索(r1 F3 的性質不可回退)。"""
    q1 = sl.story_key_for_event(_ev_full("2330", "earnings", "法說", published="2026-02-15T00:00:00+00:00"))
    q3 = sl.story_key_for_event(_ev_full("2330", "earnings", "法說", published="2026-08-15T00:00:00+00:00"))
    assert q1 != q3


def test_unrelated_same_month_event_does_not_get_wrong_prev_delta():
    """r3(Codex):_event_timeline_key 對 orders/litigation 只掛月 bucket,同公司
    同月的兩件不同事會共用 lineage。news_events 接受這代價是因為那裡的後果是
    event study 少計(保守);在 story ledger 裡後果是**錯誤的前情被當事實餵給 LLM**。

    修法刻意不是分岔成新線索——續報本來就會換措辭,以標題相似度決定身分會把真正的
    長線切碎(自測否決過)。只清掉對不上的前情:寧可少給脈絡,不可給錯脈絡。"""
    led = sl.update_ledger(
        [], [_ev_full("2882", "litigation", "國泰金遭控違反個資法遭裁罰")], "2026-07-10")
    led = sl.update_ledger(
        led, [_ev_full("2882", "litigation", "國泰金與海外再保商仲裁案開庭")], "2026-07-20")
    assert led[0]["prev_delta"] == "", (
        f"不相干的事件被寫成前情:{led[0]['prev_delta']!r}")


def test_genuine_followup_keeps_prev_delta_even_if_reworded():
    """真正的續報會換措辭,不得因此丟掉前情——那是本模組的核心價值。"""
    led = sl.update_ledger(
        [], [_ev_full("2317", "orders", "鴻海洽談收購案 傳金額達百億")], "2026-07-10")
    led = sl.update_ledger(
        led, [_ev_full("2317", "orders", "鴻海收購案傳金額上修 洽談進入最後階段")],
        "2026-07-20")
    assert led[0]["prev_delta"] == "鴻海洽談收購案 傳金額達百億", \
        "換了措辭的續報丟失前情"


def test_subject_overlap_strips_entity_before_comparing():
    """剝實體名是關鍵:同公司的兩件事標題都含公司名,不剝的話任何兩則都有基礎重疊。"""
    high = sl._subject_overlap("鴻海收購案洽談中", "鴻海收購案進入最後階段", "鴻海")
    low = sl._subject_overlap("鴻海遭控違反個資法", "鴻海接獲車用大單", "鴻海")
    assert high > low
    assert low < sl.SUBJECT_OVERLAP_MIN


def test_production_shape_code_entity_with_chinese_titles():
    """r5(Codex,P1):生產環境的 entity 是**股票代號**(extract_structured_events
    優先取 code/company_label),中文標題寫的是公司名。只剝「2317」等於沒剝——
    「鴻海」仍留在兩則標題撐高重疊度,短標題就可能越過門檻拿到錯誤前情。"""
    name_map = {"2317": "鴻海"}
    led = sl.update_ledger(
        [], [_ev_full("2317", "orders", "鴻海傳接獲蘋果訂單")], "2026-07-10",
        name_map=name_map)
    assert led[0]["entity_name"] == "鴻海"
    led = sl.update_ledger(
        led, [_ev_full("2317", "orders", "鴻海公告取得車用訂單")], "2026-07-20",
        name_map=name_map)
    assert led[0]["prev_delta"] == "", (
        f"同月同型別的另一件事被寫成前情:{led[0]['prev_delta']!r}"
        "(公司名沒被剝掉時,兩則短標題只靠「鴻海」與「訂單」就會越過門檻)")


def test_alias_stripping_changes_the_verdict():
    """直接佐證:不剝公司名時兩則會被判為同主體,剝了才判為不同。"""
    a, b = "鴻海傳接獲蘋果訂單", "鴻海公告取得車用訂單"
    without_alias = sl._subject_overlap(a, b, "2317", "")
    with_alias = sl._subject_overlap(a, b, "2317", "鴻海")
    assert without_alias > with_alias
    assert with_alias < sl.SUBJECT_OVERLAP_MIN <= without_alias
