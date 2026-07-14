"""G7|tools/query_history.py 歷史索引/查詢測試。

tools/ 依本 repo 慣例不入版控(本機工具)→ CI checkout 無此檔,必須整檔 skip;
本機開發環境則完整驗證 index/query 行為。
"""
import gzip
import importlib.util
import io
import json
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parent.parent / "tools" / "query_history.py"
pytestmark = pytest.mark.skipif(
    not _TOOL.exists(), reason="tools/query_history.py 未入版控(本機工具),CI 無此檔")


def _load_tool():
    spec = importlib.util.spec_from_file_location("query_history", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _setup(tmp_path, monkeypatch):
    """組一個假的 state/:兩天 history + 一天 email 存檔。回傳已改路徑的模組。"""
    qh = _load_tool()
    state = tmp_path / "state"
    emails = state / "emails"
    emails.mkdir(parents=True)
    hist = [
        {"date": "2026-07-10", "weekday": "Fri", "stance_label": "偏多",
         "pred_taiex": 46000, "actual_open_taiex": 46460,
         "weighted_final_2330": 2400, "critical_news": ["台積電法說優於預期"]},
        {"date": "2026-07-11", "weekday": "Sat", "stance_label": "中性",
         "pred_taiex": 46500, "critical_news": []},
        {"date": "bad-date", "stance_label": "壞資料應被跳過"},
    ]
    (state / "history.json").write_text(
        json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    with gzip.open(emails / "2026-07-10.html.gz", "wt", encoding="utf-8") as f:
        f.write("<html><style>x{}</style><body><h1>晨報</h1>"
                "<p>聯發科 發表新晶片&amp;股價大漲</p></body></html>")
    monkeypatch.setattr(qh, "HISTORY_JSON", state / "history.json")
    monkeypatch.setattr(qh, "EMAIL_DIR", emails)
    monkeypatch.setattr(qh, "INDEX_JSONL", state / "history_index.jsonl")
    return qh


def test_index_builds_jsonl_with_email_text(tmp_path, monkeypatch):
    qh = _setup(tmp_path, monkeypatch)
    n = qh.build_index()
    assert n == 2                                    # 壞日期被跳過
    rows = [json.loads(x) for x in
            io.open(qh.INDEX_JSONL, encoding="utf-8") if x.strip()]
    assert [r["date"] for r in rows] == ["2026-07-10", "2026-07-11"]
    r0 = rows[0]
    assert r0["stance_label"] == "偏多" and r0["pred_taiex"] == 46000
    # 信件 HTML → 純文字:去 style/標籤、解實體
    assert "聯發科 發表新晶片&股價大漲" in r0["email_text"]
    assert "<p>" not in r0["email_text"] and "x{}" not in r0["email_text"]
    assert rows[1]["email_chars"] == 0               # 無存檔的那天


def _q(qh, **kw):
    import argparse
    args = argparse.Namespace(keyword=None, code=None, date_from=None,
                              date_to=None, deep=False)
    for k, v in kw.items():
        setattr(args, k, v)
    return qh.run_query(args)


def test_query_filters(tmp_path, monkeypatch, capsys):
    qh = _setup(tmp_path, monkeypatch)
    qh.build_index()
    # 關鍵字搜信件全文
    assert [r["date"] for r in _q(qh, keyword="聯發科")] == ["2026-07-10"]
    # 關鍵字搜 critical_news
    assert [r["date"] for r in _q(qh, keyword="台積電法說")] == ["2026-07-10"]
    # 日期範圍
    assert [r["date"] for r in _q(qh, date_from="2026-07-11")] == ["2026-07-11"]
    # 無條件 → 全列(≤10 天)
    assert len(_q(qh)) == 2
    # 摘要含誤差(46460/46000-1=+1.00%)
    out = capsys.readouterr().out
    assert "+1.00%" in out


def test_query_deep_prints_context(tmp_path, monkeypatch, capsys):
    qh = _setup(tmp_path, monkeypatch)
    qh.build_index()
    _q(qh, keyword="聯發科", deep=True)
    out = capsys.readouterr().out
    assert "發表新晶片" in out                        # 關鍵字前後文
