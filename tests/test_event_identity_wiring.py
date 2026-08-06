# -*- coding: utf-8 -*-
"""**延燒事件身分的接線與污染**(第二十四輪 P1-11 回歸)。

兩個缺陷,同一個形狀 —— 都是「程式在、生產不生效/生效成別的東西」:

1. **斷線**:`update_event_timeline()` 回的項目只有 `key`
   (`geopolitical:伊朗`),而 `evidence_packet` 是用 `t.get("entity")`
   建對照表 —— 於是 `continuing_days` 在生產**永遠是 0**,
   「延燒第 N 天」一次都沒有進到分析。
   既有測試沒抓到,因為 fixture 都手寫 `{"entity": ..., "days": ...}`。

2. **污染**:身分只有 `event_type:entity` 一個字串。抽不出主體時落進
   `geopolitical:` 這個共用桶,生產實測累積到 **47 天** —— 那不是任何
   一件事的天數;而多主體字串會讓同一個故事裂成好幾個身分
   (`伊朗` 5 天、`美國` 3 天、`美國、伊朗、阿曼` 1 天)。

**本檔的鐵則:timeline 一律由 `update_event_timeline()` 產生,不得手寫。**
"""
from __future__ import annotations

import datetime as dt
import json

import evidence_packet as ep
import morning_report as mr


def _timeline(tmp_path, monkeypatch, events, days=2, start=None):
    """跑 `days` 天的生產路徑,回 (active, state)。"""
    f = tmp_path / "tl.json"
    monkeypatch.setattr(mr, "EVENT_TIMELINE_FILE", f)
    now = start or dt.datetime(2026, 8, 5, 7, 0, tzinfo=mr.TPE)
    active = []
    for d in range(days):
        active = mr.update_event_timeline(events, now + dt.timedelta(days=d))
    state = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return active, state


def test_active_items_carry_the_field_the_packet_reads(tmp_path, monkeypatch):
    """**接線**:packet 用 `entity` 取值,timeline 就必須給 `entity`。"""
    active, _ = _timeline(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "伊朗", "title": "荷姆茲談判"}])
    assert active, "兩天後應該是進行中的事件"
    assert all(a.get("entity") for a in active), "缺 entity → packet 端讀不到"


def test_continuing_days_actually_reaches_the_packet(tmp_path, monkeypatch):
    """端到端:生產形狀的 timeline 進 packet 後,`continuing_days` 必須 > 0。

    修正前這裡永遠是 0(而且沒有任何測試看得出來)。
    """
    active, _ = _timeline(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "伊朗", "title": "荷姆茲談判"}])
    news = [{"source_item_id": "n1", "title": "伊朗 荷姆茲 談判 傳出 進展",
             "summary": "談判", "source": "R", "source_name": "R",
             "entities": ["伊朗"], "published": "2026-08-05T01:00:00Z"}]
    pk = ep.build({"EVENT_TIMELINE": active}, {}, {}, news, [], {},
                  as_of="x", target_session_date="2026-08-06", sanitize=str)
    days = [c.get("continuing_days") for c in pk["news_clusters"]["clusters"]]
    assert max(days) >= 2, f"延燒天數沒有進到 packet:{days}"


def test_subjectless_events_never_enter_the_timeline(tmp_path, monkeypatch):
    """**空主體不得累積。** 生產那個 `geopolitical:` 桶長到 47 天,
    把不相干的地緣新聞合併成同一件事。"""
    _active, state = _timeline(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "", "title": "抽不出主體"},
        {"event_type": "geopolitical", "entity": "   ", "title": "只有空白"}])
    assert not [k for k in state if k.split(":", 1)[-1].strip() == ""], (
        f"空主體進了 state:{sorted(state)}")


def test_the_same_story_does_not_fragment_by_subject_order(tmp_path, monkeypatch):
    """主體順序不是語意 —— 「美國、伊朗」與「伊朗、美國」要是同一個身分。"""
    _a, state = _timeline(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "美國、伊朗、阿曼",
         "title": "荷姆茲談判"}], days=1)
    _a2, state2 = _timeline(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "伊朗、阿曼、美國",
         "title": "同一件事,主體換順序"}], days=1)
    assert set(state) == set(state2), f"{sorted(state)} != {sorted(state2)}"


def test_subjects_are_normalised_and_recorded():
    ev = {"event_type": "geopolitical", "entity": "美國,伊朗 、阿曼"}
    assert mr._timeline_subjects(ev) == ["伊朗", "美國", "阿曼"]
    assert mr._timeline_subjects({"entity": ""}) == []
    assert mr._timeline_subjects({}) == []


def test_days_accumulate_once_per_day(tmp_path, monkeypatch):
    """同一天跑兩次不得算兩天(重跑/重試不該灌天數)。"""
    f = tmp_path / "tl.json"
    monkeypatch.setattr(mr, "EVENT_TIMELINE_FILE", f)
    now = dt.datetime(2026, 8, 5, 7, 0, tzinfo=mr.TPE)
    ev = [{"event_type": "geopolitical", "entity": "伊朗", "title": "x"}]
    mr.update_event_timeline(ev, now)
    mr.update_event_timeline(ev, now)
    state = json.loads(f.read_text(encoding="utf-8"))
    assert state["geopolitical:伊朗"]["days"] == 1
