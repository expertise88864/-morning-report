"""Bracket labels are formatting, not source provenance or trade evidence."""

import pytest

import analysis_origin as ao
from legacy_actor_guard import correct_reader_claims
from reader_source_line import is_source_link_line


@pytest.mark.parametrize("origin", [ao.LEGACY_PRIMARY, ao.LEGACY_AFTER_LUNA_FAILURE,
                                     ao.LUNA_SPECIALIZED])
@pytest.mark.parametrize("marker", ["", "- ", "> ", "> - "])
def test_labelled_model_summary_reaches_actual_writer_guard(origin, marker):
    raw = "## 一句話總結\r\n" + marker + "[本報解讀] 偏空，建議減碼公開範例。\r\n"
    manifest = {}
    revised = correct_reader_claims(raw, [], origin=origin, manifest=manifest)
    assert "建議減碼" not in revised
    assert "不是買賣訊號" in revised
    assert revised.startswith("## 一句話總結\r\n" + marker)
    assert revised.endswith("\r\n")
    assert manifest["llm"]["conclusion_trade_claims_neutralized"] == 1


@pytest.mark.parametrize("origin", [ao.LEGACY_PRIMARY, ao.LEGACY_AFTER_LUNA_FAILURE,
                                     ao.LUNA_SPECIALIZED])
@pytest.mark.parametrize("marker", ["", "- ", "> ", "> - "])
def test_labelled_stance_flow_reaches_actual_writer_guard(origin, marker):
    prose = "[媒體] 今天的錢明顯往電子零組件集中，這個流向與外資現貨賣超、期貨偏空的方向一致。"
    raw = "## 我的明確立場\n" + marker + prose + "\n"
    manifest = {}
    revised = correct_reader_claims(raw, [], origin=origin, manifest=manifest)
    assert "今天的錢" not in revised
    assert "不能確認為同一資金流向" in revised
    assert marker + "[媒體]" in revised
    assert revised.endswith("\n")
    assert manifest["llm"]["sector_flow_claims_neutralized"] == 2


@pytest.mark.parametrize("marker", ["", "- ", "> ", "> - "])
@pytest.mark.parametrize("url", ["https://example.test/news", "https://example.test/a_(b_(c))"])
def test_whole_source_link_keeps_literal_title_and_url(marker, url):
    line = marker + "[今天的錢明顯往電子零組件集中，建議減碼](" + url + ")\n"
    for heading in ("## 我的明確立場\n", "## 一句話總結\n"):
        manifest = {}
        raw = heading + line
        assert correct_reader_claims(raw, [{"link": url, "title": '今天的錢明顯往電子零組件集中，建議減碼'}], origin=ao.LEGACY_PRIMARY,
                                     manifest=manifest) == raw
        assert not manifest


@pytest.mark.parametrize("origin", [ao.LEGACY_PRIMARY, ao.LUNA_SPECIALIZED])
def test_bracket_label_does_not_cut_attributed_trade_fact(origin):
    raw = "## 一句話總結\n- [媒體] 外資大舉買進台積電，觀察成交量。\n"
    manifest = {}
    assert correct_reader_claims(raw, [], origin=origin, manifest=manifest) == raw
    assert not manifest


def test_self_authored_instruction_inside_brackets_is_not_a_source():
    raw = "## 一句話總結\n- [本報建議減碼公開範例]\n"
    manifest = {}
    revised = correct_reader_claims(raw, [], origin=ao.LEGACY_PRIMARY,
                                    manifest=manifest)
    assert "建議減碼" not in revised and "不是買賣訊號" in revised
    assert revised.startswith("## 一句話總結\n- ") and revised.endswith("\n")


def test_link_prefix_does_not_exempt_trailing_self_authored_advice():
    raw = "## 一句話總結\n- [原始標題](https://example.test/news) 偏空，建議減碼公開範例。\n"
    revised = correct_reader_claims(raw, [], origin=ao.LEGACY_PRIMARY, manifest={})
    assert "建議減碼" not in revised and "不是買賣訊號" in revised


@pytest.mark.parametrize("line", [
    "[標題](https://example.test/a_(b_(c)))",
    "[標題](HTTPS://example.test/a_%28b%29)  ",
    r"[標題](https://example.test/a_\(b\))",
])
def test_standalone_source_syntax_accepts_literal_http_link(line):
    assert is_source_link_line(line)


@pytest.mark.parametrize("line", [
    "[標籤] 本報文字", "[標題](https://)", "[標題](javascript:ignored)",
    "[標題](https://example.test/a) 建議買進", "[標題](https://example.test/a)(b)",
    "[標題](https://example.test/a_(b)", "[標題](https://example.test/a b)",
    "[標題](https://[invalid)/a)", None,
])
def test_standalone_source_syntax_rejects_missing_malformed_or_trailing_prose(line):
    assert not is_source_link_line(line)


