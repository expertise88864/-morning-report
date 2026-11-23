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
