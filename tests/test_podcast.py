"""Podcast 摘要整合測試:讀取時效、觀點對照、渲染。"""
import datetime as dt
import json

import morning_report as mr
import pytest


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


def test_load_podcast_digest_skips_shown_prefix(tmp_path, monkeypatch):
    path = tmp_path / "podcast_digest.json"
    monkeypatch.setattr(mr, "PODCAST_DIGEST_FILE", path)
    state = _digest_state(_now_iso(1))
    episodes = state["gooaye"]["episodes"]
    episodes[0]["shown_at"] = _now_iso(0.5)
    episodes.extend([
        {
            **episodes[0],
            "guid": "ep670",
            "title": "EP670",
            "shown_at": _now_iso(0.4),
        },
        {
            **episodes[0],
            "guid": "ep671",
            "title": "EP671",
            "shown_at": None,
        },
    ])
    path.write_text(json.dumps(state), encoding="utf-8")

    loaded = mr.load_podcast_digest()

    assert [episode["guid"] for episode in loaded] == ["ep671"]


def test_deliver_report_does_not_commit_state_when_email_fails(monkeypatch):
    committed = []
    monkeypatch.setattr(
        mr, "send_email",
        lambda *args: (_ for _ in ()).throw(RuntimeError("smtp failed")))
    monkeypatch.setattr(
        mr, "persist_delivered_report_state",
        lambda *args, **kwargs: committed.append((args, kwargs)))
    with pytest.raises(RuntimeError, match="smtp failed"):
        mr.deliver_report("<html>", "subject", {"date": "2026-06-14"}, [{"guid": "x"}])
    assert committed == []


def test_deliver_report_commits_state_after_email(monkeypatch):
    events = []
    monkeypatch.setattr(mr, "send_email", lambda *args: events.append("sent"))
    monkeypatch.setattr(
        mr, "persist_delivered_report_state",
        lambda *args, **kwargs: events.append("persisted"))
    mr.deliver_report("<html>", "subject", {"date": "2026-06-14"}, [{"guid": "x"}])
    assert events == ["sent", "persisted"]


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


def test_dedup_podcast_skips_near_duplicate_episode():
    """同場聯名特輯被兩個 feed 各收一次 → 重疊高的整集略過。"""
    ep_a = {"show": "美股投資學", "guid": "a",
            "digest": {"summary_points": ["P1", "P2", "P3", "P4", "P5"]}}
    ep_b = {"show": "財經M平方", "guid": "b",   # 4/5 重疊 → 視為重貼,略過
            "digest": {"summary_points": ["P1", "P2", "P3", "P4", "X6"]}}
    out = mr._dedup_podcast_episodes([ep_a, ep_b])
    assert [e["guid"] for e in out] == ["a"]


def test_dedup_podcast_drops_repeated_points_keeps_episode():
    """重疊不足以整集略過時,移除個別重複重點、保留該集。"""
    ep_a = {"show": "股癌", "guid": "a",
            "digest": {"summary_points": ["P1", "P2", "P3", "P4", "P5"]}}
    ep_c = {"show": "財報狗", "guid": "c",   # 僅 P1 重複(1/5)→ 保留,但 P1 被移除
            "digest": {"summary_points": ["P1", "Y2", "Y3", "Y4", "Y5"]}}
    out = mr._dedup_podcast_episodes([ep_a, ep_c])
    assert len(out) == 2
    assert out[1]["digest"]["summary_points"] == ["Y2", "Y3", "Y4", "Y5"]


def test_dedup_podcast_keeps_original_when_too_few_unique():
    """2 點全重複的短集(不觸發整集略過門檻)→ 保留原樣,避免變空集。"""
    ep_a = {"show": "股癌", "guid": "a",
            "digest": {"summary_points": ["P1", "P2", "P3"]}}
    ep_d = {"show": "財報狗", "guid": "d", "digest": {"summary_points": ["P1", "P2"]}}
    out = mr._dedup_podcast_episodes([ep_a, ep_d])
    assert len(out) == 2
    assert out[1]["digest"]["summary_points"] == ["P1", "P2"]   # 原樣保留


def test_render_podcast_international_point_cap():
    """國際節目重點壓到 6 條;台系節目全展開。"""
    import html as htmllib
    intl = [{"show": "Odd Lots", "title": "x",
             "digest": {"summary_points": [f"pt{i}" for i in range(10)]}}]
    h = mr._render_podcast_html(intl, [], htmllib)
    assert "pt5" in h and "pt6" not in h          # 國際 → 只到第 6 條
    tw = [{"show": "股癌", "title": "x",
           "digest": {"summary_points": [f"pt{i}" for i in range(12)]}}]
    h2 = mr._render_podcast_html(tw, [], htmllib)
    assert "pt11" in h2                            # 台系 → 全展開(≤15)


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
