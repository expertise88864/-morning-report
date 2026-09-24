"""Offline incident regression: the 9/24 letter inferred ETF flows from cap."""
from __future__ import annotations

import reader_causality_guard as guard
from reader_hedge_causality_guard import correct_aggregate_hedge_inferences


def test_delivered_etf_inferences_are_qualified_without_losing_the_news():
    text = (
        "## 八、科技板塊脈動\n"
        "台積電：台股總市值上升，台積電單日貢獻逾 1 兆元市值（鉅亨）。"
        "傳導機制是被動式資金的機械買盤——0050、006208 等市值型 ETF "
        "因權重膨脹被迫加碼，反過來替 2330 現貨價格做地板。"
        "量級上，昨天這 1 兆元市值增幅是單日事件，無法外推，"
        "但要留意同一批被動資金在台積電回檔時的反向賣壓會同樣機械化。"
        "下一個可驗證點是公司法說。"
    )
    out, rules = guard.correct_etf_flow_inferences(text)
    assert rules == ("etf_forced_buy_from_market_cap",
                     "etf_reverse_flow_from_market_cap")
    assert "被迫加碼" not in out and "價格做地板" not in out
    assert "同一批被動資金" not in out
    assert "申贖與交易資料" in out
    assert "台股總市值上升" in out and "（鉅亨）" in out
    assert "昨天這 1 兆元市值增幅是單日事件，無法外推" in out
    assert "下一個可驗證點是公司法說" in out
    assert out.startswith("## 八、科技板塊脈動\n")


def test_source_headline_and_actual_flow_report_are_not_rewritten():
    text = (
        "**新聞標題：ETF 因權重調整被迫加碼。**\n"
        "基金申贖報告記錄 ETF 實際買入，來源為交易所公告。"
        "市值型 ETF 的市場成交量增加，尚待釐清原因。"
    )
    assert guard.correct_etf_flow_inferences(text) == (text, ())


def test_evidence_in_same_sentence_is_preserved():
    text = (
        "交易所公告申贖資料顯示，傳導機制是被動式資金的機械買盤——"
        "0050 等市值型 ETF 因權重膨脹被迫加碼，反過來替 2330 現貨價格做地板。"
    )
    assert guard.correct_etf_flow_inferences(text) == (text, ())


def test_unrelated_analysis_and_partial_markers_are_unchanged():
    text = "權重膨脹可能影響 ETF。被動資金有反向賣壓，但交易資料尚未提供。"
    assert guard.correct_etf_flow_inferences(text) == (text, ())


def test_delivered_price_risk_inference_is_qualified_without_losing_observations():
    text = (
        "利率與科技股評價風險:科技股昨天照樣創新高，說明市場目前願意忽略 "
        "4.968% 的殖利率；但殖利率已在一年 98.4 百分位、距 5% 僅一步，"
        "這是尚未被反映的風險，而不是已被否證的風險。"
        "兩邊都成立，差別在時間尺度：即日由風險偏好主導，"
        "1 到 4 週由折現率主導。"
    )
    out, rules = guard.correct_price_risk_inferences(text)
    assert rules == ("price_gain_does_not_prove_rate_risk_ignored",
                     "single_day_price_does_not_prove_future_driver")
    assert "科技股昨天照樣創新高" in out
    assert "4.968%" in out and "98.4 百分位" in out and "距 5% 僅一步" in out
    assert "單日股價無法判定" in out and "更多證據判斷" in out
    assert "不能由單日價格確定未來 1 到 4 週的主導因素" in out
    assert "願意忽略" not in out and "尚未被反映的風險" not in out


