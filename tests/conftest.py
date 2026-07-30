"""
pytest 共用設定與 fixtures。

重點：
- 在 import morning_report 之前先塞假環境變數（雖然程式已改成 import 不會炸，
  但設好可讓測試更穩定、不依賴本機環境）。
- 提供 fake_yf fixture：用 monkeypatch 把 morning_report.yf.Ticker 換成假物件，
  測試完全不連 Yahoo Finance。
"""
import os
import socket as _socket

os.environ.setdefault("GMAIL_USER", "test@example.com")
os.environ.setdefault("GMAIL_APP_PASSWORD", "dummy")
os.environ.setdefault("LLM_PROVIDER", "gemini")

import pandas as pd
import pytest

import morning_report as mr


@pytest.fixture(autouse=True)
def _reset_twse_stock_day_all_cache(monkeypatch, tmp_path_factory):
    """STOCK_DAY_ALL 共用快取在測試間必須清空,否則 mock 資料會跨測試污染;
    重試退避在測試中歸零,避免失敗路徑測試慢 10 倍。"""
    mr._TWSE_STOCK_DAY_ALL_CACHE["data"] = None
    mr._TWSE_STOCK_DAY_ALL_CACHE.pop("failed", None)
    mr._RSS_CONTENT_CACHE.clear()   # N5:RSS 內容快取也必須測試間清空,避免跨測試污染
    mr._FEED_STATS.clear()          # V2-N1:per-host feed 統計同理
    mr._HTTP_HOST_STATS.clear()     # 批#32:_http_get per-host 熔斷計數同理
    mr._DEGRADED_STEPS.clear()      # 批#32:模組級可變 list,不重置會跨測試污染資料品質區
    monkeypatch.setattr(mr, "_TWSE_RETRY_SLEEP_BASE", 0.0)
    # 批#37:_http_get 的退避用自己的 backoff 參數 + 裸 time.sleep,不受
    # _TWSE_RETRY_SLEEP_BASE 影響——實測全套 103s 有約 84s 是真的在睡
    # (單一「全失敗」測試就睡 18s)。測試不需要真的等待;需要驗「重試次數」的
    # 測試本來就自己 patch mr.time.sleep 並計數,不受影響。
    monkeypatch.setattr(mr.time, "sleep", lambda _s: None)
    # §B:信件存檔目錄導到 tmp,避免經 deliver_report 的測試把 *.html.gz 寫進真實 state/emails/
    monkeypatch.setattr(mr, "EMAIL_ARCHIVE_DIR", tmp_path_factory.mktemp("emails"))
    # 政策「已顯示」記錄同理導到 tmp(deliver_report 內會呼叫 mark_intel_shown)
    monkeypatch.setattr(mr, "INTEL_SHOWN_FILE",
                        tmp_path_factory.mktemp("intel") / "intel_shown.json")
    # Polymarket 護欄(斷路器/時間預算)測試間重置,避免跨測試污染
    mr._POLY_GUARD.update({"spent": 0.0, "consecutive_failures": 0, "tripped": False})
    # Polymarket delta 快照導到 tmp(地基批#4)
    monkeypatch.setattr(mr, "POLY_HISTORY_FILE",
                        tmp_path_factory.mktemp("poly") / "poly_history.json")
    # 類股排名快照導到 tmp(地基批#5)
    monkeypatch.setattr(mr, "SECTOR_RANK_FILE",
                        tmp_path_factory.mktemp("sector") / "sector_rank_history.json")
    # Forecast Ledger 導到 tmp(2026-07-18)
    monkeypatch.setattr(mr, "FORECAST_LEDGER_FILE",
                        tmp_path_factory.mktemp("ledger") / "forecast_ledger.json")
    # model_history 分區目錄與 legacy 檔導到 tmp:防測試讀寫真實 state/(地基批#1)
    _mh = tmp_path_factory.mktemp("mh")
    monkeypatch.setattr(mr, "MODEL_HISTORY_FILE", _mh / "model_history.json")
    monkeypatch.setattr(mr, "MODEL_HISTORY_DIR", _mh / "model_history")
    yield
    mr._TWSE_STOCK_DAY_ALL_CACHE["data"] = None
    mr._TWSE_STOCK_DAY_ALL_CACHE.pop("failed", None)
    mr._RSS_CONTENT_CACHE.clear()
    mr._FEED_STATS.clear()


def _bdays(n: int, start: str = "2026-01-05"):
    return pd.date_range(start, periods=n, freq="B")


def make_close_df(values, index=None) -> pd.DataFrame:
    """產生只有 Close 欄位的歷史 DataFrame（模擬 yfinance .history() 回傳）。"""
    idx = index if index is not None else _bdays(len(values))
    return pd.DataFrame({"Close": list(values)}, index=idx)


class FakeTicker:
    """依 symbol 回傳預先準備好的 DataFrame；查無資料回傳空 DataFrame。"""

    def __init__(self, symbol):
        self.symbol = symbol

    # data_map / div_map 由 fixture 注入到 class attribute
    data_map: dict = {}
    div_map: dict = {}   # {symbol: pd.Series 配息(index 為除息日 Timestamp)}

    def history(self, **kwargs):
        df = FakeTicker.data_map.get(self.symbol)
        if df is None:
            return pd.DataFrame({"Close": []})
        return df.copy()

    @property
    def dividends(self):
        return FakeTicker.div_map.get(self.symbol, pd.Series([], dtype=float))


@pytest.fixture
def mkdf():
    """測試用：快速產生只含 Close 欄位的歷史 DataFrame。"""
    return make_close_df


