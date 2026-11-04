"""2026-11-03 晨報品質告警的三條「證據核對未通過」與一處內部用語外漏。"""
import legacy_evidence as le
import writing_rules


def test_mops_thousand_variant_is_parsed():
    """觀測站寫「949,512仟元」;核對器只認「千」,中信金的「9.5億元」被誤判為查無來源。"""
    assert le._quantities("淨值增加949,512仟元") == le._quantities("淨值增加949,512千元")
    le.fresh_ids([])
    n = le.tag({"title": "代子公司公告會計政策變更,使淨值增加949,512仟元", "link": "https://mops/x", "code": "2891"})
    assert le._check(f"中信金淨值增加約 9.5 億元 [MOPS]{n.strip()}", []) == []


def test_ai_race_pricing_and_market_lines_get_citable_ids():
    """「AI 模型競賽」取材自 OpenRouter 定價與 Polymarket 盤,這兩類素材原本沒有編號 → 每天被判「沒有附新聞編號」。"""
    import morning_report as mr
    src = open(mr.__file__, encoding="utf-8").read()
    assert "'source_name': 'OpenRouter'" in src and "'source_name': 'Polymarket'" in src
    le.fresh_ids([])
    n = le.tag({"title": "11-02 上架 z-ai/glm-5.3:輸入 $0.37/M・輸出 $1.25/M", "source_name": "OpenRouter",
                "link": "https://openrouter.ai/models"}).strip()
    block = f"**AI 模型競賽**:新模型輸入每百萬 tokens 0.37 美元、輸出 1.25 美元 [OpenRouter]{n}"
    assert le._check(block, []) == []
    assert "](https://openrouter.ai/models)" in le._convert(block)


def test_internal_rule_numbers_stay_out_of_the_email():
    """實信:「SOX 單日跌幅逾 3% 是 Rule 2 的紅燈」—— 規則編號是寫給模型的。"""
    assert "不寫「規則 2」「Rule 2」這類內部編號" in open(writing_rules.__file__, encoding="utf-8").read()


def test_finance_only_on_big_news_and_biotech_derm_ophth_first():
    """11-03 使用者:近期金融條目都不是大消息;生技優先皮膚科/眼科。兩條產報路徑都要有。"""
    import morning_report as mr
    import prompt_profiles
    legacy = open(mr.__file__, encoding="utf-8").read()
    luna = open(prompt_profiles.__file__, encoding="utf-8").read()
    for src in (legacy, luna):
        assert "**金融只在有大消息時寫**" in src and "皮膚科、眼科" in src
    assert "皮膚科" in mr.OTHER_SECTOR_QUERIES["生技-台股"] and "眼科" in mr.OTHER_SECTOR_QUERIES["生技-台股"]



def test_policy_deep_dive_is_hidden_on_both_paths(monkeypatch):
    """11-03 使用者:十之二先暫時隱藏。備援 prompt 不再要它,特化路徑不渲染。"""
    import analysis_render as ar
    import fixtures_analysis as fx
    import morning_report as mr
    from test_deepseek_legacy_golden import legacy_prompt_inputs
    args = legacy_prompt_inputs()
    args[0]["GAZETTE_RECORDS"] = [{"title": "某部預告修正某辦法", "meta_id": "1", "date": "2026-11-02"}]
    monkeypatch.setattr(mr, "_format_gazette_prompt_block", lambda recs: "【行政院公報】某部預告修正某辦法")
    assert "十之二" not in mr._build_prompt(*args)
    monkeypatch.setattr(mr, "SHOW_POLICY_DEEPDIVE", True)
    assert "## 十之二、重大政策深度解析" in mr._build_prompt(*args)   # 改 True 即恢復
    obj = fx.valid_analysis()
    obj["taiwan_policy"] = [{"source_item_id": "n1", "what": "政院拍板普發現金", "impact": "內需短多"}]
    assert ar.SECTION_POLICY not in ar.render(obj)


def test_sector_heat_table_is_hidden_but_kept_for_the_prompt():
    """11-03 使用者:隱藏「類股熱度表」,保留近 5 日資金輪動;資料照餵九段。"""
    import morning_report as mr
    assert mr.SHOW_SECTOR_HEAT_TABLE is False
    src = open(mr.__file__, encoding="utf-8").read()
    assert "if SHOW_SECTOR_HEAT_TABLE and _hsec and _hrank:" in src
    import render_utils
    assert "5 日中位" in open(render_utils.__file__, encoding="utf-8").read()   # 資金輪動另有渲染,不受影響


