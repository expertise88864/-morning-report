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
        assert sl.is_market_wrap(title, _NAMES), title


def test_company_news_is_not_mistaken_for_a_wrap():
    """「鴻海盤後公告」是合法的個股新聞——「盤後」只是時間標記。

    **自測時我一度把這個案例的期望值改成 True 讓測試通過**,
    那是把缺陷釘成規格,正是本專案反覆犯的錯。
    """
    for title in ("鴻海盤後公告 斥資100億擴廠",
                  "台積電盤後宣布擴產",
                  "台積電董事會通過收購案",
                  "聯發科法說會展望樂觀",
                  ):
        assert not sl.is_market_wrap(title, _NAMES), title


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

#: v7 起 is_market_wrap 需要**已知公司名詞彙表** —— 那是它判斷「這篇有沒有
#: 單一主體」的直接訊號,取代前六版的關鍵字/詞性後綴堆疊。
_NAMES = ("台積電", "聯發科", "鴻海", "輝達", "恩智浦", "勤誠", "緯創",
          "廣達", "國泰金", "中信金")

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


# ===== r1(Codex)四條 + 自驗一條 =====

def test_wrap_uses_strong_and_weak_markers():
    """強標記(操盤筆記/盤中速報/台股收盤…)一律視為總結;
    弱標記(盤後/盤前…)只有在**沒有任何事件詞**時才算。

    第一版我用單一清單 + 事件詞豁免,結果「本週操盤筆記:Fed決策、AI支出大戶
    **財報**」因為含「財報」而被放行 —— 豁免規則反而放走了最典型的綜覽。
    """
    for wrap in ("〈美股盤後〉油價下滑 道瓊漲逾500點",
                 "〈台股盤後〉台積電穩盤 回測4萬3",
                 "本週操盤筆記:Fed決策、AI支出大戶財報",
                 "台股收盤跌破半年線", "台股收跌1195.97點",
                 "盤中速報 - 勤誠(8210)大跌7.2%"):
        assert sl.is_market_wrap(wrap, _NAMES), wrap

    for real in ("恩智浦半導體盤後下跌,儘管季度業績及展望均超預期",
                 "鴻海盤後公告 斥資100億擴廠",
                 "台積電董事會通過收購案",
                 ):
        assert not sl.is_market_wrap(real, _NAMES), real


def test_generic_flash_prefix_is_not_a_wrap():
    """r1(Codex,P2):「快報」太泛用 —— 「【財報快報】台積電獲利創高」
    「重訊快報:某公司取得百億訂單」都是合法個股新聞,那個詞本身不表示綜覽。
    誤判的後果是**線索永遠開不起來,而且是靜默的**。"""
    for real in ("【財報快報】台積電第二季獲利創高",
                 "重訊快報:某公司取得百億訂單"):
        assert not sl.is_market_wrap(real, _NAMES), real


def test_all_tech_industries_go_to_the_tech_section():
    """r1(Codex,P2):我原本用「半導體/電子」子字串比對,漏掉「電腦及週邊設備業」
    「光電業」「通信網路業」「資訊服務業」「數位雲端」—— 那些公司會被歸到九段,
    但九段規定聚焦非科技,於是事件被錯段書寫或因規則衝突而遭省略。
    **重用既有的 _TECH_INDUSTRIES_FOR_SECTOR_NEWS,不另造子集。**"""
    for industry in mr._TECH_INDUSTRIES_FOR_SECTOR_NEWS:
        got = mr.assign_event_sections(
            [{"entity": "9999", "event_type": "orders", "title": "某消息",
              "quality_score": 0.9}],
            [{"code": "9999", "industry": industry}])
        assert got[0]["section"].startswith("八"), (industry, got)


def test_assignment_covers_every_event_injected_into_the_prompt():
    """r1(Codex,P2):歸屬原本只做前 12 個,而 prompt 注入 25 個 ——
    第 13–25 個沒有主段落,不受「只在指定段落深寫一次」約束,
    跨段重複在那些事件上原封不動。"""
    events = [{"entity": f"2{i:03d}", "event_type": "orders",
               "title": f"事件{i}", "quality_score": 1.0 - i * 0.01}
              for i in range(30)]
    got = mr.assign_event_sections(events, _UNIVERSE)
    assert len(got) == mr.STRUCTURED_EVENTS_IN_PROMPT == 25


