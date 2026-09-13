import copy

import podcast_comparison as pc


def fixture():
    packet = {'news': [{'source_item_id': 'n1', 'summary': '公司本季訂單年增兩成且維持全年展望',
                        'published': '2026-09-12T07:00:00+08:00'}],
              'podcast_context': {'episodes': [{'opinion_id': 'opinion:one',
                  'show': '節目', 'title': '需求追蹤', 'published_at': '2026-09-11T06:00:00+08:00',
                  'summary_points': ['主持人認為訂單將成長但仍須確認量產狀況']}]}}
    row = {'source_item_id': 'n1', 'podcast_comparisons': [{
        'opinion_id': 'opinion:one', 'opinion_excerpt': '訂單將成長但仍須確認量產狀況',
        'news_excerpt': '公司本季訂單年增兩成', 'relation': 'agreement',
        'comparison': '方向相近，但訂單成長不等於量產已完成',
        'open_question': '後續量產及出貨是否兑现'}]}
    return packet, row


def test_comparison_has_attribution_and_does_not_claim_verification():
    packet, row = fixture()
    before = copy.deepcopy((packet, row))
    assert pc.validate({'top_news_analysis': [row]}, packet) == []
    text = pc.prose(row, packet)
    assert '節目「需求追蹤」' in text and '2026-09-11' in text
    assert '節目較早發布' in text and '非事實佐證' in text and '待驗證' in text
    assert (packet, row) == before


def test_wrong_episode_news_or_excerpt_cannot_render():
    for key, value in [('opinion_id', 'opinion:fake'), ('news_excerpt', '不存在的新聞原文資料'),
                       ('opinion_excerpt', '其他節目的意見不能引用'), ('open_question', ''),
                       ('relation', 'confirmed')]:
        packet, row = fixture()
        row['podcast_comparisons'][0][key] = value
        assert pc.row_problems(row, packet)
        assert pc.prose(row, packet) == ''


def test_opinion_cannot_leak_into_regular_claim_evidence():
    packet, row = fixture()
    obj = {'top_news_analysis': [row], 'claim_audit': [{'evidence_ids': ['opinion:one']}]}
    assert any('一般證據' in error for error in pc.validate(obj, packet))


def test_missing_opinion_is_allowed_but_duplicate_is_not():
    packet, row = fixture()
    assert pc.prose({'source_item_id': 'n1'}, packet) == ''
    row['podcast_comparisons'] *= 2
    assert any('重複' in error for error in pc.row_problems(row, packet))


def test_unknown_date_never_infers_chronology():
    packet, row = fixture()
    packet['podcast_context']['episodes'][0]['published_at'] = ''
    assert '日期不足，不排先後' in pc.prose(row, packet)


def render_probe():
    import analysis_render
    import fixtures_analysis as fx
    packet, row = fixture()
    obj = fx.valid_analysis()
    obj['top_news_analysis'][0].update(row)
    valid = analysis_render.render(obj, packet)
    obj['top_news_analysis'][0]['podcast_comparisons'][0]['opinion_id'] = 'opinion:fake'
    invalid = analysis_render.render(obj, packet)
    return valid, invalid


def test_real_analysis_renderer_preserves_attribution_and_blocks_bad_reference():
    valid, invalid = render_probe()
    assert 'Podcast 觀點對照' in valid and '節目「需求追蹤」' in valid
    assert 'Podcast 觀點對照' not in invalid


def validation_probe():
    import analysis_schema
    packet, row = fixture()
    obj = {'top_news_analysis': [row]}
    valid = [e for e in analysis_schema.validate(obj, packet) if 'Podcast' in e]
    row['podcast_comparisons'][0]['opinion_id'] = 'opinion:fake'
    invalid = [e for e in analysis_schema.validate(obj, packet) if 'Podcast' in e]
    return valid, invalid


def test_production_validator_reaches_opinion_reference_guard():
    valid, invalid = validation_probe()
    assert valid == []
    assert any('引用不存在' in e for e in invalid)
