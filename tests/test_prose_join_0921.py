from prose_join import sentences
from news_display_quality import relevant


def test_sentence_fields_keep_uncertainty_and_numbers():
    assert sentences(["可能往後。", "，因為缺乏證據。", "5.2%並非已驗證門檻"] ) == (
        "可能往後。 因為缺乏證據。 5.2%並非已驗證門檻。")


def test_empty_fields_and_question_marks():
    assert sentences([None, "", "，", "會成立嗎？", "未確認"]) == "會成立嗎？ 未確認。"


def test_periods_survive_outer_sentence_rendering():
    from analysis_render_depth import _join_sentence
    for text in ["Revenue may decline.", "尚未確認．", "待確認!"]:
        assert _join_sentence(sentences([text])) == text


def test_local_discussion_repost_not_reporting():
    assert not relevant("選情", "1326 台化- 市長回鍋選議員 - 股市爆料同學會 - CMoney")
    assert relevant("選情", "市長回鍋選議員 - 自由時報")
    assert relevant("選情", "議員討論CMoney相關政策 - 自由時報")
