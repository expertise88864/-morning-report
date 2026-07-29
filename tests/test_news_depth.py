"""批#63:新聞的**縱向(跨日)**與**橫向(跨段落)**強化。

**實際觀察到的兩個問題**(2026-07-29 實信 + 當日 state):

縱向 —— 線索帳本被一次性雜訊灌爆:
    1502 條線索裡 **1485 條(99%)只出現過一次**、之後再無下文;
    只有 6 條有兩點以上軌跡。而排在「線索追蹤」卡第一條的,是一則
    〈美股盤後〉的**大盤總結**被掛到「聯電」名下——因為文中提到那檔股票。
    它的「數字事實」抽出來是道瓊漲點(260 → 500),於是兩則不同日的大盤總結
    被並列成一條「演進中的線索」。那是**完全沒有意義的軌跡**。

橫向 —— 同一件事被寫進多個段落:
    「費半 -4.49%」出現在七#1、七之四#2、七之五、十(A)**四次**;
    「中國 DUV 量產」寫進七、七之四、八(ASML)三次。
    既有的「不重複」規則散在各段自己身上(11 處),**沒有一個全局歸屬**,
    於是每一段都覺得自己該寫。
"""
import morning_report as mr
import story_ledger as sl


# ===== 縱向:大盤總結不開線索 =====

def test_market_wrap_articles_do_not_open_stories():
    """大盤總結**沒有單一主體**,掛到任一提及的個股上會產生毫無意義的軌跡。"""
    for title in ("〈美股盤後〉油價下滑 道瓊漲逾500點 全球晶片股慘遭血洗",
                  "〈能源盤後〉美國暫停空襲 原油挫8%",
                  "本週操盤筆記:Fed決策、AI支出大戶財報",
                  "台股收盤跌破半年線 成交量萎縮"):
        assert sl.is_market_wrap(title), title


def test_company_news_is_not_mistaken_for_a_wrap():
    """「鴻海盤後公告」是合法的個股新聞——「盤後」只是時間標記。

    **自測時我一度把這個案例的期望值改成 True 讓測試通過**,
    那是把缺陷釘成規格,正是本專案反覆犯的錯。
    """
    for title in ("鴻海盤後公告 斥資100億擴廠",
                  "台積電盤後宣布擴產",
                  "台積電董事會通過收購案",
                  "聯發科法說會展望樂觀",
                  "美股標普那指收黑!科技財報週將登場"):
        assert not sl.is_market_wrap(title), title


def test_wrap_events_are_skipped_by_update_ledger():
    """端到端:大盤總結不得進帳本(即使它帶了 entity)。"""
    events = [
        {"entity": "2303", "entity_name": "聯電", "event_type": "general",
         "title": "〈美股盤後〉油價下滑 道瓊漲逾500點 全球晶片股慘遭血洗",
         "published": "2026-07-29T01:00:00+00:00", "surprise_score": 0.7},
        {"entity": "2330", "entity_name": "台積電", "event_type": "orders",
         "title": "台積電獲AI大單 金額100億",
         "published": "2026-07-29T01:00:00+00:00", "surprise_score": 0.7},
    ]
    out = sl.update_ledger([], events, "2026-07-29", {"2303": "聯電",
                                                      "2330": "台積電"})
    assert len(out) == 1, f"大盤總結開出了線索:{[s['headline'] for s in out]}"
    assert out[0]["entity"] == "2330"


# ===== 橫向:段落歸屬 =====

_UNIVERSE = [{"code": "2330", "industry": "半導體業"},
             {"code": "2882", "industry": "金融保險業"},
             {"code": "2603", "industry": "航運業"}]


