"""股癌雷達純函式單測(不需網路/LLM/whisper):混合驗證擋幻覺、排序、渲染免責+轉繁、state。"""
import json

import gooaye_radar as gr


def test_norm_stance():
    assert gr._norm_stance("看多") == "看多"
    assert gr._norm_stance("偏多") == "看多"
    assert gr._norm_stance("看空") == "看空"
    assert gr._norm_stance("示警") == "看空"
    assert gr._norm_stance("") == "中性"
    assert gr._norm_stance("觀望") == "中性"


def test_validate_tickers_filters_hallucinations():
    wl = {"2481": {"name": "強茂"}, "2408": {"name": "南亞科"}, "3017": {"name": "奇鋐"}}
    cands = [
        {"code": "2481", "name": "強茂"},      # 存在 → 收
        {"code": "9999", "name": "亂碼科技"},   # 不在白名單(幻覺/下市)→ 擋
        {"code": "", "name": "南亞科"},         # 代號空、名稱反查救回 → 2408
        {"code": "abc", "name": "奇鋐"},        # 格式爛 + 名稱救回 → 3017
        {"code": "2481", "name": "強茂"},       # 重複 → 去重
    ]
    assert gr.validate_tickers(cands, wl) == ["2481", "2408", "3017"]
    assert gr.validate_tickers(cands, {}) == []   # 白名單空 → fail-closed,不展開個股


def test_validate_tickers_name_wins_on_code_name_conflict():
    """張冠李戴:LLM 給真實代號卻配錯公司名 → 以名稱反查正確代號,不誤收衝突代號。"""
    wl = {"2330": {"name": "台積電"}, "3017": {"name": "奇鋐"}}
    assert gr.validate_tickers([{"code": "2330", "name": "奇鋐"}], wl) == ["3017"]
    assert gr.validate_tickers([{"code": "2330", "name": "不存在公司"}], wl) == []   # 名稱也查無 → 丟棄


def test_morning_report_radar_processed_guids(tmp_path, monkeypatch):
    """晨報去重來源:只認雷達 state 內『已寄(radar_sent_at)』的股癌 guid;無檔→空集(降級不去重)。"""
    import morning_report as mr
    f = tmp_path / "gooaye_radar.json"
    f.write_text(json.dumps({"gooaye": {"episodes": [
        {"guid": "sent1", "radar_sent_at": "2026-06-18T00:00:00Z"},
        {"guid": "pending"}]}}), encoding="utf-8")
    # **盯常數,不靠 chdir**:這個路徑外審補審 F1 之後是具名常數
    # (inline 路徑掃描器看不見它,而 conftest 也只導得動常數)。
    monkeypatch.setattr(mr, "GOOAYE_RADAR_FILE", f)
    assert mr._radar_processed_guids() == {"sent1"}
    f.unlink()
    assert mr._radar_processed_guids() == set()


def test_rank_top5_orders_by_radar_score():
    entries = [
        {"code": "A", "smart_money": {"score": 20}, "foreign_30d_lot": 100, "rev_yoy_pct": 5, "pct_5d": 1},
        {"code": "B", "smart_money": {"score": 90}, "foreign_30d_lot": 9000, "rev_yoy_pct": 50, "pct_5d": 8},
        {"code": "C", "smart_money": {"score": 50}, "foreign_30d_lot": 3000, "rev_yoy_pct": 20, "pct_5d": 3},
    ]
    top = gr.rank_top5(entries, top_n=2)
    assert [e["code"] for e in top] == ["B", "C"]       # 籌碼/法人/營收/動能最強者在前
    assert all(isinstance(e.get("radar_score"), (int, float)) for e in entries)


def test_strip_emoji():
    assert gr._strip_emoji("EP671 | 🌼") == "EP671 |"
    assert gr._strip_emoji("強茂😇月月千點💕🌟") == "強茂月月千點"
    assert gr._strip_emoji("⚠ 重要 ⭐") == "重要"
    assert gr._strip_emoji("純文字 2481") == "純文字 2481"   # 不誤刪一般字/數字