def test_narrative_section_rule_no_longer_contradicts_itself():
    """r1(Codex,P2):七之四的段落規則原本要求列出「哪些**無進展**」,
    而 R16 鐵則 4 說無進展的整條不要寫 —— 模型收到直接矛盾的指令,
    而且後者位置更接近輸出格式、更具體,實信就照它寫了空條目。"""
    from tests.test_data_validation import _empty_quotes
    prompt = mr._build_prompt(_empty_quotes(), {"error": "x"}, {"error": "x"},
                              [], [], "")
    # 錨在**段落標題**上,不是規則區裡提到「七之四」的地方
    # (自測第一版抓到 R16b 的敘述,量到的是另一段文字)
    i = prompt.index("## 七之四、")
    section = prompt[i:prompt.index("## 七之五", i)]
    assert "哪些**無進展**" not in section, "矛盾的舊要求還在"
    assert "整條不要出現" in section or "整段省略" in section


def test_prompt_never_quotes_the_thing_it_forbids():
    """**這個錯我犯了三次**:批#58(在 prompt 提已刪除的段名)、
    批#62(在禁令裡逐字列舉違規措辭)、批#63(在規則旁重述被禁的舊要求
    與實信的違規寫法)。在禁令旁邊重述被禁的東西,等於又把它講一次給模型聽。

    規則本身要留在 prompt,**理由與反面範例一律放程式註解**。
    """
    from tests.test_data_validation import _empty_quotes
    prompt = mr._build_prompt(_empty_quotes(), {"error": "x"}, {"error": "x"},
                              [], [], "")
    # 批#63 的反面範例
    for banned in ("無進展", "無實質新資訊", "維持觀望"):
        assert banned not in prompt, f"prompt 重述了被禁的寫法:{banned}"
    # 批#58 / 批#62 的(回歸保護)
    for banned in ("總體經濟與政策環境", "使用者指定", "本報追蹤"):
        assert banned not in prompt, f"prompt 重述了被禁的寫法:{banned}"
    # 但規則本身必須在
    assert "鐵則 3" in prompt and "鐵則 4" in prompt
    assert "只在指定段落深寫一次" in prompt


def test_market_index_moves_are_wraps_regardless_of_position():
    """r3:**「市場主體 + 漲跌方向」是獨立的強訊號**,不該被弱標記關卡擋在前面。

    Codex 這一輪主張兩條既有斷言必然失敗 —— 那個**機制判斷是錯的**
    (標題含「收黑」不是「收盤」,前 16 字沒有弱標記,函式在更早就 return False,
     實跑全過)。但它的**直覺是對的**:「美股標普那指收黑!科技財報週將登場」
    本來就是大盤總結,沒有單一主體。
    **我從第一版就把那個案例的期望值寫成 False,四個版本一路帶著這個錯誤假設。**
    """
    for wrap in ("美股標普那指收黑!科技財報週將登場",
                 "台股大盤重挫 電子權值股領跌",
                 "費半指數大跌 亞股同步走低"):
        assert sl.is_market_wrap(wrap, _NAMES), wrap

    # 但「台股上市公司某某…」這種以市場詞開頭的**個股**標題不得誤傷
    # (送審時我自己列的擔心點)
    for real in ("台股上市公司某某獲利創高",
                 "台積電董事會通過收購案"):
        assert not sl.is_market_wrap(real, _NAMES), real


def test_market_direction_still_yields_to_a_concrete_company_event():
    """r4(Codex,P2):市場主體+方向雖是強訊號,**仍要讓給具體公司事件**。
    我 r3 把它提前判斷,結果繞過了豁免 —— 「美股焦點:輝達財報後大漲」含
    「美股+大漲」就被排除,但那是輝達的財報故事。兩個分支現在套同一個判斷。
    """
    for real in ("美股焦點:輝達財報後大漲",
                 "美股大漲 台積電ADR收購案通過"):
        assert not sl.is_market_wrap(real, _NAMES), real
    # 純市場級的仍要擋
    for wrap in ("美股標普那指收黑!科技財報週將登場",
                 "台股大盤重挫 電子權值股領跌"):
        assert sl.is_market_wrap(wrap, _NAMES), wrap


def test_v7_replaces_the_whole_keyword_stack_with_a_direct_signal():
    """**這個函式改了七版。** 前六版都在加關鍵字與詞性後綴,每一版都被下一個
    反例打破:單一清單 → 事件詞豁免 → 強/弱兩級 → 市場主體+方向 →
    時節後綴(含動詞)→ 拿掉動詞。每次都是同一個病:
    **用「有沒有出現某個詞」代替「這篇有沒有單一主體」。**

    v7 改用直接訊號:**標題裡有沒有具名公司**(呼叫端提供詞彙表)。
    這幾組正是把前六版逐一打破的反例。
    """
    for wrap in ("台股盤後收紅 法說會旺季登場",   # v6 的反例
                 "美股盤後收黑 財報登場",          # v6 的反向反例
                 "美股標普那指收黑!科技財報週將登場"):
        assert sl.is_market_wrap(wrap, _NAMES), wrap
    for real in ("台積電法說會展望樂觀",
                 "台積電盤後法說會登場",          # v5 的反例
                 "美股焦點:輝達財報後大漲"):      # v4 的反例
        assert not sl.is_market_wrap(real, _NAMES), real


