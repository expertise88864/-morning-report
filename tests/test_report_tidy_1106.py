"""2026-11-06 實信:法人缺席顯示成 0、模型沒照規則的幾件事,改由 Python 收尾。"""
import legacy_evidence as le
import morning_report as mr
import report_polish as rp
import report_tidy as rt
import run_quality as rq


_MAIL = """## 七、昨夜三大重點
- 第一條

## 八、科技板塊脈動
**台積電**:ADR 跌 10.78% [鉅亨][N1]。[A 級・信心:中]

**AI 模型競賽**:成本下降 [OpenRouter][N2]。[B 級・信心:低-中]

這條與第 1 條台積電估值殺同向——市場擔心 AI 投資報酬。

**蘋果**:毛利率下滑 [鉅亨][N3]。[A 級・信心:中-低]

## 九、其他類股資訊
**【金融｜台灣】金控**:前 9 月獲利 6,318 億元 [永豐][N4]。[A 級・信心:中]

**【金融｜台灣】富邦金**:經濟學家點名三大風險 [鉅亨][N5]。[B 級・信心:低-中]

**【生技｜台灣】漢康-KY**:與高醫簽約 [鉅亨][N6]。[B 級・信心:低]

**【生技｜台灣】德英**:新藥一期臨床 [工商][N7]。[A 級・信心:低]

**【生技｜台灣】某公司**:乾癬新藥進入臨床 [工商][N8]。[B 級・信心:低]

**【航運｜台灣】長榮**:運價上漲 [鉅亨][N9]。[B 級・信心:低]

## 十、台灣本地動態
主計總處公布 CPI 年增 2.33% [鉅亨][N10]。

央行外匯存底月減 [鉅亨][N11]。

台股昨日跌 1,374 點、融資減少 [中央社][N12]。
"""


def test_orphan_line_joins_the_item_above():
    out = rt.tidy(_MAIL)
    assert "\n\n這條與第 1 條" not in out
    assert "[B 級・信心:低-中] 這條與第 1 條台積電估值殺同向" in out


def test_minor_finance_and_biotech_are_dropped_but_skin_and_others_stay():
    out = rt.tidy(_MAIL)
    for gone in ("富邦金", "漢康-KY", "德英"):
        assert gone not in out, gone
    for kept in ("金控", "乾癬新藥", "長榮"):      # 皮膚科例外;航運不在這條規則內
        assert kept in out, kept


def test_market_item_goes_first_only_on_big_move_days():
    def first(text):
        return text.split("## 十、台灣本地動態")[1].strip().split("\n\n")[0]
    assert first(rt.tidy(_MAIL, big_move=True)).startswith("台股昨日跌")
    assert first(rt.tidy(_MAIL, big_move=False)).startswith("主計總處")


def test_other_sections_are_untouched_and_garbage_is_safe():
    assert rt.tidy(_MAIL).startswith("## 七、昨夜三大重點\n- 第一條\n\n## 八、")
    assert rt.tidy("") == "" and rt.tidy("沒有標題的文字") == "沒有標題的文字"


def test_big_move_line_is_fixed_by_python():
    assert rp.big_move_line(-3.2, {"advance_ratio": 15.4}) == "昨日台股大幅波動:加權 -3.20%、上漲家數僅佔 15.4%。"
    assert rp.big_move_line(-0.5, {"advance_ratio": 47}) == ""
    assert rp.big_move_line(None, {"advance_ratio": 15}) == "昨日台股大幅波動:上漲家數僅佔 15.0%。"


def test_t86_retries_once_then_gives_up(monkeypatch):
    """實信:06:08 主站與備援都回非 JSON,之後恢復。整班只等一次。"""
    import time
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(rp, "_T86_RETRIED", [])
    waits = []
    monkeypatch.setattr(time, "sleep", waits.append)
    calls = []

    def failing(*_):
        calls.append(1)
        raise ValueError("not json")

    monkeypatch.setattr(mr, "_twse_main_api", failing)
    monkeypatch.setattr(mr, "_twse_openapi", failing)
    assert mr.fetch_twse_institutional() == {}
    assert waits == [180] and len(calls) == 10         # (4 個日期 + OpenAPI) × 2 輪
    assert mr.fetch_twse_institutional() == {} and waits == [180]


