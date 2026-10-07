"""Offline regression for misleading wording in the 2026-09-26 normal mail."""

import analysis_origin
import legacy_actor_guard
import morning_report
import prompt_profiles
import reader_market_language_guard as guard
from reader_citation_registry import source_bindings
import writing_rules


_SUMMARY = "中性，2330 於 2,472 元附近震盪，不加碼不追高，等外資期現貨同步轉正再談。"
_FLOW = (
    "把類股熱度表合起來讀，今天的錢明顯往電子零組件集中——該類股成交 1,969 億、"
    "法人淨買超 45.3 億，反觀半導體法人淨賣超 317.9 億，"
    "這個流向與外資現貨賣超、期貨偏空的方向一致。"
    "要讓這個流向反轉，得看到下一筆同口徑資料。"
)
_MODEL_TEXT = (
    "## 十一、我的明確立場\n" + _FLOW + "\n\n"
    "## 十二、一句話總結\n" + _SUMMARY + "\n"
)


def test_delivered_conclusion_preserves_observation_without_trading_instruction():
    manifest = {}
    revised = guard.neutralize_summary(_SUMMARY, manifest)
    assert revised.startswith("中性，2330 於 2,472 元附近震盪")
    assert "不加碼" not in revised and "不追高" not in revised
    assert "不是買賣訊號" in revised
    assert manifest["llm"]["conclusion_trade_claims_neutralized"] == 1


def test_support_threshold_as_trade_instruction_is_not_retained():
    manifest = {}
    revised = guard.neutralize_summary("偏多，2330 守穩 2,472 元逢回加碼。", manifest)
    assert revised.startswith("偏多；")
    assert "2,472" not in revised and "加碼" not in revised
    assert "不是買賣訊號" in revised


def test_prose_first_specialized_summary_cannot_bypass_trade_guard():
    manifest = {}
    revised = guard.neutralize_summary(
        "2330 守穩 2,472 元可加碼；本報偏多。", manifest)
    assert revised == "開盤預測僅供行情觀察，不是買賣訊號。"
    assert manifest["llm"]["conclusion_trade_claims_neutralized"] == 1


def test_comma_before_trade_verb_does_not_leave_support_price_threshold():
    for summary, expected in (
        ("偏多，2330 守穩 2,472 元，逢回加碼。",
         "偏多；開盤預測僅供行情觀察，不是買賣訊號。"),
        ("2330 守穩 2,472 元，可加碼；本報偏多。",
         "開盤預測僅供行情觀察，不是買賣訊號。"),
    ):
        manifest = {}
        revised = guard.neutralize_summary(summary, manifest)
        assert revised == expected
        assert "2,472" not in revised
        assert manifest["llm"]["conclusion_trade_claims_neutralized"] == 1


def test_company_capex_in_summary_is_not_mistaken_for_reader_purchase():
    summary = "中性，台積電加碼資本支出，但訂單增量仍待驗證。"
    assert guard.neutralize_summary(summary, {}) == summary


def test_later_capacity_clause_does_not_disguise_trade_advice():
    for summary in (
        "偏多，逢回加碼台積電，先進製程產能滿載。",
        "偏多，加碼 AI 伺服器供應鏈，受惠產能擴張。",
        "偏多，加碼 AI 伺服器供應鏈受惠產能擴張。",
    ):
        revised = guard.neutralize_summary(summary, {})
        assert "加碼" not in revised
        assert "不是買賣訊號" in revised


def test_descriptive_institutional_trades_and_numbered_capex_survive():
    factual = (
        "中性，昨日外資大幅賣出，但投信買進；"
        "台積電宣布加碼 300 億元資本支出。"
    )
    manifest = {}
    assert guard.neutralize_summary(factual, manifest) == factual
    assert manifest == {}
    assert "不是買賣訊號" in guard.neutralize_summary(
        "中性，建議加碼 300 億元資本支出。", {})


def test_institutional_trade_observations_are_not_cut_mid_sentence():
    for summary in ("偏空，外資連三日賣出台積電",
                    "偏空，三大法人合計賣出逾百億",
                    "偏空，外資大舉減碼半導體，觀察匯率",
                    "偏空，外資加碼半導體",
                    "外資加碼半導體，觀察匯率",
                    "資金進場意願下降",
                    "停損賣壓擴大，觀察成交量",
                    "追高意願降低，觀察量能",
                    "Fed 加碼降息，市場關注通膨"):
        manifest = {}
        assert guard.neutralize_summary(summary, manifest) == summary
        assert manifest == {}
    revised = guard.neutralize_summary("偏空，外資連三日賣出評等台積電", {})
    assert "賣出評等" not in revised