def test_wrap_detection_fails_open_without_a_vocabulary():
    """沒有詞彙表時偏向**放行** —— 誤判成綜覽會讓線索永遠開不起來且完全無聲;
    誤放行只是多一條雜訊線索,而卡片本來就要求 ≥2 個時間點。"""
    assert not sl.is_market_wrap("美股焦點:輝達財報後大漲", ())
    # 但強標記與欄目型不需詞彙表就能判
    assert sl.is_market_wrap("〈美股盤後〉油價下滑", ())
    assert sl.is_market_wrap("本週操盤筆記:Fed決策", ())


def test_column_branch_only_matches_session_columns():
    """r7(Codex,P1):欄目分支原本還收「速報/快訊」與市場詞,於是
    【美股焦點】輝達財報後大漲、【財報快訊】台積電獲利創高 在檢查公司名**之前**
    就被判成綜覽而**靜默丟棄** —— 「焦點/快訊」是版面標籤,不是市場總結。

    只認**場次詞**(盤後/盤前/盤中/收盤/開盤);「盤中速報」這類完整欄目名
    仍由強標記抓。
    """
    for real in ("【美股焦點】輝達財報後大漲",
                 "【財報快訊】台積電獲利創高",
                 "【財報快報】台積電第二季獲利創高"):
        assert not sl.is_market_wrap(real, _NAMES), real
    for wrap in ("〈台股盤後〉台積電穩盤 回測4萬3",
                 "〈能源盤後〉美國暫停空襲 原油挫8%",
                 "盤中速報 - 勤誠大跌7.2%"):
        assert sl.is_market_wrap(wrap, _NAMES), wrap


def test_market_column_yields_to_a_named_company():
    """r8(Codex,P2):把市場詞從欄目分支整個拿掉,會讓「【美股】道瓊漲500點」
    漏判(「漲」不在方向詞裡)。但市場詞欄目必須**讓給具名公司**,
    否則【美股焦點】輝達財報後大漲 又會被誤殺。

    最終結構的三條規則用**同一個原則**:有具名公司就不是綜覽。
      (a) 場次欄目(〈台股盤後〉)—— 絕對成立
      (b) 市場詞欄目(【美股】)—— 讓給具名公司
      (c) 市場主體+方向        —— 讓給具名公司
    """
    assert sl.is_market_wrap("【美股】道瓊漲500點", _NAMES)
    assert not sl.is_market_wrap("【美股焦點】輝達財報後大漲", _NAMES)
    # 場次欄目即使有公司名仍成立(那是總結文章提到的例子)
    assert sl.is_market_wrap("〈台股盤後〉台積電穩盤 回測4萬3", _NAMES)


def test_market_column_respects_the_fail_open_contract():
    """r9(Codex,P1):市場詞欄目那一支原本被寫在 fail-open 守衛**前面**,
    於是無詞彙表時反而變成「偏向丟棄」——跟守衛上方那段註解寫的契約正好相反,
    而且是最危險的方向:線索永遠開不起來,且完全無聲。

    判準:它跟主規則一樣靠「標題裡沒有已知公司名」當證據,而空詞彙表下
    「沒有公司名」必然成立,所以它必須跟主規則待在守衛的同一側。
    """
    # 無詞彙表:只有場次欄目與強標記能丟,市場詞欄目一律放行
    assert not sl.is_market_wrap("【美股焦點】輝達財報後大漲", ())
    assert not sl.is_market_wrap("【美股】道瓊漲500點", ())
    assert not sl.is_market_wrap("美股盤後收黑 財報登場", ())
    assert sl.is_market_wrap("〈台股盤後〉台積電穩盤", ())      # 場次欄目
    assert sl.is_market_wrap("台股收跌1195.97點", ())           # 強標記


# --------------------------------------------------------------------------
# 批#64:事件聚合誠實化
# --------------------------------------------------------------------------

def _ev(title, source, **kw):
    base = {"title": title, "source": source,
            "published": "2026-07-30T00:00:00+00:00"}
    base.update(kw)
    return base