def test_stock_verdict_flags_strength_and_overheat():
    strong = gr._stock_verdict({"foreign_streak": 3, "invest_streak": 2, "rev_yoy_pct": 25,
                                "op_margin": 30, "per": 12, "yield_pct": 5.0,
                                "director_pct": 35, "forecast_achv_pct": 105,
                                "roe_q": 8, "major_holder_pct": 70, "foreign_hold_pct": 60,
                                "eps_yoy_pct": 45, "ma20_dist_pct": 1.0, "radar_score": 70})
    assert "偏強" in strong and "外資投信同步連買" in strong
    assert "營益率" in strong and "本益比12偏低" in strong and "殖利率" in strong
    assert "董監持股35%高" in strong and "財測達成105%" in strong
    assert "單季ROE8%佳" in strong and "大戶持股70%集中" in strong and "外資持股60%高" in strong
    assert "EPS年增45%" in strong
    weak = gr._stock_verdict({"foreign_30d_lot": -3000, "smart_money": {"score": 0},
                              "per": 55, "pledge_pct": 40, "forecast_achv_pct": 80,
                              "ma20_dist_pct": 30.0, "radar_score": 20})
    assert "偏弱" in weak and "過熱" in weak and "賣超" in weak and "本益比55偏高" in weak
    assert "董監設質40%偏高" in weak and "財測達成僅80%" in weak


def test_fetch_valuation_parse(monkeypatch):
    monkeypatch.setattr(gr, "_twse_json", lambda url: [
        {"Code": "2330", "PEratio": "32.40", "DividendYield": "0.91", "PBratio": "10.61"},
        {"Code": "00", "PEratio": "1"}])   # 非 4 位數字 → 略過
    assert gr.fetch_valuation() == {"2330": {"per": 32.4, "yield_pct": 0.91, "pbr": 10.61}}


def test_fetch_margins_parse(monkeypatch):
    """改用 t187ap17_L 營益分析(官方直接給率)。"""
    monkeypatch.setattr(gr, "_twse_json", lambda url: [
        {"公司代號": "2330", "毛利率(%)(營業毛利)/(營業收入)": "66.25",
         "營業利益率(%)(營業利益)/(營業收入)": "58.10",
         "稅後純益率(%)(稅後純益)/(營業收入)": "50.51"},
        {"公司代號": "00", "毛利率(%)(營業毛利)/(營業收入)": "1"}])   # 非4位 → 略過
    out = gr.fetch_margins()
    assert out["2330"] == {"gross_margin": 66.25, "op_margin": 58.1, "net_margin": 50.51}
    assert "00" not in out


def test_fetch_roe_parse(monkeypatch):
    """單季 ROE/ROA = 稅後淨利(t187ap14)÷ 權益/資產總額(t187ap07_L_ci)。"""
    def fake(url):
        if "t187ap14" in url:
            return [{"公司代號": "2330", "稅後淨利": "1,000"},
                    {"公司代號": "2454", "稅後淨利": "500"}]
        if "t187ap07" in url:
            return [{"公司代號": "2330", "權益總額": "5,000", "資產總額": "10,000"},
                    {"公司代號": "2454", "權益總額": "0", "資產總額": ""}]   # 權益 0/資產空 → None 不爆
        return []
    monkeypatch.setattr(gr, "_twse_json", fake)
    out = gr.fetch_roe()
    assert out["2330"] == {"roe_q": 20.0, "roa_q": 10.0}
    assert out["2454"] == {"roe_q": None, "roa_q": None}


def test_fetch_foreign_holding_parse(monkeypatch):
    """FinMind 外資持股比率,取最新一筆;失敗的代號略過不爆。"""
    class _R:
        def json(self):
            return {"data": [{"date": "2026-06-10", "ForeignInvestmentSharesRatio": 68.0},
                             {"date": "2026-06-18", "ForeignInvestmentSharesRatio": 69.98}]}
    monkeypatch.setattr(gr.requests, "get", lambda *a, **k: _R())
    assert gr.fetch_foreign_holding(["2330"]) == {"2330": {"foreign_hold_pct": 69.98}}

    def boom(*a, **k):
        raise RuntimeError("rate limit")
    monkeypatch.setattr(gr.requests, "get", boom)
    assert gr.fetch_foreign_holding(["2330"]) == {}   # 失敗 → 空,不拋例外


