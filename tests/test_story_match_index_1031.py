"""`_StoryMatchIndex` 預篩必須與逐條全比**完全等價**(2026-10-31)。

背景:帳本長到 3.6 萬條線索後,`_match_open_story` 的逐條全比讓生產
`update_ledger` 跑到約 845s,state 閘的轉載重播測試單獨 366~572s。
索引只跳過「沒有任何候選能通過主旨重疊條件」的線索 —— 這裡用三種資料
逐一對照「帶索引」與「不帶索引(參考實作)」的結果:
  1. 刻意構造的邊界(剝除 token 拼出跨接 bigram、同分取先到者、沒 mark 的新物件);
  2. 固定種子的隨機組合(代號/名稱有無、媒體尾綴、期別型別、軌跡點);
  3. 真實落地的 `state/story_ledger.json`。
"""
import json
import random
from pathlib import Path

import pytest

import story_ledger as sl

STATE = Path(__file__).resolve().parents[1] / "state"


def _both(ev, by_key, index):
    return sl._match_open_story(ev, by_key, index), sl._match_open_story(ev, by_key)


class _FullScan:
    """update_ledger 的對照組:不跳過任何線索(等同沒有索引)。"""

    def __init__(self, by_key):
        pass

    def mark(self, story):
        pass

    def hopeless(self, *args):
        return ()


def test_stripping_an_event_token_that_creates_junction_bigrams_is_still_exact():
    # 事件代號/名稱只會從**沒有自己欄位**的線索剝掉;剝掉中間的「台積電」會拼出
    # 「進封」這個跨接 bigram —— 只靠線索自己的文字建的倒排表只數得到 4 個共同
    # bigram(門檻 5),少了跨接那一個就會被誤判成必然 0 分。
    ev = {"entity": "2330", "entity_name": "台積電", "title": "台積電先進封裝擴產"}
    by_key = {"junction": {"headline": "先進台積電封裝擴產"},
              "noise": {"headline": "鴻海電動車布局"}}
    index = sl._StoryMatchIndex(by_key)
    got, want = _both(ev, by_key, index)
    assert got == want == "junction"


def test_ties_follow_by_key_order_exactly_like_the_full_scan():
    title = "聯電法說AI營收三年拚逾10億美元"
    by_key = {"a": {"entity": "2303", "headline": title},
              "b": {"entity": "2303", "headline": title}}
    index = sl._StoryMatchIndex(by_key)
    ev = {"entity": "2303", "entity_name": "聯電", "title": title + " - 鉅亨網"}
    assert _both(ev, by_key, index) == ("a", "a")
    by_key["a"] = by_key.pop("a")           # 重新插入 → 排到最後
    assert _both(ev, by_key, index) == ("b", "b")


def test_a_story_the_index_has_never_seen_is_never_skipped():
    by_key = {"old": {"headline": "鴻海電動車布局"}}
    index = sl._StoryMatchIndex(by_key)
    by_key["new"] = {"entity": "2330", "headline": "台積電先進封裝擴產計畫"}  # 沒有 mark
    ev = {"entity": "2330", "entity_name": "台積電", "title": "台積電先進封裝擴產計畫 - 工商時報"}
    assert _both(ev, by_key, index) == ("new", "new")


def test_mark_picks_up_a_rewritten_headline():
    story = {"entity": "2317", "entity_name": "鴻海", "headline": "鴻海電動車布局"}
    by_key = {"s": story}
    index = sl._StoryMatchIndex(by_key)
    story["headline"] = "鴻海洽談收購日本車廠 金額三千億"
    index.mark(story)
    ev = {"entity": "2317", "entity_name": "鴻海", "title": "鴻海洽談收購日本車廠 金額三千億 - 經濟日報"}
    assert _both(ev, by_key, index) == ("s", "s")


