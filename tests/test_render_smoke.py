"""渲染 golden 煙霧測試(A4):用固定 fixture(無網路)跑 render_html,
確認 (1) 端到端不崩、(2) 關鍵區塊 marker 都在(防「某段悄悄消失」的 regression)、
(3) 不外漏簡體/未替換的 raw。fixture 刻意填夠資料以觸發主要區段。"""
import morning_report as mr


def _fixture():
    quotes = {
        "QQQ": {"ticker": "QQQ", "close": 736.4, "change_pct": 1.7},
        "TSM": {"ticker": "TSM", "close": 477.57, "change_pct": 4.94},
        "SPY": {"ticker": "SPY", "close": 746.77, "change_pct": 0.78},
        "MACRO": {
            "VIX": {"close": 16.45, "change_pct": -6.8, "pct_rank_1y": 37},
            "SOX": {"close": 14246.96, "change_pct": 3.92, "pct_rank_1y": 99},
        },
        "MARKET_REGIME": "neutral",
        "STANCE_PY": {"total": 5, "label": "偏多", "components": {"qqq": 1}},
        "MARKET_BREADTH": {"advance_ratio": 69.4, "advancers": 757, "decliners": 238},
        "TAIEX_PRED": {"last_close": 46125.9, "pred_pct": 0.91, "pred_point": 46545},
        "TW0050_PRED": {"pred_open": 109.31, "last": 107.8},
        "TW_UNIVERSE_SNAPSHOT": [{
            "code": "2330", "name": "煙霧測試龍頭", "industry": "半導體業", "close": 2410.0,
            "day_pct": 1.2, "ranking_score": 40.0, "smart_money": {"score": 55, "tags": ["外資連買"]},
            "rev_yoy_pct": 30.0, "op_margin": 58.1, "per": 32.4, "yield_pct": 0.9,
            "market_cap": 2.6e13, "eps": 22.08, "foreign_streak": 3,
        }],
        "PODCAST_DIGEST": [],
        "TW_DAILY_INTELLIGENCE": {"policy": [], "medical": [], "policy_window": "", "medical_window": ""},
        "SPORTS": {},
        "MED_LITERATURE": [],
        "TW_IPO_CALENDAR": [],
        "NIGHT_TAIFEX": {"close": 47428.0, "change_pct": 1.39},
        "MODEL_MONITORING": {},
        "DISCLOSURE_EVENTS": [],
    }
    fair = {"avg_deviation_pct": 1.07, "fx_pct": 0.06, "implied_change_pct": 1.5, "premium_pct": 1.07,
            "fair_price": 124.43, "last_00662_price": 122.5, "qqq_pct": 1.7}
    predictions = {"mid": 2464, "last_2330": 2410, "low": 2440, "high": 2489, "pct": 2.25}
    analysis = ("## 我的明確立場\n偏多 +5。SOX +3.92% 與 TSM ADR +4.94% 同步發動。\n\n"
                "## 昨夜三大重點\n費半創高。\n\n## 科技板塊脈動\n台積電：法說前上修。")
    return quotes, fair, predictions, analysis


def test_render_html_smoke_has_core_sections():
    # render_html 已改為純渲染(不做 live HTTP:Top5 的 FinMind 補值移到 main 抓取階段)→ 本測試天然無網路
    quotes, fair, predictions, analysis = _fixture()
    html = mr.render_html(quotes, fair, predictions, analysis, "2026-07-03", "daily")
    assert isinstance(html, str) and len(html) > 2000          # 端到端有產出
    # 恆在的骨架
    assert "晨報" in html
    # 免責/來源/產生方式信尾三行已依使用者要求移除(2026-07-14),不再斷言存在
    assert "不構成投資建議" not in html
    # 由 fixture 資料驅動的區段
    assert "QQQ" in html and "SOX" in html                     # 美股/總經
    assert "煙霧測試龍頭" in html                            # Top5 卡已加回(使用者 2026-07-18)
    assert "124.43" in html                                      # 00662 公允價(六段)
    assert "個股開盤預測" in html                                # 六段結構標題(防整段被移除)
    assert "偏多" in html                                        # 立場(由 Python 權威驅動)