def test_fetch_eps_growth_parse(monkeypatch):
    """FinMind 財報季 EPS 序列 → 最新季 vs 去年同季年增率。"""
    class _R:
        def json(self):
            return {"data": [
                {"date": "2025-03-31", "type": "EPS", "value": 10.0},
                {"date": "2025-06-30", "type": "EPS", "value": 12.0},
                {"date": "2026-03-31", "type": "EPS", "value": 15.0},
                {"date": "2026-03-31", "type": "Revenue", "value": 999}]}   # 非 EPS → 略過
    monkeypatch.setattr(gr.requests, "get", lambda *a, **k: _R())
    out = gr.fetch_eps_growth(["2330"])
    assert out["2330"]["eps_latest"] == 15.0 and out["2330"]["eps_latest_q"] == "2026-03-31"
    assert out["2330"]["eps_yoy_pct"] == 50.0    # (15-10)/10,對齊去年同季 2025-03-31


def test_radar_tradeable_filter():
    assert gr._radar_tradeable({"market_cap": 5e9, "liquidity_eligible": True}) is True
    assert gr._radar_tradeable({"market_cap": 1e9}) is False          # < 30 億 → 剔除
    assert gr._radar_tradeable({"liquidity_eligible": False}) is False  # 流動性不足 → 剔除
    assert gr._radar_tradeable({}) is True                            # 缺資料 → 放行(不誤殺)


def test_roc_md():
    assert gr._roc_md("1150709") == "07/09"
    assert gr._roc_md("") == "" and gr._roc_md("abc") == ""


def test_fetch_dividends_sums_latest_year(monkeypatch):
    monkeypatch.setattr(gr, "_twse_json", lambda url: [
        # 114 年度兩期(季配)→ 彙總:現金 4+4.5=8.5,配股 1.0
        {"公司代號": "2330", "股利年度": "114", "股東配發-盈餘分配之現金股利(元/股)": "4.0",
         "股東配發-盈餘轉增資配股(元/股)": "1.0", "決議（擬議）進度": "董事會擬議"},
        {"公司代號": "2330", "股利年度": "114", "股東配發-盈餘分配之現金股利(元/股)": "4.0",
         "股東配發-資本公積發放之現金(元/股)": "0.5", "決議（擬議）進度": "股東會確認"},
        {"公司代號": "2330", "股利年度": "113", "股東配發-盈餘分配之現金股利(元/股)": "99"},  # 舊年度 → 不取
        {"公司代號": "2330", "股利年度": ""},                                              # 無年度 → 略過
        {"公司代號": "00", "股利年度": "114", "股東配發-盈餘分配之現金股利(元/股)": "1"}])    # 非4位 → 略過
    out = gr.fetch_dividends()
    assert out["2330"] == {"div_year": "114", "cash_div": 8.5, "stock_div": 1.0,
                           "progress": "股東會確認"}   # 取最新年度、彙總各期、進度取最後有值
    assert "00" not in out


def test_fetch_dividends_year_falls_back_to_period(monkeypatch):
    """股利年度缺值時,改由『股利所屬期間』起日取民國年。"""
    monkeypatch.setattr(gr, "_twse_json", lambda url: [
        {"公司代號": "2603", "股利年度": "", "股利所屬期間": "1140101~1141231",
         "股東配發-盈餘分配之現金股利(元/股)": "5.0"}])
    assert gr.fetch_dividends()["2603"]["div_year"] == "114"


def test_card_renders_stock_only_dividend():
    """純配股(現金=0、配股>0)不可被吃掉。"""
    meta = {"title": "EP1", "published": "x", "guid": "g"}
    extract = {"sectors": [{"name": "A", "stance": "看多", "reasoning": "r"}]}
    ss = [{"sector": {"name": "A", "reasoning": "r"},
           "stocks": [{"code": "1234", "name": "測試", "cash_div": 0, "stock_div": 2.0,
                       "div_year": "114", "radar_score": 50}]}]
    html = gr.render_radar_html(meta, extract, ss)
    assert "114年度配股 2.0 元" in html and "現金股利" not in html


def test_fetch_exdiv_calendar_parse(monkeypatch):
    monkeypatch.setattr(gr, "_twse_json", lambda url: [
        {"Code": "2330", "Date": "1150709", "Exdividend": "息", "CashDividend": "4.5"},
        {"Code": "00400A", "Date": "1150709", "Exdividend": "息"}])   # 非4位數字 → 略過
    assert gr.fetch_exdiv_calendar() == {
        "2330": {"exdiv_md": "07/09", "exdiv_type": "息", "cash_div": 4.5}}


