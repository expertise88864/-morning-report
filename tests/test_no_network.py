"""測試不得打真實網路——這條護欄本身也要有測試。

r3(突變測試審查,P2-1)實測:先前有 5 個測試會打 gazette.nat.gov.tw、
news.google.com、www.dgpa.gov.tw、openapi.twse.com.tw、www.twse.com.tw
——CI 每次 push/PR 都在真的打政府網站與 TWSE。

而且**打通或打不通,斷言完全一樣**(生產程式碼到處都有 try/except,連不上
就走降級路徑)→ 這些網路呼叫對測試零價值,只承擔風險。

風險是具體的:把那些 host 導到黑洞 IP(模擬「站在、但不回應」)後,
光 test_lifestyle + test_universe 兩個檔就跑了 **12 分 29 秒**,
超過 ci.yml 的 timeout-minutes: 10 → job 被 GitHub 砍掉、CI 紅燈,
而且與程式碼完全無關。封鎖後同樣兩個檔 1.6 秒。
"""
import socket

import pytest

# pytest 把 tests/conftest.py 載成頂層模組 `conftest`,
# 用 `from tests.conftest import ...` 會拿到**另一個** module 實例,
# 例外類別身分不同 → pytest.raises 抓不到(自測時踩到)。
from conftest import NetworkBlockedInTests


def test_outbound_dns_is_blocked():
    """任何連外的名稱解析都必須當場失敗,並指名 host。"""
    with pytest.raises(NetworkBlockedInTests) as e:
        socket.getaddrinfo("news.google.com", 443)
    assert "news.google.com" in str(e.value)
    assert "不得打真實網路" in str(e.value)


def test_outbound_tcp_is_blocked():
    with pytest.raises(NetworkBlockedInTests):
        socket.create_connection(("openapi.twse.com.tw", 443), timeout=1)


def test_localhost_still_allowed():
    """封鎖只針對連外——localhost 要留著,否則本機起服務的測試會被誤殺。"""
    socket.getaddrinfo("localhost", 0)
    socket.getaddrinfo("127.0.0.1", 0)


def test_real_fetchers_degrade_instead_of_hanging():
    """被擋之後生產程式碼必須走降級路徑,而不是拋到測試外面。

    這同時證明了審查的判斷:這些網路呼叫對斷言零價值。
    """
    import morning_report as mr
    mr._DEGRADED_STEPS.clear()
    # 公報:抓不到要拋 GazetteUnavailable 讓呼叫端記降級(不是靜默回空)
    import tw_policy_sources as tps
    with pytest.raises(tps.GazetteUnavailable):
        tps.fetch_gazette(mr._http_get_relaxed_strict)
