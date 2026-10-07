"""
pytest 共用設定與 fixtures。

重點：
- 在 import morning_report 之前先塞假環境變數（雖然程式已改成 import 不會炸，
  但設好可讓測試更穩定、不依賴本機環境）。
- 提供 fake_yf fixture：用 monkeypatch 把 morning_report.yf.Ticker 換成假物件，
  測試完全不連 Yahoo Finance。
"""
import os
import shutil as _shutil
import smtplib as _smtplib
import socket as _socket
import sys as _sys
import tempfile as _tempfile
from pathlib import Path as _PathLib
from urllib.parse import urlsplit as _urlsplit

import pandas as pd
import pytest

# Never inherit production credentials or recipients into an offline test run.
# These changes live only in the pytest process (and its child processes).
os.environ["GMAIL_USER"] = "test@example.com"
os.environ["GMAIL_APP_PASSWORD"] = ""
os.environ["RECIPIENT"] = "test@example.com"
os.environ["CONTACT_EMAIL"] = "test@example.com"
os.environ["RADAR_RECIPIENT"] = "test@example.com"
os.environ["QUALITY_RECIPIENT"] = "test@example.com"
os.environ["LLM_PROVIDER"] = "gemini"
for _credential in (
    "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "FRED_API_KEY",
    "GEMINI_API_KEY", "OPENAI_API_KEY", "FINMIND_TOKEN",
):
    os.environ[_credential] = ""


class SMTPBlockedInTests(RuntimeError):
    """An offline test must replace SMTP with an explicit in-memory fake."""


def _blocked_smtp(*_args, **_kwargs):
    raise SMTPBlockedInTests("SMTP is blocked in offline tests")


# Install before importing the report or collecting tests, not just per test.
_smtplib.SMTP = _blocked_smtp
_smtplib.SMTP_SSL = _blocked_smtp


_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


class NetworkBlockedInTests(RuntimeError):
    """An offline test must replace outbound network access with a fake."""


def _blocked(host):
    return NetworkBlockedInTests(
        f"測試嘗試連線 {host} —— 測試不得打真實網路。"
        "請 patch 該路徑的 _http_get / requests.get / feedparser。"
    )


def _audit_network_call(event, args):
    """Reject Python socket traffic during collection, before fixtures start.

    Native curl clients need their own collection-time guard below.  No socket
    is opened by this hook.
    """
    if event == "socket.getaddrinfo":
        host = args[0] if args else None
    elif event == "socket.connect":
        address = args[1] if len(args) > 1 else None
        if not isinstance(address, tuple):  # local Unix-domain socket
            return
        host = address[0] if address else None
    else:
        return
    if host is None:
        return  # local passive resolution
    name = os.fsdecode(host) if isinstance(host, bytes) else str(host)
    if name not in _ALLOWED_HOSTS:
        raise _blocked(name)


_sys.addaudithook(_audit_network_call)


# curl_cffi uses native libcurl, so Python socket audit events do not see its
# outbound requests.  Install this before importing report code or collecting
# tests; an autouse fixture starts too late for import-time requests.
import curl_cffi.requests as _curl_requests  # noqa: E402

_REAL_CURL_REQUEST = _curl_requests.Session.request


def _guard_curl_request(self, method, url, *args, **kwargs):
    host = _urlsplit(str(url)).hostname or str(url)
    if host not in _ALLOWED_HOSTS:
        raise _blocked(host)
    return _REAL_CURL_REQUEST(self, method, url, *args, **kwargs)


_CURL_COLLECTION_GUARD = _guard_curl_request
_curl_requests.Session.request = _CURL_COLLECTION_GUARD

# AsyncSession also uses native libcurl; the synchronous Session guard does
# not intercept its requests, and Python socket audit events cannot see them.
_REAL_ASYNC_CURL_REQUEST = _curl_requests.AsyncSession.request


async def _guard_async_curl_request(self, method, url, *args, **kwargs):
    host = _urlsplit(str(url)).hostname or str(url)
    if host not in _ALLOWED_HOSTS:
        raise _blocked(host)
    return await _REAL_ASYNC_CURL_REQUEST(self, method, url, *args, **kwargs)


_guard_async_curl_request._morning_offline_guard = True
_curl_requests.AsyncSession.request = _guard_async_curl_request