def _events(items):
    import datetime as _dt
    now = _dt.datetime(2026, 7, 30, tzinfo=_dt.timezone.utc)
    return mr.extract_structured_events(items, [], None, now)


def test_two_different_events_for_one_company_do_not_swallow_each_other():
    """舊聚合鍵對「有主體的型別事件」會**抹掉標題**,於是同一天同一家公司
    同型別同方向的所有事件塌成一則:輸的那則靜默消失,活下來的還宣稱
    自己被多來源交叉驗證(實測 corroboration_count=2)。

    真實 state 佐證:2884 同桶並存的兩個標題是「115年6月自結盈餘」與
    「董事會決議增資發行新股」——兩件毫不相干的公告。
    """
    out = _events([
        _ev("台積電獲蘋果2奈米大單", "經濟日報財經",
            entity="2330", event_type="orders", direction=1),
        _ev("台積電獲輝達CoWoS追加訂單", "中央社財經",
            entity="2330", event_type="orders", direction=1),
    ])
    assert len(out) == 2, f"不同事件被互吞:{[e['title'] for e in out]}"
    assert {e["corroboration_count"] for e in out} == {1}


def test_mops_style_different_filings_stay_separate():
    """真實語料中同桶並存的那一組(2884/earnings),重疊率 0.27。"""
    out = _events([
        _ev("公告本公司暨子公司115年6月自結盈餘", "MOPS",
            entity="2884", event_type="earnings", direction=0),
        _ev("公告本公司董事會決議增資發行新股", "MOPS",
            entity="2884", event_type="earnings", direction=0),
    ])
    assert len(out) == 2


def test_same_event_across_outlets_still_merges():
    out = _events([
        _ev("台積電法說會展望樂觀 上調全年財測", src,
            entity="2330", event_type="guidance_raise", direction=1)
        for src in ("CNBC Tech", "經濟日報財經", "中央社財經")
    ])
    assert len(out) == 1


def test_corroboration_counts_publishers_not_feed_labels():
    """58 個 feed 裡有 29 個是 news.google.com,CNBC 三個頻道、聯合報系兩家報。
    舊法直接數 sources 長度 → 實測有事件宣稱 10 個來源交叉驗證,其中 6 個
    是同一個 Google News 的不同查詢。"""
    srcs = ("CNBC Tech", "CNBC Top News", "CNBC Economy",
            "Google-半導體", "類股-金融-台股", "經濟日報財經", "聯合新聞兩岸")
    out = _events([
        _ev("台積電法說會展望樂觀 上調全年財測", s,
            entity="2330", event_type="guidance_raise", direction=1)
        for s in srcs
    ])
    assert len(out) == 1
    assert len(out[0]["sources"]) == len(srcs)          # 原始來源全部保留
    assert out[0]["corroboration_count"] == 3           # cnbc / google / udn


def test_near_duplicate_wire_copy_merges_even_without_an_entity():
    """無主體事件舊法要求正規化後前 48 字元完全相同,轉載改一個字就漏掉。"""
    out = _events([
        _ev("Trump Accounts for kids launch July 4: What parents need to know",
            "CNBC Tech", event_type="general"),
        _ev("Trump Accounts for kids launched July 4: What parents need to know",
            "Bloomberg Markets", event_type="general"),
    ])
    assert len(out) == 1


def test_serial_draws_are_not_merged_by_the_digit_guard():
    """字元重疊率高達 0.92,但期別不同就是兩次開獎——數字守衛負責擋下。"""
    out = _events([
        _ev("威力彩第115050期　頭獎槓龜", "中央社財經", event_type="general"),
        _ev("威力彩第115051期　頭獎槓龜", "中央社政治", event_type="general"),
    ])
    assert len(out) == 2


def test_containing_title_does_not_swallow_the_shorter_filing():
    """r1(Codex,P2):相似度原本除以**較短**的一邊(overlap coefficient),
    於是「A 完全包含於 B」必得 1.0——實測
    「公告本公司董事會決議增資發行新股」與「⋯之資金用途變更」重疊率 1.000,
    兩件不同的公告會被併掉一件。MOPS 標題共用大量制式前綴,這種包含關係不罕見。
    改用較長的一邊當分母後同一組降到 0.682。
    """
    out = _events([
        _ev("公告本公司董事會決議增資發行新股", "MOPS",
            entity="2884", event_type="general", direction=0),
        _ev("公告本公司董事會決議增資發行新股之資金用途變更", "MOPS",
            entity="2884", event_type="general", direction=0),
    ])
    assert len(out) == 2