def test_price_risk_guard_does_not_rewrite_quotes_or_unrelated_risk_analysis():
    quote = ("「科技股昨天照樣創新高，說明市場目前願意忽略 4.968% 的殖利率；"
             "這是尚未被反映的風險。」")
    headline = ("**新聞標題：科技股昨天照樣創新高，說明市場目前願意忽略殖利率；"
                "這是尚未被反映的風險。**")
    bullet_headline = ("- **新聞標題：**科技股昨天照樣創新高，說明市場目前願意忽略"
                       " 4.968% 的殖利率；這是尚未被反映的風險。")
    attributed_quote = ("路透引述：「科技股昨天照樣創新高，說明市場目前願意忽略"
                        " 4.968% 的殖利率；這是尚未被反映的風險。」")
    reported_quote = ("路透報導，「科技股昨天照樣創新高，說明市場目前願意忽略"
                      " 4.968% 的殖利率；這是尚未被反映的風險。」")
    other = "科技股創新高，殖利率同時升至 4.968%；是否反映利率風險仍待觀察。"
    assert guard.correct_price_risk_inferences(quote) == (quote, ())
    assert guard.correct_price_risk_inferences(headline) == (headline, ())
    assert guard.correct_price_risk_inferences(bullet_headline) == (bullet_headline, ())
    assert guard.correct_price_risk_inferences(attributed_quote) == (attributed_quote, ())
    assert guard.correct_price_risk_inferences(reported_quote) == (reported_quote, ())
    assert guard.correct_price_risk_inferences(other) == (other, ())


def test_bold_analysis_heading_and_short_risk_clause_are_corrected():
    text = ("**利率與科技股評價風險：**科技股昨天照樣創新高，"
            "說明市場目前願意忽略 4.968% 的殖利率；"
            "這是尚未被反映的風險。")
    out, rules = guard.correct_price_risk_inferences(text)
    assert rules == ("price_gain_does_not_prove_rate_risk_ignored",)
    assert "**利率與科技股評價風險：**" in out
    assert "願意忽略" not in out and "尚未被反映的風險" not in out


def test_two_price_claims_in_one_sentence_are_both_corrected():
    text = ("利率與科技股評價風險:科技股昨天照樣創新高，說明市場目前願意忽略 4.968% 的殖利率；"
            "這是尚未被反映的風險；兩邊都成立，差別在時間尺度："
            "即日由風險偏好主導，1 到 4 週由折現率主導。")
    out, rules = guard.correct_price_risk_inferences(text)
    assert rules == ("price_gain_does_not_prove_rate_risk_ignored",
                     "single_day_price_does_not_prove_future_driver")
    assert "願意忽略" not in out and "尚未被反映的風險" not in out
    assert "即日由風險偏好主導" not in out


def test_attributed_quote_and_adjacent_analysis_are_handled_separately():
    text = ("本報解讀：路透引述：「殖利率走高」，但科技股昨天照樣創新高，"
            "說明市場目前願意忽略 4.968% 的殖利率；"
            "這是尚未被反映的風險。")
    out, rules = guard.correct_price_risk_inferences(text)
    assert rules == ("price_gain_does_not_prove_rate_risk_ignored",)
    assert out.startswith("本報解讀：路透引述：「殖利率走高」，但科技股")
    assert "願意忽略" not in out and "尚未被反映的風險" not in out


def test_quote_after_price_claim_does_not_hide_the_sentence_ending():
    text = ("本報解讀：科技股昨天照樣創新高，說明市場目前願意忽略 4.968% 的殖利率；"
            "這是尚未被反映的風險（路透引述：「殖利率走高」）。")
    out, rules = guard.correct_price_risk_inferences(text)
    assert rules == ("price_gain_does_not_prove_rate_risk_ignored",)
    assert "路透引述：「殖利率走高」" in out
    assert "願意忽略" not in out and "尚未被反映的風險" not in out


def test_quoted_final_punctuation_does_not_hide_preceding_analysis():
    text = ("本報解讀：科技股昨天照樣創新高，說明市場目前願意忽略 4.968% 的殖利率；"
            "這是尚未被反映的風險；路透引述：「殖利率走高。」")
    out, rules = guard.correct_price_risk_inferences(text)
    assert rules == ("price_gain_does_not_prove_rate_risk_ignored",)
    assert out.endswith("路透引述：「殖利率走高。」")
    assert "願意忽略" not in out and "尚未被反映的風險" not in out


