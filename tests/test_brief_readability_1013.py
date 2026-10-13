"""Reader preferences must not weaken evidence or quality checks."""
from bs4 import BeautifulSoup
import pytest

import morning_report as mr
import reader_prose
import render_utils as render
from test_golden_faults import _golden_quotes


def test_post_send_contract_has_budget_without_extending_report_budget():
    from pathlib import Path
    import yaml
    workflow = yaml.safe_load((Path(mr.__file__).parent / '.github/workflows/morning-report-b.yml').read_text(encoding='utf-8'))
    job = workflow['jobs']['send-report']
    contract = next(step for step in job['steps'] if step.get('id') == 'contract')
    report = next(step for step in job['steps'] if step.get('id') == 'report')
    assert 'RUN_BUDGET_SECONDS' not in report['env']
    assert mr.RUN_BUDGET_SECONDS == 2700
    assert job['timeout-minutes'] >= 45 + contract['timeout-minutes'] + 5
    assert contract['timeout-minutes'] == 20
    assert 'test_state_schema_contract.py' in contract['run']
    assert not contract.get('continue-on-error', False)


def test_group_citations_link_each_publisher_to_its_own_collected_article():
    title = "Fed Hammack 利率維持高檔 通膨壓力延續"
    long_url = "https://news.google.com/rss/articles/" + "A" * 800
    sources = render.build_news_link_index([
        {"title": title, "source_name": "CNBC", "link": long_url},
        {"title": title, "source_name": "鉅亨", "link": "https://news.cnyes.com/news/id/123"},
    ])
    html = render._dim_source_citations(render._md_to_html(f"- {title}（CNBC／鉅亨）"))
    soup = BeautifulSoup(render._link_source_citations(html, sources), "html.parser")
    assert [(a.get_text(), a['href']) for a in soup.find_all('a')] == [
        ("（CNBC）", long_url), ("（鉅亨）", "https://news.cnyes.com/news/id/123")]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://example.com/" + "A" * 2050])
def test_group_citations_do_not_bypass_url_safety(url):
    text = "Fed 通膨壓力利率維持高檔"
    sources = [{"t": render._cite_sig_tokens(text), "u": url, "m": "cnbc"}]
    html = render._dim_source_citations(render._md_to_html(f"{text}（CNBC／鉅亨）"))
    assert not BeautifulSoup(render._link_source_citations(html, sources), "html.parser").find('a')


def test_ambiguous_publisher_stays_unlinked_and_other_publisher_can_link():
    title = "Fed 通膨壓力利率維持高檔"
    sources = render.build_news_link_index([
        {"title": title, "source_name": media, "link": f"https://example.com/{i}"}
        for i, media in enumerate(["CNBC", "CNBC", "鉅亨"])])
    html = render._dim_source_citations(render._md_to_html(f"{title}（CNBC／鉅亨）"))
    soup = BeautifulSoup(render._link_source_citations(html, sources), "html.parser")
    assert [a['href'] for a in soup.find_all('a')] == ['https://example.com/2']


def test_explanations_are_preserved_without_schema_labels():
    original = "所以會怎樣：能源成本提高。**後續可能影響**：物價壓力延續。"
    assert reader_prose.clean_text(original) == "能源成本提高。物價壓力延續。"
    url = "https://example.com/後續可能影響:"
    assert reader_prose.clean_text(url) == url


def test_market_overview_is_integrated_and_hidden_cards_stay_hidden():
    quotes = _golden_quotes()
    quotes.update({"TARGET_SESSION": "2026-10-13", "LAST_TRADING_SESSION": "2026-10-12",
        "TAIEX_PRED": {"pred_open": 48985, "last_close": 49301.96, "consensus": "全部偏空"},
        "BREADTH": {"total": 1095, "advance": 544, "decline": 457, "advance_ratio": 49.7},
        "TW_CALENDAR": {"ipos": [{"name": "Hidden subscription"}]}})
    result = mr.render_html(quotes, {"error": "fixture"}, {"error": "fixture"},
                            "## 七、昨夜三大重點\n保留新聞", "2026-10-13", "每日報")
    soup = BeautifulSoup(result, "html.parser")
    assert "市場結構訊號" not in result and "台股行事曆" not in result
    overview = soup.find('h2', string="五、行情與開盤展望")
    assert overview is not None
    headings = [h.get_text() for h in overview.find_all_next('h3')][:3]
    assert headings == ["美股收盤行情", "加權指數開盤預測",
                        "個股開盤預測與公允價（2330 / 0050 開盤；00662 公允價）"]
    assert result.index("大盤成交額與市場廣度") < result.index("個股開盤預測與公允價")


def test_sector_order_and_rotation_remains_trimmed_with_watchlist(monkeypatch):
    quotes = _golden_quotes()
    quotes["TW_UNIVERSE_SNAPSHOT"] = [{"code": "2330", "name": "台積電", "close": 2420,
        "day_pct": 0, "ranking_score": 40, "smart_money": {"score": 55, "tags": []}}]
    quotes["BREADTH"] = {"total": 100, "advance_ratio": 50}
    quotes["SECTOR_HEAT"] = {"ranked": ["半導體業"], "sectors": {"半導體業": {}}}
    monkeypatch.setattr(mr, "_sector_rotation", lambda _: {"fixture": True})
    monkeypatch.setattr(mr, "_render_sector_rotation_table", lambda *_: "<p>ROTATION_FIXTURE</p>")
    monkeypatch.setattr(mr, "_RENDER_TOP5_CARD", True)
    def render_email():
        return mr.render_html(quotes, {"error": "fixture"}, {"error": "fixture"},
                              "## 分析\n保留", "2026-10-13", "每日報")
    result = render_email()
    watchlist = BeautifulSoup(result, "html.parser").find('h2', string=lambda s: s and 'Top 1' in s)
    assert watchlist is not None
    assert result.index(watchlist.get_text()) < result.index("類股熱度表") < result.index("ROTATION_FIXTURE")
    monkeypatch.setenv("EMAIL_OVERFLOW_MODE", "trim")
    monkeypatch.setattr(mr, "_estimated_email_kb", lambda _: 999)
    trimmed = render_email()
    assert "ROTATION_FIXTURE" not in trimmed
    assert "類股熱度表" in trimmed