_WORDS = ["台積電", "先進", "封裝", "擴產", "計畫", "鴻海", "收購", "美國", "關稅", "談判",
          "營收", "創新高", "AI", "伺服器", "NVIDIA", "Apple", "2330", "2317", "聯電",
          "法說", "10億美元", "俄羅斯", "Russia", "制裁", "公告", "115年6月份", "115年7月份",
          "自結合併營收", "CHANNEL PILOT", "ASUS INTERNATIONAL", "提供者", "鉅亨速報"]
_OUTLETS = ["", " - 工商時報", " - 鉅亨網", " | Yahoo奇摩股市", " - 提供者 智通財經 - Investing"]
_ENTITIES = [("2330", "台積電"), ("2317", "鴻海"), ("", ""), ("2330", ""), ("", "台積電"),
             ("Russia", "俄羅斯"), ("俄羅斯", ""), ("2303", "聯電 UMC"), ("A", "")]
_TYPES = ["general", "orders", "revenue_growth", "earnings"]
_PUBLISHED = ["2026-07-10T09:00:00+08:00", "2026-08-10T09:00:00+08:00", ""]


def _title(rng):
    return "".join(rng.sample(_WORDS, rng.randint(2, 6))) + rng.choice(_OUTLETS)


def _random_ledger(rng, n):
    by_key = {}
    for i in range(n):
        ent, name = rng.choice(_ENTITIES)
        etype = rng.choice(_TYPES)
        key = f"k{i}" + (f"|l:{etype}|202607" if rng.random() < 0.2 else "")
        by_key[key] = {
            "key": key, "entity": ent, "entity_name": name, "event_type": etype,
            "headline": _title(rng), "last_published": rng.choice(_PUBLISHED),
            "timeline": [{"t": _title(rng)} for _ in range(rng.randint(0, 2))]}
    return by_key


def _random_event(rng, by_key):
    ent, name = rng.choice(_ENTITIES)
    if rng.random() < 0.5:                  # 從既有線索改寫,確保有命中
        base = rng.choice(list(by_key.values()))
        title = base["headline"].split(" - ")[0] + rng.choice(_OUTLETS)
    else:
        title = _title(rng)
    return {"entity": ent, "entity_name": name, "title": title,
            "event_type": rng.choice(_TYPES), "published": rng.choice(_PUBLISHED)}


def test_randomized_ledgers_match_the_full_scan():
    rng = random.Random(20261031)
    hits = 0
    for _ in range(30):
        by_key = _random_ledger(rng, 60)
        index = sl._StoryMatchIndex(by_key)
        for _ in range(40):
            got, want = _both(_random_event(rng, by_key), by_key, index)
            assert got == want
            hits += bool(want)
    assert hits > 100                       # 對照必須涵蓋大量真正的命中


def test_update_ledger_marks_the_story_it_rewrites(monkeypatch):
    # 第一則續報把 headline 改寫成新措辭;第二則只像新措辭、不像舊 headline。
    # 索引若沒看到改寫,第二則會另開一條線索。
    ledger = [{"key": "e:2317|l:general|x", "entity": "2317", "entity_name": "鴻海",
               "event_type": "general", "headline": "鴻海洽談收購日本車廠", "state": "brewing",
               "first_seen": "2026-08-01", "last_update": "2026-08-01", "updates": 1,
               "timeline": [{"d": "2026-08-01", "t": "鴻海洽談收購日本車廠"}]}]
    events = [{"entity": "2317", "entity_name": "鴻海", "event_type": "general",
               "title": "鴻海洽談收購日本車廠 金額三千億 董事會通過"},
              {"entity": "2317", "entity_name": "鴻海", "event_type": "general",
               "title": "金額三千億 董事會通過 交割完成"}]
    fast = sl.update_ledger(ledger, events, "2026-08-02")
    monkeypatch.setattr(sl, "_StoryMatchIndex", _FullScan)
    slow = sl.update_ledger(ledger, events, "2026-08-02")
    assert len(slow) == 1
    assert json.dumps(fast, sort_keys=True, ensure_ascii=False) == \
        json.dumps(slow, sort_keys=True, ensure_ascii=False)


