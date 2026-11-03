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