def test_futures_position_observation_is_not_a_reader_order():
    for summary in ("偏空，外資連續賣超、期貨空單加碼，留意匯率。",
                    "中性，期貨多單減碼，等待下一筆部位資料。"):
        manifest = {}
        assert guard.neutralize_summary(summary, manifest) == summary
        assert manifest == {}
    for advice in ("偏空，建議期貨空單加碼。",
                   "偏多，讀者可在期貨多單減碼時進場。"):
        assert "不是買賣訊號" in guard.neutralize_summary(advice, {})


def test_stance_markdown_prose_is_guarded_without_changing_source_titles():
    for marker in ("> ", "- ", "* ", "> - "):
        source = marker + "[今天的錢明顯往電子零組件集中](https://example.test/title)\n"
        report = "## 我的明確立場\n" + marker + _FLOW + "\n" + source
        manifest = {}
        corrected = guard.neutralize_report(report, manifest, allowed_urls=("https://example.test/title",), source_titles=source_bindings([{'url': 'https://example.test/title', 'title': '今天的錢明顯往電子零組件集中'}]))
        assert marker + "把類股熱度表合起來讀，上一交易日電子零組件成交較集中" in corrected
        assert "不能確認為同一資金流向" in corrected
        assert source in corrected
        assert manifest["llm"]["sector_flow_claims_neutralized"] == 3


def test_joined_institutional_subjects_preserve_facts_but_not_advice():
    for summary in ("中性，外資與投信同步買進台積電。",
                    "中性，投信與自營商同步賣出電子股。",
                    "偏空，外資及投信與自營商合計減碼半導體。"):
        manifest = {}
        assert guard.neutralize_summary(summary, manifest) == summary
        assert manifest == {}
    for advice in ("中性，外資與投信建議投資人買進台積電。",
                   "中性，外資與投信同步買進評等。"):
        revised = guard.neutralize_summary(advice, {})
        assert revised != advice
        assert "不是買賣訊號" in revised


def test_attributed_institutional_recommendation_is_not_a_trade_fact():
    for advice in ("外資建議投資人買進台積電", "外資重申買進台積電",
                   "外資評等買進台積電", "外資轉為買進評等"):
        revised = guard.neutralize_summary("中性，" + advice + "。", {})
        assert "買進" not in revised and "外資轉為；" not in revised
        assert "不是買賣訊號" in revised
    factual = "中性，外資現貨大幅賣出，但投信持續買進。"
    assert guard.neutralize_summary(factual, {}) == factual
    for fact in ("外資大舉買進台積電", "投信同步買進",
                 "外資轉為賣出", "法人逢低買進"):
        summary = "中性，" + fact + "。"
        assert guard.neutralize_summary(summary, {}) == summary


def test_ambiguous_add_investment_remains_blocked_as_trading_advice():
    revised = guard.neutralize_summary("偏多，建議加碼投資台積電。", {})
    assert "建議加碼" not in revised
    assert "不是買賣訊號" in revised
    for advice in ("偏多，建議外資加碼台積電。",
                   "偏多，可在外資減碼時進場。",
                   "偏多，投資人加碼半導體。"):
        revised = guard.neutralize_summary(advice, {})
        assert revised != advice
        assert "不是買賣訊號" in revised


def test_holding_instruction_and_markdown_wrappers_are_neutralized():
    assert "續抱" not in guard.neutralize_summary("偏多，2330 續抱。", {})
    for marker in ("> ", "- ", "> - "):
        manifest = {}
        report = "## 一句話總結\n" + marker + "偏空，建議減碼 00662。\n"
        revised = guard.neutralize_report(report, manifest)
        assert "建議減碼" not in revised
        assert "不是買賣訊號" in revised
        assert marker in revised
        assert manifest["llm"]["conclusion_trade_claims_neutralized"] == 1


def test_delivered_flow_correction_keeps_numbers_but_not_same_money_claim():
    manifest = {}
    revised = guard.neutralize_flow(_FLOW, manifest)
    assert "電子零組件成交較集中" in revised
    assert "1,969 億" in revised and "45.3 億" in revised
    assert "同一資金流向" in revised and "不能確認" in revised
    assert "要判斷類股交易分布是否改變" in revised
    assert "今天的錢" not in revised
    assert manifest["llm"]["sector_flow_claims_neutralized"] == 3