@pytest.fixture
def bdays():
    """測試用：產生 n 個營業日 DatetimeIndex。"""
    return _bdays


@pytest.fixture
def fake_yf(monkeypatch):
    """
    回傳一個 setter：測試呼叫 set_data({symbol: DataFrame}) 後，
    morning_report 內所有 yf.Ticker(...) 都會走假資料。
    """
    def set_data(data_map: dict):
        FakeTicker.data_map = dict(data_map)
        monkeypatch.setattr(mr.yf, "Ticker", FakeTicker)
        return FakeTicker

    yield set_data
    FakeTicker.data_map = {}
    FakeTicker.div_map = {}


# ============================================================================
# 網路封鎖(r3 突變測試審查,P2-1)
#
# 實測發現 5 個測試會打**真實網路**:gazette.nat.gov.tw、news.google.com、
# openapi.twse.com.tw、www.twse.com.tw、www.dgpa.gov.tw。也就是 CI 每次
# push/PR 都在真的打政府網站與 TWSE。而且——**打通或打不通,斷言完全一樣**,
# 這些網路呼叫對測試零價值,只承擔風險。
#
# 風險是具體的:把這些 host 導到黑洞 IP(模擬「站在、但不回應」)後,
# 光兩個測試檔就跑了 12 分 29 秒 > ci.yml 的 timeout-minutes: 10
# → job 被 GitHub 砍掉、CI 紅燈,而且**與程式碼完全無關**。
# 時間來源:_http_get 預設 timeout=20 × retries=2(3 次)= 60s/次。
#
# 封鎖之後,任何新測試意外打網路都會**當場失敗並指名 host**,而不是變成
# 一個偶爾很慢、偶爾在 CI 掛掉的謎題。
# ============================================================================
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", ""}
_real_getaddrinfo = _socket.getaddrinfo
_real_create_connection = _socket.create_connection


class NetworkBlockedInTests(RuntimeError):
    """測試意外嘗試連外。請 patch 掉該路徑的 _http_get / requests / feedparser。"""


def _blocked(host):
    return NetworkBlockedInTests(
        f"測試嘗試連線 {host} —— 測試不得打真實網路。"
        "請 patch 該路徑的 _http_get / requests.get / feedparser。"
    )


@pytest.fixture(autouse=True)
def _block_outbound_network(monkeypatch):
    def guard_getaddrinfo(host, port, *a, **kw):
        if str(host) not in _ALLOWED_HOSTS:
            raise _blocked(host)
        return _real_getaddrinfo(host, port, *a, **kw)

    def guard_create_connection(address, *a, **kw):
        host = address[0] if isinstance(address, tuple) else address
        if str(host) not in _ALLOWED_HOSTS:
            raise _blocked(host)
        return _real_create_connection(address, *a, **kw)

    monkeypatch.setattr(_socket, "getaddrinfo", guard_getaddrinfo)
    monkeypatch.setattr(_socket, "create_connection", guard_create_connection)


@pytest.fixture(autouse=True)
def _never_write_repo_state(monkeypatch, tmp_path_factory):
    """測試不得寫入 repo 的真實 state 檔。

    r4(Codex,P2)**確認**:批#52 給 run_weekend_digest 補了 _write_run_manifest,
    而週日測試沒有 monkeypatch RUN_MANIFEST_FILE → **測試把 2026-07-25 的真實
    manifest(total_seconds 468.2)覆寫成測試資料**,再被 `git add -A` 提交。
    後果是下一次 production run 讀到 model_history_days: null,失去前次歷史長度
    基準(無法偵測 history 縮短),d1_ready: null 也會讓就緒提醒重新觸發。

    個別測試各自 monkeypatch 是防不住的——漏一個就中。改為在 conftest 統一把
    寫入型 state 路徑導到暫存目錄,新增的寫入點自動受保護。

    批#71:**上面這段是原本的宣稱,但程式碼只導了 `RUN_MANIFEST_FILE` 一個檔。**
    實害:批#66 新增的 `EXDIV_HISTORY_FILE` 不在任何隔離清單裡,而我的測試
    `test_exdiv_history_keeps_the_first_record_and_prunes_old_ones` 會呼叫
    `update_exdiv_history([], 2028-01-01)` 把保留期外的紀錄修剪掉 ——
    於是真實的 `state/exdiv_history.json` **115 筆除權息事件被清成空陣列**,
    而 `days` 仍宣稱當天收集成功。那是最危險的組合:覆蓋檢查判定完整、
    紀錄卻是空的 → Top5 會用原始價格照常結算而不是作廢。再被 `git add -A` 提交。

    (與 r4 那次是**同一個病灶的第二次**:註解宣稱了一個通用性質,程式碼只做了
     一個特例。這次改成真的通用——列舉模組裡所有指向 repo `state/` 的
     Path 常數並全部導走,下次新增 state 檔自動受保護,不需要記得改這裡。)
    """
    from pathlib import Path as _Path
    d = tmp_path_factory.mktemp("state_guard")
    repo_state = _Path("state").resolve()
    modules = [mr]
    try:
        import model_history_store as _mhs
        modules.append(_mhs)
    except Exception:
        pass
    for mod in modules:
        for attr in dir(mod):
            if not (attr.endswith("_FILE") or attr.endswith("_DIR")):
                continue
            value = getattr(mod, attr, None)
            if not isinstance(value, _Path):
                continue
            try:
                inside = value.resolve().is_relative_to(repo_state)
            except (OSError, ValueError):
                inside = False
            if inside:
                monkeypatch.setattr(mod, attr, d / value.name, raising=False)