def test_unclosed_quote_on_source_line_does_not_mask_next_analysis_line():
    text = ("新聞標題：路透引述：「利率走高\n"
            "利率與科技股評價風險:科技股昨天照樣創新高，說明市場目前願意忽略 "
            "4.968% 的殖利率；這是尚未被反映的風險。\n"
            "本報解讀：市場稱「軟著陸」。")
    out, rules = guard.correct_price_risk_inferences(text)
    assert rules == ("price_gain_does_not_prove_rate_risk_ignored",)
    assert out.startswith("新聞標題：路透引述：「利率走高\n")
    assert "願意忽略" not in out and "尚未被反映的風險" not in out
    assert out.endswith("本報解讀：市場稱「軟著陸」。")


def test_wrapped_price_claim_is_qualified_without_touching_source():
    source = "新聞標題：科技股創新高，利率風險仍待查證。\n"
    text = (source + "本報解讀：科技股昨天照樣創新高，說明市場目前願意忽略 "
            "4.968% 的殖利率；\n這是尚未被反映的風險。")
    out, rules = guard.correct_price_risk_inferences(text)
    assert rules == ("price_gain_does_not_prove_rate_risk_ignored",)
    assert out.startswith(source)
    assert "願意忽略" not in out and "尚未被反映的風險" not in out
    assert "4.968%" in out and "\n" in out


def test_wrapped_price_claim_with_transition_is_also_qualified():
    text = ("本報解讀：科技股昨天照樣創新高，說明市場目前願意忽略 "
            "4.968% 的殖利率；\n但殖利率已在一年 98.4 百分位，"
            "這是尚未被反映的風險。")
    out, rules = guard.correct_price_risk_inferences(text)
    assert rules == ("price_gain_does_not_prove_rate_risk_ignored",)
    assert "願意忽略" not in out and "尚未被反映的風險" not in out
    assert "98.4 百分位" in out


def test_price_claim_across_three_lines_is_qualified_without_crossing_headline():
    text = ("本報解讀：科技股昨天照樣創新高，說明市場目前願意忽略 "
            "4.968% 的殖利率；\n但殖利率已在一年 98.4 百分位，\n"
            "這是尚未被反映的風險。")
    out, rules = guard.correct_price_risk_inferences(text)
    assert rules == ("price_gain_does_not_prove_rate_risk_ignored",)
    assert "願意忽略" not in out and "尚未被反映的風險" not in out
    source = "新聞標題：這是尚未被反映的風險。"
    assert guard.correct_price_risk_inferences(text.split("\n")[0] + "\n" + source) == (
        text.split("\n")[0] + "\n" + source, ())


def test_actual_unlabeled_news_headline_shapes_remain_source_text():
    title = ("科技股昨天照樣創新高，說明市場目前願意忽略 4.968% 的殖利率；"
             "這是尚未被反映的風險")
    lines = (
        f"**{title}**（路透）。",
        f"[{title}](https://example.test/news)（路透）。",
        f"**科技類股**｜[{title}](https://example.test/news)（路透）。",
        f"**科技類股**｜{title}（路透）。",
    )
    for line in lines:
        assert guard.correct_price_risk_inferences(line) == (line, ())


def test_unattributed_unlabeled_text_is_not_treated_as_this_incident():
    text = ("科技股昨天照樣創新高，說明市場目前願意忽略 4.968% 的殖利率；"
            "這是尚未被反映的風險。")
    assert guard.correct_price_risk_inferences(text) == (text, ())


def test_bold_analytical_heading_with_pipe_is_not_a_source_headline():
    text = ("**利率與科技股評價風險**｜科技股昨天照樣創新高，"
            "說明市場目前願意忽略 4.968% 的殖利率；"
            "這是尚未被反映的風險。")
    out, rules = guard.correct_price_risk_inferences(text)
    assert rules == ("price_gain_does_not_prove_rate_risk_ignored",)
    assert "願意忽略" not in out and "尚未被反映的風險" not in out


def test_price_guard_preserves_unquoted_source_before_inline_own_analysis():
    claim = ("科技股昨天照樣創新高，說明市場目前願意忽略 4.968% 的殖利率；"
             "這是尚未被反映的風險。")
    source = "路透報導：" + claim
    out, rules = guard.correct_price_risk_inferences(source + " 本報解讀：" + claim)
    assert out.startswith(source + " 本報解讀：")
    assert out.count("願意忽略") == 1
    assert rules == ("price_gain_does_not_prove_rate_risk_ignored",)