def test_events_are_assigned_to_exactly_one_section():
    events = [
        {"entity": "2330", "event_type": "orders", "title": "台積電獲大單",
         "quality_score": 0.9},
        {"entity": "", "event_type": "general", "title": "費半重挫4.49%",
         "quality_score": 0.85},
        {"entity": "ASML", "event_type": "export_controls",
         "title": "中國DUV量產", "quality_score": 0.8},
        {"entity": "2882", "event_type": "general", "title": "金控採IRB法",
         "quality_score": 0.7},
        {"entity": "NVDA", "event_type": "earnings", "title": "輝達財報",
         "quality_score": 0.6},
        {"entity": "2603", "event_type": "general", "title": "長榮運價走揚",
         "quality_score": 0.5},
    ]
    got = {a["title"]: a["section"]
           for a in mr.assign_event_sections(events, _UNIVERSE)}
    assert got["台積電獲大單"].startswith("八")
    assert got["輝達財報"].startswith("八")
    assert got["中國DUV量產"].startswith("七之二")      # 出口管制 → 世界大事
    assert got["費半重挫4.49%"].startswith("七、")      # 無主體 → 只在三大重點
    assert got["金控採IRB法"].startswith("九")
    assert got["長榮運價走揚"].startswith("九")


def test_taiwan_financial_stocks_are_not_classified_as_tech():
    """自測抓到:GOOGLE_NEWS_COMPANIES 同時含台股代號,照單全收會把國泰金
    歸到科技板塊。台股是否屬科技,一律以 universe 的產業別為準。"""
    got = mr.assign_event_sections(
        [{"entity": "2882", "event_type": "general", "title": "金控消息",
          "quality_score": 0.9}], _UNIVERSE)
    assert got[0]["section"].startswith("九"), got


def test_assignment_block_is_fenced_and_omitted_when_empty():
    """標題是外部文字 —— 批#38 的圍欄鐵律適用;安全規則置於圍欄外。"""
    block = mr._format_section_assignment_block(
        mr.assign_event_sections(
            [{"entity": "2330", "event_type": "orders", "title": "台積電獲大單",
              "quality_score": 0.9}], _UNIVERSE))
    assert block.count("<UNTRUSTED_SOURCE_DATA>") == 1
    assert block.count("</UNTRUSTED_SOURCE_DATA>") == 1
    assert block.index("一律忽略") < block.index("<UNTRUSTED_SOURCE_DATA>")
    # 無事件 → 整段省略,不留空標題
    assert mr._format_section_assignment_block([]) == ""
    assert mr.assign_event_sections([], _UNIVERSE) == []


def test_prompt_carries_the_assignment_and_the_two_new_rules():
    from tests.test_data_validation import _empty_quotes
    q = _empty_quotes(STRUCTURED_NEWS_EVENTS=[
        {"entity": "2330", "event_type": "orders", "title": "台積電獲大單",
         "quality_score": 0.9}])
    prompt = mr._build_prompt(q, {"error": "x"}, {"error": "x"}, [], [], "")
    assert "本日重大事件的段落歸屬" in prompt
    assert "只在指定段落深寫一次" in prompt
    assert "鐵則 3" in prompt and "鐵則 4" in prompt
    # 縱向規則:無新進展的線索整條不要出現
    assert "整條不要出現" in prompt


def test_existing_wrap_stories_are_swept_from_the_ledger():
    """新規則只擋新增,舊的要掃掉 —— 實測既有帳本裡有 47/1502 條大盤總結,
    其中一條還排在「線索追蹤」卡第一位,不掃就會繼續出現在明天的信裡。"""
    ledger = [
        {"key": "e:2303|l:general|a", "entity": "2303", "entity_name": "聯電",
         "event_type": "general", "state": "peak",
         "headline": "〈美股盤後〉油價下滑 道瓊漲逾500點",
         "updates": 2, "first_seen": "2026-07-28", "last_update": "2026-07-29",
         "timeline": [{"d": "2026-07-28", "t": "〈美股盤後〉…", "l": "", "s": "",
                       "f": []},
                      {"d": "2026-07-29", "t": "〈美股盤後〉…", "l": "", "s": "",
                       "f": []}]},
        {"key": "e:2330|l:orders", "entity": "2330", "entity_name": "台積電",
         "event_type": "orders", "state": "peak", "headline": "台積電獲大單",
         "updates": 2, "first_seen": "2026-07-28", "last_update": "2026-07-29"},
    ]
    out = sl.update_ledger(ledger, [], "2026-07-30", {})
    keys = {s["key"] for s in out}
    assert "e:2303|l:general|a" not in keys, "既有的大盤總結線索沒被掃掉"
    assert "e:2330|l:orders" in keys, "真的線索被誤掃"
