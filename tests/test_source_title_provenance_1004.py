"""A source-tag shape is not evidence that a line came from Python's news list."""

import copy
import hashlib

import analysis_origin as ao
import morning_report as report
import pytest
from reader_market_language_guard import neutralize_report


CBC = "央行報告：2018至2025年中國18.8%，美國躍居最大投資地。"
MU = "美光（MU）財報對台股週三（9/30）盤中才有實質影響。"
EVENTS = [{"title": "MU 財報", "date_zone_uncertain": True}]


@pytest.mark.parametrize("tag,prose,expected", [
    ("央行", CBC, "央行明確指出2023、2024年"),
    ("CNBC", MU, "財報公布時刻未核實，不能推定"),
])
@pytest.mark.parametrize("lead", ["", "本報解讀："])
def test_tagged_model_prose_cannot_skip_actual_delivery_guards(tag, prose, expected, lead):
    original = f"- [{tag}] {lead}{prose}\n"
    manifest = {"llm": {"analysis_origin": ao.LEGACY_AFTER_LUNA_FAILURE}}
    corrected = neutralize_report(original, manifest, event_calendar=EVENTS)
    assert corrected != original
    assert expected in corrected


def _fallback(monkeypatch, titles):
    manifest = {}
    monkeypatch.setattr(report, "_RUN_MANIFEST", manifest)
    text = report._fallback_analysis_text(
        [{"source": "fixture-source", "title": title} for title in titles],
        RuntimeError("offline fixture"))
    return text, manifest


def test_actual_python_fallback_preserves_titles_but_not_added_tagged_prose(monkeypatch):
    text, manifest = _fallback(monkeypatch, [CBC, MU])
    added = f"- [央行] 本報解讀：{CBC}\n- [CNBC] 本報解讀：{MU}\n"
    corrected = neutralize_report(text + added, manifest, event_calendar=EVENTS)
    for title in (CBC, MU):
        assert f"- [fixture-source] {title}\n" in corrected
    assert added not in corrected
    assert "央行明確指出2023、2024年" in corrected
    assert "財報公布時刻未核實，不能推定" in corrected
    assert manifest["llm"]["uncertain_earnings_timing_claims_neutralized"] == 1
    assert manifest["llm"]["policy_scope_guard_rules"] == ["cbc_2018_2025_vs_2023_2024_scope"]


def test_registration_records_only_bounded_line_digests_not_titles_or_error(monkeypatch):
    _, manifest = _fallback(monkeypatch, [CBC, MU] * 11)
    provenance = manifest["llm"]["emergency_source_title_provenance"]
    assert provenance["basis"] == "emergency-source-title-lines.v1"
    assert len(provenance["line_sha256"]) == 20
    assert all(len(value) == 64 for value in provenance["line_sha256"])
    assert CBC not in str(provenance) and MU not in str(provenance)
    assert "offline fixture" not in str(provenance)


@pytest.mark.parametrize("origin", [ao.LEGACY_PRIMARY, ao.LUNA_SPECIALIZED, ao.UNKNOWN,
                                     ao.LEGACY_AFTER_LUNA_FAILURE, " emergency_fallback ", None])
def test_stale_digest_cannot_protect_a_non_emergency_origin(monkeypatch, origin):
    _, manifest = _fallback(monkeypatch, [CBC])
    manifest["llm"]["analysis_origin"] = origin
    title = f"- [fixture-source] {CBC}\n"
    assert "央行明確指出2023、2024年" in neutralize_report(title, manifest)


@pytest.mark.parametrize("revision", [
    None, {}, {"basis": "wrong", "line_sha256": []},
    {"basis": "emergency-source-title-lines.v1", "line_sha256": "a" * 64},
    {"basis": "emergency-source-title-lines.v1", "line_sha256": [0]},
    {"basis": "emergency-source-title-lines.v1", "line_sha256": ["A" * 64]},
    {"basis": "emergency-source-title-lines.v1", "line_sha256": ["a" * 64] * 21},
])
def test_malformed_or_missing_provenance_never_exempts_tagged_prose(monkeypatch, revision):
    _, manifest = _fallback(monkeypatch, [CBC])
    manifest["llm"]["emergency_source_title_provenance"] = revision
    assert "央行明確指出2023、2024年" in neutralize_report(
        f"- [fixture-source] {CBC}\n", manifest)