def test_t86_recovers_after_the_wait(monkeypatch):
    import time
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(rp, "_T86_RETRIED", [])
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    rows = [{"證券代號": "2330", "外陸資買賣超股數(不含外資自營商)": "1,000", "投信買賣超股數": "0",
             "自營商買賣超股數": "0", "三大法人買賣超股數": "1,000"}]
    state = {"n": 0}

    def main_api(_):
        state["n"] += 1
        if state["n"] <= 4:
            raise ValueError("not json")
        return rows

    monkeypatch.setattr(mr, "_twse_main_api", main_api)
    monkeypatch.setattr(mr, "_twse_openapi", lambda *_: (_ for _ in ()).throw(ValueError("x")))
    assert "2330" in mr.fetch_twse_institutional()


def test_missing_institutional_is_alerted_and_not_shown_as_zero():
    manifest = {"degraded_steps": ["universe:institutional_missing"]}
    assert "universe:institutional_missing" in {f["code"] for f in rq.assess(manifest)}
    src = open(mr.__file__, encoding="utf-8").read()
    assert "今日三大法人資料缺席,本名單未含法人籌碼" in src and "'法人資料缺席' if" in src
    assert "                if f30:   #" in src


def test_own_prediction_is_not_flagged_as_unsourced():
    """實信:2,153.98 元是本報的預測值,卻被判「查任何素材都沒有」。"""
    le.fresh_ids([])
    n = le.tag({"title": "費半大跌", "link": "https://x/1"}).strip()
    block = f"模型估計 2330 今日開在 2,153.98 元 [鉅亨]{n}"
    assert le._check(block, le._market_values({"EVIDENCE_PREDICTIONS": {"weighted_final": 2153.98}})) == []
    assert le._check(block, le._market_values({})) != []
    assert 'quotes["EVIDENCE_PREDICTIONS"] = predictions' in open(mr.__file__, encoding="utf-8").read()


def test_market_first_needs_a_real_index_move():
    """外審 PR #44:「重點關注台股資金外流」不是指數漲跌。"""
    mail = ("## 十、台灣本地動態\n央行發布金融穩定報告,重點關注台股資金外流 [鉅亨][N1]。\n\n"
            "台股昨日跌 1,374 點、融資減少 [中央社][N2]。\n")
    assert rt.tidy(mail, big_move=True).split("動態\n")[1].strip().startswith("台股昨日跌")
    for ok in ("昨日台股盤中一度跌破 42,000 點", "加權指數收 41,574 點", "大盤重挫 3.2%"):
        assert rt._INDEX_MOVE.search(ok), ok
    none = "## 十、台灣本地動態\n央行報告關注台股資金外流。\n\n主計總處公布 CPI。\n"
    assert rt.tidy(none, big_move=True).split("動態\n")[1].strip().startswith("央行")


def test_t86_retry_reaches_a_recovered_host_through_the_circuit_breaker(monkeypatch):
    """外審 PR #44:走 HTTP 層驗證,連續失敗 4 次熔斷後,等完要真的連得到伺服器。"""
    import time
    import requests
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(rp, "_T86_RETRIED", [])
    monkeypatch.setattr(mr, "_HTTP_HOST_STATS", {})
    state = {"recovered": False}

    def sleep(seconds):
        if seconds == 180:
            state["recovered"] = True

    monkeypatch.setattr(time, "sleep", sleep)

    class Ok:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"stat": "OK", "fields": ["證券代號", "外陸資買賣超股數(不含外資自營商)", "投信買賣超股數",
                                             "自營商買賣超股數", "三大法人買賣超股數"],
                    "data": [["2330", "1,000", "0", "0", "1,000"]]}

    def fake_get(url, **_):
        if state["recovered"] and "www.twse.com.tw" in url:
            return Ok()
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(mr.requests, "get", fake_get)
    assert "2330" in mr.fetch_twse_institutional()
    assert state["recovered"]


def test_circuit_breaker_is_only_reset_once(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(rp, "_T86_RETRIED", [True])
    stats = {"www.twse.com.tw": {"fail": 9, "streak": 9}, "other.example": {"fail": 9, "streak": 9}}
    assert rp.wait_once_for_t86(stats) is False and stats["www.twse.com.tw"]["streak"] == 9
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(rp, "_T86_RETRIED", [])
    assert rp.wait_once_for_t86(stats) is True
    assert stats["www.twse.com.tw"]["streak"] == 0 and stats["other.example"]["streak"] == 9
