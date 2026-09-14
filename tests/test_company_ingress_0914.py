"""Fixed-query company identity must be supported by article text, offline."""
import datetime as dt
from types import SimpleNamespace

import pytest

import morning_report as mr
import news_memory


def fetch(monkeypatch, label, title, summary=""):
    entry = dict(title=title, summary=summary, link="https://example.invalid/article",
                 published="Mon, 14 Sep 2026 00:00:00 GMT")
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda url: SimpleNamespace(entries=[entry]))
    monkeypatch.setattr(mr.requests, "get", lambda *a, **kw: pytest.fail("network forbidden"))
    return mr._process_feed_item(dict(source=f"Google:{label}", label=label,
                                     kind="company", url="https://example.invalid/feed"),
                                 dt.datetime(2026, 9, 13, tzinfo=dt.timezone.utc))


@pytest.mark.parametrize("label,title", [
    ("2881", "中信金(2891) 個股概覽 | 個股 - 股市 - CMoney"),
    ("2382", "台積電新廠動工 AI伺服器供應鏈關注"),
    ("MU", "記憶體產業展望 MUSIC 活動登場"),
    ("2330", "大盤上漲 2330 點 財報法說資本支出焦點"),
    ("ARM", "Pharmaceutical materials demand grows"),
    ("2603", "長榮航空公布新航線"),
    ("AMD", "美超微公布AI伺服器新訂單"),
    ("2308", "Delta Air Lines reports quarterly earnings"),
    ("2308", "The river delta faces flood warnings"),
])
def test_unrelated_query_results_cannot_become_issuer_news(monkeypatch, label, title):
    assert fetch(monkeypatch, label, title) == []


@pytest.mark.parametrize("label,title", [
    ("2881", "富邦金公布獲利"), ("2330", "TSMC announces new factory"),
    ("AVGO", "Broadcom reports earnings"), ("AMAT", "Applied Materials raises outlook"),
    ("NFLX", "Netflix announces earnings"), ("2382", "廣達新廠正式啟用"),
    ("2891", "台灣人壽公布投資計畫"), ("2882", "國泰世華公布人事異動"),
    ("2603", "長榮航空增班；長榮海運公布營收"),
    ("AMD", "美超微與超微共同展示伺服器"),
    ("3231", "Wistron posts record revenue"),
    ("2881", "Fubon Financial reports earnings"),
    ("2603", "Evergreen Marine raises outlook"),
    ("2308", "Delta Electronics reports quarterly earnings"),
    ("2308", "台達電表示 Delta 電源產品需求增長"),
])
def test_named_company_and_existing_subsidiary_coverage_remain(monkeypatch, label, title):
    rows = fetch(monkeypatch, label, title)
    assert len(rows) == 1
    assert rows[0]["company_label"] == label


def test_summary_name_supports_label_and_memory(monkeypatch):
    rows = fetch(monkeypatch, "2881", "金控公布月度獲利", "富邦金公布最新業績")
    assert len(rows) == 1
    assert news_memory.subjects(rows[0]) == ["富邦金"]


@pytest.mark.parametrize("query,label", mr.GOOGLE_NEWS_COMPANIES)
def test_configured_named_coverage_does_not_disappear(monkeypatch, query, label):
    # Every configured query starts with a declared company/subsidiary name.
    # Query words are used only to construct fixtures, never issuer aliases.
    rows = fetch(monkeypatch, label, query.split()[0] + "公布最新營運進展")
    assert len(rows) == 1