# Python's audit events sit below Path.open, os.open, shutil.rmtree and the
# ordinary os/pathlib wrappers.  Keep this active from collection onward: a
# per-test monkeypatch cannot protect a collection-time write or a hard stop.
_REAL_STATE_ROOT = (_PathLib(__file__).resolve().parents[1] / "state").resolve()
_WRITE_OPEN_FLAGS = (
    os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
)


def _audit_targets_repo_state(path) -> bool:
    if isinstance(path, int):  # an already-open descriptor has no pathname
        return False
    try:
        candidate = _PathLib(os.fsdecode(path)).resolve()
    except (OSError, ValueError, TypeError):
        return False
    return candidate == _REAL_STATE_ROOT or _REAL_STATE_ROOT in candidate.parents


def _audit_state_write(event, args):
    if event == "open":
        path, mode, flags = args
        writing = any(ch in str(mode) for ch in "wax+") or (
            isinstance(flags, int) and bool(flags & _WRITE_OPEN_FLAGS)
        )
        targets = (path,) if writing else ()
    elif event == "os.rename":
        targets = args[:2]  # moving a production file away is destructive too
    elif event in {
        "os.remove", "os.rmdir", "os.mkdir", "os.truncate",
        "os.chmod", "os.chown", "os.utime", "os.link", "os.symlink",
    }:
        # link/symlink have source and destination; the rest have one path.
        targets = args[:2] if event in {"os.link", "os.symlink"} else args[:1]
    else:
        return
    for target in targets:
        if _audit_targets_repo_state(target):
            raise AssertionError(
                f"測試試圖寫入 repo 的真實 state:{event} → {target}"
            )


_sys.addaudithook(_audit_state_write)

# Import-time production constants must point at a disposable copy, not at the
# versioned state.  Keep its historical contents for read-path tests; the audit
# guard above still protects the original tree and contract tests can read it.
_TEST_STATE_SANDBOX = _tempfile.TemporaryDirectory(prefix="morning-test-state-")
_TEST_STATE_ROOT = _PathLib(_TEST_STATE_SANDBOX.name) / "state"
_shutil.copytree(_REAL_STATE_ROOT, _TEST_STATE_ROOT)
os.environ["STATE_ROOT"] = str(_TEST_STATE_ROOT)
os.environ["TWSE_TOP100_ARCHIVE_FILE"] = str(_TEST_STATE_ROOT / "twse_top100_archive.json")
os.environ["REVENUE_CONSENSUS_FILE"] = str(_TEST_STATE_ROOT / "revenue_consensus.json")
# Python subprocesses do not inherit sys.addaudithook or monkeypatches.  Load
# the same offline boundary at child interpreter startup, only in pytest's
# environment; the report runner itself never gets this PYTHONPATH entry.
_CHILD_GUARD_DIR = _PathLib(__file__).resolve().parent / "offline_child_guard"
os.environ["MORNING_TEST_FORMAL_STATE_ROOT"] = str(_REAL_STATE_ROOT)
os.environ["PYTHONPATH"] = os.pathsep.join(
    part for part in (str(_CHILD_GUARD_DIR), os.environ.get("PYTHONPATH", "")) if part
)


# Install the collection-time state guard before importing production modules.
import morning_report as mr  # noqa: E402

