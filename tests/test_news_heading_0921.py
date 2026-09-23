from news_heading import companies


def test_multi_company_does_not_attribute_to_short_parent_name():
    packet = {'tw_universe': [{'code': '1301', 'name': '台塑'},
                             {'code': '6505', 'name': '台塑化'},
                             {'code': '1303', 'name': '南亞'}]}
    assert companies({'title': '南亞、台塑化季底補漲'}, packet) == '南亞（1303）、台塑化（6505）'
    assert companies({'title': '台塑與台塑化'}, packet) == '台塑（1301）、台塑化（6505）'


def test_market_story_does_not_inherit_query_entity():
    assert companies({'title': '大盤上漲2330點', 'entities': ['2330']},
                     {'tw_universe': [{'code': '2330', 'name': '台積電'}]}) == ''


def test_shortened_ccl_headline_does_not_inherit_peer_asset():
    packet = {'tw_universe': [{'code': '2383', 'name': '台光電'},
                              {'code': '6274', 'name': '台燿'}]}
    item = {'title': '台燿海內外擴產', 'company_label': '2383',
            'entities': ['2383']}
    assert companies(item, packet) == '台燿（6274）'
    assert companies({'title': '台光電、台燿海內外擴產'}, packet) == (
        '台光電（2383）、台燿（6274）')


def test_multi_company_source_title_is_not_truncated_by_primary_subject():
    from analysis_render_depth import _news_line
    packet = {'tw_universe': [{'code': '2383', 'name': '台光電'},
                              {'code': '6274', 'name': '台燿'}],
              'news': [{'source_item_id': 'ccl',
                        'title': '台光電、台燿海內外擴產',
                        'url': 'https://example.com/ccl'}]}
    card = {'source_item_id': 'ccl', 'why_it_matters': '兩家公司均有擴充計畫。',
            'affected_assets': [{'asset_id': '2383'}]}
    rendered = _news_line(card, packet)
    assert '[台光電、台燿海內外擴產](https://example.com/ccl)' in rendered
    assert '**台光電（2383）、台燿（6274）**｜' in rendered


def test_foreign_company_and_word_boundary():
    assert companies({'title': 'Microsoft announces results'}, {}) == 'Microsoft（MSFT）'
    assert companies({'title': 'pharmaceutical demand'}, {}) == ''


def test_short_company_names_inside_non_company_phrases_are_not_attributed():
    packet = {'tw_universe': [{'code': '2330', 'name': '台積電'},
                              {'code': '1303', 'name': '南亞'},
                              {'code': '1216', 'name': '統一'},
                              {'code': '3045', 'name': '台灣大'}]}
    assert companies({'title': '台積電東南亞擴廠'}, packet) == '台積電（2330）'
    assert companies({'title': '統一投信新ETF掛牌'}, packet) == ''
    assert companies({'title': '統一發票中獎號碼'}, packet) == ''
    assert companies({'title': '台灣大學研究'}, packet) == ''
    assert companies({'title': '南亞、台積電在東南亞擴廠'}, packet) == (
        '南亞（1303）、台積電（2330）')


def test_one_company_with_two_share_classes_has_one_heading():
    assert companies({'title': 'Alphabet 財報優於預期'}, {}) == 'Alphabet'
    assert companies({'title': 'Alphabet（GOOG）財報優於預期'}, {}) == 'Alphabet（GOOG）'


def test_card_keeps_full_title_and_source_link():
    from analysis_render_depth import _news_line
    packet = {'tw_universe': [{'code': '1301', 'name': '台塑'},
                             {'code': '6505', 'name': '台塑化'}],
              'news': [{'source_item_id': 'x', 'title': '台塑化宣布油價不調整',
                        'url': 'https://example.com/news', 'source_name': '原始來源'}]}
    result = _news_line({'source_item_id': 'x', 'why_it_matters': '價差尚待核對。'}, packet)
    assert '**台塑化（6505）**｜' in result
    assert '[台塑化宣布油價不調整](https://example.com/news)' in result
    assert '價差尚待核對。' in result
