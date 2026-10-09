"""No review-opportunity inference without the supplied previous session."""
import json

import pytest

import analysis_recap as recap


@pytest.mark.parametrize("created,deadline,today,previous,missed,no_session", [
    ('2026-09-04', '2026-09-05', '2026-09-07', '2026-09-04', 0, 1),
    ('2026-09-07', '2026-09-08', '2026-09-09', '2026-09-08', 1, 0),
    ('2026-09-04', '2026-09-05', '2026-09-07', '', 1, 0),
    ('2026-09-04', '2026-09-05', '2026-09-07', 'unknown', 1, 0),
])
def test_expiry_telemetry_uses_session_evidence_and_preserves_history(
        tmp_path, created, deadline, today, previous, missed, no_session):
    path = tmp_path / 'recap.json'
    row = {'watch_id': 'w1', 'trigger': 'PRIVATE_TRIGGER', 'created': created,
           'deadline': deadline, 'last_reviewed': '', 'status': 'open'}
    path.write_text(json.dumps({'date': created, 'watch': [row]}), encoding='utf-8')
    manifest = {}
    packet = {'target_session_date': today, 'market': {'LAST_TRADING_SESSION': previous}}
    assert recap.save(path, {}, packet, manifest) == recap.SAVED
    assert manifest['llm']['watch_expired_unreviewed'] == missed
    assert manifest['llm']['watch_expired_no_review_session'] == no_session
    assert 'PRIVATE_TRIGGER' not in str(manifest)
    assert row['deadline'] == deadline