def test_llm_extractor_is_not_counted_as_an_independent_publisher():
    """r1(Codex,P2):LLM 抽取器的 source 被統一釘成 "LLM extractor",而它的
    輸入正是同一批新聞。把它算成一個獨立來源,等於讓「我們自己讀了一遍」
    變成一次交叉驗證。"""
    import datetime as _dt
    now = _dt.datetime(2026, 7, 30, tzinfo=_dt.timezone.utc)
    body = {"title": "台積電獲輝達追加訂單", "entity": "2330",
            "event_type": "orders", "direction": 1,
            "published": "2026-07-30T00:00:00+00:00"}
    out = mr.extract_structured_events(
        [dict(body, source="經濟日報財經")], [],
        [dict(body, summary="x", confidence=0.7, surprise_score=0.6)], now)
    assert len(out) == 1
    assert "LLM extractor" in out[0]["sources"]      # 出處仍完整保留
    assert out[0]["corroboration_count"] == 1        # 但不計入交叉驗證


def test_fiscal_period_comes_from_the_report_not_the_publication_date():
    """批#67(P1-2):期別 bucket 原本直接取 `published` —— 那是**新聞發布時間**,
    不是報表所屬期間。台股月營收固定在次月 10 日前公告,所以「115年6月營收」
    永遠被掛到 2026-07;季報同理(Q1 財報四月公布 → 掛 2026Q2)。
    整條序列的期別標籤系統性偏一期,同期別的更正公告跨月出現時還會被切成兩集。
    """
    import news_events as ne

    def bucket(title, published, monthly):
        return ne._event_period_bucket(
            {"title": title, "published": published}, monthly)

    # MOPS 月營收:標題寫民國年月
    assert bucket("公告本公司115年6月份自結合併營收",
                  "2026-07-06T00:00:00+00:00", True) == "2026-06"
    assert bucket("台積公司2026年6月營收報告",
                  "2026-07-13T00:00:00+00:00", True) == "2026-06"
    # 季報:標題寫第 N 季 / QN
    assert bucket("台積電第二季獲利創高",
                  "2026-07-16T00:00:00+00:00", False) == "2026Q2"
    assert bucket("鴻海Q3財報優於預期",
                  "2026-11-14T00:00:00+00:00", False) == "2026Q3"
    # 標題沒寫期別 → 退回 published(後備,不是主要來源)
    assert bucket("某公司營收成長", "2026-07-06T00:00:00+00:00", True) == "2026-07"
    # 合理性守衛:標題提到很久以前的年月不採信
    assert bucket("回顧2019年12月的那場危機",
                  "2026-07-06T00:00:00+00:00", True) == "2026-07"


# --------------------------------------------------------------------------
# 批#71:2026-07-30 實信抓到的四項
# --------------------------------------------------------------------------

def test_market_wrap_timeline_points_are_swept_from_existing_stories():
    """2026-07-30 實信的實害——「線索追蹤」卡第一條:

        [高潮] 聯電:Factset…EPS預估上修至0.78元 ・已追蹤 3 次
          07-28 〈美股盤後〉油價大幅回落 道瓊漲逾260點…   ← 大盤總結
          07-29 〈美股盤後〉油價下滑 道瓊漲逾500點…       ← 大盤總結
          07-30 Factset 最新調查:聯電 ADR…

    批#63 只擋新增的大盤總結**線索**,而輸出時的清掃也只看 `headline`。
    這條線索的 headline 已經換成聯電那則(乾淨),於是整條被放行、
    軌跡卻是兩則跟聯電無關的大盤總結。實測全帳本 23/1476 個軌跡點是這種殘留。
    """
    stale = {
        "key": "e:2303|l:earnings|2026q3", "entity": "2303",
        "entity_name": "聯電", "event_type": "earnings", "state": "peak",
        "updates": 3, "last_update": "2026-07-30", "max_surprise": 0.6,
        "headline": "Factset 最新調查:聯電 ADR(UMC-US)EPS預估上修至0.78元",
        "last_published": "2026-07-30T01:00:00+00:00",
        "timeline": [
            {"d": "2026-07-28", "t": "〈美股盤後〉油價大幅回落 道瓊漲逾260點 美光、SK海力士ADR、ASML同步重挫"},
            {"d": "2026-07-29", "t": "〈美股盤後〉油價下滑 道瓊漲逾500點 全球晶片股慘遭血洗 費半狂瀉近5%"},
            {"d": "2026-07-30", "t": "Factset 最新調查:聯電 ADR(UMC-US)EPS預估上修至0.78元"},
        ],
    }
    out = sl.update_ledger([stale], [], "2026-07-30", {"2303": "聯電"})
    story = next(s for s in out if s["key"] == stale["key"])
    kept = [p["t"] for p in story["timeline"]]
    assert len(kept) == 1 and "Factset" in kept[0], f"大盤總結軌跡點沒掃掉:{kept}"