def test_fetch_insider_aggregates_and_pct(monkeypatch):
    monkeypatch.setattr(gr, "_twse_json", lambda url: [
        {"公司代號": "2330", "目前持股": "100", "設質股數": "20"},
        {"公司代號": "2330", "目前持股": "300", "設質股數": "0"},   # 逐人 → 彙總 held=400
        {"公司代號": "6525", "目前持股": "1500", "設質股數": "0"},   # KY:held>股本 → >100% 不可信
        {"公司代號": "ab", "目前持股": "999"}])                     # 非4位 → 略過
    out = gr.fetch_insider({"2330": {"shares": 1000}, "6525": {"shares": 1000}})
    assert out["2330"]["director_pct"] == 40.0    # 400/1000
    assert out["2330"]["pledge_pct"] == 5.0       # 20/400
    assert out["6525"]["director_pct"] is None    # 150% → 捨棄(KY 股本基準不符)
    assert "ab" not in out
    # 白名單缺已發行股數 → 佔比 None(不爆),設質比例仍可算
    out2 = gr.fetch_insider({})
    assert out2["2330"]["director_pct"] is None and out2["2330"]["pledge_pct"] == 5.0


def test_fetch_guidance_parse(monkeypatch):
    monkeypatch.setattr(gr, "_twse_json", lambda url: [
        {"公司代號": "2412", "截至該季經會計師查核或核閱數": "10000",
         "截至該季綜合損益預測數": "8000~12000"},     # 區間中值=10000 → 100%
        {"公司代號": "1234", "截至該季經會計師查核或核閱數": "",
         "截至該季綜合損益預測數": "100"}])             # 無實際數 → 略過
    out = gr.fetch_guidance()
    assert out["2412"]["forecast_achv_pct"] == 100.0
    assert "1234" not in out


def test_render_radar_html_disclaimers_cards_no_emoji():
    meta = {"title": "EP671 | 🌼 测试", "published": "Wed, 17 Jun 2026", "guid": "g1"}
    extract = {"episode_summary": "本集主轴", "key_takeaways": ["重点一"], "market_view": "偏多",
               "sectors": [{"name": "功率半导体", "stance": "看多", "reasoning": "需求强"},
                           {"name": "面板", "stance": "看空", "reasoning": "供过于求"}]}
    sector_stocks = [{"sector": {"name": "功率半導體", "reasoning": "需求強"},
                      "trend": "功率元件需求回溫、報價止跌😇",
                      "stocks": [{"code": "2481", "name": "強茂", "close": 55.3, "day_pct": 2.1,
                                  "market_cap": 32_500_000_000, "smart_money": {"score": 70, "tag": "外資連買"},
                                  "foreign_streak": 3, "invest_streak": 2,
                                  "foreign_30d_lot": 1200, "invest_30d_lot": 300, "tdcc_wow_pct": 0.3,
                                  "major_holder_pct": 58.0, "foreign_hold_pct": 21.0,
                                  "margin_balance_lot": 4200, "short_cover_ratio": 1.1,
                                  "rev_yoy_pct": 15.0, "rev_mom_pct": 4.0, "rev_cum_yoy_pct": 12.0,
                                  "gross_margin": 35.5, "op_margin": 22.1, "net_margin": 18.0,
                                  "roe_q": 4.5, "eps": 1.2, "eps_yoy_pct": 58.0,
                                  "per": 14.0, "yield_pct": 4.2, "pbr": 2.1,
                                  "div_year": "114", "cash_div": 16.5, "stock_div": 1.0,
                                  "progress": "股東會確認",
                                  "exdiv_md": "07/09", "exdiv_type": "息",
                                  "director_pct": 12.3, "pledge_pct": 0.0,
                                  "pct_5d": 3.2, "ma20_dist_pct": 2.0, "radar_score": 62.0,
                                  "theme_fit": "功率分離元件供 Non-China 缺口",
                                  "_news": "強茂 Q2 拉貨😇旺 🌟"}]}]
    html = gr.render_radar_html(meta, extract, sector_stocks)
    assert "非股癌推薦" in html and "非投資建議" in html               # 免責
    assert "強茂" in html and "2481" in html
    assert "本族群資料面首選" in html and "綜合分 62" in html          # #1 標記 + 綜合分
    assert "雷達評語" in html and "排名 = 綜合資料面強弱" in html       # 判斷依據說明
    assert "外資連買3日" in html and "EPS 1.2" in html and "30日外資" in html  # 籌碼欄位
    assert "毛利率" in html and "營益率" in html and "P/E 14.0" in html and "P/B 2.1" in html  # 基本面+估值
    assert "市值 325 億" in html                                       # 市值(自算/顯示)
    assert "淨利率 18.0%" in html and "單季ROE 4.5%" in html            # 基本面延伸
    assert "EPS 1.2(年增 +58%)" in html                                # FinMind EPS 年增率(緊接 EPS)
    assert "大戶持股 58%" in html and "外資持股 21%" in html and "融資餘額 4,200張" in html  # 第二籌碼列
    assert "產業近況" in html                                          # 類股趨勢一句話
    assert "官方:" in html and "114年度現金股利 16.5 元" in html and "+配股 1.0 元" in html  # 股利(標年度)
    assert "息日 07/09" in html and "董監持股 12.3%" in html             # 除權息預告 + 董監持股
    assert "符合子題" in html and "Non-China" in html                 # theme-fit
    assert "🌼" not in html and "😇" not in html                       # trend/news 也要去 emoji
    assert "看空" in html and "面板" in html                          # 看空族群仍列立場
    # 不外漏簡體 + 不外漏 emoji(標題/新聞/總綱)
    assert "测试" not in html and "半导体" not in html and "主轴" not in html
    assert "🌼" not in html and "😇" not in html and "🌟" not in html