def test_world_events_up_to_seven_longer_and_no_overlap_with_top3():
    """11-03 使用者:七之二每條多一兩句、最多 7 條,不重複七段的市場影響。"""
    import analysis_render as ar
    import fixtures_analysis as fx
    import prompt_profiles
    legacy = open(writing_rules.__file__, encoding="utf-8").read()
    assert "最多 7 條" in legacy and "150～240 字" in legacy and "市場影響見七段" in legacy
    assert "最多七條" in open(prompt_profiles.__file__, encoding="utf-8").read()
    obj = fx.valid_analysis()
    obj["world_events"] = [{"source_item_id": "n1", "what": f"事件{i}", "why_it_matters": "意涵",
                            "what_next": "後續"} for i in range(9)]
    out = ar.render(obj)
    world = out.split("## " + ar.SECTION_WORLD, 1)[1].split("\n## ", 1)[0]
    assert world.count("\n- ") == 7


def test_offseason_hides_mlb_and_cpbl_standings_only():
    """11-04 使用者:MLB/中職打完了就先隱藏戰績。季後賽還在打的不收,其他項目不動。"""
    import datetime as dtm
    import sports_season as ss
    data = {"standings": {"美聯": []}, "cpbl": [1], "cpbl_full_year": [2], "nba": [3], "news": {"MLB": [4]}}
    off = ss.drop_offseason_standings(data, dtm.datetime(2026, 11, 4))
    assert off == {"nba": [3], "news": {"MLB": [4]}}
    assert ss.drop_offseason_standings(data, dtm.datetime(2027, 3, 24)) == off
    assert ss.drop_offseason_standings(data, dtm.datetime(2027, 3, 25)) == data
    assert ss.drop_offseason_standings(data, dtm.datetime(2026, 10, 31)) == data
    mid = ss.drop_offseason_standings({**data, "mlb_post": {"x": 1}, "cpbl_post": {"y": 1}}, dtm.datetime(2026, 11, 1))
    assert mid["standings"] and mid["cpbl"] and mid["cpbl_full_year"]


def test_news_and_awards_still_render_without_standings():
    """戰績收掉後,MVP/賽揚盤與體育新聞仍要出現,標題也不可出現沒有內容的項目。"""
    import datetime as dtm
    import html as htmllib
    import render_utils as ru
    import sports_season as ss
    sports = ss.drop_offseason_standings(
        {"standings": {"美聯": [{"team": "Tampa Bay Rays", "record": "98-64", "pct": 0.605}]},
         "cpbl": [{"team": "中信兄弟", "record": "36-0-24", "pct": "0.600", "gb": "-"}],
         "news": {"MLB": [{"title": "MVP決選名單出爐", "link": "https://x/1", "published": ""}]}},
        dtm.datetime(2026, 11, 4))
    out = ru._render_sports_html(sports, htmllib)
    assert "戰績" not in out and "MVP決選名單出爐" in out and "MLB" in out


def test_heat_table_name_stays_out_while_hidden_and_ai_race_must_cite():
    """11-04 實信:熱度表隱藏後,結論與八段仍寫「類股熱度表」當出處;AI 模型競賽仍未附編號。"""
    import morning_report as mr
    block = mr._format_sector_heat_block({"ranked": ["半導體業"], "total_value_yi": 1,
                                          "sectors": {"半導體業": {"value_yi": 1, "median_pct": 0}}})
    assert "全信不得出現「類股熱度表」這個名稱" in block
    assert "`[OpenRouter][N12]`" in open(mr.__file__, encoding="utf-8").read()
    assert "每條 3～4 句、合計 150～240 字" in open(writing_rules.__file__, encoding="utf-8").read()


def test_full_prompt_has_no_conflicting_heat_table_citation_rule(monkeypatch):
    """外審 PR #40:九段行情觀察還要求「來源就寫類股熱度表、信件內有刊出」,與隱藏時的禁令矛盾。"""
    import morning_report as mr
    from test_deepseek_legacy_golden import legacy_prompt_inputs
    hidden = mr._build_prompt(*legacy_prompt_inputs())
    assert "這張表信件內有刊出" not in hidden and "來源寫「本報類股統計」" in hidden
    monkeypatch.setattr(mr, "SHOW_SECTOR_HEAT_TABLE", True)
    shown = mr._build_prompt(*legacy_prompt_inputs())
    assert "這張表信件內有刊出" in shown and "全信不得出現" not in shown
