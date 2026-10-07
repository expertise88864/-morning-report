"""Current-only fallback must not claim an unchanged historical stance."""
import pytest

import morning_report as mr
from conclusion_guard import fallback


@pytest.mark.parametrize("authority", ("偏多", "偏空", "中性"))
def test_current_only_fallback_does_not_invent_continuity(authority):
    text = fallback(authority)
    assert text.startswith(f"今日立場為{authority}。")
    assert "維持" not in text
    assert "價位估算見下方預測表" in text
    reminder = {"偏多": "買進訊號", "偏空": "賣出訊號", "中性": "等待量能"}
    assert reminder[authority] in text


@pytest.mark.parametrize("previous", (None, "偏多", "偏空", "中性"))
@pytest.mark.parametrize("authority,total", (("偏多", 6), ("偏空", -6), ("中性", 0)))
def test_rendered_conflict_uses_current_stance_without_historical_claim(
    monkeypatch, previous, authority, total,
):
    monkeypatch.setitem(mr._RUN_MANIFEST, "llm", {})
    quotes = {
        "STANCE_PY": {"total": total, "label": authority, "components": {}},
        "HISTORY": [] if previous is None else [
            {"date": "2026-10-05", "stance_label_py": previous}
        ],
        "MACRO": {}, "TAIEX_PRED": {}, "NIGHT_TXF": {},
    }
    rejected = "偏空" if authority == "偏多" else "偏多"
    analysis = (
        f"## 十二、我的明確立場\n> **立場：{rejected}**\n"
        f"## 十三、一句話總結\n{rejected}，立即加碼虛構商品。"
    )
    html = mr.render_html(quotes, {"error": "offline"}, {"error": "offline"},
                          analysis, "2026-10-06", "離線合成晨報")
    assert f"今日立場為{authority}。" in html
    assert "今日維持" not in html
    assert "立即加碼虛構商品" not in html
    assert "立場變化歸因" not in html


def test_unknown_authority_stays_unknown_without_continuity():
    text = fallback("資料不足")
    assert text.startswith("目前資料不足，暫不提供方向性結論")
    assert "維持" not in text


@pytest.mark.parametrize("authority", ("偏多", "偏空", "中性"))
def test_short_stance_observation_survives_trade_neutralization(authority):
    from reader_market_language_guard import neutralize_summary

    manifest = {}
    corrected = neutralize_summary(f"台股{authority}，建議逢低加碼", manifest)
    assert corrected == f"台股{authority}；開盤預測僅供行情觀察，不是買賣訊號。"
    assert manifest["llm"]["conclusion_trade_claims_neutralized"] == 1


@pytest.mark.parametrize("authority,total", (("偏多", 6), ("偏空", -6), ("中性", 0)))
@pytest.mark.parametrize("minimal", (False, True))
@pytest.mark.parametrize("writer_origin", (None, "legacy_primary", "legacy_after_luna_failure", "luna_specialized"))
def test_short_stance_observation_survives_actual_writer_and_renderers(
    monkeypatch, authority, total, minimal, writer_origin,
):
    import analysis_origin as ao

    manifest = {}
    monkeypatch.setattr(mr, "_RUN_MANIFEST", manifest)
    quotes = {"STANCE_PY": {"total": total, "label": authority, "components": {}},
              "MACRO": {}, "TAIEX_PRED": {}, "NIGHT_TXF": {}}
    raw = (f"## 我的明確立場\n> **立場：{authority}**\n"
           f"## 一句話總結\n台股{authority}，建議逢低加碼\n")
    analysis = raw
    if writer_origin:
        origin = {"legacy_primary": ao.LEGACY_PRIMARY,
                  "legacy_after_luna_failure": ao.LEGACY_AFTER_LUNA_FAILURE,
                  "luna_specialized": ao.LUNA_SPECIALIZED}[writer_origin]
        recorded = []
        monkeypatch.setattr(mr, "_record_report_writer", recorded.append)

        def already_generated(*_args):
            # Replace paid generation only; run the actual shared writer guard.
            mr._set_analysis_origin(origin)
            return raw

        monkeypatch.setattr(mr, "_call_llm_analysis_impl", already_generated)
        analysis = mr.call_llm_analysis(quotes, {}, {}, [])
        assert recorded == [analysis]
    if minimal:
        result = mr._render_minimal_html(quotes, {}, {}, analysis, "2026-10-07", "離線合成")
    else:
        result = mr.render_html(quotes, {"error": "offline"}, {"error": "offline"},
                                analysis, "2026-10-07", "離線合成")
    assert f"台股{authority}；開盤預測僅供行情觀察，不是買賣訊號。" in result
    assert "建議逢低加碼" not in result
    assert manifest["llm"]["conclusion_trade_claims_neutralized"] == 1


