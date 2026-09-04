# -*- coding: utf-8 -*-
"""**整條生產路徑走一次**(外審 2026-09-04 P2:integration coverage)。

現況的測試很密,但覆蓋的是「每一片」:leaf 單元測試、AST 接線檢查、
`test_main_decomposition` 的合成相位、各子系統的 deterministic 整合測試。
唯一走完整真 `_PIPELINE` 的是 CI 那個**手動**的 dry-run canary。於是這個形狀
一直沒有自動化的守衛:

    每一片都綠、接線檢查也綠,但相位 A 交給相位 B 的物件形狀錯了

—— 而這正是這個 repo 的事故史(9/3 的 date 序列化、9/4 的空正文卡都是
「跑完了、寄出去了、內容少一塊」)。

這個檔跑**真的** `main()`、真的八個相位、真的渲染與 manifest 組裝,只把
最外緣換成 deterministic adapter:

    網路(requests / yfinance / feedparser)、SMTP、LLM、時鐘

state 已由 `conftest._never_write_repo_state` 導到 tmp;`_git_commit_and_push_state`
與收據發佈本來就只在 `GITHUB_ACTIONS=true` 才動作,本機是 no-op。

兩個情境:
  1. **外部全滅**:所有網路回 404/空 —— 驗「晨報不可斷」:信照樣寄出、
     manifest 完整、降級標籤全部登記得到(沒有 unknown_degradation)。
  2. **LLM 給出合法特化輸出**:驗特化路徑的接線 —— 信裡有事件卡、
     `analysis_origin=luna_specialized`、recap 有存。
"""
import datetime as dt
import json
import types
from pathlib import Path

import pandas as pd
import pytest

import fixtures_analysis as fx
import morning_report as mr

_ROOT = Path(mr.__file__).resolve().parent
#: 固定在一個**平日**早晨(週五);週日會走 `run_weekend_digest`,那是另一條路。
_FROZEN = dt.datetime(2026, 9, 4, 5, 7, 0, tzinfo=mr.TPE)


# ----------------------------------------------------------------- adapters
class _Resp:
    """一個「上游沒有東西給你」的回應。404 不在 `_http_get` 的重試名單裡,
    所以不會觸發退避 —— 測試不該花時間在假的退避上。"""

    status_code = 404
    text = ""
    content = b""
    headers: dict = {}
    encoding = "utf-8"

    def json(self):
        raise ValueError("no json")

    def raise_for_status(self):
        raise mr.requests.exceptions.HTTPError("404", response=self)

    def iter_content(self, chunk_size=1):
        return iter(())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Net:
    """記下每一次外呼(讓測試證明「真的有人想連網、而且被擋在這裡」)。"""

    def __init__(self):
        self.calls: list = []

    def get(self, url, **kw):
        self.calls.append(("GET", str(url)))
        return _Resp()

    def post(self, url, **kw):
        self.calls.append(("POST", str(url)))
        return _Resp()


def _ohlc(rows: int = 2) -> pd.DataFrame:
    """`fetch_quote` 讀得懂的最小行情(Close/High/Low/Volume + 日期索引)。"""
    idx = pd.to_datetime(["2026-09-02", "2026-09-03", "2026-09-04"][:rows])
    base = [500.0, 505.0, 510.0][:rows]
    return pd.DataFrame({"Close": base, "High": [b + 2 for b in base],
                         "Low": [b - 2 for b in base],
                         "Open": base, "Volume": [1_000_000] * rows}, index=idx)


class _FakeTicker:
    #: 外部全滅的情境回空表;特化情境要有行情,否則 packet 沒有
    #: `market:QQQ.change_pct`,合法的分析也會被判成「引用了不存在的證據」。
    rows = 0

    def __init__(self, *a, **kw):
        pass

    def history(self, *a, **kw):
        return _ohlc(type(self).rows) if type(self).rows else pd.DataFrame()

    @property
    def upgrades_downgrades(self):
        return pd.DataFrame()

    @property
    def info(self):
        return {}

    @property
    def dividends(self):
        return pd.Series(dtype="float64")