def test_stories_without_any_timeline_are_not_deleted_by_the_sweep():
    """對照組(**自測抓到**):第一版的條件寫成「timeline 為空就丟」,
    而批#57 之前建立的線索本來就沒有 timeline 欄位(真實帳本 466/1502)
    —— 那一版把一條完全正常的線索直接刪掉了。只能丟「本次被掃空」的。
    """
    legacy = {"key": "e:2330|l:orders|202607", "entity": "2330",
              "entity_name": "台積電", "event_type": "orders",
              "state": "developing", "updates": 2, "last_update": "2026-07-30",
              "max_surprise": 0.6, "headline": "台積電獲追加訂單"}
    out = sl.update_ledger([legacy], [], "2026-07-30", {"2330": "台積電"})
    assert [s["key"] for s in out] == [legacy["key"]]


def test_labelled_and_unlabelled_versions_of_one_story_merge():
    """2026-07-30 實信:同一篇〈聯電法說〉AI營收三年拚逾10億美元在 yahoo 與
    cnyes 兩個鏡像站各開一條線索,一條 entity=2303、一條 entityless —— 因為
    比對原本要求 entity **完全相等**,兩者落在不同桶、永遠不會互相比較。
    聯電相關線索因此散成 26 條。

    代號是**額外資訊**不是衝突:一邊有、一邊沒有仍可能是同一件事。
    """
    base = {"event_type": "revenue_growth", "surprise_score": 0.5,
            "published": "2026-07-30T01:00:00+00:00"}
    title = "〈聯電法說〉AI營收三年拚逾10億美元 搶先進封裝、矽光子商機"
    led = sl.update_ledger([], [dict(
        base, entity="", title=f"{title} - tw.stock.yahoo.com",
        link="https://a/1", source_name="Yahoo股市")], "2026-07-30",
        {"2303": "聯電"})
    led = sl.update_ledger(led, [dict(
        base, entity="2303", entity_name="聯電",
        title=f"{title} - news.cnyes.com",
        link="https://a/2", source_name="鉅亨台股")], "2026-07-30",
        {"2303": "聯電"})
    assert len(led) == 1, f"鏡像站開了兩條:{[s['key'] for s in led]}"


def test_two_named_companies_still_do_not_merge():
    """對照組:兩邊都有代號卻不同,那才是真的衝突(不同公司),不得合併。"""
    base = {"event_type": "revenue_growth", "surprise_score": 0.5,
            "published": "2026-07-30T01:00:00+00:00"}
    body = "Q2每股賺3.39元 宣布台南、新加坡同步擴產 資本支出上調至20億美元"
    vocab = {"2303": "聯電", "2330": "台積電"}
    led = sl.update_ledger([], [dict(base, entity="2303", entity_name="聯電",
                                     title=f"聯電{body}", link="https://b/1",
                                     source_name="財訊")], "2026-07-30", vocab)
    led = sl.update_ledger(led, [dict(base, entity="2330", entity_name="台積電",
                                      title=f"台積電{body}", link="https://b/2",
                                      source_name="財訊")], "2026-07-30", vocab)
    assert len(led) == 2


