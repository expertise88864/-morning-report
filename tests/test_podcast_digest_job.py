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
