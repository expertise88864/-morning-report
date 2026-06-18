"""podcast_digest 轉錄/儲存順序:同優先級內『最新集先轉』、儲存恆為新→舊(顯示挑到最新)。"""
import datetime as dt

import podcast_digest as pd


def test_stored_pub_dt_parses_and_falls_back():
    newer = pd._stored_pub_dt({"published": "Wed, 17 Jun 2026 08:00:00 +0000"})
    older = pd._stored_pub_dt({"published": "Mon, 15 Jun 2026 08:00:00 +0000"})
    assert newer > older
    # published 缺/壞 → 退回 processed_at
    p = pd._stored_pub_dt({"published": "", "processed_at": "2026-06-16T00:00:00Z"})
    assert (p.year, p.month, p.day) == (2026, 6, 16)
    # 全缺 → 最小值(排序時墊底)
    assert pd._stored_pub_dt({}) == dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def test_storage_sorted_newest_first():
    eps = [
        {"guid": "a", "published": "Mon, 15 Jun 2026 00:00:00 +0000"},
        {"guid": "b", "published": "Wed, 17 Jun 2026 00:00:00 +0000"},
        {"guid": "c", "published": "Tue, 16 Jun 2026 00:00:00 +0000"},
    ]
    eps.sort(key=pd._stored_pub_dt, reverse=True)
    assert [e["guid"] for e in eps] == ["b", "c", "a"]   # 新→舊,晨報才挑得到最新未顯示


def test_process_order_newest_first_within_priority():
    def item(prio, pub, dur):
        return ({"priority": prio}, {"published": pub}, "audio_url", dur)
    pending = [
        item(1, "Mon, 15 Jun 2026 00:00:00 +0000", 50),   # P1 舊
        item(1, "Wed, 17 Jun 2026 00:00:00 +0000", 51),   # P1 最新 → 應最先轉(預算優先)
        item(2, "Thu, 18 Jun 2026 00:00:00 +0000", 6),    # P2:更新但優先級低 → 最後
        item(1, "", 40),                                   # P1 無日期 → P1 內墊底
    ]
    pending.sort(key=pd._process_order_key)
    order = [(cfg["priority"], entry.get("published", "")[:11]) for cfg, entry, _a, _d in pending]
    assert order[0] == (1, "Wed, 17 Jun")   # 旗艦最新集先轉,預算吃緊也不被擠掉
    assert order[1] == (1, "Mon, 15 Jun")   # 同優先級舊集次之
    assert order[2] == (1, "")              # 無日期墊底(仍在 P1 內)
    assert order[3][0] == 2                 # 低優先級最後