def test_entityless_story_upgrades_its_entity_and_stops_absorbing_others():
    """r1(Codex,P2):批#71 讓 entityless 線索可以跟有代號的事件合併(修鏡像站
    散成兩條),但合併後線索的 `entity` 仍是空的 → 它變成一張**萬用牌**:
    之後另一家公司的高相似度標題進來時,衝突檢查(要求兩邊都有代號)不會擋,
    可能被錯併、覆寫標題。

    三步序列:entityless A → 有代號 A(升級)→ 有代號 B(必須擋下)。
    """
    base = {"event_type": "revenue_growth", "surprise_score": 0.5,
            "published": "2026-07-30T01:00:00+00:00"}
    title = "〈聯電法說〉AI營收三年拚逾10億美元 搶先進封裝、矽光子商機"
    vocab = {"2303": "聯電", "2330": "台積電"}
    # (1) entityless 先開一條
    led = sl.update_ledger([], [dict(base, entity="",
                                     title=f"{title} - tw.stock.yahoo.com",
                                     link="https://a/1",
                                     source_name="Yahoo股市")],
                           "2026-07-30", vocab)
    assert len(led) == 1 and not led[0].get("entity")
    # (2) 有代號版接上 → entity 必須升級(否則它一直是萬用牌)
    led = sl.update_ledger(led, [dict(base, entity="2303", entity_name="聯電",
                                      title=f"{title} - news.cnyes.com",
                                      link="https://a/2",
                                      source_name="鉅亨台股")],
                           "2026-07-30", vocab)
    assert len(led) == 1
    assert led[0]["entity"] == "2303", "entity 沒升級 —— 線索仍是萬用牌"
    assert led[0]["key"].startswith("e:2303|"), "key 沒跟著遷移(r7 的教訓)"
    # (3) 另一家公司的同型標題不得被吸進去
    led = sl.update_ledger(led, [dict(
        base, entity="2330", entity_name="台積電",
        title="〈台積電法說〉AI營收三年拚逾10億美元 搶先進封裝、矽光子商機",
        link="https://a/3", source_name="鉅亨台股")], "2026-07-31", vocab)
    keys = {s["key"] for s in led}
    assert len(led) == 2, f"另一家公司被萬用牌吸走:{keys}"
    assert any(k.startswith("e:2330|") for k in keys)


def test_entity_upgrade_happens_in_a_single_update_ledger_call():
    """r2(Codex,P2):**生產是一次把整份 structured_events 傳進 update_ledger。**

    同一批裡 entityless 鏡像事件先建立線索並把 key 標成 `touched`,有代號的那則
    隨即在 `key in touched` 分支提前 continue —— 永遠到不了(當時放在函式尾端的)
    代號升級,線索因此一直是 entityless 萬用牌,之後別家公司的相似標題會被吸走。

    我上一版的測試把兩則拆成**兩次**呼叫,`touched` 重新變空,所以測不到 ——
    又一次「驗的是我蓋的東西,不是生產送進來的東西」。這條用**一次呼叫**
    餵入 [entityless A, labelled A, labelled B],完全照生產路徑。
    """
    base = {"event_type": "revenue_growth", "surprise_score": 0.5,
            "published": "2026-07-30T01:00:00+00:00"}
    title = "〈聯電法說〉AI營收三年拚逾10億美元 搶先進封裝、矽光子商機"
    vocab = {"2303": "聯電", "2330": "台積電"}
    led = sl.update_ledger([], [
        dict(base, entity="", title=f"{title} - tw.stock.yahoo.com",
             link="https://a/1", source_name="Yahoo股市"),
        dict(base, entity="2303", entity_name="聯電",
             title=f"{title} - news.cnyes.com",
             link="https://a/2", source_name="鉅亨台股"),
        dict(base, entity="2330", entity_name="台積電",
             title="〈台積電法說〉AI營收三年拚逾10億美元 搶先進封裝、矽光子商機",
             link="https://a/3", source_name="鉅亨台股"),
    ], "2026-07-30", vocab)
    by_ent = {s.get("entity"): s for s in led}
    assert "" not in by_ent, (
        f"線索仍是 entityless 萬用牌:{[(s.get('entity'), s['key']) for s in led]}")
    assert set(by_ent) == {"2303", "2330"}, f"公司被錯併:{set(by_ent)}"
    assert by_ent["2303"]["key"].startswith("e:2303|"), "key 沒跟著遷移"


def test_story_card_is_gone_but_the_narrative_source_survives():
    """批#86:線索追蹤**卡片**移除,但**帳本與 prompt 注入必須留著**。

    使用者要的是「像科技板塊那樣前後連貫的敘述」,而那種寫法的素材來源正是
    `format_story_block` 產生的【進行中的線索(跨日追蹤)】—— 前情與軌跡數字。
    只刪卡片、順手把帳本一起砍掉的話,會把使用者真正想要的東西一起砍掉;
    這條測試就是為了讓那種「順手」當場失敗。
    """
    import ast
    import pathlib

    import morning_report as mr
    import render_utils as ru

    src = pathlib.Path(mr.__file__).read_text(encoding="utf-8")
    assert not hasattr(ru, "_render_story_timeline_html"), \
        "渲染器仍在 —— 卡片沒有真的移除"
    assert "story_timeline_html" not in src, "email 組裝仍引用線索追蹤卡"

    # 反向:prompt 注入與帳本維護都必須還在
    assert "【進行中的線索(跨日追蹤)】" in src, \
        "prompt 的線索區塊被一起砍掉了 —— 敘事連貫的素材來源沒了"
    tree = ast.parse(src)
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for fn in ("load_story_ledger", "save_story_ledger",
               "load_story_ledger_for_run"):
        assert fn in names, f"{fn} 不見了 —— 帳本不再跨日累積"


