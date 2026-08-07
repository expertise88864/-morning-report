# -*- coding: utf-8 -*-
"""**2026-08-05 使用者七項回饋。**

信件內容的問題,而不是契約的問題。共同形狀:

  * 有兩項**根本不是模型寫的** —— 財經日曆與事件名稱由 Python 直接排進
    HTML,prompt 改再多也碰不到(與 2026-08-04 的 Top5 卡片同型:
    「問題在 Python 排版那一塊,而我一直在改 prompt」);
  * 有兩項是**規則沒說** —— 三大重點寫成行情摘要、內部試算寫進正文;
  * 有一項是**取材範圍不對** —— 別縣市的停車場管理要點佔掉一個政策版位。
"""
import econ_terms as et
import render_utils as ru
import writing_rules as wr


# ---------------------------------------------------------------- 英文名詞

def test_economic_indicators_lead_with_chinese():
    """使用者原話:「什麼是 AVERAGE HOURLY EARNINGS M/M、
    non-farm employment change、unemployment rate」。"""
    assert et.zh("Non-Farm Employment Change") == \
        "非農就業人數變動（Non-Farm Employment Change）"
    assert et.zh("Average Hourly Earnings m/m").startswith("平均時薪月增率")
    assert et.zh("Unemployment Rate").startswith("失業率")
    assert et.zh("WTI") == "西德州原油（WTI）"


def test_an_unknown_term_is_left_alone():
    """**硬翻比不翻更糟。** 認不得就原樣回傳。"""
    assert et.zh("Some Brand New Indicator") == "Some Brand New Indicator"
    assert et.zh("") == "" and et.zh(None) == ""


def test_case_and_spacing_variants_all_match():
    """來源字串的大小寫與連字號不穩定。"""
    for raw in ("NON-FARM EMPLOYMENT CHANGE", "Non Farm Employment Change",
                "non-farm employment change"):
        assert et.zh(raw).startswith("非農就業人數變動"), raw


def test_annotation_does_not_eat_a_longer_term_with_a_shorter_one():
    """`cpi m/m` 不能先被 `cpi` 吃掉 —— 長詞優先,而且**只掃一次**。

    突變驗證抓到兩件事:先前表裡沒有互為前綴的詞,這條測試根本沒碰到
    排序;補上 `cpi` 之後又發現逐詞替換會**巢狀套疊** ——
    `CPI m/m` 變成「消費者物價指數月增率（消費者物價指數（CPI） m/m）」。
    """
    assert "cpi" in et._TERMS, "沒有互為前綴的詞,這條測不到排序"
    out = et.annotate("CPI m/m 公布")
    assert out == "消費者物價指數月增率（CPI m/m） 公布", out
    assert out.count("（") == 1, "巢狀套疊了"


def test_the_calendar_renders_chinese_first():
    """**這一段是 Python 排的** —— 日曆的英文標題先前直接進 HTML。"""
    import datetime as dt
    html = ru._render_event_calendar_html([
        {"date": dt.date(2026, 8, 7), "time": "20:30", "impact": "high",
         "title": "Non-Farm Employment Change", "note": "預期 85K、前值 57K"}])
    assert "非農就業人數變動" in html
    assert "Non-Farm Employment Change" in html, "英文原名要留著對得上外電"


def test_the_48h_scenario_source_is_translated_too():
    """**模型看到什麼就抄什麼** —— 使用者說「未來 48 小時裡面也都有英文」。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "morning_report.py"
           ).read_text(encoding="utf-8")
    body = src[src.index("def _format_event_scenarios"):]
    body = body[:body.index("def _format_narrative_delta")]
    assert "_et.annotate(" in body, "48 小時事件的取材沒有翻譯就進了 prompt"


# ---------------------------------------------------------------- 規則

def test_the_top_three_must_be_events_not_price_moves():
    """使用者原話:「只說科技反彈、QQQ／ADR 漲等等……我要的是真正
    國際上昨夜三大發生的重大事件,而不是數據文字堆疊」。"""
    text = wr.RULES if hasattr(wr, "RULES") else _rules_text()
    assert "不是「昨夜漲了多少」" in text
    assert "QQQ 漲 3.40%" in text, "禁止的寫法要逐字舉出來 —— 抽象規則會被繞過"
    assert "至多一條可以是純市場行情事件" in text


def test_internal_model_state_stays_out_of_the_letter():
    """使用者原話:「什麼簡化版估值、資料有限、今日 rolling-origin……
    中間這些試算隱藏就好」。"""
    text = _rules_text()
    assert "R18." in text and "內部試算不進信" in text
    for term in ("rolling-origin", "機率校準", "簡化版"):
        assert term in text, f"{term} 沒有被逐字列為禁用內容"


def test_the_sector_sections_must_answer_four_questions():
    """使用者原話:「要再做更多橫向縱向的深入探討,例如對整體經濟影響、
    對 2330／0050 的影響、以及產業影響等等,目前利多還是利空」。"""
    text = _rules_text()
    for want in ("對產業", "對 2330 或 0050", "對整體經濟或市場",
                 "今天算利多還是利空"):
        assert want in text, want
    # **「偏多」不是答案 —— 要說對誰**
    assert "對設備商偏多、對成熟製程偏空" in text


def test_a_policy_already_published_cannot_be_called_pending():
    """新青安 3.0 的細項早已公布,而信裡連兩天寫「細節待官方公告」。"""
    text = _rules_text()
    assert "R20." in text and "新青安" in text
    assert "相較上次多了什麼" in text


def _rules_text() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "writing_rules.py"
            ).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 政策範圍
