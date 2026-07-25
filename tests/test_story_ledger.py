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


def test_story_key_ignores_dates_and_numbers():
    """同一條線索的後續報導金額會變、日期會變。把它們放進 key 等於每天開新 story。"""
    a = sl.story_key("2330", "earnings", "台積電法說 毛利率 58%")
    b = sl.story_key("2330", "earnings", "台積電法說 毛利率上修至 60%,7月25日")
    assert a == b, "同實體同事件類型應視為同一條線索"
    c = sl.story_key("2454", "earnings", "聯發科法說")
    assert a != c


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