def test_shared_writer_guards_legacy_and_specialized_without_touching_source_title():
    raw = "[原始新聞標題：加碼買進](https://example.test/news)\n\n" + _MODEL_TEXT
    for origin in (analysis_origin.LEGACY_AFTER_LUNA_FAILURE,
                   analysis_origin.LUNA_SPECIALIZED):
        manifest = {}
        corrected = legacy_actor_guard.correct_reader_claims(
            raw, [], origin=origin, manifest=manifest)
        assert corrected.startswith("[原始新聞標題：加碼買進](https://example.test/news)")
        assert "今天的錢" not in corrected and "不加碼不追高" not in corrected
        assert "1,969 億" in corrected
        assert manifest["llm"]["conclusion_trade_claims_neutralized"] == 1


def test_unrelated_market_text_and_emergency_source_list_are_preserved():
    ordinary = "## 八、科技板塊脈動\n公司公告新產品。\n"
    assert guard.neutralize_report(ordinary, {}) == ordinary
    raw = "- [來源] 公司宣布加碼投資。"
    assert legacy_actor_guard.correct_reader_claims(
        raw, [], origin=analysis_origin.EMERGENCY_FALLBACK,
        manifest={}) == raw


def test_stale_us_quote_cannot_be_called_a_confirmed_holiday_in_stance():
    report = ("## 我的明確立場\n今日美股休市，美股訊號 stale。\n"
              "## 一句話總結\n> 美股昨日休市，因此只看台灣訊號。\n"
              "## 消息來源\n[美股昨日休市](https://example.test/title)\n")
    manifest = {}
    corrected = guard.neutralize_report(report, manifest, us_stale=True)
    assert "美股行情未更新" in corrected
    assert "今日美股休市" not in corrected
    assert "美股昨日休市" not in corrected.split("## 消息來源")[0]
    assert "[美股昨日休市](https://example.test/title)" in corrected
    assert manifest["llm"]["unverified_us_holiday_claims_neutralized"] == 2
    assert guard.neutralize_report(report, {}, us_stale=False) == report


def test_both_writer_contracts_separate_session_dates_and_flow_denominators():
    assert "EVIDENCE.as_of" in prompt_profiles.LUNA_DEVELOPER_INSTRUCTIONS
    assert "尚未開盤的目標交易日要寫「下個交易日」" in prompt_profiles.LUNA_DEVELOPER_INSTRUCTIONS
    assert "開盤價預測不是支撐價或買賣門檻" in prompt_profiles.LUNA_DEVELOPER_INSTRUCTIONS
    assert "成交占比不是資金淨流入" in writing_rules.LEGACY_RULES
    assert "不同口徑不可當作同一筆資金流動" in writing_rules.LUNA_WRITING
    assert "不得斷言已影響該日期的台股盤中" in prompt_profiles.LUNA_DEVELOPER_INSTRUCTIONS
    assert "不得推論「已影響某日台股盤中」" in writing_rules.LEGACY_RULES
    assert "今日美股休市" not in writing_rules.LEGACY_RULES


def test_sector_heat_evidence_does_not_present_previous_trade_as_today():
    heat = {"ranked": ["電子零組件"], "total_value_yi": 1969,
            "sectors": {"電子零組件": {"value_yi": 1969,
                                        "value_share_pct": 35.5,
                                        "median_pct": 1.0,
                                        "up": 3, "down": 1}}}
    block = morning_report._format_sector_heat_block(heat)
    assert "最近可得交易日 TWSE 全市場" in block
    assert "今日 TWSE 全市場" not in block


def test_duplicate_subscription_only_prints_once_without_dropping_distinct_terms():
    import datetime as dt

    row = {"code": "6698", "name": "旭暉應材", "start": dt.date(2026, 9, 25),
           "end": dt.date(2026, 9, 30), "draw": dt.date(2026, 10, 2),
           "price": 25.5, "market": 31.7, "spread": 6.2, "profit": 6200}
    another = dict(row, end=dt.date(2026, 10, 1))
    duplicate = dict(row, start=dt.date(2026, 9, 26), lottery_pct="1.2")
    rendered = morning_report._render_tw_calendar_html(
        {"ipo": [row, duplicate, another]})
    assert rendered.count("旭暉應材") == 2
    assert rendered.count("申購至 09/30") == 1
    assert rendered.count("申購至 10/01") == 1
