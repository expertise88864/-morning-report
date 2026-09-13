"""Provider requests are always mocked; no paid generation in these tests."""
import json
from unittest.mock import Mock

import pytest
import requests

import podcast_digest as producer


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f'offline HTTP {status}', response=response)


@pytest.fixture
def request_mocks(monkeypatch):
    post, sleep = Mock(), Mock()
    monkeypatch.setattr(producer.requests, 'post', post)
    monkeypatch.setattr(producer.time, 'sleep', sleep)
    return post, sleep


@pytest.mark.parametrize('status', [400, 401, 402, 403, 404, 422])
def test_permanent_rejection_stops_without_sleep(request_mocks, status):
    post, sleep = request_mocks
    post.side_effect = http_error(status)
    with pytest.raises(RuntimeError, match=f'HTTP {status}'):
        producer.deepseek_digest('離線逐字稿')
    assert post.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize('error', [http_error(408), http_error(409),
    http_error(429), http_error(500), http_error(503), requests.Timeout('offline')])
def test_transient_failure_exhausts_budget_without_final_sleep(request_mocks, error):
    post, sleep = request_mocks
    post.side_effect = error
    with pytest.raises(RuntimeError, match='DeepSeek 摘要失敗'):
        producer.deepseek_digest('離線逐字稿')
    assert post.call_count == 4
    assert sleep.call_count == 3
    assert all(call.args == (15,) for call in sleep.call_args_list)


@pytest.mark.parametrize('first_error', [http_error(503), requests.Timeout('offline'),
                                       ValueError('invalid JSON')])
def test_retry_can_recover_and_return_valid_digest(request_mocks, first_error):
    post, sleep = request_mocks
    payload = {'summary_points': ['主持人分享市場與產業觀點。'], 'tickers': []}
    response = Mock()
    response.json.return_value = {'choices': [{'message': {'content': json.dumps(payload)}}]}
    post.side_effect = [first_error, response]
    result = producer.deepseek_digest('離線逐字稿')
    assert result['summary_points'] == payload['summary_points']
    assert post.call_count == 2
    sleep.assert_called_once_with(15)


def test_transient_then_payment_rejection_stops_remaining_attempts(request_mocks):
    post, sleep = request_mocks
    post.side_effect = [http_error(503), http_error(402)]
    with pytest.raises(RuntimeError, match='HTTP 402'):
        producer.deepseek_digest('離線逐字稿')
    assert post.call_count == 2
    sleep.assert_called_once_with(15)