@pytest.mark.parametrize("summary", ("台股，建議加碼", "建議逢低加碼", "觀察，建議買進"))
def test_short_observation_without_a_stance_does_not_invent_one(summary):
    from conclusion_guard import summary_word
    from reader_market_language_guard import neutralize_summary

    corrected = neutralize_summary(summary, {})
    assert corrected == "開盤預測僅供行情觀察，不是買賣訊號。"
    assert summary_word(corrected, "偏多") == ""


@pytest.mark.parametrize("authority,total,opposite", (
    ("偏多", 6, "偏空"), ("偏多", 6, "中性"),
    ("偏空", -6, "偏多"), ("偏空", -6, "中性"),
    ("中性", 0, "偏多"), ("中性", 0, "偏空"),
))
@pytest.mark.parametrize("minimal", (False, True))
@pytest.mark.parametrize("writer_origin", (None, "legacy_primary", "legacy_after_luna_failure", "luna_specialized"))
def test_opposite_short_stance_never_reaches_actual_writer_rendered_conclusion(
    monkeypatch, authority, total, opposite, minimal, writer_origin,
):
    import analysis_origin as ao

    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    monkeypatch.setattr(mr, "_structured_stance", lambda: {})
    quotes = {"STANCE_PY": {"total": total, "label": authority, "components": {}},
              "MACRO": {}, "TAIEX_PRED": {}, "NIGHT_TXF": {}}
    raw = (f"## 我的明確立場\n> **立場：{opposite}**\n"
           f"## 一句話總結\n台股{opposite}，建議逢低加碼\n"
           "## 其他公開資料\n離線來源本文。[必要來源](https://example.org/source)\n")
    analysis = raw
    if writer_origin:
        origin = {"legacy_primary": ao.LEGACY_PRIMARY,
                  "legacy_after_luna_failure": ao.LEGACY_AFTER_LUNA_FAILURE,
                  "luna_specialized": ao.LUNA_SPECIALIZED}[writer_origin]
        recorded = []
        monkeypatch.setattr(mr, "_record_report_writer", recorded.append)

        def already_generated(*_args):
            mr._set_analysis_origin(origin)
            return raw

        monkeypatch.setattr(mr, "_call_llm_analysis_impl", already_generated)
        analysis = mr.call_llm_analysis(quotes, {}, {}, [])
        assert recorded == [analysis]
    render = mr._render_minimal_html if minimal else mr.render_html
    output = render(quotes, {"error": "offline"}, {"error": "offline"},
                    analysis, "2026-10-07", "離線合成")
    assert f"今日立場為{authority}。" in output
    assert f"台股{opposite}" not in output
    assert f"立場：{opposite}" not in output
    assert "建議逢低加碼" not in output
    assert "離線來源本文" in output


@pytest.mark.parametrize("summary", ("本地籌碼偏空，但台股偏多，建議逢低加碼",
                                      "台股偏多，但整體偏空，建議逢低加碼"))
def test_minimal_fallback_checks_all_overall_labels_not_first_or_background(monkeypatch, summary):
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    monkeypatch.setattr(mr, "_structured_stance", lambda: {})
    quotes = {"STANCE_PY": {"total": 6, "label": "偏多", "components": {}}}
    raw = "## 我的明確立場\n> **立場：偏多**\n## 一句話總結\n" + summary
    output = mr._render_minimal_html(quotes, {}, {}, raw, "2026-10-07", "離線合成")
    if "整體偏空" in summary:
        assert "今日立場為偏多。" in output and "整體偏空" not in output
    else:
        assert "台股偏多" in output and "今日立場為偏多。" not in output


def test_minimal_unknown_authority_does_not_adopt_model_stance_after_guard(monkeypatch):
    monkeypatch.setattr(mr, "_RUN_MANIFEST", {})
    quotes = {"STANCE_PY": {"total": None, "label": "資料不足"}}
    raw = "## 我的明確立場\n> **立場：偏空**\n## 一句話總結\n台股偏空，建議逢低加碼"
    output = mr._render_minimal_html(quotes, {}, {}, raw, "2026-10-07", "離線合成")
    assert "立場未知" in output and "台股偏空" not in output