def test_other_sectors_section_carries_the_tech_section_discipline():
    """批#86:九段(其他類股)要與八段(科技)同一套深度紀律與敘事寫法。

    使用者指出八段的寫法正是他要的「前後連貫」,九段則還停在各自獨立的公告。
    這條釘住九段的 prompt 真的帶著那套規矩 —— 否則改動只存在於我的說明裡。
    """
    import morning_report as mr

    prompt = mr._build_prompt({}, {}, {}, [], [], "")
    nine = prompt[prompt.index("## 九、其他類股資訊"):
                  prompt.index("## 十、台灣本地動態")]
    for must in ("三段式因果鏈", "傳導機制", "半句承接",
                 "資訊強度", "信心"):
        assert must in nine, f"九段缺少「{must}」的要求"
    # 八段的三段式紀律是來源,兩段要一致
    eight = prompt[prompt.index("## 八、科技板塊脈動"):
                   prompt.index("## 九、其他類股資訊")]
    assert "三段式因果鏈" in eight


def test_sections_do_not_re_expand_what_belongs_elsewhere():
    """批#86:使用者回報「前後有一些重複的訊息」。

    七段是標題層、十段只寫八/九沒寫過的 —— 兩條排除規則都要真的在 prompt 裡。
    """
    import morning_report as mr

    prompt = mr._build_prompt({}, {}, {}, [], [], "")
    seven = prompt[prompt.index("## 七、昨夜三大重點"):
                   prompt.index("## 七之二")]
    assert "標題層" in seven and "不得展開傳導機制" in seven
    ten = prompt[prompt.index("## 十、台灣本地動態"):
                 prompt.index("## 十一、")]
    assert "整條不要出現" in ten and "八段" in ten and "九段" in ten

    # r1(Codex,P2):**政策也要有唯一完整版歸屬。** 十之二對每項高分政策要求
    # 6-10 行完整解析,而九段又被要求涵蓋金融/房市政策 —— 同一項政策會被
    # 完整寫兩次,正是本批要消除的重複。
    # r1(Codex,P2):**政策也要有唯一完整版歸屬**,但這條規則本身必須**條件化**
    # —— 沒有深度解析段時提到它,會讓模型以為政策已在別處寫過而整段略過
    # (既有的 `policy_deepdive_note` 就是為了這個才做成條件化的)。
    from tests.test_data_validation import _empty_quotes
    with_policy = mr._build_prompt(
        _empty_quotes(TW_DAILY_INTELLIGENCE={"policy": [
            {"title": "新青安 3.0 八月上路 五大門檻一次看",
             "link": "https://example.com/1", "source": "自由時報",
             "importance": 9.0, "topic": "新青安",
             "published": "2026-07-30T08:00:00+08:00"}]}),
        {"error": "x"}, {"error": "x"}, [], [], "")
    nine_p = with_policy[with_policy.index("## 九、其他類股資訊"):
                         with_policy.index("## 十、台灣本地動態")]
    assert "完整版屬於該段" in nine_p and "不得重複展開" in nine_p,         "有深度解析段時,九段沒有把政策的完整版讓出去"
    nine_np = prompt[prompt.index("## 九、其他類股資訊"):
                     prompt.index("## 十、台灣本地動態")]
    assert "完整版屬於該段" not in nine_np,         "沒有深度解析段時仍提到它 —— 模型會以為政策已在別處寫過而整段略過"


def test_section_nine_example_uses_the_bracket_source_format():
    """r1(Codex,P2):範例的來源格式必須與全報契約一致。

    我寫的範例用了 ASCII 圓括號 `(中央社)`,而 R10b 與九段格式都要求
    `[媒體名]`;渲染層 `_dim_source_citations` 的 fallback 也只處理方括號與
    全形括號,**ASCII 圓括號的來源不會被淡化**。模型照抄範例就會產出
    淡化不到的來源 —— 範例是模型最直接模仿的東西,格式錯了影響全段。
    """
    import morning_report as mr

    prompt = mr._build_prompt({}, {}, {}, [], [], "")
    nine = prompt[prompt.index("## 九、其他類股資訊"):
                  prompt.index("## 十、台灣本地動態")]
    assert "[中央社]" in nine, "範例沒有用方括號來源"
    for bad in ("(中央社)", "（中央社）"):
        assert bad not in nine, f"範例仍有圓括號來源:{bad}"
