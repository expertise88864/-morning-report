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
