"""Podcast producer tests: bounded RSS and multi-episode backlog handling."""
from types import SimpleNamespace

import podcast_digest as pd


def _entry(guid: str, minutes: int = 30) -> dict:
    return {
        "id": guid,
        "title": f"Episode {guid}",
        "published": "Sun, 14 Jun 2026 00:00:00 GMT",
        "itunes_duration": str(minutes * 60),
        "enclosures": [{"href": f"https://example.com/{guid}.mp3"}],
    }


def test_parse_feed_url_uses_bounded_request(monkeypatch):
    captured = {}

    class Response:
        content = b"<rss/>"

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(pd.requests, "get", fake_get)
    monkeypatch.setattr(pd.feedparser, "parse", lambda content: SimpleNamespace(entries=[]))
    pd.parse_feed_url("https://example.com/feed", timeout=7)
    assert captured["timeout"] == 7
    assert captured["url"] == "https://example.com/feed"


def test_find_new_episodes_returns_multiple_backlog_items(monkeypatch):
    entries = [_entry("new-1"), _entry("new-2"), _entry("old")]
    state = {"show": {"name": "Show", "episodes": [{"guid": "old"}]}}
    cfg = {"key": "show", "name": "Show", "search": "Show", "country": "TW"}
    monkeypatch.setattr(pd, "resolve_feed_url", lambda *args, **kwargs: "https://feed")
    monkeypatch.setattr(
        pd, "parse_feed_url", lambda url: SimpleNamespace(entries=entries))
    monkeypatch.setattr(pd, "_entry_age_hours", lambda entry: 1.0)
    found = pd.find_new_episodes(cfg, state, limit=5)
    assert [item[0]["id"] for item in found] == ["new-1", "new-2"]


def test_accuracy_settings_routing():
    """accuracy=high → medium + beam5 + v4-pro;一般 → small + beam1 + flash。"""
    high = pd._accuracy_settings({"accuracy": "high"})
    assert high["whisper"] == pd.WHISPER_MODEL_HIGH and high["beam"] == 5
    assert high["summary_model"] == pd.DEEPSEEK_MODEL_HIGH
    assert high["rt_factor"] == pd.TRANSCRIBE_REALTIME_FACTOR_HIGH
    base = pd._accuracy_settings({})
    assert base["whisper"] == pd.WHISPER_MODEL and base["beam"] == 1
    assert base["summary_model"] == pd.DEEPSEEK_MODEL


def test_core_podcasts_marked_high_accuracy():
    """台系核心節目(股癌/財報狗/財經皓角/M觀點)應標 accuracy=high。"""
    by_key = {p["key"]: p for p in pd.PODCASTS}
    for key in ("gooaye", "statementdog", "haojiao", "mviewpoint"):
        assert by_key[key].get("accuracy") == "high", key
    # 已剔除的節目不應再存在
    for gone in ("ft-briefing", "unhedged", "animalspirits", "investlikebest"):
        assert gone not in by_key
    # 新增的節目應存在
    assert "sharptech" in by_key and "allin" in by_key


def test_main_processes_multiple_episodes_from_same_show(monkeypatch):
    cfg = {"key": "show", "name": "Show", "search": "Show", "priority": 1}
    processed = []
    monkeypatch.setattr(pd, "DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(pd, "PODCASTS", [cfg])
    monkeypatch.setattr(pd, "load_state", lambda: {})
    monkeypatch.setattr(
        pd, "find_new_episodes",
        lambda *args, **kwargs: [
            (_entry("one"), "https://example.com/one.mp3", 20),
            (_entry("two"), "https://example.com/two.mp3", 25),
        ],
    )
    monkeypatch.setattr(
        pd, "process_episode",
        lambda cfg, state, entry, audio_url: processed.append(entry["id"]) or True,
    )
    monkeypatch.setattr(pd, "save_state", lambda state: None)
    assert pd.main() == 0
    assert processed == ["one", "two"]


def test_http_get_retries_on_5xx(monkeypatch):
    """podcast_digest._http_get:5xx 重試、下次成功即回。"""
    calls = {"n": 0}

    class _R:
        def __init__(self, code):
            self.status_code = code

    def fake(url, **kw):
        calls["n"] += 1
        return _R(503 if calls["n"] == 1 else 200)

    monkeypatch.setattr(pd.time, "sleep", lambda *_: None)
    monkeypatch.setattr(pd.requests, "get", fake)
    assert pd._http_get("https://x", retries=2).status_code == 200 and calls["n"] == 2


def test_state_write_failure_is_not_reported_as_success(monkeypatch):
    """2026-08-24 外審 P2:落盤失敗先前與轉錄共用同一個 except,被歸類成
    「處理失敗(不影響其他節目)」——而 `updated` 早已是 True,結尾照樣印
    「已寫入 <檔>」並回 0。轉錄是這條流程最貴的一步,靜默丟掉比失敗更糟。"""
    cfg = {"key": "show", "name": "Show", "search": "Show", "priority": 1}
    monkeypatch.setattr(pd, "DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(pd, "PODCASTS", [cfg])
    monkeypatch.setattr(pd, "load_state", lambda: {})
    monkeypatch.setattr(
        pd, "find_new_episodes",
        lambda *a, **k: [(_entry("one"), "https://example.com/one.mp3", 20)])
    monkeypatch.setattr(
        pd, "process_episode", lambda cfg, state, entry, audio_url: True)

    def _boom(state):
        raise OSError("disk full")

    monkeypatch.setattr(pd, "save_state", _boom)
    assert pd.main() == 1, "轉錄了但沒寫進檔案,不可以回報成功"


def test_a_later_successful_write_clears_the_earlier_failure(monkeypatch):
    """逐集落盤共用同一個 `state` dict:後面那次成功會把前面那集一併寫進去,
    所以不該因為第一次失敗就永遠算失敗(那是相反方向的假警報)。"""
    cfg = {"key": "show", "name": "Show", "search": "Show", "priority": 1}
    monkeypatch.setattr(pd, "DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(pd, "PODCASTS", [cfg])
    monkeypatch.setattr(pd, "load_state", lambda: {})
    monkeypatch.setattr(
        pd, "find_new_episodes",
        lambda *a, **k: [(_entry("one"), "https://example.com/one.mp3", 20),
                         (_entry("two"), "https://example.com/two.mp3", 20)])
    monkeypatch.setattr(
        pd, "process_episode", lambda cfg, state, entry, audio_url: True)
    calls = []

    def _flaky(state):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("transient")

    monkeypatch.setattr(pd, "save_state", _flaky)
    assert pd.main() == 0 and len(calls) == 2, calls
