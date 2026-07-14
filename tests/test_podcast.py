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


def test_dedup_podcast_skips_crossover_by_title():
    """同場聯名特輯被兩個 feed 各收一次(內容各自改寫、逐字不重疊,但標題含同一特輯名)
    → 用標題重疊係數偵測為重貼,略過後者。"""
    ep_a = {"show": "美股投資學", "guid": "a",
            "title": "【聯名特輯】財經M平方 x 美股投資學-財女珍妮｜通膨修估值，還是 SpaceX 救估值",
            "digest": {"summary_points": [f"美股投資學第{i}點獨特內容" for i in range(8)]}}
    ep_b = {"show": "財經M平方", "guid": "b",
            "title": "財女珍妮聯合特輯｜通膨修估值，還是 SpaceX 救估值",
            "digest": {"summary_points": [f"財經M平方改寫第{i}點" for i in range(8)]}}
    out = mr._dedup_podcast_episodes([ep_a, ep_b])
    assert [e["guid"] for e in out] == ["a"]   # 標題高度重疊 → 後者整集略過


def test_dedup_podcast_keeps_distinct_short_episodes():
    """不同短集(標題不同、<8 點)即使主題相近也不可被整集略過(防誤砍)。"""
    ep_a = {"show": "股癌", "guid": "a", "title": "EP670 台股觀察",
            "digest": {"summary_points": ["看好散熱族群", "記憶體循環向上", "被動元件漲價"]}}
    ep_b = {"show": "財報狗", "guid": "b", "title": "財報狗 AI 伺服器專題",
            "digest": {"summary_points": ["光通訊需求強", "CoWoS 產能吃緊", "金融股估值低"]}}
    out = mr._dedup_podcast_episodes([ep_a, ep_b])
    assert [e["guid"] for e in out] == ["a", "b"]   # 兩集都保留


def test_dedup_podcast_drops_near_identical_point():
    """跨集近乎相同的個別重點(模糊比對)→ 移除重複者、保留該集其餘。"""
    line = "AI 伺服器下半年拉貨需求強勁，散熱族群受惠"
    ep_a = {"show": "股癌", "guid": "a", "title": "EP670",
            "digest": {"summary_points": [line, "被動元件漲價成功", "記憶體循環向上"]}}
    ep_b = {"show": "財報狗", "guid": "b", "title": "完全不同的標題避免整集略過",
            "digest": {"summary_points": [line + "。", "光通訊獨家觀點甲", "重電獨家觀點乙"]}}
    out = mr._dedup_podcast_episodes([ep_a, ep_b])
    assert len(out) == 2
    pts_b = out[1]["digest"]["summary_points"]
    assert line not in pts_b and (line + "。") not in pts_b   # 近重複句被移除
    assert "光通訊獨家觀點甲" in pts_b


def test_dedup_podcast_keeps_same_show_recurring_episodes():
    """同節目連續集(標題格式雷同、內容不同)不可被標題重疊互砍。"""
    ep_a = {"show": "股癌", "guid": "ep670",
            "title": "EP670 | 美股財經週報 market update",
            "digest": {"summary_points": [f"第670集獨特觀點{i}" for i in range(8)]}}
    ep_b = {"show": "股癌", "guid": "ep671",   # 標題重疊極高但同節目 → 必須都保留
            "title": "EP671 | 美股財經週報 market update",
            "digest": {"summary_points": [f"第671集獨特觀點{i}" for i in range(8)]}}
    out = mr._dedup_podcast_episodes([ep_a, ep_b])
    assert [e["guid"] for e in out] == ["ep670", "ep671"]


def test_dedup_podcast_short_episode_does_not_swallow_long_one():
    """先前的短集(3 點)即使內容是後面長集(8 點)的子集,也不可把更豐富的長集砍掉。"""
    shared = ["AI 伺服器拉貨強", "散熱族群受惠", "記憶體循環向上"]
    ep_short = {"show": "財報狗", "guid": "s", "title": "短講",
                "digest": {"summary_points": list(shared)}}
    ep_long = {"show": "股癌", "guid": "l", "title": "深度長集",
               "digest": {"summary_points": shared + [f"長集獨家觀點{i}" for i in range(5)]}}
    out = mr._dedup_podcast_episodes([ep_short, ep_long])
    assert [e["guid"] for e in out] == ["s", "l"]   # 長集保留(其獨家觀點不被吃掉)
    long_pts = out[1]["digest"]["summary_points"]
    assert any("長集獨家觀點" in p for p in long_pts)


def test_dedup_podcast_does_not_mutate_input():
    ep = {"show": "股癌", "guid": "a", "title": "x",
          "digest": {"summary_points": ["A", "B", "C"]}}
    original = ["A", "B", "C"]
    mr._dedup_podcast_episodes([ep])
    assert ep["digest"]["summary_points"] == original   # 原輸入不被改動


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
    assert "非本報建議" not in html_out      # 免責註腳已依使用者要求移除(2026-07-14)
    # 無集 → 空字串(信件不出現該區塊)
    assert mr._render_podcast_html([], snapshot, htmllib) == ""


def test_load_podcast_digest_excludes_removed_shows(tmp_path, monkeypatch):
    """已刪節目(如科技報橘/WSJ What's News)的 state 殘留未顯示集,不得再進信件
    (Codex review:否則清單瘦身在下一封信不生效)。"""
    now = _now_iso(1)
    state = {
        "gooaye": {"name": "股癌", "episodes": [
            {"guid": "g1", "title": "現行節目集", "processed_at": now,
             "summary_points": ["重點"], "published": now}]},
        "techorange": {"name": "科技報橘", "episodes": [
            {"guid": "t1", "title": "已刪節目殘留集", "processed_at": now,
             "summary_points": ["重點"], "published": now}]},
        "wsj-whatsnews": {"name": "WSJ What's News", "episodes": [
            {"guid": "w1", "title": "removed show ep", "processed_at": now,
             "summary_points": ["pt"], "published": now}]},
    }
    path = tmp_path / "podcast_digest.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(mr, "PODCAST_DIGEST_FILE", path)
    eps = mr.load_podcast_digest()
    shows = {e["show"] for e in eps}
    assert "股癌" in shows
    assert "科技報橘" not in shows and "WSJ What's News" not in shows
