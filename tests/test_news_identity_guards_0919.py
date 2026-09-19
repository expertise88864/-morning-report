from copy import deepcopy

import pytest

from news_identity_guards import static_stock_page, merge_compatible


@pytest.mark.parametrize('title', [
    '中信金(2891) 個股概覽 | 個股 - 股市 - CMoney',
    '國泰金個股概覽', '台積電（2330）個股總覽',
])
def test_static_navigation_titles(title):
    assert static_stock_page(title)


@pytest.mark.parametrize('title', [
    '國泰金公布季度獲利', '個股概覽改版：新增財報欄位',
    '台積電個股概覽揭露資料錯誤，平台回應',
    '平台新增台積電個股概覽',
])
def test_actual_stories_are_not_static_pages(title):
    assert not static_stock_page(title)


@pytest.mark.parametrize('a,b', [
    ('台積電證實擴廠計畫將投資100億元', '台積電否認擴廠計畫將投資100億元'),
    ('台積電宣布擴廠計畫將投資100億元', '台積電宣布擴廠計畫將投資200億元'),
    ('台積電擴廠計畫將如期進行', '台積電擴廠計畫將不如期進行'),
    ('Company confirmed a major new factory', 'Company denied a major new factory'),
    ('公司公布本季營收成長1.2%', '公司公布本季營收成長12%'),
    ('公司宣布擴廠計畫投資一百億元', '公司宣布擴廠計畫投資兩百億元'),
    ('市場盛傳公司擴廠計畫將投資一百億元', '市場確認公司擴廠計畫將投資一百億元'),
    ('公司宣布投資100美元', '公司宣布投資100日圓'),
    ('公司宣布新廠計畫預計斥資十億', '公司宣布新廠計畫預計斥資百億'),
    ('公司宣布新廠計畫預計斥資三點五億', '公司宣布新廠計畫預計斥資四點五億'),
])
def test_material_changes_are_not_fuzzy_merged(a, b):
    import news_rules
    assert not merge_compatible(a, b)
    rows = [{'title': a}, {'title': b}]
    assert len(news_rules.dedup_news(deepcopy(rows), similarity=.1)) == 2


def test_navigation_pages_do_not_consume_analysis_slots(capsys):
    import news_rules
    rows = [{'title': '中信金(2891) 個股概覽 | 個股 - 股市 - CMoney'},
            {'title': '中信金公布季度獲利'}]
    before = deepcopy(rows)
    assert news_rules.dedup_news(rows) == rows[1:]
    assert 'static_pages=1' in capsys.readouterr().err
    assert rows == before


def test_one_sided_ticker_is_not_a_material_amount():
    import news_rules
    rows = [{'title': '台積電(2330)法說會登場 外資看好AI需求', 'source_name': '媒體甲'},
            {'title': '台積電法說會登場 外資看好AI需求', 'source_name': '媒體乙'}]
    assert merge_compatible(rows[0]['title'], rows[1]['title'])
    kept = news_rules.dedup_news(rows)
    assert len(kept) == 1 and kept[0]['merged_n'] == 2


@pytest.mark.parametrize('left,right', [
    ('台積電第3季營收創新高', '台積電第三季營收創新高'),
    ('台積電Q3營收創新高', '台積電第三季營收創新高'),
    ('公司宣布投資一百億元擴廠', '公司宣布投資100億元擴廠'),
    ('公司宣布投資三點五億元擴廠', '公司宣布投資3.5億元擴廠'),
    ('鴻海Q3營收1.5兆元創新高', '鴻海Q3營收1.5兆創新高'),
    ('公司宣布投資400億美元擴廠', '公司宣布投資400億美金擴廠'),
    ('公司宣布投資100億元擴廠', '公司宣布投資新台幣100億擴廠'),
])
def test_equivalent_notation_does_not_veto_merge(left, right):
    import news_rules
    assert merge_compatible(left, right)
    rows = [{'title': left, 'source_name': '媒體甲'},
            {'title': right, 'source_name': '媒體乙'}]
    kept = news_rules.dedup_news(rows, similarity=.1)
    assert len(kept) == 1 and kept[0]['merged_n'] == 2