# **子目錄裡的測試也要 import 得到 `fixtures_analysis`**(2026-09-04)。
# pytest 只會把「測試檔自己所在的目錄」放進 sys.path,所以 tests/incidents/
# 底下的檔案看不到住在 tests/ 的共用 fixture 模組 —— 那會在收集階段炸,
# 也就是退出碼 2 那一種最難查的失敗。
_TESTS_DIR = str(_PathLib(__file__).resolve().parent)
if _TESTS_DIR not in _sys.path:
    _sys.path.insert(0, _TESTS_DIR)


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
_real_getaddrinfo = _socket.getaddrinfo
_real_create_connection = _socket.create_connection


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
    # 批#78 r1(Codex,P2 的同類):**不能寫 `Path("state")`。**
    # 那是相對於 process CWD 的,從 repo 根目錄以外啟動 pytest 時會解析到
    # 別的地方 —— 於是這道守衛「保護」了一個不存在的目錄,真實 state 全裸。
    # 守衛的位置必須由**這個檔案的位置**決定,那是唯一不會動的錨。
    repo_state = _Path(__file__).resolve().parents[1] / "state"

    # 批#74(第七輪 P1-10):**OS 層寫入守衛。**
    # 上面那套「掃描指向 repo state 的 Path 常數」比逐一 monkeypatch 好,
    # 但仍然漏兩類:
    #   (a) 函式內動態組出的路徑(例如 `STATE_ROOT / "gooaye_radar.json"`
    #       出現在函式體內,模組層掃不到)
    #   (b) 直接 `open("state/…", "w")`
    # 所以再加一道**與命名/宣告位置無關**的守衛:任何指向 repo `state/` 的
    # 寫入型操作直接拋。這才是真正的不變式 —— 批#71 r1 那次
    # (真實 exdiv_history.json 115 筆被清空並提交)就是被上述兩類漏掉的。
    def _blocked(target) -> bool:
        try:
            rp = _Path(target).resolve()
        except (OSError, ValueError, TypeError):
            return False
        return rp == repo_state or repo_state in rp.parents

    def _guard(name, orig, path_of=lambda a, kw: a[0]):
        def wrapper(*args, **kwargs):
            try:
                target = path_of(args, kwargs)
            except Exception:
                target = None
            if target is not None and _blocked(target):
                raise AssertionError(
                    f"測試試圖寫入 repo 的真實 state:{name} → {target}"
                    " / 請改用 tmp 路徑(conftest 已把模組層 state 常數導走);"
                    " 若是新增的 state 路徑,請確認它由 STATE_ROOT 衍生。")
            return orig(*args, **kwargs)
        return wrapper

    import builtins as _builtins
    import os as _os
    for _cls, _name in ((_Path, "write_text"), (_Path, "write_bytes"),
                        (_Path, "unlink")):
        _orig = getattr(_cls, _name)
        monkeypatch.setattr(_cls, _name,
                            _guard(f"Path.{_name}", _orig), raising=False)

    # r1(Codex,P1):**`Path.replace`/`rename` 要守的是「目的地」。**
    # 這兩個是以類別方法被呼叫的,wrapper 收到 `(self, target)` ——
    # `args[0]` 是**來源**。第一版用預設的 `args[0]` 取路徑,於是
    # `tmp.replace(Path("state/exdiv_history.json"))` 完全不會被擋,
    # 而那正是本批要防的那一類不可回復損毀
    # (`model_history_store.write_partition_manifest` 就是這樣寫 manifest 的:
    #  `tmp.replace(partition_dir / MANIFEST_NAME)`)。
    # 來源與目的地**兩邊都要檢查**:把 repo state 搬走一樣是損毀。
    def _guard_move(name, orig):
        def wrapper(self, target, *a, **kw):
            for candidate in (self, target):
                if _blocked(candidate):
                    raise AssertionError(
                        f"測試試圖搬動 repo 的真實 state:{name} "
                        f"{self} → {target}")
            return orig(self, target, *a, **kw)
        return wrapper

    for _name in ("replace", "rename"):
        _orig = getattr(_Path, _name)
        monkeypatch.setattr(_Path, _name,
                            _guard_move(f"Path.{_name}", _orig), raising=False)
    # **`os.rename` 也要擋,而且兩端都要檢查**(2026-09-02 r13 外審)。
    #
    # 先前只 patch 了 `os.replace`,而 `shutil.move` 走的是 `os.rename`
    # —— 於是一條「把真實 state 搬走再用 finally 搬回來」的測試**完全沒有
    # 被擋下**。而那個檔正是剛被標成「必要」的 `analysis_recap.json`:
    # pytest 被強制中斷時它就永遠不見了(這件事在驗證修正時**真的發生過
    # 一次**,靠 `git checkout` 救回來)。
    #
    # 更隱蔽的是舊寫法只看 `a[1]`(**目的地**):於是「搬**進** state」會擋、
    # 「從 state **搬走**」不擋 —— 而後者才是不可回復的那個方向。
    # `Path.replace/rename` 的守衛早就兩端都看了,`os.*` 這一半沒跟上:
    # 同一條規則兩份實作,其中一份漏了半邊。
    def _guard_os_move(name, orig):
        def wrapper(src, dst, *a, **kw):
            for candidate in (src, dst):
                if _blocked(candidate):
                    raise AssertionError(
                        f"測試試圖搬動 repo 的真實 state:{name} "
                        f"{src} → {dst}")
            return orig(src, dst, *a, **kw)
        return wrapper

    for _fn in ("replace", "rename"):
        _orig_os = getattr(_os, _fn)
        monkeypatch.setattr(_os, _fn, _guard_os_move(f"os.{_fn}", _orig_os))
    _orig_open = _builtins.open

    def _open_guard(file, mode="r", *a, **kw):
        if any(ch in str(mode) for ch in ("w", "a", "x", "+")) and _blocked(file):
            raise AssertionError(
                f"測試試圖以 {mode!r} 開啟 repo 的真實 state:{file}")
        return _orig_open(file, mode, *a, **kw)

    monkeypatch.setattr(_builtins, "open", _open_guard)
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
                resolved = value.resolve()
                inside = (resolved.is_relative_to(_TEST_STATE_ROOT)
                          or resolved.is_relative_to(repo_state))
            except (OSError, ValueError):
                inside = False
            if inside:
                monkeypatch.setattr(mod, attr, d / value.name, raising=False)


