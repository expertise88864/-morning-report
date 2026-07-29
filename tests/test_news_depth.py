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
                  ):
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
        assert sl.is_market_wrap(wrap), wrap

    for real in ("恩智浦半導體盤後下跌,儘管季度業績及展望均超預期",
                 "鴻海盤後公告 斥資100億擴廠",
                 "台積電董事會通過收購案",
                 ):
        assert not sl.is_market_wrap(real), real


def test_generic_flash_prefix_is_not_a_wrap():
    """r1(Codex,P2):「快報」太泛用 —— 「【財報快報】台積電獲利創高」
    「重訊快報:某公司取得百億訂單」都是合法個股新聞,那個詞本身不表示綜覽。
    誤判的後果是**線索永遠開不起來,而且是靜默的**。"""
    for real in ("【財報快報】台積電第二季獲利創高",
                 "重訊快報:某公司取得百億訂單"):
        assert not sl.is_market_wrap(real), real


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


def test_event_word_exemption_needs_a_real_company_event():
    """r2(Codex,P2):**豁免要證明「有具體公司事件」,不是出現關鍵字就算**。
    「美股盤後收黑,科技財報週將登場」因為含「財報」而被放行 —— 但那是
    **市場級**標題:「財報週」是時節,不是某家公司的事。

    兩個條件收緊:
      (a) 市場主體(美股/台股/費半/道瓊…)+ 漲跌方向 → 一律總結,事件詞不得豁免
      (b) 事件詞若接「週/季/旺季/行情/來臨/登場」等時節後綴,那是期間不是事件
    """
    for wrap in ("美股盤後收黑,科技財報週將登場",
                 "台股盤後收紅 法說會旺季登場",
                 "〈美股盤後〉費半重挫 財報週來臨"):
        assert sl.is_market_wrap(wrap), wrap

    # 具體公司事件仍要放行
    for real in ("恩智浦半導體盤後下跌,儘管季度業績及展望均超預期",
                 "聯發科盤後公布財報 EPS 創高",
                 "鴻海盤後公告 斥資100億擴廠"):
        assert not sl.is_market_wrap(real), real


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
        assert sl.is_market_wrap(wrap), wrap

    # 但「台股上市公司某某…」這種以市場詞開頭的**個股**標題不得誤傷
    # (送審時我自己列的擔心點)
    for real in ("台股上市公司某某獲利創高",
                 "台積電董事會通過收購案"):
        assert not sl.is_market_wrap(real), real


def test_market_direction_still_yields_to_a_concrete_company_event():
    """r4(Codex,P2):市場主體+方向雖是強訊號,**仍要讓給具體公司事件**。
    我 r3 把它提前判斷,結果繞過了豁免 —— 「美股焦點:輝達財報後大漲」含
    「美股+大漲」就被排除,但那是輝達的財報故事。兩個分支現在套同一個判斷。
    """
    for real in ("美股焦點:輝達財報後大漲",
                 "美股大漲 台積電ADR收購案通過"):
        assert not sl.is_market_wrap(real), real
    # 純市場級的仍要擋
    for wrap in ("美股標普那指收黑!科技財報週將登場",
                 "台股大盤重挫 電子權值股領跌"):
        assert sl.is_market_wrap(wrap), wrap


def test_season_suffix_survives_a_connector_character():
    """自測抓到:「法說**會**旺季」的事件詞是「法說」,兩字後綴視窗只看到
    「會旺」而看不到「旺季」,於是整句被當成有公司事件而放行。
    後綴視窗放寬到 4 字並允許「會/週」當連接字。"""
    assert sl.is_market_wrap("台股盤後收紅 法說會旺季登場")
    # 但真的法說會消息不得誤傷
    assert not sl.is_market_wrap("台積電法說會展望樂觀")


def test_verbs_are_not_seasonal_suffixes():
    """r5(Codex,P1):**動詞不能當時節後綴**。我上一輪把「登場/來臨」放進清單,
    於是「台積電盤後**法說會登場**」的「法說」後面接到「會登場」→ 被判成時節,
    整句變成大盤總結而被靜默丟棄。

    真正的時節標記是**期間名詞**(週/季/月/旺季/行情);
    「財報週將登場」的季節性來自「週」,不是「登場」。
    """
    for real in ("台積電盤後法說會登場", "台積電法說會展望樂觀",
                 "聯發科盤後公布財報 EPS 創高"):
        assert not sl.is_market_wrap(real), real
    for wrap in ("台股盤後收紅 法說會旺季登場",
                 "美股盤後收黑,科技財報週將登場"):
        assert sl.is_market_wrap(wrap), wrap
