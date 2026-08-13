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
    """主體順序不是語意 —— 「美國、伊朗」與「伊朗、美國」要是同一個身分。

    2026-08-08:身分主鍵改成**動作**(見 `event_identity`),而這一條驗的是
    認不出動作時的降級路徑 —— 所以兩則標題都刻意不帶動作關鍵詞。

    2026-08-08(identity v7):**標題要一樣。** 上一版兩則用了完全不同的
    佔位標題(「三國代表昨日會面」vs「同一件事,主體換順序」)——
    而 v7 之後標題的辨識詞會分出「同鍵下的另一樁」,於是這條測試
    同時動了兩個變數,量到的不再是主體順序。
    **反例要只靠被測那條規則分勝負。**
    """
    title = "三國代表昨日於第三地會面"
    _a, state = _timeline(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "美國、伊朗、阿曼",
         "title": title}], days=1)
    _a2, state2 = _timeline(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "伊朗、阿曼、美國",
         "title": title}], days=1)
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
    assert state["geopolitical:伊朗:2026-08"]["days"] == 1


def test_producer_timeline_carries_the_summary_only_recipient(
        tmp_path, monkeypatch):
    """第三十一輪外審 r1(P1):`match_days` 的記錄側讀 `object` /
    `latest_summary`,而 producer 先前兩個都沒存 —— 手捏 record 的測試
    全綠,真實 state 形狀下受詞只在 summary 的事件隔天回 0 天。
    **timeline 一律由 `update_event_timeline()` 產生**(本檔鐵則)。"""
    import event_identity as eid
    _active, state = _timeline(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "美國",
         "entities": ["美國", "台灣"],
         "title": "美國軍售最新動向",
         "summary": "五角大廈證實新一批軍售 package for Taiwan,交付時程未定"}],
        days=1)
    rec = next(iter(state.values()))
    assert rec.get("object") == "台灣", rec
    assert "Taiwan" in str(rec.get("latest_summary") or ""), rec
    # 隔天:標題明寫對台 —— 要接上第 1 天那條(state 形狀是生產的)
    got = eid.match_days(list(state.values()), ["美國", "台灣"],
                         "美國宣布對台軍售")
    assert got == 1, (got, rec)


def test_the_packet_cluster_carries_the_timeline_lineage(tmp_path,
                                                         monkeypatch):
    """端到端:producer 產的 timeline(帶 key)進 packet 後,同一樁事的
    cluster 要帶 `lineage_id` —— recap 明天就是拿它直接接。"""
    import evidence_packet as ep
    _active, state = _timeline(tmp_path, monkeypatch, [
        {"event_type": "geopolitical", "entity": "伊朗",
         "entities": ["伊朗", "美國"],
         "title": "美國宣布對伊朗新一輪經濟制裁措施", "summary": ""}],
        days=2)
    timeline = [dict(v, key=k) for k, v in state.items()]
    pk = ep.build({"EVENT_TIMELINE": timeline},
                  {}, {}, [{"source_item_id": "n1",
                            # 標題要與 day-1 有足夠 incident 重疊(P1-1 之後同語言 NO_MATCH
                            # 會否決 —— 這正是要的行為)
                            "title": "美國對伊朗新一輪制裁 波斯灣航運受阻",
                            "entities": ["美國", "伊朗"], "source": "X",
                            "source_name": "X"}],
                  [], {}, as_of="x", target_session_date="2026-08-07",
                  sanitize=str)
    cl = pk["news_clusters"]["clusters"]
    assert cl and cl[0].get("lineage_id"), cl
    assert cl[0]["lineage_id"] in state, (cl[0]["lineage_id"], list(state))