# ---------------------------------------------------------------- state 不變式
#: **不要再玩打地鼠**(2026-09-02 r14 外審)。
#:
#: 早期 monkeypatch 守衛只列舉部分 API，曾漏掉
#: `os.remove` / `os.rmdir` / `shutil.rmtree` / `Path.open` / `os.truncate`。
#: 上面的 Python audit hook 現在攔截這些 API 的底層事件（包括收集階段），
#: 但子行程與繞過 Python audit 的原生程式碼仍不受此 hook 保護；不能把它
#: 說成完整的隔離式 state 根目錄。
#: 而這個 repo 已經有三次實害紀錄(覆寫 manifest、清掉 exdiv history、
#: 搬走 analysis_recap;最後那次還是我在**驗證守衛修正時**造成的)。
#:
#: 這一層不依賴「有沒有漏 patch 哪個 API」:整輪測試跑完之後,
#: 直接問 git「`state/` 有沒有被動過」。
#:
#: **但它是事後偵測,不是寫入隔離**(2026-09-02 r15 外審)。它掛在
#: `pytest_sessionfinish` —— 只有 pytest **正常走完**才會執行。SIGKILL、
#: runner crash、IDE 的 stop、斷電都不會跑到這裡,而「測試中途被強制
#: 中斷」正是那次真的弄丟 `analysis_recap` 的形狀(我在驗證守衛修正時
#: 把它搬走,然後暫存目錄被清掉)。所以這裡只宣稱:
#:
#:   * 正常結束的那一輪,漏 patch 哪個 API 都會被抓到;
#:   * 被強制中止的那一輪,**這一層什麼都保證不了**。
#:
#: 測試的預設 `STATE_ROOT` 現在是正式 state 的一次性副本；契約測試仍可
#: 唯讀檢查真實 state。子行程或原生程式碼若自行定位 repo 路徑，仍能繞過
#: 本行程的 audit hook，因此這不是對所有外部程序的完整檔案系統沙箱。
#: 這一輪**開始時** `state/` 長什麼樣。`None` = 查不動;
#: `"__unset__"` = `pytest_sessionstart` 根本沒跑(不是 pytest 主流程)。
_STATE_BASELINE = "__unset__"