def test_price_guard_checks_risk_observation_heading():
    text = ("**風險觀察**｜利率與科技股評價風險：科技股昨天照樣創新高，"
            "說明市場目前願意忽略 4.968% 的殖利率；這是尚未被反映的風險。")
    out, rules = guard.correct_price_risk_inferences(text)
    assert out.startswith("**風險觀察**｜利率與科技股評價風險：")
    assert "願意忽略" not in out and "尚未被反映的風險" not in out
    assert rules == ("price_gain_does_not_prove_rate_risk_ignored",)


def test_delivered_cash_futures_hedge_claims_are_qualified_without_losing_positions():
    text = (
        "今日結論\n費半漲 2.06%；但外資台指期淨空 75,568 口"
        "（現貨同步大買，多為避險）與台股上漲家數僅 30.7%。\n"
        "外資現貨買超是否延續，且台指期淨空是否縮小（"
        "外資現貨大買但期貨淨空是今天的核心矛盾；"
        "若現貨轉賣且淨空擴大，避險的解釋就不成立。）"
    )
    out, rules = correct_aggregate_hedge_inferences(text)
    assert rules == ("cash_futures_aggregate_does_not_prove_hedge",
                     "watch_hedge_hypothesis_not_established")
    assert "75,568 口" in out and "30.7%" in out and "台指期淨空是否縮小" in out
    assert "多為避險" not in out and "避險的解釋就不成立" not in out
    assert "不能判定是否為同一批人避險" in out
    assert "仍須核對交易者與部位變化" in out


def test_hedge_guard_preserves_headlines_quotes_and_qualified_unrelated_text():
    quoted = "路透引述：「外資台指期淨空（現貨同步大買，多為避險）」\n"
    headline = "新聞標題：外資台指期淨空（現貨同步大買，多為避險）\n"
    linked = "[外資台指期淨空（現貨同步大買，多為避險）](https://example.test/story)\n"
    labeled_linked = ("**市場**｜[外資台指期淨空（現貨同步大買，多為避險）]"
                      "(https://example.test/story)\n")
    bold = "**外資台指期淨空（現貨同步大買，多為避險）**（路透）\n"
    open_quote = "路透引述：「外資台指期淨空（現貨同步大買，多為避險）\n"
    qualified = "外資台指期淨空與現貨買超並存；無法判定是否同一批投資人避險。"
    text = quoted + headline + linked + labeled_linked + bold + open_quote + qualified
    assert correct_aggregate_hedge_inferences(text) == (text, ())


def test_hedge_guard_preserves_unlinked_company_source_lead():
    source = ("**科技類股**｜外資台指期淨空 75,568 口"
              "（現貨同步大買，多為避險）（路透）。\n\n"
              "本報解讀：外資台指期淨空 75,568 口"
              "（現貨同步大買，多為避險）。")
    out, rules = correct_aggregate_hedge_inferences(source)
    assert out.startswith(source.split("\n\n")[0])
    assert "本報解讀：外資台指期淨空 75,568 口（現貨同步買超" in out
    assert rules == ("cash_futures_aggregate_does_not_prove_hedge",)


def test_hedge_guard_preserves_standalone_unlinked_company_source_lead():
    source = ("**科技類股**｜外資台指期淨空 75,568 口"
              "（現貨同步大買，多為避險）（路透）。")
    assert correct_aggregate_hedge_inferences(source) == (source, ())


def test_hedge_guard_preserves_unquoted_source_report_before_own_analysis():
    claim = "外資台指期淨空 75,568 口（現貨同步大買，多為避險）。"
    source = "路透報導：" + claim
    text = source + "\n本報解讀：" + claim
    out, rules = correct_aggregate_hedge_inferences(text)
    assert out.startswith(source + "\n本報解讀：")
    assert rules == ("cash_futures_aggregate_does_not_prove_hedge",)
    assert out.count("多為避險") == 1


