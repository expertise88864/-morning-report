import podcast_comparison as pc
import podcast_topic as pt
from test_podcast_comparison import fixture


def test_same_company_different_event_is_not_agreement():
    packet, row = fixture()
    packet['news'][0]['summary'] = '公司董事會決議配發股利每股五元'
    row['podcast_comparisons'][0]['news_excerpt'] = packet['news'][0]['summary']
    assert any('主題不同' in e for e in pc.row_problems(row, packet))
    assert pc.prose(row, packet) == ''
    row['podcast_comparisons'][0]['relation'] = 'not_comparable'
    assert any('主題不同' in e for e in pc.row_problems(row, packet))
    assert pc.prose(row, packet) == ''


def test_same_subject_can_be_incomparable_due_to_different_conditions():
    packet, row = fixture()
    row['podcast_comparisons'][0]['relation'] = 'not_comparable'
    row['podcast_comparisons'][0]['comparison'] = '同為訂單但期間不同，不能直接驗證'
    assert pc.row_problems(row, packet) == []
    assert '暫不可比' in pc.prose(row, packet)


def test_narrow_detector_does_not_claim_semantic_truth():
    assert pt.mismatch('需求持續增加', '公司配息提高')
    assert not pt.mismatch('需求持續增加', '需求大幅下降')  # Same subject, opposite views.
    assert not pt.mismatch('orders remain strong', '需求持續增加')
    assert not pt.mismatch('訂單與產能增加', '量產時間延後')
    assert not pt.mismatch('尚未收錄的新議題', '公司配息提高')  # Unknown, not proven related.
    assert not pt.topics('boarders')  # ASCII token boundaries.


def test_malformed_source_collections_fail_closed_without_throwing():
    for value in (None, 1, 'bad', [None]):
        packet, row = fixture()
        packet['podcast_context'] = value
        assert pc.row_problems(row, packet)
        assert pc.prose(row, packet) == ''
        assert pc.validate({'top_news_analysis': value}, {}) == []
