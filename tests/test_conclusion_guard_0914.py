"""Actual rendered conclusions distinguish signal direction from report stance."""
import pytest

import morning_report as mr
from tests.test_markdown import _full_quotes


def render(monkeypatch, summary, label="中性", authority="中性"):
    monkeypatch.setattr(mr, "_structured_stance", lambda: {"label": label})
    quotes = _full_quotes()
    quotes["STANCE_PY"] = {"total": 0, "label": authority}
    analysis = ("## 八、科技板塊脈動\n保留新聞內容。\n"
                f"## 十二、我的明確立場\n立場：{label}\n"
                f"## 十三、一句話總結\n{summary}")
    return mr.render_html(quotes, {"error": "x"}, {"error": "x"}, analysis,
                          "2026-09-14", "每日報")


@pytest.mark.parametrize("summary", [
    "外部定價偏多，但本地籌碼偏空；整體立場維持中性，等待量能確認。",
    "本報中性，外部訊號偏多、本地籌碼偏空，等待量能確認。",
    "今日以觀望為主，等待量能確認。",
    "外部訊號：偏多、本地籌碼：偏空；整體立場維持中性，等待量能確認。",
    "外部定價（偏多）、本地籌碼（偏空）；整體立場中性，等待量能確認。",
])
def test_agreeing_conclusion_retains_real_context(monkeypatch, summary, capsys):
    html = render(monkeypatch, summary)
    assert "等待量能確認" in html
    assert "[stance-echo]" not in capsys.readouterr().err
    assert "兩者不一致" not in html


@pytest.mark.parametrize("summary,label", [
    ("中性觀望，但本報整體立場偏多，立即全面加碼。", "中性"),
    ("偏多，立即全面加碼。", "中性"),
    ("中性，等待量能確認。", "偏多"),
    ("外部定價偏多，但本報立場偏空，立即全面加碼。", "中性"),
    ("本報立場因外部訊號偏多，立即全面加碼。", "中性"),
    ("偏空風險升高，偏多仍可全面加碼。", "中性"),
    ("外部訊號：偏多，但本報立場：偏空，立即全面加碼。", "中性"),
    ("全面加碼。", ""),
])
def test_real_conflicts_still_remove_directional_advice(monkeypatch, summary, label, capsys):
    html = render(monkeypatch, summary, label)
    assert "[stance-echo]" in capsys.readouterr().err
    assert "全面加碼" not in html
    assert "保留新聞內容" in html
    assert "依系統計分" not in html
    assert "分析師觀點為" not in html


def test_signal_only_without_any_report_stance_is_not_verified(monkeypatch, capsys):
    render(monkeypatch, "外部定價偏多，但本地籌碼偏空。", label="")
    assert "[stance-echo]" in capsys.readouterr().err
