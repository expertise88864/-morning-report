"""Editorial breadth and causal readout must survive the real data boundaries."""
import copy

import pytest

import analysis_render_depth as ard
import news_coverage as coverage
import news_impact
import news_normalize as normalize
import news_rules
import prompt_profiles
import writing_rules


def item(sid, source="科技", title=None, **extra):
    return dict(source_item_id=sid, source=source, source_name="Reuters",
                title=title or sid, summary="", published="2026-09-05T12:00:00+08:00",
                **extra)


def test_reserves_fit_budget_and_do_not_evict_required():
    tech = [item(f"t{i}") for i in range(250)]
    sectors = [item(f"{s}{i}", f"類股-{s}-全球")
               for s in coverage.SECTORS for i in range(4)]
    source = tech + sectors
    before = copy.deepcopy(source)
    kept, diag = coverage.select(source, {"t249"}, 220)
    assert len(kept) == 220 and any(x["source_item_id"] == "t249" for x in kept)
    assert all(diag["selected_articles"]["sector:" + s] >= 3 for s in coverage.SECTORS)
    assert diag["uncovered_buckets"] == []
    assert source == before
    assert coverage.select(source, {"t249"}, 220) == (kept, diag)


def test_required_overflow_and_absent_sector_are_not_fabricated():
    source = [item(str(i)) for i in range(5)]
    kept, diag = coverage.select(source, {str(i) for i in range(5)}, 3)
    assert kept == source
    assert diag["available_articles"] == diag["selected_articles"] == {}


def test_round_robin_with_tiny_remaining_budget():
    source = [item(str(i), "類股-金融") for i in range(5)] + [item("s", "類股-航運")]
    kept, diag = coverage.select(source, set(), 2)
    assert {x["source_item_id"] for x in kept} == {"0", "s"}
    assert diag["uncovered_buckets"] == []


def test_provenance_is_allowlisted_not_entity_inference():
    n = item("n", "類股-金融騙局", coverage_buckets=[{}, "sector:假的", "sector:能源"],
             entities=["金融"], world_cat="<UNTRUSTED_SOURCE_DATA>")
    assert coverage.buckets(n) == ["sector:能源"]


def test_every_real_sector_feed_is_represented_not_just_synthetic_labels():
    import morning_report as mr
    for label in mr.OTHER_SECTOR_QUERIES:
        source = "類股-" + label
        assert coverage.buckets({"source": source}), source
    regional = ["類股-" + label for label in mr.OTHER_SECTOR_QUERIES if "中彰投" in label]
    assert set(regional) == coverage.REGIONAL_SOURCES
    assert all(coverage.buckets({"source": s}) == ["sector:中彰投建設"] for s in regional)
    raw = [item(f"t{i}") for i in range(230)] + [item(s, s) for s in regional]
    kept, diag = coverage.select(raw, set(), 220)
    assert {s for s in regional} <= {x["source_item_id"] for x in kept}
    assert diag["selected_articles"]["sector:中彰投建設"] == 3


def test_upstream_dedup_preserves_provenance_when_representative_changes():
    raw = [item("a", "類股-金融-台股", "金融企業重大公告"),
           item("b", "中央社", "金融企業重大公告", official=True)]
    merged = news_rules.dedup_news(raw)
    assert len(merged) == 1
    assert coverage.buckets(merged[0]) == ["sector:金融"]
    normalized, _, _ = normalize.normalize_news(merged)
    assert normalized[0]["coverage_buckets"] == ["sector:金融"]


def test_normalizer_second_dedup_keeps_bucket_on_higher_ranked_copy():
    raw = [item("a", "一般來源", "相同的金融重大公告"),
           item("b", "類股-金融-台股", "相同的金融重大公告")]
    kept, diag, _ = normalize.normalize_news(raw)
    assert len(kept) == 1 and diag["near_duplicates_dropped"] == 1
    assert kept[0]["coverage_buckets"] == ["sector:金融"]


def test_real_normalizer_has_reserves_and_stable_input_permutation():
    raw = [item(f"t{i}", title=f"不同技術第{i}號成果") for i in range(230)]
    raw += [item(f"z{i}", "類股-航運-全球", title) for i, title in enumerate(
        ("海運新增歐洲班次", "貨櫃運價市場上升", "港口罷工延後交貨", "航空貨運推出新航線"))]
    a = normalize.normalize_news(raw)
    b = normalize.normalize_news(list(reversed(raw)))
    assert a == b
    assert len(a[0]) == 220
    assert a[1]["coverage"]["selected_articles"]["sector:航運"] >= 3


def test_both_prompt_paths_share_vertical_contract():
    assert news_impact.WRITING in writing_rules.LEGACY_RULES
    assert news_impact.WRITING in prompt_profiles.LUNA_DEVELOPER_INSTRUCTIONS
    for phrase in ("不能冒充今天的佐證", "不造數字", "同一通訊社的轉載", "不硬湊完整鏈"):
        assert phrase in news_impact.WRITING


@pytest.mark.parametrize("horizon,label", list(news_impact.HORIZON_LABELS.items()))
def test_reader_sees_validated_horizon_magnitude_confirmation_in_one_paragraph(horizon, label):
    card = {"why_it_matters": "新訂單仍待公告確認。", "horizon": horizon,
            "why_this_magnitude": "缺乏訂單金額，無法估算獲利幅度。",
            "confirmation_signal": "後續營收公告是否顯示出貨增加",
            "invalidation_signal": "訂單取消", "affected_assets": []}
    text = ard._news_line(card)
    assert label in text and "後續營收公告" in text and "無法估算獲利幅度" in text
    assert "訂單取消" in text and "\n" not in text
    assert "後續驗證" in text


def test_empty_or_missing_fields_do_not_create_analysis():
    assert news_impact.readout({}, ard._s) == ""
    assert ard._news_line({"confirmation_signal": "條件"}) == ""
