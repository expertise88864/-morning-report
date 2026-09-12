import datetime as dt
import html

import pytest

import podcast_dates as dates
import render_utils as render


@pytest.mark.parametrize('raw', [
    'Tue, 01 Sep 2026 00:00:00 GMT', '2026-09-01T00:00:00Z',
    '2026-09-01T08:00:00+08:00',
])
def test_rss_and_iso_publication_are_equivalent(raw):
    output = render._episode_age_tag({'published': raw}, '2026-09-12 (Sat)')
    assert '09/01 發布' in output and '11 天前' in output
    assert '錄製' not in output


def test_publication_and_report_datetime_use_taipei_day():
    episode = {'published': 'Fri, 11 Sep 2026 18:00:00 GMT'}
    clock = dt.datetime(2026, 9, 18, 18, tzinfo=dt.timezone.utc)
    assert '09/12 發布' in dates.age_tag(episode, clock)
    assert '7 天前' in dates.age_tag(episode, clock)
    assert dates.age_tag(episode, clock.isoformat()) == dates.age_tag(episode, clock)


@pytest.mark.parametrize('raw', [None, '', 'not-a-date', [], 123, '9999-12-31T23:59:59-08:00'])
def test_invalid_dates_do_not_break_email(raw):
    assert dates.age_tag({'published': raw}, '2026-09-12') == ''


def test_future_and_unknown_report_time_do_not_invent_age():
    episode = {'published': '2026-09-13T00:00:00+08:00'}
    assert '時效待確認' in dates.age_tag(episode, '2026-09-12')
    assert dates.age_tag(episode, 'invalid') == ' ・09/13 發布'
    assert '天前' not in dates.age_tag(episode, '2026-09-13')


def test_card_uses_explicit_report_clock_not_wall_clock():
    episode = {'show': '測試節目', 'title': '不同時間的觀點',
               'published': 'Tue, 01 Sep 2026 00:00:00 GMT',
               'digest': {'summary_points': ['主持人個人觀點']}}
    output = render._render_podcast_html([episode], [], html, as_of='2026-09-10 (Thu)')
    assert '9 天前' in output and '09/01 發布' in output
    assert '主持人個人觀點' in output


@pytest.mark.parametrize('clock', ['2026-09-12garbage', '2026-09-12 (Wrong)',
                                  '2026-09-12 99:99', '2026-09-12T99:99'])
def test_invalid_explicit_clock_does_not_infer_age(clock):
    assert dates.age_tag({'published': '2026-09-01'}, clock) == ' ・09/01 發布'
