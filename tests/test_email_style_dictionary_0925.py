"""Archived-mail and stripped-CSS regressions for safe style factoring."""
import gzip
from pathlib import Path
import re

from bs4 import BeautifulSoup
import pytest

import email_style_dictionary
import email_syntax_compact
import email_content_audit


ROOT = Path(__file__).resolve().parents[1]
CRITICAL = {"font-size", "line-height", "text-align"}
SEMANTIC_COLORS = {"#fff", "#ffffff", "white", "#dc2626", "#16a34a", "#b91c1c"}


def _archived_html(day):
    path = ROOT / "state" / "emails" / f"{day}.html.gz"
    if not path.is_file():
        pytest.skip(f"{day} archived mail is unavailable after retention; not a pass")
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return stream.read()


@pytest.mark.parametrize("day", ("2026-09-20", "2026-09-21", "2026-09-22",
                                      "2026-09-23", "2026-09-24", "2026-09-25"))
def test_archived_week_preserves_text_links_and_tables(day):
    original = _archived_html(day)
    result = email_syntax_compact.compact(email_style_dictionary.compact(original))
    before, after = BeautifulSoup(original, "html.parser"), BeautifulSoup(result, "html.parser")
    assert tuple(before.stripped_strings) == tuple(after.stripped_strings)
    assert [(a.get_text(), a.get("href")) for a in before.find_all("a")] == [
        (a.get_text(), a.get("href")) for a in after.find_all("a")]
    for name in ("table", "tr", "td", "th"):
        assert len(before.find_all(name)) == len(after.find_all(name))
    old_rules, new_rules = _simple_class_styles(original), _simple_class_styles(result)
    old_all = [tag for tag in before.find_all(True) if tag.name != "style"]
    new_all = [tag for tag in after.find_all(True) if tag.name != "style"]
    assert len(old_all) == len(new_all)
    for old, new in zip(old_all, new_all):
        old_css, new_css = {}, {}
        for name in old.get("class", []):
            old_css.update(old_rules.get(name, {}))
        for name in new.get("class", []):
            new_css.update(new_rules.get(name, {}))
        old_css.update(_declarations(old))
        new_css.update(_declarations(new))
        assert old_css == new_css
    old_nodes, new_nodes = _without_sheets(original).find_all(True), _without_sheets(result).find_all(True)
    assert len(old_nodes) == len(new_nodes)
    for old, new in zip(old_nodes, new_nodes):
        assert old.name == new.name
        old_css, new_css = _declarations(old), _declarations(new)
        for key in CRITICAL:
            assert old_css.get(key) == new_css.get(key)
        if old_css.get("color") in SEMANTIC_COLORS:
            assert old_css["color"] == new_css.get("color")


def _declarations(tag):
    return {name.strip().lower(): value.strip().lower()
            for part in tag.get("style", "").split(";") if ":" in part
            for name, _, value in (part.partition(":"),)}


def _without_sheets(markup):
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup.find_all("style"):
        tag.decompose()
    for tag in soup.find_all(True):
        tag.attrs.pop("class", None)
    return soup


def _simple_class_styles(markup):
    return {name: {key.strip().lower(): value.strip().lower()
                   for part in body.split(";") if ":" in part
                   for key, _, value in (part.partition(":"),)}
            for name, body in re.findall(r"\.([sz]\d+)\{([^{}]*)\}", markup)}


def test_archived_mail_is_below_clipping_line_without_dropping_content():
    original = _archived_html("2026-09-25")
    result = email_syntax_compact.compact(email_style_dictionary.compact(original))
    assert len(original.encode("utf-8")) > 102 * 1024
    assert len(result.encode("utf-8")) < 102 * 1024

    before = BeautifulSoup(original, "html.parser")
    after = BeautifulSoup(result, "html.parser")
    assert tuple(before.stripped_strings) == tuple(after.stripped_strings)
    assert [(a.get_text(), a.get("href")) for a in before.find_all("a")] == [
        (a.get_text(), a.get("href")) for a in after.find_all("a")]
    for name in ("table", "tr", "td", "th"):
        assert len(before.find_all(name)) == len(after.find_all(name))
    old_rules, new_rules = _simple_class_styles(original), _simple_class_styles(result)
    old_all, new_all = before.find_all(True), after.find_all(True)
    old_all = [tag for tag in old_all if tag.name != "style"]
    new_all = [tag for tag in new_all if tag.name != "style"]
    assert len(old_all) == len(new_all)
    for old, new in zip(old_all, new_all):
        old_css, new_css = {}, {}
        for name in old.get("class", []):
            old_css.update(old_rules.get(name, {}))
        for name in new.get("class", []):
            new_css.update(new_rules.get(name, {}))
        old_css.update(_declarations(old))
        new_css.update(_declarations(new))
        assert old_css == new_css

    # Clients discarding head CSS still receive the original small type and
    # alignment.  Color-coded meaning and protected table styling remain inline.
    bare_before, bare_after = _without_sheets(original), _without_sheets(result)
    old_nodes, new_nodes = bare_before.find_all(True), bare_after.find_all(True)
    assert len(old_nodes) == len(new_nodes)
    for old, new in zip(old_nodes, new_nodes):
        assert old.name == new.name
        old_css, new_css = _declarations(old), _declarations(new)
        for key in CRITICAL:
            assert old_css.get(key) == new_css.get(key)
        if old_css.get("color") in SEMANTIC_COLORS:
            assert old_css["color"] == new_css.get("color")
        if old.find_parent("table", attrs={"data-mobile-layout": "table"}) or (
                old.name == "table" and old.get("data-mobile-layout") == "table"):
            assert old_css == new_css


def test_contrast_pair_and_source_attribute_text_are_preserved():
    markup = ("<html><head></head><body>"
              + '<span title=\'example style="color:#fff;"\' '
                'style="color:#fff;background:#0f172a;padding:5px;">字</span>' * 8
              + '<a href="https://example.test/?a=1&amp;b=2">來源</a>'
              + "</body></html>")
    result = email_style_dictionary.compact(markup)
    assert _without_sheets(result).get_text() == _without_sheets(markup).get_text()
    assert all("color:#fff" in tag.get("style", "") and
               "background:#0f172a" in tag.get("style", "")
               for tag in _without_sheets(result).find_all("span"))
    assert _without_sheets(result).find("a")["href"] == (
        _without_sheets(markup).find("a")["href"])


def test_pass_is_idempotent_and_unhelpful_inputs_are_untouched():
    markup = ("<html><head></head><body>"
              + '<div style="color:#94a3b8;">文字</div>' * 30
              + "</body></html>")
    result = email_style_dictionary.compact(markup)
    assert len(result.encode()) < len(markup.encode())
    assert email_style_dictionary.compact(result) == result
    assert email_style_dictionary.compact("<div style='color:#94a3b8;'>文字</div>") == (
        "<div style='color:#94a3b8;'>文字</div>")


def test_style_pass_failure_is_reported_without_dropping_the_mail(monkeypatch, capsys):
    monkeypatch.setattr(email_content_audit.email_mobile, "enhance", lambda html: html)

    def fail(_html):
        raise RuntimeError("offline failure")

    monkeypatch.setattr(email_style_dictionary, "compact", fail)
    markup = "<html><head></head><body><p>完整內容</p></body></html>"
    manifest = {}
    result = email_content_audit.finalize("", markup, manifest)
    assert "完整內容" in result
    assert manifest["llm"]["email_html"]["style_error"] == "RuntimeError"
    assert "style compaction failed" in capsys.readouterr().err
