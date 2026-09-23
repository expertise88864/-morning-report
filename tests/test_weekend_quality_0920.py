import datetime as dt
import html

import pytest

import weekend_quality as quality
import render_utils as render
from local_news_routing import select
from news_display_quality import relevant
from sports_quality import news_allowed
from sports_news_selection import select as sports_select


NOW = dt.datetime(2026, 9, 20, 7, tzinfo=dt.timezone(dt.timedelta(hours=8)))


def test_related_sources_are_dated_bounded_and_not_stance(monkeypatch, tmp_path):
    def row(title, date, url):
        return {'title': title, 'published_at': date, 'url': url}
    rows = [row('台積電公告設備', '2026-09-19T00:00:00Z', 'https://example.com/a'),
            row('台積電未來報導', '2026-09-21T00:00:00Z', 'https://example.com/b'),
            row('台積電舊報導', '2026-09-01T00:00:00Z', 'https://example.com/c'),
            row('台積電不安全網址', '2026-09-19T00:00:00Z', 'javascript:alert(1)'),
            row('XAMD 不應匹配', '2026-09-19T00:00:00Z', 'https://example.com/d')]
    monkeypatch.setattr(quality.memory, 'load', lambda *a, **kw: rows)
    episodes = [{'digest': {'tickers': [{'name': '台積電'}, {'name': 'AMD'}]}}]
    context = quality.related_news(episodes, tmp_path, NOW)
    assert len(context['台積電']) == 1
    assert context['AMD'] == []
    assert context['台積電'][0]['published_at'].endswith('+08:00')
    output = render._render_podcast_html(episodes, [], html, as_of=NOW, related_sources=context)
    assert '相關報導 2026-09-19' in output
    assert '未核對公司身分或證實觀點' in output
    assert '[看多]' not in output and '[看空]' not in output


def test_context_corruption_propagates_to_caller(monkeypatch, tmp_path):
    def fail(*a, **kw):
        raise ValueError('corrupt')
    monkeypatch.setattr(quality.memory, 'load', fail)
    with pytest.raises(ValueError):
        quality.related_news([], tmp_path, NOW)


def test_missing_archive_degrades_but_existing_empty_archive_is_healthy(tmp_path, capsys):
    import gzip
    import json
    degraded = []
    assert quality.load_context([], tmp_path, NOW, degraded) == {}
    assert degraded == ['weekend_podcast_context']
    from degradation_registry import KNOWN_DEGRADED
    assert set(degraded) <= KNOWN_DEGRADED
    assert '::warning::weekend_podcast_context' in capsys.readouterr().err
    path = tmp_path / '2026-09-20.json.gz'
    path.write_bytes(gzip.compress(json.dumps({'schema': quality.memory.SCHEMA, 'rows': []}).encode()))
    degraded.clear()
    assert quality.load_context([], tmp_path, NOW, degraded) == {}
    assert degraded == []


@pytest.mark.parametrize('name,title', [('長榮', '長榮航空公告'), ('台塑', '台塑化公告'), ('中鋼', '中鋼構公告')])
def test_substring_reading_link_does_not_claim_same_company(monkeypatch, tmp_path, name, title):
    monkeypatch.setattr(quality.memory, 'load', lambda *a, **kw: [
        {'title': title, 'published_at': '2026-09-19T00:00:00Z', 'url': 'https://example.com/news'}])
    episodes = [{'digest': {'tickers': [{'name': name}]}}]
    context = quality.related_news(episodes, tmp_path, NOW)
    output = render._render_podcast_html(episodes, [], html, as_of=NOW, related_sources=context)
    assert title in output
    assert '同公司素材' not in output
    assert '標題含此名稱，未核對公司身分或證實觀點' in output


def test_awards_dedup_without_merging_changed_counts():
    titles = ['台灣創博會 彰基奪2金2銀1銅 - 自由時報',
              '彰基創新研發成果傳捷報 創博會勇奪兩金兩銀一銅 - 台灣教會公報',
              '彰基創博會 更正2金2銀1銅', '彰基創博會奪3金2銀1銅',
              '中國醫創博會奪2金2銀1銅']
    output = select([('醫院', {'title': title}) for title in titles], [('醫院', '', 8)], 8,
                    is_dup=lambda *a: False, seen_entry=str)
    assert [r['title'] for r in output['醫院']] == [titles[0], *titles[2:]]


