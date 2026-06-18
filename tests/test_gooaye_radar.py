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
    (tmp_path / "state").mkdir()
    f = tmp_path / "state" / "gooaye_radar.json"
    f.write_text(json.dumps({"gooaye": {"episodes": [
        {"guid": "sent1", "radar_sent_at": "2026-06-18T00:00:00Z"},
        {"guid": "pending"}]}}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
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
                                "ma20_dist_pct": 1.0, "radar_score": 70})
    assert "偏強" in strong and "外資投信同步連買" in strong
    weak = gr._stock_verdict({"foreign_30d_lot": -3000, "smart_money": {"score": 0},
                              "ma20_dist_pct": 30.0, "radar_score": 20})
    assert "偏弱" in weak and "過熱" in weak and "賣超" in weak


def test_render_radar_html_disclaimers_cards_no_emoji():
    meta = {"title": "EP671 | 🌼 测试", "published": "Wed, 17 Jun 2026", "guid": "g1"}
    extract = {"episode_summary": "本集主轴", "key_takeaways": ["重点一"], "market_view": "偏多",
               "sectors": [{"name": "功率半导体", "stance": "看多", "reasoning": "需求强"},
                           {"name": "面板", "stance": "看空", "reasoning": "供过于求"}]}
    sector_stocks = [{"sector": {"name": "功率半導體", "reasoning": "需求強"},
                      "stocks": [{"code": "2481", "name": "強茂", "close": 55.3, "day_pct": 2.1,
                                  "smart_money": {"score": 70, "tag": "外資連買"},
                                  "foreign_streak": 3, "invest_streak": 2,
                                  "foreign_30d_lot": 1200, "invest_30d_lot": 300, "tdcc_wow_pct": 0.3,
                                  "rev_yoy_pct": 15.0, "rev_mom_pct": 4.0, "eps": 1.2,
                                  "pct_5d": 3.2, "ma20_dist_pct": 2.0, "radar_score": 62.0,
                                  "_news": "強茂 Q2 拉貨😇旺 🌟"}]}]
    html = gr.render_radar_html(meta, extract, sector_stocks)
    assert "非股癌推薦" in html and "非投資建議" in html               # 免責
    assert "強茂" in html and "2481" in html
    assert "本族群資料面首選" in html and "綜合分 62" in html          # #1 標記 + 綜合分
    assert "雷達評語" in html and "排名 = 綜合資料面強弱" in html       # 判斷依據說明
    assert "外資連買3日" in html and "EPS 1.2" in html and "大戶持股週變" in html  # 更詳細欄位
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
