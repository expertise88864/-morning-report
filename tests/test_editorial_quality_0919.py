import news_display_quality as quality
from reader_fact_labels import WRITING
from writing_rules import LEGACY_RULES, LUNA_WRITING


def test_discussion_wrapper_does_not_displace_actual_article():
    rows = [{"title": "討論牆 | 網球》Salisbury退休", "link": "discussion"},
            {"title": "網球》Salisbury退休 - TSNA", "link": "article"}]
    assert quality.unique(rows) == [rows[1]]


def test_promotion_filter_preserves_concrete_builder_news():
    assert not quality.relevant("建商動態", "【青安上路 成家更安心 分享】品牌建商 #聚富建設")
    assert quality.relevant("建商動態", "利率16%壓垮小建商？台中建案全數解約")
    assert quality.relevant("建商動態", "國雄建設推案遭裁罰 #國雄建設")


def test_both_analysis_paths_receive_inference_boundaries():
    assert WRITING in LEGACY_RULES and WRITING in LUNA_WRITING
    assert "不能證明同一群人的避險目的" in WRITING
    assert "不把開盤估值改寫成買進" in WRITING