def _state_fingerprint(root):
    """`state/` 現在的樣子 → `(porcelain 行, {髒檔: 內容雜湊})`;查不動回 None。

    **雜湊那一半是必要的**:porcelain 只說「這個檔是 M」——
    測試改動一個**本來就是 M** 的檔,前後兩次的 porcelain 完全一樣。
    只比對狀態行等於漏掉那一類。
    """
    import hashlib
    import subprocess
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", "state"],
            cwd=root, capture_output=True, encoding="utf-8",
            errors="replace", timeout=60)
    except Exception:                       # noqa: BLE001
        return None
    if out.returncode != 0:
        return None
    lines = tuple(ln for ln in (out.stdout or "").splitlines() if ln.strip())
    digests = {}
    for ln in lines:
        rel = ln[3:].strip().strip('"')
        rel = rel.split(" -> ")[-1]         # rename:記重新命名之後那個
        f = root / rel
        try:
            digests[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
        except OSError:
            digests[rel] = "<讀不到>"        # 已刪除也是一種狀態
    return lines, digests


#: `tests/incidents/` 底下的每一條都是 incident 回歸測試(外審 2026-09-04 P3)。
#: 那些檔原本以批次/日期命名散在 tests/ 裡,共同性質不是主題而是**來歷**。
_INCIDENT_DIR = _PathLib(__file__).resolve().parent / "incidents"


def is_incident_path(path) -> bool:
    """這個檔案在不在 `tests/incidents/` 裡。

    **前綴比對不等於路徑包含**(外審 2026-09-04 r1 P3):`str.startswith`
    會把 `incidents_archive/` 這種兄弟目錄底下的檔也算進來,於是 `-m incident`
    悄悄多出一批不是事故回歸的測試 —— 而那個標記的意義就是「來歷」。
    (同一天在發佈路徑上也踩過一次:`state/../morning_report.py` 通過了
    `case "$p" in state/*)`。)
    """
    try:
        return _PathLib(str(path)).resolve().is_relative_to(_INCIDENT_DIR)
    except (OSError, ValueError):
        return False


def pytest_collection_modifyitems(items):
    """**標記用目錄套,而且 hook 放在這個唯一的 conftest 裡。**

    在 `tests/incidents/` 底下也放一個 conftest 是更直覺的寫法,但兩個 conftest 在
    `sys.modules` 裡都叫 `conftest` —— `tests/test_no_network.py` 的
    `from conftest import NetworkBlockedInTests` 會拿到子目錄那一個,
    整輪在收集階段就掛掉(2026-09-04 實測)。所以只留一個。

    用目錄而不是在 26 個檔案裡各寫一行:新丟進去的檔自動被標,不會有人忘記。
    """
    for item in items:
        if is_incident_path(getattr(item, "path", None) or getattr(item, "fspath", "")):
            item.add_marker(pytest.mark.incident)


def pytest_sessionstart(session):
    """**基準是「這一輪開始時」,不是 HEAD**(2026-09-03 r16 外審)。

    先前直接問「跑完之後 state/ 乾淨嗎」—— 於是使用者在開跑之前就有
    合法的未提交 state(這個 repo 的生產流程本來就會改 `state/`)時,
    一輪什麼都沒碰的測試也會被判成「測試動了 state」。
    那道不變式要驗的是**測試有沒有改東西**,不是「你開跑前乾不乾淨」。
    """
    global _STATE_BASELINE
    _STATE_BASELINE = _state_fingerprint(_PathLib(__file__).resolve().parents[1])


def _pytest_failed_code() -> int:
    """讓整輪 pytest 以「有測試失敗」的退出碼結束。"""
    code = getattr(pytest, "ExitCode", None)
    return int(code.TESTS_FAILED) if code is not None else 1


def pytest_sessionfinish(session, exitstatus):
    root = _PathLib(__file__).resolve().parents[1]
    after = _state_fingerprint(root)
    if _STATE_BASELINE is None or after is None:
        # **查詢失敗不等於乾淨**(r14 外審第二輪):git 出錯就當成
        # 「不知道」,而不知道不可以被讀成「沒事」—— 這道不變式的
        # 全部意義就是不依賴任何人的自律。
        print("[state-invariant] git status 查不動,無法確認 state 是否被動過",
              file=_sys.stderr)
        session.exitstatus = _pytest_failed_code()
        return
    if _STATE_BASELINE == "__unset__":
        return                              # sessionstart 沒跑(不是 pytest 主流程)
    before_lines, before_hashes = _STATE_BASELINE
    after_lines, after_hashes = after
    changed = sorted(
        set(after_lines) - set(before_lines)) or []
    changed += sorted(f"{rel}(內容變了)" for rel, h in after_hashes.items()
                      if rel in before_hashes and before_hashes[rel] != h)
    gone = sorted(set(before_lines) - set(after_lines))
    if not changed and not gone:
        return
    # **不自動還原**:那會把使用者自己的修改一起丟掉(這個 repo 的
    # `state/` 本來就會被生產流程改)。只把事實喊出來,並指出怎麼救。
    print(chr(10) + "=" * 68, file=_sys.stderr)
    print("[state-invariant] 這一輪測試改動了 state/:", file=_sys.stderr)
    for ln in (changed + gone)[:20]:
        print("   " + ln, file=_sys.stderr)
    print("測試不應該修改 repo 的真實 state(見上面的守衛說明);"
          + chr(10) + "確認不是你自己要的改動之後,用 `git restore -- state/` 還原。",
          file=_sys.stderr)
    print("=" * 68, file=_sys.stderr)
    # **印出來不等於擋下來**(r14 外審第二輪):先前只 print,退出碼仍是 0
    # —— CI 照樣綠,而這道不變式的整個目的就是在 push 之前擋住。
    # 我自己驗證時只看「有沒有印出警告」,沒看退出碼:那是同一種錯的觀測版本。
    session.exitstatus = _pytest_failed_code()