class _SMTP:
    """`with smtplib.SMTP(...) as s: s.send_message(msg)` 的最小替身。"""

    sent: list = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, **kw):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        type(self).sent.append(msg)
        return {}          # 沒有人被拒


def _install(monkeypatch, *, llm_json=None, market_rows: int = 0):
    """把最外緣換成 deterministic adapter,其餘一律走真的程式碼。"""
    import llm_http as lh

    net = _Net()
    _SMTP.sent = []
    monkeypatch.setattr(_FakeTicker, "rows", market_rows)
    for mod in (mr, lh):
        monkeypatch.setattr(mod, "requests",
                            types.SimpleNamespace(
                                get=net.get, post=net.post,
                                exceptions=mr.requests.exceptions,
                                RequestException=mr.requests.RequestException,
                                Session=lambda: types.SimpleNamespace(
                                    get=net.get, post=net.post, headers={},
                                    mount=lambda *a, **k: None)),
                            raising=False)
    monkeypatch.setattr(mr, "yf", types.SimpleNamespace(
        Ticker=_FakeTicker, download=lambda *a, **kw: pd.DataFrame()))
    monkeypatch.setattr(mr, "feedparser", types.SimpleNamespace(
        parse=lambda *a, **kw: types.SimpleNamespace(entries=[], bozo=1, feed={})))
    monkeypatch.setattr(mr, "smtplib", types.SimpleNamespace(SMTP=_SMTP, SMTP_SSL=_SMTP))
    monkeypatch.setattr(mr, "GMAIL_USER", "bot@example.com")
    monkeypatch.setattr(mr, "GMAIL_APP_PASSWORD", "x")
    monkeypatch.setattr(mr, "RECIPIENTS", ["reader@example.com"])
    # 退避不該吃掉測試時間(真實時鐘仍由 `time.monotonic` 供給預算判斷)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda *_a, **_k: None)

    # 時鐘:固定在平日早晨 —— 週日走的是另一條路徑(run_weekend_digest)
    class _Now(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _FROZEN.astimezone(tz) if tz else _FROZEN.replace(tzinfo=None)

        @classmethod
        def today(cls):
            return _FROZEN.replace(tzinfo=None)
    monkeypatch.setattr(mr.dt, "datetime", _Now)

    # LLM:給合法輸出就走特化路徑;給 None 代表「LLM 也不可用」
    if llm_json is None:
        monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "")
        monkeypatch.setattr(mr, "OPENAI_API_KEY", "")
        monkeypatch.setattr(mr, "GEMINI_API_KEY", "")
        for env in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
            monkeypatch.delenv(env, raising=False)
    else:
        # **設定驗證讀的是 `os.environ`,不是模組屬性**(`has_key=lambda env:
        # os.environ.get(env)`)—— 只設模組屬性的話,`validate_llm_config` 會判
        # 「選了 deepseek 卻沒有金鑰」= fatal,路由 fail-closed 成
        # `emergency_fallback`,特化路徑根本走不到。兩邊都要設。
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setattr(mr, "LLM_PROVIDER", "deepseek")
        monkeypatch.setattr(mr, "DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setattr(mr, "DEEPSEEK_MODEL", "deepseek-v4-flash")
        monkeypatch.setattr(mr, "LLM_PRIMARY_PROMPT_PROFILE", "")
        monkeypatch.setattr(mr, "GEMINI_API_KEY", "")
        # **新聞是唯一額外換掉的 adapter**:它是網路來源,而特化輸出的證據 ID
        # 必須真的在 packet 裡才驗得過(外部全滅時 packet 沒有新聞,合法的分析
        # 也會被判成「引用了不存在的證據」)。換的是抓取,不是後面的正規化、
        # 分群、身分、packet 組裝、驗證與渲染 —— 那些全走真的。
        monkeypatch.setattr(mr, "fetch_news", lambda *a, **kw: fx.news())
        monkeypatch.setattr(mr, "fetch_news_fulltext", lambda news, *a, **kw: news)
        # **假的模型要「聽得懂」今天的 packet**:必須揭露的資料缺口與必須處理的
        # 訊號張力是 Python 端當天算出來的(`tension_refs`),靜態 fixture 猜不到。
        # 真模型看得到 packet 才寫得出來;這裡讓 fake 走同一條資訊路徑 ——
        # 觀察真的 `_ep.build` 產出的 packet,再照它的要求補齊。packet 組裝、
        # 驗證、修補、渲染全部仍是真的。
        import tension_refs as _tr
        seen: dict = {}
        _real_build = mr._ep.build

        def _spy_build(*a, **kw):
            pk = _real_build(*a, **kw)
            seen["packet"] = pk
            return pk
        monkeypatch.setattr(mr._ep, "build", _spy_build)

        def _respond(payload):
            obj = json.loads(json.dumps(llm_json, ensure_ascii=False))
            pk = seen.get("packet") or {}
            need_gaps = (pk.get("required_disclosures")
                         or _tr.required_gap_ids(pk.get("signal_tensions")) or {})
            obj["data_gaps"] = [
                {"gap_id": g, "what_is_missing": "這項檢查需要的行情欄位",
                 "impact_on_conclusions": "今天這個面向沒有答案"} for g in need_gaps]
            cms = obj.setdefault("cross_market_synthesis", {})
            # 欄位名與「證據要涵蓋這筆張力」的規則都照 schema / validator 來
            # (引用張力自己的 id 就算涵蓋 —— 見 `analysis_depth.both_sides_cited`)。
            cms["tension_resolutions"] = [
                {"tension_id": t,
                 "resolution": "兩者方向不同,今天以利率那一側為準",
                 "dominant_side": "left",
                 "why": "利率是估值的分母,時間尺度也比單日情緒長",
                 "decision_rule": "十年期殖利率若回落到 4.5% 以下,改看成長側",
                 "evidence_ids": [t]}
                for t in sorted(_tr.required_tension_ids(pk.get("signal_tensions")))]
            return {
                "status": "completed", "reasoning": {"effort": "max"},
                "output": [{"type": "message", "role": "assistant", "phase": "final_answer",
                            "content": [{"type": "output_text",
                                         "text": json.dumps(obj, ensure_ascii=False)}]}],
                "usage": {"input_tokens": 1000, "output_tokens": 500,
                          "input_tokens_details": {"cached_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 100}}}
        monkeypatch.setattr(mr, "_call_deepseek_responses", _respond)
    return net


def _manifest() -> dict:
    raw = Path(mr.RUN_MANIFEST_FILE).read_bytes().decode("utf-8")
    return json.loads(raw)


def _run(monkeypatch, **kw):
    net = _install(monkeypatch, **kw)
    mr._DEGRADED_STEPS.clear()
    # `_RUN_MANIFEST` 與 `_RECORDER.data` 是**同一個物件**(recorder 的 docstring
    # 明說不複製)。整個 clear 會把 `marks` 這個 recorder 自己維護的鍵一起刪掉,
    # 而 `main()` 直接 `data["marks"].clear()` —— 所以清內容、保留骨架。
    mr._RUN_MANIFEST.clear()
    mr._RECORDER.data.setdefault("marks", [])
    mr._RECORDER._state_corrupt.clear()
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    rc = mr.main()
    return rc, net


# ----------------------------------------------------------------- 情境一
@pytest.mark.slow
def test_the_letter_still_goes_out_when_every_upstream_is_down(monkeypatch):
    """**晨報不可斷** —— 外部全滅時信照樣寄出,而且說得出少了什麼。"""
    rc, net = _run(monkeypatch)
    assert rc == 0, "外部全滅不得讓整班失敗"
    assert len(_SMTP.sent) == 1, _SMTP.sent
    msg = _SMTP.sent[0]
    body = msg.get_payload()[-1].get_payload(decode=True).decode("utf-8")
    assert "<html" in body.lower() and len(body) > 2000, len(body)
    assert net.calls, "一次外呼都沒有?那這個測試沒有在測生產路徑"

    m = _manifest()
    assert str(m.get("date", ""))[:10] == "2026-09-04"
    assert (m.get("delivery") or {}).get("success") is True
    assert m.get("report_kind") == "morning_report"

    # 降級一定很多(外部全滅),但**每一個都要登記得到** —— 這正是
    # `unknown_degradation` 那條 finding 要防的:告警信印出一串沒見過的標籤。
    #
    # **問真的判準,不要在測試裡重寫一份**(Codex r1 P2):第一版自己複製了
    # `assess()` 的分類規則,而且順手把 `SURFACE_AS_UNKNOWN` 也豁免掉 ——
    # 那個常數的語意正好相反(那些標籤**就是要**浮成 unknown)。重寫判準的
    # 測試會與判準一起漂移,而且漂的方向永遠是「比較容易綠」。
    import run_quality as rq
    unknown = [f for f in rq.assess(m) if f.get("code") == "unknown_degradation"]
    assert not unknown, f"沒見過的降級標籤:{unknown}"


# ----------------------------------------------------------------- 情境二
@pytest.mark.slow
def test_a_valid_specialized_analysis_reaches_the_letter(monkeypatch):
    """特化路徑的接線:LLM 給合法 JSON → 信裡真的有那些段落。"""
    good = fx.valid_analysis()
    rc, _net = _run(monkeypatch, llm_json=good, market_rows=3)
    assert rc == 0
    assert len(_SMTP.sent) == 1
    body = _SMTP.sent[0].get_payload()[-1].get_payload(decode=True).decode("utf-8")

    m = _manifest()
    llm = m.get("llm") or {}
    assert llm.get("analysis_origin") == "luna_specialized", llm.get("analysis_origin")
    assert llm.get("recap_saved"), "特化成功卻沒有存昨日觀點"
    # **要驗實質內容,不是標題**(Codex r1 P2):標題與卡片內文是分開組的,
    # 只看標題等於容忍「段落在、內容空」—— 那正是 9/4 那封信的形狀。
    # 這幾句只可能來自 fixture 的分析,經由真的渲染鏈進到 HTML。
    import analysis_render as ar
    assert ar.SECTION_TECH.split("、")[-1] in body or ar.SECTION_WORLD.split("、")[-1] in body
    # 兩句走的是**不同的渲染路徑**,所以兩句都要在:
    #   `why_it_matters` → `_news_line` → `_blocks` → 八/九段的卡片內文
    #   `key_drivers[].statement` → `_claim_line` → 七、昨夜三大重點
    # 用 `any` 是不夠的(第一版如此):把八段的卡片全部清掉,靠事件卡那句
    # 照樣綠 —— 那正好是這條測試要抓的「段落在、內容空」。
    # `executive_summary` 刻意不驗:它不是逐字進信的(會被壓縮改寫),
    # 拿它當斷言等於對一個不成立的性質下賭注。
    for label, sentence in (("news.why_it_matters",
                             good["top_news_analysis"][0]["why_it_matters"]),
                            ("key_drivers[0].statement",
                             (good.get("key_drivers") or [{}])[0].get("statement", ""))):
        assert sentence, label
        assert sentence.split("。")[0][:12] in body, f"信裡找不到 {label} 的內容(段落在、內容空?)"


# ----------------------------------------------------------------- 守衛自己
def test_this_file_actually_drives_the_real_pipeline():
    """**判準不可空轉**:上面兩條若哪天改成呼叫別的東西,這條要紅。

    真 `main()`、真 `_PIPELINE`、真渲染 —— 只有最外緣被換掉。
    """
    src = (Path(__file__)).read_text(encoding="utf-8")
    assert "mr.main()" in src, "沒有走真的 main()"
    for fake in ('monkeypatch.setattr(mr, "yf"', 'monkeypatch.setattr(mr, "smtplib"',
                 'monkeypatch.setattr(mod, "requests"'):
        assert fake in src, fake
    # 相位本身不得被 patch 掉(那就不是端到端了)
    for phase in mr._PIPELINE:
        assert f'"{phase.__name__}"' not in src, f"{phase.__name__} 被 patch 掉了"
    assert len(mr._PIPELINE) == 8, mr._PIPELINE