def test_radar_state_roundtrip_and_processed_guids(tmp_path, monkeypatch):
    f = tmp_path / "gooaye_radar.json"
    monkeypatch.setattr(gr, "RADAR_STATE_FILE", f)
    gr.save_radar_state({"gooaye": {"name": "股癌", "episodes": [
        {"guid": "g1", "radar_sent_at": "2026-06-17T00:00:00Z"},
        {"guid": "g2"}]}})                          # g2 未寄 → 不算已處理
    assert gr.load_radar_state()["gooaye"]["episodes"][0]["guid"] == "g1"
    assert gr.radar_processed_guids() == {"g1"}     # 只有已寄(radar_sent_at)的算
    assert gr.save_radar_state({"x": 1}) is True    # 成功要說得出來


def test_delivered_but_unrecorded_is_not_a_green_run(monkeypatch, tmp_path):
    """2026-08-24 外審 P2:寄信成功之後 `save_radar_state` 吞掉例外、
    `process_new_episode` 照樣 `return 0`。信已經寄出但沒有留下
    `radar_sent_at` —— 下一次執行會**重寄同一集**,而 workflow 是綠的。"""
    monkeypatch.setattr(gr, "RADAR_STATE_FILE",
                        tmp_path / "nope" / "x.json")
    monkeypatch.setattr(gr.Path, "mkdir",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    assert gr.save_radar_state({"a": 1}) is False   # 失敗要說得出來
    # 接線:寄送成功後拿到 False 必須回非零(不是只印一行 log)
    import inspect
    src = inspect.getsource(gr.process_new_episode)
    i = src.index("if not save_radar_state(state):")
    assert "return 1" in src[i:i + 400], src[i:i + 400]


# ===================== oneliner 加深(F)=====================

class _OLEntry:
    def __init__(self, title):
        self._t = title

    def get(self, k, d=None):
        return self._t if k == "title" else d


class _OLFeed:
    def __init__(self, titles):
        self.entries = [_OLEntry(t) for t in titles]


def test_stock_news_oneliner_prefers_catalyst_title(monkeypatch):
    import morning_report as mr
    # 前 3 則:純股價 / 具催化詞 / 法說 → 應挑含催化詞者(非盲取第一則)
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda u: _OLFeed(["長榮股價收紅", "長榮獲美線大單、運價漲價", "長榮法說"]))
    out = gr._stock_news_oneliner("2603", "長榮")
    assert "大單" in out or "漲價" in out


def test_stock_news_oneliner_falls_back_to_latest(monkeypatch):
    import morning_report as mr
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda u: _OLFeed(["長榮股價震盪", "外資調節長榮"]))
    out = gr._stock_news_oneliner("2603", "長榮")
    assert out == "長榮股價震盪"          # 無催化詞 → 回最新一則


def test_stock_news_oneliner_empty_returns_dash(monkeypatch):
    import morning_report as mr
    monkeypatch.setattr(mr, "_feedparser_parse_url_with_timeout",
                        lambda u: _OLFeed([]))
    assert gr._stock_news_oneliner("2603", "長榮") == "—"
