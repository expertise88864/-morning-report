"""11-23 實信:Nitto ATP Finals 已出冠軍,「進行中/即將」仍列它。"""
import html as htmllib
import re

import morning_report as mr


def test_tournament_with_a_final_is_not_listed_as_ongoing():
    tennis = {
        "results": [{"tour": "ATP", "tier": "年終賽", "winner": "C. Alcaraz", "loser": "A. Zverev",
                     "event": "Nitto ATP Finals", "event_key": "Nitto ATP Finals", "date": "11/23", "round": "Final"}],
        "tournaments": [{"name": "Nitto ATP Finals", "event_key": "Nitto ATP Finals", "status": "進行中", "tier": "年終賽"},
                        {"name": "Davis Cup Finals", "event_key": "Davis Cup Finals", "status": "11/24 起", "tier": "團體"}],
    }
    page = re.sub(r"<[^>]+>", "", mr._render_sports_html({"tennis": tennis}, htmllib))
    assert "冠軍" in page and "Nitto ATP Finals（進行中）" not in page
    assert "Davis Cup Finals（11/24 起）" in page


def test_ongoing_tournament_without_a_final_stays_listed():
    tennis = {
        "results": [{"tour": "ATP", "tier": "年終賽", "winner": "C. Alcaraz", "loser": "B. Shelton",
                     "event": "Nitto ATP Finals", "event_key": "Nitto ATP Finals", "date": "11/21", "round": "Semifinal"}],
        "tournaments": [{"name": "Nitto ATP Finals", "event_key": "Nitto ATP Finals", "status": "進行中", "tier": "年終賽"}],
    }
    page = re.sub(r"<[^>]+>", "", mr._render_sports_html({"tennis": tennis}, htmllib))
    assert "Nitto ATP Finals（進行中）" in page


def _audit(block, quotes):
    import legacy_evidence as le
    le.reset()
    manifest = {}
    out = le.audit(f"## 八、科技板塊脈動\n{block}\n", quotes, manifest)
    le.reset()
    return out, manifest["llm"]["legacy_evidence"]


QUOTES = {"SECTOR_HEAT": {"sectors": {"半導體業": {"value_yi": 3579.0, "inst_net_yi": -162.3, "median_pct": -9.0}}}}


def test_house_statistics_items_need_no_news_id_when_numbers_match_market_data():
    for label in ("（本報類股統計）", "[本報行情統計]"):
        _, report = _audit(f"**半導體類股**：成交 3,579 億元,法人估算淨賣超 162.3 億元 {label}。[B 級・信心:中-低]", QUOTES)
        assert report["failed"] == 0, label


def test_house_label_does_not_excuse_numbers_missing_from_market_data():
    out, report = _audit("**半導體類股**：成交 4,000 億元 （本報類股統計）。", QUOTES)
    assert report["failed"] == 1 and "4,000 億元（未核實）" in out


def test_items_without_news_id_or_house_label_still_fail():
    _, report = _audit("**半導體類股**：成交 3,579 億元,資金流出。", QUOTES)
    assert report["failed"] == 1


def test_review_pr64_mentioning_the_table_in_prose_is_not_a_source_label():
    _, report = _audit("**半導體類股**：依類股熱度表觀察成交活躍;某公司宣布取得大單。", QUOTES)
    assert report["failed"] == 1
    _, ok = _audit("**半導體類股**：成交 3,579 億元 （類股熱度表）。", QUOTES)
    assert ok["failed"] == 0


def test_review_pr64_grand_slam_stays_listed_while_one_draw_is_unfinished():
    tennis = {
        "results": [{"tour": "WTA", "tier": "大滿貫", "winner": "A. Sabalenka", "loser": "C. Gauff",
                     "event": "Australian Open", "event_key": "Australian Open", "date": "01/31", "round": "Final"},
                    {"tour": "ATP", "tier": "大滿貫", "winner": "J. Sinner", "loser": "A. Zverev",
                     "event": "Australian Open", "event_key": "Australian Open", "date": "01/30", "round": "Semifinal"}],
        "tournaments": [{"name": "Australian Open", "event_key": "Australian Open", "status": "進行中", "tier": "大滿貫"}],
    }
    page = re.sub(r"<[^>]+>", "", mr._render_sports_html({"tennis": tennis}, htmllib))
    assert "冠軍" in page and "（進行中）" in page


def test_review_pr64_round2_open_draw_recorded_before_results_are_cut():
    """男單準決賽被三場其他決賽擠出賽果,截斷前記下的 open_events 仍讓賽事列進行中。"""
    tennis = {
        "results": [{"tour": "WTA", "tier": "大滿貫", "winner": "A. Sabalenka", "loser": "C. Gauff",
                     "event": "Australian Open", "event_key": "Australian Open", "date": "01/31", "round": "Final"}],
        "tournaments": [{"name": "Australian Open", "event_key": "Australian Open", "status": "進行中", "tier": "大滿貫"}],
        "open_events": ["Australian Open"],
    }
    page = re.sub(r"<[^>]+>", "", mr._render_sports_html({"tennis": tennis}, htmllib))
    assert "冠軍" in page and "Australian Open" in page and "（進行中）" in page


def test_review_pr64_round2_fetch_records_open_draws(monkeypatch):
    import datetime as dt
    from zoneinfo import ZoneInfo

    def comp(cid, slug_round, day, win, lose):
        return {"id": cid, "date": f"2027-01-{day}T09:00Z", "round": {"displayName": slug_round},
                "status": {"type": {"completed": True}},
                "competitors": [{"athlete": {"shortName": win}, "winner": True},
                                {"athlete": {"shortName": lose}, "winner": False}]}
    event = {"shortName": "Australian Open", "date": "2027-01-12T00:00Z", "endDate": "2027-02-02T00:00Z",
             "groupings": [{"grouping": {"slug": "womens-singles"}, "competitions": [comp("w", "Final", 31, "A. Sabalenka", "C. Gauff")]},
                           {"grouping": {"slug": "mens-singles"}, "competitions": [comp("m", "Semifinal", 30, "J. Sinner", "A. Zverev")]}]}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"events": [event]}
    monkeypatch.setattr(mr, "_http_get", lambda *a, **k: Response())
    tennis = mr.fetch_tennis_digest(dt.datetime(2027, 2, 1, 6, tzinfo=ZoneInfo("Asia/Taipei")))
    assert tennis["open_events"] == ["Australian Open"]
