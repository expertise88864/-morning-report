"""Delivered September 14 examples: absence of evidence is not a negative result."""
import copy

import pytest

import analysis_recap
import analysis_render
import fixtures_analysis as fx
from reader_revision import watch_status


@pytest.mark.parametrize(('text', 'ids', 'expected'), [
    ('核心 PPI 的實際月增數字不在今日證據中，第一段條件仍待驗證。',
     ['n1'], 'insufficient_evidence'),
    ('複合條件部分完成：升息機率超過七成，但核心 PPI 數字仍待驗證。',
     ['n1'], 'partially_triggered'),
    ('還在等 DRAM 現貨價的官方報價確認；今日沒有報價資訊。',
     [], 'insufficient_evidence'),
    ('官方公布月增 0.2%，未達原定 0.3% 門檻。', ['n1'], 'not_triggered'),
])
def test_real_mail_negative_status_preserves_uncertainty(text, ids, expected):
    row = dict(watch_id='w1', status='not_triggered', what_happened=text, evidence_ids=ids)
    original = copy.deepcopy(row)
    assert watch_status(row) == expected
    obj = fx.valid_analysis()
    obj['watch_review'] = [row]
    label = {'insufficient_evidence': '資料不足，續追蹤',
             'partially_triggered': '部分成立，續追蹤', 'not_triggered': '未觸發'}[expected]
    assert f'：{label}（{text}）' in analysis_render.render(obj)
    assert row == original


@pytest.mark.parametrize('status', ['insufficient_evidence', 'partially_triggered'])
def test_incomplete_status_keeps_same_watch_id_and_deadline(status):
    prior = {'watch_seq': 1, 'watch': [dict(watch_id='w1', trigger='A 且 B',
             status='open', created='2026-09-10', deadline='2026-09-20')]}
    before = copy.deepcopy(prior)
    obj = {'watch_review': [dict(watch_id='w1', status=status,
           what_happened='待補資料', evidence_ids=['n1'])]}
    ledger, seq, dropped = analysis_recap.carry_watch(prior, obj, '2026-09-14')
    assert len(ledger) == 1 and ledger[0]['watch_id'] == 'w1'
    assert ledger[0]['deadline'] == '2026-09-20'
    assert ledger[0]['last_reviewed'] == '2026-09-14'
    assert seq == 1 and dropped == 0 and prior == before


def test_contract_accepts_explicit_uncertainty_and_partial_requires_evidence():
    from analysis_schema import ANALYSIS_OUTPUT_SCHEMA
    from test_watch_review import _validate
    choices = ANALYSIS_OUTPUT_SCHEMA['properties']['watch_review']['items']['properties']['status']['enum']
    assert {'insufficient_evidence', 'partially_triggered'} <= set(choices)
    row = dict(watch_id='w1', status='insufficient_evidence',
               what_happened='缺少官方 PPI 實際值', evidence_ids=[])
    assert not _validate({'watch_review': [row]})
    row.update(status='partially_triggered', what_happened='A 已成立，B 待驗證')
    assert _validate({'watch_review': [row]})
    row['evidence_ids'] = ['n1']
    assert not _validate({'watch_review': [row]})


def test_trigger_with_explicit_missing_evidence_does_not_close_watch():
    row = dict(watch_id='w1', status='triggered', evidence_ids=['n1'],
               what_happened='核心 PPI 的實際值不在今日證據中。')
    assert watch_status(row) == 'insufficient_evidence'
    prior = {'watch': [dict(watch_id='w1', trigger='PPI 達標', status='open',
                           created='2026-09-10', deadline='2026-09-20')]}
    ledger, *_ = analysis_recap.carry_watch(prior, {'watch_review': [row]}, '2026-09-14')
    assert [w['watch_id'] for w in ledger] == ['w1']


@pytest.mark.parametrize('text', ['不只觸發 A 部分，B 也已完成。',
                                  '並非僅部分成立，条件全部觸發。',
                                  '不是部分完成，全部完成。'])
def test_negated_partial_does_not_downgrade_full_result(text):
    assert watch_status(dict(status='triggered', what_happened=text,
                             evidence_ids=['n1'])) == 'triggered'


@pytest.mark.parametrize('status', ['partially_triggered', 'not_triggered'])
def test_asserted_result_cannot_rely_only_on_stale_evidence(status):
    import analysis_validate as av
    import evidence_packet as ep
    packet = ep.build({'QQQ': {'change_pct': 1.2}, 'US_HOLIDAY': {'detected': True}},
                      {}, {}, [{'source_item_id': 'n1', 'title': '公司公告',
                                'entities': [], 'source': '公告'}], [], {},
                      as_of='2026-09-14 06:00', target_session_date='2026-09-14',
                      sanitize=lambda s, *a: s)
    packet['yesterday_watch'] = [dict(watch_id='w1', trigger='A 且 B')]
    row = dict(watch_id='w1', status=status, what_happened='部分完成：A 成立，B 待確認。',
               evidence_ids=['market:QQQ.change_pct'])
    obj = fx.valid_analysis()
    obj['watch_review'] = [row]
    problems = [p for p in av.validate(obj, packet) if 'watch_review' in p]
    assert any('不同步' in p for p in problems)
    row['evidence_ids'] = ['n1']
    assert not [p for p in av.validate(obj, packet) if 'watch_review' in p]
    row['evidence_ids'] = ['invented-id']
    assert [p for p in av.validate(obj, packet) if 'watch_review' in p]