def test_advice_label_does_not_become_attributed_trade_fact():
    raw = "## 一句話總結\n- [建議] 外資買進台積電。\n"
    revised = correct_reader_claims(raw, [], origin=ao.LEGACY_PRIMARY, manifest={})
    assert "買進" not in revised and "不是買賣訊號" in revised


@pytest.mark.parametrize("origin", [ao.LEGACY_PRIMARY, ao.LEGACY_AFTER_LUNA_FAILURE,
                                     ao.LUNA_SPECIALIZED])
@pytest.mark.parametrize("marker", ["", "- ", "> ", "> - "])
@pytest.mark.parametrize("url", ["https://example.test/news",
                                "https://example.test/a_(b_(c))",
                                r"https://example.test/a_\(b\)"])
def test_inline_citation_then_attributed_action_keeps_literal_fact(origin, marker, url):
    raw = "## 一句話總結\r\n" + marker + "[原始標題](" + url + ") 外資買進台積電。\r\n"
    manifest = {}
    assert correct_reader_claims(raw, [{"link": url, "title": '原始標題'}], origin=origin, manifest=manifest) == raw
    assert not manifest


@pytest.mark.parametrize("origin", [ao.LEGACY_PRIMARY, ao.LEGACY_AFTER_LUNA_FAILURE,
                                     ao.LUNA_SPECIALIZED])
@pytest.mark.parametrize("marker", ["", "- ", "> ", "> - "])
def test_inline_citation_does_not_turn_following_advice_into_a_fact(origin, marker):
    raw = "## 一句話總結\n" + marker + "[原始標題](https://example.test/a_(b)) 建議外資買進台積電。\n"
    revised = correct_reader_claims(raw, [], origin=origin, manifest={})
    assert "買進" not in revised and "不是買賣訊號" in revised

@pytest.mark.parametrize("origin", [ao.LEGACY_PRIMARY, ao.LEGACY_AFTER_LUNA_FAILURE,
                                     ao.LUNA_SPECIALIZED])
@pytest.mark.parametrize("marker", ["", "- ", "> ", "> - "])
@pytest.mark.parametrize("citation", [
    "[外資買進台積電](https://example.test/news)",
    "[建議減碼的原始標題](https://example.test/a_(b_(c)))",
    r"[原始標題](https://example.test/買進_\(b\))",
])
def test_inline_quoted_title_and_url_are_not_model_authored_trade_prose(origin, marker, citation):
    raw = "## 一句話總結\r\n" + marker + citation + "  外資買進台積電。\r\n"
    manifest = {}
    url = citation[citation.index("](") + 2:-1]
    assert correct_reader_claims(raw, [{"link": url, "title": citation[1:citation.index("](")]}], origin=origin, manifest=manifest) == raw
    assert not manifest


@pytest.mark.parametrize("origin", [ao.LEGACY_PRIMARY, ao.LEGACY_AFTER_LUNA_FAILURE,
                                     ao.LUNA_SPECIALIZED])
@pytest.mark.parametrize("marker", ["", "- ", "> ", "> - "])
def test_inline_quoted_trade_title_survives_neutralizing_own_advice(origin, marker):
    citation = "[建議減碼的原始標題](https://example.test/買進_(b))"
    raw = "## 一句話總結\n" + marker + citation + "  建議外資買進台積電。\n"
    manifest = {}
    revised = correct_reader_claims(raw, [{"link": "https://example.test/買進_(b)", "title": '建議減碼的原始標題'}], origin=origin, manifest=manifest)
    assert revised.startswith("## 一句話總結\n" + marker + citation + "  ")
    assert revised.endswith("\n") and "不是買賣訊號" in revised
    assert "建議外資買進" not in revised
    assert manifest["llm"]["conclusion_trade_claims_neutralized"] == 1


@pytest.mark.parametrize("origin", [ao.LEGACY_PRIMARY, ao.LEGACY_AFTER_LUNA_FAILURE,
                                     ao.LUNA_SPECIALIZED])
@pytest.mark.parametrize("marker", ["", "- ", "> ", "> - "])
def test_inline_quoted_flow_title_survives_neutralizing_own_flow_claim(origin, marker):
    citation = "[今天的錢明顯往電子零組件集中](https://example.test/news)"
    prose = "今天的錢明顯往電子零組件集中，這個流向與外資現貨賣超、期貨偏空的方向一致。"
    raw = "## 我的明確立場\n" + marker + citation + "  " + prose + "\n"
    manifest = {}
    revised = correct_reader_claims(raw, [{"link": "https://example.test/news", "title": '今天的錢明顯往電子零組件集中'}], origin=origin, manifest=manifest)
    assert revised.startswith("## 我的明確立場\n" + marker + citation + "  ")
    assert "不能確認為同一資金流向" in revised and revised.endswith("\n")
    assert manifest["llm"]["sector_flow_claims_neutralized"] == 2