def test_exact_line_binding_rejects_mutation_and_preserves_crlf(monkeypatch):
    from source_title_provenance import is_registered_emergency_title

    _, manifest = _fallback(monkeypatch, [CBC])
    original = f"- [fixture-source] {CBC}"
    assert is_registered_emergency_title(original + "\r\n", manifest)
    assert not is_registered_emergency_title(" " + original, manifest)
    assert not is_registered_emergency_title(original + " 本報解讀", manifest)
    assert not is_registered_emergency_title(original.replace("fixture-source", "央行"), manifest)
    original_manifest = copy.deepcopy(manifest)
    assert not is_registered_emergency_title(original, {"llm": []})
    assert not is_registered_emergency_title(original, None)
    assert manifest == original_manifest


def test_new_emergency_list_replaces_old_registration(monkeypatch):
    from source_title_provenance import is_registered_emergency_title

    _, manifest = _fallback(monkeypatch, [CBC])
    report._fallback_analysis_text([], RuntimeError("second offline fixture"))
    assert not is_registered_emergency_title(f"- [fixture-source] {CBC}", manifest)
    assert manifest["llm"]["emergency_source_title_provenance"]["line_sha256"] == []


def test_registration_cannot_register_self_authored_body_or_wrong_origin():
    from source_title_provenance import register_emergency_titles

    manifest = {"llm": {"analysis_origin": ao.EMERGENCY_FALLBACK}}
    line = f"- [fixture-source] {CBC}"
    register_emergency_titles("not a source-list line\n" + line, manifest)
    assert manifest["llm"]["emergency_source_title_provenance"]["line_sha256"] == [
        hashlib.sha256(line.encode("utf-8")).hexdigest()]
    other = {"llm": {"analysis_origin": ao.LEGACY_PRIMARY}}
    register_emergency_titles(line, other)
    assert "emergency_source_title_provenance" not in other["llm"]


def test_registration_metadata_survives_actual_manifest_build_without_raw_titles(monkeypatch):
    from run_manifest import ManifestRecorder

    _, manifest = _fallback(monkeypatch, [CBC])
    persisted = ManifestRecorder(data=manifest).build(
        date="2026-10-04", report_kind="weekend_digest",
        budget_seconds=0, news_workers=0, degraded_steps=[])
    assert persisted["llm"]["emergency_source_title_provenance"] == (
        manifest["llm"]["emergency_source_title_provenance"])
    assert CBC not in str(persisted["llm"]["emergency_source_title_provenance"])


def test_non_source_shape_or_private_object_is_never_coerced_into_provenance():
    from source_title_provenance import is_registered_emergency_title

    class DoNotSerialize:
        def __str__(self):
            raise AssertionError("must not coerce unrecognized values")

    manifest = {"llm": {"analysis_origin": ao.EMERGENCY_FALLBACK,
                "emergency_source_title_provenance": {
                    "basis": "emergency-source-title-lines.v1",
                    "line_sha256": [hashlib.sha256(CBC.encode("utf-8")).hexdigest()]}}}
    assert not is_registered_emergency_title(CBC, manifest)
    assert not is_registered_emergency_title(DoNotSerialize(), manifest)
    manifest["llm"]["emergency_source_title_provenance"]["line_sha256"] = [DoNotSerialize()]
    assert not is_registered_emergency_title(f"- [fixture-source] {CBC}", manifest)


def test_explicit_quoted_and_rendered_source_titles_still_remain_verbatim():
    for title in (f"> {CBC}\n", f"來源標題：{CBC}\n",
                  f"**央行報告**｜[{CBC}](https://example.test/news)\n"):
        assert neutralize_report(title, {}, event_calendar=EVENTS) == title