def test_junk_rejected_but_real_reporting_kept():
    assert not relevant('建商動態', '混搭風裝修 25坪 81萬（8/20）裝修案例效果圖')
    assert relevant('建商動態', '建商公告新建案動工')
    assert not relevant('產業/科技', '2026濁水溪紫金馬拉松－花城竹山站')
    assert not relevant('產業/科技', '8年減債239.3億 彰化重大建設全面開花')
    assert not relevant('產業/科技', 'Taichung Marathon 完賽')
    assert relevant('產業/科技', '台中AI新創發表會')
    assert relevant('產業/科技', '中科半導體廠擴充產線')
    assert not news_allowed({'title': '回饋列車來到苗栗 小球員把握交流機會'})
    assert news_allowed({'title': '中信兄弟再見安打逆轉勝'})


def test_local_region_and_event_dates_from_september_23_mail():
    import datetime as dt
    from local_news_routing import region_relevant
    from local_news_event_dates import event_date_relevant
    from morning_report import _LOCAL_REGION_TOKENS
    assert not region_relevant('新北最大里換人 學區恐變', _LOCAL_REGION_TOKENS)
    assert not region_relevant('新北市十大里排行大洗牌', _LOCAL_REGION_TOKENS)
    assert region_relevant('台中大里學區調整', _LOCAL_REGION_TOKENS)
    today = dt.date(2026, 9, 23)
    assert not event_date_relevant('交通異動', '國1大雅銜接台74線 5月13日開放通車', today)
    assert not event_date_relevant('交通異動', '國1大雅銜接台74線 1月15日開放通車', today)
    assert event_date_relevant('交通異動', '國道1號中部路段 11月1日起夜間封閉', today)
    assert event_date_relevant('交通異動', '台74線 9月24日夜間施工', today)
    assert event_date_relevant('建設', '彰化5月13日完工案後續', today)


def test_traffic_event_age_uses_taipei_report_day():
    import datetime as dt
    from zoneinfo import ZoneInfo
    from local_news_event_dates import event_date_relevant
    now_utc = dt.datetime(2026, 9, 22, 22, tzinfo=dt.timezone.utc)
    report_day = now_utc.astimezone(ZoneInfo('Asia/Taipei')).date()
    assert report_day == dt.date(2026, 9, 23)
    assert not event_date_relevant('交通異動', '台74線 8月23日開放', report_day)


def test_sports_publication_date_survives_selection_and_render():
    rows, _ = sports_select([{'title': '旅外球員全壘打', 'link': 'https://example.com/news',
                             'published_parsed': (2026, 9, 19, 22, 0, 0)}], NOW)
    assert rows[0]['published_at'] == '09/20 06:00 台北'
    output = render._render_sports_html({'news': {'MLB': rows}, 'mlb_tw': [
        {'name': '球員', 'date': '09/19', 'summary': '0 HR'}]}, html)
    assert '09/20 06:00 台北' in output
    assert '非台北新聞發布日' in output and '0 HR' in output


def test_exact_repeated_podcast_extras_not_reprinted():
    text = '主持人偏向長線持有。'
    output = render._render_podcast_html([{'digest': {'summary_points': [text],
        'action_view': text, 'market_view': '市場仍有不確定性。'}}], [], html, as_of=NOW)
    assert output.count(text) == 1
    assert '市場仍有不確定性。' in output


@pytest.mark.parametrize('compact', [None, 2])
def test_extra_matching_hidden_point_is_preserved(compact):
    text = '主持人偏向長線持有。'
    ep = {'digest': {'summary_points': ['其他重點'] * 5 + [text], 'action_view': text}}
    output = render._render_podcast_html([ep], [], html, as_of=NOW, compact_points=compact)
    assert output.count(text) == 1
    assert '操作思路' in output


def test_policy_prompt_does_not_infer_no_cost_from_missing_attachments():
    import morning_report as mr
    from tests.test_tw_policy_sources import _XML
    import tw_policy_sources as sources
    prompt = mr._build_weekend_policy_prompt(sources.parse_gazette_xml(_XML))
    rule = '未讀到附件不能聲稱管制範圍不變'
    assert rule in prompt
    assert prompt.rfind('</UNTRUSTED_SOURCE_DATA>') < prompt.index(rule)
