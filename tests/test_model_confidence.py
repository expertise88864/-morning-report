"""model_confidence.py 的前視偏誤防護。

這支工具在開發時踩過一個**會讓整份結論反轉**的坑,故單獨立測試釘死:
history.json 的 `date` 與 `target_session_date` 有 50/51 是同一天(晨報在當日
開盤前發出,預測的就是當天開盤)。若隨機漫步基準取「`date` 那天的收盤」,等於用
今天的收盤預測今天的開盤 —— 前視偏誤,會讓基準假性地強到打敗全部模型
(實測基準平均誤差從 1.482% 假性降到 1.039%,SPA p 值從 0.121 假性升到 0.773,
結論由「模型贏基準」翻成「模型全輸」)。

這類 bug 不會讓任何測試變紅,只會讓你對系統下錯結論——正是最該被釘住的那種。
"""
import model_confidence as mc


def _mh(session_closes: dict[str, float], opens: dict[str, float] | None = None):
    """建 {session: record} 對照表,label_prices 帶 2330 的收盤/開盤。"""
    out = {}
    for s, c in session_closes.items():
        lp = {"close": c}
        if opens and s in opens:
            lp["open"] = opens[s]
        out[s] = {"session_date": s, "label_prices": {"2330": lp}}
    return out


def test_random_walk_baseline_uses_prior_session_not_target_day():
    """基準必須取目標日**之前**那個交易日的收盤,不能取目標日自己的收盤。"""
    closes = {"2026-07-21": 100.0, "2026-07-22": 200.0, "2026-07-23": 300.0}
    mh = _mh(closes)
    sessions = sorted(mh)

    assert mc._prev_close(mh, sessions, "2026-07-23") == 200.0, \
        "應取前一交易日(07-22)的收盤,取到 300 就是用了目標日自己的收盤(前視)"
    assert mc._prev_close(mh, sessions, "2026-07-22") == 100.0


def test_first_session_has_no_baseline():
    """序列中第一個交易日沒有前一日 → 必須回 None 而非悄悄取到自己。"""
    mh = _mh({"2026-07-21": 100.0, "2026-07-22": 200.0})
    sessions = sorted(mh)
    assert mc._prev_close(mh, sessions, "2026-07-21") is None


def test_unknown_session_returns_none():
    """目標日不在交易日序列中(例如尚未結算)→ 回 None,不得猜。"""
    mh = _mh({"2026-07-21": 100.0})
    assert mc._prev_close(mh, sorted(mh), "2026-07-25") is None


def test_actual_open_prefers_label_prices():
    """實際開盤以 label_prices 為權威來源(那是標籤價的正式出處)。"""
    mh = {"2026-07-22": {"session_date": "2026-07-22",
                         "label_prices": {"2330": {"open": 111.0, "close": 1.0}},
                         "stocks": {"2330": {"open": 999.0}}}}
    assert mc._actual_open(mh, "2026-07-22") == 111.0


def test_actual_open_falls_back_to_stocks():
    """label_prices 缺開盤時才退回 stocks。"""
    mh = {"2026-07-22": {"session_date": "2026-07-22",
                         "label_prices": {"2330": {"close": 1.0}},
                         "stocks": {"2330": {"open": 222.0}}}}
    assert mc._actual_open(mh, "2026-07-22") == 222.0
