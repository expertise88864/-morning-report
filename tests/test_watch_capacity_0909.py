"""Storage must not inherit the daily model review limit."""
import copy
import inspect
import json

import analysis_recap as rc
import evidence_packet as ep
import morning_report as mr


def prior(count=9):
    return {'date': '2026-09-08', 'watch_seq': count, 'items': [], 'watch': [
        {'watch_id': f'w{i}', 'trigger': f'待驗證事件{i}', 'horizon': '1-4w',
         'status': 'open', 'created': '2026-09-07', 'last_reviewed': '',
         'deadline': '2026-10-05'} for i in range(1, count + 1)]}


def test_storage_roundtrip_keeps_ninth_and_tenth_watch(tmp_path):
    path = tmp_path / 'recap.json'
    path.write_text(json.dumps(prior()), encoding='utf-8')
    obj = {'watch_triggers': [{'trigger': '財報前檢查報價', 'why': '驗證供需', 'horizon': '1-4w'}]}
    manifest = {}
    assert rc.save(path, obj, {'target_session_date': '2026-09-09'}, manifest) == rc.SAVED
    saved = rc.load(path)
    assert len(saved['watch']) == 10 and saved['watch_seq'] == 10
    assert saved['watch'][-1]['trigger'] == '財報前檢查報價'
    assert manifest['llm'].get('watch_dropped_capacity', 0) == 0
    assert len(rc.usable_watch(saved, '2026-09-10')) == 8


def test_rotation_reviews_the_previously_deferred_watch_without_mutating_prior():
    p = prior()
    before = copy.deepcopy(p)
    first = rc.usable_watch(p, '2026-09-09')
    assert p == before
    obj = {'watch_review': [{'watch_id': w['watch_id'], 'status': 'not_triggered'} for w in first]}
    ledger, seq, dropped = rc.carry_watch(p, obj, '2026-09-09')
    second = rc.usable_watch({'date': '2026-09-09', 'watch': ledger, 'watch_seq': seq}, '2026-09-10')
    assert len(first) == len(second) == 8 and dropped == 0
    assert len({w['watch_id'] for w in first + second}) == 9
    assert second[0]['watch_id'] not in {w['watch_id'] for w in first}


def test_deferred_watch_keeps_deadline_and_still_expires():
    p = prior(10)
    p['watch'][-1]['deadline'] = '2026-09-08'
    p['watch'][-2]['created'] = '2026-09-09'
    chosen = rc.usable_watch(p, '2026-09-09')
    assert {w['watch_id'] for w in chosen}.isdisjoint({'w9', 'w10'})
    ledger, _, _ = rc.carry_watch(p, {}, '2026-09-09')
    assert 'w10' not in {w['watch_id'] for w in ledger}
    assert next(w for w in ledger if w['watch_id'] == 'w9')['deadline'] == '2026-10-05'


def test_packet_and_legacy_projection_keep_daily_budget_without_mutating_state():
    p = prior(10)
    p['watch'][-1]['deadline'] = '2026-09-08'
    p['watch'][-2]['created'] = '2026-09-09'
    original = copy.deepcopy(p)
    projected = rc.prompt_recap(p, '2026-09-09')
    packet = ep.build({'ANALYSIS_RECAP': p}, {}, {}, [], [], {},
                      as_of='2026-09-09', target_session_date='2026-09-09',
                      sanitize=lambda text: text)
    selected = {w['watch_id'] for w in packet['yesterday_watch']}
    assert len(selected) == 8
    assert selected.isdisjoint({'w9', 'w10'})
    assert {w['watch_id'] for w in projected['watch']} == selected
    assert {w['watch_id'] for w in packet['market']['ANALYSIS_RECAP']['watch']} == selected
    assert p == original


def test_weekend_packet_reviews_report_day_watches_not_monday_open():
    p = prior(10)
    for row in p['watch']:
        row['created'] = '2026-09-24'
        row['deadline'] = '2026-10-05'
    p['watch'][-1]['deadline'] = '2026-09-26'
    packet = ep.build({'ANALYSIS_RECAP': p}, {}, {}, [], [], {},
                      as_of='2026-09-26T08:00:00+08:00',
                      target_session_date='2026-09-28',
                      sanitize=lambda text: text)
    selected = {w['watch_id'] for w in packet['yesterday_watch']}
    projected = {w['watch_id'] for w in packet['market']['ANALYSIS_RECAP']['watch']}
    assert len(selected) == 8
    assert 'w10' in selected and selected == projected


def test_weekend_production_preprojection_keeps_saturday_due_watch():
    assert 'report_date=now_tpe.strftime("%Y-%m-%d")' in inspect.getsource(
        mr._phase_events_and_models)
    p = prior(10)
    for row in p['watch']:
        row['created'] = '2026-09-24'
        row['deadline'] = '2026-10-05'
    p['watch'][-1]['deadline'] = '2026-09-26'
    original = copy.deepcopy(p)
    # Production first projects the recap before evidence_packet.build.
    projected = rc.prompt_recap(p, '2026-09-28',
                                report_date='2026-09-26')
    packet = ep.build({'ANALYSIS_RECAP': projected}, {}, {}, [], [], {},
                      as_of='2026-09-26T08:00:00+08:00',
                      target_session_date='2026-09-28',
                      sanitize=lambda text: text)
    assert 'w10' in {w['watch_id'] for w in packet['yesterday_watch']}
    assert p == original


def test_weekend_new_watch_uses_report_day_without_changing_view_session(tmp_path):
    path = tmp_path / 'recap.json'
    obj = {'watch_triggers': [
        {'trigger': '追蹤週末事件', 'why': '核對後續公告', 'horizon': '1-5d'}]}
    packet = {'as_of': '2026-09-26T08:00:00+08:00',
              'target_session_date': '2026-09-28'}
    assert rc.save(path, obj, packet) == rc.SAVED
    saved = rc.load(path)
    assert saved['date'] == '2026-09-28'  # 觀點仍屬下一交易日
    assert saved['watch'][0]['created'] == '2026-09-26'
    assert saved['watch'][0]['deadline'] == rc._days_after('2026-09-26', 5)
    assert saved['watch'][0]['watch_id'] in {
        w['watch_id'] for w in rc.usable_watch(saved, '2026-09-27')}
    assert '_arc.report_day(packet)' in inspect.getsource(
        mr._luna_analysis)
    assert rc.report_day({'as_of': 'invalid',
                          'target_session_date': '2026-09-28'}) == '2026-09-28'