def test_update_ledger_with_the_index_equals_the_full_scan(monkeypatch):
    # 同一批裡先建立的線索會被後面的轉載命中 —— 這條路徑靠 mark() 讓索引看到新線索。
    rng = random.Random(1031)
    ledger = list(_random_ledger(rng, 80).values())
    events = [_random_event(rng, {s["key"]: s for s in ledger}) for _ in range(60)]
    events += [dict(ev, title=ev["title"].split(" - ")[0] + " - 經濟日報") for ev in events[:30]]
    fast = sl.update_ledger(ledger, events, "2026-08-11")
    monkeypatch.setattr(sl, "_StoryMatchIndex", _FullScan)
    slow = sl.update_ledger(ledger, events, "2026-08-11")
    assert json.dumps(fast, sort_keys=True, ensure_ascii=False) == \
        json.dumps(slow, sort_keys=True, ensure_ascii=False)


def _real_ledger():
    path = STATE / "story_ledger.json"
    if not path.exists():
        pytest.skip("story_ledger.json 尚未產生")
    rows = json.loads(path.read_text(encoding="utf-8"))
    newest = max((str(r.get("first_seen") or "")[:10] for r in rows), default="")
    if not newest:
        pytest.skip("線索帳本是空的或沒有 first_seen")
    return rows, [r for r in rows if str(r.get("first_seen") or "")[:10] == newest]


def _as_event(story):
    return {"title": story.get("headline"), "entity": story.get("entity"),
            "entity_name": story.get("entity_name"), "event_type": story.get("event_type"),
            "published": story.get("last_published")}


def test_real_ledger_replay_matches_the_full_scan():
    """真實帳本:最新一批當事件、對整份帳本比(與 state 契約測試同一種重播)。

    逐條全比每則約 1 秒,只對照所有命中與有/無代號的前兩則未命中;
    整批 576 則的完整對照(0 差異)記錄在 PR 說明。
    """
    rows, cohort = _real_ledger()
    by_key = {str(r.get("key") or ""): r for r in rows}
    index = sl._StoryMatchIndex(by_key)
    hits, misses = [], {True: [], False: []}
    for story in cohort:
        key = str(story.get("key") or "")
        mine = by_key.pop(key)
        hit = sl._match_open_story(_as_event(story), by_key, index)
        (hits if hit else misses[bool(story.get("entity"))]).append((story, hit))
        by_key[key] = mine
    for story, hit in hits[:8] + misses[True][:2] + misses[False][:2]:
        key = str(story.get("key") or "")
        mine = by_key.pop(key)
        assert sl._match_open_story(_as_event(story), by_key) == hit, key
        by_key[key] = mine


def test_real_ledger_update_matches_the_full_scan(monkeypatch):
    """真實文字跑完整 `update_ledger`(含建立、續報、mark 與代號升級)。

    帳本取固定間隔的切片讓對照組跑得動;事件是最新一批再加一份換媒體尾綴的
    轉載,後者會命中同一批剛建立的線索。
    """
    rows, cohort = _real_ledger()
    cohort_keys = {str(r.get("key")) for r in cohort}
    ledger = [r for r in rows if str(r.get("key")) not in cohort_keys][::12]
    events = [dict(_as_event(s), is_incremental=True) for s in cohort[:40]]
    events += [dict(ev, title=str(ev["title"]) + " - 經濟日報") for ev in events[:20]]
    today = str(cohort[0].get("first_seen"))[:10]
    fast = sl.update_ledger(ledger, events, today)
    monkeypatch.setattr(sl, "_StoryMatchIndex", _FullScan)
    slow = sl.update_ledger(ledger, events, today)
    assert json.dumps(fast, sort_keys=True, ensure_ascii=False) == \
        json.dumps(slow, sort_keys=True, ensure_ascii=False)
