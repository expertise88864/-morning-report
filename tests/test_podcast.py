"""Podcast 摘要整合測試:讀取時效、觀點對照、渲染。"""
import datetime as dt
import json

import morning_report as mr


def _digest_state(processed_at: str) -> dict:
    return {
        "gooaye": {
            "name": "股癌",
            "episodes": [{
                "guid": "ep669",
                "title": "EP669 | 🎈",
                "published": "Wed, 10 Jun 2026 07:37:19 GMT",
                "processed_at": processed_at,
                "digest": {
                    "summary_points": ["看好 AI 伺服器下半年拉貨", "提醒油價回落利多通膨"],
                    "tickers": [
                        {"name": "雙鴻", "code": "3324", "market": "TW",
                         "direction": "bullish", "reason": "散熱需求強勁"},
                        {"name": "特斯拉", "code": "TSLA", "market": "US",
                         "direction": "neutral", "reason": "估值偏高"},
                    ],
                    "market_view": "大盤短線震盪偏多",
                    "action_view": "拉回找買點,不追高",
                    "notable_quote": "市場永遠是對的",
                },
            }],
        }
    }


def _now_iso(hours_ago: float = 1.0) -> str:
    ts = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_load_podcast_digest_respects_age_window(tmp_path, monkeypatch):
    path = tmp_path / "podcast_digest.json"
    monkeypatch.setattr(mr, "PODCAST_DIGEST_FILE", path)
    # 1 小時前處理 → 載入
    path.write_text(json.dumps(_digest_state(_now_iso(1))), encoding="utf-8")
    eps = mr.load_podcast_digest()
    assert len(eps) == 1 and eps[0]["show"] == "股癌"
    # 超過時效視窗(96h)→ 過期不載入
    path.write_text(json.dumps(_digest_state(_now_iso(120))), encoding="utf-8")
    assert mr.load_podcast_digest() == []
    # 壞 JSON → 空,不炸
    path.write_text("{not json", encoding="utf-8")
    assert mr.load_podcast_digest() == []


def test_podcast_episode_shown_only_once(tmp_path, monkeypatch):
    """每集只出現一次:寄信後 mark shown_at,之後 load 不再回傳。"""
    path = tmp_path / "podcast_digest.json"
    monkeypatch.setattr(mr, "PODCAST_DIGEST_FILE", path)
    path.write_text(json.dumps(_digest_state(_now_iso(1))), encoding="utf-8")
    eps = mr.load_podcast_digest()
    assert len(eps) == 1
    # 標記已顯示
    mr.mark_podcast_episodes_shown(eps)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["gooaye"]["episodes"][0].get("shown_at")
    # 再 load → 空(不重複出現)
    assert mr.load_podcast_digest() == []
    # 重複標記冪等(shown_at 不被覆寫)
    first_ts = saved["gooaye"]["episodes"][0]["shown_at"]
    mr.mark_podcast_episodes_shown(eps)
    saved2 = json.loads(path.read_text(encoding="utf-8"))
    assert saved2["gooaye"]["episodes"][0]["shown_at"] == first_ts


def test_podcast_ticker_crosscheck_rules():
    snapshot = [{"code": "3324", "foreign_30d_lot": 5200, "pct_5d": 3.1}]
    bull = {"name": "雙鴻", "code": "3324", "market": "TW", "direction": "bullish"}
    bear = {"name": "雙鴻", "code": "3324", "market": "TW", "direction": "bearish"}
    assert "一致" in mr._podcast_ticker_crosscheck(bull, snapshot)
    assert "分歧" in mr._podcast_ticker_crosscheck(bear, snapshot)
    # 不在追蹤池
    out = mr._podcast_ticker_crosscheck(
        {"name": "X", "code": "9999", "market": "TW", "direction": "bullish"}, snapshot)
    assert "不在本報追蹤池" in out
    # 美股/無代號 → 不對照
    assert mr._podcast_ticker_crosscheck(
        {"name": "TSLA", "code": "TSLA", "market": "US", "direction": "bullish"},
        snapshot) == ""


def test_render_podcast_html(tmp_path, monkeypatch):
    import html as htmllib
    path = tmp_path / "podcast_digest.json"
    monkeypatch.setattr(mr, "PODCAST_DIGEST_FILE", path)
    path.write_text(json.dumps(_digest_state(_now_iso(1))), encoding="utf-8")
    eps = mr.load_podcast_digest()
    snapshot = [{"code": "3324", "foreign_30d_lot": 5200, "pct_5d": 3.1}]
    html_out = mr._render_podcast_html(eps, snapshot, htmllib)
    assert "Podcast 重點" in html_out
    assert "股癌" in html_out and "雙鴻" in html_out
    assert "看多" in html_out               # direction 中文化
    assert "與法人方向一致" in html_out      # 對照有出現
    assert "非本報建議" in html_out          # 免責
    # 無集 → 空字串(信件不出現該區塊)
    assert mr._render_podcast_html([], snapshot, htmllib) == ""