def test_hedge_guard_checks_inline_analysis_after_unquoted_source_report():
    claim = "外資台指期淨空 75,568 口（現貨同步大買，多為避險）。"
    source = "路透報導：" + claim
    out, rules = correct_aggregate_hedge_inferences(source + " 本報解讀：" + claim)
    assert out.startswith(source + " 本報解讀：")
    assert out.count("多為避險") == 1
    assert rules == ("cash_futures_aggregate_does_not_prove_hedge",)


def test_hedge_guard_checks_analytical_risk_heading():
    text = "**風險觀察**｜外資台指期淨空 75,568 口（現貨同步大買，多為避險）。"
    out, rules = correct_aggregate_hedge_inferences(text)
    assert out.startswith("**風險觀察**｜外資台指期淨空 75,568 口")
    assert "多為避險" not in out
    assert rules == ("cash_futures_aggregate_does_not_prove_hedge",)


def test_hedge_guard_qualifies_analysis_after_inline_source_link():
    source = "[路透：外資部位](https://example.test/news)"
    text = (source + " 本報解讀：外資台指期淨空 75,568 口"
            "（現貨同步大買，多為避險）。")
    out, rules = correct_aggregate_hedge_inferences(text)
    assert rules == ("cash_futures_aggregate_does_not_prove_hedge",)
    assert out.startswith(source + " 本報解讀：")
    assert "75,568 口" in out and "多為避險" not in out


def test_hedge_guard_corrects_analytical_heading_with_same_bold_pipe_shape():
    text = ("**外資部位**｜外資台指期淨空 75,568 口"
            "（現貨同步大買，多為避險）。")
    out, rules = correct_aggregate_hedge_inferences(text)
    assert rules == ("cash_futures_aggregate_does_not_prove_hedge",)
    assert out.startswith("**外資部位**｜外資台指期淨空 75,568 口")


def test_hedge_guard_corrects_today_conclusion_heading():
    text = "**今日結論**｜外資台指期淨空 75,568 口（現貨同步大買，多為避險）。"
    out, rules = correct_aggregate_hedge_inferences(text)
    assert rules == ("cash_futures_aggregate_does_not_prove_hedge",)
    assert "多為避險" not in out
    assert "多為避險" not in out


def test_hedge_guard_handles_wrapped_analysis_without_rewriting_source():
    text = (
        "## 今日結論\n外資台指期淨空 75,568 口\n"
        "（現貨同步大買，多為避險），台股上漲家數 30.7%。\n"
        "外資現貨大買但期貨淨空是今天的核心矛盾；\n"
        "若現貨轉賣且淨空擴大，避險的解釋就不成立。"
    )
    out, rules = correct_aggregate_hedge_inferences(text)
    assert rules == ("cash_futures_aggregate_does_not_prove_hedge",
                     "watch_hedge_hypothesis_not_established")
    assert "75,568 口" in out and "30.7%" in out
    assert "多為避險" not in out and "避險的解釋就不成立" not in out


def test_delivered_0921_hedge_inferences_are_qualified_without_losing_counts():
    text = (
        "外資台指期淨空 76,110 口，但後者與現貨買超同時出現，"
        "性質更接近避險而非方向看空。\n"
        "現貨買超 44,076 張與期貨淨空 76,110 口同時出現，"
        "最合理的解釋是現貨多單搭配期貨避險，而非方向看空。"
        "本報不把它當成看空訊號，但也不宣稱一定是避險——"
        "只能說在現貨同步買超的前提下，淨空的參考性偏低。\n"
        "風險：外資台指期淨空 76,110 口即使多為避險，"
        "在連假前流動性變薄時仍可能放大波動。\n"
        "失效條件：外資台指期淨空持續擴大且現貨同步轉賣，"
        "代表資金撤出而非對沖。"
    )
    out, rules = correct_aggregate_hedge_inferences(text)
    assert rules == (
        "cash_futures_coincidence_not_hedge_evidence",
        "cash_futures_pairing_not_established",
        "futures_short_reference_not_dismissed",
        "holiday_hedge_purpose_unconfirmed",
        "withdrawal_vs_hedge_unidentified",
    )
    assert "76,110 口" in out and "44,076 張" in out
    assert "性質更接近避險" not in out and "最合理的解釋" not in out
    assert "即使多為避險" not in out and "代表資金撤出" not in out
    assert "本報不把它當成看空訊號" in out
