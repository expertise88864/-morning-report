"""11-07(週六)與 11-08(週日)實信的修正。"""
import legacy_evidence as le
import report_polish as rp
import session_wording as sw
from tools import quality_alert

SAT = {"TARGET_SESSION": "2026-11-09"}


def test_saturday_today_market_wording_becomes_next_session():
    text = ("今日開盤方向偏多。這是今日 2330 映射買盤的天花板。反轉條件是今日開高後上漲家數不到 500 檔。"
            "評價面壓力會蓋過今天的 ADR 映射。今日改中性。金管會今日宣布五項台股措施。")
    out = sw.rewrite(text, SAT, today="2026-11-07")
    assert "週一開盤方向偏多" in out and "週一 2330 映射" in out and "週一開高後" in out
    assert "週一的 ADR 映射" in out
    assert "今日改中性" in out and "金管會今日宣布" in out   # 報告本身與新聞的「今日」不動
    news = "金管會今日宣布開放盤中零股交易。外資今日買超後盤中翻空。"   # PR #45 外審
    assert sw.rewrite(news, SAT, today="2026-11-07") == news
    assert sw.rewrite("今日台股開盤方向偏多,今天加權指數開高。", SAT, today="2026-11-07") == \
        "週一台股開盤方向偏多,週一加權指數開高。"   # PR #45 外審第二輪


def test_trading_day_and_long_holiday():
    text = "今日開盤方向偏多。"
    assert sw.rewrite(text, {"TARGET_SESSION": "2026-11-09"}, today="2026-11-09") == text
    assert sw.prompt_note({"TARGET_SESSION": "2026-11-09"}, today="2026-11-09") == ""
    assert sw.rewrite(text, {"TARGET_SESSION": "2027-02-17"}, today="2027-02-06") == "2/17開盤方向偏多。"
    assert sw.rewrite(text, {}, today="2026-11-07") == text and sw.prompt_note({}, today="2026-11-07") == ""
    assert sw.rewrite(text, {"TARGET_SESSION": "壞"}, today="2026-11-07") == text


def test_saturday_prompt_says_market_is_closed():
    note = sw.prompt_note(SAT, today="2026-11-07")
    assert "11/7（週六）台股休市" in note and "11/9（週一）" in note and "不可寫「今日開盤」" in note


def test_reader_claims_apply_session_wording(monkeypatch):
    import legacy_actor_guard as g
    import datetime as dt
    monkeypatch.setattr(sw, "_days", lambda quotes, today=None: (dt.date(2026, 11, 7), dt.date(2026, 11, 9)))
    out = g.correct_reader_claims("## 一、結論\n今日開盤偏多,斷頭與自行了斷同時發生。\n", [],
                                  origin="luna_primary", manifest={}, quotes=SAT)
    assert "週一開盤偏多" in out and "自行了結" in out and "了斷" not in out


def test_term_fix():
    assert rp.normalize_terms("斷頭與自行了斷") == "斷頭與自行了結"


def test_sector_turnover_in_yi_is_supported_by_market_data():
    """11-07 實信:金融保險成交 424 億元來自類股統計(value_yi=424),不是查無來源。"""
    claim = le._quantities("成交 424 億元")[0]
    market = le._market_values({"SECTOR_HEAT": {"sectors": {"金融保險業": {"value_yi": 424.0, "n": 31}}}})
    assert le._supported(claim, [], market)
    assert not le._supported(claim, [], [424.0])   # 單位不明的數字(例如指數點數)不算
    assert not le._supported(le._quantities("成交 470 億元")[0], [], market)
    assert not le._supported(le._quantities("成交 424 萬元")[0], [], market)


def test_alert_for_observe_only_evidence_says_nothing_was_removed():
    import datetime as dt
    now = dt.datetime(2026, 11, 7, 6, 18)
    observe = ("今天的晨報跑起來了,但有 1 項不對:\n  [degraded] legacy_evidence_unverified —— "
               "備援分析 2／21 條證據核對未通過(觀察期,只記錄未刪除)")
    msg = quality_alert.build_message(observe, "u", now)
    assert "內容未刪除" in msg["Subject"] and "有段落沒跑成" not in msg.get_content()
    mixed = observe + "\n  [degraded] universe:institutional_missing —— 法人缺席"
    assert "有段落沒跑成" in quality_alert.build_message(mixed, "u", now)["Subject"]


def test_weekend_policy_hidden():
    import pathlib
    import morning_report as mr
    assert mr.SHOW_POLICY_DEEPDIVE is False
    src = pathlib.Path(mr.__file__).read_text(encoding="utf-8")
    assert "if SHOW_POLICY_DEEPDIVE else \"\")" in src
